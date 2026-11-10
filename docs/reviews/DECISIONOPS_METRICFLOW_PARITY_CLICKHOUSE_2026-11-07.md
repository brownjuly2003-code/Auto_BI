# Матрица паритета MetricFlow ↔ reference SQL на PostgreSQL и ClickHouse (ADR 0008, S5c) — 2026-11-07

Результат: **в каждом из двух контекстов 823 метрики — 615 pass, 208 no_reference_sql, 0 fail**;
все семь контрольных пар в обоих контекстах разошлись числом. Доказаны 328 строк матрицы: 164 в
контексте `postgres.date` и 164 в `clickhouse.date`. Это и есть `PROVEN_PARITY` в
`auto_bi/semantic/metricflow_formula.py`. Дизайн, классы типов ClickHouse и правило сравнения —
[ADR 0008](../adr/0008-dbt-semantic-manifest-external-formulas.md), п. 7. Прежний прогон
(только PostgreSQL, рецепт S2) — [отчёт 2026-11-04](DECISIONOPS_METRICFLOW_PARITY_CHECKS_2026-11-04.md).

## Что сравнивалось

Скрипт `scripts/metricflow_parity.py` строит dbt-проект (legacy-диалект) над одной таблицей
`dm.sales`: ключи grain `date`, `store_id`; nullable-колонки `amount` (класс `decimal`), `qty`
(`integer`), `price` (`float`) и их NOT NULL-двойники `amount_nn`, `qty_nn`, `price_nn` — те же
значения, ноль там, где колонка NULL. 19 строк данных. Нативных моделей две, по одной на
контекст, с типами, как их пишет движок:

| Колонка | `postgres.date` | `clickhouse.date` |
|---|---|---|
| `date` | `date` | `Date` |
| `store_id` | `integer` | `Int32` |
| `amount` / `amount_nn` | `numeric(18,2)` | `Nullable(Decimal(18, 2))` / `Decimal(18, 2)` |
| `qty` / `qty_nn` | `bigint` | `Nullable(Int64)` / `Int64` |
| `price` / `price_nn` | `double precision` | `Nullable(Float64)` / `Float64` |

Для каждой метрики и каждого контекста:

- reference-сторона — формула, которую `resolve_formula` разрешил из артефакта для таблицы,
  объявленной в нативной модели контекста, в native metric contract (grain
  `dm.sales [date, store_id]`, событийное время `date`, `unit.precision` 6 у ratio и 2 у
  агрегатов), и SQL из `compile_metric_queries` в диалекте движка контекста. Выполняется на
  своём движке: PostgreSQL — через psql, ClickHouse — через `clickhouse local`, который на
  каждый запрос заново загружает данные рецепта (`data.clickhouse.sql`) и ничего не хранит;
- сторона MetricFlow — `MetricFlowEngine.explain` по тому же `semantic_manifest.json` с
  группировкой `metric_time__day`, `sale__store_id` (`scripts/metricflow_parity_sql.py`).
  metricflow 0.213.0 ClickHouse не рендерит, поэтому сторона MetricFlow в обоих контекстах
  одна: его SQL для PostgreSQL, выполненный на PostgreSQL один раз на метрику.

Так проверяется перевод определения на общих данных, а не рендерер MetricFlow для ClickHouse,
которого нет.

До сравнения `run` сверяет с рецептом файлы проекта, настройки `clickhouse local`, колонки
таблицы с типами (PostgreSQL — `format_type` с модификатором и NOT NULL, ClickHouse —
`system.columns`) и мультимножество строк на обоих движках; иное — отказ. Настройки ClickHouse
прогон задаёт флагами, проверяет по `system.settings` и записывает:
`count_distinct_implementation = uniqExact`, `aggregate_functions_null_for_empty = 0`,
`decimal_check_overflow = 1`, `cast_keep_nullable = 0` (значения 24.8 по умолчанию) и
`output_format_json_quote_64bit_integers = 0` (64-битные целые — числами JSON). Ещё прогон
записывает типы `sum`, `min`, `max`, `avg` над каждым членом классов ClickHouse
(`CLICKHOUSE_CLASSES`: `Int8` … `Int64`, `Decimal(P, 2)` при P 2…38, `Float64`) и над
`UInt8` … `UInt64`.

