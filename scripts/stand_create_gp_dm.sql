-- Greenplum demo DM for the Phase 3 GP track (mirrors the ClickHouse demo star).
-- Star: fact dm.sales + dims dm.stores / dm.products. Exercises GP-specific physical
-- metadata the introspector and advisor reason about:
--   * DISTRIBUTED BY (distribution key) -> introspector reads gp_distribution_policy
--   * RANGE partitions by month        -> partition pruning advisor rule
--   * co-located join (sales x stores on store_id, both DISTRIBUTED BY store_id) vs
--     a motion join (sales x products: sales is dist by store_id, products by product_id)
-- Run: docker cp + psql -f as gpadmin against db `postgres`.

DROP SCHEMA IF EXISTS dm CASCADE;
CREATE SCHEMA dm;

CREATE TABLE dm.stores (
  store_id int NOT NULL,
  city     text,
  format   text,
  region   text
) DISTRIBUTED BY (store_id);
COMMENT ON TABLE dm.stores IS 'Магазины сети';
COMMENT ON COLUMN dm.stores.city IS 'Город магазина';

CREATE TABLE dm.products (
  product_id int NOT NULL,
  category   text,
  name       text
) DISTRIBUTED BY (product_id);
COMMENT ON TABLE dm.products IS 'Справочник товаров';

CREATE TABLE dm.sales (
  "date"     date NOT NULL,
  store_id   int  NOT NULL,
  product_id int  NOT NULL,
  revenue    numeric(12,2),
  qty        int,
  orders     int
)
DISTRIBUTED BY (store_id)
PARTITION BY RANGE ("date")
( START (date '2026-01-01') INCLUSIVE
  END   (date '2026-07-01') EXCLUSIVE
  EVERY (interval '1 month'),
  DEFAULT PARTITION other );
COMMENT ON TABLE dm.sales IS 'Продажи по дням, магазинам и товарам';
COMMENT ON COLUMN dm.sales.revenue IS 'Выручка, руб';

INSERT INTO dm.stores
SELECT g,
       'city_' || (g % 20),
       (ARRAY['hyper','super','convenience'])[1 + (g % 3)],
       'region_' || (g % 5)
FROM generate_series(1, 20) g;

INSERT INTO dm.products
SELECT g, 'cat_' || (g % 8), 'product_' || g
FROM generate_series(1, 50) g;

INSERT INTO dm.sales
SELECT date '2026-01-01' + ((random() * 180)::int),
       1 + (random() * 19)::int,
       1 + (random() * 49)::int,
       round((random() * 1000)::numeric, 2),
       (1 + random() * 10)::int,
       1
FROM generate_series(1, 300000);

ANALYZE dm.stores;
ANALYZE dm.products;
ANALYZE dm.sales;

-- read-only role for Auto_BI (mirror of CH auto_bi_ro)
DROP ROLE IF EXISTS auto_bi_ro;
CREATE ROLE auto_bi_ro LOGIN PASSWORD 'ro_pw';
GRANT USAGE ON SCHEMA dm TO auto_bi_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA dm TO auto_bi_ro;
