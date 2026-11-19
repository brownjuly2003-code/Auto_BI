#!/bin/bash
# E4a on Superset 6.1.0, the clean pass: the image rebuilt with SQLite's busy wait at 30 s
# (superset_config.py), so the stand's "database is locked" 500s do not leave tiles unmeasured.
# Nothing is written to the charts but the time_compare probe's save; C1 and C2 are measured
# as they stand after run_61.sh .. run_tc.sh 61 (rec/61-c1w, rec/61-c2w).
# Usage: bash run_61d.sh
set -u
S="${E4A_STAND:-$HOME/e4astand}"
cd "$S"
PY=pwvenv/bin/python
VENV=/tmp/autobi_t47/venv/bin/python
step() { echo "== $(date -u +%H:%M:%S) $*"; }
step "stand 61"; bash stand_up.sh 61 2>&1 | grep -v -i password | tail -4
TC=$($PY -c "import json;print(json.load(open('c2_ids_61.json'))['charts']['p-multi'][0])")
step "time_compare save $TC"; $PY measure.py --version 61 --user admin --out rec/61-c2tc --ui-save "$TC"
C1=$(for i in 1 2 3 4 5 6 7 8 9; do printf -- "--dashboard %d " "$i"; done)
C2=$($PY -c "import json;d=json.load(open('c2_ids_61.json'))['dashboards'];print(' '.join('--dashboard %d'%i for i in d.values()))")
RLS=$($PY -c "import json;print(json.load(open('c2_ids_61.json'))['datasets']['stores'])")
step "C1 again"; $PY measure.py --version 61 --user admin --out rec/61-c1w $C1
step "C2 again"; $PY measure.py --version 61 --user admin --out rec/61-c2w $C2
$VENV analyze.py rec/61-c1w rec/61-c2w
$VENV area.py --rls "$RLS" rec/61-c1w rec/61-c2w
$VENV diff_rows.py rec/61-c2w "$TC"
step R61D_DONE
