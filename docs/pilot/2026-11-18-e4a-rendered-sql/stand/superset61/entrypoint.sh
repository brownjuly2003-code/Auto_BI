#!/bin/bash
# First-start bootstrap (idempotent), then the stock server.
set -e

superset db upgrade
superset fab create-admin \
    --username "${ADMIN_USERNAME:-admin}" \
    --firstname Auto --lastname BI \
    --email admin@localhost \
    --password "${ADMIN_PASSWORD:-change_me}" \
    || true  # already exists on restarts
superset init

exec /usr/bin/run-server.sh
