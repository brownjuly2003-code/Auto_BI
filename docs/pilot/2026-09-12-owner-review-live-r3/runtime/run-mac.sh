#!/bin/sh
set -eu

source_archive=${1:?source archive is required}
result_archive=${2:?result archive is required}
work_root=$(mktemp -d /tmp/auto-bi-owner-r3.XXXXXX)
server_pid_value=""

cleanup() {
    if [ -n "$server_pid_value" ]; then
        kill "$server_pid_value" 2>/dev/null || true
        wait "$server_pid_value" 2>/dev/null || true
    fi
    case "$work_root" in
        /tmp/auto-bi-owner-r3.*) rm -rf "$work_root" ;;
    esac
}
trap cleanup EXIT HUP INT TERM

if [ -e "$result_archive" ]; then
    printf 'result archive already exists: %s\n' "$result_archive" >&2
    exit 2
fi

tar -xf "$source_archive" -C "$work_root"
cd "$work_root"
/Users/julia/.local/bin/uv sync --frozen --quiet

set -a
. /Users/julia/chstand/creds.env
set +a

stand_root="$work_root/chstand"
mkdir -p "$stand_root/access" "$stand_root/data" "$stand_root/format_schemas" \
    "$stand_root/logs" "$stand_root/tmp" "$stand_root/user_files"
sed \
    -e "s#/Users/julia/chstand#$stand_root#g" \
    -e 's#<http_port>8123</http_port>#<http_port>18123</http_port>#' \
    -e 's#<tcp_port>9000</tcp_port>#<tcp_port>19000</tcp_port>#' \
    /Users/julia/chstand/config.xml >"$stand_root/config.xml"
sed '/<gate_ro>/,/<\/gate_ro>/d' /Users/julia/chstand/users.xml >"$stand_root/users.xml"

/Users/julia/chlocal/clickhouse24 server \
    --config-file="$stand_root/config.xml" \
    >"$work_root/clickhouse.log" 2>&1 &
server_pid_value=$!

/usr/bin/curl --fail --silent --retry 20 --retry-delay 1 --retry-all-errors --max-time 2 \
    --user "default:$CH_ADMIN_PASSWORD" --data-binary "SELECT 1" \
    http://127.0.0.1:18123/ >/dev/null

/Users/julia/chlocal/clickhouse24 client \
    --host 127.0.0.1 --port 19000 --user default --password "$CH_ADMIN_PASSWORD" \
    --multiquery <docs/pilot/2026-09-12-owner-review-live-r3/runtime/schema.sql
/Users/julia/chlocal/clickhouse24 client \
    --host 127.0.0.1 --port 19000 --user default --password "$CH_ADMIN_PASSWORD" \
    --query "CREATE USER gate_ro IDENTIFIED WITH plaintext_password BY '$CH_GATE_PASSWORD' SETTINGS readonly = 2"
/Users/julia/chlocal/clickhouse24 client \
    --host 127.0.0.1 --port 19000 --user default --password "$CH_ADMIN_PASSWORD" \
    --query "GRANT SELECT ON dm.* TO gate_ro"
/Users/julia/chlocal/clickhouse24 client \
    --host 127.0.0.1 --port 19000 --user default --password "$CH_ADMIN_PASSWORD" \
    --query "GRANT SELECT ON system.* TO gate_ro"

export AUTO_BI_CH_HOST=127.0.0.1
export AUTO_BI_CH_PORT=18123
export AUTO_BI_CH_DATABASE=dm
export AUTO_BI_CH_USER=gate_ro
export AUTO_BI_CH_PASSWORD="$CH_GATE_PASSWORD"

package_root=docs/pilot/2026-09-12-owner-review-live-r3
environment_ref=owner-review-mac-native-ch24.8.14.39
rm -rf "$package_root/evidence" "$package_root/trust"
rm -f "$package_root/results.yaml"

private_key=/Users/julia/.auto-bi/owner-review-r3-signing-private.pem
public_key=/Users/julia/.auto-bi/owner-review-r3-signing-public.pem
mkdir -p /Users/julia/.auto-bi
if [ ! -f "$private_key" ]; then
    /Users/julia/.local/bin/uv run --frozen python - "$private_key" <<'PY'
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

private_path = Path(sys.argv[1])
payload = Ed25519PrivateKey.generate().private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)
descriptor = os.open(private_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "wb") as stream:
    stream.write(payload)
PY
fi
chmod 600 "$private_key"
/Users/julia/.local/bin/uv run --frozen python - "$private_key" "$public_key" <<'PY'
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization

