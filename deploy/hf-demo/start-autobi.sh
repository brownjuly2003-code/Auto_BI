#!/bin/bash
# auto_bi wrapper (P8 demo): wait for Superset, then serve in auto-overview-only
# mode. All settings are env-driven (config.py::Settings); nothing here is a real
# secret — ClickHouse and Superset listen on localhost inside the container only.
set -euo pipefail

echo "[autobi] waiting for superset..."
until curl -fsS http://127.0.0.1:8088/health >/dev/null 2>&1; do sleep 2; done

# auto-overview stays the DEFAULT (safe: no LLM, zero budget). Set AUTO_BI_DEMO_AUTO_ONLY=false
# (or DEMO_AUTO_ONLY=false) in the Space to open the text/fields path — then wire an LLM provider
# and a session quota below.
export AUTO_BI_DEMO_AUTO_ONLY="${AUTO_BI_DEMO_AUTO_ONLY:-${DEMO_AUTO_ONLY:-true}}"
if [ "${AUTO_BI_DEMO_AUTO_ONLY}" = "false" ]; then
    # Text path is LIVE. Provider defaults to GraceKelly (claude-sonnet-5); its URL must be a
    # PUBLIC tunnel to a running GraceKelly — a Space container CANNOT reach 127.0.0.1 on your
    # machine, so set AUTO_BI_GRACEKELLY_URL to the tunnel (ngrok/cloudflared) in the Space
    # secrets. Alternatively use the direct Anthropic API (AUTO_BI_LLM_PROVIDER=anthropic +
    # ANTHROPIC_API_KEY, needs an image built with the anthropic extra). The per-IP session quota
    # is forced ON so an anonymous visitor cannot drain the LLM budget.
    export AUTO_BI_LLM_PROVIDER="${AUTO_BI_LLM_PROVIDER:-gracekelly}"
    export AUTO_BI_SESSION_RATE_ENABLED="${AUTO_BI_SESSION_RATE_ENABLED:-true}"
    export AUTO_BI_SESSION_RATE_PER_DAY="${AUTO_BI_SESSION_RATE_PER_DAY:-50}"
    # behind nginx in the container: trust the loopback proxy so the per-IP quota is truly per-IP
    export AUTO_BI_FORWARDED_ALLOW_IPS="${AUTO_BI_FORWARDED_ALLOW_IPS:-127.0.0.1}"
    echo "[autobi] TEXT path ENABLED — provider=${AUTO_BI_LLM_PROVIDER}, quota ${AUTO_BI_SESSION_RATE_PER_DAY}/day/IP"
else
    echo "[autobi] auto-overview only (text/fields/enrichment disabled)"
fi
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
