"""A metric contract's formula resolved from a dbt semantic manifest (ADR 0008, slice S2).

A contract whose formula is ``external_ref`` with ``external.source: dbt_semantic_manifest``
names a dbt metric (``external.ref``) and pins its closure (``external.source_revision``).
:func:`resolve_formula` reads that metric from what :mod:`auto_bi.semantic.metricflow` read
and answers with a typed formula or with the gaps that keep the formula ``external_ref`` —
never both. A construct the typed formula would not see is a gap, not a formula that ignores
it (ADR 0008, item 9).

The answer comes in steps, and a step that finds a gap is the last one:

1. **The artifact.** None given is block ``gap.external_source_missing``; an artifact the
   reader refused is its problems, each a block; no metric ``external.ref`` in it is block
   ``gap.external_ref_missing``.
2. **The pin.** A closure digest other than ``external.source_revision`` is block
   ``gap.external_revision_mismatch`` with both digests: what follows would read a definition
   nobody pinned.
3. **The contract and the supported subset** (ADR 0008, items 3, 7 and 8), every gap reported:

   - *relation* — the semantic model's ``schema_name.alias`` is not the contract's grain
     table, or its database is not the one declared with the artifact: block
     ``gap.external_relation_mismatch``; a database the artifact names when none was declared:
     review ``gap.external_relation_unproven``;
   - *time* — the column of the aggregation time dimension is not the contract's event time
     column: block ``gap.external_time_mismatch``; no such dimension, a dimension that is not
     ``time``, an expression that is not one column, or a granularity that is not ``day`` or
     not in the artifact: review ``gap.external_time_unproven``;
   - *ratio* — numerator and denominator over different semantic models or aggregation
     times: review ``gap.external_ratio_unproven``;
   - *additivity* — ``count_distinct``, ``min``, ``max``, ``average`` or a ratio under
     ``additivity: additive``: review ``gap.external_additivity_unproven``;
   - *private* — the contract's own metric is private: review
     ``gap.external_private_metric`` (a private input of a public ratio is not);
   - *construct* — anything outside the supported subset: review
     ``gap.external_construct_unsupported`` at the JSON path of the construct.

   The relation and time of a simple metric that names one way to aggregate (a measure or
   aggregation parameters) are checked whatever else it holds; a simple metric naming both or
   neither, and a metric that is neither simple nor a ratio of simple metrics, are answered by
   their construct gap.
4. **Parity.** A resolution relies on rows of the parity matrix — one per aggregate, dialect
   and class of the column's type, one per ratio form over two such terms, one per way of
   naming the column — each in its context ``<engine>.<event time>``: the engine the native
   model declares for the grain table, and the class of the event time column's type when that
   column is a grain key. A row not in ``proven`` is review ``gap.external_parity_unproven``:
   that MetricFlow and the contract's reference SQL give one number is a fact of a run on an
   engine, over columns of a type, at a grain, not of this code.

The supported subset (ADR 0008, item 7) is a simple metric — no filter, no
``time_granularity``, no ``datatype``, an aggregate among ``sum``, ``count``,
``count_distinct``, ``min``, ``max``, ``average``, default ``agg_params``, no
``non_additive_dimension``, ``join_to_timespine`` false, no ``fill_nulls_with``, one lower-case
column as its expression — and a ratio of two such metrics whose inputs carry no ``filter``,
``alias`` or offset. Every key of the metric objects a resolution reads is either one of those
or a name, a description, a label, metadata, config or a measure's ``create_metric``; a set key
of any other is a construct.
A legacy metric names its measure, whose ``expr`` (or, without one, its name) is the column; a
legacy metric ``expr`` is taken only where it says the same as the measure's (dbt writes one
for ``create_metric``). A V2 metric carries ``metric_aggregation_params`` and its own ``expr``.

The typed formula's table is the contract's grain table, which the relation check has made the
semantic model's; its column and aggregate come from dbt. A gap carries the JSON path of what
it is about — empty where the artifact has no place for it — and names from the artifact, never
another of its values (§10.2).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from auto_bi.semantic.metric_contract import (
    FormulaKind,
    MetricContract,
    MetricFormula,
)
from auto_bi.semantic.metricflow import SOURCE, JsonObject, ManifestRead, SemanticManifest
from auto_bi.semantic.model import Additivity, Aggregation
from auto_bi.severity import Severity

SOURCE_MISSING = "gap.external_source_missing"
REF_MISSING = "gap.external_ref_missing"
REVISION_MISMATCH = "gap.external_revision_mismatch"
RELATION_MISMATCH = "gap.external_relation_mismatch"
RELATION_UNPROVEN = "gap.external_relation_unproven"
TIME_MISMATCH = "gap.external_time_mismatch"
TIME_UNPROVEN = "gap.external_time_unproven"
RATIO_UNPROVEN = "gap.external_ratio_unproven"
ADDITIVITY_UNPROVEN = "gap.external_additivity_unproven"
PRIVATE_METRIC = "gap.external_private_metric"
CONSTRUCT_UNSUPPORTED = "gap.external_construct_unsupported"
PARITY_UNPROVEN = "gap.external_parity_unproven"

LEGACY = "legacy"
V2 = "v2"

#: dbt aggregation -> the typed formula's.
_AGGREGATION: dict[str, Aggregation] = {
    "sum": Aggregation.SUM,
    "count": Aggregation.COUNT,
    "count_distinct": Aggregation.COUNT_DISTINCT,
    "min": Aggregation.MIN,
    "max": Aggregation.MAX,
    "average": Aggregation.AVG,
}

#: Aggregates whose values do not add up to the value of the union of their groups.
_NOT_SUMMABLE = frozenset(
    {Aggregation.COUNT_DISTINCT, Aggregation.MIN, Aggregation.MAX, Aggregation.AVG}
)

#: Where the native model declares no engine or no type for a column.
UNDECLARED = "undeclared"
#: A declared type outside the classes below; an engine other than PostgreSQL and ClickHouse
#: has no classes.
UNCLASSIFIED = "unclassified"
#: The event time column of a contract whose grain keys do not hold it.
OUTSIDE_GRAIN = "outside_grain"

#: Declared engine -> the engine a parity row names.
_ENGINES = {"postgres": "postgres", "postgresql": "postgres", "clickhouse": "clickhouse"}

#: PostgreSQL types -> the class a parity row names: what an aggregate over the column is
#: computed in, and what the day of a time column is.
_PG_CLASSES = {
    "numeric": "decimal",
    "decimal": "decimal",
    "smallint": "integer",
    "integer": "integer",
    "bigint": "integer",
    "int": "integer",
    "int2": "integer",
    "int4": "integer",
    "int8": "integer",
    "double precision": "float",
    "real": "float",
    "float": "float",
    "float4": "float",
    "float8": "float",
    "date": "date",
}
#: A PostgreSQL type as `_PG_CLASSES` reads it: a name and an optional modifier, nothing after
#: them — an array or another suffix is a type the classes do not hold.
_PG_TYPE = re.compile(r"([a-z][a-z0-9 ]*?)\s*(?:\(\s*\d+\s*(?:,\s*\d+\s*)?\))?")

#: ClickHouse types, spelled as the engine writes them in `system.columns`, -> the class a
#: parity row names. A member computes the matrix's aggregates in the types the run's column
#: of its class computes them in — `sum` over `Int8` … `Int64` in `Int64`, over
#: `Decimal(P, 2)` in `Decimal(38, 2)`, `avg` in `Float64` — and the run records them. Any
#: other type or spelling is unclassified: `sum` over `UInt8` … `UInt64` is a `UInt64`, and
#: the reference SQL reads a type by its spelling. `Nullable(T)` is `T`'s class for an
#: aggregated column, not for the event time.
CLICKHOUSE_CLASSES: Mapping[str, str] = {
    **{name: "integer" for name in ("Int8", "Int16", "Int32", "Int64")},
    **{f"Decimal({precision}, 2)": "decimal" for precision in range(2, 39)},
    "Float64": "float",
    "Date": "date",
}

#: The contexts of the recorded run: PostgreSQL and ClickHouse, a `date` event time column
#: among the grain keys.
_RUNS = ("postgres.date", "clickhouse.date")
#: Ratio terms the run compared: a float term under `sum`, `min`, `max` or `average`, and any
#: term under `average`, have no reference SQL (`sql.ratio_inexact_term`).
_RATIO_TERMS = (
    *(
        f"legacy.{agg}.{kind}"
        for agg in ("sum", "count", "count_distinct", "min", "max")
        for kind in ("decimal", "integer")
    ),
    "legacy.count.float",
    "legacy.count_distinct.float",
)

#: Rows of the parity matrix a run has shown MetricFlow and the reference SQL to agree on:
#: `scripts/metricflow_parity.py` on PostgreSQL 16.15 and ClickHouse 24.8.14.39 with
#: metricflow 0.213.0, its result `tests/fixtures/dbt_semantic_manifest/parity/result.json`.
#: No V2 metric resolves, and no V2 row is here.
PROVEN_PARITY: frozenset[str] = frozenset(
    row
    for run in _RUNS
    for row in (
        *(
            f"{run}:legacy.{agg}.{kind}"
            for agg in ("sum", "count", "count_distinct", "min", "max", "average")
            for kind in ("decimal", "integer", "float")
        ),
        f"{run}:legacy.measure_without_expr",
        f"{run}:legacy.metric_expr",
        *(f"{run}:ratio.{top}/{bottom}" for top in _RATIO_TERMS for bottom in _RATIO_TERMS),
    )
)

#: A column a typed term may name: one lower-case identifier. Case folding differs between
#: engines, and anything else is an expression.
_COLUMN = re.compile(r"[a-z_][a-z0-9_]*")

#: Bare words SQL reads as a value, not as a column.
_VALUE_WORDS = frozenset(
    {
        "true",
        "false",
        "null",
        "current_date",
        "current_time",
        "current_timestamp",
        "localtime",
        "localtimestamp",
        "current_user",
        "current_role",
        "current_catalog",
        "current_schema",
        "session_user",
        "user",
    }
)

#: The default `agg_params` of metricflow 0.213.0.
_DEFAULT_AGG_PARAMS = {
    "percentile": None,
    "use_discrete_percentile": False,
    "use_approximate_percentile": False,
}

# Keys a resolution reads, each with the values that leave the number alone (an absent key
# reads as None) and what a set one is. Keys not listed in an object the resolution reads are
# its own (handled where it is read) or a name, a description, a label, metadata or config.
_Unset = Mapping[str, tuple[tuple[Any, ...], str]]

_METRIC: _Unset = {
    "filter": ((None,), "a metric filter"),
    "time_granularity": ((None,), "a metric time granularity"),
    "datatype": ((None,), "a declared metric datatype"),
}
_PARAMS_COMMON: _Unset = {
    "window": ((None,), "a window"),
    "grain_to_date": ((None,), "a grain to date"),
    "metrics": ((None, []), "input metrics"),
    "conversion_type_params": ((None,), "conversion parameters"),
    "cumulative_type_params": ((None,), "cumulative parameters"),
    "join_to_timespine": ((None, False), "a join to the time spine"),
    "fill_nulls_with": ((None,), "a value that fills NULL"),
}
_SIMPLE_PARAMS: _Unset = {
    **_PARAMS_COMMON,
    "numerator": ((None,), "a numerator"),
    "denominator": ((None,), "a denominator"),
}
_RATIO_PARAMS: _Unset = {
    **_PARAMS_COMMON,
    "measure": ((None,), "a measure"),
    "expr": ((None,), "an expression"),
    "metric_aggregation_params": ((None,), "aggregation parameters"),
}
_INPUT_MEASURE: _Unset = {
    "filter": ((None,), "a measure filter"),
    "alias": ((None,), "an alias"),
    "join_to_timespine": ((None, False), "a join to the time spine"),
    "fill_nulls_with": ((None,), "a value that fills NULL"),
}
_METRIC_INPUT: _Unset = {
    "filter": ((None,), "an input filter"),
    "alias": ((None,), "an alias"),
    "offset_window": ((None,), "an offset window"),
    "offset_to_grain": ((None,), "an offset to grain"),
}


@dataclass(frozen=True)
class ExternalGap:
    """Why a formula stays ``external_ref``: a gap code, its severity, a JSON path, a reason."""

    code: str
    severity: Severity
    path: str
    reason: str


@dataclass(frozen=True)
class DeclaredTable:
    """What the native model declares of the contract's grain table: its engine, empty when
    it declares none, and the type of each column it declares."""

    engine: str
    column_types: Mapping[str, str]


@dataclass(frozen=True)
class FormulaResolution:
    """`formula` is set exactly when `gaps` is empty. `closure_digest` is the digest the pin was
    compared with, empty when no artifact gave one. `parity_rows` are the rows of the parity
    matrix the formula relies on — set when the answer reached the parity step, proven or not,
    and empty otherwise."""

    formula: MetricFormula | None
    closure_digest: str
    gaps: tuple[ExternalGap, ...]
    parity_rows: frozenset[str] = frozenset()


def is_dbt_formula(contract: MetricContract) -> bool:
    """Whether the contract's formula is one a dbt semantic manifest defines."""
    external = contract.formula.external
    return (
        contract.formula.kind is FormulaKind.EXTERNAL_REF
        and external is not None
        and external.source == SOURCE
    )


