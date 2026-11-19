#!/bin/bash
# E4a on Superset 6.1.0: the Save probe again for the charts whose first save did not land
# (rec/61-c2v/ui_save.json without status 200: Explore opened without the chart, or no PUT),
# with ui_save reloading an empty Explore; then the three C2 dashboards measured (rec/61-c2v2).
# Usage: bash run_61c.sh
set -u
S="${E4A_STAND:-$HOME/e4astand}"
cd "$S"
PY=pwvenv/bin/python
VENV=/tmp/autobi_t47/venv/bin/python
step() { echo "== $(date -u +%H:%M:%S) $*"; }
AGAIN=$($PY -c "
import json
d = json.load(open('rec/61-c2v/ui_save.json'))
print(' '.join(f'--ui-save {k}' for k, v in d.items() if v.get('status') != 200))")
step "save again: $AGAIN"
$PY measure.py --version 61 --user admin --out rec/61-c2v2 $AGAIN
$PY measure.py --version 61 --user admin --out rec/61-c2v2 $($PY -c "
import json
d = json.load(open('c2_ids_61.json'))['dashboards']
print(' '.join(f'--dashboard {d[k]}' for k in ('c2-1', 'c2-2', 'c2-3')))")
$VENV analyze.py rec/61-c2v rec/61-c2v2
step R61C_DONE
