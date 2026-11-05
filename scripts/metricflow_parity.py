"""Parity matrix of ADR 0008, item 7: MetricFlow against the reference SQL, on one PostgreSQL.

Manual and stand-only, like ``verify_live_clickhouse.py``; CI does not run it. A row of the
matrix is one way a typed formula is resolved from a dbt semantic manifest — an aggregate in
the legacy dialect over a class of column type, a ratio form over two such terms, a way of
naming the column — in the context ``postgres.date``: a table PostgreSQL holds, its event time
column a ``date`` grain key. A row is proven when, for every metric built to test it, the SQL
MetricFlow writes from the artifact and the reference SQL the gate compiles for the resolved
formula give the same rows on the same data: the same grain keys, and per key the same number —
equal for an aggregate; for a ratio, MetricFlow's double lies, within the error of a double,
among the values that truncate at the contract's precision (`unit.precision`) to the
reference value. `auto_bi.semantic.metricflow_formula.PROVEN_PARITY` lists the rows of the last
run; the run's result is a test fixture, with the digest of the recipe it ran (`recipe`).

The data covers what ADR 0008 names: a nullable column under ``count``, groups where a column
is all NULL, zero and NULL denominators, negative values, a large numerator, repeated values
under ``count_distinct``, and a date without rows (no time spine: both queries leave it out).
Columns are ``numeric(18,2)`` (class ``decimal``), ``bigint`` (``integer``) and ``double
precision`` (``float``); a ratio is over any two of them, the same one included. Its float term
is a ``count`` or a ``count_distinct``: under another aggregate a float term has no reference
SQL, as an ``average`` term has none (``sql.ratio_inexact_term``) — the ratios with an
``average`` term are in the matrix to show it, and their rows stay unproven.
V2 metrics are not in the matrix: dbt-core 1.12.6 writes no granularity for a V2 time
dimension, so none of them resolves.

Steps (the Mac stand, `docs/reviews/DECISIONOPS_METRICFLOW_PARITY_CHECKS_2026-11-04.md`)::

    python scripts/metricflow_parity.py project OUT          # dbt project, data.sql, model.yaml
    (cd OUT && dbt parse)                                    # in the dbt/MetricFlow environment
    psql ... -f OUT/data.sql                                 # a throwaway PostgreSQL database
    python scripts/metricflow_parity.py run --project OUT --mf-python PY --psql PSQL \\
        --conninfo "host=... port=... dbname=parity" --out result.json

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
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from auto_bi.evidence.canonical import sha256_digest
from auto_bi.semantic.gateway import NativeModelSource
from auto_bi.semantic.metric_contract import MetricContract
from auto_bi.semantic.metricflow import read_semantic_manifest
from auto_bi.semantic.metricflow_formula import DeclaredTable, resolve_formula
from auto_bi.verification.sql_contract import compile_metric_queries

REPO = Path(__file__).resolve().parent.parent

DATABASE = "parity"
SCHEMA = "dm"
TABLE = "sales"
ENGINE = "postgres"
EVENT_TIME = "date"
KEYS = ("date", "store_id")
#: MetricFlow's names for the grain keys: the day of the aggregation time, the dimension.
GROUP_BY = ("metric_time__day", "sale__store_id")
RATIO_PRECISION = 6
#: The context of every row: the engine, the class of the event time column's type.
CONTEXT = "postgres.date"

COLUMNS = {"amount": "numeric(18,2)", "qty": "bigint", "price": "double precision"}
#: The class of each column's type, as a parity row names it.
CLASSES = {"amount": "decimal", "qty": "integer", "price": "float"}
AGGREGATES = ("sum", "count", "count_distinct", "min", "max", "average")
#: Aggregates whose metric the matrix contracts as `additive`; the others and ratios are not.
ADDITIVE = ("sum", "count")
RATIO_SIDES = tuple((top, bottom) for top in COLUMNS for bottom in COLUMNS)
#: The grain table as the native model declares it.
DECLARED = DeclaredTable(ENGINE, {EVENT_TIME: "date", "store_id": "integer", **COLUMNS})

#: (date, store_id, amount, qty, price). 2026-01-03 has no rows.
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
)


def _simple_name(column: str, agg: str) -> str:
    return f"{column}_{agg}"


def _ratio_name(top: str, top_agg: str, bottom: str, bottom_agg: str) -> str:
    return f"r_{top}_{top_agg}_by_{bottom}_{bottom_agg}"


def _side_aggregates(column: str) -> tuple[str, ...]:
    """The aggregates of a ratio term over the column."""
    return ("count", "count_distinct") if CLASSES[column] == "float" else AGGREGATES


def _term(column: str, agg: str) -> str:
    return f"legacy.{agg}.{CLASSES[column]}"


def subjects() -> dict[str, str]:
    """The row each metric of the matrix is built to test."""
    found = {
        _simple_name(column, agg): f"{CONTEXT}:{_term(column, agg)}"
        for column in COLUMNS
        for agg in AGGREGATES
    }
    found["qty_bare"] = f"{CONTEXT}:legacy.measure_without_expr"
    found["amount_created"] = f"{CONTEXT}:legacy.metric_expr"
    # dbt writes the measure's name as the metric's `expr` here: both ways at once
    found["price"] = f"{CONTEXT}:legacy.metric_expr"
    for top, bottom in RATIO_SIDES:
        for top_agg in _side_aggregates(top):
            for bottom_agg in _side_aggregates(bottom):
                name = _ratio_name(top, top_agg, bottom, bottom_agg)
                found[name] = f"{CONTEXT}:ratio.{_term(top, top_agg)}/{_term(bottom, bottom_agg)}"
    return found


#: Pairs of metrics (reference, MetricFlow) that must not agree on the data: a comparison that
#: passed them would not tell one aggregate or ratio form from another.
CONTROLS = (
    ("amount_sum", "amount_max"),
    ("amount_min", "amount_average"),
    ("qty_count", "qty_count_distinct"),
    ("price_sum", "price_average"),
    ("r_amount_sum_by_qty_sum", "r_amount_sum_by_qty_count"),
    ("r_qty_max_by_amount_min", "r_qty_min_by_amount_max"),
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
    """The dbt project, the data and the native model of the matrix: path -> text."""
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
    columns = ", ".join(f"cast(null as {kind}) as {name}" for name, kind in COLUMNS.items())
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
    files["data.sql"] = _data_sql()
    types = DECLARED.column_types
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
                "physical": {"engine": DECLARED.engine},
                "classification": "internal",
            }
        ]
    }
    files["model.yaml"] = yaml.safe_dump(model, sort_keys=False)
    return files


def write_project(out: Path) -> None:
    """The files of the matrix (`project_files`) under `out`."""
    for name, text in project_files().items():
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


def recipe() -> str:
    """The digest of what a run compares: the files, the grain on each side, the contracts
    and the controls. `run` refuses project files, table columns and their types, or table rows
    other than these, so a recorded result is the run of the recipe whose digest it carries."""
    return sha256_digest(
        {
            "files": project_files(),
            "keys": list(KEYS),
            "group_by": list(GROUP_BY),
            "contracts": [
                _contract("metric", "pin", ratio=ratio, additive=not ratio)
                for ratio in (False, True)
            ],
            "declared": {"engine": DECLARED.engine, "column_types": dict(DECLARED.column_types)},
            "controls": [list(control) for control in CONTROLS],
        }
    )


def _literal(value: object) -> str:
    return "NULL" if value is None else (str(value) if isinstance(value, int) else f"'{value}'")


def _data_sql() -> str:
    columns = ", ".join(f"{name} {kind}" for name, kind in COLUMNS.items())
    values = ",\n".join(
        f"  ('{day}', {store}, {_literal(amount)}, {_literal(qty)}, {_literal(price)})"
        for day, store, amount, qty, price in ROWS
    )
    return (
        f"CREATE SCHEMA {SCHEMA};\n"
        f"CREATE TABLE {SCHEMA}.{TABLE} (date date NOT NULL, store_id integer NOT NULL, "
        f"{columns});\n"
        f"INSERT INTO {SCHEMA}.{TABLE} VALUES\n{values};\n"
        f"CREATE TABLE {SCHEMA}.metricflow_time_spine AS SELECT d::date AS date_day "
        "FROM generate_series('2026-01-01'::date, '2026-01-10'::date, '1 day') AS d;\n"
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


def run(project: Path, mf_python: str, psql: str, conninfo: str, out: Path) -> int:
    raw = (project / "target" / "semantic_manifest.json").read_bytes()
    read = read_semantic_manifest(raw)
    if read.manifest is None:
        print(json.dumps([problem.__dict__ for problem in read.problems], indent=1))
        return 2
    manifest = read.manifest
    expected = subjects()
    if sorted(expected) != manifest.metric_names():
        raise SystemExit("the artifact holds other metrics than the matrix builds")
    _check_inputs(project, psql, conninfo)

    cases: dict[str, dict[str, Any]] = {}
    contracts: dict[str, MetricContract] = {}
    for name in manifest.metric_names():
        contract = metric_contract(manifest, name)
        # every row taken as proven: what is asked is the formula, not whether to trust it
        resolution = resolve_formula(
            contract, read, database=DATABASE, declared=DECLARED, proven=_all_rows()
        )
        case: dict[str, Any] = {"subject": expected[name]}
        cases[name] = case
        if resolution.formula is None:
            case["outcome"] = "unresolved"
            case["gaps"] = [[gap.code, gap.path] for gap in resolution.gaps]
            continue
        case["rows"] = sorted(resolution.parity_rows)
        case["formula"] = resolution.formula.model_dump(mode="json", exclude_none=True)
        if expected[name] not in resolution.parity_rows:
            case["outcome"] = "subject_not_relied_on"
            continue
        contracts[name] = contract.model_copy(update={"formula": resolution.formula})

    reference = _reference_sql(project / "model.yaml", contracts)
    mf_sql = _metricflow_sql(mf_python, project / "target" / "semantic_manifest.json", contracts)

    def compared(ours_name: str, theirs_name: str) -> tuple[list[list[str | None]], list[str]]:
        ours = _query(psql, conninfo, _keyed(reference[ours_name], KEYS, ours_name))
        theirs = _query(psql, conninfo, _keyed(mf_sql[theirs_name], GROUP_BY, theirs_name))
        ratio = cases[ours_name]["formula"]["kind"] == "typed_ratio"
        return _values(ours, theirs), _compare(ours, theirs, ratio=ratio)

    for name in contracts:
        case = cases[name]
        if name not in reference:
            case["outcome"] = "no_reference_sql"
            continue
        case["values"], case["mismatches"] = compared(name, name)
        case["outcome"] = "pass" if not case["mismatches"] else "fail"

    controls = []
    for ours_name, theirs_name in CONTROLS:
        _, mismatches = compared(ours_name, theirs_name)
        controls.append(
            {"reference": ours_name, "metricflow": theirs_name, "mismatches": len(mismatches)}
        )

    proven = _proven(cases)
    result = {
        "versions": {
            "server": _query(psql, conninfo, "SELECT json_agg(t) FROM (SELECT version() AS v) t")[
                0
            ]["v"],
            "metricflow": _metricflow_version(mf_python),
        },
        "artifact_digest": read.artifact_digest,
        "recipe": recipe(),
        "proven": sorted(proven),
        "controls": controls,
        "metrics": cases,
    }
    out.write_text(
        json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    failed = sorted(name for name, case in cases.items() if case["outcome"] != "pass")
    print(f"metrics {len(cases)}, not passing {len(failed)}: {', '.join(failed)}")
    print(f"proven rows: {', '.join(sorted(proven))}")
    blind = [control for control in controls if control["mismatches"] == 0]
    if blind:
        print(f"controls that agreed: {blind}")
        return 1
    return 0


def _check_inputs(project: Path, psql: str, conninfo: str) -> None:
    """Refuse a project or a table other than the recipe's: the result carries `recipe()`, and
    the rows it proves name the type classes of the declared columns."""
    for name, text in project_files().items():
        if (project / name).read_text(encoding="utf-8") != text:
            raise SystemExit(f"{name} is not the recipe's: write the project with `project`")
    columns = _query(
        psql,
        conninfo,
        "SELECT json_agg(json_build_object('c', attname, 't', format_type(atttypid, atttypmod)) "
        f"ORDER BY attnum) FROM pg_attribute WHERE attrelid = '{SCHEMA}.{TABLE}'::regclass "
        "AND attnum > 0 AND NOT attisdropped",
    )
    if {column["c"]: column["t"] for column in columns} != dict(DECLARED.column_types):
        raise SystemExit(f"{SCHEMA}.{TABLE} has other columns or types than the declared ones")
    table = _query(psql, conninfo, f"SELECT json_agg(t) FROM {SCHEMA}.{TABLE} AS t")
    found = Counter(
        _row(row["date"], row["store_id"], row["amount"], row["qty"], row["price"]) for row in table
    )
    if found != Counter(_row(*row) for row in ROWS):
        raise SystemExit(f"{SCHEMA}.{TABLE} holds other rows than the recipe's")


def _row(day: str, store: int, *values: Any) -> tuple[Any, ...]:
    return (day, store, *(None if value is None else Decimal(str(value)) for value in values))


def metric_contract(manifest: Any, name: str) -> MetricContract:
    """The contract `run` resolves the metric's formula for, pinned to the metric's closure."""
    ratio = manifest.metric(name)["type"] == "ratio"
    agg = "" if ratio else _agg_of(manifest, name)
    return MetricContract.model_validate(
        _contract(name, manifest.closure_digest(name), ratio=ratio, additive=agg in ADDITIVE)
    )


def _all_rows() -> frozenset[str]:
    return frozenset(subjects().values())


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


def _metricflow_sql(
    mf_python: str, artifact: Path, contracts: dict[str, MetricContract]
) -> dict[str, str]:
    request = {"artifact": str(artifact), "metrics": sorted(contracts), "group_by": list(GROUP_BY)}
    done = subprocess.run(
        [mf_python, str(REPO / "scripts" / "metricflow_parity_sql.py")],
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
    """`sql` as JSON rows `{k1, k2, v}`, the keys as a date and an integer, in key order."""
    body = sql.strip().rstrip(";")
    return (
        "SELECT json_agg(t ORDER BY t.k1, t.k2) FROM ("
        f'SELECT CAST(q."{keys[0]}" AS date) AS k1, q."{keys[1]}" AS k2, q."{value}" AS v '
        # MetricFlow's SQL carries `--` comments: the line break keeps them off the wrapper
        f"FROM ({body}\n) AS q) AS t"
    )


def _query(psql: str, conninfo: str, sql: str) -> list[dict[str, Any]]:
    done = subprocess.run(
        [psql, "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-d", conninfo, "-c", sql],
        capture_output=True,
        text=True,
        check=True,
    )
    rows: list[dict[str, Any]] | None = json.loads(done.stdout, parse_float=Decimal)
    return rows or []


def _values(ours: list[dict[str, Any]], theirs: list[dict[str, Any]]) -> list[list[str | None]]:
    """`[date, store, reference, MetricFlow]` per key of either side, values as text."""
    mine = {(row["k1"], row["k2"]): row["v"] for row in ours}
    other = {(row["k1"], row["k2"]): row["v"] for row in theirs}

    def text(value: Any) -> str | None:
        return None if value is None else str(value)

    return [
        [day, str(store), text(mine.get((day, store))), text(other.get((day, store)))]
        for day, store in sorted(mine.keys() | other.keys())
    ]


def _compare(ours: list[dict[str, Any]], theirs: list[dict[str, Any]], *, ratio: bool) -> list[str]:
    mine = {(row["k1"], row["k2"]): row["v"] for row in ours}
    other = {(row["k1"], row["k2"]): row["v"] for row in theirs}
    found = [f"key {key} only in the reference" for key in sorted(mine.keys() - other.keys())]
    found += [f"key {key} only in MetricFlow" for key in sorted(other.keys() - mine.keys())]
    for key in sorted(mine.keys() & other.keys()):
        if not _same(mine[key], other[key], ratio=ratio):
            found.append(f"key {key}: reference {mine[key]}, MetricFlow {other[key]}")
    return found


def _same(reference: Any, metricflow: Any, *, ratio: bool) -> bool:
    if reference is None or metricflow is None:
        return reference is None and metricflow is None
    ours, theirs = Decimal(str(reference)), Decimal(str(metricflow))
    if not ratio:
        return ours == theirs
    # The reference is the exact quotient truncated toward zero at the precision; MetricFlow's
    # is a double. Not their truncations are compared: the exact quotient lies in
    # [ours, ours + step) (mirrored below zero), and the double within its own error of it.
    step = Decimal(1).scaleb(-RATIO_PRECISION)
    error = abs(theirs) * Decimal("1e-15") + Decimal("1e-300")
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
    project = commands.add_parser("project", help="write the dbt project, data and model")
    project.add_argument("out", type=Path)
    running = commands.add_parser("run", help="compare MetricFlow and the reference SQL")
    running.add_argument("--project", type=Path, required=True)
    running.add_argument("--mf-python", required=True)
    running.add_argument("--psql", required=True)
    running.add_argument("--conninfo", required=True)
    running.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "project":
        write_project(args.out)
        return 0
    return run(args.project, args.mf_python, args.psql, args.conninfo, args.out)


if __name__ == "__main__":
    sys.exit(main())
