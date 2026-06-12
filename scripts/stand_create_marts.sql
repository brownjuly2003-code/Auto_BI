-- Stand materialization of DE_project's 3 dbt marts (warehouse/agentflow/dv2/dbt/models/marts)
-- against the X5 slice in the Auto_BI stand ClickHouse. dbt is not deployed on
-- the stand, so the models are inlined as CTAS with refs resolved:
--   source('rv', 'bv_order_canonical')   -> rv.bv_order_canonical_mat
--   source('rv', 'bv_customer_mdm__<b>') -> rv.bv_customer_mdm__<b>
-- The mart SQL itself is kept verbatim — this is somebody else's DM, its
-- shape (and its gaps) is the 1.10 test subject.

CREATE DATABASE IF NOT EXISTS marts;

DROP TABLE IF EXISTS marts.branch_pnl;
CREATE TABLE marts.branch_pnl
ENGINE = MergeTree ORDER BY (branch, month) AS
SELECT
    branch                                            AS branch,
    toStartOfMonth(order_date)                        AS month,
    count()                                           AS orders,
    sum(toFloat64(total_amount))                      AS gross_revenue,
    sum(toFloat64(tax_amount))                        AS tax_collected,
    sum(toFloat64(subtotal_amount))                   AS net_revenue,
    sum(toFloat64(discount_amount))                   AS discounts,
    sum(toFloat64(shipping_cost))                     AS shipping,
    countIf(order_status = 'returned')                AS returned_orders,
    sumIf(toFloat64(total_amount),
          order_status = 'returned')                  AS returned_value,
    round(sum(toFloat64(tax_amount)) /
          nullIf(sum(toFloat64(subtotal_amount)), 0),
          4)                                          AS effective_tax_rate
FROM rv.bv_order_canonical_mat
WHERE order_date IS NOT NULL
  AND subtotal_amount IS NOT NULL
GROUP BY branch, month
SETTINGS max_threads = 2, max_memory_usage = 2500000000;

DROP TABLE IF EXISTS marts.returns_velocity;
CREATE TABLE marts.returns_velocity
ENGINE = MergeTree ORDER BY (branch, channel, week) AS
SELECT
    branch                                              AS branch,
    channel                                             AS channel,
    toStartOfWeek(order_date)                           AS week,
    count()                                             AS orders,
    countIf(order_status = 'returned')                  AS returned_orders,
    round(countIf(order_status = 'returned') * 1.0 /
          count(), 4)                                   AS return_rate,
    sumIf(toFloat64(total_amount),
          order_status = 'returned')                    AS returned_value,
    sumIf(toFloat64(tax_amount),
          order_status = 'returned')                    AS returned_tax_unrecovered
FROM rv.bv_order_canonical_mat
WHERE order_date IS NOT NULL
  AND channel IS NOT NULL
GROUP BY branch, channel, week
SETTINGS max_threads = 2, max_memory_usage = 2500000000;

DROP TABLE IF EXISTS marts.customer_360;
CREATE TABLE marts.customer_360
ENGINE = MergeTree ORDER BY (branch, customer_hk) AS
WITH customers AS (
    SELECT customer_hk, customer_bk, branch, first_name, last_name, email,
           loyalty_segment, loyalty_points, last_visit_at, pii_source, loyalty_source
    FROM rv.bv_customer_mdm__msk
    UNION ALL
    SELECT customer_hk, customer_bk, branch, first_name, last_name, email,
           loyalty_segment, loyalty_points, last_visit_at, pii_source, loyalty_source
    FROM rv.bv_customer_mdm__spb
    UNION ALL
    SELECT customer_hk, customer_bk, branch, first_name, last_name, email,
           loyalty_segment, loyalty_points, last_visit_at, pii_source, loyalty_source
    FROM rv.bv_customer_mdm__ekb
    UNION ALL
    SELECT customer_hk, customer_bk, branch, first_name, last_name, email,
           loyalty_segment, loyalty_points, last_visit_at, pii_source, loyalty_source
    FROM rv.bv_customer_mdm__dxb
    UNION ALL
    SELECT customer_hk, customer_bk, branch, first_name, last_name, email,
           loyalty_segment, loyalty_points, last_visit_at, pii_source, loyalty_source
    FROM rv.bv_customer_mdm__ala
),
order_agg AS (
    SELECT
        customer_hk,
        branch,
        count()                                      AS order_count,
        sum(toFloat64(total_amount))                 AS lifetime_value,
        min(order_date)                              AS first_order_dt,
        max(order_date)                              AS last_order_dt,
        countIf(order_status = 'returned')           AS returned_orders,
        sumIf(toFloat64(total_amount),
              order_status = 'returned')             AS returned_value
    FROM rv.bv_order_canonical_mat
    WHERE customer_hk != toFixedString('', 16)
    GROUP BY customer_hk, branch
)
SELECT
    c.customer_hk                                    AS customer_hk,
    c.customer_bk                                    AS customer_bk,
    c.branch                                         AS branch,
    c.first_name                                     AS first_name,
    c.last_name                                      AS last_name,
    c.email                                          AS email,
    c.loyalty_segment                                AS loyalty_segment,
    c.loyalty_points                                 AS loyalty_points,
    c.last_visit_at                                  AS last_visit_at,
    c.pii_source                                     AS pii_source,
    c.loyalty_source                                 AS loyalty_source,
    coalesce(o.order_count, 0)                       AS order_count,
    coalesce(o.lifetime_value, 0.0)                  AS lifetime_value,
    o.first_order_dt                                 AS first_order_dt,
    o.last_order_dt                                  AS last_order_dt,
    coalesce(o.returned_orders, 0)                   AS returned_orders,
    coalesce(o.returned_value, 0.0)                  AS returned_value,
    if(coalesce(o.order_count, 0) > 0,
       toFloat64(o.returned_orders) / o.order_count,
       0.0)                                          AS return_rate
FROM customers c
LEFT JOIN order_agg o
  ON c.customer_hk = o.customer_hk
 AND c.branch = o.branch
SETTINGS max_threads = 2, max_memory_usage = 2500000000,
         max_bytes_before_external_group_by = 805306368,
         max_bytes_before_external_sort = 805306368,
         join_algorithm = 'grace_hash';