Метрики — те же формы, что в S2, над шестью колонками:

- шесть агрегатов на каждую колонку (36);
- measure без `expr` (`qty_bare`), `create_metric` с `expr` и без (`amount_created`, `price`);
- ratio над каждой упорядоченной парой из шести колонок, включая одну и ту же колонку, одной
  nullability и разной. Терм над `decimal`- или `integer`-колонкой — любой из шести
  агрегатов, над `float` — `count` или `count_distinct`: 28 термов, 784 ratio.

Данные: всё из S2 (nullable-колонка под `count`, группы, где колонка целиком NULL, нулевой и
NULL знаменатель, отрицательные значения, числитель `999999999999.99`, повторы под
`count_distinct`, дата 2026-01-03 без строк) и группа 2026-01-06, store 1, где среднее в
double не равно точному у `amount` (133333.336666…) и у `qty` (33333333333333333.666…).
Сравнено по 6150 значений в каждом контексте.

## Правило

- Агрегат — точное равенство (`Decimal`, `NULL` только с `NULL`).
- `average` в контексте `clickhouse.date` — правило `double`: |reference − MetricFlow| ≤
  |reference| · 1e-15 + 1e-300. `avg` ClickHouse возвращает `Float64` над любой колонкой, а
  MetricFlow на PostgreSQL — `avg` PostgreSQL: над decimal- и integer-колонкой `numeric` в
  масштабе, который выбирает PostgreSQL (у `qty` на 2026-01-06 — `33333333333333334`, не точное
  `33333333333333333.666…`), над float-колонкой — double. Допуск — правило этого рецепта на его
  данных, где суммы точны, а не оценка ошибки `avg` на любых данных. В прогоне оно нужно у
  `amount`, `qty` и их двойников на 2026-01-06, store 1: ClickHouse `133333.33666666667`
  против `133333.336666666667`, `33333333333333332` против `33333333333333334`. Среднее
  `price` совпало точно.
- Ratio — как в S2: reference отсекает частное на 6 знаках (`divideDecimal(…, 6)` на
  ClickHouse, `TRUNC(…, 6)` на PostgreSQL), MetricFlow даёт double; совпадение — double лежит,
  в пределах своей ошибки (`|mf| · 1e-15 + 1e-300`), среди значений, которые при отсечении дают
  значение reference (зеркально ниже нуля). Пример: `r_amount_sum_by_qty_sum`, 2026-01-04,
  store 1 — reference `142857142857.142857`, MetricFlow `142857142857.14285`.
- Строка доказана, если все метрики, построенные для неё, прошли, и каждая строка, на которую
  они ещё опираются, тоже доказана.

Контрольные пары (reference одной метрики против MetricFlow другой) обязаны разойтись числом
— хотя бы на одном ключе, где значение есть у обеих сторон; иначе exit 1. В обоих контекстах
одинаково (расхождений / из них числом): `amount_sum`/`amount_max` — 4/4,
`amount_min`/`amount_average` — 4/4, `qty_count`/`qty_count_distinct` — 3/3,
`price_sum`/`price_average` — 4/4, `r_amount_sum_by_qty_sum`/`r_amount_sum_by_qty_count` —
7/5, `r_qty_max_by_amount_min`/`r_qty_min_by_amount_max` — 4/3,
`amount_average`/`amount_nn_average` — 3/1. Последняя пара показывает, что правило `double` не
прячет NULL, прочитанный как ноль: 2026-01-01, store 1 — 13.5 против 10.125.

## Итог

Доказаны, в каждом из контекстов `postgres.date` и `clickhouse.date`:

- 18 агрегатов: шесть агрегатов на `decimal`, `integer` и `float`;
- `legacy.measure_without_expr` и `legacy.metric_expr`;
- 144 формы ratio: каждый терм — `sum`, `count`, `count_distinct`, `min` или `max` над
  `decimal` или `integer`, либо `count` или `count_distinct` над `float`, 12 × 12.