private_key = serialization.load_pem_private_key(Path(sys.argv[1]).read_bytes(), password=None)
Path(sys.argv[2]).write_bytes(
    private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
)
PY
chmod 644 "$public_key"
mkdir -p "$package_root/trust"
cp "$public_key" "$package_root/trust/evidence-public.pem"

/Users/julia/.local/bin/uv run --frozen auto_bi decision capture \
    "$package_root/assets" \
    --candidate "$package_root/candidate" \
    --asset asset://sales/weekly_margin_by_region \
    --dwh clickhouse \
    --environment "$environment_ref" \
    --output "$package_root/results.yaml"

created_at=$(/bin/date -u '+%Y-%m-%dT%H:%M:%SZ')
set +e
/Users/julia/.local/bin/uv run --frozen auto_bi decision verify \
    "$package_root/contracts" \
    --base "$package_root/base" \
    --candidate "$package_root/candidate" \
    --dashboards "$package_root/dashboards" \
    --assets "$package_root/assets" \
    --results "$package_root/results.yaml" \
    --product decision://sales/weekly_margin \
    --dwh clickhouse \
    --environment "$environment_ref" \
    --created-at "$created_at" \
    --signing-key "$private_key" \
    --evidence "$package_root/evidence" \
    --json >"$work_root/verify.json"
verify_exit=$?
set -e
if [ "$verify_exit" -ne 4 ]; then
    tail -40 "$work_root/verify.json"
    exit 41
fi

/Users/julia/.local/bin/uv run --frozen auto_bi decision evidence \
    "$package_root/evidence" \
    --trust-key "$package_root/trust/evidence-public.pem" \
    --json >"$work_root/evidence-inspection.json"

/Users/julia/.local/bin/uv run --frozen python - \
    "$package_root" "$work_root/verify.json" "$work_root/evidence-inspection.json" <<'PY'
import json
import sys
from pathlib import Path

package_root = Path(sys.argv[1])
report = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
inspection = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
manifests = list((package_root / "evidence").glob("run-*/manifest.json"))
assert len(manifests) == 1, manifests
manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
assert manifest["integrity"] == "signed", manifest["integrity"]
assert report["run_id"] == manifest["run_id"], report["run_id"]
assert report["verdict"] == "block", report["verdict"]
assert inspection["run_id"] == manifest["run_id"], inspection["run_id"]
assert inspection["verdict"] == "pass", inspection["verdict"]
assert inspection["signature_verified"] is True, inspection["signature_verified"]

expected = {
    "contracts.validate": "pass",
    "impact.products": "block",
    "results.reconcile": "pass",
    "results.provenance": "pass",
    "sql.compile": "pass",
    "dwh.access": "pass",
    "dwh.physical": "pass",
    "dwh.security": "pass",
    "dwh.cost": "pass",
}
checks = {check["check_id"]: check for check in report["checks"]}
actual = {name: checks[name]["status"] for name in expected}
cost_path = manifests[0].parent / "checks" / "check-dwh.cost.json"
cost_payload = json.loads(cost_path.read_text(encoding="utf-8"))
cost_facts = cost_payload["facts"]["executions"]
if actual != expected:
    for name, wanted in expected.items():
        if actual[name] != wanted:
            check = checks[name]
            print(
                f"UNEXPECTED_CHECK={name} STATUS={actual[name]} "
                f"SUMMARY={check['summary_code']} DETAIL={check['detail']}"
            )
    print(f"COST_FACTS={json.dumps(cost_payload['facts'], sort_keys=True)}")
assert actual == expected, actual
assert len(cost_facts) == 1, cost_facts
assert cost_facts[0]["checked_bounds"] == ["max_scanned_bytes", "timeout_ms"], cost_facts

print(f"RUN_ID={manifest['run_id']}")
print(f"INTEGRITY={manifest['integrity']}")
print(f"KEY_ID={manifest['signature']['key_id']}")
print(f"EVIDENCE_DIGEST={inspection['manifest_digest']}")
print(f"CHECKS={json.dumps(actual, sort_keys=True)}")
print(f"COST_BOUNDS={','.join(cost_facts[0]['checked_bounds'])}")
PY

tar -cf "$result_archive" -C "$package_root" results.yaml evidence trust
printf 'VERIFY_EXIT=%s\n' "$verify_exit"
printf 'RESULT_ARCHIVE=%s\n' "$result_archive"
printf 'DONE\n'
