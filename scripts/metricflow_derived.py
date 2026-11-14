"""What MetricFlow does with a derived quotient (ADR 0012, D4b): its SQL, run on PostgreSQL.

Manual and stand-only, like ``metricflow_parity.py``, whose table, rows and bridge to MetricFlow
it reuses; CI does not run it. The resolver answers a derived metric whose ``expr`` divides one
of its input metrics by another or by itself with a construct gap (row ``derived.quotient`` of the
inventory), not with the typed ratio. This script records why. Its dbt project (legacy dialect,
over the parity table) holds, for each pair of `PAIRS`, a derived quotient of the two metrics
and the ratio metric over them; ``run`` asks MetricFlow for the SQL of each and runs it on
PostgreSQL over the parity recipe's rows, which hold groups with a zero count. The record keeps
the SQL, and per metric its rows with the type of the value, or the SQLSTATE of the error the
query ended with. ``tests/test_metricflow_derived.py`` holds the record to the resolver.

The pairs: a sum over a count with a zero in some group; the maximum of an integer column over
a count, two integer terms; a sum over a count with no zero, where the quotient and the ratio
agree; and that last quotient again with aliases, which name the columns the quotient reads.

Steps (the Mac stand)::

    python scripts/metricflow_derived.py project OUT
    (cd OUT && dbt parse)                       # in the dbt/MetricFlow environment
    psql ... -f OUT/data.postgres.sql           # a throwaway PostgreSQL database
    python scripts/metricflow_derived.py run --project OUT --mf-python PY --psql PSQL \\
        --conninfo "host=... port=... dbname=parity" --out result.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from auto_bi.evidence.canonical import digest_bytes, sha256_digest


def _load_parity() -> Any:
    """`metricflow_parity.py` beside this script, by path: the script runs from anywhere, and a
    test loads it the same way."""
    path = Path(__file__).resolve().parent / "metricflow_parity.py"
    spec = importlib.util.spec_from_file_location("metricflow_derived_parity", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # a dataclass looks its module up by name while the module runs
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parity = _load_parity()

#: Each simple metric of the project: its column and aggregate.
SIMPLE = {
    "amount_sum": ("amount", "sum"),
    "qty_count": ("qty", "count"),
    "qty_nn_max": ("qty_nn", "max"),
    "qty_nn_count": ("qty_nn", "count"),
}
#: (numerator, denominator, aliases or None): a derived quotient and a ratio of each.
PAIRS: tuple[tuple[str, str, tuple[str, str] | None], ...] = (
    ("amount_sum", "qty_count", None),
    ("qty_nn_max", "qty_nn_count", None),
    ("amount_sum", "qty_nn_count", None),
    ("amount_sum", "qty_nn_count", ("top_value", "bottom_value")),
)


def derived_name(top: str, bottom: str, aliases: tuple[str, str] | None) -> str:
    return f"d_{top}_by_{bottom}" + ("_aliased" if aliases else "")


def ratio_name(top: str, bottom: str) -> str:
    return f"r_{top}_by_{bottom}"


def _metrics() -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = [
        {"name": name, "type": "simple", "type_params": {"measure": name}} for name in SIMPLE
    ]
    for top, bottom, aliases in PAIRS:
        inputs = [{"name": top}, {"name": bottom}]
        names = (top, bottom)
        if aliases is not None:
            inputs = [{**item, "alias": alias} for item, alias in zip(inputs, aliases, strict=True)]
            names = aliases
        metrics.append(
            {
                "name": derived_name(top, bottom, aliases),
                "type": "derived",
                "type_params": {"expr": f"{names[0]} / {names[1]}", "metrics": inputs},
            }
        )
    for top, bottom in dict.fromkeys((top, bottom) for top, bottom, _ in PAIRS):
        metrics.append(
            {
                "name": ratio_name(top, bottom),
                "type": "ratio",
                "type_params": {"numerator": top, "denominator": bottom},
            }
        )
    return metrics


def project_files() -> dict[str, str]:
    """The dbt project and the data: path -> text."""
    files = parity._dbt_files("derived")
    schema = {
        "models": [parity._TIME_SPINE],
        "semantic_models": [
            {
                "name": parity.TABLE,
                "model": f"ref('{parity.TABLE}')",
                "primary_entity": "sale",
                "defaults": {"agg_time_dimension": parity.EVENT_TIME},
                "dimensions": [
                    {
                        "name": parity.EVENT_TIME,
                        "type": "time",
                        "type_params": {"time_granularity": "day"},
                    },
                    {"name": "store_id", "type": "categorical"},
                ],
                "measures": [
                    {"name": name, "agg": agg, "expr": column}
                    for name, (column, agg) in SIMPLE.items()
                ],
            }
        ],
        "metrics": _metrics(),
    }
    files["models/schema.yml"] = json.dumps(schema, indent=1) + "\n"
    files["data.postgres.sql"] = parity._postgres_data()
    return files


def write_project(out: Path) -> None:
    for name, text in project_files().items():
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


def recipe() -> str:
    """The digest of what a run asks: the project, the bridge and the keys it groups by."""
    return sha256_digest(
        {
            "files": project_files(),
            "bridge": parity.BRIDGE.read_text(encoding="utf-8"),
            "group_by": list(parity.GROUP_BY),
        }
    )


#: The SQLSTATE of the error psql reports at `VERBOSITY=verbose`.
_SQLSTATE = re.compile(r"ERROR:\s+([0-9A-Z]{5}):")


def _psql(psql: str, conninfo: str, sql: str) -> subprocess.CompletedProcess[str]:
    command = [psql, "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-v", "VERBOSITY=verbose"]
    return subprocess.run(
        [*command, "-d", conninfo, "-c", sql], capture_output=True, text=True, check=False
    )


def _rows(psql: str, conninfo: str, sql: str) -> list[dict[str, Any]]:
    done = _psql(psql, conninfo, sql)
    if done.returncode != 0:
        raise SystemExit(f"psql failed: {done.stderr}")
    found: list[dict[str, Any]] | None = json.loads(done.stdout, parse_float=Decimal)
    return found or []


def outcome(psql: str, conninfo: str, sql: str, name: str) -> dict[str, Any]:
    """MetricFlow's SQL of the metric run: its rows `[day, store, value, type]` in key order, or
    the SQLSTATE of the error the query ended with."""
    body = sql.strip().rstrip(";")
    day, store = parity.GROUP_BY
    keyed = (
        "SELECT json_agg(t ORDER BY t.k1, t.k2) FROM ("
        f'SELECT CAST(q."{day}" AS date) AS k1, q."{store}" AS k2, '
        f'q."{name}" AS v, pg_typeof(q."{name}")::text AS kind '
        # MetricFlow's SQL carries `--` comments: the line break keeps them off the wrapper
        f"FROM ({body}\n) AS q) AS t"
    )
    done = _psql(psql, conninfo, keyed)
    if done.returncode != 0:
        error = _SQLSTATE.search(done.stderr)
        if error is None:
            raise SystemExit(f"{name}: psql failed without a SQLSTATE: {done.stderr}")
        return {"error": error.group(1)}
    rows = json.loads(done.stdout, parse_float=Decimal) or []
    return {
        "rows": [
            [row["k1"], row["k2"], None if row["v"] is None else str(row["v"]), row["kind"]]
            for row in rows
        ]
    }


def _check_inputs(project: Path, psql: str, conninfo: str) -> None:
    """Refuse a project or a table other than the recipe's."""
    for name, text in project_files().items():
        if (project / name).read_text(encoding="utf-8") != text:
            raise SystemExit(f"{name} is not the recipe's: write the project with `project`")
    table = f"{parity.SCHEMA}.{parity.TABLE}"
    found = Counter(
        parity._row(row[parity.EVENT_TIME], row["store_id"], *(row[c] for c in parity.COLUMNS))
        for row in _rows(psql, conninfo, f"SELECT json_agg(t) FROM {table} AS t")
    )
    if found != Counter(parity._row(*row) for row in parity.table_rows()):
        raise SystemExit(f"{table} holds other rows than the recipe's")


