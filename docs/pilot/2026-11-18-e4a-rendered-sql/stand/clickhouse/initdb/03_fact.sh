#!/bin/bash
# Synthetic fact: DEMO_FACT_ROWS rows (default 100M) over 730 days from 2024-07-01,
# generated sorted by date to match ORDER BY (date, store_id, product_id) cheaply.
set -euo pipefail

ROWS="${DEMO_FACT_ROWS:-100000000}"
echo "Generating dm.sales_daily: ${ROWS} rows..."

clickhouse-client --user "${CLICKHOUSE_USER}" --password "${CLICKHOUSE_PASSWORD}" --query "
INSERT INTO dm.sales_daily
WITH
    greatest(1, intDiv(${ROWS}, 730)) AS rows_per_day,
    [1.0, 0.95, 1.05, 1.0, 0.9, 0.85, 0.9, 1.0, 1.1, 1.15, 1.2, 1.45] AS month_mult
SELECT
    toDate('2024-07-01') + toIntervalDay(least(729, intDiv(number, rows_per_day))) AS date,
    toUInt32(cityHash64(number, 1) % 4200 + 1) AS store_id,
    toUInt32(cityHash64(number, 2) % 2000 + 1) AS product_id,
    toUInt32((store_id - 1) * 4 + cityHash64(number, 3) % 4 + 1) AS manager_id,
    toDecimal64(
        round((1 + cityHash64(number, 5) % 20) * (150 + cityHash64(number, 4) % 1850)
              * month_mult[toMonth(date)], 2),
        2) AS revenue,
    toUInt32(1 + cityHash64(number, 6) % (1 + cityHash64(number, 5) % 20)) AS orders,
    toUInt32(1 + cityHash64(number, 5) % 20) AS items
FROM numbers(${ROWS})
SETTINGS max_insert_threads = 4
"

echo "dm.sales_daily ready: $(clickhouse-client --user "${CLICKHOUSE_USER}" --password "${CLICKHOUSE_PASSWORD}" --query 'SELECT count() FROM dm.sales_daily') rows"
