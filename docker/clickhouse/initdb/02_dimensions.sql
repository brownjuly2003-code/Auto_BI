-- Synthetic dimensions: ~4200 stores (cardinality from ARCHITECTURE §3.2 example), 2000 products.

INSERT INTO dm.stores
SELECT
    number + 1 AS id,
    concat('Магазин №', toString(number + 1)) AS name,
    ['Москва', 'Санкт-Петербург', 'Новосибирск', 'Екатеринбург', 'Казань', 'Нижний Новгород',
     'Челябинск', 'Самара', 'Омск', 'Ростов-на-Дону', 'Уфа', 'Красноярск', 'Воронеж', 'Пермь',
     'Волгоград', 'Краснодар', 'Саратов', 'Тюмень', 'Тольятти', 'Ижевск']
        [cityHash64(number, 1) % 20 + 1] AS city,
    ['ЦФО', 'СЗФО', 'СФО', 'УФО', 'ПФО', 'ЮФО', 'ДФО', 'СКФО']
        [cityHash64(number, 2) % 8 + 1] AS region,
    ['магазин у дома', 'магазин у дома', 'супермаркет', 'супермаркет', 'гипермаркет']
        [cityHash64(number, 3) % 5 + 1] AS format,
    toDate('2010-01-01') + toIntervalDay(cityHash64(number, 4) % 5000) AS opened_date
FROM numbers(4200);

INSERT INTO dm.products
SELECT
    number + 1 AS id,
    concat('Товар ', toString(number + 1)) AS name,
    ['Молочные продукты', 'Хлеб и выпечка', 'Мясо и птица', 'Рыба', 'Овощи и фрукты',
     'Бакалея', 'Напитки', 'Кондитерские изделия', 'Заморозка', 'Бытовая химия',
     'Детские товары', 'Алкоголь']
        [cityHash64(number, 1) % 12 + 1] AS category,
    concat('Бренд ', toString(cityHash64(number, 2) % 40 + 1)) AS brand,
    toDecimal64(30 + (cityHash64(number, 3) % 97000) / 100, 2) AS price
FROM numbers(2000);
