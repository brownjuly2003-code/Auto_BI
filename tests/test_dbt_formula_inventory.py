"""What keeps a dbt formula `external_ref`: the inventory (ADR 0008, slice S5).

`docs/specs/dbt-formula-inventory.md` names every reason the resolver and the reader give, one
row each. Each row is resolved here as a case — on the artifacts dbt wrote
(`tests/fixtures/dbt_semantic_manifest`), changed in memory where they do not carry the
construct — and the tables are held to the code: every gap code, every place that names or
makes a gap (and no mention of what names or makes one elsewhere), every key the resolver reads
as a construct (on each side of a ratio; of `type_params`, on each kind of metric that does not
take its number from it, by the schema), every aggregation, `agg_params` key and metric type of
the schema it does not resolve, every value of an enumeration where it compares one, the empty
value of a key's kind — an empty string, 0, an empty object or list — where the resolver reads
the key, the empty string where an expression or a name stands in for a column, both sources of
a column (an `expr` and a name), and where it ends the reading early.
"""

from __future__ import annotations

import ast
import copy
import inspect
import json
import re
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType
from typing import Any

import pytest

from auto_bi.semantic import metricflow, metricflow_formula
from auto_bi.semantic.metricflow import ManifestRead
from auto_bi.semantic.metricflow_formula import (
    _AGGREGATION,
    _DEFAULT_AGG_PARAMS,
    _INPUT_MEASURE,
    _METRIC,
    _METRIC_INPUT,
    _RATIO_PARAMS,
    _SIMPLE_PARAMS,
    CONSTRUCT_UNSUPPORTED,
    PARITY_UNPROVEN,
    PRIVATE_METRIC,
    PROVEN_PARITY,
    TIME_MISMATCH,
    DeclaredTable,
    ExternalGap,
    _engine,
    _Resolver,
    resolve_formula,
)
from tests.test_metricflow_formula import (
    CONSTRUCT_PARAMS,
    FILTER,
    FIXTURES,
    REPO,
    SALES,
    SET_VALUES,
    _contract,
    _data,
    _dimension_path,
    _gaps,
    _index,
    _measure,
    _measure_input,
    _metric,
    _metric_path,
    _model,
    _read,
    _resolve,
    _second_revenue,
    _second_time_dimension,
    _set_measure_input,
    _set_metric,
    _set_metric_input,
    _set_params,
    _v2_with_granularity,
)

SPEC = REPO / "docs" / "specs" / "dbt-formula-inventory.md"
ENUMS: dict[str, list[str]] = json.loads((FIXTURES / "dsi_0.213.0_schema.json").read_bytes())[
    "enums"
]
PIN = "sha256:" + "0" * 64
SIDES = ("numerator", "denominator")
#: A value other than the default for each key of `agg_params`.
AGG_PARAMS_SET = {
    "percentile": 0.5,
    "use_discrete_percentile": True,
    "use_approximate_percentile": True,
}
#: The empty value of each key of `agg_params`, and whether it is the default: a percentile of 0
#: is set as much as another.
AGG_PARAMS_EMPTY = {
    "percentile": (0, False),
    "use_discrete_percentile": (False, True),
    "use_approximate_percentile": (False, True),
}
#: Expressions that are not one column: SQL, a quoted or an upper-case name, a value word, and the
#: empty string, which is an expression as much as another.
NOT_A_COLUMN = ("revenue - gross_margin", '"revenue"', "Revenue", "null", "true", "")
#: The path cell of the one row whose gaps are each at the path of a problem the reader found.
PROBLEM_PATHS = "the problem's"
PROBLEM_ROW = "artifact.invalid"

Gaps = list[tuple[str, str, str]]


@dataclass(frozen=True)
class Row:
    id: str
    code: str
    severity: str
    #: The shape of the gap's path; None where the case checks each path itself.
    path: str | None


def _shape(path: str) -> str:
    """A path with every position folded: `$.metrics[3].filter` and `$.metrics[m].filter` meet."""
    return re.sub(r"\[(?:\d+|[a-z])\]", "[]", path)


def _spec_rows() -> dict[str, Row]:
    rows: dict[str, Row] = {}
    for line in SPEC.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split(" | ")]
        assert len(cells) == 5, line
        ident, _what, code, severity, path = cells
        found = re.fullmatch(r"`([a-z0-9_.\-]+)`", ident)
        assert found is not None, line
        assert re.fullmatch(r"`gap\.external_[a-z_]+`", code), line
        assert severity in ("block", "review"), line
        if path == "none":
            shape: str | None = ""
        elif path == PROBLEM_PATHS:
            shape = None
        else:
            assert re.fullmatch(r"`\$[^`]*`", path), line
            shape = _shape(path.strip("`"))
        assert found.group(1) not in rows, f"row {found.group(1)} twice"
        rows[found.group(1)] = Row(found.group(1), code.strip("`"), severity, shape)
    return rows


ROWS = _spec_rows()
CASES: dict[str, Callable[[], Gaps]] = {}


def case(ident: str) -> Callable[[Callable[[], Gaps]], Callable[[], Gaps]]:
    def register(make: Callable[[], Gaps]) -> Callable[[], Gaps]:
        assert ident not in CASES, ident
        CASES[ident] = make
        return make

    return register


def _against(read: ManifestRead | None, ref: str = "revenue") -> Gaps:
    """The gaps of a contract that pins nothing the artifact gives."""
    return _gaps(resolve_formula(_contract(ref, PIN), read, database="x", declared=SALES))


# --- the artifact ---------------------------------------------------------------------


@case("artifact.missing")
def _artifact_missing() -> Gaps:
    return _against(None)