def resolve_formula(
    contract: MetricContract,
    read: ManifestRead | None,
    *,
    database: str | None,
    declared: DeclaredTable,
    proven: frozenset[str] = PROVEN_PARITY,
) -> FormulaResolution:
    """Resolve the contract's formula from `read` (see the module docstring).

    `database` is the dbt database declared with the artifact — empty for an engine with
    two-part names, None when nothing was declared. `declared` is the grain table as the native
    model declares it: the context of the parity rows. `ValueError` if the contract's formula is
    not one this source defines.
    """
    external = contract.formula.external
    if not is_dbt_formula(contract) or external is None:
        raise ValueError(f"the formula is not an external_ref of {SOURCE}")
    if read is None:
        gap = ExternalGap(
            SOURCE_MISSING,
            Severity.BLOCK,
            "",
            f"the formula is defined in a {SOURCE} artifact, and none was given",
        )
        return FormulaResolution(None, "", (gap,))
    if read.manifest is None:
        problems = tuple(
            ExternalGap(problem.code, Severity.BLOCK, problem.path, problem.reason)
            for problem in read.problems
        )
        return FormulaResolution(None, "", problems)
    manifest = read.manifest
    if not manifest.has_metric(external.ref):
        gap = ExternalGap(
            REF_MISSING,
            Severity.BLOCK,
            "$.metrics",
            f"the artifact has no metric {external.ref!r}",
        )
        return FormulaResolution(None, "", (gap,))
    digest = manifest.closure_digest(external.ref)
    if digest != external.source_revision:
        gap = ExternalGap(
            REVISION_MISMATCH,
            Severity.BLOCK,
            "",
            f"the closure of metric {external.ref!r} is {digest}; "
            f"the contract pins {external.source_revision}",
        )
        return FormulaResolution(None, digest, (gap,))

    resolver = _Resolver(manifest, contract, database, declared)
    formula, forms = resolver.resolve(external.ref)
    # A ratio over one metric twice reads it twice; one fact is one gap.
    gaps = list(dict.fromkeys(resolver.gaps))
    if formula is None or gaps:
        return FormulaResolution(None, digest, tuple(gaps))
    context = _context(contract, declared)
    rows = frozenset(f"{context}:{form}" for form in forms)
    missing = sorted(rows - proven)
    if missing:
        gap = ExternalGap(
            PARITY_UNPROVEN,
            Severity.REVIEW,
            manifest.metric_path(external.ref),
            "no parity run has shown MetricFlow and the reference SQL to agree on "
            + ", ".join(missing),
        )
        return FormulaResolution(None, digest, (gap,), rows)
    return FormulaResolution(formula, digest, (), rows)


