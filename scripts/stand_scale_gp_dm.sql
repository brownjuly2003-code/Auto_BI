-- Scale the GP demo fact dm.sales past the 10M advisor threshold (Phase 3.3/3.4).
--
-- The 300k demo from stand_create_gp_dm.sql can't trigger the at-scale rules:
--   * no_filter_on_large_fact (CRITICAL)  -- fires only when physical.rows >= 10M
--   * distribution_skew       (WARN, DCR) -- fires when rows >= 10M AND the
--                                             distribution key cardinality < 1000
-- dm.sales is DISTRIBUTED BY (store_id) with 20 distinct stores -> the skew rule
-- fires by design once the fact crosses 10M (a deliberately low-cardinality key).
--
-- Prerequisite: stand_create_gp_dm.sql has built dm.* (300k fact). This tops it up
-- to ~10.1M so the introspector reads reltuples >= 10M after ANALYZE.
-- Run as gpadmin: docker cp + psql -f, OR batched docker exec for memory headroom
-- on the 8GB Mac (see runbook "Воспроизвести live-валидацию на скейле").
--
-- Dates stay inside the Jan-Jun 2026 monthly RANGE partitions (no DEFAULT spillover).

INSERT INTO dm.sales
SELECT date '2026-01-01' + ((random() * 180)::int),
       1 + (random() * 19)::int,
       1 + (random() * 49)::int,
       round((random() * 1000)::numeric, 2),
       (1 + random() * 10)::int,
       1
FROM generate_series(1, 9800000);

ANALYZE dm.sales;
