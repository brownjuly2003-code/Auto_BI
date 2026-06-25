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
- **Замена id-измерений на названия через join (B3, 2026-06-15)**: детерминированный IR-трансформ `agent/normalize.py::apply_label_joins(spec, model)` в `compile_and_build` (ДО B1/валидации/SQL/адаптера, поэтому чинит ОБА BI из одного места) заменяет dimension-роль, являющуюся сырым FK-id (`store_id` с `Column.fk`), на человекочитаемую name-колонку целевой таблицы через LEFT JOIN (`store_id` → `dm.stores.name`). **Lossless-by-construction**: swap только когда `physical.cardinality` доказывает, что name ~уникально на id (`label_card ≥ 0.99·id_card`; нет cardinality-доказательства → нет swap) — иначе id остаётся, никаких молчаливых слияний строк (correctness-тренодофф, на котором B3 был gated). Только IR-уровень: оба адаптера уже рендерят joined-измерения (Phase 3 контракт-тесты) → правок адаптеров нет. Добавляемый join зеркалит ребро модели (инвариант 2); коллизия bare-алиасов / уже-квалифицированный ref / non-FK измерение → no-op (transform никогда не отдаёт spec, который не пройдёт `validate_spec`). Свапаются dimensions/series/rows/columns; меры и фильтры держат сырой id; `order_by` по id ремапится. Идемпотентно. Live-verified E2E на DataLens (имена магазинов на оси) + Superset-build на том же SQL; S6 (субагент `code-reviewer`, `fable_audit_b3_label_joins.md`): 0 P1/0 P2/5 P3.

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

### 3.6 LLM Layer — GraceKelly

GraceKelly — локальный multi-model API (FastAPI, `http://127.0.0.1:8011`), уже в проде у других интеграторов (RAG_Support_Assistant, agent_toolkit, juhub).

Вызов:

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

Констрейнты и как мы с ними живём:

| Констрейнт GraceKelly | Решение в Auto_BI |
|---|---|
| `prompt` ≤ 40 000 символов | Context selection: при большом DM в промпт идут только релевантные таблицы (keyword/embedding match по описанию запроса) + компактный текстовый формат модели |
| Text-in/text-out, нет tool-use | Структурированный вывод: JSON-блок в ответе → pydantic-валидация → repair loop (фидбек ошибки в LLM, max 3) |
| Нет prompt caching | Семантический контекст компактный by design; чарты одного дашборда генерируются батчем в одном вызове, не по одному |

Абстракция `LLMClient` (protocol: `complete(prompt, schema) -> ValidatedModel`): GraceKellyClient — первая реализация; если упрёмся в tool-use/кэширование — добавляется прямой AnthropicClient без изменения агента.

Все вызовы логируются в store: prompt hash, латентность, объём, статус валидации.

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

**Auto-overview (третий вход, реализовано)** — `auto_bi/agent/autospec.py::build_auto_spec(model, table, *, max_charts, target_bi)`: детерминированный билдер курируемого обзорного дашборда из витрины, **без LLM** (не добавляет зависимость от GraceKelly, не упирается в prompt/eval-гейт). Принципиально это НЕ «все возможные графики» (комбинаторный взрыв, который обнулил бы grounding-by-DM ценность), а фиксированный приоритетный скелет, наполняемый из ролей и `physical.cardinality`: KPI на каждую меру → динамика (line) по time-колонке → топ-N разрезы (bar) по «хорошим» измерениям (кардинальность в диапазоне, включая атрибуты смежных dim-таблиц через model-edge JOIN) → структура (pie) по самому низкокардинальному → детализация (table). Жёсткие стопы делают это дашбордом, а не свалкой: агрегируются только `role=measure` (или синтетический COUNT для справочной таблицы без мер), id-разрезы без читаемого имени и высококардинальные отбрасываются, JOIN — только ребро `model.joins` (инвариант 2). Отдаёт обычный `DashboardSpec` → тот же `validate → normalize (label-join + top-N) → SQL-guard → adapter`; инварианты 1–8 не затрагиваются. **Входы:** CLI `auto_bi build --auto <table> [--max-charts N]`; web UI вкладка «Авто» + `POST /api/v1/sessions/auto` → `AgentSession.adopt_spec` (садит готовый spec прямо в APPROVE без вызова LLM) → дальше approve/build/итерации как у остальных режимов. Advisor-вердикты в авто-режиме не нарративятся (нарратив требует LLM) — детерминированные находки доступны в CLI-пути.

**Enrichment UI (задача 2.7, реализовано)** — gaps report как first-class workflow в web UI (§3.2): секция «Качество модели» в правой панели — `GET /api/v1/model/gaps` (offline-чеки `find_gaps`; live-пробы грануляции остаются в CLI `auto_bi gaps`), инлайн-редакторы описаний таблиц/колонок и роли/agg колонок прямо в findings, save → `PATCH /api/v1/model/tables/{t}[/columns/{c}]` → запись в model.yaml (commit в git — руками, файл версионируется). Запись включается параметром `create_app(model_path=…)` (`auto_bi serve` прокидывает свой `--model-path`; без него PATCH = 503, чтение gaps работает). Валидация правок: role ∈ time/dimension/measure, agg только при role=measure (явный agg на не-мере = 422, уход с measure сбрасывает agg — зеркало F9). Правки модели видны СЛЕДУЮЩЕМУ grounding-вызову тех же сессий (один объект модели, мутация под lock) — by design: enrichment и есть улучшение grounding.

