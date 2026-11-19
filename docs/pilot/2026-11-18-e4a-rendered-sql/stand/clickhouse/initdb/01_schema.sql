-- Demo-DM: star sales/stores/products (ARCHITECTURE §3.2, D7).
-- Column COMMENTs are first-class: the introspector turns them into model.yaml descriptions.

CREATE DATABASE IF NOT EXISTS dm;

CREATE TABLE dm.stores
(
    id          UInt32 COMMENT 'ID магазина',
    name        String COMMENT 'Название магазина',
    city        LowCardinality(String) COMMENT 'Город',
    region      LowCardinality(String) COMMENT 'Регион (федеральный округ)',
    format      LowCardinality(String) COMMENT 'Формат: гипермаркет / супермаркет / магазин у дома',
    opened_date Date COMMENT 'Дата открытия'
)
ENGINE = MergeTree
ORDER BY id
COMMENT 'Справочник магазинов';

CREATE TABLE dm.products
(
    id       UInt32 COMMENT 'ID товара',
    name     String COMMENT 'Название товара',
    category LowCardinality(String) COMMENT 'Категория товара',
    brand    LowCardinality(String) COMMENT 'Бренд',
    price    Decimal(18, 2) COMMENT 'Базовая цена, руб'
)
ENGINE = MergeTree
ORDER BY id
COMMENT 'Справочник товаров';

CREATE TABLE dm.sales_daily
(
    date       Date COMMENT 'День продажи',
    store_id   UInt32 COMMENT 'ID магазина (dm.stores.id)',
    product_id UInt32 COMMENT 'ID товара (dm.products.id)',
    manager_id UInt32 COMMENT 'ID менеджера смены (высокая кардинальность, НЕ в ключе сортировки — анти-паттерн-кейс для advisor)',
    revenue    Decimal(18, 2) COMMENT 'Выручка, руб',
    orders     UInt32 COMMENT 'Число заказов',
    items      UInt32 COMMENT 'Число позиций'
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(date)
ORDER BY (date, store_id, product_id)
COMMENT 'Дневные продажи по магазинам и товарам (грейн: date, store_id, product_id)';
