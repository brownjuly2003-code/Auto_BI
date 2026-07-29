# Auto_BI — Архитектура

> Этот файл описывает **текущий дизайн**: компоненты, границы и обязательные
> инварианты. Текущий продуктовый статус находится в
> [CURRENT_STATE.md](CURRENT_STATE.md), хронология архитектуры — в
> [ARCHITECTURE_HISTORY.md](ARCHITECTURE_HISTORY.md), фазовый план — в
> [PLAN.md](PLAN.md), принятые решения и последствия — в [adr/](adr/).
> Статус, историю поставки и журнал сессий здесь не дублируем.

## 1. Концепция

Auto_BI строит дашборд из семантической модели витрин:

1. интроспектирует read-only DM-слой DWH;
2. принимает текст, раскладку полей или запрос на автоматический обзор;
3. уточняет только неоднозначный или несовместимый с моделью запрос;
4. формирует BI-независимый `DashboardSpec`;
5. проверяет запросы и предупреждает о дорогих паттернах;
6. компилирует spec в выбранную BI и возвращает ссылку.

LLM отвечает за сопоставление пользовательского намерения с моделью и за
структурированное предложение. Валидация, SQL, performance findings, native BI
payloads, ownership и cleanup принадлежат детерминированному коду.

### 1.1 Скоуп: «спроектировано для N, построено для 1»

- Основной путь: ClickHouse DM + Superset.
- Второй поддержанный DWH-путь: Greenplum/Greengage.
- Второй BI-адаптер: self-hosted open-source DataLens.
- Универсальность живёт в швах `DashboardSpec`, `BIAdapter`, `Introspector` и
  engine-specific advisor rules. Новый движок или BI добавляется реализацией
  соответствующего шва, а не ветвлением agent core.
- Power BI, Tableau, Metabase и другие платформы не входят в текущий scope.

## 2. Ключевое решение: IR-first (Dashboard Spec)

```text
текст / поля / auto-overview
            |
            v
      DashboardSpec
       /          \
      v            v
 SQL + guard   BIAdapter compiler
                    |
              Superset / DataLens
```

LLM не генерирует `form_data`, DataLens blobs или другие native BI-форматы.
Публичная граница между reasoning и исполнением — валидируемый
`DashboardSpec`.

Обязательные инварианты:

1. LLM выдаёт только IR; операторский `raw_sql` — отдельный ручной люк.
2. Все ссылки spec проверяются против `SemanticModel`; несуществующие поля и
   недопустимые связи отклоняются, а не исправляются молча.
3. Любой исполняемый SQL проходит SELECT-only guard, engine parse, `EXPLAIN` и
   ограниченный пробный запуск.
4. Native BI payloads строят только адаптеры; reverse-engineered formats и
   поддержанная версия стенда закрепляются contract tests.
5. Feasibility verdict, normalization и remediation вычисляет код; LLM может
   лишь сформулировать результат.
6. Удаление BI-объектов разрешено только по durable ownership evidence.
7. Remote BI mutation выполняется под durable build attempt и stable token.
8. Изменение prompt/schema surface сопровождается fingerprinted eval fixtures.

## 3. Компоненты

```text
auto_bi/
  introspect/     DWH -> сырые метаданные
  semantic/       SemanticModel, model.yaml, enrichment, gaps
  agent/          state machine, normalization, SQL, pipeline
  advisor/        engine rules и EXPLAIN evidence
  llm/            LLMClient, structured repair, budget
  ir/             DashboardSpec и JSON Schema
  adapters/       Superset и DataLens compilers
  api/            FastAPI, auth, sessions, SSE, metrics
  ui/             CLI и встроенный web UI
  store/          SQLite persistence и recovery state
```

### 3.1 Introspect

`Introspector` работает под read-only ролью и выдаёт черновик
`semantic/model.yaml` вместе с gaps report.

- ClickHouse-путь читает `system.tables`/`system.columns`, engine, sorting key,
  partition key, размер, приблизительную кардинальность и безопасные sample
  statistics.
