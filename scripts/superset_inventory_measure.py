"""Measure the Superset inventory reader on the pinned replay corpus (ADR 0013, E4b).

The corpus (`tests/fixtures/superset_inventory/e4a/<run>.json`) holds, per dashboard of an E4a
record, every answer the reader can ask for by `GET` and the reference: the SQL the dashboard
showed for each tile in a browser, each statement with whether the engine log of that window
holds it (`docs/pilot/2026-11-18-e4a-rendered-sql/README.md`). The reader runs against a replay
of the answers; each tile of a written spec is then read the way the gate reads it:

* a typed tile reads the columns its IR query names (`decision/impact.py`, `_columns_read`);
* an SQL tile reads the columns `sql_guard` attributes or leaves unattributed, and it is read
  *whole* only when the gate's chart resolver names no `chart.sql_partial` or
  `chart.opaque_sql` for it.

Against the reference: a *missed* read is a column the dashboard's SQL reads that a tile read
whole does not name — a false "tile not affected"; an *extra* read is the other way. Names are
compared without their table, lower-cased, as in E4a. A tile is *measured* when the dashboard
showed SQL for it and the engine ran every statement of it.

Usage: uv run python scripts/superset_inventory_measure.py RUN.json [RUN.json ...] [--rows OUT]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import expressions as exp

from auto_bi.agent.sql_guard import extract_column_refs
from auto_bi.decision.impact import _ChartResolver, _columns_read
from auto_bi.inventory import superset as reader
from auto_bi.inventory.superset import (
    SupersetAnswerError,
    SupersetSourceError,
    inventory_dashboard,
)
from auto_bi.semantic.model import Column, ColumnRole, SemanticModel, Table

PARTIAL = {"chart.sql_partial", "chart.opaque_sql"}
NUMERIC = ("INT", "FLOAT", "DOUBLE", "NUMERIC", "DECIMAL", "REAL")


class ReplaySource:
    """The recorded answers of one run, by `GET`; a non-200 answer is a source error."""

    def __init__(self, run: Mapping[str, Any]) -> None:
        self._boards: Mapping[str, Any] = run["dashboards"]
        self._chart_board = {
            int(chart): board for board, doc in self._boards.items() for chart in doc["chart"]
        }
        #: `GET /api/v1/dataset/{id}` by dataset id, recorded for the 4.1.2 runs after the E4b-1
        #: review r1 (`docs/pilot/2026-11-19-e4b-inventory-reads/README.md`). A run without it
        #: answers 404: the reader then reads no tile of that dataset from rendered SQL.
        self._definitions: Mapping[str, Any] = run.get("dataset", {})
        self.calls: Counter[str] = Counter()

    def _ok(self, pair: Sequence[Any], path: str) -> Any:
        status, body = pair
        if status != 200:
            raise SupersetSourceError(f"GET {path} -> HTTP {status}")
        return body.get("result")

    def dashboard(self, ident: str) -> Mapping[str, Any]:
        self.calls["dashboard"] += 1
        found: Mapping[str, Any] = self._ok(
            self._boards[ident]["dashboard"], f"/api/v1/dashboard/{ident}"
        )
        return found

    def charts(self, ident: str) -> Sequence[Mapping[str, Any]]:
        self.calls["charts"] += 1
        found: Sequence[Mapping[str, Any]] = self._ok(
            self._boards[ident]["charts"], f"/api/v1/dashboard/{ident}/charts"
        )
        return found

    def datasets(self, ident: str) -> Sequence[Mapping[str, Any]]:
        self.calls["datasets"] += 1
        found: Sequence[Mapping[str, Any]] = self._ok(
            self._boards[ident]["datasets"], f"/api/v1/dashboard/{ident}/datasets"
        )
        return found

    # --- E4b: what the rendered level asks ------------------------------------------------

    def saves(self, dashboard_id: int) -> Sequence[Mapping[str, Any]]:
        """Rows of the chart list for the charts of the dashboard with this Superset id:
        `id`, `changed_on_utc`, `last_saved_at`."""
        self.calls["saves"] += 1
        return [dict(row) for row in self._boards[str(dashboard_id)]["chart_list"].values()]

    def chart(self, chart_id: int) -> Mapping[str, Any]:
        self.calls["chart"] += 1
        board = self._boards[self._chart_board[chart_id]]
        found: Mapping[str, Any] = self._ok(
            board["chart"][str(chart_id)]["detail"], f"/api/v1/chart/{chart_id}"
        )
        return found

    def rendered(self, chart_id: int) -> tuple[int, Mapping[str, Any]]:
        self.calls["rendered"] += 1
        board = self._boards[self._chart_board[chart_id]]
        status, body = board["chart"][str(chart_id)]["query"]
        return int(status), body

    def dataset_definition(self, dataset_id: int) -> tuple[int, Mapping[str, Any]]:
        self.calls["definition"] += 1
        pair = self._definitions.get(str(dataset_id))
        if pair is None:
            return 404, {"message": "Not found"}
        status, body = pair
        return int(status), body


def corpus_model(run: Mapping[str, Any]) -> SemanticModel:
    """Every physical table of the run with its plain columns (the D6 corpus model)."""
    tables: dict[str, Table] = {}
    for doc in run["dashboards"].values():
        status, body = doc["datasets"]
        if status != 200:
            continue
        for dataset in body["result"]:
            if dataset.get("sql") or not dataset.get("schema"):
                continue
            name = f"{dataset['schema']}.{dataset['table_name']}"
            columns = []
            for column in dataset.get("columns") or []:
                if column.get("expression"):
                    continue
                kind = (column.get("type") or "").upper()
                role = (
                    ColumnRole.TIME
                    if column.get("is_dttm")
                    else (
                        ColumnRole.MEASURE
                        if any(word in kind for word in NUMERIC)
                        else ColumnRole.DIMENSION
                    )
                )
                columns.append(Column(name=column["column_name"], type=kind or "STRING", role=role))
            if columns:
                tables.setdefault(name, Table(name=name, columns=columns))
    return SemanticModel(tables=list(tables.values()))


def reference_reads(sqls: Sequence[str], dialect: str) -> set[str] | None:
    """Column names the statements read, without references to their own select aliases
    (E4a `analyze.reads`); None when one does not parse."""
    out: set[str] = set()
    for sql in sqls:
        try:
            tree = sqlglot.parse_one(sql.strip().rstrip(";"), read=dialect)
        except Exception:
            return None
        if tree is None:
            return None
        aliases = {a.alias for a in tree.find_all(exp.Alias)}
        for column in tree.find_all(exp.Column):
            if column.name in aliases and column.find_ancestor(exp.Alias) is None:
                continue
            out.add(column.name.lower())
    return out


def _dialect(backend: str) -> str:
    return "clickhouse" if "clickhouse" in backend else "postgres"


def _reason_class(text: str) -> str:
    return re.sub(r"chart_\d+", "chart_*", text)


def measure_run(run: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source = ReplaySource(run)
    model = corpus_model(run)
    counts: Counter[str] = Counter()
    refusals: Counter[str] = Counter()
    forms_all: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    for ident in sorted(run["dashboards"], key=int):
        doc = run["dashboards"][ident]
        counts["dashboards"] += 1
        try:
            read = inventory_dashboard(f"dashboard://e4a/d{ident}@1", ident, source, model)
        except (SupersetAnswerError, SupersetSourceError) as error:
            counts["dashboards_error"] += 1
            refusals[f"error: {type(error).__name__}"] += 1
            continue
        for chart in read.record.charts:
            forms_all[str(chart.form)] += 1
        if read.spec is None:
            counts["dashboards_refused"] += 1
            for text in read.record.refusals:
                refusals[_reason_class(text)] += 1
            continue
        counts["dashboards_written"] += 1
        forms = {c.chart: c for c in read.record.charts}
        resolver = _ChartResolver([], {}, [])
        board = f"dashboard://e4a/d{ident}@1"
        for spec_chart in read.spec.charts:
            superset_id = forms[spec_chart.id].superset_id
            form = str(forms[spec_chart.id].form)
            backend = forms[spec_chart.id].backend
            query = spec_chart.query
            if query.raw_sql is None:
                spec_reads = {name.rsplit(".", 1)[-1].lower() for name in _columns_read(query)}
                whole = True
            else:
                resolver._match_raw(board, spec_chart.id, query, {}, set(), reaches_product=False)
                subject = f"{board}#{spec_chart.id}"
                whole = not any(
                    gap.code in PARTIAL and gap.subject == subject for gap in resolver.gaps()
                )
                reads = extract_column_refs(query.raw_sql, dialect="clickhouse")
                spec_reads = {c.name.lower() for c in reads.attributed} | {
                    name.lower() for name in reads.unattributed
                }
            ref = doc["reference"].get(str(superset_id), {})
            measured = bool(ref.get("sql")) and all(ref.get("ran") or [False])
            ref_reads = (
                reference_reads(ref.get("sql") or [], _dialect(backend)) if measured else None
            )
            missed = sorted(ref_reads - spec_reads) if ref_reads is not None and whole else None
            extra = sorted(spec_reads - ref_reads) if ref_reads is not None and whole else None
            counts["tiles"] += 1
            counts[f"tiles_{form}"] += 1
            counts["tiles_whole"] += whole
            counts["tiles_whole_measured"] += whole and ref_reads is not None
            counts["tiles_whole_missed"] += bool(missed)
            counts["tiles_whole_extra"] += bool(extra)
            rows.append(
                {
                    "dashboard": int(ident),
                    "chart": superset_id,
                    "viz": forms[spec_chart.id].viz_type,
                    "form": form,
                    "whole": whole,
                    "measured": ref_reads is not None,
                    "missed": missed,
                    "extra": extra,
                    "reasons": list(forms[spec_chart.id].reasons),
                }
            )
    summary = {
        "run": run["run"],
        "superset": run["superset"],
        "reader_version": reader.READER_VERSION,
        "counts": dict(sorted(counts.items())),
        "chart_forms": dict(sorted(forms_all.items())),
        "refusals": dict(sorted(refusals.items())),
        "calls": dict(sorted(source.calls.items())),
    }
    return summary, rows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--rows", type=Path, help="write the per-tile rows here (JSON)")
    args = parser.parse_args(argv)
    every: dict[str, Any] = {}
    for path in args.runs:
        run = json.loads(path.read_text(encoding="utf-8"))
        summary, rows = measure_run(run)
        print(json.dumps(summary, ensure_ascii=False))
        every[run["run"]] = {"summary": summary, "rows": rows}
    if args.rows is not None:
        text = json.dumps(every, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
        args.rows.write_bytes(text.encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
