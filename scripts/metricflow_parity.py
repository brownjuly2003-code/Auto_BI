"""Parity matrix of ADR 0008, item 7: MetricFlow against the reference SQL, on PostgreSQL and
ClickHouse.

Manual and stand-only, like ``verify_live_clickhouse.py``; CI does not run it. A row of the
matrix is one way a typed formula is resolved from a dbt semantic manifest — an aggregate in
the legacy dialect over a class of column type, a ratio form over two such terms, a way of
naming the column — in a context ``<engine>.<event time>``: the engine holding the table, and
the class of its event time column, a grain key. The matrix has two contexts, ``postgres.date``
and ``clickhouse.date`` (`CONTEXTS`). A row is proven when, for every metric built to test it,
the SQL MetricFlow writes from the artifact and the reference SQL the gate compiles for the
resolved formula give the same rows on the same data: the same grain keys, and per key the same
number by the metric's rule (`rule`, `_same`) — equal for an aggregate; for a ratio,
MetricFlow's double lies, within the error of a double, among the values that truncate at the
contract's precision (`unit.precision`) to the reference value; for an ``average`` on
ClickHouse, which returns a double over any column, the reference lies within 1e-15 of its own
value from MetricFlow's — the recipe's tolerance on its data, whose sums are exact, not a bound
on the error of ``avg`` on any data. `auto_bi.semantic.metricflow_formula.PROVEN_PARITY` lists
the rows of the last run; the run's result is a test fixture, with the digest of the recipe it
ran (`recipe`) and of the reference SQL each metric ran in each context, so a compiler that
writes other SQL now no longer holds the record.

MetricFlow 0.213.0 renders no ClickHouse SQL. Its side is the same in both contexts: its SQL
for PostgreSQL, run once per metric on PostgreSQL. The reference side of a context is the
gate's SQL for the context's engine, compiled from the context's native model and run on that
engine over the same rows: PostgreSQL through psql, ClickHouse through ``clickhouse local``,
which loads the recipe's rows for each query and keeps nothing. ``clickhouse local`` runs with
the recipe's settings (`CLICKHOUSE_SETTINGS`, ClickHouse 24.8's defaults for what the
comparison leans on); `run` refuses others and records them, and records the types ClickHouse
aggregates each member of its classes in (`PROBED_TYPES`).

The data covers what ADR 0008 names: a nullable column under ``count``, groups where a column
is all NULL, zero and NULL denominators, negative values, a large numerator, repeated values
under ``count_distinct``, a date without rows (no time spine: both queries leave it out), and a
group whose mean in a double is not the exact one, of a decimal and of an integer column.
Columns are ``amount`` (class ``decimal``), ``qty`` (``integer``) and ``price`` (``float``),
nullable, and their NOT NULL twins ``amount_nn``, ``qty_nn``, ``price_nn``, holding zero where
the column is NULL: ClickHouse's reference SQL casts an integral ratio numerator by whether the
term may be NULL. A ratio is over any two columns of one nullability, the same one included.
Its float term is a ``count`` or a ``count_distinct``: under another aggregate a float term has
no reference SQL, as an ``average`` term has none (``sql.ratio_inexact_term``) — the ratios
with an ``average`` term are in the matrix to show it, and their rows stay unproven. The
control pairs of other aggregates and forms (`CONTROLS`) must differ in number on a key where
both sides hold one, or the run fails. V2 metrics are not in the matrix: dbt-core 1.12.6 writes
no granularity for a V2 time dimension, so none of them resolves.

Steps (the Mac stand, `docs/reviews/DECISIONOPS_METRICFLOW_PARITY_CLICKHOUSE_2026-11-07.md`)::

    python scripts/metricflow_parity.py project OUT          # dbt project, data, native models
    (cd OUT && dbt parse)                                    # in the dbt/MetricFlow environment
    psql ... -f OUT/data.postgres.sql                        # a throwaway PostgreSQL database
    python scripts/metricflow_parity.py run --project OUT --mf-python PY --psql PSQL \\
        --conninfo "host=... port=... dbname=parity" --clickhouse CLICKHOUSE --out result.json

``run`` writes the reference side with this repository's code and asks
``scripts/metricflow_parity_sql.py``, run by ``--mf-python``, for MetricFlow's SQL.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from auto_bi.evidence.canonical import sha256_digest
from auto_bi.semantic.gateway import NativeModelSource
from auto_bi.semantic.metric_contract import MetricContract
from auto_bi.semantic.metricflow import read_semantic_manifest
from auto_bi.semantic.metricflow_formula import CLICKHOUSE_CLASSES, DeclaredTable, resolve_formula
from auto_bi.verification.sql_contract import compile_metric_queries

REPO = Path(__file__).resolve().parent.parent
#: The script `run` hands MetricFlow's planning to, in MetricFlow's environment.
BRIDGE = REPO / "scripts" / "metricflow_parity_sql.py"

DATABASE = "parity"
SCHEMA = "dm"
TABLE = "sales"
EVENT_TIME = "date"
KEYS = ("date", "store_id")
#: MetricFlow's names for the grain keys: the day of the aggregation time, the dimension.
GROUP_BY = ("metric_time__day", "sale__store_id")
RATIO_PRECISION = 6

#: Nullable columns and the class of their type, as a parity row names it.
NULLABLE = {"amount": "decimal", "qty": "integer", "price": "float"}
#: Each nullable column's NOT NULL twin: its value, zero where it is NULL.
TWINS = {column: f"{column}_nn" for column in NULLABLE}
COLUMNS = (*NULLABLE, *TWINS.values())
CLASSES = {**NULLABLE, **{twin: NULLABLE[column] for column, twin in TWINS.items()}}
ZERO = {"amount": "0.00", "qty": 0, "price": "0"}
AGGREGATES = ("sum", "count", "count_distinct", "min", "max", "average")
#: Aggregates whose metric the matrix contracts as `additive`; the others and ratios are not.
ADDITIVE = ("sum", "count")
#: A ratio's columns: any two of one nullability.
RATIO_SIDES = tuple(
    (top, bottom)
    for group in (tuple(NULLABLE), tuple(TWINS.values()))
    for top in group
    for bottom in group
)

#: The grain table as each context's native model declares it, and as the run loads it.
DECLARED = {
    "postgres.date": DeclaredTable(
        "postgres",
        {
            EVENT_TIME: "date",
            "store_id": "integer",
            "amount": "numeric(18,2)",
            "qty": "bigint",
            "price": "double precision",
            "amount_nn": "numeric(18,2)",
            "qty_nn": "bigint",
            "price_nn": "double precision",
        },
    ),
    "clickhouse.date": DeclaredTable(
        "clickhouse",
        {
            EVENT_TIME: "Date",
            "store_id": "Int32",
            "amount": "Nullable(Decimal(18, 2))",
            "qty": "Nullable(Int64)",
            "price": "Nullable(Float64)",
            "amount_nn": "Decimal(18, 2)",
            "qty_nn": "Int64",
            "price_nn": "Float64",
        },
    ),
}
CONTEXTS = tuple(DECLARED)
#: Aggregates a context computes in a double over an exact column: ClickHouse's `avg`.
DOUBLE = {"postgres.date": (), "clickhouse.date": ("avg",)}
#: What `clickhouse local` runs with, as `system.settings` writes it: 24.8's defaults for an
#: exact `count(DISTINCT)`, an aggregate over no value, decimal overflow and a cast's
#: nullability; 64-bit integers as JSON numbers, which is not the default.
CLICKHOUSE_SETTINGS = {
    "count_distinct_implementation": "uniqExact",
    "aggregate_functions_null_for_empty": "0",
    "decimal_check_overflow": "1",
    "cast_keep_nullable": "0",
    "output_format_json_quote_64bit_integers": "0",
}
#: Types whose aggregates' types the run records: each member of a ClickHouse class an
#: aggregate reads, and the unsigned integers, which are in none.
PROBED_TYPES = (
    *(kind for kind, found in CLICKHOUSE_CLASSES.items() if found != "date"),
    "UInt8",
    "UInt16",
    "UInt32",
    "UInt64",
)
PROBED_AGGREGATES = ("sum", "min", "max", "avg")

#: (date, store_id, amount, qty, price); the twins follow from them. 2026-01-03 has no rows.
ROWS: tuple[tuple[str, int, str | None, int | None, str | None], ...] = (
    ("2026-01-01", 1, "10.00", 1, "1.5"),
    ("2026-01-01", 1, "20.50", 2, "2.25"),
    ("2026-01-01", 1, None, None, None),
    ("2026-01-01", 1, "10.00", 1, "1.5"),
    ("2026-01-01", 2, None, None, None),
    ("2026-01-01", 2, None, None, None),
    ("2026-01-02", 1, "0.00", 0, "0"),
    ("2026-01-02", 1, "-5.25", 0, "-0.5"),
    ("2026-01-02", 2, "3.33", 3, "0.1"),
    ("2026-01-02", 3, "0.00", 0, "0"),
    ("2026-01-04", 1, "999999999999.99", 3, "1e15"),
    ("2026-01-04", 1, "0.01", 4, "2"),
    ("2026-01-04", 2, "7.77", None, None),
    ("2026-01-05", 1, None, 5, None),
    ("2026-01-05", 1, None, 6, None),
    ("2026-01-05", 2, "-1.00", -3, "-1"),
    # a mean a double does not hold: of amount 133333.336666…, of qty 33333333333333333.666…
    ("2026-01-06", 1, "100000.00", 100000000000000001, "1"),
    ("2026-01-06", 1, "100000.00", 0, "2"),
    ("2026-01-06", 1, "200000.01", 0, "2"),
)


def table_rows() -> tuple[tuple[Any, ...], ...]:
    """`ROWS` with the twins: (date, store_id, *the values of `COLUMNS`)."""
    found = []
    for day, store, *values in ROWS:
        twins = (
            ZERO[column] if value is None else value
            for column, value in zip(NULLABLE, values, strict=True)
        )
        found.append((day, store, *values, *twins))
    return tuple(found)


def _simple_name(column: str, agg: str) -> str:
    return f"{column}_{agg}"


def _ratio_name(top: str, top_agg: str, bottom: str, bottom_agg: str) -> str:
    return f"r_{top}_{top_agg}_by_{bottom}_{bottom_agg}"


def _side_aggregates(column: str) -> tuple[str, ...]:
    """The aggregates of a ratio term over the column."""
    return ("count", "count_distinct") if CLASSES[column] == "float" else AGGREGATES


def _term(column: str, agg: str) -> str:
    return f"legacy.{agg}.{CLASSES[column]}"


def subjects(context: str) -> dict[str, str]:
    """The row each metric of the matrix is built to test in the context."""
    found = {
        _simple_name(column, agg): f"{context}:{_term(column, agg)}"
        for column in COLUMNS
        for agg in AGGREGATES
    }
    found["qty_bare"] = f"{context}:legacy.measure_without_expr"
    found["amount_created"] = f"{context}:legacy.metric_expr"
    # dbt writes the measure's name as the metric's `expr` here: both ways at once
    found["price"] = f"{context}:legacy.metric_expr"
    for top, bottom in RATIO_SIDES:
        for top_agg in _side_aggregates(top):
            for bottom_agg in _side_aggregates(bottom):
                name = _ratio_name(top, top_agg, bottom, bottom_agg)
                found[name] = f"{context}:ratio.{_term(top, top_agg)}/{_term(bottom, bottom_agg)}"
    return found


#: Pairs of metrics (reference, MetricFlow) that must differ in number on the data in every
#: context: a comparison that passed them would not tell one aggregate or ratio form from another.
CONTROLS = (
    ("amount_sum", "amount_max"),
    ("amount_min", "amount_average"),
    ("qty_count", "qty_count_distinct"),
    ("price_sum", "price_average"),
    ("r_amount_sum_by_qty_sum", "r_amount_sum_by_qty_count"),
    ("r_qty_max_by_amount_min", "r_qty_min_by_amount_max"),
    # the rule of an average on ClickHouse still tells a mean over values from one over rows
    ("amount_average", "amount_nn_average"),
)


def _measures() -> list[dict[str, Any]]:
    measures: list[dict[str, Any]] = [
        {"name": _simple_name(column, agg), "agg": agg, "expr": column}
        for column in COLUMNS
        for agg in AGGREGATES
    ]
    # the column named by the measure alone, and the metric dbt makes for `create_metric`
    measures.append({"name": "qty", "agg": "sum"})
    measures.append(
        {"name": "amount_created", "agg": "sum", "expr": "amount", "create_metric": True}
    )
    measures.append({"name": "price", "agg": "sum", "create_metric": True})
    return measures


def _metrics() -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = [
        {
            "name": _simple_name(column, agg),
            "type": "simple",
            "type_params": {"measure": _simple_name(column, agg)},
        }
        for column in COLUMNS
        for agg in AGGREGATES
    ]
    metrics.append({"name": "qty_bare", "type": "simple", "type_params": {"measure": "qty"}})
    for top, bottom in RATIO_SIDES:
        for top_agg in _side_aggregates(top):
            for bottom_agg in _side_aggregates(bottom):
                metrics.append(
                    {
                        "name": _ratio_name(top, top_agg, bottom, bottom_agg),
                        "type": "ratio",
                        "type_params": {
                            "numerator": _simple_name(top, top_agg),
                            "denominator": _simple_name(bottom, bottom_agg),
                        },
                    }
                )
    return metrics


def project_files() -> dict[str, str]:
    """The dbt project, the data and the native models of the matrix: path -> text."""
    files: dict[str, str] = {}
    files["dbt_project.yml"] = (
        "name: parity\nversion: '1.0'\nconfig-version: 2\nprofile: parity\n"
        "model-paths: ['models']\n"
    )
    files["profiles.yml"] = (
        "parity:\n  target: dev\n  outputs:\n    dev:\n      type: postgres\n"
        "      host: localhost\n      user: parity\n      password: parity\n      port: 5432\n"
        f"      dbname: {DATABASE}\n      schema: {SCHEMA}\n      threads: 1\n"
    )
    types = DECLARED["postgres.date"].column_types
    columns = ", ".join(f"cast(null as {types[name]}) as {name}" for name in COLUMNS)
    files[f"models/{TABLE}.sql"] = f"select cast(null as date) as date, 1 as store_id, {columns}\n"
    files["models/metricflow_time_spine.sql"] = "select cast('2026-01-01' as date) as date_day\n"
    schema = {
        "models": [
            {
                "name": "metricflow_time_spine",
                "time_spine": {"standard_granularity_column": "date_day"},
                "columns": [{"name": "date_day", "granularity": "day"}],
            }
        ],
        "semantic_models": [
            {
                "name": TABLE,
                "model": f"ref('{TABLE}')",
                "primary_entity": "sale",
                "defaults": {"agg_time_dimension": EVENT_TIME},
                "dimensions": [
                    {
                        "name": EVENT_TIME,
                        "type": "time",
                        "type_params": {"time_granularity": "day"},
                    },
                    {"name": "store_id", "type": "categorical"},
                ],
                "measures": _measures(),
            }
        ],
        "metrics": _metrics(),
    }
    files["models/schema.yml"] = json.dumps(schema, indent=1) + "\n"
    files["data.postgres.sql"] = _postgres_data()
    files["data.clickhouse.sql"] = _clickhouse_data()
    for context, declared in DECLARED.items():
        files[f"model.{declared.engine}.yaml"] = _native_model(context)
    return files


def _native_model(context: str) -> str:
    declared = DECLARED[context]
    types = declared.column_types
    model = {
        "tables": [
            {
                "name": f"{SCHEMA}.{TABLE}",
                "grain": list(KEYS),
                "columns": [
                    {"name": EVENT_TIME, "type": types[EVENT_TIME], "role": "time"},
                    {"name": "store_id", "type": types["store_id"], "role": "dimension"},
                    *({"name": name, "type": types[name], "role": "measure"} for name in COLUMNS),
                ],
                "physical": {"engine": declared.engine},
                "classification": "internal",
            }
        ]
    }
    return yaml.safe_dump(model, sort_keys=False)


def write_project(out: Path) -> None:
    """The files of the matrix (`project_files`) under `out`."""
    for name, text in project_files().items():
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


def recipe() -> str:
    """The digest of what a run compares: the files, the grain on each side, the contracts,
    the rules with their bounds, the bridge that asks MetricFlow, the ClickHouse settings and
    probed types, and the controls. `run` refuses project files, table columns and their types,
    table rows or settings other than these, so a recorded result is the run of the recipe whose
    digest it carries; the reference SQL each metric ran is in the result by its digest."""
    return sha256_digest(
        {
            "files": project_files(),
            "bridge": BRIDGE.read_text(encoding="utf-8"),
            "keys": list(KEYS),
            "group_by": list(GROUP_BY),
            "contracts": [
                _contract("metric", "pin", ratio=ratio, additive=not ratio)
                for ratio in (False, True)
            ],
            "declared": {
                context: {"engine": declared.engine, "column_types": dict(declared.column_types)}
                for context, declared in DECLARED.items()
            },
            "double": {context: list(aggregates) for context, aggregates in DOUBLE.items()},
            "bounds": {"error": str(_ERROR), "tiny": str(_TINY), "ratio": RATIO_PRECISION},
            "clickhouse_settings": CLICKHOUSE_SETTINGS,
            "probed": {"types": list(PROBED_TYPES), "aggregates": list(PROBED_AGGREGATES)},
            "controls": [list(control) for control in CONTROLS],
        }
    )


def _literal(value: object, *, quoted: bool) -> str:
    if value is None:
        return "NULL"
    return str(value) if isinstance(value, int) or not quoted else f"'{value}'"


def _values_sql(*, quoted: bool) -> str:
    """The rows as SQL tuples: PostgreSQL reads a quoted number as the column's type,
    ClickHouse reads a bare one."""
    return ",\n".join(
        f"  ('{day}', {store}, "
        + ", ".join(_literal(value, quoted=quoted) for value in values)
        + ")"
        for day, store, *values in table_rows()
    )


def _postgres_data() -> str:
    types = DECLARED["postgres.date"].column_types
    columns = ", ".join(
        f"{name} {kind}" + ("" if name in NULLABLE else " NOT NULL") for name, kind in types.items()
    )
    return (
        f"CREATE SCHEMA {SCHEMA};\n"
        f"CREATE TABLE {SCHEMA}.{TABLE} ({columns});\n"
        f"INSERT INTO {SCHEMA}.{TABLE} VALUES\n{_values_sql(quoted=True)};\n"
        f"CREATE TABLE {SCHEMA}.metricflow_time_spine AS SELECT d::date AS date_day "
        "FROM generate_series('2026-01-01'::date, '2026-01-10'::date, '1 day') AS d;\n"
    )


def _clickhouse_data() -> str:
    types = DECLARED["clickhouse.date"].column_types
    columns = ", ".join(f"{name} {kind}" for name, kind in types.items())
    return (
        f"CREATE DATABASE {SCHEMA} ENGINE = Memory;\n"
        f"CREATE TABLE {SCHEMA}.{TABLE} ({columns}) ENGINE = Memory;\n"
        f"INSERT INTO {SCHEMA}.{TABLE} VALUES\n{_values_sql(quoted=False)};\n"
    )


def _contract(name: str, pin: str, *, ratio: bool, additive: bool) -> dict[str, Any]:
    return {
        "metadata": {
            "id": f"metric://parity/{name}",
            "version": 1,
            "owner": "parity",
            "status": "active",
        },
        "formula": {
            "kind": "external_ref",
            "external": {"source": "dbt_semantic_manifest", "ref": name, "source_revision": pin},
        },
        "grain": {"table": f"{SCHEMA}.{TABLE}", "keys": list(KEYS)},
        "additivity": "additive" if additive else "non_additive",
        "time": {"event_time_column": EVENT_TIME, "timezone": "UTC"},
        "unit": {
            "unit": "ratio" if ratio else "value",
            "precision": RATIO_PRECISION if ratio else 2,
            "null_semantics": "null_is_missing",
            "zero_semantics": "zero_is_value",
        },
        "freshness": {"max_age": "PT6H"},
        "classification": "internal",
    }


@dataclass(frozen=True)
class Stand:
    """Where the run's queries go: psql for PostgreSQL, `clickhouse local` with the recipe's
    settings and data (`data.clickhouse.sql`), loaded anew for each query."""

    psql: str
    conninfo: str
    clickhouse: str
    clickhouse_data: str

    def postgres(self, sql: str) -> list[dict[str, Any]]:
        command = [self.psql, "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-d", self.conninfo]
        done = subprocess.run(
            [*command, "-c", sql],
            capture_output=True,
            text=True,
            check=True,
        )
        rows: list[dict[str, Any]] | None = json.loads(done.stdout, parse_float=Decimal)
        return rows or []

    def on_clickhouse(self, sql: str) -> list[dict[str, Any]]:
        settings = [f"--{name}={value}" for name, value in CLICKHOUSE_SETTINGS.items()]
        query = f"{self.clickhouse_data}{sql}\nFORMAT JSONEachRow"
        done = subprocess.run(
            [self.clickhouse, "local", "--multiquery", *settings, "-q", query],
            capture_output=True,
            text=True,
            check=True,
        )
        return [json.loads(line, parse_float=Decimal) for line in done.stdout.splitlines() if line]

    def keyed(self, engine: str, sql: str, keys: Sequence[str], value: str) -> list[dict[str, Any]]:
        """`sql`'s rows as `{k1, k2, v}`, the keys as a date and an integer."""
        if engine == "clickhouse":
            return self.on_clickhouse(_keyed_clickhouse(sql, keys, value))
        return self.postgres(_keyed(sql, keys, value))


