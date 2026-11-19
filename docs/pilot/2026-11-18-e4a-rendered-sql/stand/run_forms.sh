#!/bin/bash
# E4a: the inventory reader v1 on the C2 corpus of both versions (typed / read SQL / refused), as
# the corpus stands after the probes; C1 was read before any probe (rec/<V>-c1/forms.json).
# 6.1 first (the stand is on it after run_61d.sh), then 4.1.2, which stays up afterwards.
# Usage: bash run_forms.sh
set -u
S="${E4A_STAND:-$HOME/e4astand}"
cd "$S"
PY=pwvenv/bin/python
VENV=/tmp/autobi_t47/venv/bin/python
step() { echo "== $(date -u +%H:%M:%S) $*"; }
for V in 61 41; do
  if [ "$V" = 41 ]; then step "stand 41"; bash stand_up.sh 41 2>&1 | grep -v -i password | tail -4; fi
  IDS=$($PY -c "import json;print(' '.join(str(i) for i in json.load(open('c2_ids_$V.json'))['dashboards'].values()))")
  mkdir -p "rec/$V-c2forms"
  step "forms $V C2: $IDS"; $VENV inventory_forms.py "$V" "rec/$V-c2forms/forms.json" $IDS
done
step FORMS_DONE
