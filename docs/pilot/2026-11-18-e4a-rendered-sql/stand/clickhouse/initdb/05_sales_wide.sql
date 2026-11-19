-- E4a stand only: sales_daily joined to stores and products, so one physical dataset has text
-- dimensions, a date and measures for one chart per viz.
CREATE TABLE dm.sales_wide
(
    date Date, region LowCardinality(String), city LowCardinality(String),
    format LowCardinality(String), category LowCardinality(String), brand LowCardinality(String),
    store_id UInt32, revenue Decimal(18, 2), orders UInt32, items UInt32
)
ENGINE = MergeTree ORDER BY (date, region);

INSERT INTO dm.sales_wide
SELECT s.date, st.region, st.city, st.format, p.category, p.brand, s.store_id, s.revenue, s.orders, s.items
FROM dm.sales_daily AS s
INNER JOIN dm.stores AS st ON st.id = s.store_id
INNER JOIN dm.products AS p ON p.id = s.product_id;