@case("artifact.invalid")
def _artifact_invalid() -> Gaps:
    data = _data("legacy")
    params = _metric(data, "revenue")["type_params"]
    params["measure"]["name"] = params["input_measures"][0]["name"] = "absent"
    read = _read(data)
    gaps = _against(read)
    assert read.problems
    assert [path for *_, path in gaps] == [problem.path for problem in read.problems]
    return gaps


@case("artifact.version")
def _artifact_version() -> Gaps:
    data = _data("legacy")
    data["project_configuration"]["dsi_package_version"]["minor_version"] = "214"
    return _against(_read(data))


@case("artifact.ref")
def _artifact_ref() -> Gaps:
    return _against(_read(_data("legacy")), "absent")


@case("artifact.pin")
def _artifact_pin() -> Gaps:
    return _against(_read(_data("legacy")))


# --- compatibility with the contract --------------------------------------------------


@case("relation.table")
def _relation_table() -> Gaps:
    return _gaps(_resolve(_data("legacy"), "revenue", table="dm.sales"))


def _named_database() -> dict[str, Any]:
    data = _data("legacy")
    _model(data, "sales_daily")["node_relation"]["database"] = "analytics"
    return data


@case("relation.database")
def _relation_database() -> Gaps:
    return _gaps(_resolve(_named_database(), "revenue", database="warehouse"))


@case("relation.database-undeclared")
def _relation_database_undeclared() -> Gaps:
    return _gaps(_resolve(_named_database(), "revenue", database=None))


@case("time.none-legacy")
def _time_none_legacy() -> Gaps:
    data = _data("legacy")
    assert _measure(data, "revenue")["agg_time_dimension"] is None
    _model(data, "sales_daily")["defaults"] = None
    return _gaps(_resolve(data, "revenue"))


@case("time.none-v2")
def _time_none_v2() -> Gaps:
    data = _v2_with_granularity()
    _metric(data, "revenue")["type_params"]["metric_aggregation_params"][
        "agg_time_dimension"
    ] = None
    return _gaps(_resolve(data, "revenue"))


def _time_dimension(**change: Any) -> Gaps:
    data = _data("legacy")
    dimension = _model(data, "sales_daily")["dimensions"][0]
    assert dimension["name"] == "date"
    dimension.update(change)
    return _gaps(_resolve(data, "revenue"))


@case("time.not-time")
def _time_not_time() -> Gaps:
    return _time_dimension(type="categorical")


@case("time.expression")
def _time_expression() -> Gaps:
    return _same([_time_dimension(expr=expr) for expr in ("date_trunc('day', ts)", *NOT_A_COLUMN)])


@case("time.name")
def _time_name() -> Gaps:
    def renamed(name: str, holder: str) -> Gaps:
        data = _data("legacy")
        model = _model(data, "sales_daily")
        model["dimensions"][0]["name"] = name
        if holder == "defaults":
            model["defaults"]["agg_time_dimension"] = name
        else:
            model["defaults"] = None
            _measure(data, "revenue")["agg_time_dimension"] = name
        return _gaps(_resolve(data, "revenue"))

    # named by the model's default or by the measure; the empty string is a name as much as another
    names = ("null", "Date", "")
    return _same([renamed(name, holder) for name in names for holder in ("defaults", "measure")])


@case("time.column-name")
def _time_column_name() -> Gaps:
    return _gaps(_resolve(_data("legacy"), "revenue", event_time="day"))


@case("time.column-expr")
def _time_column_expr() -> Gaps:
    return _time_dimension(expr="order_date")


@case("time.no-granularity")
def _time_no_granularity() -> Gaps:
    # as dbt-core 1.12.6 writes every V2 time dimension
    return _gaps(_resolve(_data("v2"), "revenue"))


@case("time.granularity")
def _time_granularity() -> Gaps:
    return _time_dimension(type_params={"time_granularity": "month", "validity_params": None})


@case("ratio.models")
def _ratio_models() -> Gaps:
    data = _data("legacy")
    other = copy.deepcopy(_model(data, "sales_daily"))
    other["name"] = "sales_daily_b"
    measure = copy.deepcopy(_measure(data, "revenue"))
    measure["name"] = "revenue_b"
    other["measures"] = [measure]
    data["semantic_models"].append(other)
    _second_revenue(data, measure)
    return _gaps(_resolve(data, "gross_margin_pct"))


@case("ratio.times")
def _ratio_times() -> Gaps:
    data = _data("legacy")
    _second_time_dimension(data, "date_b", "date")
    measure = copy.deepcopy(_measure(data, "revenue"))
    measure["name"], measure["agg_time_dimension"] = "revenue_b", "date_b"
    _model(data, "sales_daily")["measures"].append(measure)
    _second_revenue(data, measure)
    return _gaps(_resolve(data, "gross_margin_pct"))


@case("additivity.aggregate-legacy")
def _additivity_aggregate_legacy() -> Gaps:
    return _gaps(_resolve(_data("legacy"), "active_stores", additivity="additive"))


@case("additivity.aggregate-v2")
def _additivity_aggregate_v2() -> Gaps:
    return _gaps(_resolve(_v2_with_granularity(), "active_stores", additivity="additive"))


@case("additivity.ratio")
def _additivity_ratio() -> Gaps:
    return _gaps(_resolve(_data("legacy"), "gross_margin_pct", additivity="additive"))


@case("private")
def _private() -> Gaps:
    # as dbt wrote it
    return _gaps(_resolve(_v2_with_granularity("wide_v2"), "revenue_hidden"))


# --- constructs -----------------------------------------------------------------------


def _same(variants: list[Gaps]) -> Gaps:
    """Each variant a row names gives the one gap."""
    first, *others = variants
    assert all(other == first for other in others), variants
    return first


