# Auto_BI — Архитектура

Дата: 2026-06-11 (вкл. вечернюю переработку под «определённую историю»). Статус: утверждённый дизайн, код не начат.

## 1. Концепция

Агент, который:
1. видит DM-слой DWH (интроспекция + семантическая модель, включая физический слой движка),
2. принимает запрос в одном из трёх UI-режимов: словесное описание (text-first), drag&drop-раскладка полей витрин (fields-first) или авто-обзор витрины (auto-overview, детерминированный, без LLM),
3. ведёт уточняющий диалог **только** когда запрос расходится с данными или неоднозначен,
4. честно предупреждает, когда запрошенное витриной не предусмотрено (engine-aware Feasibility Advisor: «этот набор фильтров убьёт производительность — это запрос на другую витрину»),
5. даёт выбрать целевую BI-платформу (v1: Superset; v2: DataLens; дальше по спросу),
6. строит дашборд в выбранной BI и возвращает ссылку.

LLM: **Sonnet 4.6 thinking через GraceKelly API** (см. §3.6).

### 1.1 Скоуп: «спроектировано для N, построено для 1»

Целевой рынок — российский. Решение от 2026-06-11: **не универсальная история, а определённая** —

- **v1: ClickHouse (DM) + Superset (BI)** — типовой RU-стек «быстрых витрин под BI»; advisor глубокий по ClickHouse — это и есть продукт.
- **v2: Greengage/Greenplum-семейство** вторым движком (ядровые DWH; PG-based — переиспользование интроспектора) + **DataLens** вторым BI (подтверждён Public API).
- **Вне скоупа**: Power BI и Tableau (ушли с рынка RU), Metabase (не входит в RU-топ).

Универсальность остаётся **в швах, а не в имплементации**: IR, интерфейс `BIAdapter`, структура rule pack per engine — это дисциплина кода, стоит ~0; имплементируется один путь. Возврат любой вычеркнутой платформы — новый адаптер, не переделка агента.

## 2. Ключевое решение: IR-first (Dashboard Spec)

Центральная идея архитектуры — промежуточное BI-агностичное представление дашборда:

```
NL-запрос / раскладка полей ──LLM──▶ DashboardSpec (JSON, валидируется по семантической модели)
                                          │
                                          ├──компилятор──▶ Superset (REST API)        [v1]
                                          ├──компилятор──▶ DataLens (Public API)      [v2]
                                          └──компилятор──▶ … (шов для следующих BI)
```

**LLM никогда не генерирует нативные форматы BI** (form_data Superset, конфиги чартов DataLens). LLM думает — код компилирует. Это даёт:
- изоляцию от drift'а форматов BI (ломается адаптер — чинится адаптер, агент не трогается);
- мульти-BI без изменения агента — один spec, N таргетов;
- тестируемость: golden specs → детерминированная компиляция → contract-тесты;
- валидируемость: spec проверяется против семантической модели ДО любых вызовов BI.

## 3. Компоненты

```
auto_bi/
  introspect/     # DWH → сырые метаданные (ClickHouse v1; Greengage/PG v2)
  semantic/       # SemanticModel: model.yaml, enrichment, gaps report, валидация
  agent/          # state machine диалога: grounding, уточнения, генерация spec, SQL
  advisor/        # Feasibility Checker: rule packs per engine + EXPLAIN-evidence
  llm/            # LLMClient-абстракция; GraceKellyClient — первая реализация
  ir/             # DashboardSpec: pydantic-схемы + JSON Schema
  adapters/       # superset/ (v1), datalens/ (v2) — компиляторы IR
  api/            # FastAPI backend (Phase 2)
  ui/             # v0 = CLI-чат (rich), v1 = web (Phase 2)
  store/          # SQLite: sessions, dialogue history, specs, builds, llm calls
```

### 3.1 Introspect

- Подключение к DWH **строго read-only ролью**.
- **ClickHouse — референсная реализация (v1)**: `system.tables` / `system.columns` — движок таблицы, `sorting_key`, `partition_key`, комментарии, `total_rows`/`total_bytes`; приблизительные кардинальности низкокардинальных колонок (`uniq()` по сэмплу), top-N значений, min/max дат.
- Greengage/Greenplum (v2): PG-катологи + distribution key, партиции; частично переиспользует PG-путь. _Реализовано (Phase 3.3): `introspect/greenplum.py` — `pg_get_table_distributedby` (distribution key), `pg_partition` (range-партиция), `pg_stats.n_distinct` (кардинальность), reltuples суммируются по партиционным детям; FK-guess пропускает self-ref. `make_run_query_pg` (psycopg, одна сессия). Live-валидировано на Greenplum 6.25 → `semantic/model_gp.yaml`. Greengage — форк GP, тот же путь._
- Опциональный импорт dbt `manifest.json` / `catalog.json` → descriptions, relationships, тесты. _Реализовано (задача 2.6): `auto_bi dbt-import` / `semantic/dbt_import.py`. Политика: dbt — источник ОБОГАЩЕНИЯ, не схемы (схемой владеет интроспектор): заполняются только ПУСТЫЕ описания/fk (ручные правки выигрывают), relationships-тесты → joins (дедуп) + fk, dbt-модели/колонки без пары в model.yaml репортятся, но не добавляются; повторный прогон идемпотентен; `--dry-run` для превью._
- Интерфейс `Introspector` — диалекты добавляются как плагины.
- Выход: черновик `semantic/model.yaml` + **gaps report** (что без описаний/ролей).

### 3.2 Semantic Model

`model.yaml` — версионируется в git, правится руками после автогенерации:

```yaml
tables:
  - name: dm.sales_daily
    description: Дневные продажи по магазинам
    grain: [date, store_id]
    columns:
      - {name: date,     type: date,    role: time}
      - {name: store_id, type: int,     role: dimension, fk: dm.stores.id}
      - {name: revenue,  type: numeric, role: measure, agg: sum, description: Выручка, руб}
      - {name: orders,   type: int,     role: measure, agg: sum}
    physical:
      engine: clickhouse                # v1: clickhouse; v2: greengage/greenplum
      table_engine: MergeTree
      sorting_key: [date, store_id]
      partition_key: toYYYYMM(date)
      rows: 120000000
      cardinality: {store_id: 4200, manager_id: 18000}
joins:
  - {left: dm.sales_daily.store_id, right: dm.stores.id, type: many_to_one}
metrics:
  - {name: avg_check, sql: "sum(revenue) / nullif(sum(orders), 0)", description: Средний чек}
```

- Роли колонок (`time` / `dimension` / `measure`) — автоэвристика по типам и именам + ручная правка.
- `physical` — заполняется интроспекцией автоматически: движок, ключи сортировки/партиционирования, размеры, приблизительные кардинальности. Это формальное определение «что предусмотрено дизайном витрины» — топливо для Feasibility Advisor.
- Качество модели = качество всего продукта: если DM без комментариев, уточнения агента будут мусорными. Поэтому gaps report — обязательный артефакт, а enrichment (дозаполнение описаний) — first-class workflow, не «потом».

### 3.3 Agent Core

State machine (без LangChain/LangGraph — простой цикл + pydantic):

```
INTAKE → GROUNDING → CLARIFY* → PROPOSE_SPEC → APPROVE → SQL_GEN → VALIDATE → BUILD → DONE
                ▲________________│ (правки словами)            ▲_____│ (repair loop, max 3)
```

- **GROUNDING**: LLM сопоставляет запрос с семантической моделью. Вход — текст или fields-first раскладка полей (§3.7). Выход — grounding report: какие сущности запроса нашлись в модели, какие нет, где неоднозначность (два кандидата-поля и т.п.). Параллельно детерминированный Feasibility Checker (ниже) прогоняет затронутые таблицы/поля по правилам движка.
- **CLARIFY**: вопросы генерируются **только из grounding report**, максимум 3 за раунд. Однозначный запрос → ноль вопросов, сразу spec. Это анти-паттерн-гард: болтливый агент хуже бесполезного.
- **PROPOSE_SPEC**: DashboardSpec + человекочитаемое резюме («6 чартов: выручка по дням (line), топ-10 магазинов (bar)…») + вердикты Feasibility Advisor по каждому проблемному чарту. Пользователь подтверждает или правит словами → patch spec.
- **SQL_GEN**: SQL для каждого чарта по семантической модели, в диалекте целевого DWH.
- **VALIDATE**: два уровня. (1) Spec против модели: ссылка на несуществующее поле → reject с фидбеком LLM (никаких «молчаливых починок»), max 3 итерации. (2) SQL: sqlglot-парсинг (только SELECT), `EXPLAIN`, пробное выполнение с `LIMIT` и timeout. EXPLAIN-результаты прикладываются к findings advisor'а как измеренное evidence.
- **BUILD**: вызов адаптера, лог сборки шаг за шагом, итог — URL дашборда.

Режим итераций (Phase 2): «добавь фильтр по региону» → grounding по diff → patch spec → пересборка только изменённого.

#### Feasibility Advisor (engine-aware; советует, никогда не блокирует)

«Не предусмотрено витриной» — технический факт, а не мнение: DM спроектирован под конкретные паттерны доступа, и они считываются из `physical`-метаданных. Разделение труда по D5 — **вердикт выносит код, LLM формулирует**.

Детекция — два слоя (это НЕ перечисление сценариев):

1. **Универсальный слой — EXPLAIN/dry-run**: движок сам оценивает запрос (`EXPLAIN indexes=1`, `EXPLAIN ESTIMATE` в ClickHouse). Детектирует дорогие запросы без знания «сценариев» — пороги по доле скана/объёму чтения.
2. **Rule pack per engine — объясняет ПОЧЕМУ и что делать**: набор *механизмов*, а не кейсов. v1 — глубокий ClickHouse-пак (~8–12 правил): `filter_not_in_sorting_key_prefix`, `partition_misaligned_filter`, `join_large_large`, `group_by_high_cardinality`, `final_required`, `point_lookup_pattern`… Каждое правило накрывает класс дашбордов. v2 — Greengage-пак (distribution skew, broadcast motion, partition pruning). Новый движок подключается дёшево: интроспектор + EXPLAIN-адаптер → advisor работает в evidence-only режиме, rule pack добавляется потом. _Реализовано (Phase 3.4): `advisor/greenplum.py` — `non_colocated_join` (join мимо distribution key → motion), `partition_not_pruned`, `distribution_skew`; `gp_explain_evidence` парсит план GP (motion-узлы + «Partitions selected»). `advisor/core.py` выбирает rule pack + форму EXPLAIN-evidence + SQL-диалект по `physical.engine` (CH → CH-пак/EXPLAIN ESTIMATE; greenplum → GP-пак/парс плана). Дизель-шов: `auto_bi/engine.py` (engine → sqlglot dialect), `generate_chart_sql(query, dialect=…)`, `guard_sql`/`LiveSQLValidator` per-engine trial-run. Live-валидировано на GP-стенде._

Выход и подача:

