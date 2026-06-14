# S6-ревью: адекватность дашборда (B1 + B2)

Дата: 2026-06-15. Ревьюер: субагент `code-reviewer` (Opus high).
Диапазон: `cd947c2..e1c0504` (ветка `main`). Содержательные код-коммиты — `c7e7833` (B1), `f13586c` (B2).
План: `docs/plans/2026-06-14-dashboard-adequacy-fixes.md`. B4 закрыт без правок кода (resolved-by-verification).

## Краткое резюме

Обе правки соответствуют плану и заявленному поведению, реализованы чисто и детерминированно,
не трогают промпты и не меняют IR-схему/интерфейс `BIAdapter`. Инварианты 1–8 соблюдены.

- **B1** (`auto_bi/agent/normalize.py` + вызов в `compile_and_build`): чистый идемпотентный
  IR-трансформ, который мирорит резолюцию order-target из `sqlgen.py` **точно** (column / alias /
  label), корректно скипует time-bar и явный авторский top-N, не порождает невалидный spec.
- **B2** (`auto_bi/adapters/datalens/chart_config.py`): string-cast числового DIMENSION-поля только
  в chart-placeholder column-чарта; датасет и селекторы не задеты (новый dict на каждый item, без
  мутации общего field-dict); `data_type`/`initial_data_type`/`cast` выставляются консистентно.

Прогон: **pytest 346 passed / 32 deselected**, **ruff All checks passed!** (оба запущены ревьюером).

Вердикт: **B1 и B2 принимаются как есть.** Блокеров (P1) и обязательных к доработке до мержа (P2)
нет. Семь P3 — покрытие тестами и одна предсуществующая Superset-узость (не введена этой сессией).

## Проверка инвариантов дизайна 1–8

1. **IR-first** — ✅. B1 трансформирует `DashboardSpec` (pydantic IR) через `model_copy`, нативные
   форматы не трогает. B2 — детерминированный код адаптера (placeholder-каст), не LLM.
2. **Валидация ДО BI** — ✅. `apply_chart_defaults` вызван в `compile_and_build` строго ПЕРЕД
   `validate_spec` (pipeline.py:82 → :85). Нормализованный order_by всегда ∈ `orderable`
   (`validate.py:149-153` добавляет column/alias/label), `limit∈[1,cap]⊂[1,50000]` — нормализация
   не может породить spec, который провалит validate. Defense-in-depth validate не ослаблен.
3. **SQL SELECT-only + guard** — ✅. SQL_GEN/sql_guard не тронуты; B1 лишь дополняет order_by/limit,
   которые SQL_GEN и так умеет рендерить (тест `test_normalized_query_emits_order_by_and_limit`).
4. **Уточнения из grounding ≤3** — ✅ (не задето).
5. **Feasibility Advisor** — ✅ (не задето). Замечание: advisor (`machine._propose_turn`) ревьюит
   **до-нормализационный** `self.spec`, т.е. `group_by_high_cardinality` всё ещё может предупреждать
   о «стене», которую B1 на build-time погасит. Advisory-only, не блокер (см. P3-1).
6. **Fields-first** — ✅. B1 действует на IR одинаково для текстового и fields-входа (нормализация
   в общем `compile_and_build`).
7. **Версии BI запинены** — ✅. B2 не меняет контрактные маркеры (gateway/schemeVersion/shared
   version "4"); shape-тест `test_shared_full_shape_pins_service_blocks` зелёный.
8. **Промпты только с eval** — ✅. Ни один промпт не тронут (B1/B2 — детерминированный код).
   S2-стоппер соблюдён.

## Сверка с заявленным поведением

### B1 — default top-N
- Скоуп viz {BAR, STACKED_BAR, PIE} — ✅ (`_CATEGORICAL_VIZ`). Заявленный планом `column` — это
  Superset/DataLens-маппинг BAR, в IR такого viz нет; набор корректен.
- Time-bar скип — ✅. `_is_time_dimension` резолвит роль первичной оси против модели; `date`
  (TIME) → skip. Подтверждено `test_time_dimension_bar_is_untouched`.
- Явный авторский top-N не трогается — ✅. `_orders_by_measure` **точно** мирорит
  `sqlgen.py:126-132` (column + `measure_alias` + label по всем order_by). Это и есть верная
  референс-точка (B1 кормит SQL_GEN). Подтверждено `test_explicit_measure_topn_is_untouched`,
  `test_order_by_raw_measure_column_counts_as_topn`.
- order_by по самому измерению → заменяется на меру-desc — ✅
  (`test_dimension_only_order_is_replaced_by_measure_desc`): order_by по `store_id` это всё ещё
  стена, B1 верно перетирает на `sum_revenue desc`.
- `limit=min(limit,cap)` только ужимает — ✅ (`test_small_explicit_limit_is_not_widened`: 10 не
  расширяется до 25). pie cap=12, иначе 25 — ✅.
- Идемпотентность — ✅ (`test_idempotent`; после 1-го прохода order_by по `measure_alias` ∈
  measure_refs → short-circuit). Верно и для measures с label.
- Stored/preview spec не меняется — ✅. `save_spec` в `build_dashboard`/`machine._propose_turn`
  происходит ДО `compile_and_build`; нормализация — build-time, как и задумано планом.
- Защитность `_is_time_dimension` — ✅. Неизвестная таблица/колонка → False (non-time) → spec
  пойдёт в `validate_spec` на reject, а не упадёт здесь.
- `measures` всегда ≥1 (`Field(min_length=1)`) → `measures[0]` безопасен.

### B2 — категориальная ось числового измерения в DataLens
- Скоуп column-only — ✅. `discrete = chart.viz in (BAR, STACKED_BAR)` (chart_config.py:159);
  line/area оставлены непрерывными (`test_shared_line_numeric_dimension_x_not_cast`).
