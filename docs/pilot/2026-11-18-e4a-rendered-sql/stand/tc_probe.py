"""E4a time_compare probe: the p-multi line chart with `time_compare: ["1 year ago"]` answered 400
on the dashboard ("time_range" was "No filter": an offset needs an enclosed range). Its params get
an enclosed range through the REST API; the caller then saves it in Explore (measure.py
--ui-save), so the saved query_context is the one the UI builds, and measures the dashboard.
The question: does GET type=query name the offset query Superset runs next to the main one?

Usage: pwvenv/bin/python tc_probe.py 41|61   (prints the chart id; records rec/<V>-c2tc/put.json)
"""

from __future__ import annotations

import json
import sys

from measure import STAND, Api

RANGE = "2025-07-01 : 2026-07-01"  # dm.sales_wide spans 2024-07-01 .. 2026-06-30

version = sys.argv[1]
base = "http://127.0.0.1:" + ("8088" if version == "41" else "8089")
ids = json.loads((STAND / f"c2_ids_{version}.json").read_text(encoding="utf-8"))
chart = ids["charts"]["p-multi"][0]
api = Api(base, "admin")
status, token = api.get("/api/v1/security/csrf_token/")
api.http.headers.update({"X-CSRFToken": token["result"], "Referer": base})
status, detail = api.get(f"/api/v1/chart/{chart}")
params = json.loads(detail["result"]["params"])
assert params.get("time_compare"), f"chart {chart} is not the time_compare probe"
params["time_range"] = RANGE
answer = api.http.put(
    f"/api/v1/chart/{chart}", json={"params": json.dumps(params, ensure_ascii=False)}
)
out = STAND / "rec" / f"{version}-c2tc"
out.mkdir(parents=True, exist_ok=True)
(out / "put.json").write_text(
    json.dumps({"chart": chart, "time_range": RANGE, "put_status": answer.status_code}, indent=1),
    encoding="utf-8",
)
print(chart)
