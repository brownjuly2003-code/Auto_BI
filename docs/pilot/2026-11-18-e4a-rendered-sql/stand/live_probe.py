"""E4a review r2: three live probes on in-area C2 charts of Superset 4.1.2, one mechanism each.

- time:    a native time filter with the default `Last quarter`, scoped to the whole dashboard
           of chart 125 (pie), stored with `chartsInScope` listing its charts, as the UI does;
- key:     chart 145 (bubble_v2) saved by REST with `row_limit` removed from `params`;
- dataset: chart 139 (funnel) moved by REST to a virtual dataset over the same table.
Each was a synthetic mutant of area_mutants.py; here Superset answers. The REST writes carry
the session's CSRF token. Records the answers before and after into rec/<out>/live_probe.json;
the dashboards are measured afterwards with measure.py (run_live.sh).

Usage: pwvenv/bin/python live_probe.py rec/41-c2live
"""

from __future__ import annotations

import json
import sys
from typing import Any

from measure import STAND, Api

base = "http://127.0.0.1:8088"
api = Api(base, "admin")
status, token = api.get("/api/v1/security/csrf_token/")
api.http.headers.update({"X-CSRFToken": token["result"], "Referer": base})
out = STAND / sys.argv[1]
record: dict[str, Any] = {}


def chart(cid: int) -> dict[str, Any]:
    return api.get(f"/api/v1/chart/{cid}")[1]["result"]


def put(path: str, body: dict[str, Any]) -> dict[str, Any]:
    answer = api.http.put(path, json=body)
    return {"status": answer.status_code, "answer": answer.text[:300]}


# time: the dashboard of chart 125.
board = chart(125)["dashboards"][0]["id"]
dash = api.get(f"/api/v1/dashboard/{board}")[1]["result"]
meta = json.loads(dash.get("json_metadata") or "{}")
charts = [c["id"] for c in api.get(f"/api/v1/dashboard/{board}/charts")[1]["result"]]
natives = meta.setdefault("native_filter_configuration", [])
if not any(item.get("id") == "NATIVE_FILTER-e4a-time" for item in natives):
    natives.append(
        {
            "id": "NATIVE_FILTER-e4a-time",
            "name": "Время",
            "filterType": "filter_time",
            "type": "NATIVE_FILTER",
            "targets": [{}],
            "controlValues": {"enableEmptyFilter": False},
            "defaultDataMask": {
                "extraFormData": {"time_range": "Last quarter"},
                "filterState": {"value": "Last quarter"},
                "ownState": {},
            },
            "cascadeParentIds": [],
            "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
            "chartsInScope": charts,
            "tabsInScope": [],
            "description": "",
        }
    )
    record["time"] = {
        "dashboard": board,
        "charts": charts,
        "put": put(f"/api/v1/dashboard/{board}", {"json_metadata": json.dumps(meta)}),
    }

# key: chart 145 without row_limit.
before = chart(145)
params = json.loads(before["params"])
if "row_limit" in params:
    removed = params.pop("row_limit")
    answer = put("/api/v1/chart/145", {"params": json.dumps(params)})
    after = chart(145)
    record["key"] = {
        "chart": 145,
        "removed": {"row_limit": removed},
        "put": answer,
        "qc_unchanged": before.get("query_context") == after.get("query_context"),
        "params_has_key": "row_limit" in json.loads(after["params"]),
    }

# dataset: chart 139 to a virtual dataset over the same table.
before = chart(139)
listing = api.get("/api/v1/dataset/", q="(page_size:100)")[1]["result"]
wide = next(
    d["id"] for d in listing if (d.get("table_name"), d.get("schema")) == ("sales_wide", "dm")
)
source = api.get(f"/api/v1/dataset/{wide}")[1]["result"]
virtual = next((d["id"] for d in listing if d.get("table_name") == "sales_wide_e4a_live"), None)
made = None
if virtual is None:
    answer = api.http.post(
        "/api/v1/dataset/",
        json={
            "database": source["database"]["id"],
            "schema": "dm",
            "table_name": "sales_wide_e4a_live",
            "sql": "SELECT * FROM dm.sales_wide",
        },
    )
    made = {"status": answer.status_code, "answer": answer.text[:300]}
    virtual = answer.json().get("id")
moved = put("/api/v1/chart/139", {"datasource_id": virtual, "datasource_type": "table"})
after = chart(139)
record["dataset"] = {
    "chart": 139,
    "from": source["id"],
    "to": virtual,
    "made": made,
    "put": moved,
    "qc_unchanged": before.get("query_context") == after.get("query_context"),
    "qc_datasource": json.loads(after.get("query_context") or "{}").get("datasource"),
}
out.mkdir(parents=True, exist_ok=True)
(out / "live_probe.json").write_text(json.dumps(record, indent=1, ensure_ascii=False), "utf-8")
print(json.dumps(record, ensure_ascii=False)[:1500])
