CREATE DATABASE dm;

CREATE TABLE dm.stores
(
    id UInt32
)
ENGINE = MergeTree
ORDER BY id;

INSERT INTO dm.stores VALUES (1);

CREATE TABLE dm.sales_daily
(
    date Date,
    store_id UInt32,
    region String,
    category String,
    revenue Decimal(18, 2),
    gross_margin Decimal(18, 2)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(date)
ORDER BY (date, store_id);

INSERT INTO dm.sales_daily
SELECT
    toDate('2026-09-12') - toIntervalDay(number),
    toUInt32(1),
    'msk',
    'all',
    toDecimal64(100000, 2),
    toDecimal64(45205.40, 2)
FROM numbers(91);