- **Findings с severity** `info | warn | critical`; advisor никогда не блокирует сборку — решает пользователь.
- **LLM-нарратив** — прямой вердикт без эвфемизмов + альтернативы: «Этот дашборд убьёт производительность BI: фильтр по `manager_id` идёт мимо ключа сортировки (`date, store_id`) — скан ~96% из 120M строк на каждое обновление. Варианты: (а) обязательный date-фильтр; (б) убрать фильтр; (в) это запрос на другую витрину».
- **Классы вердикта**: `ok` | `spec_adjustment` (поправить запрос/фильтры/grain) | `dm_change_request` — запрошенное витриной не предусмотрено: нужна новая витрина / projection / другой ключ сортировки.
- **`dm_change_request` — first-class артефакт**: структурированная заявка владельцу DM (какие поля/фильтры/grain нужны, чем не подходит текущая витрина, частота спроса). Накопленные заявки в store — карта реального спроса на изменения DM-слоя.
- **Remediation — готовый артефакт-решение (2026-06-25)**: `dm_change_request`-находка несёт не только диагноз, но и **сгенерированный кодом** исполняемый артефакт (`Finding.remediation`, `findings.py`): CH `filter_not_in_sorting_key_prefix` → `ADD PROJECTION … ORDER BY <off-key cols>` + `MATERIALIZE`; CH `join_large_large` (реактивирован — джойны в IR с Phase 2) → денормализующая витрина `CREATE TABLE …__wide … LEFT JOIN …`; GP `distribution_skew` → `SET DISTRIBUTED BY (<higher-card>)` либо `RANDOMLY`. Артефакт детерминирован из `physical`-метаданных (как и вердикт — LLM его НЕ сочиняет, D9), хранится в `dm_change_requests.remediation` (store v4) и рендерится секцией «Предлагаемое решение» в заявке (`dmcr.render_remediation`). Заявка превращается из «вот проблема» в «вот миграция, проверьте и примените». Advisory-only: артефакт для ревью человеком, агент его не применяет.

### 3.4 IR — DashboardSpec

Pydantic v2 + экспорт JSON Schema (она же вставляется в промпт LLM):

```json
{
  "title": "Продажи: обзор",
  "target_bi": "superset",
  "filters": [
    {"column": "dm.sales_daily.date", "type": "time_range", "default": "last 90 days"}
  ],
  "charts": [
    {
      "id": "c1",
      "title": "Выручка по дням",
      "viz": "line",
      "query": {
        "table": "dm.sales_daily",
        "dimensions": ["date"],
        "measures": [{"column": "revenue", "agg": "sum", "label": "Выручка"}],
        "filters": [],
        "order_by": [{"by": "date", "dir": "asc"}],
        "limit": 5000
      },
      "layout_hint": {"w": 6, "h": 4, "row": 0}
    }
  ]
}
```

