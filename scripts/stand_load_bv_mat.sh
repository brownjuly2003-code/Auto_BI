#!/usr/bin/env bash
# Stand adaptation of DE_project's load_bv_order_canonical_mat.sh (x5_optionA).
# Target: auto_bi_clickhouse container (CH 24.8) on the Mac stand instead of
# the kind-cluster pod. Same staged per-branch strategy + frugal settings —
# the slice (15M line items, ~2.6M orders) is ~1/3 of the scale the original
# was hardened for, so the pod-bounce machinery is replaced by a single
# `docker restart` retry per branch.
# Usage (on the Mac): bash stand_load_bv_mat.sh [branch ...]   # default all 5
set -euo pipefail

BRANCHES=("$@")
[ ${#BRANCHES[@]} -eq 0 ] && BRANCHES=(msk spb ekb dxb ala)

export PATH=$HOME/bin:/usr/local/bin:$PATH
CH_PASS=$(grep '^CH_ADMIN_PASSWORD=' ~/auto_bi_stand/.env | cut -d= -f2)

ch() {
  docker exec -i auto_bi_clickhouse clickhouse-client \
    --user admin --password "$CH_PASS" --multiquery
}

purge() { echo "SYSTEM JEMALLOC PURGE;" | ch; sleep 3; }

restart_ch() {
  echo "[$(date '+%F %T')] restarting auto_bi_clickhouse"
  docker restart auto_bi_clickhouse >/dev/null
  until echo "SELECT 1" | ch >/dev/null 2>&1; do sleep 5; done
  sleep 10
}

FRUGAL="max_threads = 2,
    max_bytes_before_external_group_by = 805306368,
    max_bytes_before_external_sort = 805306368,
    max_memory_usage = 2500000000"

stage_header_sql() {
  local B=$1
  cat <<SQL
DROP TABLE IF EXISTS rv._bvmat_header_${B};
CREATE TABLE rv._bvmat_header_${B} ENGINE = MergeTree ORDER BY order_hk AS
SELECT
    order_hk,
    argMax(order_date, load_ts)    AS order_date,
    argMax(channel, load_ts)       AS channel,
    argMax(order_status, load_ts)  AS order_status,
    argMax(total_amount, load_ts)  AS total_amount
FROM (
    SELECT order_hk, order_date, channel, order_status, total_amount, load_ts
    FROM rv.sat_order_header__bitrix__${B} WHERE is_deleted = 0
    UNION ALL
    SELECT order_hk, order_date, channel, order_status, total_amount, load_ts
    FROM rv.sat_order_header__1c__${B} WHERE is_deleted = 0
)
GROUP BY order_hk
SETTINGS ${FRUGAL};
SQL
}

stage_pricing_sql() {
  local B=$1
  cat <<SQL
DROP TABLE IF EXISTS rv._bvmat_pricing_${B};
CREATE TABLE rv._bvmat_pricing_${B} ENGINE = MergeTree ORDER BY order_hk AS
SELECT
    order_hk,
    argMax(subtotal_amount, load_ts)  AS subtotal_amount,
    argMax(discount_amount, load_ts)  AS discount_amount,
    argMax(tax_amount, load_ts)       AS tax_amount,
    argMax(shipping_cost, load_ts)    AS shipping_cost
FROM rv.sat_order_pricing__1c__${B}
WHERE is_deleted = 0
GROUP BY order_hk
SETTINGS ${FRUGAL};
SQL
}

stage_customer_sql() {
  local B=$1
  cat <<SQL
DROP TABLE IF EXISTS rv._bvmat_customer_${B};
CREATE TABLE rv._bvmat_customer_${B} ENGINE = MergeTree ORDER BY order_hk AS
SELECT order_hk, argMax(customer_hk, load_ts) AS customer_hk
FROM rv.lnk_order_customer
WHERE order_hk IN (
    SELECT order_hk FROM rv.hub_order
    WHERE splitByString('__', record_source)[2] = '${B}')
GROUP BY order_hk
SETTINGS ${FRUGAL};
SQL
}

stage_store_sql() {
  local B=$1
  cat <<SQL
DROP TABLE IF EXISTS rv._bvmat_store_${B};
CREATE TABLE rv._bvmat_store_${B} ENGINE = MergeTree ORDER BY order_hk AS
SELECT order_hk, argMax(store_hk, load_ts) AS store_hk
FROM rv.lnk_order_store
WHERE order_hk IN (
    SELECT order_hk FROM rv.hub_order
    WHERE splitByString('__', record_source)[2] = '${B}')
GROUP BY order_hk
SETTINGS ${FRUGAL};
SQL
}

final_insert_sql() {
  local B=$1
  cat <<SQL
INSERT INTO rv.bv_order_canonical_mat
    (order_hk, order_bk, branch, customer_hk, store_hk,
     order_date, channel, order_status, total_amount,
     subtotal_amount, discount_amount, tax_amount, shipping_cost,
     wb_status, wb_commission, wb_return_window_until,
     header_source, pricing_source, marketplace_source)
WITH
    order_branch AS (
        SELECT DISTINCT order_hk, order_bk, '${B}' AS branch
        FROM rv.hub_order
        WHERE splitByString('__', record_source)[2] = '${B}'
    ),
    marketplace AS (
        SELECT
            order_hk,
            argMax(wb_status, load_ts)           AS wb_status,
            argMax(wb_commission, load_ts)       AS wb_commission,
            argMax(return_window_until, load_ts) AS wb_return_window_until
        FROM rv.sat_order_marketplace__wb__msk
        WHERE is_deleted = 0
        GROUP BY order_hk
    )
SELECT
    o.order_hk           AS order_hk,
    o.order_bk           AS order_bk,
    o.branch             AS branch,
    oc.customer_hk       AS customer_hk,
    os.store_hk          AS store_hk,
    h.order_date         AS order_date,
    h.channel            AS channel,
    h.order_status       AS order_status,
    h.total_amount       AS total_amount,
    p.subtotal_amount    AS subtotal_amount,
    p.discount_amount    AS discount_amount,
    p.tax_amount         AS tax_amount,
    p.shipping_cost      AS shipping_cost,
    m.wb_status          AS wb_status,
    m.wb_commission      AS wb_commission,
    m.wb_return_window_until AS wb_return_window_until,
    if(h.order_hk != toFixedString('', 16), concat('1c__', o.branch), NULL)  AS header_source,
    if(p.order_hk != toFixedString('', 16), concat('1c__', o.branch), NULL)  AS pricing_source,
    if(m.order_hk != toFixedString('', 16), 'wb__msk', NULL)                 AS marketplace_source
FROM order_branch o
LEFT JOIN rv._bvmat_header_${B}   h  ON o.order_hk = h.order_hk
LEFT JOIN rv._bvmat_pricing_${B}  p  ON o.order_hk = p.order_hk
LEFT JOIN marketplace             m  ON o.order_hk = m.order_hk
LEFT JOIN rv._bvmat_customer_${B} oc ON o.order_hk = oc.order_hk
LEFT JOIN rv._bvmat_store_${B}    os ON o.order_hk = os.order_hk
SETTINGS
    join_algorithm = 'full_sorting_merge',
    ${FRUGAL}
SQL
}

drop_stage_sql() {
  local B=$1
  cat <<SQL
DROP TABLE IF EXISTS rv._bvmat_header_${B};
DROP TABLE IF EXISTS rv._bvmat_pricing_${B};
DROP TABLE IF EXISTS rv._bvmat_customer_${B};
DROP TABLE IF EXISTS rv._bvmat_store_${B};
SQL
}

load_branch() {
  local B=$1
  echo "[$(date '+%F %T')] BRANCH ${B}: drop partition (clean retry)" &&
  echo "ALTER TABLE rv.bv_order_canonical_mat DROP PARTITION '${B}';" | ch || return 1
  local stage
  for stage in header pricing customer store; do
    echo "[$(date '+%F %T')] BRANCH ${B}: stage ${stage}"
    "stage_${stage}_sql" "$B" | ch || return 1
    purge || return 1
  done
  echo "[$(date '+%F %T')] BRANCH ${B}: final merge-join INSERT"
  final_insert_sql "$B" | ch || return 1
  purge
  echo "[$(date '+%F %T')] BRANCH ${B}: drop stage tables"
  drop_stage_sql "$B" | ch || return 1
  echo "[$(date '+%F %T')] BRANCH ${B}: DONE"
}

echo "[$(date '+%F %T')] DDL (idempotent)"
ch < "$HOME/x5_optionA/bv_order_canonical_mat.sql"

for B in "${BRANCHES[@]}"; do
  if ! load_branch "$B"; then
    echo "[$(date '+%F %T')] BRANCH ${B}: attempt 1 failed — restart CH and retry once"
    restart_ch
    if ! load_branch "$B"; then
      echo "[$(date '+%F %T')] BRANCH ${B}: FAILED after retry"
      echo "MAT_LOAD_FAILED branch=${B}"
      exit 1
    fi
  fi
done

echo "[$(date '+%F %T')] verify: per-branch counts + revenue (mat)"
ch <<'SQL'
SELECT branch, count() AS rows, sum(total_amount) AS revenue
FROM rv.bv_order_canonical_mat
GROUP BY branch ORDER BY branch FORMAT TSVWithNames;
SQL
echo "[$(date '+%F %T')] MAT_LOAD_DONE"