def run(project: Path, mf_python: str, psql: str, conninfo: str, clickhouse: str, out: Path) -> int:
    raw = (project / "target" / "semantic_manifest.json").read_bytes()
    read = read_semantic_manifest(raw)
    if read.manifest is None:
        print(json.dumps([problem.__dict__ for problem in read.problems], indent=1))
        return 2
    if sorted(subjects(CONTEXTS[0])) != read.manifest.metric_names():
        raise SystemExit("the artifact holds other metrics than the matrix builds")
    data = (project / "data.clickhouse.sql").read_text(encoding="utf-8")
    stand = Stand(psql, conninfo, clickhouse, data)
    _check_inputs(project, stand)

    resolved = {context: _resolve_all(read, context) for context in CONTEXTS}
    asked = sorted({name for _, contracts in resolved.values() for name in contracts})
    mf_sql = _metricflow_sql(mf_python, project / "target" / "semantic_manifest.json", asked)
    # MetricFlow's side is one query per metric, whichever contexts compare with it
    metricflow: dict[str, list[dict[str, Any]]] = {}
    contexts: dict[str, dict[str, Any]] = {}
    for context in CONTEXTS:
        cases, contracts = resolved[context]
        engine = DECLARED[context].engine
        reference = _reference_sql(project / f"model.{engine}.yaml", contracts)
        compared = _Compared(stand, engine, reference, mf_sql, metricflow)
        for name in contracts:
            case = cases[name]
            if name not in reference:
                case["outcome"] = "no_reference_sql"
                continue
            case["reference_sql"] = sha256_digest(reference[name])
            ours, theirs = compared(name, name)
            case["values"] = _values(ours, theirs)
            case["mismatches"] = _compare(ours, theirs, rule=case["rule"])
            case["outcome"] = "pass" if not case["mismatches"] else "fail"
        controls = []
        for ours_name, theirs_name in CONTROLS:
            ours, theirs = compared(ours_name, theirs_name)
            by = cases[ours_name]["rule"]
            controls.append(
                {
                    "reference": ours_name,
                    "metricflow": theirs_name,
                    "mismatches": len(_compare(ours, theirs, rule=by)),
                    "numeric": _numeric(ours, theirs, rule=by),
                }
            )
        contexts[context] = {
            "proven": sorted(_proven(cases)),
            "controls": controls,
            "metrics": cases,
        }

    postgres_version = stand.postgres("SELECT json_agg(t) FROM (SELECT version() AS v) t")
    result = {
        "versions": {
            "postgres": postgres_version[0]["v"],
            "clickhouse": stand.on_clickhouse("SELECT version() AS v")[0]["v"],
            "metricflow": _metricflow_version(mf_python),
        },
        "artifact_digest": read.artifact_digest,
        "recipe": recipe(),
        "clickhouse_settings": _clickhouse_settings(stand),
        "clickhouse_types": _clickhouse_types(stand),
        "proven": sorted({row for found in contexts.values() for row in found["proven"]}),
        "contexts": contexts,
    }
    out.write_text(
        json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    blind = []
    for context, found in contexts.items():
        cases = found["metrics"]
        failed = sorted(name for name, case in cases.items() if case["outcome"] != "pass")
        print(f"{context}: metrics {len(cases)}, not passing {len(failed)}: {', '.join(failed)}")
        print(f"{context}: proven rows: {', '.join(found['proven'])}")
        blind += [
            {"context": context, **control}
            for control in found["controls"]
            if control["numeric"] == 0
        ]
    if blind:
        print(f"controls that differ in no number: {blind}")
        return 1
    return 0


class _Compared:
    """A context's reference rows of a metric against MetricFlow's rows of another or the same
    one; MetricFlow's are asked once, whichever context compares with them."""

    def __init__(
        self,
        stand: Stand,
        engine: str,
        reference: dict[str, str],
        mf_sql: dict[str, str],
        metricflow: dict[str, list[dict[str, Any]]],
    ) -> None:
        self._stand, self._engine, self._reference = stand, engine, reference
        self._mf_sql, self._metricflow = mf_sql, metricflow

    def __call__(
        self, ours_name: str, theirs_name: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        ours = self._stand.keyed(self._engine, self._reference[ours_name], KEYS, ours_name)
        if theirs_name not in self._metricflow:
            self._metricflow[theirs_name] = self._stand.postgres(
                _keyed(self._mf_sql[theirs_name], GROUP_BY, theirs_name)
            )
        return ours, self._metricflow[theirs_name]


def _resolve_all(
    read: Any, context: str
) -> tuple[dict[str, dict[str, Any]], dict[str, MetricContract]]:
    """Each metric's case in the context, and the contracts of those whose formula resolved
    relying on the row the metric tests."""
    manifest = read.manifest
    expected = subjects(context)
    cases: dict[str, dict[str, Any]] = {}
    contracts: dict[str, MetricContract] = {}
    for name in manifest.metric_names():
        contract = metric_contract(manifest, name)
        # every row taken as proven: what is asked is the formula, not whether to trust it
        resolution = resolve_formula(
            contract,
            read,
            database=DATABASE,
            declared=DECLARED[context],
            proven=frozenset(expected.values()),
        )
        case: dict[str, Any] = {"subject": expected[name]}
        cases[name] = case
        if resolution.formula is None:
            case["outcome"] = "unresolved"
            case["gaps"] = [[gap.code, gap.path] for gap in resolution.gaps]
            continue
        case["rows"] = sorted(resolution.parity_rows)
        case["formula"] = resolution.formula.model_dump(mode="json", exclude_none=True)
        case["rule"] = rule(context, case["formula"])
        if expected[name] not in resolution.parity_rows:
            case["outcome"] = "subject_not_relied_on"
            continue
        contracts[name] = contract.model_copy(update={"formula": resolution.formula})
    return cases, contracts


def rule(context: str, formula: dict[str, Any]) -> str:
    """How the context compares the formula's values: `ratio`, `double` or `equal`."""
    if formula["kind"] == "typed_ratio":
        return "ratio"
    return "double" if formula["aggregate"]["agg"] in DOUBLE[context] else "equal"


def _check_inputs(project: Path, stand: Stand) -> None:
    """Refuse a project, a table or ClickHouse settings other than the recipe's: the result
    carries `recipe()`, and the rows it proves name the type classes of the declared columns."""
    for name, text in project_files().items():
        if (project / name).read_text(encoding="utf-8") != text:
            raise SystemExit(f"{name} is not the recipe's: write the project with `project`")
    settings = _clickhouse_settings(stand)
    if settings != CLICKHOUSE_SETTINGS:
        raise SystemExit(f"clickhouse local runs with other settings than the recipe's: {settings}")
    postgres = stand.postgres(
        "SELECT json_agg(json_build_object('c', attname, 't', format_type(atttypid, atttypmod), "
        "'n', attnotnull) ORDER BY attnum) FROM pg_attribute "
        f"WHERE attrelid = '{SCHEMA}.{TABLE}'::regclass AND attnum > 0 AND NOT attisdropped"
    )
    declared = DECLARED["postgres.date"].column_types
    if {row["c"]: (row["t"], row["n"]) for row in postgres} != {
        name: (kind, name not in NULLABLE) for name, kind in declared.items()
    }:
        raise SystemExit(
            f"{SCHEMA}.{TABLE} on PostgreSQL has other columns or types than the declared ones"
        )
    clickhouse = stand.on_clickhouse(
        "SELECT name AS c, type AS t FROM system.columns "
        f"WHERE database = '{SCHEMA}' AND table = '{TABLE}' ORDER BY position"
    )
    if {row["c"]: row["t"] for row in clickhouse} != dict(DECLARED["clickhouse.date"].column_types):
        raise SystemExit(
            f"{SCHEMA}.{TABLE} on ClickHouse has other columns or types than the declared ones"
        )
    expected = Counter(_row(*row) for row in table_rows())
    for engine, table in (
        ("PostgreSQL", stand.postgres(f"SELECT json_agg(t) FROM {SCHEMA}.{TABLE} AS t")),
        ("ClickHouse", stand.on_clickhouse(f"SELECT * FROM {SCHEMA}.{TABLE}")),
    ):
        found = Counter(
            _row(row[EVENT_TIME], row["store_id"], *(row[column] for column in COLUMNS))
            for row in table
        )
        if found != expected:
            raise SystemExit(f"{SCHEMA}.{TABLE} on {engine} holds other rows than the recipe's")


def _clickhouse_settings(stand: Stand) -> dict[str, str]:
    """The recipe's settings as the run's `clickhouse local` has them."""
    names = ", ".join(f"'{name}'" for name in CLICKHOUSE_SETTINGS)
    rows = stand.on_clickhouse(
        f"SELECT name, value FROM system.settings WHERE name IN ({names}) ORDER BY name"
    )
    return {row["name"]: row["value"] for row in rows}


def _clickhouse_types(stand: Stand) -> dict[str, dict[str, str]]:
    """The type of each probed aggregate (`PROBED_AGGREGATES`) over each probed type."""
    sql = "\nUNION ALL\n".join(
        f"SELECT '{kind}' AS probed, "
        + ", ".join(f"toTypeName({agg}(CAST(0 AS {kind}))) AS {agg}" for agg in PROBED_AGGREGATES)
        for kind in PROBED_TYPES
    )
    return {row.pop("probed"): row for row in stand.on_clickhouse(sql)}


def _row(day: str, store: int, *values: Any) -> tuple[Any, ...]:
    return (day, store, *(None if value is None else Decimal(str(value)) for value in values))


def metric_contract(manifest: Any, name: str) -> MetricContract:
    """The contract `run` resolves the metric's formula for, pinned to the metric's closure."""
    ratio = manifest.metric(name)["type"] == "ratio"
    agg = "" if ratio else _agg_of(manifest, name)
    return MetricContract.model_validate(
        _contract(name, manifest.closure_digest(name), ratio=ratio, additive=agg in ADDITIVE)
    )


def _agg_of(manifest: Any, name: str) -> str:
    measure = manifest.metric(name)["type_params"]["measure"]["name"]
    return str(manifest.measure(measure)[1]["agg"])


def _reference_sql(model: Path, contracts: dict[str, MetricContract]) -> dict[str, str]:
    with tempfile.TemporaryDirectory() as folder:
        for name, contract in contracts.items():
            (Path(folder) / f"{name}.yaml").write_text(
                yaml.safe_dump(contract.model_dump(mode="json", exclude_none=True)),
                encoding="utf-8",
            )
        snapshot = NativeModelSource(model, folder).snapshot()
    blocking = [gap for gap in snapshot.gaps if gap.severity.value == "block"]
    if blocking:
        raise SystemExit(f"the parity snapshot has block gaps: {blocking}")
    compiled = compile_metric_queries(snapshot, [f"metric://parity/{name}@1" for name in contracts])
    return {query.metric_ref.split("/")[-1].split("@")[0]: query.sql for query in compiled.queries}


def _metricflow_sql(mf_python: str, artifact: Path, metrics: list[str]) -> dict[str, str]:
    request = {"artifact": str(artifact), "metrics": metrics, "group_by": list(GROUP_BY)}
    done = subprocess.run(
        [mf_python, str(BRIDGE)],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        check=True,
    )
    answer: dict[str, str] = json.loads(done.stdout)
    return answer


def _metricflow_version(mf_python: str) -> str:
    done = subprocess.run(
        [mf_python, "-c", "import importlib.metadata as m; print(m.version('metricflow'))"],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def _keyed(sql: str, keys: Sequence[str], value: str) -> str:
    """`sql` as PostgreSQL JSON rows `{k1, k2, v}`, the keys as a date and an integer, in key
    order."""
    body = sql.strip().rstrip(";")
    return (
        "SELECT json_agg(t ORDER BY t.k1, t.k2) FROM ("
        f'SELECT CAST(q."{keys[0]}" AS date) AS k1, q."{keys[1]}" AS k2, q."{value}" AS v '
        # MetricFlow's SQL carries `--` comments: the line break keeps them off the wrapper
        f"FROM ({body}\n) AS q) AS t"
    )


def _keyed_clickhouse(sql: str, keys: Sequence[str], value: str) -> str:
    """`sql` as ClickHouse rows `{k1, k2, v}`, the keys as a Date and an integer, in key order."""
    body = sql.strip().rstrip(";")
    return (
        f'SELECT CAST(q."{keys[0]}" AS Date) AS k1, q."{keys[1]}" AS k2, q."{value}" AS v '
        f"FROM ({body}\n) AS q ORDER BY k1, k2"
    )


def _by_key(rows: list[dict[str, Any]]) -> dict[tuple[Any, Any], Any]:
    return {(row["k1"], row["k2"]): row["v"] for row in rows}


def _values(ours: list[dict[str, Any]], theirs: list[dict[str, Any]]) -> list[list[str | None]]:
    """`[date, store, reference, MetricFlow]` per key of either side, values as text."""
    mine, other = _by_key(ours), _by_key(theirs)

    def text(value: Any) -> str | None:
        return None if value is None else str(value)

    return [
        [day, str(store), text(mine.get((day, store))), text(other.get((day, store)))]
        for day, store in sorted(mine.keys() | other.keys())
    ]


def _compare(ours: list[dict[str, Any]], theirs: list[dict[str, Any]], *, rule: str) -> list[str]:
    mine, other = _by_key(ours), _by_key(theirs)
    found = [f"key {key} only in the reference" for key in sorted(mine.keys() - other.keys())]
    found += [f"key {key} only in MetricFlow" for key in sorted(other.keys() - mine.keys())]
    for key in sorted(mine.keys() & other.keys()):
        if not _same(mine[key], other[key], rule=rule):
            found.append(f"key {key}: reference {mine[key]}, MetricFlow {other[key]}")
    return found


def _numeric(ours: list[dict[str, Any]], theirs: list[dict[str, Any]], *, rule: str) -> int:
    """The keys where both sides hold a number, and the numbers differ by the rule."""
    mine, other = _by_key(ours), _by_key(theirs)
    return sum(
        1
        for key in mine.keys() & other.keys()
        if mine[key] is not None
        and other[key] is not None
        and not _same(mine[key], other[key], rule=rule)
    )


#: A double's error relative to its value, and below the smallest one: the bound of the ratio
#: rule, and the tolerance of the `double` rule on the recipe's data.
_ERROR = Decimal("1e-15")
_TINY = Decimal("1e-300")


def _same(reference: Any, metricflow: Any, *, rule: str) -> bool:
    if reference is None or metricflow is None:
        return reference is None and metricflow is None
    ours, theirs = Decimal(str(reference)), Decimal(str(metricflow))
    if rule == "equal":
        return ours == theirs
    if rule == "double":
        # the reference a double; MetricFlow's value PostgreSQL's avg: a numeric, rounded at
        # its scale, over a decimal or an integer column, a double over a float one
        return abs(ours - theirs) <= abs(ours) * _ERROR + _TINY
    # The reference is the exact quotient truncated toward zero at the precision; MetricFlow's
    # is a double. Not their truncations are compared: the exact quotient lies in
    # [ours, ours + step) (mirrored below zero), and the double within its own error of it.
    step = Decimal(1).scaleb(-RATIO_PRECISION)
    error = abs(theirs) * _ERROR + _TINY
    if ours > 0 or (ours == 0 and theirs >= 0):
        return ours - error <= theirs < ours + step + error
    return ours - step - error < theirs <= ours + error


def _proven(cases: dict[str, dict[str, Any]]) -> set[str]:
    """Rows whose every metric passed, and whose metrics' other rows are proven too."""
    subjects: dict[str, list[dict[str, Any]]] = {}
    for case in cases.values():
        if "subject" in case:
            subjects.setdefault(case["subject"], []).append(case)
    proven = {row for row, found in subjects.items() if all(c["outcome"] == "pass" for c in found)}
    # a row dropped here may be one another row's metrics rely on: drop until none is
    while True:
        kept = {row for row in proven if all(set(c["rows"]) <= proven for c in subjects[row])}
        if kept == proven:
            return proven
        proven = kept


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    project = commands.add_parser("project", help="write the dbt project, data and models")
    project.add_argument("out", type=Path)
    running = commands.add_parser("run", help="compare MetricFlow and the reference SQL")
    running.add_argument("--project", type=Path, required=True)
    running.add_argument("--mf-python", required=True)
    running.add_argument("--psql", required=True)
    running.add_argument("--conninfo", required=True)
    running.add_argument("--clickhouse", required=True)
    running.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "project":
        write_project(args.out)
        return 0
    return run(args.project, args.mf_python, args.psql, args.conninfo, args.clickhouse, args.out)


if __name__ == "__main__":
    sys.exit(main())