**HTTP API (задача 2.1, реализовано)** — `auto_bi/api/`, тонкий слой над agent core (ядро HTTP не знает); запуск `auto_bi serve`. Все коллабораторы (model/llm/advisor/store/builder) инжектируются в `create_app` — тесты идут на скриптованном LLM и фейковом builder'е. Endpoints (`/api/v1`): `POST /sessions` (start → TurnResponse: phase/questions/spec/verdicts), `POST /sessions/{id}/reply` (ответы clarify и правки словами), `POST /sessions/{id}/approve` (202; сборка в фоне), `GET /sessions/{id}/events` (SSE: `log`-шаги compile_and_build, терминальные `done`/`error`; события буферизуются — поздний подписчик получает replay), `GET /sessions/{id}` (фаза + статус сборки). Контракт ошибок: неудачная правка = 200 с `error` и прежним spec (сессия живёт, зеркало F6); 404/409 — только протокольные ошибки (unknown session / wrong phase); 503 — builder не сконфигурирован. Store потокобезопасен (одно соединение + lock): пишут threadpool-хендлеры и build-поток.

### 3.8 Store

SQLite (одна машина, один пользователь — достаточно до Phase 4): `sessions`, `messages`, `specs` (версии), `builds` (статус, лог, URL), `llm_calls`, `dm_change_requests`, `trace_events` (§3.9). Миграция на Postgres только если появится мульти-юзер. Версия схемы в `PRAGMA user_version`; апгрейд существующей БД — идемпотентным `_migrate()` при открытии (v2: новые колонки `llm_calls` + `trace_events`).

### 3.9 Observability (Phase 4)

Трейс шагов агента на сессию + дашборд расходов LLM — две половины, обе поверх Store, без внешних систем (Grafana/OTel избыточны для single-user §1.1).

- **Трейс шагов** — таблица `trace_events` (`session_id, seq, kind, status, latency_ms, detail`): машина агента (`agent/machine.py`) пишет одно событие на шаг (`grounding/clarify/propose/patch/advisor/approve`) с таймингом и исходом (`ok`/`error` + текст исключения), путь сборки (`api/app.py`) — `build_start`/`build_done`/`build_error`. `seq` упорядочивает шаги внутри сессии (created_at слишком груб). Трейсинг **best-effort**: сбой записи никогда не роняет пайплайн (как и логирование LLM-вызовов).
- **Расходы LLM** — GraceKelly **не возвращает usage/токены/стоимость** (контракт §3.6), поэтому дашборд строится на измеримом: число вызовов, успех/ошибки, латентность (всего/сред./макс), объём промпта и **ответа** (`completion_chars` — новое поле `llm_calls`, `len(output_text)`). Объёмы в символах — это **size-прокси, не токены и не деньги** (так и помечено в UI/доках). Каждый LLM-вызов несёт `step` (`grounding/propose_spec/patch_spec/narrate_advisor`) → разбивка расходов по шагу агента. Агрегаты считает `Store.llm_usage_summary()`.
- **API**: `GET /api/v1/sessions/{id}/trace` (durable timeline + LLM-вызовы сессии + per-session summary, читается прямо из Store — переживает выселение из in-memory реестра); `GET /api/v1/observability/llm` (глобальные агрегаты; 503 без Store).
- **UI**: сворачиваемая панель «Наблюдаемость» в правой колонке (зеркало DCR/gaps-секций) — stat-grid расходов + разбивка по шагам + таймлайн текущей сессии; обновляется после каждого хода и сборки.

Токен/$-учёт отложен: требует, чтобы оркестратор отдавал usage (сегодня не отдаёт) — тогда это аддитивные колонки в `llm_calls` без смены модели данных.

## 4. Безопасность

- DWH: отдельная **read-only роль**, видит только DM-схемы.
- SQL-guard: sqlglot-парсинг, только `SELECT`, запрет DDL/DML/множественных стейтментов; timeout; принудительный `LIMIT` на валидационных прогонах.
- LLM получает: схему и метаданные всегда; любые **значения данных** (top-N значений низкокардинальных колонок, в будущем — сэмплы строк) — только под флагом `AUTO_BI_SEND_SAMPLES` (default `true`, т.к. GraceKelly локальный и трафик идёт только в Anthropic API; `false` для чувствительных DM убирает значения из промпта — `render_model(include_samples=False)`). Значения остаются в локальном `semantic/model.yaml` (артефакт под контролем оператора); флаг управляет только отправкой в LLM.
- BI: сервисный аккаунт с правами только на выделенную папку/workspace «Auto_BI».
- Секреты — `.env` (в `.gitignore`), никогда в коде/логах/доках.

## 5. Решения (ADR-кратко)

| # | Решение | Почему |
|---|---|---|
| D1 | IR-first: LLM → DashboardSpec → детерминированные компиляторы | См. §2; единственный способ сделать мульти-BI поддерживаемым |
| D2 | Superset — первый таргет; DataLens — второй | Superset: OSS-стандарт RU + полный бесплатный API + локальный docker. DataLens: лидер self-service RU, Public API подтверждён |
| D3 | LLM через GraceKelly (`claude-sonnet-4-6`, `reasoning: true`) | Сервис уже есть и обкатан; логирование/retry/каталог моделей бесплатно; ограничения закрыты LLMClient-абстракцией |
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