- `viz` enum v1: `big_number, line, bar, stacked_bar, area, pie, table, pivot, heatmap`.
- **Роли измерений в `query`** (rich roles, Phase 1.1): `dimensions` — основная группировка (x-ось line/bar/area, доли pie, две оси heatmap, колонки table); `series` — разбивка/стек для `stacked_bar`/`area`; `rows`/`columns` — строки и колонки `pivot`. SQL_GEN группирует по объединению всех четырёх; адаптер читает каждую роль для раскладки чарта. Каждый viz объявляет используемые роли — неиспользуемые роли должны быть пустыми (валидация по форме).
- **Joins (2026-06-13, снимает ограничение Phase 0)**: измерение/фильтр из смежной таблицы пишется полным именем (`"dm.stores.city"`) + явный `query.joins: [{table, on_left, on_right}]`. Валидация принимает только пары колонок, существующие как рёбра `joins` semantic model (LLM не может выдумать условие); меры — только с базовой таблицы (анти-fan-out); связь — только прямое ребро (без multi-hop). SQL_GEN компилирует LEFT JOIN, квалифицирует ВСЕ ссылки базовой таблицей (смежные таблицы могут разделять имена колонок) и алиасит присоединённые колонки в «голое» имя (`AS "city"`) — датасет для BI всегда выглядит одинаково, `column_alias()` в `ir/spec.py` — единый источник. Коллизии голых имён между таблицами в одном чарте отклоняются валидацией.
- `target_bi` enum v1–v2: `superset | datalens`.
- **Capability matrix** viz → BI: что таргет не умеет — деградация по явному правилу, с пометкой в build log.
- `query` — декларативный (таблица/измерения/меры/фильтры), не сырой SQL: SQL генерируется отдельным шагом и валидируется. Эскейп-хэтч `raw_sql` допускается, но помечается и проходит sqlglot-guard.
- **Дефолтный top-N категориальных чартов (B1, 2026-06-15)**: детерминированный IR-трансформ `agent/normalize.py::apply_chart_defaults(spec, model)` в начале `compile_and_build` (ДО валидации/SQL/адаптера, поэтому чинит ОБА BI из одного места) проставляет `order_by=[первая мера desc]` + ужимает `limit` (pie≤12, иначе 25) для `bar/stacked_bar/pie` с непустыми `dimensions`, чья первичная ось НЕ time-колонка и где нет `order_by` по мере — снимает «стену баров» на high-cardinality измерении без top-N. Идемпотентно; явный top-N автора (order_by по мере — raw-колонка/алиас/лейбл, зеркало SQL_GEN) и time-оси не трогает. Preview/advisor видят до-нормализационный spec (build-time нормализация), `compile_and_build` логирует, к каким чартам применён.
- **Производные меры — PoP / доля / running total (2026-06-25, обогащение #2)**: `Measure.transform: MeasureTransform | None` (`pop_abs`/`pop_pct`/`yoy_pct`/`share_of_total`/`running_total`) — аналитический трансформ поверх базового агрегата, детерминированно компилируемый в оконную функцию (без LLM, инвариант 1/D5). Архитектура «derived-as-column»: SQL_GEN считает производную как именованную колонку (`measure_alias`), а оба адаптера и поле датасета DataLens уже адресуют меры по этому alias → правок адаптеров почти нет (только percent-формат). SQL_GEN при наличии transform-мер строит **двухуровневый** SELECT: inner GROUP BY базовых агрегатов под приватными `__src_i` → outer оконные функции поверх них (обычные меры проходят насквозь); не-transform путь не тронут. Per-dialect — через sqlglot: `exp.Lag` → CH `lagInFrame` / PG `LAG`; `SUM() OVER ()` идентичен. Валидация: pop_*/yoy_pct/running требуют первое измерение = TIME, share требует ≥1 измерение, transform запрещён на big_number/pivot/heatmap. Числовая сверка PG-пути — DuckDB (postgres-семантика окон); CH `lagInFrame` (frame-bounded, явный фрейм `1 PRECEDING`) — за live-verify gate (`docs/plans/2026-06-25-derived-metrics-pop.md`). **yoy_pct (2026-06-28)** — тот же оконный лаг, но на целый год периодов: `_periods_per_year(time_grain)` (month12/quarter4/week52/year1) → `lag(k)` с фреймом `k PRECEDING` (offset опускается при k=1 → pop байт-в-байт; gotcha: у sqlglot `exp.Lag` смещение = `offset`, не `expression`); поэтому yoy_pct требует non-day `time_grain`. mom отдельным трансформом не нужен — это pop_pct при month-grain. **lag_periods (2026-06-29, A2)** — `Measure.lag_periods: int | None` обобщает фиксированный годовой лаг yoy на произвольное смещение: pop_abs/pop_pct сравнивают со значением `N` периодов назад (lag_periods=3 при month-grain = «vs 3 месяца назад») через тот же `lag(k)` (k = `lag_periods or 1`; при k=1 SQL байт-в-байт как раньше). Применим ТОЛЬКО к pop_abs/pop_pct (yoy сам считает год; share/running без смещения; обычная мера без лага) — валидация по всем мерам; alias получает суффикс `_lag<N>`, чтобы не коллидить с соседней мерой смежного периода. Та же frame-bounded `lagInFrame`-конструкция, что у yoy → CH live-verified (`scripts/verify_live_clickhouse.py`: lag3 на 24 мес, первые 3 NULL). Design-варианты A2–A5 — `docs/plans/2026-06-29-core-deepening-a2-a5.md`. **running_share (2026-06-29, A3)** — `MeasureTransform.RUNNING_SHARE`: Pareto/ABC — категории, ранжированные по мере УБЫВАЮЩЕ, накопленная доля от итога: `SUM(src) OVER (ORDER BY src DESC ROWS UNBOUNDED PRECEDING) / SUM(src) OVER ()`. В отличие от прочих ordered-трансформов окно сортируется по ЗНАЧЕНИЮ МЕРЫ, не по времени → НЕ в `_ORDERED_TRANSFORMS`, требует измерение (как share_of_total), но НЕ time-ось; значение каждой категории = её ранговая накопл. доля независимо от порядка вывода, наименьшая закрывается на 1.0 (точные тай-значения упорядочиваются произвольно в ROWS-фрейме — несущественно для ранжир-вью). `is_percent_measure`→True (percent-формат). Новая SQL-конструкция (не yoy-лаг) → отдельный CH live-verify (`running_share` по 4200 магазинам = независимый кумулятив, закрывается на 1.0).
- **Замена id-измерений на названия через join (B3, 2026-06-15)**: детерминированный IR-трансформ `agent/normalize.py::apply_label_joins(spec, model)` в `compile_and_build` (ДО B1/валидации/SQL/адаптера, поэтому чинит ОБА BI из одного места) заменяет dimension-роль, являющуюся сырым FK-id (`store_id` с `Column.fk`), на человекочитаемую name-колонку целевой таблицы через LEFT JOIN (`store_id` → `dm.stores.name`). **Lossless-by-construction**: swap только когда `physical.cardinality` доказывает, что name ~уникально на id (`label_card ≥ 0.99·id_card`; нет cardinality-доказательства → нет swap) — иначе id остаётся, никаких молчаливых слияний строк (correctness-тренодофф, на котором B3 был gated). Только IR-уровень: оба адаптера уже рендерят joined-измерения (Phase 3 контракт-тесты) → правок адаптеров нет. Добавляемый join зеркалит ребро модели (инвариант 2); коллизия bare-алиасов / уже-квалифицированный ref / non-FK измерение → no-op (transform никогда не отдаёт spec, который не пройдёт `validate_spec`). Свапаются dimensions/series/rows/columns; меры и фильтры держат сырой id; `order_by` по id ремапится. Идемпотентно. Live-verified E2E на DataLens (имена магазинов на оси) + Superset-build на том же SQL; S6 (субагент `code-reviewer`, `docs/history/fable_audit_b3_label_joins.md`): 0 P1/0 P2/5 P3.
- **Меры-отношения — ratio (2026-06-28)**: `Measure.denominator: Measure | None` → мера = `agg(num) / agg(den)`, оба агрегата в одном GROUP BY, деление во float с защитой от нуля. Тот же «derived-as-column» двухуровневый SELECT, что у трансформов: inner эмитит и числитель (`__src_i`), и знаменатель (`__den_i`), outer делит через `_safe_div` (CAST→Float64/DOUBLE + NULLIF) — уже live-verified на CH для share/pop_pct. Адаптеры не тронуты (адресуют по `measure_alias`, у ratio суффикс `_per_<den>`); формат — точное число (не compact, не percent; `is_ratio_measure`); DataLens-тип поля = float. Валидация: колонка/роль знаменателя как у числителя; запрет вложенности и совмещения с transform; ratio допустим на всех viz (он просто значение меры). Domain-neutral примитив (маржа/конверсия/доля брака/ошибки-на-запрос — «средний чек» лишь частный случай). Числовая сверка PG-пути — DuckDB; CH-деление за тем же live-verify gate.
- **Грануляция времени — time_grain (2026-06-28)**: `ChartQuery.time_grain: TimeGrain | None` (`day/week/month/quarter/year`) усекает time-ось (первое измерение) до периода, чтобы длинный дневной ряд читался трендом (730 дней → 24 месяца). SQL_GEN оборачивает измерение в trunc per-dialect (`_grouped_select` получил параметр `dialect`): CH `toStartOf*` (неделя с понедельника, mode 1) / PG `date_trunc` (неделя с понедельника) — в SELECT и GROUP BY, alias на «голое» имя → адаптеры/датасет не тронуты; `day` = сырой ряд. Композится с трансформами (month-over-month) и ratio. Валидация: первое измерение = TIME. Основа yoy_pct. Числовая сверка месячной агрегации — DuckDB.
- **Гистограмма — bins (2026-06-29, A4)**: `Viz.HISTOGRAM` + `ChartQuery.bins: int | None` — распределение числовой колонки: первое измерение (колонка role=measure, количественная) бьётся на `bins` равноширинных корзин, мера = число строк в корзине (COUNT). Триггер SQL_GEN — `query.bins is not None` (отдельный путь `_generate_histogram_sql`, не трогает flat/windowed): одностроковый подзапрос `b` считает `min` и ширину `w=(max−min)/bins` над (отфильтрованной) таблицей, внешний запрос через CROSS JOIN маппит каждую строку в нижнюю границу корзины `mn + least(floor((x−mn)/NULLIF(w,0)), bins−1)*w` (idx зажат в bins−1 — максимум попадает в последнюю корзину, не в одиночную сверх; нулевая ширина → одна корзина через NULLIF) и группирует по ней. Бинируемая колонка **кастуется в Float64** (Decimal-деление/floor в CH мис-бинит граничные строки — тот же урок, что `_safe_div`; live-verify это поймал) и **квалифицируется базовой таблицей** в bucket-выражении (anti alias-shadow, как `_grained_source`). Корзина алиасится «голым» именем измерения → адаптеры рендерят как **бар** по упорядоченным корзинам (Superset `echarts_timeseries_bar`+`xAxisForceCategorical`, DataLens `column` discrete) — **бинирование в SQL, рендер переиспользует bar-путь, без реверса движка**. Фильтры применяются и к подзапросу ширины, и к внешнему запросу (корзины = отфильтрованный диапазон). Валидация: ровно 1 измерение role=measure + 1 мера-count, bins↔HISTOGRAM взаимно-обязательны, несовместимо с time_grain/transform. CH live-verified (`scripts/verify_live_clickhouse.py`: 8 корзин по `dm.products.price`, границы+счётчики = ручной расчёт, все 2000 строк в корзинах).
- **Скалярный period-compare KPI — compare (2026-07-04, S14)**: `Measure.compare: ScalarCompare | None` (`{column, grain, kind: yoy|pop, output: pct|abs}`) — KPI, значение которого = **одно число**: последний период vs период год назад (`yoy`) или предыдущий (`pop`), как относительное (`pct`, процент — `is_percent_measure`) или абсолютное изменение. В отличие от `yoy_pct` (оконный РЯД, точка на период вдоль time-оси) сворачивается до **одной строки** через условную агрегацию по двум бакетам (`_generate_compare_kpi_sql`, триггер SQL_GEN — `m.compare is not None`, отдельно от flat/windowed/histogram): одностроковый подзапрос `b` считает `max(toStartOf<grain>(date))` (последний ПРИСУТСТВУЮЩИЙ бакет) и тот же max минус yoy/pop-интервал (yoy = полный год периодов в единице грейна: week=364 day, quarter=12 month, month=12 month, year=1 year; pop = один период), внешний запрос агрегирует меру по каждому бакету через `agg(CASE WHEN toStartOf<grain>(date) = b.p_* THEN col END)` и формирует `(cur − prior)/prior` (pct, числитель в Float64 через `_safe_div`) или `cur − prior` (abs). Отсутствующий бакет год назад → NULL (`NULLIF`), не падение. Так big_number остаётся ИСТИННЫМ скаляром (нет окна, нет отображаемого измерения — одна строка). Условная агрегация (`SUM(CASE …)`) кросс-диалектна (CH/PG), без оконных CH-кварков (`lagInFrame`); time-колонка квалифицируется базовой таблицей (anti alias-shadow, как `_grained_source`). Валидация: только на `big_number`, grain non-day, `compare.column` = TIME-колонка, взаимоисключимо с `transform`/`denominator`. Адаптеры НЕ тронуты — процентный big_number уже рендерится (`is_percent_measure` → Superset d3 `.1%` / DataLens percent-`formatting`; DataLens percent на плитке — тот же known engine-limit, что percent-на-оси, числа верны). Autospec (при ≥2 годах) добавляет плитку «`<герой>`, г/г» рядом с уровнем героя, заместив прежнюю полноширинную yoy-линию (тот же инсайт компактнее, освобождает слот для structure/share-вью). Числовая сверка PG-пути — DuckDB; **CH live-verified** (`scripts/verify_live_clickhouse.py`: yoy-скаляр = последний месяц vs год назад, ручной расчёт). Design-варианты — `docs/plans/2026-07-04-s14-yoy-kpi-scalar.md`.
- **Аналитическое ядро в text-first (2026-07-03, S01/F1)**: всё вышеперечисленное (`transform`, `denominator`, `time_grain`, `lag_periods`, `bins`) теперь выражается СЛОВАМИ, а не только программным spec/fields-first. `SPEC_RULES` (`agent/propose.py`) документирует каждый примитив с few-shot JSON-примерами, дословно согласованными с `ir/validate.py` (yoy_pct⇒non-day grain, running_share⇒категориальное измерение без order_by, histogram⇒count той же колонки + bins); grounding-правило 5 (`agent/grounding.py`) переведено с устаревшего «отношения мер дашборд не умеет» на «ratio = matched, когда обе составляющие есть в модели» (обе колонки в candidates), а аналитические обороты («год к году», «Парето», «нарастающим итогом», «распределение») закреплены как форма подачи, не сущности. Дрейф-гард: тест требует упоминания каждого члена `MeasureTransform`/`TimeGrain` и core-полей в SPEC_RULES — новый примитив не пройдёт CI, пока промпт его не объяснит. Golden-eval расширен ожиданиями по самим примитивам (`expect_transforms/expect_ratio/expect_time_grain/expect_bins/expect_lag` — правильные колонки без примитива = FAIL): кейсы g13–g23 + it4 (CH) и gp_g9–gp_g12 (GP); «средний чек» переведён из ambiguous в clear — с ratio-мерой канонное чтение revenue/orders лучше лишнего вопроса (grounding-правило 4).

### 3.5 BI Adapters

Общий интерфейс:

```python
class BIAdapter(Protocol):
    def healthcheck(self) -> AdapterHealth
    def ensure_database(self, dwh: DWHConfig) -> DatabaseRef      # connection внутри BI
    def ensure_dataset(self, query: ChartQuery) -> DatasetRef     # physical table или SQL-датасет
    def create_chart(self, chart: ChartSpec, ds: DatasetRef) -> ChartRef
    def assemble_dashboard(self, spec: DashboardSpec, charts: list[ChartRef]) -> DashboardRef
    def build(self, spec: DashboardSpec) -> DashboardRef          # оркестратор: full compile
```

`build(spec)` оркеструет шаги одинаково для обоих адаптеров (Phase 4 F1): семантическая модель, нужная адаптеру (скоупинг нативных фильтров по роли/grain колонки у Superset; типы полей датасета у DataLens), **инжектится в конструктор**, поэтому `build` принимает только spec — единая сигнатура позволяет пайплайну диспетчить один spec в любой BI по `spec.target_bi`. Без модели фильтры Superset деградируют в задокументированное предупреждение. `assemble_dashboard` принимает доп. `datasets`/`model` (Superset) или `placements` (DataLens) — аддитивно к Protocol, для прямых вызовов в контракт-тестах.

**Фабрика `adapters/factory.py::make_adapter(target_bi, settings, model) -> BIAdapter`** — единственная точка, знающая о конкретных адаптерах: собирает клиент + DWHConfig из настроек. Пайплайн (`agent/pipeline.py`) типизирован на `BIAdapter` и получает резолвер `Callable[[TargetBI], BIAdapter]` (фабрика с зафиксированными settings+model); `compile_and_build` вызывает `adapter_for(spec.target_bi)`, так что spec с `target_bi="datalens"` не может молча собраться в Superset (инвариант 2 на границе BI). `cli build` принимает `--target {superset|datalens}` (переопределяет дефолт spec); API/UI-селектор BI ставит `spec.target_bi` (Phase 4 F8, реализовано): `POST /sessions {target_bi}` фиксирует цель на сессию (как режим text/fields), и она (пере)штампуется на spec после каждого turn — IR BI-агностичен, а LLM-patch сбрасывает `target_bi` в дефолт, поэтому выбор переприменяется до build.

Ref-id'ы (`DatabaseRef.id`, `DatasetRef.id`, `ChartRef.id`, `DashboardRef.id`) типизированы **`int | str`** — BI-нативный идентификатор: Superset отдаёт целые id, DataLens — строковые entry id. Ref'ы потребляются только внутри своего адаптера (в общий код не текут), поэтому тип не дискриминируется нигде, а Superset-путь продолжает оперировать int без изменений (решение S4-2, 2026-06-13). `TargetBI` enum — `superset | datalens` (§3.4).

**DataLens-таргет — выделенный workbook (Phase 4 F3).** Адаптер пишет в **отдельный workbook «Auto_BI»** (`datalens_workbook_id`, дефолт `ra7f79yirtumb` на self-hosted стенде), НЕ в общий demo-workbook. Идемпотентность через delete-then-create (`_delete_if_exists`, реверс §5.5) удаляет entry по совпадению имени — изолированный workbook гарантирует, что удаляются только entry самого агента, чужие данные не под угрозой. **Rebuild атомарен на границе build/promote (Phase 4 F2, реализовано):** `build` создаёт каждый entry под временным именем `<canonical>__wip`, и canonical-entry прошлой рабочей версии НЕ трогаются, пока вся сборка не прошла успешно. Затем `_promote_to_canonical` для каждого entry удаляет устаревший canonical и **переименовывает** temp→canonical через gateway-экшен `us/renameEntry {entryId, name}` (entryId не меняется при rename → линки чарт↔дашборд и URL `/{entryId}` остаются валидны). Сбой ПОСРЕДИ build (транзиентный charts-engine 5xx) пробрасывается с **полностью целой прошлой версией** (promote не достигнут); сессия помечается failed и пересобирается. **Но сам promote НЕ атомарен между тремя entry** (P3): крэш внутри цикла (между delete и rename одного entry либо после части entry) оставляет частично-промотированное состояние — старый дашборд может ссылаться на уже удалённый dataset-id. Окно много́ меньше полного ребилда (две быстрые US-операции на entry, без charts-engine) и самовосстанавливается следующим build, но не нулевое. **Упавший build чистит свои temp-`__wip` entry** (`_cleanup_wip` в except-ветке `build`, best-effort, не маскирует исходную ошибку) — сироты не остаются даже если у следующей попытки другой title/набор чартов (закрыт P3 аудита; live-verified: сбой на 2-м чарте оставил 0 `__wip`). Прямой REST `POST /v1/entries/:id/rename` через UI-gateway НЕ проксируется (404) — используется gateway-экшен. Live-verified 2026-06-14 (rebuild×2 без `__wip`-сирот + симуляция сбоя: старый дашборд продолжил рендерить реальные данные). Полностью атомарный promote (нужна серверная multi-entry транзакция, которой в US нет, либо all-or-nothing порядок rename) — backlog.

| Адаптер | Фаза | Механика | Главная боль |
|---|---|---|---|
| Superset | 0–1 | REST `/api/v1/{database,dataset,chart,dashboard}`; auth `/security/login` → JWT + CSRF | `form_data` чартов недокументирован → библиотека шаблонов на viz_type (реверс через GET вручную созданных чартов), `position_json` — свой генератор 12-колоночной сетки |
| DataLens | 3 | **Public API** `api.datalens.tech` (статус Preview): `createConnection/createDataset/…`, создание Wizard/QL-чартов, `createDashboard`, workbooks; auth — IAM-токен Yandex Cloud | API в Preview — может меняться; таргетит облачный DataLens (для OSS-инстанса — спайк по внутренним API). Вход только через спайк 2–3 дня |
| Visiology / Luxms | по спросу | проприетарные API | делать только под реального клиента |

Power BI / Tableau / Metabase — вне скоупа (см. §1.1); интерфейс позволяет вернуть.

> **Уточнение (2026-06-23):** строка «DataLens» в таблице выше — *исходный замысел* (облачный Public API `api.datalens.tech`). Фактически реализован и live-проверен **self-hosted open-source DataLens** (gateway-реверс — см. жирные блоки §3.5 ниже и runbook `docs/plans/2026-06-13-datalens-selfhosted-runbook.md`), а не облако: Yandex Cloud требует аккаунт/биллинг, недоступные проекту. Швы IR и `BIAdapter` идентичны — отличаются только auth и транспорт (cookie-gateway вместо IAM-Bearer).

Правила стабильности Superset-адаптера: версия Superset зафиксирована в `docker-compose.yml`; contract-тесты «create → GET → assert» на каждый viz_type; обновление версии — отдельная задача с прогоном контрактов.

**Правила стабильности DataLens-адаптера (Phase 4 F7, инвариант 7 распространён на DataLens):** реверс-блобы (zod `dataSchema` `schemeVersion=8`, chart `shared` `version="4"`, charset имени entry, `mix/createDashboardV1`/`mix/deleteEntry`, gateway `v4.10.4`, `HC=1`) завязаны на конкретную версию self-hosted стенда. Поддержанная версия и контрактные маркеры зафиксированы в runbook `docs/plans/2026-06-13-datalens-selfhosted-runbook.md` (секция «Версия стенда — контрактный пин»); обновление версии стенда — отдельная задача с обязательным прогоном live contract-сьюта `tests/test_datalens_contract.py`. Гэп: точные image-digest'ы (стенд = depth-1 клон, плавающий тег) ещё не сняты — команда захвата в runbook.

**Категориальная ось числового измерения DataLens (B2, 2026-06-15)** — числовое DIMENSION-поле (`store_id`) на категориальном placeholder column-чарта (`bar`/`stacked_bar` → DataLens `column`) рендерилось на НЕпрерывной оси (тонкие бары на числовых позициях 0…N), а не категориями (Superset форсит дискретность через `xAxisForceCategorical`; DataLens — нет). `chart_config.py` кастит такое поле (`_is_numeric_dimension`: `type==DIMENSION` & `data_type∈{integer,float}`) в string ПРЯМО В PLACEHOLDER'е (`_field_item(as_string=True)` → data_type/initial_data_type/cast="string") для оси X и color/breakdown — DataLens тогда отдаёт highcharts `categories` + индексные x вместо сырых числовых (механизм реверснут live через `/api/run`). **Датасет НЕ трогается** (каст только в чарте) → subselect-SQL и dashboard-селекторы не задеты. `line`/`area` (читают ВДОЛЬ непрерывной оси), date/string-измерения и меры — без изменений (scope строго column-viz). Live-verified 2026-06-15 (bar→categories, line→continuous, stacked_bar→4 distinct series; скриншот Wizard).

**Native dashboard filters (2026-06-13, снимает предупреждение «фильтры не переносятся» и advisor-F3)** — `adapters/superset/native_filters.py` компилирует `spec.filters` в `json_metadata.native_filter_configuration` (формат реверснут с живого стенда: create в UI → GET). Каждый чарт = свой пре-агрегированный виртуальный датасет, поэтому **scope-to-applicable**: фильтр (WHERE по «голому» алиасу колонки) выводится только на чарты, чей grain (`group_columns`) содержит эту колонку — остальные в `scope.excluded` (Superset показывает фильтр, но они его игнорируют). Это сохраняет интент чарта: KPI «общая выручка» остаётся одним числом под city-фильтром. `filterType` берётся из РОЛИ колонки (`time` → `filter_time` с пустым target; иначе `filter_select` с `targets:[{datasetId, column:{name}}]`), не из `DashboardFilter.type` (его дефолт «time_range» LLM не переопределяет надёжно). **Семантика limit**: чарт в scope фильтра теряет top-N `LIMIT` в SQL датасета (`generate_chart_sql(apply_limit=False)`) — лимит уезжает в form_data `row_limit`, иначе фильтр ре-ранжировал бы пре-обрезанный топ-N, а опции select-фильтра были бы сами обрезаны до него. Контракт-тест `native_filter_configuration` round-trip на живом стенде; scope/типы — юнит-тесты. Фильтр, чью колонку не раскрывает ни один чарт, не виснет молча: пропускается, а зашитые `query.filters` всё равно ограничивают данные.

**Superset display-доводки (S13, 2026-07-04, живой фидбэк владельца по стенду; IR не тронут — только детерминированная компиляция form_data/фильтров):**
- **B5 — преднастроенный период дашборда.** `DashboardFilter.default` (уже был в IR) наконец наполняет `defaultDataMask` (раньше пустой `{}`): `superset_time_range()` нормализует относительный токен («last quarter» → title-case «Last quarter»; ISO-диапазон проходит без изменений), `_time_default_mask`/`_select_default_mask` сажают `extraFormData` (что реально ре-скоупит запросы: `time_range` для time-фильтра, `filters:[{col,op:IN,val}]` для select) + `filterState.value` (что показывает контрол выбранным). Пустой `default` → прежняя нейтральная маска. Дашборд открывается уже суженным на период/значение. **Auto-обзор (autospec) теперь задаёт этот дефолт** — `_OVERVIEW_PERIOD = "last 12 months"` (свежие данные, но полный год для yoy-KPI на экране), развернув прежний намеренный «без пресета»; пользователь расширяет до всей истории прямо на дашборде. **Связывание time_range с чартом (иначе пресет косметический):** маска доносит `time_range` до in-scope чарта, но ECharts-timeseries-запрос сам НЕ называет временную колонку — без этого Superset не к чему привязать диапазон (запрос идёт без `WHERE`, ряд остаётся полным, хотя контрол показывает период и бейдж «Applied filters (1)»). Поэтому `build_form_data` для timeseries над TIME-колонкой ставит `granularity_sqla` = алиас этой колонки (`SupersetAdapter._temporal_alias` по роли модели), а Superset должен пометить колонку `is_dttm` в датасете (авто-детект на свежем `Date`-столбце виртуального датасета; на переиспользованном датасете с устаревшими колонками — нет, поэтому пере-стройка того же дашборда — известное ограничение). Проверено end-to-end: свежая сборка → строка ряда 24 мес → 11 (последние ~12) под пресетом; контракт-тест `test_time_filter_actually_narrows_timeseries` фиксирует «сузилось < полного», а не только round-trip конфигурации.
- **RU-единицы KPI.** Крупный рублёвый `big_number` читается как «236 / млрд ₽» (масштабированное число + единица отдельной, мельче, subheader-строкой), а не d3 SI «236G» (движок не умеет русские слова величин — k/M/G/T зашиты). `form_data.ru_kpi_scale(value)` даёт `(делитель, слово)` по порогам 1e12/1e9/1e6/1e3 (зеркалит `insights._compact`); адаптер замеряет величину KPI **живым** `POST /api/v1/chart/data` (best-effort — любой сбой → `None` → дефолтный формат, косметика не ломает build) и масштабирует ТОЛЬКО аддитивные денежные агрегаты (`is_compact_number`); «₽» добавляется по денежному маркеру в описании колонки модели (счётчик без ₽). Эталон формата — память `autobi-kpi-ruble-units`. **Точность в полосе 1–10 единиц (L-1, 2026-07-07):** целочисленное округление там теряло до трети величины («1,5 млрд» → «2 млрд»), поэтому `_ru_scale` отдаёт ещё и масштабированную величину, и заголовок держит один десятичный знак (`",.1f"`), когда она < 10; от 10 и выше — круглое число как раньше. **Единый формат KPI-ряда (2026-07-09):** все `big_number`-плитки делят форму «значение / юнит-строка» и одинаковые пропорции шрифтов (`header_font_size 0.4` / `subheader_font_size 0.15` запинены явно — дефолт-дрифт Superset не рассинхронизирует ряд); процентная плитка больше НЕ рендерит «1.5%» одной строкой (длинная строка ужимала кегль, а без subheader-строки плитка центрировалась иначе соседних) — метрика ×100 в SQL, заголовок `.1~f` (точность `.1%` + трим хвостового нуля: «34», не «34.0»), «%» уезжает в subheader как юнит; центрирование плиток по обеим осям — `KPI_CENTER_CSS` в POST дашборда (у form_data нет ручки выравнивания, dashboard-CSS — детерминированный шов нативного формата, как `position_json`; селекторы сверены с DOM запиненного 4.1 и живым рендером демо). DataLens-зеркало не требуется в той же форме: там юнит инлайн (`_ru_kpi_formatting` postfix) и кегль «s» един на всех плитках — ряд взаимно однороден by construction; центрирование indicator-виджета — engine-default без публичной ручки (не реверсить). Live-verified пиксель-чеком 2026-07-10 на self-hosted стенде (auto-сборка `dm.sales_daily`): все 4 плитки DOM-идентичны (значение 24px/500, тайл 184×148, оффсеты блока 15/15/59/60), юнит инлайн одной строкой («236 млрд ₽» / «0,0%»); вертикаль блока значения центрируется движком (±1px), горизонталь — `text-align:start` без ручки, т.е. Superset-центрирования по X у DataLens нет и не будет средствами конфига.
- **RU-единицы на ОСИ line/bar/area** (тот же приём для cartesian-оси, `_axis_scale`/`build_form_data(axis_scale=...)`). d3 SI-формат оси тоже говорит только k/M/G/T («15G»), поэтому мера масштабируется тем же `ru_kpi_scale`, а единица уезжает на **заголовок оси значений** — «15 … млрд ₽» вместо «15G». Общая логика вынесена в `_ru_scale` (KPI-заголовок и ось используют её), проба величины — `_measure_magnitude` (`MAX` по датасету = скаляр для big_number, самая высокая точка ряда для line/bar). Единица всегда в `y_axis_title` — Superset моделирует его как ось МЕРЫ независимо от ориентации (горизонтальный бар лишь визуально флипает её вниз; `x_axis_title` сел бы на ось категорий). Percent-ось (доля) и средние — НЕ масштабируются. **Только одномерные чарты (F-3, 2026-07-07):** делитель тарифицируется по первой мере, но делил бы ВСЕ метрики чарта — на line «выручка + число заказов» вторая мера рендерилась бы в чужих единицах, поэтому при `len(measures) != 1` масштаб не применяется и чарт остаётся на компактном SI (guard в обоих адаптерах). **Это снимает прежний «engine-limit оси» частично** (для line/bar/area; в остальных типах ось не трогаем).
- **Человеческие легенды.** Отображаемое имя меры в легенде/тултипе/шапке колонки развязано с SQL-алиасом: autospec намеренно оставляет `measure.label=""` (тех. alias), а адаптер резолвит человеческое имя из модели (`_human_label`: явный label, иначе короткая форма описания колонки «Выручка, руб» → «Выручка») и прокидывает его в `metric.label`, синхронизируя ключ `column_config` у таблиц. SQL по-прежнему адресует датасет по алиасу — display и колонка независимы. **Сцепка с сортировкой баров:** Superset матчит `x_axis_sort` по МЕТКЕ метрики, поэтому ключ сортировки бара тоже берётся из display-label (`_label(m) or measure_alias(m)`) — иначе гуманизация легенды тихо ломала бы measure-сортировку (бар откатывался в алфавит). **Направление для горизонтали инвертируется:** echarts кладёт `category[0]` ВНИЗ, поэтому desc-спека сортируется ascending, чтобы крупнейший бар был СВЕРХУ (dashboard-craft §5 «крупнейший первый»).

Все — офлайн-юниты (`test_superset_adapter.py`/`test_native_filters.py`) + live render-verify на стенде (KPI/легенды/период — дашборды #21–#25; measure-сортировка баров — #28).

**DataLens — паритет по легендам (2026-07-05).** Тот же приём человеческих легенд перенесён в DataLens-адаптер: поле датасета (`result_schema`) несёт человеческий `title` («Выручка»), а не сырой алиас, из `measure.label`/короткой формы описания колонки (`dataset._human_field_title`). Привязка чарта к полю переключена с `title` на `source` (алиас-источник), поэтому humanized-заголовок не ломает поиск полей в `chart_config`. Сортировка баров по мере уже была (S12). Проверено: свежая auto-сборка приняла humanized-датасеты, DataLens contract 12/12 live. **RU-единицы на DataLens (N2) закрыты ФИКСОМ 2026-07-06 (P4)** — прежняя оценка «engine-limit» опровергнута: виджетный `unit:"auto"` действительно locale-bound (SI «236B»), поэтому адаптер меряет магнитуду live inline-`/api/run`-пробой, пересоздаёт датасет с мерой `/divisor` в обёртке-SELECT (`dataset.measure_scale`) и вешает RU-единицу postfix'ом на KPI (`_ru_kpi_formatting`, шрифт «s») / manual-title на ось значений — метод и render-verify в runbook `docs/plans/2026-06-13-datalens-selfhosted-runbook.md`. Guard мультимерности (F-3) и точность 1–10 (L-1) действуют и здесь, зеркально Superset.

### 3.6 LLM Layer — Anthropic (default) + GraceKelly (opt-in)

Абстракция `LLMClient` (protocol: `complete(prompt, schema) -> ValidatedModel`, `llm/base.py`) —
бизнес-код никогда не видит конкретного провайдера, только этот протокол. `llm/factory.py`
резолвит реализацию из `AUTO_BI_LLM_PROVIDER` (S02): **`anthropic`** (default) или
**`gracekelly`**. Обе делят репэйр-петлю и логирование вызовов (`llm/_structured.py`); отличается
только транспорт. Каждый клиент импортируется лениво — выбор одного провайдера никогда не
тянет зависимость другого (опциональный `anthropic`-SDK не импортируется на пути GraceKelly).

**Anthropic (по умолчанию, `llm/anthropic.py`)** — прямой Messages API, без внешнего сервиса:
нужен только ключ (`ANTHROPIC_API_KEY` или `AUTO_BI_ANTHROPIC_API_KEY`). Убирает единственную
точку отказа стороннего сервиса — воспроизводимость у внешнего пользователя не зависит от
чужой локальной инфраструктуры. `thinking:{type:"adaptive"}` на reasoning-шагах (зеркало
`reasoning: true` у GraceKelly), `{type:"disabled"}` на механических. Реальные токены usage
(`response.usage.input_tokens/output_tokens`) захватываются в store (§3.9) — GraceKelly их не
возвращает, поэтому токен-метрики есть только на этом пути.

**GraceKelly (опция, `llm/gracekelly.py`)** — локальный multi-model API (FastAPI,
`http://127.0.0.1:8011`), уже в проде у других интеграторов (RAG_Support_Assistant,
agent_toolkit, juhub); полезен, если нужен общий каталог моделей/логирование нескольких
проектов через один сервис.

```http
POST http://127.0.0.1:8011/orchestrate
{
  "prompt": "<system+context+task>",
  "model": "claude-sonnet-4-6",
  "reasoning": true,          // = thinking
  "decompose": false,         // оркеструем сами, декомпозиция GK не нужна
  "session_id": "<auto_bi session>",   // chaining диалога
  "metadata": {"trace_id": "...", "app": "auto_bi"}
}
```

Констрейнты GraceKelly и как мы с ними живём (устройство репэйр-петли/context selection ниже
общее для обоих провайдеров — ни один из них не поддерживает tool-use/кэширование):

| Констрейнт | Решение в Auto_BI |
|---|---|
| `prompt` ≤ 40 000 символов (GraceKelly; Anthropic — практический лимит контекст-окна) | Context selection: при большом DM в промпт идут только релевантные таблицы (keyword/embedding match по описанию запроса) + компактный текстовый формат модели |
| Text-in/text-out, нет tool-use (оба провайдера) | Структурированный вывод: JSON-блок в ответе → pydantic-валидация → repair loop (фидбек ошибки в LLM, max 3) |
| Нет prompt caching (оба провайдера) | Семантический контекст компактный by design; чарты одного дашборда генерируются батчем в одном вызове, не по одному |

Все вызовы логируются в store: prompt hash, латентность, объём (символы у обоих; токены — только
Anthropic), статус валидации.

### 3.7 UI

Три входных режима, один пайплайн:

- **text-first**: описание дашборда словами (чат) — сходится на GROUNDING;
- **fields-first**: панель всех полей витрин (из семантической модели); поля перетаскиваются в черновые группы «будущих чартов». Это вход, а не конструктор чартов: viz-типы и настройки вручную не выбираются — раскладка уходит в GROUNDING как структурированный seed, LLM возвращает варианты дашборда + анализ раскладки (включая вердикты advisor'а: «такой расклад витриной не предусмотрен — вот почему и вот варианты»);
- **auto-overview**: выбирается одна витрина — детерминированный билдер (БЕЗ LLM) собирает курируемый обзорный дашборд из ролей и кардинальности витрины и сразу отдаёт готовый `DashboardSpec` в APPROVE (минуя GROUNDING/PROPOSE); дальше — общий путь валидации/SQL/сборки и правки словами.

Реализация:
- **v0 (Phase 0–1)**: CLI-чат `auto_bi chat` (rich), только text-first — быстрые итерации без фронта.
- **v1 (Phase 2)**: web — FastAPI + лёгкий фронт: чат, панель полей с drag&drop, превью spec карточками ДО сборки (с вердиктами advisor'а), селектор целевого BI, лог сборки, ссылка на результат. Спокойный белый layout, минимум акцентов, плотная читаемая информация.

**Web UI v1 (задача 2.2, реализовано, text-first)** — `auto_bi/api/static/`: vanilla HTML/CSS/JS без node-цепочки, статика отдаётся самим FastAPI (`/`). Чат + spec-превью карточками (вердикты advisor, scope нативных фильтров — какие чарты затронет) + SSE-лог сборки + ссылка + список dm_change_requests; итерации через правки словами (задача 2.4: APPROVED → правка → APPROVE → пересборка, SSE-буфер сбрасывается на новую сборку). **Селектор целевой BI (Phase 4 F8, разблокирован фабрикой F1):** активен с опциями Superset/DataLens, выбор уходит в `POST /sessions {target_bi}` и фиксируется началом сессии (как режим text/fields — после старта `disabled`), превью показывает `· {target_bi}`, сборка диспетчится в выбранную BI. Ручная проверка фронта без LLM/стенда: `scripts/dev_ui_server.py` (его fake-builder отражает `spec.target_bi` в логе/URL).

**Fields-first (задача 2.3, реализовано)** — второй вход в тот же `POST /sessions` (D8 соблюдён, отдельного пайплайна нет): `auto_bi/agent/seed.py` (`FieldsSeed` = черновые группы полей + комментарий; детерминированная валидация по модели ДО LLM — панель строится из `GET /api/v1/model/fields`, неизвестное поле = 422, не clarify) → `render_seed_request` рендерит seed в текстовый запрос с ролями полей и advisory-инструкцией («группа = черновик одного чарта, viz выбирает LLM»), GROUNDING/PROPOSE потребляют его без изменения шаблонов промптов; таблицы seed пинятся в context selection (раскладка и есть запрос). UI: вкладка «Полями», панель полей по таблицам (role-бейджи T/D/M), HTML5 drag&drop в группы + клик-фоллбек, после старта сессии оба режима продолжаются одним чатом (clarify/правки/итерации). **Отклонение от исходного §3.7 (решение 2026-06-13):** вместо «вариантов дашборда» — один spec (как text-first) + **детерминированный анализ раскладки** (`seed_analysis`, зеркало D5: код сравнивает seed и spec — какие поля групп не вошли, сколько групп дало сколько чартов; строки идут в `AgentTurn.notes`, web UI рендерит их в превью, CLI — в message). Live-гейт: fields-first golden-кейсы f1/f2 в eval-сьюте, прогнаны через живой GraceKelly (PASS) вместе со спот-чеком текстовых.

**Auto-overview (третий вход, реализовано)** — `auto_bi/agent/autospec.py::build_auto_spec(model, table, *, max_charts, target_bi)`: детерминированный билдер курируемого обзорного дашборда из витрины, **без LLM** (не добавляет зависимость от GraceKelly, не упирается в prompt/eval-гейт). Принципиально это НЕ «все возможные графики» (комбинаторный взрыв, который обнулил бы grounding-by-DM ценность), а фиксированный приоритетный скелет, наполняемый из ролей и `physical.cardinality`: KPI на каждую меру → динамика (line) по time-колонке, **сбитая в читаемый грейн** (день/неделя/месяц по кардинальности оси — 730 дневных точек = шум, не тренд; `time_grain`) → **при ≥2 годах истории вторая линия «Динамика г/г»** (изменение герой-меры год-к-году, `yoy_pct` — проценты; приоритетнее третьего разреза, чтобы доля-вью пережила срез по `max_charts`) → топ-N разрезы (bar) по «хорошим» измерениям (кардинальность в диапазоне, включая атрибуты смежных dim-таблиц через model-edge JOIN) → структура (доля-бар, **не pie** — playbook банит угол/площадь) по самому низкокардинальному → детализация (table). Жёсткие стопы делают это дашбордом, а не свалкой: агрегируются только `role=measure` (или синтетический COUNT для справочной таблицы без мер), id-разрезы без читаемого имени и высококардинальные отбрасываются, JOIN — только ребро `model.joins` (инвариант 2). Отдаёт обычный `DashboardSpec` → тот же `validate → normalize (label-join + top-N) → SQL-guard → adapter`; инварианты 1–8 не затрагиваются. **Входы:** CLI `auto_bi build --auto <table> [--max-charts N]`; web UI вкладка «Авто» + `POST /api/v1/sessions/auto` → `AgentSession.adopt_spec` (садит готовый spec прямо в APPROVE без вызова LLM) → дальше approve/build/итерации как у остальных режимов. Advisor-вердикты в авто-режиме не нарративятся (нарратив требует LLM) — детерминированные находки доступны в CLI-пути.

**Enrichment UI (задача 2.7, реализовано)** — gaps report как first-class workflow в web UI (§3.2): секция «Качество модели» в правой панели — `GET /api/v1/model/gaps` (offline-чеки `find_gaps`; live-пробы грануляции остаются в CLI `auto_bi gaps`), инлайн-редакторы описаний таблиц/колонок и роли/agg колонок прямо в findings, save → `PATCH /api/v1/model/tables/{t}[/columns/{c}]` → запись в model.yaml (commit в git — руками, файл версионируется). Запись включается параметром `create_app(model_path=…)` (`auto_bi serve` прокидывает свой `--model-path`; без него PATCH = 503, чтение gaps работает). Валидация правок: role ∈ time/dimension/measure, agg только при role=measure (явный agg на не-мере = 422, уход с measure сбрасывает agg — зеркало F9). Правки модели видны СЛЕДУЮЩЕМУ grounding-вызову тех же сессий (один объект модели, мутация под lock) — by design: enrichment и есть улучшение grounding.

**HTTP API (задача 2.1, реализовано)** — `auto_bi/api/`, тонкий слой над agent core (ядро HTTP не знает); запуск `auto_bi serve`. Все коллабораторы (model/llm/advisor/store/builder) инжектируются в `create_app` — тесты идут на скриптованном LLM и фейковом builder'е. Endpoints (`/api/v1`): `POST /sessions` (start → TurnResponse: phase/questions/spec/verdicts), `POST /sessions/{id}/reply` (ответы clarify и правки словами), `POST /sessions/{id}/approve` (202; сборка в фоне), `GET /sessions/{id}/events` (SSE: `log`-шаги compile_and_build, терминальные `done`/`error`; события буферизуются — поздний подписчик получает replay), `GET /sessions/{id}` (фаза + статус сборки). **Абсолютная ссылка на дашборд (F-1, 2026-07-07):** адаптеры возвращают BI-относительный `ref.url`, а относительный href в UI резолвился бы против хоста Auto_BI (`:8200` vs `:8088`) → 404; `create_app(bi_base_urls={TargetBI: base})` (wiring из `settings.superset_url`/`datalens_url` в `cli.py::_serve`) склеивает базу выбранной BI в `dashboard_url` и `done.url` той же конвенцией `base.rstrip("/") + url`, что CLI-пути. Контракт ошибок: неудачная правка = 200 с `error` и прежним spec (сессия живёт, зеркало F6); 404/409 — только протокольные ошибки (unknown session / wrong phase); 503 — builder не сконфигурирован. Store потокобезопасен (одно соединение + lock): пишут threadpool-хендлеры и build-поток.

### 3.8 Store

SQLite (одна машина, один пользователь — достаточно до Phase 4): `sessions`, `messages`, `specs` (версии), `builds` (статус, лог, URL), `llm_calls`, `dm_change_requests`, `trace_events` (§3.9). Миграция на Postgres только если появится мульти-юзер. Версия схемы в `PRAGMA user_version`; апгрейд существующей БД — идемпотентным `_migrate()` при открытии (v2: новые колонки `llm_calls` + `trace_events`).

### 3.9 Observability (Phase 4)

Трейс шагов агента на сессию + дашборд расходов LLM — две половины, обе поверх Store, без внешних систем (Grafana/OTel избыточны для single-user §1.1).

- **Трейс шагов** — таблица `trace_events` (`session_id, seq, kind, status, latency_ms, detail`): машина агента (`agent/machine.py`) пишет одно событие на шаг (`grounding/clarify/propose/patch/advisor/approve`) с таймингом и исходом (`ok`/`error` + текст исключения), путь сборки (`api/app.py`) — `build_start`/`build_done`/`build_error`. `seq` упорядочивает шаги внутри сессии (created_at слишком груб). Трейсинг **best-effort**: сбой записи никогда не роняет пайплайн (как и логирование LLM-вызовов).
- **Расходы LLM** — дашборд строится на измеримом: число вызовов, успех/ошибки, латентность (всего/сред./макс), объём промпта и **ответа** (`completion_chars` — поле `llm_calls`, `len(output_text)`). Объёмы в символах — **универсальный size-прокси** (есть у каждого вызова). **Реальные токены (E2, v5):** Anthropic Messages API отдаёт `usage.input_tokens/output_tokens` → захватываются в `llm_calls.input_tokens/output_tokens` (nullable; NULL = провайдер usage не вернул). GraceKelly usage **не возвращает** (контракт §3.6) → его строки остаются NULL, символы — единственный сигнал. `llm_usage_summary` суммирует токены NULL-игнорируя + считает `token_calls` (вызовы с реальными токенами), поэтому UI показывает токен-ячейки только когда они есть, не выдавая NULL-обнулённый 0 за измеренное. Каждый LLM-вызов несёт `step` (`grounding/propose_spec/patch_spec/narrate_advisor`) → разбивка по шагу агента.
- **API**: `GET /api/v1/sessions/{id}/trace` (durable timeline + LLM-вызовы сессии + per-session summary, читается прямо из Store — переживает выселение из in-memory реестра); `GET /api/v1/observability/llm` (глобальные агрегаты; 503 без Store).
- **UI**: сворачиваемая панель «Наблюдаемость» в правой колонке (зеркало DCR/gaps-секций) — stat-grid расходов + разбивка по шагам + таймлайн текущей сессии; обновляется после каждого хода и сборки.

Токен-учёт **реализован** на Anthropic-пути (аддитивные nullable-колонки `llm_calls`, без смены модели данных — ровно как и планировалось). **$-стоимость — осознанный non-goal:** требует поддерживаемой таблицы цен (model → $/Mtok), которая дрейфует и провайдер-специфична; токены — дрейф-устойчивая правда, $ тривиально считается поверх них, когда появится владелец цен.

### 3.10 Insight-слой «Что видно» (2026-06-26)

Детерминированный read-only проход поверх **построенного** дашборда: `auto_bi/agent/insights.py::analyze_spec(spec, model, run_query)` гоняет SQL каждого чарта один раз (тот же `generate_chart_sql`, тот же read-only `RunQuery`-шов, что у интроспекции/guard'а/Advisor'а) и превращает **реальные агрегаты** в несколько наблюдений. По временно́му ряду: **тренд** (`% к началу периода, сглажено по первой/последней десятой — один шумный день не переворачивает знак`), **второ-половинный сюжет** — взаимоисключающе либо **разворот** (вторая половина идёт против первой — перелом, который общий тренд скрывает; каждое полупериодное изменение материально и по разные стороны нуля), либо **темп** (`momentum`: то же направление, но наклон явно изменился — рост/снижение ускоряется или замедляется; **порог по наклону, а НЕ по проценту** — линейный рост даёт падающий процент в каждой половине лишь из-за растущей базы и не должен ложно читаться как «замедление»; полу-движения сравниваются по абсолютной дельте, материальность и человеческая подпись — в процентах), **сезонность по дням недели** (будний/выходной профиль: день недели, чья **медиана** сильнее всего отклоняется от общей — напр. выходные выше; плюс самый слабый день, если и его разрыв материален), и единственная самая экстремальная **аномалия** — пик или провал (`выше mean + 3σ и ≥ 2× среднего` либо `ниже mean − 3σ и ≤ ½ среднего`; экстремум на конце растущего ряда не ложно-флагуется именно из-за порога по σ). По рейтингу: **лидер** и — взаимоисключающе — либо его **концентрация** (топ-3 ≥ 50%), либо **ровный разброс** (топ-3 ≤ 40% при ≥ 5 категориях; полоса 40–50% — намеренная мёртвая зона, чтобы рейтинг никогда не назывался одновременно концентрированным и ровным). По структуре: наибольшая **доля** (`share_of_total`). Отвечает на вопрос «что этот дашборд вообще говорит», не заставляя читать каждый чарт глазами. До четырёх наблюдений на чарт (`max_per_chart`), упорядоченных по важности — тренд → второ-половинный сюжет (разворот **или** темп) → сезонность → экстремум, — чтобы срез по лимиту оставлял заголовочные (разворот и темп взаимоисключающи, поэтому добавление темпа не увеличивает максимум на чарт). **Перцентная линия** (`yoy_pct`/`pop_pct` — производная ставка, какую теперь несёт авто-обзор) **не нарративится**: машинерия тренда/экстремума читает уровневый ряд и форматирует величины (рубли), а не проценты, так что «тренд ставки» был бы мутным — такой чарт сам и есть инсайт (доля-BAR — другое: её крупнейшая часть = чистый заголовок).

  **Сезонность — robust by construction:** медиана по дню недели за много недель не сдвигается от одного спайка, а почти равномерная выборка дней недели по периоду не даёт тренду смещать профиль. Молчит, пока КАЖДАЯ строка не несёт распознаваемую дату (`_parse_date`: date/datetime-объекты ИЛИ ISO-строки), каждый учитываемый день недели не покрыт `≥ _SEASON_MIN_SAMPLES`(=6) неделями, и разрыв пика/провала от общей медианы не превысил `_SEASON_MIN_PCT`(=12%); иначе недельное чтение — шум, не находка (нечисловое/нечасовое измерение, короткий ряд, ровный профиль → нет наблюдения).

- **Отдельная поверхность, НЕ внутри дашборда** — операционный дашборд показывает числа и фильтры; нарратив живёт своим слоем (`dashboard-not-presentation`). CLI печатает блок «Что видно» под сборкой (`auto_bi build --auto`); web UI показывает его сворачиваемой секцией под сборкой (`GET /api/v1/sessions/{id}/insights` → 503 без DWH-подключения, пусто пока нет spec; обновляется на `build_done`). Текст наблюдений рендерится через `textContent` (без HTML-инъекции из данных).
- **Без LLM** — факты считает код, RU-проза форматируется детерминированно из этих чисел (нет GraceKelly-зависимости, нет prompt/eval-гейта, вывод воспроизводим). Зеркало Advisor'а (инвариант 5 / D9): код решает, текст лишь излагает решение.
- **Best-effort, advisory** — как у Advisor'а: чарт, чей запрос не выполнился, деградирует в «нет наблюдения», никогда не роняет успешную сборку. Проход не трогает ни один инвариант: читает тот же нормализованный spec, из которого построен дашборд (`apply_label_joins`+`apply_chart_defaults`, оба идемпотентны), и гоняет только read-only SELECT'ы — поэтому SQL инсайтов байт-в-байт совпадает с SQL дашборда (алиасы из общего `measure_alias`/`column_alias` SSOT).
- **Числа** — компактный RU-формат (`236,1 млрд`/`115 млн`/`3,6 тыс`/`842`), доли `40,2%`, знак тренда `+12,3%`/`−4%`; хвостовой `,0` всегда срезается («десятая лишняя»).

### 3.11 Ops-hardening (S07)

Закрывает B-6/B-7/O-3 из `audit_fable_03_07_26.md` — эксплуатационная наблюдаемость поверх однопроцессного деплоя (workers=1, S08), без внешних систем.

- **`GET /api/v1/ready` (B-6)** — `/api/v1/health` доказывает только, что процесс жив; оркестратор (compose healthcheck, Fly checks) должен знать, что store/DWH/BI реально достижимы, ПЕРЕД тем как направить трафик. Проверки: `store` (`Store.ping()` — `SELECT 1` в SQLite), `dwh` (`run_query("SELECT 1")` — тот же read-only `RunQuery`-шов, что у интроспекции/guard'а/Advisor'а), `bi` (`adapter.healthcheck()` на **Superset** — v1 BI-таргет по скоуп-решению 2026-06-11; DataLens вне readiness, это v2/стенд). Каждая невырезанная зависимость (store/run_query/bi_healthcheck не переданы в `create_app`, напр. в юнит-тестах) не гейтит `ok` — репортится как `{"ok": true, "configured": false}`. Ответ: `{"ok": bool, "checks": {...}}`, HTTP 200/503; путь открыт даже при `auth_enabled` (как `/health`).
- **LLM-проверка — опциональная, НЕ гейтит `ok`.** Anthropic — хостед API без отдельного процесса «жив/не жив», а реальный completion-вызов стоил бы токенов на каждый readiness-пинг → без живого вызова (уже доказано конструированием клиента при старте `create_app`/`make_llm`). GraceKelly — локальный сервис (частая причина затыков, см. CLAUDE.md «LLM»), поэтому для него `cli.py::_serve::llm_healthcheck` реально дёргает `GET {gracekelly_url}/health` с таймаутом 3с. Транзиентный сбой LLM не должен ронять готовность — уже построенный дашборд продолжает обслуживаться.
- **Структурные логи (O-3)** — `auto_bi/logging_setup.py::configure_logging(level, format)`: один stdout-хендлер, `text` (по умолчанию, для локальной консоли) или `json` (по объекту в строку — формат, который ждут ELK/Loki/CloudWatch). `auto_bi serve --log-level/--log-format` — единственная точка входа; при `--log-format json` `uvicorn.run(..., log_config=None)` отключает собственный dictConfig uvicorn, так что его `uvicorn`/`uvicorn.access`-логгеры всплывают в тот же настроенный root-логгер — один консистентный JSON-поток на весь процесс, а не два разных формата.
- **Durable failed-build запись (B-7).** Раньше `compile_and_build` писал `builds`-строку `failed`+`sessions.status=failed` только при падении `adapter.build()` — ошибка нормализации/`validate_spec`/SQL-guard/healthcheck ДО этой точки пропадала без следа. Теперь весь конвейер (label-joins → top-N → validate → SQL-guard → healthcheck → build) идёт под одним `try/except`: сессия помечается `building` в начале, любое исключение пишет `failed`-билд + статус сессии `failed`, успех пишет `ok`/`built`. Это чинит случай «процесс жив, билд упал» для ЛЮБОГО источника ошибки (CLI и API — общий код). Случай «процесс убит посреди билда» (SIGKILL/OOM — daemon build thread в `api/app.py` умирает вместе с процессом, `try/finally` в потоке не успевает отработать) чинится отдельно: `Store.reap_stuck_builds()` при старте `auto_bi serve` находит сессии, застрявшие в `building` с прошлого запуска, дописывает им синтетическую `failed`-строку в `builds` (`error="interrupted: process restarted while build was in-flight"`) и переводит сессию в `failed` — так рестарт никогда не теряет факт, что билд оборвался.

### 3.12 CI-integration stand (S09)

Закрывает T-1 из `audit_fable_03_07_26.md` — раньше adapter-регрессии всплывали только когда кто-то вручную поднимал Mac-стенд; теперь есть автоматический сигнал на каждый push/PR.

- **Отдельный job `integration`** (`.github/workflows/ci.yml`, параллельно `quality`, ubuntu-latest) поднимает **тот же** `docker-compose.yml`, что и Mac-стенд, но с `DEMO_FACT_ROWS=1000000` (вместо дефолтных 100M) — этого достаточно, чтобы каждый `viz_type`/адаптер получил реальные строки из ClickHouse, но не съедает время/RAM раннера.
- **Warm-up = существующие healthchecks, не отдельная retry-обвязка.** `docker compose up -d --build --wait --wait-timeout 900` блокируется до `service_healthy` обоих сервисов — ретраи те же, что уже описаны в `docker-compose.yml` (CH: 30×10с + `start_period` 60с; Superset: 30×15с + `start_period` 90с, `depends_on: condition: service_healthy` на CH). Отдельного polling-скрипта не заводили — дублировал бы уже рабочую конфигурацию.
- **Что гоняется живьём:** `pytest -m integration tests/test_superset_contract.py` (create→GET→assert + chart/data + native-filters roundtrip по всем `viz_type`), затем `auto_bi build --auto dm.sales_daily --target superset` — детерминированный auto-overview путь (без LLM, ARCHITECTURE §auto-overview), так что живой E2E-прогон не требует `ANTHROPIC_API_KEY`/секретов вообще. CH/Superset-пароли — дефолты `docker-compose.yml` (`change_me`), достаточно для эфемерного, непубликуемого CI-стенда.
- **DataLens вне скоупа этого job'а** (в отличие от `test_superset_contract.py`) — самостоятельный self-hosted стенд с ручным реверс-инжинирингом (см. §3.5/`docs/plans/2026-06-13-datalens-selfhosted-runbook.md`), в `docker-compose.yml` не описан; `test_datalens_contract.py` по-прежнему Mac-only.
- Стенд гасится (`docker compose down -v`) в `if: always()`-шаге; логи (`docker compose logs`) печатаются на неудаче для диагностики.

### 3.13 Docker + release-конвейер (S10)

Закрывает D-1/D-2/T-3 из `audit_fable_03_07_26.md` — до этого `version` не двигался с основания репозитория, `git tag`/GitHub Releases были пусты, Dockerfile ни разу не собирался, а coverage-бейдж в README был статичным числом, вручную правившимся (и потому дрейфовавшим — D-3).

- **`docker` job** (`ci.yml`, параллельно `quality`/`integration`) собирает образ на каждый push/PR — только сборка, без push/login в реестр. Ловит дрейф `Dockerfile` (несовпадение с текущим `pyproject.toml`/кодом) на каждом PR, а не только в момент фактического релиза.
- **`release.yml`** — отдельный workflow, триггер только на push тега `vX.Y.Z` (не на обычные коммиты — вырезание релиза остаётся отдельным осознанным действием). Собирает образ через `docker/build-push-action`, пушит в GHCR (`ghcr.io/<repo, lowercase>:<version>` + `:latest`) через `GITHUB_TOKEN` (без отдельных секретов), затем вырезает из `CHANGELOG.md` секцию `## [<version>]` (`awk` между этим заголовком и следующим `## [`) и создаёт GitHub Release с этим текстом как телом (`softprops/action-gh-release`) — тело релиза не дублирует CHANGELOG вручную.
- **`auto_bi --version`** (`argparse` `action="version"`, печатает `auto_bi.__version__` и выходит до проверки `required=True` у сабпарсера — стандартное поведение argparse, как `git --version`) и **поле `version` в `GET /api/v1/health`** — оба читают один и тот же `auto_bi/__init__.py::__version__`, единственный источник версии (правится вручную при бампе; CI ничего не бампает автоматически — semver-решение остаётся человеческим).
- **Coverage-бейдж генерируется CI, не правится руками.** Шаг `Coverage badge data` в `quality`-job читает `.coverage`, оставленный только что отработавшим pytest-cov (`coverage report --format=total`), и пишет `.github/badges/coverage.json` в [shields.io endpoint-схеме](https://shields.io/badges/endpoint-badge); README ссылается на этот файл через `img.shields.io/endpoint`. Коммит файла обратно в `main` — отдельный шаг, гейтится `if: github.event_name == 'push' && github.ref == 'refs/heads/main'` (никогда на PR, в т.ч. из форков без прав на запись) и пропускается, если значение не изменилось (`git diff --quiet`); коммит помечен `[skip ci]`, чтобы не плодить повторный CI-прогон на идентичном результате. `quality`-job получил `permissions: contents: write` только под этот шаг (workflow-дефолт остаётся `contents: read`).
- **CHANGELOG.md** — формат [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/): секция `## [Unreleased]` наверху, версии ниже в обратном хронологическом порядке. Бампается вручную вместе с версией — не генерируется из git-лога (коммиты этого проекта не следуют Conventional Commits достаточно строго для надёжной автогенерации).
- **PyPI (X-2, подготовлено).** Job `pypi` в `release.yml` (параллелен docker/GH-release-job'у, независим от него) публикует sdist+wheel через [Trusted Publishing](https://docs.pypi.org/trusted-publishers/) — OIDC (`permissions: id-token: write`, `environment: pypi`, `pypa/gh-action-pypi-publish`), долгоживущий токен в секретах не нужен вовсе. Дистрибутив называется **`autobi-agent`** (`pip install autobi-agent`), импортируемый пакет и консольная команда остаются `auto_bi`. Имя `auto-bi` PyPI отвергает («too similar to an existing project»): при сравнении имён разделители схлопываются, поэтому `auto-bi` конфликтует с уже существующим пакетом-заглушкой `autobi` — прежняя проверка «свободно» (2026-07-07) смотрела только точное совпадение и была неверна. Метаданные пакета (license/authors/classifiers/urls/keywords) — в `pyproject.toml`; `uv build` + `twine check` PASS, wheel ставится в чистый venv (`auto_bi --version` работает). Trusted publisher (project `autobi-agent`, owner `brownjuly2003-code`, repo `Auto_BI`, workflow `release.yml`, environment `pypi`) зарегистрирован на pypi.org 2026-07-10 — тег публикует пакет без дополнительных шагов.

### 3.14 Golden-eval record/replay (S11, механизм)

Начинает закрывать T-2 из `audit_fable_03_07_26.md` — golden-сьют (§1.11) диалоговый, гоняется через реальный `LLMClient` и раньше запускался только вручную с живым провайдером; в CI шла только детерминированная advisor-часть (`quality`-job, §3.6/invariant 8).

- **Контракт** — `auto_bi/llm/fixture.py`: `FixtureLLMClient` (replay) и `RecordingLLMClient` (record) оба структурно удовлетворяют протокол `LLMClient` (`complete(prompt, schema, ...) -> T`), поэтому подключаются на ТОМ ЖЕ шве, что `GraceKellyClient`/`AnthropicClient`, без нового интерфейса ни в `agent/`, ни в `eval/runner.py`. Один JSON-файл на кейс, `<fixtures_dir>/<case_id>.json`: `{"case_id": "...", "calls": [{"step": "...", "schema": "...", "response": {...}}]}` — `calls` повторяет ровно последовательность вызовов `complete()`, которые делает кейс (grounding → propose_spec → опционально patch_spec для итераций).
- **Кто знает текущий кейс** — общий `llm`-объект один на весь прогон сьюта (`run_golden_suite` передаёт один инстанс во все кейсы), поэтому `eval/runner.py::run_golden_case` дак-тайпингом вызывает `begin_case(case_id)` перед кейсом и `end_case()` после (в `finally`, кейс упал — сработает тоже) — `GraceKellyClient`/`AnthropicClient` этих методов не имеют, `getattr(..., None)` их не задевает.
- **Расхождение — громкая ошибка, не тихий повтор.** Если код/промпт с момента записи фикстуры стал звать `complete()` иначе (другой `step`/`schema`, лишний или недостающий вызов), replay бросает `FixtureMissingError` с именем кейса и номером вызова вместо того, чтобы подсунуть кейсу чужой ответ — так регрессия видна как явный CI-фейл, а не как случайный false green.
- **Что replay ловит, а что нет.** Оффлайн-replay проверяет детерминированную обвязку вокруг LLM-вызова (валидация spec, генерация SQL, advisor, адаптеры, все ассершены `eval/runner.py`) на каждом PR бесплатно и без флейков. Он **не** ловит живую деградацию модели/промпта — правка промпта, из-за которой модель стала отвечать иначе на тот же запрос, реплею не видна (он вообще не спрашивает модель). Это по-прежнему требует отдельного живого прогона (ручная сессия сейчас; опциональный weekly-job с ключом — следующий шаг).
- **CLI** — `auto_bi eval --suite golden --llm-mode {live,replay,record} --fixtures-dir DIR` (дефолт `live`, старое поведение не меняется без флага). `record` оборачивает настроенный `make_llm(...)` в `RecordingLLMClient` и пишет фикстуры для дальнейшего replay; `replay` не создаёт `LLMClient` через фабрику вовсе — офлайн, без провайдера/ключа.
- **Состояние на 2026-07-06 (P2, закрыто):** реальные фикстуры записаны через GraceKelly (владелец выбрал этот метод) для ПОЛНОГО текущего golden-сьюта — 37 CH-кейсов (`semantic/model.yaml`) + 16 GP-кейсов (`semantic/model_gp.yaml`), 53 файла в `tests/fixtures/golden_llm/` (id-неймспейсы не пересекаются — CH `g*/it*/f*/a*/i*` vs GP `gp_*` — обе модели делят один каталог фикстур). Офлайн `--llm-mode replay` подтверждён 37/37 + 16/16 PASS на обеих моделях; `replay` структурно не может дёрнуть сеть — CLI-путь вообще не создаёт `LLMClient` через фабрику в этом режиме (`cli.py::_eval`), так что «офлайн» — гарантия кода, а не наблюдение за логом. `quality`-job (`.github/workflows/ci.yml`) получил два шага сразу после advisor-сьюта: replay CH и replay GP, оба без секретов/ключей. Транзиентная браузер-флейковость GraceKelly (Perplexity UI, `Locator.click timeout` / разрыв соединения на середине прогона) ловилась точечным повтором проваленных кейсов (`--cases id1,id2,...`) — ни один кейс не потребовал больше одной повторной попытки.

### 3.15 Session-resume после рестарта (X-4)

Закрывает P-4 полностью (S08 закрывал его документационно): реестр `ManagedSession` остаётся process-local, но перестаёт быть process-bound — промах реестра в `SessionManager.get()` лениво **регидрирует** сессию из её durable-записи в Store. Рестарт `auto_bi serve` (и eviction за `MAX_SESSIONS`) больше не теряет диалоги.

- **Ленивая гидрация, не eager-скан при старте.** Старый процесс мог накопить тысячи сессий — грузить их все в память на старте бессмысленно (реестр всё равно ограничен `MAX_SESSIONS`). Вместо этого воскрешается ровно та сессия, которую клиент реально адресовал; гонка двух одновременных `get()` по одному id разрешается двойной проверкой под registry-lock (проигравшая копия отбрасывается до того, как кто-либо мог взять её lock).
- **Schema v7** — `sessions` получил `owner` (username при включённом auth, NULL иначе), `target_bi` (выбор BI на сессию, раньше жил только в памяти) и `pinned` (JSON-массив seed-таблиц) — три куска состояния, которые невозможно восстановить из messages/specs/builds. Legacy-строки бэкфиллятся безопасными дефолтами (NULL/'superset'/'[]').
- **Фаза выводится из spec-строк**: последняя строка `approved` → APPROVED, любая другая → APPROVE (правки словами дописывают `proposed`-строки, так что последняя строка И ЕСТЬ текущий spec; auto-overview-сессии без user-message тоже покрыты — label сессии заменяет запрос). Spec-строк нет → сессия ещё уточнялась: CLARIFY восстанавливается только если хотя бы один clarify-раунд реально дошёл до пользователя (`trace_events`); `_clarify_rounds` = число clarify-событий (кап `MAX_CLARIFY_ROUNDS` переживает рестарт), ответы на уточнения = все user-messages после первого (word edits существуют только при наличии spec). Сессия, умершая до первого ответа агента, не воскрешается — клиент её id и не получал (F2).
- **Билд-состояние из `builds`**: последняя строка `ok` → `built` + `dashboard_url` (pipeline хранит BI-относительный url — гидрация заново приклеивает базу из `bi_base_urls`, та же конвенция F-1), `failed` → `failed`; APPROVED без builds-строки (процесс умер в окне approve→build; mid-build случай закрывает `reap_stuck_builds`, §3.11) → синтетический `failed`, чтобы повторный approve пошёл retry-путём, а не в 409. SSE-буфер сеедится синтетическим терминальным событием (`done`/`error`) — поздний читатель `/events` получает закрытие потока, а не вечные heartbeat'ы.
- **RBAC переживает рестарт**: owner из v7-колонки; модель при гидрации заново скоупится `filter_model_by_schemas` по `allowed_schemas` владельца из `users`. Legacy-сессии (owner=NULL) при включённом auth доступны только админу — безопасный дефолт, ничья сессия не «достаётся» случайному аналитику. approve-гейт `forbidden_tables` действует как и раньше (defense in depth).
- **DELETE = tombstone.** `manager.remove` теперь помечает durable-запись `status='deleted'` — иначе ленивая гидрация воскрешала бы удалённую сессию следующим GET и DELETE стал бы no-op. Строки (messages/specs/builds/trace) остаются: delete делает сессию неадресуемой, не незаписанной.
- **Что осознанно НЕ восстанавливается** (регенерируется следующим ходом): grounding report (CLARIFY-ответ всё равно перезапускает grounding), вердикты Advisor (следующий propose/patch пересчитает; дедуп DCR-заявок при этом восстановлен из `dm_change_requests` — повторных заявок на ту же находку не будет), layout-анализ seed'а (его rendered-текст уже в `_request`, pinned-таблицы сохранены для context selection).

## 4. Безопасность

- DWH: отдельная **read-only роль**, видит только DM-схемы.
- SQL-guard: sqlglot-парсинг, только `SELECT`, запрет DDL/DML/множественных стейтментов; timeout; принудительный `LIMIT` на валидационных прогонах.
- LLM получает: схему и метаданные всегда; любые **значения данных** (top-N значений низкокардинальных колонок, в будущем — сэмплы строк) — только под флагом `AUTO_BI_SEND_SAMPLES` (default `true`, т.к. GraceKelly локальный и трафик идёт только в Anthropic API; `false` для чувствительных DM убирает значения из промпта — `render_model(include_samples=False)`). Значения остаются в локальном `semantic/model.yaml` (артефакт под контролем оператора); флаг управляет только отправкой в LLM.
- BI: сервисный аккаунт с правами только на выделенную папку/workspace «Auto_BI».
- Секреты — `.env` (в `.gitignore`), никогда в коде/логах/доках.
- **Auth-хардening (S06, закрывает B-2/B-3/B-4):**
  - Login-cookie `Secure` — `AUTO_BI_AUTH_COOKIE_SECURE` (tri-state: unset = auto, `Secure` включается сама, если сервер НЕ забинжен на loopback-хост; явный `true`/`false` форсирует). `create_app(cookie_secure=...)` — единственная точка, где флаг применяется к `response.set_cookie`.
  - Rate-limit на `/api/v1/auth/login` — in-process `LoginRateLimiter` (`api/ratelimit.py`): скользящее окно 5 попыток/60с на IP-ключ; при превышении — lockout, который растёт экспоненциально с каждым повторным нарушением (30с → 60с → 120с …, потолок 15 мин), strikes не сбрасываются, пока ключ активен. In-memory, потому что деплой однопроцессный (workers=1 — S08 обоснование, in-memory sessions/SSE). **L-4:** ключи, молчащие дольше `max(окно, потолок lockout'а)`, вычищаются амортизированным сканом (не чаще раза в минуту, piggyback на `check()`) — публичная экспозиция не растит память процесса с каждым уникальным IP; сброс strikes при этом требует молчать не меньше максимального lockout'а, т.е. атакующему ничего не даёт.
  - **Per-IP ключи за reverse-proxy (F-2):** и login-лимитер, и LLM-квота O-2 ключуются по `request.client` — за прокси это адрес прокси, и без проброса реального IP все клиенты вырождаются в один общий bucket. `auto_bi serve` всегда включает uvicorn `proxy_headers` (rewrite `request.client` из `X-Forwarded-For`), доверяя по умолчанию только loopback-пиру; для контейнерного прокси — `AUTO_BI_FORWARDED_ALLOW_IPS` (адреса прокси или `*`, только когда порт приложения не опубликован наружу). Примеры/гочи — DEPLOYMENT §3/§5/§8.
  - Токены — `auth_tokens.token` теперь хранит `sha256(raw_token)` hex, не сырой bearer-токен (миграция схемы v6, гейтится по форме значения `_TOKEN_HASH_RE`, так что повторный проход никогда не хэширует уже хэшированное). `create_token`/`token_user`/`delete_token` хэшируют на входе — вызывающий код (`app.py`) не меняется, работает с сырым токеном как раньше.
  - Периодический purge протухших токенов — daemon-поток в `cli.py::_serve` (раз в час, только когда `auth_enabled`), вызывает уже существовавший `Store.purge_expired_tokens()`. Чистая механика — `token_user` и так игнорирует протухшие строки; поток лишь не даёт таблице расти бесконечно.

## 5. Решения (ADR-кратко)

| # | Решение | Почему |
|---|---|---|
| D1 | IR-first: LLM → DashboardSpec → детерминированные компиляторы | См. §2; единственный способ сделать мульти-BI поддерживаемым |
| D2 | Superset — первый таргет; DataLens — второй | Superset: OSS-стандарт RU + полный бесплатный API + локальный docker. DataLens: лидер self-service RU, Public API подтверждён |
| D3 | LLM через `LLMClient`-абстракцию, дефолт-провайдер прямой Anthropic Messages API (S02); GraceKelly (`claude-sonnet-4-6`, `reasoning: true`) — документированная опция (`AUTO_BI_LLM_PROVIDER=gracekelly`) | Внешний пользователь ставит ключ и работает без стороннего сервиса — воспроизводимость важнее; GraceKelly остаётся для тех, у кого он уже обкатан (общий каталог моделей/логирование); ограничения обоих закрыты LLMClient-абстракцией |
| D4 | Semantic model = YAML в git | Ревью, версионирование, diff правок; не прячем семантику в БД |
| D5 | Разделение труда: LLM думает, код делает | Валидация, компиляция, API-вызовы, перфоманс-вердикты — детерминированный код; LLM не трогает нативные форматы BI |
| D6 | Stack: Python 3.12 + uv, FastAPI, pydantic v2, sqlglot, httpx; без LangChain/LangGraph | Диалог — простая state machine, тяжёлые фреймворки не окупаются |
| D7 | Демо-DM на ClickHouse (синтетическая звезда sales/stores/products, MergeTree с осмысленным sorting_key) в docker-compose | Нужен для разработки, contract-тестов, eval и анти-паттерн-кейсов advisor'а независимо от реального DWH |
| D8 | Три UI-входа (text-first / fields-first drag&drop / auto-overview по витрине), один пайплайн | Раскладка полей — seed для GROUNDING, не отдельный конструктор чартов; авто-обзор — детерминированный spec прямо в APPROVE; отдельного пайплайна нет |
| D9 | Advisor: детекция = EXPLAIN (универсально) + rule pack per engine (объяснения); вердикт выносит код, LLM формулирует; advisory-only | Не перечисляем «сценарии»: движок сам оценивает стоимость, правила покрывают классы-механизмы, LLM не выдумывает перфоманс-факты; сборку не блокирует — решает пользователь |
| D10 | Определённая история: RU-рынок, v1 = ClickHouse + Superset; универсальность только в швах | Универсальность — главный множитель стоимости; глубокий CH-advisor ценнее мелкого универсального; PBI/Tableau ушли с рынка RU |

## 6. Риски

| Риск | Митигация |
|---|---|
| DM без описаний → мусорный grounding и уточнения | Gaps report обязателен; enrichment workflow first-class; dbt-импорт |
| `form_data` Superset меняется между версиями | Пин версии, библиотека шаблонов, contract-тесты на каждый viz_type |
| 40k-лимит промпта GraceKelly на больших DM | Context selection по релевантности; компактный формат модели |
| Warning fatigue: advisor шумит на каждый чих | Severity-уровни, пороги по размеру таблиц, агрегация однотипных findings; critical наверх, info свёрнуто |
| Соблазн «прописать все сценарии» в правилах | Не делаем: детекция через EXPLAIN универсальна; rule pack = механизмы (8–12 на движок), пополняется по реальным false-negative из практики |
| DataLens Public API в Preview | Спайк 2–3 дня перед адаптером; адаптер v2, не v1 |
| Яндекс закроет нишу (Нейроаналитик эволюционирует в NL→dashboard) | Скорость: рабочий продукт после Phase 2 (~2–3 мес); дифференциация в advisor и независимости от одной BI |
| Латентность/стоимость thinking-вызовов | Батч-генерация чартов одним вызовом; reasoning только на GROUNDING/PROPOSE_SPEC, лёгкие шаги — без reasoning |
| LLM галлюцинирует поля | Жёсткая валидация spec против модели, reject + repair loop, никогда «починить молча» |
| Качество диалога деградирует при правках промптов | Eval-сьют golden-кейсов (см. PLAN, Phase 1) гоняется перед каждым merge промпт-изменений |
