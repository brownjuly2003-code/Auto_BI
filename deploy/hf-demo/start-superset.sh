#!/bin/bash
# Superset wrapper (P8 demo): wait for ClickHouse -> seed the demo DM (first start
# on this disk) -> bootstrap Superset metadata -> grant the Public role -> serve.
set -euo pipefail

# ONE secret key per container lifetime (all gunicorn workers + supervisord restarts
# of this program must share it — see superset_config.py); a Space restart wipes the
# metadata DB anyway, so regenerating there is fine.
SECRET_FILE=/tmp/superset_secret_key
if [ -z "${SUPERSET_SECRET_KEY:-}" ]; then
    [ -f "$SECRET_FILE" ] || python3 -c 'import secrets; print(secrets.token_hex(32))' > "$SECRET_FILE"
    SUPERSET_SECRET_KEY="$(cat "$SECRET_FILE")"
fi
export SUPERSET_SECRET_KEY

echo "[superset] waiting for clickhouse..."
until clickhouse-client --query "SELECT 1" >/dev/null 2>&1; do sleep 2; done

# Demo DM: the same init scripts the compose stand mounts into the CH entrypoint.
# The Space disk is ephemeral, so in practice this runs on every container start;
# the guard keeps a supervisord-restart of this program from re-seeding.
export CLICKHOUSE_USER=default CLICKHOUSE_PASSWORD="" \
       AUTO_BI_RO_PASSWORD="${AUTO_BI_RO_PASSWORD:-demo_ro_only}"
FACT_ROWS="$(clickhouse-client --query \
    "SELECT count() FROM system.tables WHERE database='dm' AND name='sales_daily'")"
if [ "$FACT_ROWS" = "1" ]; then
    FACT_ROWS="$(clickhouse-client --query "SELECT count() FROM dm.sales_daily")"
else
    FACT_ROWS=0
fi
if [ "$FACT_ROWS" = "0" ]; then
    # drop first: a partially-seeded dm (crash mid-init) would trip the CREATE TABLEs
    echo "[superset] seeding demo DM (DEMO_FACT_ROWS=${DEMO_FACT_ROWS:-1000000})..."
    clickhouse-client --query "DROP DATABASE IF EXISTS dm"
    for f in /opt/demo/initdb/*; do
        case "$f" in
            *.sql) clickhouse-client --multiquery < "$f" ;;
            *.sh)  bash "$f" ;;
        esac
    done
fi

echo "[superset] bootstrapping metadata..."
superset db upgrade
superset fab create-admin \
    --username "${ADMIN_USERNAME:-admin}" \
    --firstname Auto --lastname BI \
    --email admin@localhost \
    --password "${ADMIN_PASSWORD:-demo_admin_only}" \
    || true  # already exists when supervisord restarts this program
superset init
python /opt/demo/superset_public_role.py

echo "[superset] starting server..."
exec /usr/bin/run-server.sh
