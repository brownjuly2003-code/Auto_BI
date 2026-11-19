#!/bin/bash
# E4a C2 run on one Superset version (41|61): the ClickHouse corpus and the probes, unattended.
#  1. every C2 chart opened in Explore by the reader (a non-owner viewer), then by the admin for
#     any chart still without a query_context;
#  2. the stale probe (params changed by the API, query_context kept);
#  3. dashboards measured as the admin and as the reader; p-jinja once more with ?region= in the URL;
#  4. analyze.
# Usage: bash c2_run.sh 41
set -u
V=$1
S="${E4A_STAND:-$HOME/e4astand}"
cd "$S"
PY=pwvenv/bin/python
B=$($PY -c "import json;d=json.load(open('c2_ids_$V.json'))['dashboards'];print(' '.join('--dashboard %d'%i for i in d.values()))")
J=$($PY -c "import json;print(json.load(open('c2_ids_$V.json'))['dashboards']['p-jinja'])")
echo "== explore-open reader"; $PY measure.py --version "$V" --user reader --out "rec/$V-c2open-reader" --explore-open $B
echo "== explore-open admin"; $PY measure.py --version "$V" --user admin --out "rec/$V-c2open-admin" --explore-open $B
echo "== stale"; $PY stale.py "$V" "rec/$V-c2"
echo "== measure admin"; $PY measure.py --version "$V" --user admin --out "rec/$V-c2" $B
echo "== measure reader"; $PY measure.py --version "$V" --user reader --out "rec/$V-c2r" $B
echo "== measure jinja url"; $PY measure.py --version "$V" --user admin --out "rec/$V-c2url" --dashboard "$J" --url-suffix "?region=%D0%A1%D0%97%D0%A4%D0%9E"
echo "== analyze"; $PY analyze.py "rec/$V-c2" "rec/$V-c2r" "rec/$V-c2url"
echo C2_DONE