def _engine(declared: DeclaredTable) -> str:
    engine = declared.engine.lower()
    return _ENGINES.get(engine, engine) or UNDECLARED


def _type_class(declared: DeclaredTable, column: str, *, nullable: bool = True) -> str:
    """The class of the column's declared type a parity row names; `nullable` False where
    the run's column held no NULL, and a type saying it may is not the run's."""
    kind = declared.column_types.get(column)
    if not kind:
        return UNDECLARED
    engine = _engine(declared)
    if engine == "clickhouse":
        return _clickhouse_class(kind, nullable=nullable)
    if engine != "postgres":
        return UNCLASSIFIED
    found = _PG_TYPE.fullmatch(kind.strip().lower())
    if found is None:
        return UNCLASSIFIED
    return _PG_CLASSES.get(" ".join(found.group(1).split()), UNCLASSIFIED)


def _clickhouse_class(kind: str, *, nullable: bool) -> str:
    """`CLICKHOUSE_CLASSES` of the type as written: `Nullable(T)` is `T`'s class where the
    column may hold NULL, and nothing else is unwrapped or respelled."""
    wrapper = "Nullable("
    if nullable and kind.startswith(wrapper) and kind.endswith(")"):
        kind = kind[len(wrapper) : -1]
    return CLICKHOUSE_CLASSES.get(kind, UNCLASSIFIED)