Строки — по классам, поэтому nullable-колонка и её двойник доказывают одну строку вместе: на
ClickHouse reference SQL ratio приводит целый числитель к `Decimal(38, 0)` или
`Nullable(Decimal(38, 0))` по тому, может ли терм быть NULL, а класс этого не называет.
Поэтому каждая доказанная строка ratio держится метриками всех четырёх сочетаний
nullability своих термов: оба nullable, оба NOT NULL и смешанные пары в обе стороны.

Не доказаны:

- 52 формы ratio с `average` в каждом контексте (208 метрик): reference SQL даёт для них
  advisory `sql.ratio_inexact_term` без SQL;
- ratio с float-термом под `sum`, `min`, `max` — то же advisory, в матрицу не входят;
- строки V2: dbt-core 1.12.6 не пишет гранулярность time dimension V2, ни одна V2-метрика не
  разрешается;
- всё вне двух контекстов (см. «Границы»).

Формула, которая опирается на недоказанную строку, получает review
`gap.external_parity_unproven`.

## Среда

- Host: iMac (Intel).
- PostgreSQL 16.15 (Homebrew, `x86_64-apple-darwin22.6.0`): одноразовый кластер `initdb` в
  `mktemp`, `LC_ALL=C`, только unix-сокет, порт 55439; удалён после прогона.
- ClickHouse 24.8.14.39: одиночный бинарник, режим `clickhouse local` (без сервера), таблица
  `ENGINE = Memory`, данные загружаются в каждом запросе.
- dbt-core 1.12.6, dbt-postgres 1.11.0, metricflow 0.213.0 — отдельный venv; MetricFlow читает
  артефакт своим `parse_manifest_from_dbt_generated_manifest`.
- Сторона репозитория — Python 3.13, `uv run`, дерево среза S5d до коммита.
- Артефакт `sha256:8300f9cb2c519dcf97b63b730af94c27068f97357de65c12b0069fdea6b491f5`;
  рецепт `sha256:2d7f7eb1b2d1b3a6f5debacd008e21ea55909a26402fcb51ec3cf7fd8d51f35d`.
- Прогон сделан трижды. Второй — после ревью кода S5c, с digest reference SQL каждой
  метрики и допусками и мостом в рецепте; артефакт совпал с первым байт в байт. Третий — в
  S5d, со смешанными по nullability парами ratio (до него — 431 метрика на контекст, только
  пары одной nullability; артефакт `sha256:9f7ca589…`, рецепт `sha256:cf3e6335…`): каждая из
  431 прежних метрик записана с тем же исходом, формулой, строками и значениями, доказанные
  строки — те же 328; fixtures — третий прогон.

## Команды

```text
python scripts/metricflow_parity.py project OUT
(cd OUT && dbt parse --profiles-dir . --no-partial-parse)
psql -h SOCKET -p 55439 -U parity -d parity -X -q -v ON_ERROR_STOP=1 -f OUT/data.postgres.sql
python scripts/metricflow_parity.py run --project OUT --mf-python MF_PY --psql PSQL \
    --conninfo "host=SOCKET port=55439 user=parity dbname=parity" \
    --clickhouse CLICKHOUSE --out result.json
```

## Артефакты и проверка

`tests/fixtures/dbt_semantic_manifest/parity/semantic_manifest.json` и `result.json` — артефакт
и результат этого прогона. `tests/test_metricflow_formula.py` сверяет:

- digest артефакта; версии PostgreSQL, ClickHouse и metricflow; записанные настройки
  ClickHouse с рецептом; `PROVEN_PARITY` с `proven` результата и число доказанных строк по
  контекстам (164 и 164);
- digest рецепта (файлы проекта, данные, нативные модели обоих контекстов, grain обеих сторон,
  шаблоны контрактов, правила и их допуски, мост к MetricFlow
  `scripts/metricflow_parity_sql.py`, настройки и пробы типов ClickHouse, контрольные пары) с
  тем, что скрипт строит сейчас; контрольные пары каждого контекста — с рецептом;
