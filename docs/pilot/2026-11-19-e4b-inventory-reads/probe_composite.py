"""E4b-2 base: what a read set named by form_data alone misses against the reference.

Written before the composite SQL code, to set its threshold. For each E4b-2 candidate of the
pinned corpus on Superset 4.1.2 — a measured tile of a written dashboard that reader 2 leaves
over-read, of a viz in the 4.1.2 chart/data list — names the columns `form_data` names the way
Superset 4.1.2 `buildQueryObject` and `extractQueryFields` take them: column keys, metric keys
(a saved metric through its SQL, an adhoc metric through its column or SQL), the subjects of
adhoc filters, the time column when a time range applies, a calculated column through its
expression. Prints each candidate whose reference reads a column outside that set, and the count
per viz. The dataset's own SQL is left out on purpose: the five misses are virtual datasets.

Usage: python docs/pilot/2026-11-19-e4b-inventory-reads/probe_composite.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from superset_inventory_measure import _dialect, measure_run, reference_reads  # noqa: E402

from auto_bi.inventory import superset as reader  # noqa: E402

RUNS = ("41-c1w", "41-c2w", "41-c1rest")
COLUMN_KEYS = (
    "groupby",
    "columns",
    "all_columns",
    "series",
    "x_axis",
    "source",
    "target",
    "column",
    "groupbyRows",
    "groupbyColumns",
    "order_by_cols",
    "entity",
    "groupby_b",
)
METRIC_KEYS = (
    "metric",
    "metrics",
    "secondary_metric",
    "x",
    "y",
    "size",
    "metric_2",
    "percent_metrics",
    "timeseries_limit_metric",
    "series_limit_metric",
    "metrics_b",
)


def _sql_columns(text: str, dialect: str) -> set[str]:
    try:
        tree = sqlglot.parse_one(f"SELECT {text}", read=dialect)
    except sqlglot.errors.ParseError:
        return {"<unparsed>"}
    return {column.name.lower() for column in tree.find_all(exp.Column)}


def _values(value: Any) -> Iterator[Any]:
    if isinstance(value, list):
        for item in value:
            yield from _values(item)
    elif value not in (None, ""):
        yield value


def named(form_data: Mapping[str, Any], dataset: Mapping[str, Any], dialect: str) -> set[str]:
    calculated = {c["column_name"]: c.get("expression") for c in dataset.get("columns", [])}
    saved = {m["metric_name"]: m.get("expression") for m in dataset.get("metrics", [])}
    out: set[str] = set()

    def column(ref: Any) -> None:
        if isinstance(ref, str):
            if calculated.get(ref):
                out.update(_sql_columns(calculated[ref], dialect))
            out.add(ref.lower())
        elif isinstance(ref, Mapping):
            if ref.get("sqlExpression"):
                out.update(_sql_columns(ref["sqlExpression"], dialect))
            elif ref.get("column_name"):
                column(ref["column_name"])

    def metric(ref: Any) -> None:
        if isinstance(ref, str) and saved.get(ref):
            out.update(_sql_columns(saved[ref], dialect))
        elif isinstance(ref, Mapping):
            if ref.get("expressionType") == "SQL":
                out.update(_sql_columns(ref.get("sqlExpression") or "", dialect))
            elif isinstance(ref.get("column"), Mapping):
                column(ref["column"].get("column_name"))

    for key in COLUMN_KEYS:
        for value in _values(form_data.get(key)):
            if key == "order_by_cols" and isinstance(value, str):
                value = json.loads(value)[0]
            column(value)
    for key in METRIC_KEYS:
        for value in _values(form_data.get(key)):
            metric(value)
    for adhoc in form_data.get("adhoc_filters") or []:
        if adhoc.get("expressionType") == "SQL":
            out.update(_sql_columns(adhoc.get("sqlExpression") or "", dialect))
        else:
            column(adhoc.get("subject"))
    if form_data.get("time_range") not in (None, "", "No filter"):
        column(form_data.get("granularity_sqla") or dataset.get("main_dttm_col"))
    return out


def main() -> int:
    tiles: Counter[str] = Counter()
    covered: Counter[str] = Counter()
    for name in RUNS:
        run = json.loads((REPO / f"tests/fixtures/superset_inventory/e4a/{name}.json").read_bytes())
        _, rows = measure_run(run)
        for row in rows:
            if row["form"] == "rendered_sql" or row["form"] == "typed":
                continue
            if not row["measured"] or row["viz"] not in reader._RENDERED_VIZ:
                continue
            board = run["dashboards"][str(row["dashboard"])]
            chart = next(c for c in board["charts"][1]["result"] if c["id"] == row["chart"])
            form_data = chart["form_data"]
            dataset_id = int(str(form_data["datasource"]).split("__")[0])
            dataset = run["dataset"][str(dataset_id)][1]["result"]
            dialect = _dialect(dataset["database"]["backend"])
            reads = reference_reads(board["reference"][str(row["chart"])]["sql"], dialect)
            missed = sorted((reads or set()) - named(form_data, dataset, dialect))
            tiles[row["viz"]] += 1
            covered[row["viz"]] += not missed
            if missed:
                print(f"{name} d{row['dashboard']} chart {row['chart']} {row['viz']}: {missed}")
    for viz, count in tiles.most_common():
        print(f"{viz}: {covered[viz]} of {count}")
    print(f"covered {sum(covered.values())} of {sum(tiles.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
