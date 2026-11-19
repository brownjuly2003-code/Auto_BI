"""E4a stale probe: after the UI save, the p-stale chart's params change through the REST API
(groupby region -> category, metric label kept) and its saved query_context is left as it was —
what an API client or an import does. Records before/after into rec/<out>/stale.json.

Usage: pwvenv/bin/python stale.py 41 rec/41-c2
"""

from __future__ import annotations

import json
import sys

from measure import STAND, Api

version, out = sys.argv[1], sys.argv[2]
base = "http://127.0.0.1:" + ("8088" if version == "41" else "8089")
ids = json.loads((STAND / f"c2_ids_{version}.json").read_text(encoding="utf-8"))
chart = ids["charts"]["p-stale"][0]
api = Api(base, "admin")
# A write through the REST API needs the CSRF token of the session next to the JWT.
status, token = api.get("/api/v1/security/csrf_token/")
api.http.headers.update({"X-CSRFToken": token["result"], "Referer": base})
status, before = api.get(f"/api/v1/chart/{chart}")
result = before["result"]
params = json.loads(result["params"])
if params.get("groupby") == ["category"]:
    sys.exit(f"chart {chart} already changed")
params["groupby"] = ["category"]
answer = api.http.put(
    f"/api/v1/chart/{chart}", json={"params": json.dumps(params, ensure_ascii=False)}
)
status_after, after = api.get(f"/api/v1/chart/{chart}")
qc_before = result.get("query_context")
qc_after = after["result"].get("query_context")
record = {
    "chart": chart,
    "put_status": answer.status_code,
    "put_answer": answer.text[:300] if answer.status_code != 200 else "",
    "qc_before_present": qc_before is not None,
    "qc_unchanged": qc_before == qc_after,
    "params_after_groupby": json.loads(after["result"]["params"]).get("groupby"),
}
path = STAND / out
path.mkdir(parents=True, exist_ok=True)
(path / "stale.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
print(record)