- `_is_numeric_dimension` — ✅. `type=="DIMENSION" and data_type∈{integer,float}`. Домен
  `data_type` приходит из `_user_type` (dataset.py:98-119), который сводит всё к 6 значениям;
  `{integer,float}` точно покрывает числовую семью, исключая date/genericdatetime/boolean/string.
- X и color/breakdown кастятся, мера — нет — ✅
  (`test_shared_bar_numeric_dimension_x_is_string_cast`: X data_type/cast/initial_data_type=string,
  type остаётся DIMENSION, Y-мера float не тронута; `test_shared_stacked_bar_numeric_color_..._cast`).
- date-X не тронут — ✅ (`test_shared_bar_date_dimension_x_not_cast`).
- Датасет/селекторы не задеты — ✅ (важно). `_field_item` СТРОИТ НОВЫЙ dict на каждый вызов и не
  мутирует входной `field` из `fields_by_alias`. `build_selectors` читает `field0["data_type"]`/
  guid из dataset `result_schema` (`adapter.py:188`), не из placeholder → string-cast в чарте не
  протекает в `fieldType`/`elementType` селектора, не ломает date-`isRange`. SQL-subselect строится
  из IR в `build_dataset_payload`, до и независимо от placeholder-каста.
- `initial_data_type` консистентен — ✅. `data_type` и `initial_data_type` берут одно значение
  (`data_type` локальная), `cast` — отдельно, но синхронно ("string"/исходный). Нет рассинхрона.
- Shape-тест не сломан — ✅ (`test_shared_full_shape_pins_service_blocks` в зелёном прогоне).

## Находки

### P1 (блокеры)
Нет.

### P2 (исправить отдельным коммитом — код уже в main)
Нет.

### P3 (бэклог / косметика / покрытие)

- **P3-1 — advisor видит до-нормализационный spec (UX-несогласованность).**
  `auto_bi/agent/machine.py:260` / `auto_bi/api/app.py` preview.
  Advisor (`group_by_high_cardinality`) и UI-превью показывают spec ДО B1, а собирается
  нормализованный (order_by+тесный limit). Пользователь может увидеть «limit 5000, без order_by»,
  одобрить, и получить top-25-дашборд. Поведение осознанное (build-time нормализация, план §«Уже
  сделано»), advisory-only, не нарушает инвариант 5. Рекомендация: либо строкой в build-логе
  отметить «применён default top-N (N=25/12) на чарт X», либо явно зафиксировать решение в
  ARCHITECTURE. Низкий приоритет.

- **P3-2 — Superset под-фиксит реже, чем B1 скипует (предсуществующая узость, не введена сессией).**
  `auto_bi/adapters/superset/form_data.py:130-143` (`_ordering_measure`).
  B1 `_orders_by_measure` ловит меру в ЛЮБОМ order_by (мирор SQL_GEN), а Superset `_ordering_measure`
  смотрит только `order_by[0]` и не учитывает label. Корнер-кейс: `order_by=[store_id asc,
  sum_revenue desc]` над числовым измерением → B1 скипует (автор выразил порядок), но Superset не
  применит measure-sort (head=store_id) и отрисует стену по store_id с limit=5000. Это поведение
  Superset-адаптера, B1 его не вводил и не ухудшил. Рекомендация (бэклог): согласовать
  `_ordering_measure` с резолюцией SQL_GEN (head→any, + label) ИЛИ задокументировать узость. Не actionable в рамках B1/B2.

- **P3-3 — нет теста на детект меры по `label` в `_orders_by_measure`.**
  `tests/test_normalize.py`. Ветка `m.label` в measure_refs не покрыта (тестовая мера REVENUE без
  label). Логика верна, но регресс-защиты нет. Рекомендация: кейс с `Measure(..., label="rev")` и
  `order_by=[by="rev"]` → untouched.

- **P3-4 — нет теста на multi-measure (выбор `measures[0]`).**
  `tests/test_normalize.py`. Что именно ПЕРВАЯ мера идёт в order_by desc — не зафиксировано тестом.

- **P3-5 — нет теста на qualified/joined первичную ось в `_is_time_dimension`.**
  `auto_bi/agent/normalize.py:55` (ветка `"." in ref`). Скип/не-скип для `dimensions=["dm.stores.city"]`
  через джойн не покрыт; ветка резолвится, но без теста.

- **P3-6 — нет теста на defensive-ветки `_is_time_dimension` (unknown table/column → non-time).**
  `auto_bi/agent/normalize.py:50-53`. Поведение «оставить невалидный spec для validate_spec»
  заявлено в docstring, но не закреплено тестом.

- **P3-7 — нет негативного теста на boolean-измерение в B2 (не кастится).**
  `tests/test_datalens_chart.py`. `_is_numeric_dimension` верно исключает boolean, но это не
  проверено явно (только date/line как негативы). Косметика.

## Итоговый вердикт

**B1 и B2 принимаются как есть.** Реализация соответствует плану и инвариантам 1–8, корректна на
проверенных и разобранных edge-кейсах (idempotency, time-skip, явный top-N, label-детект,
selector-изоляция B2, отсутствие мутации общего field-dict, валидность нормализованного spec).
pytest 346/32 deselected и ruff — зелёные (проверено ревьюером, не на слово).

Блокеров и P2 нет — дорабатывать до «принято» нечего. P3 — это покрытие тестами (P3-3…P3-7),
одна UX-согласованность advisor↔build (P3-1) и предсуществующая Superset-узость (P3-2, вне
скоупа B1/B2). Все P3 — бэклог, по «go».
