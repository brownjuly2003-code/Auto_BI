# Матрица паритета MetricFlow ↔ reference SQL (ADR 0008, S2) — 2026-11-04

Результат: **217 метрик — 165 pass, 52 no_reference_sql, 0 fail**; все шесть контрольных пар
разошлись. Доказаны 164 строки матрицы, все в контексте `postgres.date`. Это и есть
`PROVEN_PARITY` в `auto_bi/semantic/metricflow_formula.py`. Дизайн и правило сравнения —
[ADR 0008](../adr/0008-dbt-semantic-manifest-external-formulas.md), п. 7.

## Что сравнивалось

Скрипт `scripts/metricflow_parity.py` строит dbt-проект (legacy-диалект) над одной таблицей
`dm.sales` (`date`, `store_id`, `amount numeric(18,2)`, `qty bigint`, `price double precision`),
16 строк данных, и нативную модель этой таблицы (движок `postgres`, те же типы). Для каждой
метрики:

- reference-сторона — формула, которую `resolve_formula` разрешил из артефакта, в native
  metric contract (grain `dm.sales [date, store_id]`, событийное время `date`,
  `unit.precision` 6 у ratio и 2 у агрегатов), и SQL из `compile_metric_queries`;
- сторона MetricFlow — `MetricFlowEngine.explain` по тому же `semantic_manifest.json`
  с группировкой `metric_time__day`, `sale__store_id`
  (`scripts/metricflow_parity_sql.py`, клиент только рендерит PostgreSQL и ничего не
  выполняет);
- оба запроса выполняются на одном PostgreSQL и сравниваются по ключам grain.

До сравнения `run` сверяет с рецептом файлы проекта, колонки таблицы с их типами (по каталогу
PostgreSQL, `format_type` с модификатором) и мультимножество её строк; иное — отказ.

Строка матрицы — `<движок>.<событийное время>:<форма>`; здесь контекст один —
`postgres.date`. Форма называет класс типа колонки: `decimal` (`amount`), `integer` (`qty`),
`float` (`price`).

Метрики:

- шесть агрегатов на каждую из трёх колонок (`legacy.<agg>.<класс>`);
- measure без `expr` (`qty_bare`, `legacy.measure_without_expr`);
- `create_metric` с `expr` и без него (`amount_created`, `price`, `legacy.metric_expr`);
- ratio над каждой упорядоченной парой колонок, включая одну и ту же колонку
  (`ratio.legacy.<agg>.<класс>/legacy.<agg>.<класс>`). Терм над `amount` или `qty` — любой
  из шести агрегатов, терм над `price` — `count` или `count_distinct`. Итого 196 ratio.

Данные: nullable-колонка под `count`; группы, где колонка целиком NULL; нулевой и NULL
знаменатель; отрицательные значения; числитель `999999999999.99`; повторы под
`count_distinct`; дата 2026-01-03 без строк (time spine не участвует, оба запроса её не дают).
Сравнено 1485 значений.

## Правило

- Агрегат — точное равенство (`Decimal`, `NULL` только с `NULL`).
- Ratio — reference SQL даёт `TRUNC(num * 1.000000 / NULLIF(den, 0), 6)`, MetricFlow — double.
  Совпадение: double MetricFlow лежит, в пределах его ошибки (`|mf| · 1e-15 + 1e-300`), среди значений,
  которые при отсечении на 6 знаках дают значение reference, то есть в `[ref, ref + 1e-6)`
  (зеркально ниже нуля). Сравниваются не отсечённые значения: `0.9999999999999999` совпадает
  с `1.000000`.
- Паритет — равенство определений, не точность double. Пример из прогона:
  `r_amount_sum_by_qty_sum`, 2026-01-04, store 1: reference `142857142857.142857`,
  MetricFlow `142857142857.14285` — double теряет последние знаки, это совпадение.
- Строка доказана, если все метрики, построенные для неё, прошли, и каждая строка, на
  которую эти метрики ещё опираются, тоже доказана: строка, выпавшая из доказанных, роняет и
  строки, которые на неё опираются.

Контрольные пары (reference одной метрики против MetricFlow другой) обязаны разойтись, иначе
сравнение не отличает агрегат или форму от соседней: `amount_sum`/`amount_max` — 3
расхождения, `amount_min`/`amount_average` — 3, `qty_count`/`qty_count_distinct` — 2,
`price_sum`/`price_average` — 3, `r_amount_sum_by_qty_sum`/`r_amount_sum_by_qty_count` — 6,
`r_qty_max_by_amount_min`/`r_qty_min_by_amount_max` — 3. Скрипт завершается с exit 1, если
хоть одна пара совпала.

## Итог

Доказаны, в контексте `postgres.date`:

- 18 агрегатов: шесть агрегатов на `decimal`, `integer` и `float`;
- `legacy.measure_without_expr` и `legacy.metric_expr`;
- 144 формы ratio. Каждый терм — `sum`, `count`, `count_distinct`, `min` или `max` над
  `decimal` или `integer`, либо `count` или `count_distinct` над `float`: 12 × 12.

Не доказаны:

- 52 формы ratio с `average` (52 метрики): reference SQL даёт для них advisory
  `sql.ratio_inexact_term` без SQL, сравнивать не с чем;
