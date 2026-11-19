#!/bin/bash
# E4a time_compare probe on one version (41|61): enclosed range by the API, a UI save, then the
# p-multi dashboard measured and analyzed (rec/<V>-c2tc). Usage: bash run_tc.sh 41
set -u
V=$1
S="${E4A_STAND:-$HOME/e4astand}"
cd "$S"
PY=pwvenv/bin/python
VENV=/tmp/autobi_t47/venv/bin/python
step() { echo "== $(date -u +%H:%M:%S) $*"; }
# On 6.1 the Save probe is repeated first (run_61c.sh), before the probe below changes p-multi.
if [ "$V" = 61 ]; then bash run_61c.sh; fi
CHART=$($PY tc_probe.py "$V") || exit 4
step "tc chart $CHART: $(cat rec/$V-c2tc/put.json | tr -d '\n ')"
$PY measure.py --version "$V" --user admin --out "rec/$V-c2tc" --ui-save "$CHART"
D=$($PY -c "import json;print(json.load(open('c2_ids_$V.json'))['dashboards']['p-multi'])")
$PY measure.py --version "$V" --user admin --out "rec/$V-c2tc" --dashboard "$D"
$VENV analyze.py "rec/$V-c2tc"
$VENV diff_rows.py "rec/$V-c2tc" "$CHART"
step "TC_DONE $V"
