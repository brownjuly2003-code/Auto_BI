#!/bin/bash
# E4a review probe on Superset 4.1.2 (stand up): a REST save with unchanged params on C1 charts
# whose query_context Explore wrote (dashboards 2 and 11), then the two dashboards measured into
# rec/41-c1rest and analyzed. Usage: bash run_rest.sh
# The probe's own run lacked the PATH export: the measure step was run again on its own.
set -u
cd "${E4A_STAND:-$HOME/e4astand}"
# measure.py reads the engine logs through docker: nohup and ssh shells lack its PATH.
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
export DOCKER_CONTEXT=colima-autobi-linux
PY=pwvenv/bin/python
VENV=/tmp/autobi_t47/venv/bin/python
step() { echo "== $(date -u +%H:%M:%S) $*"; }
step "rest save"; $PY rest_save.py 41 rec/41-c1rest 12 15 17 83 86 87 100 101 || exit 4
step "measure"; $PY measure.py --version 41 --user admin --out rec/41-c1rest --dashboard 2 --dashboard 11
$VENV analyze.py rec/41-c1rest
$VENV area.py rec/41-c1rest
step REST_DONE