- ratio с float-термом под `sum`, `min`, `max`: reference SQL отвечает тем же advisory, в
  матрицу они не входят;
- все строки V2 (`v2.<agg>.<класс>`, ratio V2): dbt-core 1.12.6 не пишет гранулярность time
  dimension V2, ни одна V2-метрика не разрешается (`gap.external_time_unproven`), в матрицу они
  не входят;
- всё вне контекста `postgres.date` (см. «Границы»).

Формула, которая опирается на недоказанную строку, получает review
`gap.external_parity_unproven`.

Первый прогон этой версии дал 14 метрик `unresolved`: у ratio одной метрики на саму себя
(`r_amount_sum_by_amount_sum` и т. п.) dbt пишет `input_measures` с одной мерой, а резолвер
ждал две. Резолвер исправлен: ожидаемый список — меры числителя, затем знаменателя, каждая
один раз (ADR 0008, п. 8). Результат выше — повторный прогон.

## Среда

- Host `imac-julia` (Intel Mac). PostgreSQL 16.15 (Homebrew,
  `x86_64-apple-darwin22.6.0`): одноразовый кластер `initdb` в `mktemp`, `LC_ALL=C`,
  только unix-сокет, порт 55439; удалён после прогона.
- dbt-core 1.12.6, dbt-postgres 1.11.0, metricflow 0.213.0 — отдельный venv (Python 3.13);
  MetricFlow читает артефакт своим `parse_manifest_from_dbt_generated_manifest`.
- Сторона репозитория — Python 3.13, `uv run`, дерево среза S2 до коммита.
- Артефакт `sha256:246dfedc0cd91df86a8691f1156336d705aaa271a52ad9c31cd4e05c93a1af53`;
  рецепт `sha256:a95e20619b238415835372c3905432a2e6456a95a469915a020abbf9d1ecee4c`.
- Повторный прогон после того, как `run` стал сверять колонки и их типы, дал тот же артефакт
  и тот же `result.json` байт в байт.

## Команды

```text
python scripts/metricflow_parity.py project OUT
(cd OUT && dbt parse --profiles-dir . --no-partial-parse)
psql -h SOCKET -p 55439 -U parity -d parity -X -q -v ON_ERROR_STOP=1 -f OUT/data.sql
python scripts/metricflow_parity.py run --project OUT --mf-python MF_PY --psql PSQL \
    --conninfo "host=SOCKET port=55439 user=parity dbname=parity" --out result.json
```

## Артефакты и проверка

`tests/fixtures/dbt_semantic_manifest/parity/semantic_manifest.json` и `result.json` — артефакт
и результат этого прогона. `tests/test_metricflow_formula.py` сверяет:

- digest артефакта с записанным;
- digest рецепта (файлы проекта и данные, grain обеих сторон, шаблоны контрактов,
  объявленная таблица, контрольные пары) с тем, что скрипт строит сейчас;
- `PROVEN_PARITY` с `proven` результата и пересчёт доказанных строк;
- записанные значения — по правилу; у каждой сравнённой метрики ключи — все группы данных;
  граница ошибки double — случаями по обе стороны от `|mf| · 1e-15`;
- что формула и строки каждой метрики — те, что `resolve_formula` даёт сейчас по контракту,
  который строит скрипт (`additive` — у `sum` и `count`, у остальных и у ratio —
  `non_additive`);
- что данные содержат названные выше случаи;
- что сторона MetricFlow (на заглушках модулей MetricFlow) читает артефакт парсером
  MetricFlow, планирует каждую метрику отдельно с группировкой grain в диалекте PostgreSQL,
  ничего не выполняет и отказывается от запроса с bind-параметрами;
- что `run` на заглушках dbt-окружения и psql, отвечающих записанными значениями, сравнивает
  reference SQL — тот, что компилятор даёт для записанной формулы, — с SQL MetricFlow (каждую
  сторону — по её ключам) и пишет записанный результат; что расходящееся значение MetricFlow
  даёт метрике `fail` и снимает доказанность её строки и строк, которые на неё опираются; что
  он завершается с exit 1, если контрольная пара совпала, и отказывается от файлов проекта,
  колонок, их типов или строк таблицы не из рецепта.

Сам прогон CI не повторяет: ему нужны dbt, MetricFlow и PostgreSQL.

## Границы

- Один движок — PostgreSQL 16.15, один контекст. Строки ClickHouse, Greenplum/Greengage и
  движка, который нативная модель не объявила, не доказаны. Не доказаны и строки, где
  событийное время — `timestamp`, другой тип или не ключ grain: группировки MetricFlow по дню
  и reference SQL по ключам там могут разойтись.
- Классы типов, а не сами типы: `numeric(18,2)` доказывает `decimal`, `bigint` — `integer`,
  `double precision` — `float`. Колонки `text` и `date` под агрегатом (`unclassified`, `date`)
  матрицей не проверены; массив или тип с другим суффиксом (`numeric(18,2)[]`) —
  `unclassified`.
- Только legacy-диалект и одна semantic model; ratio над двумя моделями или двумя временами
  резолвер не разрешает (`gap.external_ratio_unproven`).
- Новая версия metricflow или dbt, другой движок, класс или новый способ разрешения — новый
  прогон и новые строки.
