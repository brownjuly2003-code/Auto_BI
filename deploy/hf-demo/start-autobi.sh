#!/bin/bash
# auto_bi wrapper (P8 demo): wait for Superset, then serve in auto-overview-only
# mode. All settings are env-driven (config.py::Settings); nothing here is a real
# secret — ClickHouse and Superset listen on localhost inside the container only.
set -euo pipefail

echo "[autobi] waiting for superset..."
until curl -fsS http://127.0.0.1:8088/health >/dev/null 2>&1; do sleep 2; done

export AUTO_BI_DEMO_AUTO_ONLY=true
export AUTO_BI_CH_HOST=127.0.0.1
export AUTO_BI_CH_PORT=8123
export AUTO_BI_CH_USER=auto_bi_ro
export AUTO_BI_CH_PASSWORD="${AUTO_BI_RO_PASSWORD:-demo_ro_only}"
export AUTO_BI_SUPERSET_URL=http://127.0.0.1:8088
export AUTO_BI_SUPERSET_USER="${ADMIN_USERNAME:-admin}"
export AUTO_BI_SUPERSET_PASSWORD="${ADMIN_PASSWORD:-demo_admin_only}"
# the dashboard LINK must point at the public host, not the in-container one;
# HF Spaces provide SPACE_HOST (e.g. liovina-auto-bi-demo.hf.space)
if [ -n "${SPACE_HOST:-}" ]; then
    export AUTO_BI_SUPERSET_PUBLIC_URL="https://${SPACE_HOST}"
else
    export AUTO_BI_SUPERSET_PUBLIC_URL="${DEMO_PUBLIC_URL:-http://localhost:7860}"
fi
# created HERE, not in the Dockerfile: build runs as root, this runs as UID 1000
mkdir -p /tmp/auto_bi
export AUTO_BI_STORE_PATH=/tmp/auto_bi/store.sqlite

echo "[autobi] serving (public url: ${AUTO_BI_SUPERSET_PUBLIC_URL})..."
exec /opt/autobi/bin/auto_bi serve \
    --model-path /opt/demo/semantic/model.yaml \
    --host 127.0.0.1 --port 8200 --log-format json --log-level INFO