def _shaped(gaps: Gaps) -> Gaps:
    """The gaps with the shape of their paths: those of two metrics compared."""
    return [(code, severity, _shape(path)) for code, severity, path in gaps]


def _metric_type(ref: str) -> Callable[[], Gaps]:
    def make() -> Gaps:
        # as dbt wrote it
        return _gaps(_resolve(_data("wide_legacy"), ref))

    return make


for _type, _ref in (
    ("cumulative", "revenue_mtd"),
    ("derived", "revenue_growth"),
    ("conversion", "visit_to_purchase"),
):
    case(f"type.{_type}")(_metric_type(_ref))


@case("ratio.input-type")
def _ratio_input_type() -> Gaps:
    data = _data("legacy")
    params = _metric(data, "gross_margin_pct")["type_params"]
    params["numerator"]["name"] = "revenue_cum"
    params["input_measures"] = [_measure_input("revenue"), _measure_input("revenue")]
    return _gaps(_resolve(data, "gross_margin_pct"))


def _ratio_side_missing(side: str) -> Callable[[], Gaps]:
    def make() -> Gaps:
        data = _data("legacy")
        _metric(data, "gross_margin_pct")["type_params"][side] = None
        return _gaps(_resolve(data, "gross_margin_pct"))

    return make


for _side in ("numerator", "denominator"):
    case(f"ratio.{_side}-missing")(_ratio_side_missing(_side))


@case("ratio.input-measures")
def _ratio_input_measures() -> Gaps:
    data = _data("legacy")
    params = _metric(data, "gross_margin_pct")["type_params"]
    params["input_measures"] = params["input_measures"][::-1]
    return _gaps(_resolve(data, "gross_margin_pct"))


def _set(kind: str, change: Callable[[dict[str, Any]], tuple[str, str]]) -> Callable[[], Gaps]:
    """A key a resolution reads, set: its gap is at the path the change names."""

    def make() -> Gaps:
        data = _data(kind)
        ref, path = change(data)
        gaps = _gaps(_resolve(data, ref))
        assert [where for *_, where in gaps] == [path]
        return gaps

    return make


for _key in _METRIC:
    case(f"metric.{_key}")(_set("legacy", _set_metric("revenue", _key)))
for _key in dict.fromkeys([*_SIMPLE_PARAMS, *_RATIO_PARAMS]):
    _owner = "revenue" if _key in CONSTRUCT_PARAMS["simple"] else "gross_margin_pct"
    case(f"params.{_key}")(_set("legacy", _set_params(_owner, _key)))
for _key in _INPUT_MEASURE:
    case(f"measure-input.{_key}")(_set("legacy", _set_measure_input(_key)))
for _side in SIDES:
    for _key in _METRIC_INPUT:
        case(f"metric-input.{_side}.{_key}")(_set("legacy", _set_metric_input(_key, _side)))


@case("simple.both-or-neither")
def _simple_both_or_neither() -> Gaps:
    data = _data("legacy")
    params = _metric(data, "revenue")["type_params"]
    params["measure"], params["input_measures"] = None, []
    return _gaps(_resolve(data, "revenue"))


@case("legacy.input-measures")
def _legacy_input_measures() -> Gaps:
    data = _data("legacy")
    _metric(data, "revenue")["type_params"]["input_measures"].append(_measure_input("gross_margin"))
    return _gaps(_resolve(data, "revenue"))


def _legacy_measure(**change: Any) -> Callable[[], Gaps]:
    def make() -> Gaps:
        data = _data("legacy")
        _measure(data, "revenue").update(copy.deepcopy(change))
        return _gaps(_resolve(data, "revenue"))

    return make


def _v2_aggregation(**change: Any) -> Callable[[], Gaps]:
    def make() -> Gaps:
        data = _v2_with_granularity()
        params = _metric(data, "revenue")["type_params"]["metric_aggregation_params"]
        params.update(copy.deepcopy(change))
        return _gaps(_resolve(data, "revenue"))

    return make


_UNSUPPORTED_AGGREGATIONS = ("sum_boolean", "percentile", "median")
for _agg in _UNSUPPORTED_AGGREGATIONS:
    case(f"legacy.agg.{_agg}")(_legacy_measure(agg=_agg))
    case(f"v2.agg.{_agg}")(_v2_aggregation(agg=_agg))
for _key, _value in AGG_PARAMS_SET.items():
    _params = {**_DEFAULT_AGG_PARAMS, _key: _value}
    case(f"legacy.agg_params.{_key}")(_legacy_measure(agg_params=_params))
    case(f"v2.agg_params.{_key}")(_v2_aggregation(agg_params=_params))


@case("legacy.non-additive-dimension")
def _legacy_non_additive_dimension() -> Gaps:
    data = _data("wide_legacy")
    _measure(data, "revenue")["non_additive_dimension"] = copy.deepcopy(
        _measure(data, "revenue_last")["non_additive_dimension"]
    )
    return _gaps(_resolve(data, "revenue"))


#: Expressions that are not one lower-case column.
@case("legacy.expr")
def _legacy_expr() -> Gaps:
    return _same([_legacy_measure(expr=expr)() for expr in NOT_A_COLUMN])


def _measure_renamed(name: str) -> Gaps:
    data = _data("wide_legacy")
    _measure(data, "gross_margin")["name"] = name
    for metric in data["metrics"]:
        params = metric["type_params"]
        for measure in (params.get("measure"), *params["input_measures"]):
            if measure is not None and measure["name"] == "gross_margin":
                measure["name"] = name
    return _gaps(_resolve(data, "gross_margin_total"))


@case("legacy.measure-name")
def _legacy_measure_name() -> Gaps:
    return _same([_measure_renamed(name) for name in ("null", "Gross_Margin")])


