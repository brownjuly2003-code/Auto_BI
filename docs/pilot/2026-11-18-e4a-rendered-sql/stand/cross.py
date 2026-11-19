"""E4a: GET of one run against what the dashboard ran in another run (other user / other URL),
per chart, modulo date literals. Usage: python cross.py rec/41-c2 rec/41-c2r [chart ...]"""

import difflib
import json
import sys
from pathlib import Path

from analyze import answer_sqls, chart_of, no_dates, norm


def load(run: Path) -> dict[int, tuple[list[str], list[str], list]]:
    out = {}
    for path in sorted(run.glob("dashboard_*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        entries: dict = {}
        for entry in record.get("dashboard_requests", []):
            entries.setdefault(chart_of(entry), []).append(entry)
        for key, chart in record["charts"].items():
            status, body = chart["get_query"]
            got = (
                [norm(r.get("query", "")) for r in body.get("result", [])] if status == 200 else []
            )
            shown = [norm(s) for e in entries.get(int(key), []) for s in answer_sqls(e)]
            errors = [
                (e.get("status"), str(e.get("answer"))[:200])
                for e in entries.get(int(key), [])
                if e.get("status") not in (200, 202)
            ]
            out[int(key)] = (got, shown, errors)
    return out


a, b = load(Path(sys.argv[1])), load(Path(sys.argv[2]))
only = {int(x) for x in sys.argv[3:]}
same = 0
for cid in sorted(set(a) & set(b)):
    if only and cid not in only:
        continue
    got, shown = a[cid][0], b[cid][1]
    if got and [no_dates(s) for s in got] == [no_dates(s) for s in shown]:
        same += 1
        continue
    print(f"== c{cid} n_get={len(got)} n_shown={len(shown)} errors={b[cid][2]}")
    x, y = (got or [""])[0], (shown or [""])[0]
    for op in difflib.SequenceMatcher(None, x, y).get_opcodes():
        if op[0] != "equal":
            print(
                "   ",
                op[0],
                "GET:",
                repr(x[op[1] : op[2]][:160]),
                "SHOWN:",
                repr(y[op[3] : op[4]][:160]),
            )
print("same", same)