- Greenplum/Greengage-путь читает PG catalogs, distribution key, partitions и
  `pg_stats`.
- dbt import обогащает пустые descriptions/relationships, но не владеет схемой
  и не перезаписывает ручные правки.
- Каждый engine предоставляет собственный SQL dialect и EXPLAIN seam.

### 3.2 Semantic Model

`model.yaml` — reviewable source of truth для бизнес-семантики:

```yaml
tables:
  - name: dm.sales_daily
    description: Дневные продажи
    grain: [date, store_id]
    columns:
      - {name: date, role: time}
      - {name: store_id, role: dimension, fk: dm.stores.id}
      - {name: revenue, role: measure, agg: sum}
    physical:
      engine: clickhouse
      sorting_key: [date, store_id]
      partition_key: toYYYYMM(date)
joins:
  - {left: dm.sales_daily.store_id, right: dm.stores.id, type: many_to_one}
```

Модель хранит:

- роли `time`, `dimension`, `measure`, grain, joins и именованные metrics;
- `physical`-метаданные для advisor;
- `additivity` для запрета бессмысленного суммирования rate/ratio;
- `synonyms` для поиска пользовательских терминов;
- `classification` (`public`, `internal`, `confidential`, `restricted`) для
  outbound sample policy;
- UTC freshness marker физических статистик.

Enrichment меняет модель через валидируемый API под lock. Gaps report остаётся
first-class workflow: качество модели определяет качество grounding.

### 3.3 Agent Core

```text
INTAKE -> GROUNDING -> CLARIFY* -> PROPOSE_SPEC -> APPROVE
                                              |
                                              v
                               SQL_GEN -> VALIDATE -> BUILD -> DONE
```

- `GROUNDING` сопоставляет запрос и semantic model.
- `CLARIFY` задаёт не более трёх вопросов за раунд и только по найденной
  неоднозначности.
- `PROPOSE_SPEC` возвращает `DashboardSpec`, summary и advisor findings.
- Правка словами создаёт новый предложенный spec, не мутируя approved revision.
- `VALIDATE` проверяет spec, SQL и живую резолвимость запроса.
- `BUILD` передаёт нормализованный spec и build context адаптеру.

Feasibility Advisor объединяет engine-neutral evidence и rule pack конкретного
движка. Он анализирует effective query: chart filters плюс применимые
dashboard controls. Когда доступен живой каталог, scan fraction использует
живой размер таблицы; model snapshot остаётся fallback. Findings имеют severity
и могут породить структурированный `dm_change_request`, но не блокируют сборку.

### 3.4 IR — DashboardSpec

`DashboardSpec` содержит title, target BI, dashboard filters и charts. Chart
query задаёт таблицу, dimensions, series, pivot roles, measures, joins, filters,
sort, limit и layout hint.

IR поддерживает:

- явные model-approved joins и qualified column references;
- line, bar, stacked bar, area, pie, table, pivot, heatmap, histogram и KPI;
- time grain, ratio measures, period comparison, running total,
  share-of-total и другие детерминированные transforms;
- capability-aware degradation между BI;
- source-vs-owned dataset planning;
- отдельный операторский `raw_sql` contract из §3.16.

Перед validation/build идемпотентные transforms:

- добавляют безопасный top-N для категориальных charts;
- заменяют id dimension на readable label через model join только при
  lossless-доказательстве: `label_cardinality >= 0.99 * id_cardinality`;
- при отсутствии cardinality proof сохраняют id и никогда не объединяют разные
  сущности молча;
- сохраняют explicit user choices;
- используют общие alias functions для SQL и обоих adapters.

### 3.5 BI Adapters

`BIAdapter` — обязательный typed contract. `BuildContext` передаёт namespace,
plans, session и owner. `build()` возвращает `BuildResult` с
`DashboardRef` и полным набором `BuildArtifact`. `delete_artifact`,
`reconcile_build_attempt` и `close` входят в контракт и проверяются factory.