@case("legacy.metric-expr")
def _legacy_metric_expr() -> Gaps:
    def held(metric: str, expr: str) -> Gaps:
        data = _data("wide_legacy")
        _metric(data, metric)["type_params"]["expr"] = expr
        return _gaps(_resolve(data, metric))

    # held to the measure's `expr`, and, of a measure without one, to its name; the empty string
    # is an expression other than either
    assert _measure(_data("wide_legacy"), "gross_margin")["expr"] is None
    held_to_expr = _same([held("revenue_created", expr) for expr in ("gross_margin", "")])
    held_to_name = _same([held("gross_margin_total", expr) for expr in ("revenue", "")])
    assert _shaped(held_to_name) == _shaped(held_to_expr)
    return held_to_expr


@case("v2.input-measures")
def _v2_input_measures() -> Gaps:
    data = _v2_with_granularity()
    # a measure for it to name, as the legacy artifact holds it
    _model(data, "sales_daily")["measures"] = [_measure(_data("legacy"), "revenue")]
    _metric(data, "revenue")["type_params"]["input_measures"] = [_measure_input("revenue")]
    return _gaps(_resolve(data, "revenue"))


@case("v2.non-additive-dimension")
def _v2_non_additive_dimension() -> Gaps:
    # as dbt wrote it
    return _gaps(_resolve(_v2_with_granularity("wide_v2"), "revenue_last"))


def _v2_expression(expr: str | None) -> Callable[[], Gaps]:
    def make() -> Gaps:
        data = _v2_with_granularity()
        _metric(data, "revenue")["type_params"]["expr"] = expr
        return _gaps(_resolve(data, "revenue"))

    return make


case("v2.no-expr")(_v2_expression(None))


@case("v2.expr")
def _v2_expr() -> Gaps:
    return _same([_v2_expression(expr)() for expr in NOT_A_COLUMN])


@case("formula.table")
def _formula_table() -> Gaps:
    data = _data("legacy")
    _model(data, "sales_daily")["node_relation"]["alias"] = "Sales_Daily"
    return _gaps(_resolve(data, "revenue", table="dm.Sales_Daily"))


# --- parity ---------------------------------------------------------------------------


def _parity(
    kind: str = "legacy", ref: str = "revenue", *, types: dict[str, str] | None = None, **kw: Any
) -> Gaps:
    data = _v2_with_granularity() if kind == "v2" else _data(kind)
    declared = kw.pop(
        "declared", DeclaredTable("postgres", {**SALES.column_types, **(types or {})})
    )
    return _gaps(_resolve(data, ref, declared=declared, proven=PROVEN_PARITY, **kw))


CLICKHOUSE = DeclaredTable(
    "clickhouse",
    {
        "date": "Date",
        "store_id": "UInt32",
        "revenue": "Decimal(18, 2)",
        "gross_margin": "Decimal(18, 2)",
    },
)


#: The grain table as each engine but PostgreSQL declares it, and as no engine does.
OTHER_ENGINES = (
    CLICKHOUSE,
    DeclaredTable("greenplum", SALES.column_types),
    DeclaredTable("", SALES.column_types),
)


def _relies_on(table: DeclaredTable, ref: str = "revenue") -> frozenset[str]:
    """The parity rows the formula of `ref` relies on with the grain table declared as `table`."""
    rows = _resolve(_data("legacy"), ref, declared=table, proven=frozenset()).parity_rows
    assert rows, table
    return rows


@case("parity.engine")
def _parity_engine() -> Gaps:
    # each engine the formula's rows are not all proven on: once they are, it leaves this row
    unproven = [table for table in OTHER_ENGINES if not _relies_on(table) <= PROVEN_PARITY]
    assert unproven, "every engine is proven: the row is to go"
    return _same([_parity(declared=table) for table in unproven])


@case("parity.event-time-type")
def _parity_event_time_type() -> Gaps:
    return _parity(types={"date": "timestamp"})


@case("parity.event-time-outside-grain")
def _parity_event_time_outside_grain() -> Gaps:
    return _parity(keys=("store_id",))


@case("parity.column-type")
def _parity_column_type() -> Gaps:
    undeclared = {key: kind for key, kind in SALES.column_types.items() if key != "revenue"}
    return _same(
        [
            _parity(types={"revenue": "text"}),
            _parity(types={"revenue": "date"}),
            _parity(types={"revenue": "numeric(18,2)[]"}),
            _parity(declared=DeclaredTable("postgres", undeclared)),
        ]
    )


@case("parity.ratio-average")
def _parity_ratio_average() -> Gaps:
    data = _data("legacy")
    _measure(data, "gross_margin")["agg"] = "average"
    return _gaps(_resolve(data, "gross_margin_pct", proven=PROVEN_PARITY))


@case("parity.ratio-float")
def _parity_ratio_float() -> Gaps:
    return _parity(ref="gross_margin_pct", types={"gross_margin": "double precision"})


@case("parity.v2")
def _parity_v2() -> Gaps:
    return _parity("v2")


# --- the tests ------------------------------------------------------------------------


def test_every_row_is_a_case_and_every_case_a_row() -> None:
    assert sorted(ROWS) == sorted(CASES)


def test_only_the_row_of_the_reader_takes_the_paths_of_its_problems() -> None:
    # its case checks each path against the reader's problems; any other row skipping the
    # check of its path would hold whatever path its case gives
    assert [ident for ident, row in ROWS.items() if row.path is None] == [PROBLEM_ROW]


def _is_row(ident: str, gaps: Gaps) -> None:
    """`gaps` are the row's: its gap and severity and no other, at a path of its shape."""
    row = ROWS[ident]
    assert gaps, ident
    assert {(code, severity) for code, severity, _ in gaps} == {(row.code, row.severity)}
    if row.path is not None:
        assert [_shape(path) for *_, path in gaps] == [row.path]


