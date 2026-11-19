"""E4a: for each chart whose GET SQL differs from the shown SQL, the endpoint the dashboard used,
the freshness of the saved query_context (params keys whose value differs in qc.form_data) and the
first differing spot of the two SQL texts. Usage: python diff_rows.py rec/41-c1s [chart ...]"""

import difflib
import json
import sys
from pathlib import Path

from analyze import answer_sqls, chart_of, no_dates, norm

run = Path(sys.argv[1])
only = {int(a) for a in sys.argv[2:]}
for path in sorted(run.glob("dashboard_*.json")):
    record = json.loads(path.read_text(encoding="utf-8"))
    entries = {}
    for entry in record.get("dashboard_requests", []):
        entries.setdefault(chart_of(entry), []).append(entry)
    for key, chart in record["charts"].items():
        cid = int(key)
        if only and cid not in only:
            continue
        status, body = chart["get_query"]
        if status != 200:
            continue
        got = [norm(r.get("query", "")) for r in body.get("result", [])]
        shown = [norm(s) for e in entries.get(cid, []) for s in answer_sqls(e)]
        if [no_dates(s) for s in got] == [no_dates(s) for s in shown]:
            continue
        detail = chart["detail"][1]["result"]
        params = json.loads(detail.get("params") or "{}")
        qc = json.loads(detail.get("query_context") or "{}")
        form = qc.get("form_data") or {}
        stale = sorted(k for k in params if k in form and form[k] != params[k])
        missing = sorted(k for k in params if k not in form)
        endpoint = {e["url"].split("?")[0].rsplit("/", 2)[-2] for e in entries.get(cid, [])}
        print(
            f"== d{record['dashboard']} c{cid} {detail.get('viz_type')} endpoint={endpoint} "
            f"stale={stale} missing={missing[:6]} n_get={len(got)} n_shown={len(shown)}"
        )
        a, b = (got or [""])[0], (shown or [""])[0]
        for op in difflib.SequenceMatcher(None, a, b).get_opcodes():
            if op[0] != "equal":
                print(
                    "   ",
                    op[0],
                    "GET:",
                    repr(a[op[1] : op[2]][:150]),
                    "SHOWN:",
                    repr(b[op[3] : op[4]][:150]),
                )