Подробное решение: [ADR 0001](adr/0001-bi-adapter-contract.md).

Superset adapter:

- управляет database, virtual/source datasets, charts и dashboard через REST;
- генерирует native filters только для применимых charts;
- отделяет display labels от SQL aliases;
- хранит полный build token в chart/dashboard metadata;
- удаляет только exact-token objects и никогда не считает human title
  доказательством владения.

DataLens adapter:

- работает в выделенном workbook;
- создаёт dataset/widgets/dashboard через gateway contract;
- использует canonical и temporary names из stable namespace;
- при recovery ищет только точные owned names;
- не удаляет shared connection scope.

Factory — единственное место выбора конкретного adapter. Stable native-format
contracts закреплены unit и live contract tests. Crash-recovery contract:
[ADR 0002](adr/0002-durable-build-attempt-reconciliation.md).

### 3.6 LLM Layer — Anthropic (default) + GraceKelly (opt-in)

Agent core зависит только от:

```text
LLMClient.complete(prompt, schema) -> ValidatedModel
```

Factory выбирает direct Anthropic Messages API или локальный GraceKelly.
Structured repair loop общий: JSON extraction, Pydantic validation, bounded
feedback retries и durable call logging.

Контекст ограничивается релевантными таблицами и компактным model rendering.
Каждый outbound call проходит budget hook до provider request. Лимиты могут
применяться на session и actor/day по calls, tokens, provider time и estimated
cost. Бюджет opt-in; usage ledger переживает restart.

### 3.7 UI

Три входа сходятся в один pipeline:

- `text-first` — запрос и правки словами;
- `fields-first` — validated field groups как structured seed;
- `auto-overview` — deterministic spec без LLM.

Web UI и CLI используют один session contract. API предоставляет session
create/reply/approve, snapshot, SSE events, model gaps/enrichment, insights,
trace и observability. Target BI фиксируется при создании сессии. Dashboard URL
строится из adapter-relative URL и configured public base.

SSE buffer replay позволяет позднему подписчику получить terminal event.
UI пишет недоверенный текст через text-safe DOM APIs. Auth/RBAC ограничивает
видимые schemas, sessions и build specs.

### 3.8 Store

SQLite хранит sessions, messages, spec revisions, builds, durable attempts,
ownership artifacts, LLM calls, change requests, trace events, users и tokens.
Schema upgrades идемпотентны через `PRAGMA user_version`.

Критические переходы транзакционны:

- approved revision и stable build token однозначно связаны;
- build success, session terminal state, attempt state и ownership ledger
  коммитятся вместе;
- delivery с локальным commit failure получает отдельный degraded terminal
  state, а не ложный failed;
- interrupted remote attempt остаётся доступным startup reconciliation.

Retention удаляет только aged telemetry и non-live ownership rows. User work и
live ownership evidence автоматически не удаляются.

### 3.9 Observability

- `trace_events` дают ordered per-session timeline.
- `llm_calls` хранят latency, chars, provider tokens и status.
- session trace и global LLM summary читаются из Store.
- opt-in Prometheus endpoint объединяет process counters и durable aggregates.
- logging настраивается один раз как text или JSON stdout stream.

Telemetry failure не роняет основной pipeline. Global metrics при включённом
auth доступны только admin.

### 3.10 Insight-слой «Что видно»

`analyze_spec` выполняет read-only chart queries тем же SQL seam и формирует
детерминированные observations: trend, reversal/momentum, robust weekly
seasonality, extreme, ranking concentration и largest share.

Insight layer:

- не вызывает LLM;
- работает отдельно от BI dashboard;
- best-effort и никогда не превращает успешную сборку в failed;
- переиспользует normalized spec, SQL aliases и complete build-local trials;
- рендерит числа в компактном RU-формате.

### 3.11 Operational readiness и recovery

- `/health` доказывает liveness; `/ready` проверяет настроенные Store, DWH и
  primary BI dependencies и возвращает HTTP 200/503.
- LLM readiness не тратит hosted-provider tokens; локальный provider может
  проверяться отдельным health request.