@pytest.mark.parametrize("ident", sorted(ROWS))
def test_each_row_is_what_its_case_gives(ident: str) -> None:
    _is_row(ident, CASES[ident]())


def _codes(module: ModuleType) -> set[str]:
    return {
        value
        for name, value in vars(module).items()
        if name.isupper() and isinstance(value, str) and value.startswith("gap.external_")
    }


def test_every_gap_code_is_in_the_inventory() -> None:
    assert {row.code for row in ROWS.values()} == _codes(metricflow) | _codes(metricflow_formula)


def test_proven_rows_are_those_the_spec_counts() -> None:
    text = " ".join(SPEC.read_text(encoding="utf-8").split())
    counts = Counter(row.split(":", 1)[0] for row in PROVEN_PARITY)
    stated = " and ".join(
        f"{count} rows in the context `{context}`" for context, count in sorted(counts.items())
    )
    assert f"Proven today are {stated} (" in text


def test_an_engine_the_rows_are_proven_on_resolves() -> None:
    # the other side of `parity.engine`: today PostgreSQL and ClickHouse
    tables = [SALES, *OTHER_ENGINES]
    proven = [table for table in tables if _relies_on(table) <= PROVEN_PARITY]
    assert [_engine(table) for table in proven] == ["postgres", "clickhouse"]
    for table in proven:
        assert _parity(declared=table) == [], table


def test_a_context_proven_in_part_names_the_rows_it_lacks() -> None:
    # a row proven in a context does not prove the others there: each lacking one is named
    data = _data("legacy")
    rows = _relies_on(SALES, "gross_margin_pct")
    assert len(rows) == 2
    for lacking in sorted(rows):
        resolution = _resolve(data, "gross_margin_pct", proven=PROVEN_PARITY - {lacking})
        assert _gaps(resolution) == [
            (PARITY_UNPROVEN, "review", _metric_path(data, "gross_margin_pct"))
        ]
        assert resolution.gaps[0].reason.endswith(f"agree on {lacking}")


def _family(prefix: str) -> set[str]:
    return {ident for ident in ROWS if ident.startswith(prefix)}


def test_every_key_read_as_a_construct_is_a_row() -> None:
    assert _family("metric.") == {f"metric.{key}" for key in _METRIC}
    assert _family("params.") == {f"params.{key}" for key in {*_SIMPLE_PARAMS, *_RATIO_PARAMS}}
    assert _family("measure-input.") == {f"measure-input.{key}" for key in _INPUT_MEASURE}
    assert _family("metric-input.") == {
        f"metric-input.{side}.{key}" for side in SIDES for key in _METRIC_INPUT
    }


def test_every_unresolved_aggregation_and_metric_type_of_the_schema_is_a_row() -> None:
    aggregations = set(ENUMS["AggregationType"]) - set(_AGGREGATION)
    assert aggregations == set(_UNSUPPORTED_AGGREGATIONS)
    for dialect in ("legacy", "v2"):
        assert _family(f"{dialect}.agg.") == {f"{dialect}.agg.{agg}" for agg in aggregations}
        assert _family(f"{dialect}.agg_params.") == {
            f"{dialect}.agg_params.{key}" for key in _DEFAULT_AGG_PARAMS
        }
    assert set(AGG_PARAMS_SET) == set(AGG_PARAMS_EMPTY) == set(_DEFAULT_AGG_PARAMS)
    types = set(ENUMS["MetricType"]) - {"simple", "ratio"}
    assert _family("type.") == {f"type.{kind}" for kind in types}


@pytest.mark.parametrize("agg", ENUMS["AggregationType"])
def test_each_aggregation_of_the_schema_resolves_or_is_its_row(agg: str) -> None:
    for dialect, make in (("legacy", _legacy_measure), ("v2", _v2_aggregation)):
        gaps = make(agg=agg)()
        if agg in _AGGREGATION:
            assert gaps == [], (dialect, agg)
        else:
            _is_row(f"{dialect}.agg.{agg}", gaps)


@pytest.mark.parametrize("kind", ENUMS["DimensionType"])
def test_each_dimension_type_of_the_schema_is_time_or_its_row(kind: str) -> None:
    gaps = _time_dimension(type=kind)
    if kind == "time":
        assert gaps == []
    else:
        _is_row("time.not-time", gaps)


@pytest.mark.parametrize("grain", ENUMS["TimeGranularity"])
def test_each_granularity_of_the_schema_is_day_or_its_row(grain: str) -> None:
    gaps = _time_dimension(type_params={"time_granularity": grain, "validity_params": None})
    if grain == "day":
        assert gaps == []
    else:
        _is_row("time.granularity", gaps)


#: The rows of the keys read as a construct, by the object of the reader that holds them.
_KEY_ROWS = (
    ("metric.", "Metric", _METRIC),
    ("params.", "MetricTypeParams", {**_SIMPLE_PARAMS, **_RATIO_PARAMS}),
    ("measure-input.", "MetricInputMeasure", _INPUT_MEASURE),
    *((f"metric-input.{side}.", "MetricInput", _METRIC_INPUT) for side in SIDES),
)


def _key_kinds() -> list[tuple[str, tuple[str, ...]]]:
    """Each row of a key read as a construct, and the kind of value the reader takes there."""
    objects = metricflow._MODELS_0_213_0
    return [
        (prefix + key, objects[name][key][1]) for prefix, name, keys in _KEY_ROWS for key in keys
    ]


def _any_string() -> list[str]:
    """The rows of the keys the reader takes as any string."""
    return [ident for ident, kind in _key_kinds() if kind == metricflow._STR]


