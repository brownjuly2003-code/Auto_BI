#!/bin/bash
# Read-only role for Auto_BI (ARCHITECTURE §4): SELECT on dm.* only.
set -euo pipefail

clickhouse-client --user "${CLICKHOUSE_USER}" --password "${CLICKHOUSE_PASSWORD}" --multiquery --query "
CREATE USER IF NOT EXISTS auto_bi_ro IDENTIFIED BY '${AUTO_BI_RO_PASSWORD:-change_me}';
GRANT SELECT ON dm.* TO auto_bi_ro;
"

echo "auto_bi_ro user ready (SELECT on dm.* only)"