- startup сначала reconciles durable attempts, затем legacy stuck builds, затем
  pending ledgers.
- `prepared` attempt aborts без remote delete; `building` и
  `cleanup_required` делегируются owning adapter.
- provider/search error сохраняет `cleanup_required` и блокирует unsafe retry.
- success state и ownership evidence появляются одной транзакцией.

### 3.12 CI-integration stand

CI разделяет quality, Docker build и integration jobs. Integration job поднимает
тот же Compose contract с уменьшенным demo fact, ждёт service healthchecks и
запускает:

- Superset create/get/chart-data/native-filter contracts;
- filterable-dataset acceptance;
- deterministic auto-overview CLI path;
- real Chromium web flow и axe checks без LLM secret.

DataLens contract использует отдельный self-hosted stand и не маскируется
Superset job.

### 3.13 Docker + release-конвейер

Обычный CI собирает image без публикации. Tag workflow выполняет preflight,
image scan, SBOM, immutable image push, package publication и provenance.
Mutable `latest` и GitHub Release создаются только после зелёных producer jobs.

Package version имеет один source of truth для CLI и health endpoint. Release
notes берутся из maintained changelog, а не синтезируются из commit messages.
Package publication использует OIDC trusted publishing; долгоживущий registry
token не требуется.

### 3.14 Golden-eval record/replay

`FixtureLLMClient` и `RecordingLLMClient` реализуют тот же `LLMClient` contract,
что production providers. Fixture содержит template/schema/provider metadata,
ordered calls, prompt hash и response.

- prompt/schema mismatch даёт stale error;
- call-sequence mismatch даёт missing error;
- replay требует полный проход;
- live/record использует отдельные quality thresholds;
- fingerprint refresh не переписывает model responses;
- live sentinel отделён от deterministic offline gate.

### 3.15 Session-resume после рестарта

`SessionManager` лениво гидрирует отсутствующую in-memory session из Store.
Durable state включает owner, target BI, pinned tables, latest spec revision и
build state.

Hydration:

- выводит phase из durable specs/traces;
- восстанавливает terminal dashboard/error state;
- заново применяет owner schema scope;
- seed'ит terminal SSE replay;
- не оживляет tombstoned session.

Grounding и advisor view регенерируются следующим ходом, поскольку это
производные данные.

### 3.16 Escape hatch `raw_sql`

`raw_sql` доступен только операторскому CLI и только для table chart. LLM schema
не содержит этого поля, а text/fields repair validation запрещает его.

Raw SQL:

- проходит тот же SELECT-only guard, RBAC AST extraction, `EXPLAIN` и trial;
- не смешивается с aggregate IR roles;
- не получает advisor/normalization guarantees;
- поддерживается Superset virtual dataset path;
- не разрешает remote table functions.

### 3.17 Artifact namespace

Human title — display metadata, не identity. Technical names и metadata включают
stable non-secret build namespace. Approved spec revision определяет stable
token; повтор того же approve идемпотентен.

`bi_artifacts` хранит native id, kind, build token, owner, schemas и status.
Prune selects only owned non-current rows, deletes dependency-first and marks
success durably. Shared connections исключены из destructive selection.

Билды одной session остаются serial: параллельный executor обязан сохранить
эту гарантию или изменить prune selection.

### 3.18 Resource bounds

- remote bind без auth/demo profile fail-closed, пока оператор явно не разрешит;
- concurrent builds ограничены semaphore;
- work и LLM request quotas разделены;
- SSE consumers ограничены process-wide и per-session;
- rejected work получает `Retry-After`;
- public demo принудительно включает expensive-work quota.

### 3.19 Общий план запроса

`PlanCache` живёт один build call и ключуется точным SQL text.

- plan entry позволяет guard не повторять уже успешный engine plan того же SQL;
- trial entry хранит строки bounded trial и признак completeness;
- incomplete trial не используется для magnitude/insights;
- source dataset и owned chart SQL не смешиваются;
- cache miss безопасно деградирует в обычный `EXPLAIN`.