def _context(contract: MetricContract, declared: DeclaredTable) -> str:
    """``<engine>.<event time>``: what a parity row holds in, beyond the formula itself.

    MetricFlow groups by the day of the aggregation time and the reference SQL by the grain
    keys; the two rows are one only where the event time column is a grain key holding days.
    """
    event_time = contract.time.event_time_column
    if event_time not in contract.grain.keys:
        return f"{_engine(declared)}.{OUTSIDE_GRAIN}"
    # the run's event time column held no NULL day to group by
    time = _type_class(declared, event_time, nullable=False)
    return f"{_engine(declared)}.{time}"


@dataclass(frozen=True)
class _Term:
    """One simple metric as a term: what it reads and how, and the parity rows it needs."""

    model: str
    #: The semantic model and dimension it aggregates over in time, None if the artifact
    #: names none.
    time: tuple[str, str] | None
    #: None where the artifact's expression or aggregate is outside the supported subset.
    column: str | None
    agg: Aggregation | None
    agg_name: str
    #: `<dialect>.<aggregate>.<class of the column's type>` — the term in a parity row.
    form: str
    agg_path: str
    rows: frozenset[str]


class _Resolver:
    def __init__(
        self,
        manifest: SemanticManifest,
        contract: MetricContract,
        database: str | None,
        declared: DeclaredTable,
    ) -> None:
        self._manifest = manifest
        self._contract = contract
        self._database = database
        self._declared = declared
        self.gaps: list[ExternalGap] = []

    def resolve(self, name: str) -> tuple[MetricFormula | None, frozenset[str]]:
        metric = self._manifest.metric(name)
        path = self._manifest.metric_path(name)
        if metric["type_params"].get("is_private") is True:
            self._gap(
                PRIVATE_METRIC,
                Severity.REVIEW,
                f"{path}.type_params.is_private",
                f"metric {name!r} is private in dbt",
            )
        if metric["type"] == "simple":
            return self._aggregate(name)
        if metric["type"] == "ratio":
            return self._ratio(name, metric, path)
        self._construct(f"{path}.type", f"a {metric['type']} metric")
        return None, frozenset()

    def _aggregate(self, name: str) -> tuple[MetricFormula | None, frozenset[str]]:
        term = self._simple(name)
        if term is None:
            return None, frozenset()
        self._relation(term.model)
        if term.agg in _NOT_SUMMABLE:
            self._additivity(term.agg_path, f"{term.agg_name} does not add up by sum")
        typed = self._typed(term)
        if typed is None:
            return None, frozenset()
        return self._formula(name, kind=FormulaKind.TYPED_AGGREGATE, aggregate=typed), term.rows

    def _ratio(
        self, name: str, metric: JsonObject, path: str
    ) -> tuple[MetricFormula | None, frozenset[str]]:
        params = metric["type_params"]
        params_path = f"{path}.type_params"
        self._unset(metric, path, _METRIC)
        self._unset(params, params_path, _RATIO_PARAMS)
        terms: list[_Term | None] = []
        for side in ("numerator", "denominator"):
            reference = params.get(side)
            if reference is None:
                self._construct(f"{params_path}.{side}", f"a ratio without a {side}")
                terms.append(None)
                continue
            self._unset(reference, f"{params_path}.{side}", _METRIC_INPUT)
            input_metric = self._manifest.metric(reference["name"])
            if input_metric["type"] != "simple":
                self._construct(
                    f"{self._manifest.metric_path(reference['name'])}.type",
                    f"a ratio over a {input_metric['type']} metric",
                )
                terms.append(None)
                continue
            terms.append(self._simple(reference["name"]))
        numerator, denominator = terms
        if numerator is None or denominator is None:
            return None, frozenset()

        # those of the numerator, then those of the denominator, each once: as dbt writes them
        expected: list[JsonObject] = []
        for side in ("numerator", "denominator"):
            input_metric = self._manifest.metric(params[side]["name"])
            for measure in input_metric["type_params"].get("input_measures", []):
                if measure not in expected:
                    expected.append(measure)
        if params.get("input_measures", []) != expected:
            self._construct(
                f"{params_path}.input_measures",
                "input measures other than those of the numerator and the denominator",
            )
        if numerator.model != denominator.model:
            self._gap(
                RATIO_UNPROVEN,
                Severity.REVIEW,
                f"{params_path}.denominator",
                f"the numerator reads semantic model {numerator.model!r} and the denominator "
                f"{denominator.model!r}; a typed ratio has one table",
            )
        elif numerator.time != denominator.time:
            self._gap(
                RATIO_UNPROVEN,
                Severity.REVIEW,
                f"{params_path}.denominator",
                "the numerator and the denominator aggregate over different time dimensions",
            )
        for model in dict.fromkeys((numerator.model, denominator.model)):
            self._relation(model)
        self._additivity(f"{path}.type", "a ratio does not add up by sum")

        top, bottom = self._typed(numerator), self._typed(denominator)
        if top is None or bottom is None:
            return None, frozenset()
        formula = self._formula(
            name, kind=FormulaKind.TYPED_RATIO, numerator=top, denominator=bottom
        )
        form = f"ratio.{numerator.form}/{denominator.form}"
        return formula, numerator.rows | denominator.rows | {form}

    def _simple(self, name: str) -> _Term | None:
        """A simple metric as a term; None if it names neither or both ways to aggregate."""
        metric = self._manifest.metric(name)
        path = self._manifest.metric_path(name)
        params = metric["type_params"]
        params_path = f"{path}.type_params"
        self._unset(metric, path, _METRIC)
        self._unset(params, params_path, _SIMPLE_PARAMS)
        measure_input = params.get("measure")
        aggregation = params.get("metric_aggregation_params")
        if measure_input is not None and aggregation is None:
            return self._legacy(params, params_path, measure_input)
        if aggregation is not None and measure_input is None:
            return self._v2(params, params_path, aggregation)
        self._construct(
            params_path,
            "a simple metric with both a measure and aggregation " "parameters, or neither",
        )
        return None

    def _legacy(self, params: JsonObject, params_path: str, measure_input: JsonObject) -> _Term:
        self._unset(measure_input, f"{params_path}.measure", _INPUT_MEASURE)
        if params.get("input_measures", []) != [measure_input]:
            self._construct(
                f"{params_path}.input_measures", "input measures other than the metric's measure"
            )
        model, measure = self._manifest.measure(measure_input["name"])
        path = self._manifest.measure_path(measure_input["name"])
        rows: set[str] = set()
        agg = self._aggregation(measure["agg"], f"{path}.agg")
        self._agg_params(measure.get("agg_params"), f"{path}.agg_params")
        if measure.get("non_additive_dimension") is not None:
            self._construct(f"{path}.non_additive_dimension", "a non-additive dimension")

        expression = measure.get("expr")
        if expression is None:
            # dbt reads the measure's name as its column
            column = self._column(measure["name"], f"{path}.name")
            rows.add(f"{LEGACY}.measure_without_expr")
        else:
            column = self._column(expression, f"{path}.expr")
        metric_expression = params.get("expr")
        if metric_expression is not None:
            if metric_expression != (measure["name"] if expression is None else expression):
                self._construct(
                    f"{params_path}.expr", "a metric expression other than the measure's"
                )
            rows.add(f"{LEGACY}.metric_expr")

        dimension = measure.get("agg_time_dimension")
        where = f"{path}.agg_time_dimension"
        if dimension is None:
            defaults = self._manifest.semantic_model(model).get("defaults") or {}
            dimension = defaults.get("agg_time_dimension")
            if dimension is not None:
                where = f"{self._manifest.semantic_model_path(model)}.defaults.agg_time_dimension"
        time = self._time(model, dimension, where)
        form = self._form(LEGACY, measure["agg"], column)
        rows.add(form)
        return _Term(model, time, column, agg, measure["agg"], form, f"{path}.agg", frozenset(rows))

    def _v2(self, params: JsonObject, params_path: str, aggregation: JsonObject) -> _Term:
        if params.get("input_measures", []) != []:
            self._construct(f"{params_path}.input_measures", "input measures of a V2 metric")
        path = f"{params_path}.metric_aggregation_params"
        agg = self._aggregation(aggregation["agg"], f"{path}.agg")
        self._agg_params(aggregation.get("agg_params"), f"{path}.agg_params")
        if aggregation.get("non_additive_dimension") is not None:
            self._construct(f"{path}.non_additive_dimension", "a non-additive dimension")
        expression = params.get("expr")
        column: str | None = None
        if expression is None:
            self._construct(f"{params_path}.expr", "a V2 metric without an expression")
        else:
            column = self._column(expression, f"{params_path}.expr")
        model = aggregation["semantic_model"]
        time = self._time(
            model, aggregation.get("agg_time_dimension"), f"{path}.agg_time_dimension"
        )
        form = self._form(V2, aggregation["agg"], column)
        return _Term(
            model, time, column, agg, aggregation["agg"], form, f"{path}.agg", frozenset({form})
        )

    def _form(self, dialect: str, agg: str, column: str | None) -> str:
        kind = UNDECLARED if column is None else _type_class(self._declared, column)
        return f"{dialect}.{agg}.{kind}"

    def _time(self, model_name: str, dimension: str | None, where: str) -> tuple[str, str] | None:
        """Check the aggregation time against the contract's event time; its key, or None."""
        if dimension is None:
            self._gap(
                TIME_UNPROVEN,
                Severity.REVIEW,
                where,
                "the artifact names no aggregation time dimension",
            )
            return None
        model = self._manifest.semantic_model(model_name)
        model_path = self._manifest.semantic_model_path(model_name)
        position = next(
            index for index, item in enumerate(model["dimensions"]) if item["name"] == dimension
        )
        found = model["dimensions"][position]
        path = f"{model_path}.dimensions[{position}]"
        if found["type"] != "time":
            self._gap(
                TIME_UNPROVEN,
                Severity.REVIEW,
                f"{path}.type",
                f"aggregation time dimension {dimension!r} is not a time dimension",
            )
        expression = found.get("expr")
        column = dimension if expression is None else expression
        event_time = self._contract.time.event_time_column
        if not self._is_column(column):
            self._gap(
                TIME_UNPROVEN,
                Severity.REVIEW,
                f"{path}.expr",
                f"the expression of time dimension {dimension!r} is not one column",
            )
        elif column != event_time:
            self._gap(
                TIME_MISMATCH,
                Severity.BLOCK,
                f"{path}.name" if expression is None else f"{path}.expr",
                f"time dimension {dimension!r} reads column {column!r}; the contract's event "
                f"time column is {event_time!r}",
            )
        granularity = found.get("type_params")
        if granularity is None:
            self._gap(
                TIME_UNPROVEN,
                Severity.REVIEW,
                f"{path}.type_params",
                f"the artifact gives time dimension {dimension!r} no granularity",
            )
        elif granularity["time_granularity"] != "day":
            self._gap(
                TIME_UNPROVEN,
                Severity.REVIEW,
                f"{path}.type_params.time_granularity",
                f"time dimension {dimension!r} is not of day granularity",
            )
        return model_name, dimension

    def _relation(self, model_name: str) -> None:
        model = self._manifest.semantic_model(model_name)
        path = f"{self._manifest.semantic_model_path(model_name)}.node_relation"
        relation = model["node_relation"]
        table = self._contract.grain.table
        if f"{relation['schema_name']}.{relation['alias']}" != table:
            self._gap(
                RELATION_MISMATCH,
                Severity.BLOCK,
                path,
                f"semantic model {model_name!r} reads a relation other than the grain table "
                f"{table}",
            )
        database = relation.get("database") or ""
        if self._database is None:
            if database:
                self._gap(
                    RELATION_UNPROVEN,
                    Severity.REVIEW,
                    f"{path}.database",
                    f"semantic model {model_name!r} names a database, and none was declared "
                    "with the artifact",
                )
        elif database != self._database:
            self._gap(
                RELATION_MISMATCH,
                Severity.BLOCK,
                f"{path}.database",
                f"semantic model {model_name!r} reads another database than the declared "
                f"{self._database!r}",
            )

    def _additivity(self, path: str, why: str) -> None:
        if self._contract.additivity is Additivity.ADDITIVE:
            self._gap(
                ADDITIVITY_UNPROVEN,
                Severity.REVIEW,
                path,
                f"{why}, and the contract declares the metric additive",
            )

    def _aggregation(self, name: str, path: str) -> Aggregation | None:
        found = _AGGREGATION.get(name)
        if found is None:
            self._construct(path, f"aggregation {name}")
        return found

    def _agg_params(self, value: JsonObject | None, path: str) -> None:
        if value is None:
            return
        if {key: value.get(key, default) for key, default in _DEFAULT_AGG_PARAMS.items()} != (
            _DEFAULT_AGG_PARAMS
        ):
            self._construct(path, "aggregation parameters other than the defaults")

    def _column(self, expression: str, path: str) -> str | None:
        if self._is_column(expression):
            return expression
        self._construct(path, "a column that is not one lower-case identifier")
        return None

    @staticmethod
    def _is_column(expression: str) -> bool:
        return _COLUMN.fullmatch(expression) is not None and expression not in _VALUE_WORDS

    def _unset(self, item: JsonObject, path: str, keys: _Unset) -> None:
        for key, (neutral, what) in keys.items():
            if item.get(key) not in neutral:
                self._construct(f"{path}.{key}", what)

    def _typed(self, term: _Term) -> dict[str, str] | None:
        """The term of a typed formula, as data its validator reads; None if unresolved."""
        if term.column is None or term.agg is None:
            return None
        return {"table": self._contract.grain.table, "column": term.column, "agg": term.agg.value}

    def _formula(self, name: str, **fields: Any) -> MetricFormula | None:
        try:
            return MetricFormula.model_validate(fields)
        except ValidationError:
            self._construct(
                self._manifest.metric_path(name),
                "a term the typed formula does not take: the grain table is not a lower-case "
                "schema.table name",
            )
            return None

    def _construct(self, path: str, what: str) -> None:
        self._gap(
            CONSTRUCT_UNSUPPORTED,
            Severity.REVIEW,
            path,
            f"outside the subset a typed formula is resolved from: {what}",
        )

    def _gap(self, code: str, severity: Severity, path: str, reason: str) -> None:
        self.gaps.append(ExternalGap(code, severity, path, reason))