def run(project: Path, mf_python: str, psql: str, conninfo: str, out: Path) -> int:
    _check_inputs(project, psql, conninfo)
    artifact = project / "target" / "semantic_manifest.json"
    names = [metric["name"] for metric in _metrics() if metric["type"] != "simple"]
    request = {"artifact": str(artifact), "metrics": names, "group_by": list(parity.GROUP_BY)}
    done = subprocess.run(
        [mf_python, str(parity.BRIDGE)],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        check=True,
    )
    sql: dict[str, str] = json.loads(done.stdout)
    metrics = {
        name: {"sql": sql[name], "outcome": outcome(psql, conninfo, sql[name], name)}
        for name in names
    }
    version = _rows(psql, conninfo, "SELECT json_agg(t) FROM (SELECT version() AS v) t")
    result = {
        "versions": {
            "postgres": version[0]["v"],
            "metricflow": parity._version(mf_python, "metricflow"),
            "dbt-core": parity._version(mf_python, "dbt-core"),
        },
        "recipe": recipe(),
        "artifact_digest": digest_bytes(artifact.read_bytes()),
        "metrics": metrics,
    }
    out.write_text(
        json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    for name, found in metrics.items():
        print(f"{name}: {found['outcome'].get('error') or len(found['outcome']['rows'])}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    project = commands.add_parser("project", help="write the dbt project and the data")
    project.add_argument("out", type=Path)
    running = commands.add_parser("run", help="record MetricFlow's SQL and what it gives")
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