- digest reference SQL, который выполнила каждая метрика в каждом контексте, — с SQL, который
  компилятор даёт для записанной формулы сейчас: компилятор, пишущий другой SQL, требует нового
  прогона;
- в каждом контексте: пересчёт доказанных строк, правило каждой метрики, записанные значения —
  по её правилу; контрольные пары — пересчётом из записанных значений (расхождения и
  расхождения числом, больше нуля); ключи каждой сравнённой метрики — все группы данных;
  что каждая доказанная строка ratio держится прошедшими метриками всех четырёх сочетаний
  nullability своих термов, а пары колонок рецепта — все упорядоченные пары шести колонок;
- что значения MetricFlow одной метрики одинаковы в обоих контекстах; что правило `double` —
  ровно у `average` в `clickhouse.date` и что у `amount`, `qty` и двойников есть ключ, где оно
  нужно (точное равенство не проходит); граница правил — случаями по обе стороны допуска,
  у `double` — от значения reference, а не MetricFlow;
- записанные типы: у каждого члена класса `sum` и `avg` — типы проверенного типа его класса,
  `min` и `max` — сам тип; `sum` над `UInt8` … `UInt64` — `UInt64`, и они вне классов; класс
  каждого написания типа ClickHouse (члены, `Nullable` члена, и что прочее — `unclassified`,
  а `Nullable(Date)` событийного времени — тоже); что reference SQL читает член и его
  `Nullable` как точный терм того вида, что называет класс;
- что формула и строки каждой метрики в каждом контексте — те, что `resolve_formula` даёт
  сейчас по контракту, который строит скрипт, и таблице, объявленной для контекста;
- что данные содержат названные выше случаи, двойники — значения колонки и ноль вместо NULL,
  нет −0, NaN и бесконечностей, целые суммы помещаются в `Int64`, decimal — в
  `Decimal(38, 2)`, float-суммы точны при любом порядке сложения;
- что `run` на заглушках dbt-окружения, psql и `clickhouse local`, отвечающих записанными
  значениями и проверяющих полную командную строку вызова, сравнивает reference SQL каждого
  контекста — тот, что компилятор даёт для записанной формулы из нативной модели контекста, —
  с SQL MetricFlow, запрашивая каждую метрику MetricFlow один раз, и пишет записанный
  результат; что расходящееся значение MetricFlow даёт метрике `fail` в обоих контекстах и
  снимает доказанность её строк и строк, которые на них опираются; что он завершается с exit
  1, если контрольная пара не разошлась числом (в том числе когда расходится только NULL
  против нуля); что он отказывается от файлов проекта, настроек ClickHouse, колонок, их типов,
  NOT NULL или строк таблицы на любом движке не из рецепта.

Сам прогон CI не повторяет: ему нужны dbt, MetricFlow, PostgreSQL и ClickHouse.

## Границы

- Два движка и по одному контексту на каждом: событийное время — ключ grain типа `date`
  (`Date` без `Nullable` на ClickHouse). Greenplum/Greengage, движок, который нативная модель не
  объявила, событийное время `timestamp`, `Date32`, `DateTime`, `Nullable(Date)` или не ключ
  grain — не доказаны.
- Классы, а не сами типы: на ClickHouse проверены `Decimal(18, 2)`, `Int64`, `Float64` (и их
  `Nullable`); остальные члены классов — по записанным типам их агрегатов, которые совпадают с
  типами проверенного. Прочие типы и написания — `unclassified` (ADR 0008, п. 7).
- Значения, которые два движка при одном определении считают по-разному, данными не
  проверены: `count(DISTINCT)` над `Float64` различает −0.0 и 0.0, NaN и бесконечности;
  переполнение типов ClickHouse (`sum` над целыми — в `Int64`, над `Decimal(P, 2)` — в
  `Decimal(38, 2)`, приведение к `Decimal(38, 0)`, частное `divideDecimal`), где PostgreSQL
  считает в `numeric`.
- Доказательство — для настроек ClickHouse, которые прогон задаёт; сервер с другими — вне его.
- Только legacy-диалект и одна semantic model.
- Новая версия metricflow, dbt или движка, другой движок, класс или способ разрешения — новый
  прогон и новые строки.
