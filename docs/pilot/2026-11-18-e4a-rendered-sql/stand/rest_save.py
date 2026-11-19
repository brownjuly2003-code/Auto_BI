"""E4a review probe: a REST save that changes nothing, after Explore wrote the query_context.

For each chart: PUT /api/v1/chart/{id} with the chart's own `params` string, unchanged, through
the REST API — what an API client does when it touches a chart. The chart's query_context, written
by Explore on open, is left as it was; the PUT sets `last_saved_at`. Records before/after into
rec/<out>/rest_save.json; the dashboards are measured afterwards with measure.py.

Usage: pwvenv/bin/python rest_save.py 41 rec/41-c1rest 83 86 87
"""

from __future__ import annotations

import json
import sys

from measure import STAND, Api

version, out, charts = sys.argv[1], sys.argv[2], [int(a) for a in sys.argv[3:]]
base = "http://127.0.0.1:" + ("8088" if version == "41" else "8089")
api = Api(base, "admin")
# A write through the REST API needs the CSRF token of the session next to the JWT.
status, token = api.get("/api/v1/security/csrf_token/")
api.http.headers.update({"X-CSRFToken": token["result"], "Referer": base})
records = {}
for chart in charts:
    status, before = api.get(f"/api/v1/chart/{chart}")
    result = before["result"]
    answer = api.http.put(f"/api/v1/chart/{chart}", json={"params": result["params"]})
    status_after, after = api.get(f"/api/v1/chart/{chart}")
    records[str(chart)] = {
        "put_status": answer.status_code,
        "put_answer": answer.text[:300] if answer.status_code != 200 else "",
        "qc_before_present": result.get("query_context") is not None,
        "qc_unchanged": result.get("query_context") == after["result"].get("query_context"),
        "params_unchanged": result["params"] == after["result"]["params"],
    }
path = STAND / out
path.mkdir(parents=True, exist_ok=True)
(path / "rest_save.json").write_text(json.dumps(records, indent=1), encoding="utf-8")
print(json.dumps(records))
