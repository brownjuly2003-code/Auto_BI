#!/bin/bash
# E4a on Superset 6.1.0, the C1 rerun after run_61.sh: the first Explore-open pass stopped at
# chart 37 (a dropped connection) and the C1 measurement after it ran logged out (the login
# race measure.login now checks for). Explore-open resumes (charts 1-37 hold the query_context
# the first pass wrote), then C1 is measured again and analyzed.
# Usage: bash run_61b.sh
set -u
S="${E4A_STAND:-$HOME/e4astand}"
cd "$S"
PY=pwvenv/bin/python
VENV=/tmp/autobi_t47/venv/bin/python
step() { echo "== $(date -u +%H:%M:%S) $*"; }
C1=$(for i in 1 2 3 4 5 6 7 8 9; do printf -- "--dashboard %d " "$i"; done)
step "C1 explore-open (resume)"; $PY measure.py --version 61 --user admin --out rec/61-c1s --explore-open $C1
step "C1 after explore-open"; $PY measure.py --version 61 --user admin --out rec/61-c1s $C1
$VENV analyze.py rec/61-c1 rec/61-c1s
$VENV area.py rec/61-c1s | head -4
# Both C2 Explore-open passes stopped on a 500 (SQLite lock) at dashboard 18 or 19: the
# schema-less p-schema chart got no query_context. The admin pass resumes, then p-schema again.
P=$($PY -c "import json;print(json.load(open('c2_ids_61.json'))['dashboards']['p-schema'])")
step "C2 explore-open admin (resume)"; $PY measure.py --version 61 --user admin --out rec/61-c2open-admin --explore-open --dashboard "$P"
step "p-schema"; $PY measure.py --version 61 --user admin --out rec/61-c2schema --dashboard "$P"
$VENV analyze.py rec/61-c2schema
step R61B_DONE
