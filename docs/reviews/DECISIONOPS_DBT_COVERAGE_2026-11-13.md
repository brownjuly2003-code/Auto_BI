# Покрытие dbt: V2-строки паритета — D4a (ADR 0012), 2026-11-13

Первая часть пункта D4 [ADR 0012](../adr/0012-post-finish-development.md) — «Derived- и
ratio-метрики MetricFlow и V2-строки паритета; мера — доля метрик публичных проектов F4 без
гэпов проекта»: V2-строки паритета. Derived-метрики — следующая часть, D4b. Исполнитель —
Claude; ревью — Opus-субагенты (модель, не human acceptance). Предыдущий срез публичных проектов —
[отчёт F4](DECISIONOPS_DBT_PUBLIC_2026-11-10.md).

## Прогон паритета V2

`scripts/metricflow_parity.py` держит рядом с legacy-проектом второй dbt-проект — те же метрики
в синтаксисе V2 (`agg` и `expr` у метрики модели) — и спрашивает обе стороны в обоих контекстах,
`postgres.date` и `clickhouse.date`. Время V2-проекта написано `dimension: {type: time}` с
`granularity: day`: в этом написании dbt-core 1.12.6 пишет гранулярность измерения, а в
написании `dimension: time` — нет, и V2-метрика над ним остаётся review (`time.no-granularity`).

Прогон на Mac (PostgreSQL 16.15, ClickHouse 24.8.14.39, metricflow 0.213.0, dbt-core 1.12.6): в
каждом контексте 1647 метрик — 823 legacy и 824 V2, — 1231 pass, 416 no_reference_sql, 0 fail;
девять контрольных пар, две из них V2, разошлись числом в обоих контекстах. `PROVEN_PARITY` —
654 строки, по 327 в контексте: 164 legacy и 163 V2 — каждая агрегация и класс (18), V2-метрика
без `expr` (`v2.metric_without_expr`) и отношение двух V2-термов (144). Fixtures
`tests/fixtures/dbt_semantic_manifest/parity` — результат и артефакт каждого проекта — заменены
записью этого прогона.

- **V2-метрика без `expr`.** MetricFlow читает колонку по имени метрики; в прогоне таких три —
  `sum`, `count_distinct` и `max` над колонкой своего имени, — и строка `v2.metric_without_expr`
  стоит рядом со строкой агрегации и класса, как `legacy.measure_without_expr` у legacy-меры.
- **`count` без `expr`** в прогон не входит: MetricFlow 0.213.0 такой count не строит и просит
  `expr: 1`.
- **`fill_nulls_with`.** V2-метрика `v2_amount_sum_filled` заполняет сумму нулём; dbt-core 1.12.6
  пишет `fill_nulls_with: null`, и MetricFlow и reference SQL дают ей значения той же суммы без
  заполнения — NULL в группе, где колонка целиком NULL. Заполнение потеряно на обеих сторонах
  одинаково (инвентарь, «What the artifact does not show»).
- **Отношение legacy- и V2-терма** не доказано: каждый проект прогона — один диалект.

## Что изменилось в резолвере и инвентаре

- Формула V2-метрики на доказанной строке разрешается, и узел несёт типизированную формулу, где
  verifier 136 оставлял `external_ref` с `gap.external_parity_unproven` (review). Строка
  инвентаря `parity.v2` снята; отношение двух диалектов — новая строка `parity.ratio-dialects`
  (review).
- V2-метрика без `expr` читает колонкой своё имя. Строку `v2.no-expr` заменила `v2.metric-name`:
  имя, которое не одна колонка в нижнем регистре. dbt имён метрик не резервирует — `null` и
  `true` он принимает, — а имя с заглавной (`Gross_Margin`) отказывает.
- `count` без `expr` — конструкция в обоих диалектах, `legacy.count-no-expr` и
  `v2.count-no-expr` (review). Проба на стенде: legacy-меру dbt-core 1.12.6 отказывает
  валидацией; V2-метрику записывает в артефакт и падает в `dbt parse`, переводя его в документ
  OSI. Verifier 136 разрешал такую legacy-меру как count по её имени — формулу, которой
  MetricFlow не строит.
- Корпус S5b перепрогнан: 107 записей, 77 приняты, 30 отказаны dbt; классы инвентаря — Written
  63, Refused 12, Not carried 2, No entry 14. Выдержка отказа dbt кончается перед первым
  traceback Python после причины: traceback называет пути прогона.
