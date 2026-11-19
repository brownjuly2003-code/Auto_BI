#!/bin/bash
# E4a on Superset 6.1.0, unattended, after stand_up.sh 61: the same steps as on 4.1.2.
#  C1 as loaded -> Explore-open of every example chart -> C1 again; inventory reader v1 on both;
#  C2 corpus with the reader, role and RLS rule -> c2_run.sh 61 (Explore-open, measure admin,
#  reader, URL) -> stale probe -> Save probe on owned C2 charts -> their dashboards again.
# Usage: bash run_61.sh   (logs in logs/r61_*.log; never prints a secret)
set -u
S="${E4A_STAND:-$HOME/e4astand}"
cd "$S"
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
export DOCKER_CONTEXT=colima-autobi-linux
PY=pwvenv/bin/python
VENV=/tmp/autobi_t47/venv/bin/python
step() { echo "== $(date -u +%H:%M:%S) $*"; }

C1=$($PY - <<'EOF'
from measure import Api
api = Api("http://127.0.0.1:8089", "admin")
status, body = api.get("/api/v1/dashboard/", q="(page_size:100)")
print(" ".join(f"--dashboard {d['id']}" for d in sorted(body["result"], key=lambda d: d["id"])))
EOF
)
step "C1 dashboards: $C1"
step "C1 as loaded"; $PY measure.py --version 61 --user admin --out rec/61-c1 $C1
$PY analyze.py rec/61-c1
IDS=$(echo "$C1" | sed 's/--dashboard //g')
$VENV inventory_forms.py 61 rec/61-c1/forms.json $IDS 2>&1 | tail -2
step "C1 explore-open"; $PY measure.py --version 61 --user admin --out rec/61-c1s --explore-open $C1
step "C1 after explore-open"; $PY measure.py --version 61 --user admin --out rec/61-c1s $C1
$PY analyze.py rec/61-c1s

step "C2 setup"
$VENV c2_setup.py 61 | tail -c 300; echo
set -a; . ./.env; set +a
export U="$INVENTORY_SUPERSET_USER" PW="$INVENTORY_SUPERSET_PASSWORD"
ROLE=$(docker exec -i -e U -e PW e4a_superset61 sh -c 'exec $(command -v python || echo /app/.venv/bin/python) -' < reader_role.py 2>/dev/null | grep "role id" | tee /dev/stderr | awk '{print $NF}')
unset PW INVENTORY_SUPERSET_PASSWORD AUTO_BI_SUPERSET_PASSWORD
step "reader role id $ROLE"
cp c2_ids_61.json c2_ids_61.first.json
E4A_READER_ROLE_ID=$ROLE $VENV c2_setup.py 61 > /dev/null
$PY - <<'EOF'
import json
a = json.load(open("c2_ids_61.first.json")); b = json.load(open("c2_ids_61.json"))
b["charts"] = a["charts"]; json.dump(b, open("c2_ids_61.json", "w"), ensure_ascii=False, indent=1)
print("rls", b.get("rls"), "charts", sum(len(v) for v in b["charts"].values()))
EOF
step "C2 run"; bash c2_run.sh 61
step "stale"; $PY stale.py 61 rec/61-c2stale && $PY measure.py --version 61 --user admin --out rec/61-c2stale \
  --dashboard "$($PY -c "import json;print(json.load(open('c2_ids_61.json'))['dashboards']['p-stale'])")"
step "save probe"
SAVE=$($PY -c "
import json
c = json.load(open('c2_ids_61.json'))['charts']
pick = [c['c2-1'][4], c['c2-2'][6], c['c2-3'][0], c['c2-2'][0], c['c2-3'][1], c['c2-3'][4], c['c2-3'][7], c['c2-3'][6]]
print(' '.join(f'--ui-save {i}' for i in pick))")
$PY measure.py --version 61 --user admin --out rec/61-c2v $SAVE
$PY measure.py --version 61 --user admin --out rec/61-c2v $($PY -c "
import json
d = json.load(open('c2_ids_61.json'))['dashboards']
print(' '.join(f'--dashboard {d[k]}' for k in ('c2-1', 'c2-2', 'c2-3')))")
$PY analyze.py rec/61-c2v
step R61_DONE
