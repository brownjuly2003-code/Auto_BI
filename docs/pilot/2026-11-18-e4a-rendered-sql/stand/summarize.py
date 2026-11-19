"""E4a: the small, committed form of one measurement run.

A run directory (rec/<run>) holds raw records: full answers, engine logs, request bodies. This
writes results/<run>.json with what the write-up cites: the counts and per-chart rows of
analyze.py, the area rows of area.py (when the records carry the chart list), the SQL pairs of
the charts whose GET text differs from the dashboard's, and the probe summaries (explore_open,
ui_save, stale, put, rest_save, live_probe, forms) with request bodies dropped. Needs sqlglot
(the analyze venv).
Usage: python summarize.py OUT_DIR [--rls DATASET_ID] rec/<run> [rec/<run> ...]
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import analyze
import area

KEEP_ROW = (
    "dashboard",
    "chart",
    "viz",
    "has_qc",
    "qc_fresh",
    "get_status",
    "get_error",
    "get_n",
    "get_ran_engine",
    "context",
    "requests",
    "request_errors",
    "shown_n",
    "shown_ran",
    "get_ran",
    "shown_ran_sqlglot",
    "get_ran_sqlglot",
    "equal",
    "equal_but_dates",
)
DROP = {"sent", "answer", "body", "text", "requests", "engine"}


def trim(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: trim(v) for k, v in value.items() if k not in DROP}
    if isinstance(value, list):
        return [trim(v) for v in value]
    if isinstance(value, str) and len(value) > 300:
        return value[:300] + "…"
    return value


def pairs(record: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """GET and dashboard SQL, normalized, for the charts whose texts are not equal: those that
    differ beyond date literals and those equal only modulo dates, with the chart's time_range."""
    differs = {r["chart"]: r for r in rows if r["get_n"] and r["shown_n"] and not r["equal"]}
    entries: dict[int | None, list[dict[str, Any]]] = {}
    for entry in record.get("dashboard_requests", []):
        entries.setdefault(analyze.chart_of(entry), []).append(entry)
    out = []
    for key, chart in record["charts"].items():
        cid = int(key)
        if cid not in differs:
            continue
        _, body = chart["get_query"]
        got = [analyze.norm(r.get("query") or "") for r in body.get("result", [])]
        shown = [analyze.norm(s) for e in entries.get(cid, []) for s in analyze.answer_sqls(e)]
        status, detail = chart["detail"]
        params = (
            json.loads((detail.get("result") or {}).get("params") or "{}") if status == 200 else {}
        )
        out.append(
            {
                "chart": cid,
                "equal_but_dates": differs[cid]["equal_but_dates"],
                "time_range": params.get("time_range"),
                "get": got,
                "dashboard": shown,
            }
        )
    return out


def summarize(run: Path, rls: set[int]) -> dict[str, Any]:
    rows, area_rows, sql_pairs = [], [], []
    has_list = False
    for path in sorted(run.glob("dashboard_*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        these = analyze.rows_of(record, run.name)
        rows.extend({k: row[k] for k in KEEP_ROW} for row in these)
        sql_pairs.extend(pairs(record, these))
        if "chart_list" in record:
            has_list = True
            area_rows.extend(area.rows_of(record, rls))
    counts: Counter[str] = Counter()
    for row in rows:
        counts["charts"] += 1
        counts["has_qc"] += row["has_qc"]
        counts[f"get_{row['get_status']}"] += 1
        counts["shown_all_ran"] += bool(row["shown_n"]) and all(row["shown_ran"])
        counts["shown_all_ran_sqlglot"] += bool(row["shown_n"]) and all(row["shown_ran_sqlglot"])
        counts["get_all_ran"] += bool(row["get_n"]) and all(row["get_ran"])
        counts["get_all_ran_sqlglot"] += bool(row["get_n"]) and all(row["get_ran_sqlglot"])
        counts["get_ran_engine>0"] += row["get_ran_engine"] > 0
        counts["equal"] += row["equal"]
        counts["equal_but_dates"] += row["equal_but_dates"]
    out: dict[str, Any] = {"run": run.name, "counts": dict(sorted(counts.items())), "rows": rows}
    if has_list:
        confusion = Counter(f"{'in' if r['in_area'] else 'out'}&{r['equal']}" for r in area_rows)
        confusion.update(
            (
                f"{'in' if r['in_area'] else 'out'}&missed_reads={bool(r['missed_reads'])}"
                if r["missed_reads"] is not None
                else f"{'in' if r['in_area'] else 'out'}&reads_unmeasured"
            )
            for r in area_rows
        )
        out["area"] = {"confusion": dict(sorted(confusion.items())), "rows": area_rows}
    out["sql_pairs"] = sql_pairs
    for name in ("explore_open", "ui_save", "stale", "put", "rest_save", "live_probe", "forms"):
        path = run / f"{name}.json"
        if path.is_file():
            out[name] = trim(json.loads(path.read_text(encoding="utf-8")))
    return out


def main() -> None:
    args = sys.argv[1:]
    target = Path(args.pop(0))
    rls: set[int] = set()
    while args and args[0] == "--rls":
        rls.add(int(args[1]))
        args = args[2:]
    target.mkdir(parents=True, exist_ok=True)
    for arg in args:
        run = Path(arg)
        result = summarize(run, rls)
        text = json.dumps(result, ensure_ascii=False, indent=1) + "\n"
        (target / f"{run.name}.json").write_text(text, encoding="utf-8", newline="\n")
        print(run.name, len(text), result["counts"], (result.get("area") or {}).get("confusion"))


if __name__ == "__main__":
    main()