def _enumerated() -> list[tuple[str, str]]:
    """The rows of the keys the reader takes as a value of an enumeration, and the enumeration."""
    return [(ident, kind[1]) for ident, kind in _key_kinds() if kind[0] == "enum"]


def test_the_enumerated_keys_are_those_of_the_spec() -> None:
    assert _enumerated() == [
        ("metric.datatype", "DataType"),
        ("params.grain_to_date", "TimeGranularity"),
    ]


@pytest.mark.parametrize(
    ("ident", "value"),
    [(ident, value) for ident, enumeration in _enumerated() for value in ENUMS[enumeration]],
)
def test_each_value_of_an_enumerated_key_is_its_row(
    ident: str, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix, key = ident.rsplit(".", 1)
    monkeypatch.setitem(SET_VALUES, key, value)
    _is_row(ident, CASES[ident]())
    # a key of either kind of metric: on a ratio as well
    set_on_ratio = {"metric": _set_metric, "params": _set_params}[prefix]
    assert prefix == "metric" or key in CONSTRUCT_PARAMS["ratio"]
    _is_row(ident, _set("legacy", set_on_ratio("gross_margin_pct", key))())


@pytest.mark.parametrize(
    "value",
    # the reader takes any string here: a granularity of the schema, or a custom one
    [*ENUMS["TimeGranularity"], "fiscal_quarter"],
)
def test_each_granularity_as_a_metric_time_granularity_is_its_row(value: str) -> None:
    data = _data("legacy")
    _metric(data, "revenue")["time_granularity"] = value
    _is_row("metric.time_granularity", _gaps(_resolve(data, "revenue")))


def test_the_keys_of_any_string_are_those_of_the_spec() -> None:
    assert _any_string() == [
        "metric.time_granularity",
        "params.expr",
        "measure-input.alias",
        "metric-input.numerator.alias",
        "metric-input.numerator.offset_to_grain",
        "metric-input.denominator.alias",
        "metric-input.denominator.offset_to_grain",
    ]


def _empty(kind: tuple[Any, ...]) -> list[Any]:
    """The empty value of a kind the reader takes, where it has one: a value read as false."""
    if kind[0] == "model":
        fields = metricflow._MODELS_0_213_0[kind[1]].values()
        # an object with a required key is never empty
        return [{}] if all(presence != metricflow._REQUIRED for presence, _ in fields) else []
    empties: dict[str, list[Any]] = {"str": [""], "int": [0], "number": [0], "bool": [False]}
    return [[]] if kind[0] == "list" else empties.get(kind[0], [])


#: The keys read as a construct whose empty value leaves the number alone, as their rows have it:
#: no input metrics, no join to the time spine. Each other empty value is set as much as another.
NEUTRAL_EMPTY = ("params.metrics", "params.join_to_timespine", "measure-input.join_to_timespine")


def _empty_values() -> list[tuple[str, Any]]:
    """Each row of a key read as a construct whose kind has an empty value, with the value."""
    objects = metricflow._MODELS_0_213_0
    return [
        (prefix + key, value)
        for prefix, name, keys in _KEY_ROWS
        for key in keys
        for value in _empty(objects[name][key][1])
    ]


def test_the_empty_values_are_those_of_the_spec() -> None:
    assert [(ident, repr(value)) for ident, value in _empty_values()] == [
        ("metric.time_granularity", "''"),
        ("params.metrics", "[]"),
        ("params.cumulative_type_params", "{}"),
        ("params.join_to_timespine", "False"),
        ("params.fill_nulls_with", "0"),
        ("params.expr", "''"),
        ("measure-input.alias", "''"),
        ("measure-input.join_to_timespine", "False"),
        ("measure-input.fill_nulls_with", "0"),
        ("metric-input.numerator.alias", "''"),
        ("metric-input.numerator.offset_to_grain", "''"),
        ("metric-input.denominator.alias", "''"),
        ("metric-input.denominator.offset_to_grain", "''"),
    ]


def _setters(ident: str) -> list[Callable[[dict[str, Any]], tuple[str, str]]]:
    """The changes that set the key of a row: on a simple metric and on a ratio, where the key is
    one of each — by the schema, not by the resolver's tables."""
    prefix, key = ident.rsplit(".", 1)
    if prefix == "metric":
        return [_set_metric(name, key) for name in ("revenue", "gross_margin_pct")]
    if prefix == "params":
        owners = (("revenue", "simple"), ("gross_margin_pct", "ratio"))
        return [_set_params(name, key) for name, kind in owners if key in CONSTRUCT_PARAMS[kind]]
    if prefix == "measure-input":
        return [_set_measure_input(key)]
    return [_set_metric_input(key, prefix.rsplit(".", 1)[1])]


@pytest.mark.parametrize(("ident", "value"), _empty_values())
def test_the_empty_value_of_a_key_is_its_row_or_leaves_the_number_alone(
    ident: str, value: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # only null leaves such a key unset: an empty value is set as much as another, but where its
    # row has it leave the number alone
    monkeypatch.setitem(SET_VALUES, ident.rsplit(".", 1)[1], value)
    setters = _setters(ident)
    assert setters, ident
    for change in setters:
        data = _data("legacy")
        ref, path = change(data)
        gaps = _gaps(_resolve(data, ref))
        if ident in NEUTRAL_EMPTY:
            assert gaps == [], (ident, ref)
        else:
            _is_row(ident, gaps)
            assert [where for *_, where in gaps] == [path], (ident, ref)


@pytest.mark.parametrize("key", sorted(AGG_PARAMS_EMPTY))
def test_the_empty_value_of_an_aggregation_parameter_is_its_row_or_the_default(key: str) -> None:
    value, is_default = AGG_PARAMS_EMPTY[key]
    assert _empty(metricflow._MODELS_0_213_0["MeasureAggregationParameters"][key][1]) == [value]
    for dialect, make in (("legacy", _legacy_measure), ("v2", _v2_aggregation)):
        gaps = make(agg_params={**_DEFAULT_AGG_PARAMS, key: value})()
        if is_default:
            assert gaps == [], (dialect, key)
        else:
            _is_row(f"{dialect}.agg_params.{key}", gaps)


@pytest.mark.parametrize("holder", ["defaults", "measure", "v2"])
def test_an_empty_time_dimension_name_is_refused_as_any_it_lacks(holder: str) -> None:
    # the empty string names no dimension of the model: the reader refuses it, not reads it as
    # no name
    data = _v2_with_granularity() if holder == "v2" else _data("legacy")
    index = _index(data["semantic_models"], "sales_daily")
    model = data["semantic_models"][index]
    if holder == "defaults":
        model["defaults"]["agg_time_dimension"] = ""
        where = f"$.semantic_models[{index}].defaults.agg_time_dimension"
    elif holder == "measure":
        position = _index(model["measures"], "revenue")
        model["measures"][position]["agg_time_dimension"] = ""
        where = f"$.semantic_models[{index}].measures[{position}].agg_time_dimension"
    else:
        params = _metric(data, "revenue")["type_params"]["metric_aggregation_params"]
        params["agg_time_dimension"] = ""
        where = f"{_metric_path(data, 'revenue')}.type_params.metric_aggregation_params"
        where += ".agg_time_dimension"
    read = _read(data)
    assert [problem.path for problem in read.problems] == [where]
    gaps = _against(read)
    _is_row(PROBLEM_ROW, gaps)
    assert [path for *_, path in gaps] == [where]


#: A contract that a reading past the early end would find against: another table, another
#: event time, additive.
_AGAINST = {"table": "dm.sales", "event_time": "day", "additivity": "additive"}


def test_where_the_resolver_ends_the_reading_early() -> None:
    def at(data: dict[str, Any], ref: str, where: str) -> tuple[str, str, str]:
        return CONSTRUCT_UNSUPPORTED, "review", f"{_metric_path(data, ref)}{where}"

    def time(data: dict[str, Any]) -> tuple[str, str, str]:
        return TIME_MISMATCH, "block", f"{_dimension_path(data, 'date')}.name"

    # another type: its type alone, not even its filter
    data = _data("legacy")
    _metric(data, "revenue_cum")["filter"] = copy.deepcopy(FILTER)
    assert _gaps(_resolve(data, "revenue_cum", **_AGAINST)) == [at(data, "revenue_cum", ".type")]

    # ... save that a private one is private: that is asked before the type
    _metric(data, "revenue_cum")["type_params"]["is_private"] = True
    assert _gaps(_resolve(data, "revenue_cum", **_AGAINST)) == [
        (PRIVATE_METRIC, "review", f"{_metric_path(data, 'revenue_cum')}.type_params.is_private"),
        at(data, "revenue_cum", ".type"),
    ]

    # a ratio without a side: that, and the time of its other side
    data = _data("legacy")
    _metric(data, "gross_margin_pct")["type_params"]["denominator"] = None
    assert _gaps(_resolve(data, "gross_margin_pct", **_AGAINST)) == [
        time(data),
        at(data, "gross_margin_pct", ".type_params.denominator"),
    ]

    # a ratio without either side: each, at its own path
    data = _data("legacy")
    params = _metric(data, "gross_margin_pct")["type_params"]
    params["numerator"] = params["denominator"] = None
    assert _gaps(_resolve(data, "gross_margin_pct", **_AGAINST)) == [
        at(data, "gross_margin_pct", ".type_params.numerator"),
        at(data, "gross_margin_pct", ".type_params.denominator"),
    ]

    # a ratio over a metric that is not simple: that, and the time of its other side
    data = _data("legacy")
    params = _metric(data, "gross_margin_pct")["type_params"]
    params["numerator"]["name"] = "revenue_cum"
    params["input_measures"] = params["input_measures"][::-1]
    assert _gaps(_resolve(data, "gross_margin_pct", **_AGAINST)) == [
        at(data, "revenue_cum", ".type"),
        time(data),
    ]

    # a ratio over a simple metric with neither way to aggregate: that, and its other side
    data = _data("legacy")
    neither = _metric(data, "gross_margin_total")["type_params"]
    neither["measure"], neither["input_measures"] = None, []
    assert _gaps(_resolve(data, "gross_margin_pct", **_AGAINST)) == [
        at(data, "gross_margin_total", ".type_params"),
        time(data),
    ]

    # a simple metric with neither: its own keys, no time, relation or additivity
    data = _data("legacy")
    neither = _metric(data, "revenue")["type_params"]
    neither["measure"], neither["input_measures"] = None, []
    _metric(data, "revenue")["filter"] = copy.deepcopy(FILTER)
    assert _gaps(_resolve(data, "revenue", **_AGAINST)) == [
        at(data, "revenue", ".filter"),
        at(data, "revenue", ".type_params"),
    ]


#: Where a call stands in its module: first line, last line, first column, last column.
Position = tuple[int | None, int | None, int | None, int | None]


#: What names a gap (a method of the resolver) and what makes one.
_NAMERS = ("_construct", "_gap")
_MAKER = "ExternalGap"
#: What makes an object of a class without naming the class: a copy or a replacement of one.
_COPIERS = {"copy": None, "dataclasses": ("replace",)}


def _position(node: ast.expr) -> Position:
    return node.lineno, node.end_lineno, node.col_offset, node.end_col_offset


def _is_site(node: ast.AST) -> bool:
    """A call `self._construct(`, `self._gap(` or `ExternalGap(`."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == _MAKER
    return (
        isinstance(func, ast.Attribute)
        and func.attr in _NAMERS
        and isinstance(func.value, ast.Name)
        and func.value.id == "self"
    )


def _copies(node: ast.AST) -> bool:
    """Whether the node can make an object without naming its class: an import of `copy` or of
    `dataclasses.replace`, `__class__`, or a call of what a call returns (`type(gap)(...)`)."""
    if isinstance(node, ast.Import):
        return any(alias.name in _COPIERS for alias in node.names)
    if isinstance(node, ast.ImportFrom) and node.module in _COPIERS:
        names = _COPIERS[node.module]
        return names is None or any(alias.name in names for alias in node.names)
    if isinstance(node, ast.Attribute):
        return node.attr == "__class__"
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Call)


def _mentions(node: ast.AST) -> bool:
    """Whether the node names what names or makes a gap: as an attribute, a name, an import or
    a string."""
    if isinstance(node, ast.Attribute):
        return node.attr in (*_NAMERS, _MAKER)
    if isinstance(node, ast.Name):
        return node.id == _MAKER
    if isinstance(node, ast.alias):
        return _MAKER in (node.name.rsplit(".", 1)[-1], node.asname)
    return isinstance(node, ast.Constant) and node.value in (*_NAMERS, _MAKER)


def _sites(source: str) -> set[Position]:
    """Each call that names or makes a gap."""
    return {_position(node) for node in ast.walk(ast.parse(source)) if _is_site(node)}


def _other_mentions(source: str) -> list[Position]:
    """Each mention of what names or makes a gap but the call of a site and the type named in
    an annotation.

    An alias (`note = self._gap`), an import under another name (`import ExternalGap as Gap`),
    a call on another object or through the module (`resolver._gap(`, `module.ExternalGap(`),
    the name spelled as a string (`getattr(self, "_gap")`) or a copy of a gap (`_copies`) would
    name or make a gap where no site is, and a place no case reaches shows no call either. These
    are the forms the test knows, not every one Python has.
    """
    tree = ast.parse(source)
    allowed: set[int] = set()
    for node in ast.walk(tree):
        if _is_site(node):
            allowed.add(id(node.func))
        for annotation in (getattr(node, "annotation", None), getattr(node, "returns", None)):
            if isinstance(annotation, ast.AST):
                allowed.update(
                    id(part) for part in ast.walk(annotation) if isinstance(part, ast.Name)
                )
    return [
        _position(node)
        for node in ast.walk(tree)
        if _copies(node) or (_mentions(node) and id(node) not in allowed)
    ]


def _caller() -> Position:
    """The position of the call the caller of a recording function is in, as the code holds it."""
    frame = sys._getframe(2)
    assert frame.f_code.co_filename == metricflow_formula.__file__
    return list(frame.f_code.co_positions())[frame.f_lasti // 2]


def test_two_calls_on_one_line_are_two_places() -> None:
    source = "def f(self, x):\n    self._gap(self._construct(1) if x else 2)\n"
    assert len(_sites(source)) == 2


def test_a_mention_other_than_a_call_is_seen() -> None:
    source = (
        "def f(self, x: ExternalGap) -> list[ExternalGap]:\n"
        "    note = self._gap\n"
        "    make = ExternalGap\n"
        "    other._construct(x)\n"
        "    getattr(self, '_construct')(x)\n"
        "    from auto_bi.semantic.metricflow_formula import ExternalGap as Gap\n"
        "    module.ExternalGap(x)\n"
        "    y: self._gap = x\n"
        "    import copy\n"
        "    from dataclasses import dataclass, replace\n"
        "    z = x.__class__(x)\n"
        "    w = type(x)(x)\n"
        "    from dataclasses import dataclass\n"
        "    return self._gap(ExternalGap(x))\n"
    )
    # each line but the annotations, the import of `dataclass` alone and the calls of sites
    assert sorted({line for line, *_ in _other_mentions(source)}) == list(range(2, 13))


def test_every_place_that_names_or_makes_a_gap_is_reached_by_a_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = inspect.getsource(metricflow_formula)
    # no place but a site can name or make a gap, reached or not
    assert _other_mentions(source) == []
    calls: list[Position] = []
    construct, gap, make = _Resolver._construct, _Resolver._gap, ExternalGap.__init__

    def record_construct(self: _Resolver, path: str, what: str) -> None:
        calls.append(_caller())
        construct(self, path, what)

    def record_gap(self: _Resolver, code: str, severity: Any, path: str, reason: str) -> None:
        calls.append(_caller())
        gap(self, code, severity, path, reason)

    def record_external_gap(self: ExternalGap, *args: Any, **kwargs: Any) -> None:
        # each gap made, however it is made: what made it is to be a site of the module
        calls.append(_caller())
        make(self, *args, **kwargs)

    monkeypatch.setattr(_Resolver, "_construct", record_construct)
    monkeypatch.setattr(_Resolver, "_gap", record_gap)
    monkeypatch.setattr(ExternalGap, "__init__", record_external_gap)
    for case_of_row in CASES.values():
        case_of_row()
    sites = _sites(source)
    # each call that named or made a gap is one of them, and each of them was reached
    assert sorted(set(calls) - sites, key=str) == []
    assert sorted(sites - set(calls), key=str) == []


def _inline_codes(module: ModuleType) -> list[str]:
    """Strings shaped as a gap code anywhere but as the value of a module constant."""
    tree = ast.parse(inspect.getsource(module))
    constants = {
        id(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and all(isinstance(target, ast.Name) and target.id.isupper() for target in node.targets)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("gap.")
        and id(node) not in constants
    ]


def test_a_gap_code_is_spelled_only_as_a_constant() -> None:
    assert _inline_codes(metricflow) == []
    assert _inline_codes(metricflow_formula) == []