- Verifier 136 → 137, check остаётся 86 ([версии](../specs/evidence-format-versions.md)): тот же
  вход даёт другой снимок и вердикт, а ключ идемпотентности держит только версию verifier.

## Записи публичных проектов

Запись F4 — ответ резолвера на дату прогона; после правки резолвера её надо перерешить. dbt на
публичных проектах заново не запускался: новая команда `scripts/dbt_corpus.py again --record …
--artifact …` читает артефакт, сохранённый на стенде с F4, сверяет его дайджест с записанным и
заменяет в записи только проблемы ридера и ответы резолвера. Изменилась одна запись,
`jaffle_shop`: у шести V2-метрик без `expr` снят гэп `v2.no-expr`. У записи
`jaffle_sl_template` (отказ dbt) артефакта нет.

## Итог

| Запись | Метрик | Без гэпов проекта | Строки инвентаря (метрик с таким гэпом) |
|---|---|---|---|
| `jaffle_shop` | 23 | 9 | `metric.filter` 4; `type.cumulative` 1; `type.derived` 3; `v2.agg.median` 1; `v2.expr` 9 |
| `jaffle_sl_template_unnested` | 18 | 0 | `legacy.agg.percentile` 1; `legacy.agg_params.percentile / legacy.agg_params.use_approximate_percentile / legacy.agg_params.use_discrete_percentile` 1; `legacy.expr` 6; `measure-input.fill_nulls_with` 3; `measure-input.join_to_timespine` 1; `metric.filter` 3; `time.expression / time.name` 13; `type.cumulative` 2; `type.derived` 3 |
| `metricflow_tutorial` | 13 | 1 | `legacy.agg.sum_boolean` 1; `legacy.expr` 8; `metric.filter` 2; `type.cumulative` 2; `type.derived` 1 |

Строку называет причина резолвера; через « / » — строки, которые различает только значение в
артефакте, как в отчёте F4. Таблицу держит `tests/test_dbt_public.py`.

- Из 54 метрик гэпов проекта нет у десяти: `average_tax_rate`, `count_lifetime_orders`,
  `customers`, `lifetime_spend`, `lifetime_spend_pretax`, `order_cost`, `order_total`,
  `revenue`, `tax_paid` (jaffle-shop) и `transaction_amount` (tutorial). Мера D4 — 10 из 54;
  в F4 — 4 из 54. Шесть новых — V2-метрики jaffle-shop без `expr`.
- С контрактом на своей таблице разрешилась бы каждая из десяти; в F4 — одна,
  `transaction_amount`. Контракт — на таблице семантической модели метрики (PostgreSQL),
  событийное время — её измерение времени, колонка метрики объявлена `numeric(18,2)`. Метрики
  встают на строки `postgres.date:v2.sum.decimal`, `postgres.date:v2.metric_without_expr`,
  `postgres.date:v2.average.decimal`, `postgres.date:v2.count_distinct.decimal`,
  `postgres.date:legacy.sum.decimal` и `postgres.date:legacy.measure_without_expr`, и все они
  доказаны. Это проверено на артефактах стенда, не тестом; тест держит, что строки доказаны.
- Остальные 44 останавливает гэп проекта, как в F4: выражение меры или V2-метрики, которое не
  одна колонка (`legacy.expr` 14, `v2.expr` 9), время из выражения (13), фильтр метрики (9),
  derived (7), cumulative (5), `fill_nulls_with` (3), `median`, `percentile`, `sum_boolean`,
  `join_to_timespine`. Каждый гэп — строка инвентаря, review.

## Границы

- Паритет V2 — на рецепте прогона: те же классы, контексты и данные, что у legacy. Отношение
  legacy- и V2-терма не доказано.
- Записи перерешены по артефактам F4: те же ревизии проектов и те же версии dbt. Изменения
  проектов после ревизий F4 не прочитаны.
- Проверка «на своей таблице» — на объявленных типах (`numeric(18,2)`), не на таблицах
  проектов: базы у проектов нет.
- Прогон не строит: V2-метрику без `expr` с именем dimension или entity своей модели;
  `agg_time_dimension` метрики, отличный от умолчания модели (резолвер берёт время только
  у метрики, без него — review); ratio двух V2-термов без `expr` — его держат строки
  отношения и `v2.metric_without_expr` вместе, как у legacy.
- Derived-метрики — D4b.
