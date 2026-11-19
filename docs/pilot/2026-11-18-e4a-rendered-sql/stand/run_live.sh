#!/bin/bash
# E4a review r2 live probes on Superset 4.1.2 (stand up, after run_41w.sh): live_probe.py, then
# the three C2 viz dashboards measured into rec/41-c2live and analyzed. Usage: bash run_live.sh
set -u
cd "${E4A_STAND:-$HOME/e4astand}"
# measure.py reads the engine logs through docker: nohup and ssh shells lack its PATH.
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
export DOCKER_CONTEXT=colima-autobi-linux
PY=pwvenv/bin/python
VENV=/tmp/autobi_t47/venv/bin/python
step() { echo "== $(date -u +%H:%M:%S) $*"; }
BOARDS=$($PY -c "import json;d=json.load(open('c2_ids_41.json'))['dashboards'];print(' '.join(f'--dashboard {d[k]}' for k in ('c2-1','c2-2','c2-3')))")
step "probes"; $PY live_probe.py rec/41-c2live || exit 4
step "measure $BOARDS"; $PY measure.py --version 41 --user admin --out rec/41-c2live $BOARDS
$VENV analyze.py rec/41-c2live
$VENV area.py rec/41-c2live
step LIVE_DONE
