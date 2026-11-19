#!/bin/bash
# E4a back on Superset 4.1.2 after 6.1: the C1 and C2 corpora measured once more with the chart
# list (last_saved_at, changed_on_utc) in the records, for the area rule; then the time_compare
# probe. Nothing is written to the charts before the measurements (no Explore-open, no save).
# Usage: bash run_41w.sh
set -u
S="${E4A_STAND:-$HOME/e4astand}"
cd "$S"
PY=pwvenv/bin/python
VENV=/tmp/autobi_t47/venv/bin/python
step() { echo "== $(date -u +%H:%M:%S) $*"; }
step "stand 41"; bash stand_up.sh 41 2>&1 | grep -v -i password | tail -4
C1=$(for i in $(seq 1 11); do printf -- "--dashboard %d " "$i"; done)
C2=$($PY -c "import json;d=json.load(open('c2_ids_41.json'))['dashboards'];print(' '.join('--dashboard %d'%i for i in d.values()))")
RLS=$($PY -c "import json;print(json.load(open('c2_ids_41.json'))['datasets']['stores'])")
step "C1 again"; $PY measure.py --version 41 --user admin --out rec/41-c1w $C1
step "C2 again"; $PY measure.py --version 41 --user admin --out rec/41-c2w $C2
$VENV analyze.py rec/41-c1w rec/41-c2w
$VENV area.py --rls "$RLS" rec/41-c1w rec/41-c2w
step "time_compare"; bash run_tc.sh 41
step R41W_DONE
