"""E4a: each mechanism the area rule names, applied to a real in-area chart, must take it out.

Takes a measured dashboard record and one of its in-area charts, changes one thing in the copy —
the answers the inventory reads — and runs area.rows_of on it. Each mutant names the check that
must turn False; the unchanged record must keep the chart in the area. The mutants include the
counterexamples of review r1: a native time filter's default (`extraFormData.time_range`), a key
removed from `params` while the query_context keeps it, a changed dataset, a dataset missing from
the datasets answer; and the probe's own filter shape, an empty `chartsInScope` the dashboard
does not honour. They are synthetic: what Superset would answer, not a stand run.
Usage: python area_mutants.py rec/41-c2w/dashboard_<id>.json CHART
"""

from __future__ import annotations

import copy
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import area

Record = dict[str, Any]


def detail(record: Record, chart: int) -> dict[str, Any]:
    return record["charts"][str(chart)]["detail"][1]["result"]


def params_of(record: Record, chart: int) -> dict[str, Any]:
    return json.loads(detail(record, chart)["params"])


def set_params(record: Record, chart: int, params: dict[str, Any]) -> None:
    detail(record, chart)["params"] = json.dumps(params)


def add_native(record: Record, item: dict[str, Any]) -> None:
    board = record["inventory"]["dashboard"][1]["result"]
    meta = json.loads(board.get("json_metadata") or "{}")
    meta.setdefault("native_filter_configuration", []).append(item)
    board["json_metadata"] = json.dumps(meta)


def native(chart: int, **extra: Any) -> dict[str, Any]:
    base = {"id": "NATIVE_FILTER-e4a", "type": "NATIVE_FILTER", "scope": {"rootPath": ["ROOT_ID"]}}
    base.update(extra)
    if "chartsInScope" not in extra and extra.get("charts_in_scope", True):
        base["chartsInScope"] = [chart]
    base.pop("charts_in_scope", None)
    return base


def mutants(chart: int) -> list[tuple[str, str, Callable[[Record], None]]]:
    def time_default(r: Record) -> None:
        add_native(
            r,
            native(
                chart,
                filterType="filter_time",
                defaultDataMask={"extraFormData": {"time_range": "Last quarter"}},
            ),
        )

    def first_value(r: Record) -> None:
        add_native(
            r, native(chart, filterType="filter_select", controlValues={"defaultToFirstItem": True})
        )

    def tab_scope(r: Record) -> None:
        add_native(
            r,
            native(
                chart,
                filterType="filter_select",
                charts_in_scope=False,
                scope={"rootPath": ["TAB-e4a"]},
            ),
        )

    def empty_scope_list(r: Record) -> None:
        add_native(
            r,
            native(
                chart,
                filterType="filter_select",
                chartsInScope=[],
                defaultDataMask={"filterState": {"value": ["e4a"]}},
            ),
        )

    def removed_key(r: Record) -> None:
        params = params_of(r, chart)
        key = next(k for k in params if k not in area.IDENTITY and k != "viz_type")
        del params[key]
        set_params(r, chart, params)

    def changed_param(r: Record) -> None:
        params = params_of(r, chart)
        params["row_limit"] = (params.get("row_limit") or 100) + 1
        set_params(r, chart, params)

    def changed_dataset(r: Record) -> None:
        for c in r["inventory"]["charts"][1]["result"]:
            if int(c["id"]) == chart:
                c["form_data"]["datasource"] = "999999__table"

    def missing_dataset(r: Record) -> None:
        qc = json.loads(detail(r, chart)["query_context"])
        wanted = int(qc["datasource"]["id"])
        sets = r["inventory"]["datasets"][1]
        sets["result"] = [d for d in sets["result"] if int(d["id"]) != wanted]

    def jinja(r: Record) -> None:
        params = params_of(r, chart)
        params["extra_e4a"] = "{{ current_username() }}"
        set_params(r, chart, params)

    def time_compare(r: Record) -> None:
        params = params_of(r, chart)
        params["time_compare"] = ["1 year ago"]
        set_params(r, chart, params)
        d = detail(r, chart)
        qc = json.loads(d["query_context"])
        qc["form_data"]["time_compare"] = ["1 year ago"]
        d["query_context"] = json.dumps(qc)

    def legacy(r: Record) -> None:
        for e in r["dashboard_requests"]:
            if area.chart_of(e) == chart:
                e["url"] = e["url"].replace("/api/v1/chart/data", "/superset/explore_json/")

    def get_error(r: Record) -> None:
        r["charts"][str(chart)]["get_query"][1]["result"][0]["error"] = "Empty query?"

    def explore_write(r: Record) -> None:
        r["chart_list"][str(chart)]["last_saved_at"] = "2000-01-01T00:00:00+00:00"

    return [
        ("native time filter default", "plain", time_default),
        ("native filter, first value on load", "plain", first_value),
        ("native filter scoped by tab, no chartsInScope", "plain", tab_scope),
        (
            "native filter with an empty chartsInScope, as the API stores it",
            "plain",
            empty_scope_list,
        ),
        ("key removed from params", "fresh", removed_key),
        ("params changed by REST", "fresh", changed_param),
        ("chart's dataset changed", "fresh", changed_dataset),
        ("dataset missing from the datasets answer", "fresh", missing_dataset),
        ("Jinja in params", "plain", jinja),
        ("time_compare", "single", time_compare),
        ("legacy endpoint", "api", legacy),
        ("GET answers an error", "answer", get_error),
        ("Explore's own write after the save", "saved", explore_write),
    ]


def row(record: Record, chart: int) -> dict[str, Any]:
    return next(r for r in area.rows_of(record, set()) if r["chart"] == chart)


def main() -> None:
    path, chart = Path(sys.argv[1]), int(sys.argv[2])
    record = json.loads(path.read_text(encoding="utf-8"))
    base = row(record, chart)
    assert base["in_area"], f"chart {chart} is not in the area: {base['checks']}"
    failed = 0
    for name, check, mutate in mutants(chart):
        changed = copy.deepcopy(record)
        mutate(changed)
        got = row(changed, chart)
        ok = not got["in_area"] and not got["checks"][check]
        failed += not ok
        off = sorted(k for k, v in got["checks"].items() if not v)
        print(f"{'ok  ' if ok else 'MISS'} {name}: off {off}")
    print(f"{path.name} chart {chart}: {failed} missed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
