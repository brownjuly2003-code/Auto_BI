"""E4a: the candidate area, decided from what the inventory can read without a browser, against
the measurement (the GET SQL equal, modulo date literals, to the SQL the dashboard ran).

In the area when all hold:
  saved  — the chart list says the last write set `last_saved_at` (|changed_on - last_saved_at|
           <= 2 s): a save — from the UI or a REST PUT of anything but the query_context alone —
           not Explore's own query_context write;
  fresh  — the saved query_context agrees with the chart: its form_data equals `params` on every
           key of either, but the identity keys (slice_id, datasource, dashboards, url_params)
           and the request keys (force, result_format, result_type); its datasource is the
           chart's datasource in the dashboard's charts answer and a dataset of the datasets
           answer (review r1: a key removed from `params` or a changed dataset is caught);
  api    — the viz is served by /api/v1/chart/data. The measurement takes it from the requests
           the browser sent; the inventory would hold that list per Superset version;
  plain  — no Jinja in the dataset SQL or the chart's params, no native filter in the chart's
           scope, with or without a default (review r1: a time filter's default is
           `extraFormData.time_range`, not `filters`; a filter can take its first value on
           load). A filter counts as in scope unless `scope.excluded` names the chart:
           `chartsInScope` is not trusted (the probe's filter, written through the API, keeps
           `[]` there and the dashboard applies it to both its charts), and a tab root is not
           resolved (a filter on another tab takes the chart out too). `--rls ID` excludes a
           dataset with an RLS rule too; the record's results are without it: RLS is a
           per-viewer gap of the spec, not a check of the area;
  answer — GET type=query answered 200 with a query and no error in every result (6.1 answers
           200 with `"error": "Empty query?"` for a broken legacy chart);
  single — no query of the query_context has `time_offsets` or `annotation_layers`: the chart
           data action runs the time_compare offset query next to the main one and shows both,
           GET type=query names the main one only (measured on 4.1.2, chart 159); an annotation
           layer is another query the action runs and GET does not name (not measured: excluded
           by construction).
`equal` is None when the dashboard showed no SQL for the tile (its request failed): unmeasured.
`missed_reads` are the columns the dashboard's SQL reads and the GET SQL does not name (a false
"tile not affected"), `extra_reads` the other way (analyze.reads); None when one side is missing
or does not parse.
Prints the confusion of in-area against equal per run and the charts that break the rule.
Usage: python area.py [--rls DATASET_ID ...] rec/<run> [rec/<run> ...]
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from analyze import answer_sqls, chart_of, no_dates, norm, reads

IDENTITY = {"slice_id", "datasource", "dashboards", "url_params"}
REQUEST = {"force", "result_format", "result_type"}


def when(text: str | None) -> dt.datetime | None:
    if not text:
        return None
    value = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


def native_filters(dashboard: dict[str, Any]) -> list[dict[str, Any]]:
    meta = json.loads(dashboard.get("json_metadata") or "{}")
    return [
        item
        for item in meta.get("native_filter_configuration") or []
        if item.get("type") != "DIVIDER"
    ]


def in_scope(item: dict[str, Any], chart: int) -> bool:
    scope = item.get("scope") or {}
    return chart not in (scope.get("excluded") or [])


def rows_of(record: dict[str, Any], rls: set[int]) -> list[dict[str, Any]]:
    entries: dict[int | None, list[dict[str, Any]]] = {}
    for entry in record.get("dashboard_requests", []):
        entries.setdefault(chart_of(entry), []).append(entry)
    status, board = record["inventory"]["dashboard"]
    natives = native_filters(board.get("result", {})) if status == 200 else []
    status, board_charts = record["inventory"]["charts"]
    sources = (
        {
            int(c["id"]): (c.get("form_data") or {}).get("datasource")
            for c in board_charts.get("result", [])
        }
        if status == 200
        else {}
    )
    status, sets = record["inventory"]["datasets"]
    datasets = {int(d["id"]): d for d in sets.get("result", [])} if status == 200 else {}
    listed = record.get("chart_list", {})
    out = []
    for key, chart in record["charts"].items():
        cid = int(key)
        status, detail = chart["detail"]
        result = detail.get("result", {}) if status == 200 else {}
        params = json.loads(result.get("params") or "{}")
        qc = json.loads(result.get("query_context") or "null") or {}
        form = qc.get("form_data") or {}
        row = listed.get(key, {})
        changed, saved_at = when(row.get("changed_on_utc")), when(row.get("last_saved_at"))
        source = qc.get("datasource") or {}
        named = f"{source.get('id')}__{source.get('type')}"
        dataset_id = source.get("id")
        dataset = datasets.get(int(dataset_id)) if dataset_id is not None else None
        sql = (dataset or {}).get("sql") or ""
        shown = [entry for entry in entries.get(cid, [])]
        endpoint = {e["url"].split("?")[0].rsplit("/", 2)[-2] for e in shown}
        get_status, body = chart["get_query"]
        results = body.get("result", []) if get_status == 200 else []
        got = [no_dates(norm(r.get("query") or "")) for r in results]
        ran = [no_dates(norm(s)) for e in shown for s in answer_sqls(e)]
        read_get = reads([r.get("query") or "" for r in results])
        read_dash = reads([s for e in shown for s in answer_sqls(e)])
        compared = read_get is not None and read_dash is not None
        ops = sorted(
            {
                o.get("operation")
                for q in qc.get("queries", [])
                for o in q.get("post_processing") or []
                if o
            }
        )
        checks = {
            "saved": bool(changed and saved_at and abs((changed - saved_at).total_seconds()) <= 2),
            "fresh": bool(form)
            and all(
                form.get(k) == params.get(k) for k in (set(form) | set(params)) - IDENTITY - REQUEST
            )
            and dataset is not None
            and sources.get(cid) == named
            and form.get("datasource", named) == named,
            "api": endpoint == {"chart"},
            "plain": not any(mark in sql or mark in json.dumps(params) for mark in ("{{", "{%"))
            and not any(in_scope(item, cid) for item in natives)
            and not (dataset_id is not None and int(dataset_id) in rls),
            "answer": bool(results) and all(r.get("query") and not r.get("error") for r in results),
            "single": not any(
                q.get("time_offsets") or q.get("annotation_layers") for q in qc.get("queries", [])
            )
            and not params.get("time_compare")
            and not params.get("annotation_layers"),
        }
        out.append(
            {
                "chart": cid,
                "viz": result.get("viz_type"),
                "endpoint": sorted(endpoint),
                "checks": checks,
                "in_area": all(checks.values()),
                "equal": (bool(got) and got == ran) if ran else None,
                "missed_reads": sorted(read_dash - read_get) if compared else None,
                "extra_reads": sorted(read_get - read_dash) if compared else None,
                "post_processing": ops,
            }
        )
    return out


def main() -> None:
    args = sys.argv[1:]
    rls: set[int] = set()
    while args and args[0] == "--rls":
        rls.add(int(args[1]))
        args = args[2:]
    for arg in args:
        rows = []
        for path in sorted(Path(arg).glob("dashboard_*.json")):
            rows.extend(rows_of(json.loads(path.read_text(encoding="utf-8")), rls))
        counts = Counter((r["in_area"], r["equal"]) for r in rows)
        print(
            Path(arg).name,
            "charts",
            len(rows),
            "in&equal",
            counts[(True, True)],
            "in&differs",
            counts[(True, False)],
            "in&unmeasured",
            counts[(True, None)],
            "out&equal",
            counts[(False, True)],
            "out&differs",
            counts[(False, False)],
            "out&unmeasured",
            counts[(False, None)],
            "in&no_post_processing",
            sum(r["in_area"] and not r["post_processing"] for r in rows),
        )
        failed = Counter(k for r in rows for k, v in r["checks"].items() if not v)
        print("   failing checks:", dict(failed))
        for r in rows:
            if r["in_area"] and r["equal"] is not True:
                print("   IN BUT DIFFERS", r)
        (Path(arg) / "area.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