Advisor может планировать effective pre-normalization SQL, а guard —
post-normalization SQL. Evidence между разными statement не переиспользуется:
для отличающегося точного SQL cache miss обязателен, иначе Advisor или guard
получит доказательство запроса, который BI не исполняет.

Кэш не персистится и не переносит evidence между preview и build.

### 3.20 Filterable dataset

`plan_datasets` классифицирует charts:

- `SOURCE`: BI может агрегировать меру на shared semantic-grain dataset;
- `OWN`: window, scalar compare, raw SQL, histogram и другие невыразимые формы
  остаются на per-chart aggregate dataset.

Source SQL содержит mart columns и model-approved label joins без baked
dashboard filter. `chart_accepts_filter` — единая функция scope для preview и
native BI wiring. Alias collision отклоняется до build.

Superset guard проверяет фактически исполняемый source SQL один раз на mart.
Другие targets сохраняют per-chart validation, пока source path для них не
поддержан.

## 4. Безопасность

- DWH credentials принадлежат отдельной read-only роли с доступом только к DM.
- SQL guard запрещает DDL/DML, multi-statement и remote table functions.
- DWH values уходят к LLM только при явном `SEND_SAMPLES=true` и только для
  `public`/`internal`; `confidential`/`restricted` запрещены всегда.
- Model text и samples рендерятся как untrusted input с control-character caps.
- BI service account ограничен выделенным workspace.
- Secrets находятся вне VCS и не попадают в logs/docs.
- Auth tokens хранятся как hashes; expired rows периодически очищаются.
- Login и session quotas используют trusted proxy-derived client address.
- Remote bind, cookies и forwarded headers имеют fail-closed defaults.
- Startup cleanup удаляет только exact owned objects и никогда shared
  connection scope.

## 5. Решения (ADR-кратко)

| # | Решение | Почему |
|---|---|---|
| D1 | IR-first | Мульти-BI остаётся тестируемым и управляемым |
| D2 | Superset primary, DataLens secondary | Два реальных адаптера на общей границе |
| D3 | `LLMClient` с direct Anthropic default | Внешняя установка не зависит от локального orchestration service |
| D4 | Semantic model в versioned YAML | Review, diff и ручное владение семантикой |
| D5 | LLM думает, код исполняет | Валидация и native payloads остаются детерминированными |
| D6 | Python, FastAPI, Pydantic, sqlglot, httpx | Простая state machine не требует тяжёлого agent framework |
| D7 | Reproducible demo DWH/BI stand | Contract и integration tests не зависят от production DWH |
| D8 | Три входа, один pipeline | UI mode не создаёт отдельную архитектуру |
| D9 | Advisor = engine evidence + rules | Performance facts не делегируются LLM |
| D10 | Глубокий основной stack, расширяемые seams | Качество primary path важнее поверхностной универсальности |

Полные decision records:
[ADR 0001 — BIAdapter contract](adr/0001-bi-adapter-contract.md) и
[ADR 0002 — durable build-attempt reconciliation](adr/0002-durable-build-attempt-reconciliation.md).

## 6. Риски

| Риск | Митигация |
|---|---|
| DM без описаний ухудшает grounding | gaps report, enrichment и dbt import |
| Native BI formats меняются | version pins, templates и live contracts |
| Большой DM переполняет prompt | context selection и compact rendering |
| Advisor создаёт warning fatigue | severity, thresholds и deduplication |
| Engine rules превращаются в каталог кейсов | EXPLAIN first, rules описывают механизмы |
| Hosted/local LLM деградирует | provider seam, replay fixtures и live sentinel |
| LLM выдумывает поля | model validation и bounded repair |
| Remote build оставляет orphan | stable namespace, durable attempt, exact cleanup |
| Shared filter меняет не все charts | source/owned planning и единый scope function |
| Single-process state теряется | durable Store, lazy hydration и startup recovery |
| Telemetry растёт без границ | opt-in retention и bounded process metrics |
