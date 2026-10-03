# Auto_BI DecisionOps — целевая архитектура

**Статус:** runtime клина release-gate реализован; платформенные модули после пилота не начаты
**Последнее обновление:** 2026-09-02
**Аудитория:** разработчики, analytics engineers, владельцы data platform
**Первый поставляемый продукт:** Analytics Release & Evidence Gate

Этот документ — нормативная архитектура нового проекта. Текущий дизайн
унаследованного dashboard compiler остаётся в [ARCHITECTURE.md](ARCHITECTURE.md),
а решение о форме нового проекта — в
[ADR 0003](adr/0003-decisionops-additive-control-plane.md). Стратегический
исследовательский вход сохранён как архивный срез в
[`DECISIONOPS_STRATEGY_2026-08-21.md`](history/DECISIONOPS_STRATEGY_2026-08-21.md).

## Содержание

- [Контекст и baseline](#1-контекст-и-измеренный-baseline)
- [Архитектурный результат](#2-архитектурный-результат)
- [Обязательные инварианты](#3-обязательные-инварианты)
- [Границы системы](#4-границы-системы)
- [Архитектурный стиль](#5-архитектурный-стиль-и-зависимости)
- [Карта модулей](#6-целевая-карта-модулей)
- [Доменные контракты](#7-доменные-контракты)
- [Release & Evidence Gate](#8-analytics-release--evidence-gate)
- [Evidence architecture](#9-evidence-architecture)
- [Security и governance](#10-security-и-governance)
- [Persistence](#11-persistence-и-execution-evolution)
- [Interfaces](#12-interfaces-и-совместимость)
- [Observability](#13-observability)
- [Привязка к текущему коду](#14-использование-текущего-кода)
- [Verification strategy](#15-verification-strategy)
- [Последовательность поставки](#16-последовательность-поставки)
- [Риски](#17-риски-и-решения)
- [Открытые решения](#18-открытые-продуктовые-решения)
- [Bootstrap acceptance](#19-bootstrap-acceptance)
- [Источники](#20-источники)

## 1. Контекст и измеренный baseline

Новый проект создан из tracked-состояния `D:\Auto_BI` на commit
`b4bfa1d47db013b4529c8355da57c5e637a102d9`. Исходный checkout имел два
untracked presentation artifact; они, `.env`, виртуальные окружения, кеши и
логи не переносились. Копия стратегии имеет SHA-256
`69823b008dd98e0df5dd7fb4d324b6608afc927b88dee430b4fb83568be11f4f`.

| Показатель | Текущий checkout | Следствие |
|---|---:|---|
| Tracked-файлы | 324 | Это зрелый fork, не greenfield rewrite |
| Python source | 82 файла / 20 824 физических строки | Новый слой должен быть additive |
| Python tests | 100 файлов / 27 960 физических строк | v1 regression suite остаётся обязательной |
| Package | `autobi-agent` 0.5.0 | Имя пакета пока не меняется |
| Runtime | Python 3.12+, FastAPI, Pydantic, sqlglot, SQLite | Нового framework в MVP не требуется |
| Primary path | ClickHouse + Superset | Первый live gate углубляет этот путь |
| Secondary path | Greenplum + DataLens | Используется для cross-target contracts |

Физические строки отличаются от чисел в стратегическом документе более чем на
10%, вероятно из-за другой метрики LOC. Для планирования используются текущие
файлы и реальные архитектурные швы, а не старые оценки размера.

## 2. Архитектурный результат

Auto_BI DecisionOps — modular monolith, который выпускает аналитический продукт
как проверяемый артефакт. Первый вертикальный путь принимает base/candidate
семантику, набор защищаемых вопросов и dashboard specs, выполняет детерминированные
проверки и возвращает verdict вместе с воспроизводимым evidence bundle.

```text
semantic/model change
        +
protected decision products / verified questions
        |
        v
Analytics Release & Evidence Gate
        |
        +--> semantic + impact diff
        +--> SQL/security/physical checks
        +--> result reconciliation
        +--> BI compile contracts
        |
        v
verdict + evidence bundle + owner impact report
```

Продукт не начинает с scheduler, PostgreSQL, MCP, нового UI, новых BI-адаптеров
или write-actions. Эти возможности не нужны, чтобы доказать первый customer
outcome: безопаснее и быстрее выпускать изменения семантики и BI.

## 3. Обязательные инварианты

1. `DashboardSpec` остаётся presentation/query IR и не получает поля lifecycle,
   approvals или evidence.
2. Существующий `BIAdapter` остаётся контрактом remote mutation; release gate не
   вызывает `BIAdapter.build()`.
3. Формулы, joins, policy decisions, trust level и gate verdict вычисляет код,
   а не LLM.
4. Каждый run до исполнения фиксирует точные версии product spec, semantic
   snapshots, policy и среды.
5. Factual claim без валидного `evidence_ref` не попадает в governed или
   certified output.
6. Неизвестная или непереносимая семантика становится явным gap; adapters не
   восстанавливают её догадкой.
7. Любой SQL проходит существующие SELECT-only, dialect, RBAC, `EXPLAIN` и
   bounded/full-result controls.
8. `operational_status` и `gate_verdict` — разные поля: технически успешный run
   может завершиться verdict `block`.
9. Evidence content-addressed и канонизирован; одинаковый вход обязан давать
   одинаковые contract hashes.
10. Raw sensitive payload, prompt/message content и result rows не входят в
    telemetry или public evidence по умолчанию.
11. Interoperability реализуется version-pinned adapters; внешний semantic
    standard не становится внутренним public IR.
12. Observation, contribution, association, hypothesis и causal claim — разные
    типы. Causal claim запрещён до отдельного проверенного модуля.

## 4. Границы системы

```mermaid
flowchart TB
    C["CLI · CI · REST later"] --> UC["Application use cases"]

    subgraph CORE["DecisionOps core"]
      UC --> DP["Decision product"]
      UC --> VG["Verification gate"]
      UC --> DR["Decision run"]
      VG --> EV["Evidence + claims"]
      VG --> SG["Semantic gateway"]
    end

    SG --> NS["Native model.yaml"]
    SG --> DBT["dbt / MetricFlow adapter"]
    SG --> OSSIE["Apache Ossie adapter"]

    VG --> SQL["Existing SQL compiler + guard"]
    VG --> ADV["Existing physical advisor"]
    VG --> BIC["Pure BI contract compilers"]
    SQL --> DWH[("ClickHouse · Greenplum")]
    BIC --> SUP["Superset contract"]
    BIC --> DL["DataLens contract"]

    EV --> FS["Filesystem evidence store"]
    EV -. pilot .-> OBJ["Object storage port"]
    DR -. later .-> JOB["JobStore / workers"]
    EV -. later .-> MCP["Read-only MCP"]
```

### 4.1 Trust boundaries

| Граница | Вход считается недоверенным | Обязательный контроль |
|---|---|---|
| CLI/API → application | YAML/JSON, paths, actor context | strict schema, path confinement, authz |
| CLI → offline investigation | `InvestigationPlan`, canonical result files, output path | exact metric coverage, strict budgets, `max_queries=0`, new output directory |
| Semantic source → gateway | внешние manifests/specs | version pin, loss report, fail-closed validation |
| Core → DWH | скомпилированный SQL | SELECT-only guard, DWH role, policy context, budgets |
| Core → BI | target payload | pure contract compile; remote build только вне release gate |
| Core → evidence store | результаты и metadata | classification, redaction, content digest, retention |
| LLM → core | plan/text candidates | typed catalog, preconditions, evidence refs, no authority |

## 5. Архитектурный стиль и зависимости

На первые 90 дней система остаётся одним Python package и одним CLI process.
Границы задаются протоколами и направлением импортов, а не сетевыми сервисами.

```text
interfaces (CLI / REST later)
            |
            v
application use cases
            |
            v
decision · verification · investigation
      |          |             |
      +----------+-------------+
                 v
       semantic · evidence
                 |
                 v
ports: SemanticSource · QueryExecutor · ArtifactStore · Clock · Signer
                 |
                 v
existing Auto_BI + filesystem/SQLite adapters
```

Правила зависимостей:

- domain-модели не импортируют FastAPI, CLI, SQLite, HTTP clients или конкретную BI;
- `verification` оркестрирует ports, но не знает provider payloads;
- `semantic` не импортирует `decision`; обратная ссылка задаётся стабильными refs;
- `evidence` знает типизированные результаты проверок, но не выполняет запросы;
- существующие `agent`, `advisor`, `ir` и `adapters` используются через тонкие
  application adapters;
- новые директории создаются только вместе с первым исполняемым контрактом, а
  не как набор пустых placeholders.

## 6. Целевая карта модулей

```text
auto_bi/
  strict.py              StrictModel(extra="forbid") — унаследованный shared kernel
  refs.py                stable ids и version-pinned refs
  scalars.py             decimal / duration / UTC timestamp / sha256 скаляры
  decision/
    spec.py              DecisionProductSpec и lifecycle enums
    validate.py          cross-contract validation
    impact.py            product/owner/chart impact и эскалация review→block
    use_cases.py         validate / verify application services
  semantic/
    metric_contract.py   governed metric definition
    gateway.py           source protocols, snapshot, gaps
    diff.py              semantic and impact diff
    metricflow.py        bounded dbt/MetricFlow import
    ossie.py             pinned Apache Ossie compatibility adapter
  verification/
    model.py             CheckResult, verdict and policy
    repository.py        VQR contracts и reviewed reference query
    reconcile.py         canonical result comparator
    provenance.py        откуда пришло кандидатное число: attestation и её проверка
    release_gate.py      orchestration only
    bi_contract.py       pure target compile contracts
  evidence/
    model.py             bundle/claim/artifact schemas
    canonical.py         JCS bytes and digests
    manifest.py          bundle assembly and referential integrity
    store.py             ArtifactStore protocol + filesystem adapter
    lineage.py           internal edges + OpenLineage projection
  investigation/
    model.py             strict InvestigationPlan, scope, budgets and policy
    data.py              CanonicalMetricResult supplied by a trusted read-only step
    skills.py            four deterministic v1 skills; observations or typed gaps
    claims.py            evidence-backed claims and deterministic Markdown blocks
    application.py       offline orchestration and local plan/results/brief artifacts
  jobs/                  starts only after recurring use is proven
  actions/               proposal-only, post-MVP
  mcp/                   read-only, post-REST contract
```

Карта сочетает реализованные seams и явно отложенные модули. Пять functional modules
offline investigation alpha получают готовые canonical results,
не получает `QueryExecutor`, DWH/BI adapter, LLM или сетевой transport и пишет новый
локальный каталог. `jobs`, `actions` и `mcp` остаются gated placeholders архитектуры,
а не обещанием текущей реализации.

`refs.py` и `scalars.py` лежат рядом с существующим `strict.py` как shared kernel:
это грамматика идентичности и скаляров из §7.1, у них нет доменного состояния и они
не создают направления зависимости между `decision`, `semantic` и `evidence`. По той
же причине `evidence/canonical.py` не тянет ничего, кроме stdlib: contract-модели
импортируют его ради digest, а не ради evidence-домена.

## 7. Доменные контракты

### 7.1 Общие правила идентичности и версий

- Stable ids — lowercase URI-like строки, например
  `metric://sales/gross_margin_pct` и `decision://sales/weekly_margin`.
- `version` — монотонная доменная версия; Git SHA или provider version хранится
  отдельно как source revision.
- Любой immutable snapshot имеет `sha256:<hex>` digest канонических bytes.
- В canonical contract запрещены `NaN`, infinities и platform-dependent values.
- Денежные, ratio и tolerance values сериализуются decimal-строками, а не
  бинарными float.
- Время хранится в UTC RFC 3339; business timezone/calendar остаются явными
  полями metric contract.
- Unknown keys отклоняются существующей моделью `StrictModel(extra="forbid")`.

### 7.2 DecisionProductSpec

`DecisionProductSpec` — внешний контракт над одним или несколькими
`DashboardSpec`. Эскиз `v2alpha1`:

```yaml
api_version: auto-bi.io/v2alpha1
kind: DecisionProduct
metadata:
  id: decision://sales/weekly_margin
  version: 1
  owner: commercial-analytics
  risk_tier: medium
decision:
  question: Почему изменилась валовая маржа?
  consumer: commercial-director
  cadence: weekly
scope:
  metrics: [metric://sales/gross_margin_pct@3]
  dimensions: [region, category]
  time_window: P13W
verification:
  policy: policy://release/default@1
  freshness_max_age: PT6H
  cross_surface_tolerance: "0.000001"
delivery:
  dashboards:
    - ref: dashboard://weekly_margin@7
  brief_formats: [markdown]
actions:
  mode: disabled
```

В alpha-схеме `decision`, `owner`, `metric refs` и verification policy
обязательны. Cadence описывает продукт, но scheduler не входит в MVP.

### 7.3 MetricContract

Минимальная переносимая часть:

- id, version, owner, lifecycle status;
- formula ref или typed numerator/denominator;
- grain, allowed dimensions и join paths;
- additivity и non-additive axes;
- event time, timezone и calendar;
- unit, precision, null/zero semantics;
- freshness и data-quality preconditions;
- classification/policy refs;
- physical budgets;
- lineage refs и verified asset refs.

Metric node canonical graph несёт `freshness`: `max_age` как объявлен,
preconditions отсортированы без повторов; это переместило native projection на version 2.
Metric node также несёт объявленные `join_paths` (отсортированы, одинаковые объявления
один раз; висячий endpoint — `gap.metric_join_dangling`, путь с несколькими cardinality —
`gap.metric_join_conflict`, оба block), что переместило native projection на version 3.

Порядок разрешения формулы: external governed reference → typed Auto_BI
expression → reviewed parameterized SQL. Raw SQL не становится дефолтом.

### 7.4 SemanticSnapshot и SemanticGap

`SemanticSource.snapshot(ref) -> SemanticSnapshot` возвращает:

- source kind/version/revision;
- canonical graph поддержанного subset;
- source digest и canonical digest;
- declared capabilities;
- список `SemanticGap(path, reason, severity, source_value_ref)`;
- physical/catalog/policy refs без секретов.

Native `model.yaml` — первый source adapter. Текущий `dbt_import.py` остаётся
enrichment helper и не объявляется полноценным MetricFlow importer. Apache
Ossie (бывший OSI) используется через adapter, pinned на tag или commit;
неизвестные `custom_extensions` сохраняются для round-trip, но не получают
выдуманную внутреннюю семантику.

### 7.5 Verified Asset Repository

Verified asset содержит:

- canonical question и paraphrases;
- intent и metric refs;
- ожидаемое уточнение/refusal для negative cases;
- reference query plan **и — для позитивного asset'а — сам reviewed `reference_query`**;
- expected canonical result или invariants;
- tolerance, действующие semantic versions и risk tier;
- reviewer, rationale и review timestamp.

`reference_query` — это `{engine, sql}`, и он существует ради одной вещи: гейт не
исполняет запрос asset'а (§8.2 шаг 8 берёт числа из порта), поэтому digest reviewed-запроса
— единственное, с чем можно сравнить attestation кандидатного числа (§8.4). Адрес считается
по RFC 8785 от `{"engine": …, "sql": …}` — не только от текста: одна и та же строка на двух
движках значит разное, а внешний capture-runner обязан уметь получить тот же digest из тех
же двух строк. SQL проходит SELECT-only guard **на границе контракта**, при загрузке asset'а:
verified asset с `INSERT` — сломанный reviewed-артефакт, и инвариант 7 не становится
необязательным оттого, что этот SQL ещё никто не выполнял. Движок обязан быть известен
(`is_known_engine`): `sqlglot_dialect` подставляет v1-движок незнакомому имени, и охрана
PostgreSQL-текста ClickHouse-грамматикой либо отвергла бы валидный SQL, либо — хуже —
пропустила бы как SELECT то, что не разобрала.
Если парсер не может прочитать запрос из-за глубины вложенности, граница контракта возвращает
тот же guard rejection, а не выпускает `RecursionError`.

Reviewed-запрос — якорь provenance-цепочки (§8.4.1), и стоит он ровно столько, сколько
стоит его привязка к governed-модели: запрос по `dm.sales_daily_v2` даёт attested число,
которое сходится с ожидаемым и посчитано по витрине, которой никто не управляет. Поэтому
`assets.invalidated` (шаг 5) сверяет reviewed-запрос с кандидатным snapshot'ом на двух
уровнях:

| Что читает запрос | Чего нет в snapshot'е | Код |
|---|---|---|
| таблица | витрина не объявлена | `asset.query_ungoverned` (review) |
| колонка governed-витрины | поле не объявлено | `asset.query_ungoverned_field` (review) |
| — | часть колонок не разрешается | `asset.query_fields_unchecked` (advisory) |

Оба уровня нужны: governance на уровне витрины говорит, что число «про эти витрины», а
метрика определена над **полями**, и запрос по governed-витрине, читающий необъявленную
колонку, считает число частично из данных, о которых модель ничего не говорит. Колонка
приписывается таблице **только там, где выбора не оставляет ни SQL, ни модель**:
квалифицированная — разрешается наружу по цепочке scope'ов, как её разрешает язык;
неквалифицированная — когда у её scope ровно один источник, а если источников несколько,
то когда ровно один из них объявляет это имя. Источники, которые scope предлагает, — те,
что он **читает**: таблицы, derived table, LATERAL и CTE, которые его FROM/JOIN называет.
CTE, которого FROM/JOIN этого scope'а не именует, колонок ему не предлагает: движок
разрешает имя только по тому, что scope читает. Второй шаг — не догадка: движок этот SQL
принял, значит имя разрешилось ровно в один источник scope'а, и если модель оставляет
единственного кандидата, прочитать его — значит прочитать запрос. Вывод требует, чтобы
scope был виден целиком: неуправляемая витрина, **читаемый** CTE или подзапрос рядом держат
колонки, о которых snapshot не знает, и тогда имя не трогают.

Внутри подзапроса, который может читать охватывающий запрос, неквалифицированное имя
разрешается на самом внутреннем уровне, у которого есть колонка, поэтому один локальный
источник его не решает: модель приписывает имя только когда его объявляет локальный
governed-источник, иначе оставляет непроверенным — отличить внешнюю колонку от необъявленной
локальной она не может, а назвать её необъявленной колонкой локальной таблицы значило бы
отвергнуть валидный reviewed SQL. Тело LATERAL читает FROM-элементы, написанные перед ним,
на каждом уровне до стены derived table или CTE, которая окружающее не читает; sqlglot
вешает их на UDTF-scope, а тело может сидеть ниже — set operation или вложенный LATERAL.
Эти дошедшие источники — кандидаты рядом с ближайшими, а не упорядоченные по уровню: имя,
которое объявляют несколько уровней, остаётся непроверенным, а не приписывается ближайшему.

Если имя объявляют два governed-источника — два источника, пусть даже это одна таблица,
приджойненная к себе, — выбор остаётся тем же, что был бы у движка, и приписать `category`
одной из двух таблиц join'а значило бы догадываться, а гейт, догадывающийся о governance,
перестаёт быть evidence (инвариант 6). Поэтому неразрешимое **называется**, а не пропускается:
`asset.query_fields_unchecked` (advisory) разбирает остаток по причинам — имя, объявленное
несколькими источниками, в том числе на двух LATERAL-уровнях; имя, чей scope модель не видит
насквозь; квалификатор, которого не объявляет ни один охватывающий scope; scope без
собственных источников; имя, которое могло быть прочитано из охватывающего запроса, потому
что ни один локальный источник его не объявляет и проверка имя наружу не ведёт, — и отдельно
сообщает про `SELECT *`, у которого состав колонок зависит от физической таблицы, а не от
текста запроса — частичная проверка не должна читаться как чистая. На ClickHouse
звезда, читающая колонки, оставляет причину `it selects *` только в достижимой
части SQL: named CTE и scalar `WITH` следуют общему с chart impact обходу гейтов
CTE. Имена объявлений и ссылок сопоставляются с точным регистром ClickHouse:
внутреннее `a` не затеняет внешнее `A`, а чтение `a` не открывает соседнее `A`.
Квотированное и неквотированное имя с одинаковым написанием совпадают.
Звезда в непрочитанном объявлении этой причины не создаёт; живой вложенный
`SELECT *` и `IN <table>` сохраняют её, а обычный `COUNT(*)` считает строки.
Общий extractor сохраняет whole-AST `ColumnReads.starred`; уточнение применяется
только при governance ClickHouse. Именованные чтения и находки неизвестных
таблиц/полей сохраняют прежнюю область, PostgreSQL-звёзды остаются whole-AST.
ClickHouse `COLUMNS('regex')` тоже оставляет advisory: этот селектор выбирает физические колонки,
полный набор которых reviewed SQL не перечисляет. Регулярное выражение не раскрывается
по известным snapshot-полям, поэтому рядом с селектором обычные именованные чтения
грейдятся самостоятельно. Governance учитывает селектор только в достижимой части SQL,
по общему с chart impact обходу гейтов CTE: непрочитанная named CTE или scalar `WITH`
не создаёт причину `COLUMNS(...)`, а открытая ссылка сохраняет её. Общий extractor
сохраняет whole-AST `ColumnReads.column_selectors`; регулярное выражение не раскрывается.
`JOIN … USING` на ClickHouse и PostgreSQL-семействе также оставляет advisory
с именами ключей: SQL не приписывает общий ключ одной таблице. На ClickHouse
governance собирает точный набор ключей достижимых JOIN по общему с chart impact
обходу гейтов CTE: непрочитанные named CTE и scalar `WITH` ключей не добавляют.
Если рядом есть живой JOIN, в причине остаются только его ключи. Общий extractor
сохраняет whole-AST `ColumnReads.join_using_keys`, PostgreSQL — прежнюю whole-AST
область. Явные соседние колонки грейдятся отдельно.
PostgreSQL `NATURAL JOIN` оставляет advisory без догадки о ключах:
они выбираются по совпадающим именам выходных колонок источников и могут зависеть
от физической схемы. Явные соседние колонки грейдятся отдельно; остаток ищется во
всём AST, включая вложенный JOIN. PostgreSQL-алиас именованной таблицы или
скобочного `JOIN` со списком выходных имён оставляет `asset.query_fields_unchecked`
для ссылок на эти имена: физический ordinal lineage нельзя доказать по snapshot.
Для именованной таблицы поля вне списка проверяются обычным правилом; локальный
выход `GROUP BY` во вложенном LATERAL и выход SELECT в `ORDER BY` имеют свои
приоритеты. Остаток считается **по ссылкам, а не по именам**:
одно `category`, которое модель разрешила, ничего не говорит о
`z.category` в двух словах от него. Голое имя, которого не объявляет ни один governed-
источник, который его scope читает, попадает в `asset.query_ungoverned_field` вместе с
перечнем этих источников — но не тогда, когда scope может читать охватывающий запрос и
локальный источник имя не объявляет: это остаток, а не review. На ClickHouse видимый
алиас — не чтение колонки: SELECT-алиас виден на уровне запроса (другие выражения
SELECT, WHERE, PREWHERE, GROUP BY, HAVING, ORDER BY, LIMIT BY) и не виден в
подзапросе; `WITH <expr> AS alias` и `WITH (SELECT …) AS alias` видны в объявившем
запросе и во всех подзапросах и ветках UNION ниже. Ссылка на expression-form WITH
читает колонки из `<expr>` так, будто они написаны в referencing scope; запрос,
вложенный в `<expr>`, грейдится как тот же подзапрос на месте ссылки (свой FROM,
голые имена enclosed к referencing scope); внутри определения query-level алиаса X
WITH-выражение, прочитанное насквозь из него, включено, и X там — колонка;
query-level алиас на цикле через query-level или WITH-определения читается как колонка
где бы на него ни сослались; WITH-ссылка, замыкающая цикл на пути раскрытия, —
колонка, определения по пути всё равно читаются; сквозное чтение ограничено
глубиной и бюджетом раскрытий, ссылка за ними — unattributed под своим именем,
обрезанное раскрытие не мемоизируется; алиас, на который
никто не ссылается, ничего не читает;
`WITH (SELECT …) AS a` держит свой scope и FROM. На ClickHouse `x IN <table>`
читается как `x IN (SELECT * FROM <table>)`. В PostgreSQL-семействе целое имя
`GROUP BY` сначала ищется среди локальных FROM-колонок, затем среди выходных
столбцов SELECT и лишь затем во внешнем запросе: при отсутствии объявления в
governed-источниках выходной вариант оставляет advisory, а не ложное поле таблицы.
Целое голое имя в `ORDER BY` и `DISTINCT ON` PostgreSQL-семейства сначала
разрешается через выходной столбец SELECT с PostgreSQL-сворачиванием регистра;
внутри выражения то же имя остаётся чтением input-колонки. `ORDER BY` у
`UNION`/`INTERSECT`/`EXCEPT` разрешает целое имя через выход левой ветки.
Унарный `+` делает имя input-выражением и не даёт имени неалиасированной проекции;
`ORDER BY` после скобочного запроса принадлежит его телу, а `(column)` сохраняет
выходное имя колонки.
Прямые PostgreSQL `COUNT(...)`, `SUM(...)`, `AVG(...)`, `MIN(...)` и `MAX(...)`
без явного alias, в том числе в скобках, дают одноимённые неявные выходы для
`ORDER BY`/`DISTINCT ON` и левой ветки set operation; унарный `+` их убирает,
а `AS` заменяет. Квотированное имя функции сохраняет регистр: `"Avg"(...)`
даёт выход `Avg`, и `ORDER BY avg` читает input-колонку.
Те же пять агрегатов сохраняют имя выхода с `FILTER (WHERE …)`; поля аргумента
и предиката остаются чтениями.
Оконный вызов тех же агрегатов с `OVER`, в том числе с `FILTER` внутри, также
сохраняет имя выхода; аргументы, предикат, `PARTITION BY` и внутренний
`ORDER BY` продолжают читаться как входные выражения.
PostgreSQL `ROW_NUMBER() OVER (…)` без alias даёт выход `row_number` для тех
же правил сортировки; явный `AS` заменяет его, унарный `+` убирает, а поля
`PARTITION BY` и внутреннего `ORDER BY` остаются входными чтениями.
Квотированное имя функции сохраняет регистр выхода.
Настоящие `CAST(expr AS type)` и `expr::type`, в том числе вложенные,
сохраняют уже известное сильное имя колонки, этих пяти агрегатов или оконного
`ROW_NUMBER`. Чтения аргумента и оконных выражений сохраняются; `AS` заменяет
имя, унарный `+` убирает. Имя типа для слабого выражения не выводится.
Вызовы функций, нормализованные sqlglot в `Cast`, не наследуют имя аргумента;
на них обход cast прекращается. Новые имена функций не выводятся.
`GROUP BY` не использует агрегатный выход как альтернативу input-колонке.
На границе контракта корневой PostgreSQL `(SELECT …)` или скобочный set operation
разворачивается для guard и scope-чтения вместе с внешними `ORDER BY`, `LIMIT`,
`OFFSET`; повторный или неподдержанный модификатор оставляет отказ. Корневые
`INTERSECT`/`EXCEPT` без скобок принимаются также на ClickHouse; обе ветки проходят
governance и SELECT-only guard. Корневой `VALUES` и скобочный корень ClickHouse
остаются отказом. `SELECT INTO` отклоняется как запись; подзапрос в общем
`LIMIT`/`OFFSET` set operation сохраняет свои field reads.
Оконные, `WITHIN GROUP` и агрегатные сортировки сохраняют чтение input-колонок.
Inline-алиас в любой клаузе своего уровня и ARRAY JOIN-алиас видны на этом
уровне, их определения грейдятся на месте, а в подзапрос они не видны.
Скобочный элемент ClickHouse `ARRAY JOIN (e AS t)` читает `e` и объявляет `t`;
в `ARRAY JOIN (e AS t) AS a` внешний `a` заменяет внутренний алиас, а настоящий
`ARRAY JOIN (SELECT …) AS a` сохраняет область подзапроса.
Имя, объявленное и WITH-, и SELECT-алиасом на одном уровне, читается как колонка
(`WITH revenue AS X, X * 2 AS w
SELECT w AS X` даёт `asset.query_ungoverned_field` на `dm.sales_daily.X`, F-T-13-17).
Видимый scalar WITH-, SELECT-, inline- или ARRAY JOIN-алиас справа от `IN` выигрывает у одноимённой
именованной CTE, и табличная форма молчит там, где `IN (SELECT * FROM x)` открыл бы CTE
(F-T-14-3). FROM-алиас справа от `IN` читается как таблица этого имени, как и в
подзапросной форме (`FROM dm.sales_daily AS rev WHERE region IN rev` даёт
`asset.query_ungoverned` на `rev`, F-T-14-1). Первые два требуют одного имени,
объявленного дважды, — ClickHouse такой statement, скорее всего, отвергает; третий
пере-сообщает таблицу, которой ни у кого нет, и не прячет чтения. Колонка
неуправляемой витрины отдельной находкой не становится: витрину и надо чинить, а один
факт, оценённый дважды, весит вдвое.

Регистр читается по движку asset'а: ClickHouse различает `Sales_Daily` и `sales_daily`,
PostgreSQL-семейство складывает к нижнему регистру **неквотированный** идентификатор, — а
квотированное имя сравнивается точно на любом движке, потому что `"Sales_Daily"` на
PostgreSQL это таблица, которой `sales_daily` не является. Неквалифицированное имя таблицы
(`FROM sales_daily` при governed `dm.sales_daily`) тоже попадает в находку: в какой схеме
его разрешит сессия — настройка, которой у гейта нет.

Исключение CTE из физического списка таблиц и из denylist SQL guard на ClickHouse
также требует точного регистра: `WITH A AS (SELECT 1) SELECT count(*) FROM a`
читает физическую `a` и оставляет `asset.query_ungoverned` (review), если её нет в
snapshot. Точно совпадающее имя, квотированное или нет, сохраняет исключение CTE;
таблица с database/catalog-префиксом остаётся физической. На любом dialect исключение
также требует, чтобы query-владелец объявления CTE был предком конкретной ссылки.
Вложенная CTE в `SELECT count(*) FROM a WHERE EXISTS
(WITH a AS (SELECT 1) SELECT 1 FROM a)` не скрывает физическую внешнюю `a`;
объявление в соседней ветке `UNION` также не распространяет исключение.
Внешний `WITH` сохраняет исключение во вложенных запросах и обеих ветках своей
set operation. Whole-AST область извлечения, включая непрочитанные CTE, правила
сравнения имён CTE PostgreSQL, порядок объявлений одного `WITH`, self-reference,
рекурсия и классификация scalar `WITH` не меняются. Это проверка принадлежности
объявления, а не полный CTE resolver.

Production interaction попадает только в redacted review queue. Benchmark или
expected result никогда автоматически не добавляется в LLM context.

### 7.6 DecisionRun, статусы и verdict

Operational lifecycle:

```text
created -> running -> completed
                  \-> failed
                  \-> cancelled   # post-MVP cooperative cancellation
```

Gate verdict вычисляется независимо:

```text
pass | pass_with_advisory | needs_review | block
```

`completed + block` — нормальный результат проверки, не системная ошибка.

`failed` означает, что gate не смог вынести полный verdict из-за runtime fault.

Идемпотency key для one-shot verify (`release_gate.idempotency_key`) строится из
digest product spec, base и candidate snapshots, policy version, execution
environment, evidence-signing identity (`unsigned` либо algorithm + `key_id`) и verifier
version. Повтор может переиспользовать только полный terminal bundle с тем же ключом;
ротация signing key создаёт другой `run_id`.

Того, что снималось **в момент** прогона — показаний движка (§8.2 шаг 7) и записанных
результатов кандидата (§8.4), — в ключе нет: это наблюдения момента, и ключ по ним сделал
бы новым каждый прогон. Следствие принято сознательно: два прогона, различающиеся только
записанным результатом, делят `run_id` при разных вердиктах. Поэтому `BundleWriter.write`
отказывается писать **другой** bundle под уже запечатанным `run_id`
(`EvidenceConflictError`; наружу — usage-ошибка, exit 2) — и отказывается до записи первого
байта; идентичная перезапись остаётся no-op. Каждому прогону — свой каталог evidence.

`VERIFIER_VERSION` поднимается, когда сегодняшний гейт способен вынести из тех же входов
другой вердикт или сформировать другой evidence artifact (тогда его bundle нельзя
переиспользовать под прежним ключом);
`CHECK_VERSION` — когда меняется смысл отдельной проверки, чтобы прежний `CheckResult` не
читался как гранёный сегодняшним правилом. Добавление **новой** проверки — не такой случай:
ни один прежний `CheckResult` ею не переоценивается, а от чтения bundle без неё как полной
верификации защищает как раз verifier version. Текущие значения:
`VERIFIER_VERSION=84`, `CHECK_VERSION=60`.

### 7.7 CheckResult, Claim и EvidenceBundle

Каждая проверка возвращает typed `CheckResult`:

```text
check_id · check_version · category · subject_refs
status(pass/advisory/review/block/error)
summary_code · evidence_refs · started_at · duration_ms
```

Человекочитаемый текст не является кодом результата. `summary_code` стабилен и
подходит для CI, локализация формируется отдельно.

`Claim` содержит `claim_type`, structured value, unit, comparison semantics,
support/caveats и непустой список evidence refs. Bundle assembler проверяет
существование refs и запрещает certified output при dangling reference.

## 8. Analytics Release & Evidence Gate

### 8.1 Вход и выход

Вход:

- base/candidate semantic refs;
- decision product refs или dashboard specs;
- VQR selection;
- DWH/BI environment refs;
- verification policy и actor context.

Выход:

- operational status + gate verdict;
- список affected metrics/questions/charts/products/owners;
- structured check results;
- JSON evidence bundle;
- derived Markdown summary;
- стабильный process exit code.

### 8.2 Последовательность

```mermaid
sequenceDiagram
    participant CLI
    participant Gate
    participant Semantic
    participant SQL
    participant DWH
    participant BI as BI contract compiler
    participant Evidence

    CLI->>Gate: verify(base, candidate, products, policy)
    Gate->>Semantic: immutable snapshots + gaps
    Semantic-->>Gate: canonical graphs + digests
    Gate->>Gate: semantic diff + impact graph
    Gate->>SQL: compile base/candidate queries
    SQL->>DWH: guard + explain + bounded/full execute
    DWH-->>Gate: typed results + plan evidence
    Gate->>Gate: result + physical reconciliation
    Gate->>BI: pure target contract compile
    BI-->>Gate: capability/degradation contracts
    Gate->>Evidence: checks + refs + artifacts
    Evidence-->>CLI: bundle + report + verdict
```

Gate выполняет шаги в фиксированном порядке:

1. validate входных contracts;
2. load и freeze semantic snapshots;
3. reject blocking semantic gaps;
4. построить semantic diff и impact graph;
5. инвалидировать затронутые verified assets;
6. compile base/candidate SQL;
7. выполнить security, access, freshness, physical и cost checks;
8. reconcile полные typed results;
9. валидировать поставляемые specs против candidate-модели и compile Superset/DataLens contracts без remote mutation;
10. собрать и повторно провалидировать evidence bundle.

Реализовано в `verification/release_gate.py`. Что важно в реализации:

- Шаг 6 реализован в `verification/sql_contract.py` — reference query метрики: её
  определение на её же grain, без окна, фильтра и top-N (это принадлежит продукту и
  прогону, не метрике). Граница узкая намеренно: typed aggregate компилируется
  (`SUM(col)` по сгруппированной витрине значит одно и то же в обоих движках).
  Governed ratio компилируется **на двух семействах движков, чьё десятичное деление
  проверено на живом движке**, и на каждом — своей формой; вывести одну из другой нельзя,
  поэтому незнакомый семье движок получает названный гэп (`sql.ratio_unverified`), а не
  SQL, за который гейт не может ручаться. Движок читается по имени (`is_known_engine`), а
  не по диалекту: `sqlglot_dialect` подставляет v1-движок незнакомому имени — допущение,
  честное для `SUM(col)` и никакое для формы из engine-specific синтаксиса.
  **ClickHouse** (проверено на 24.8.14.39) — `divideDecimal(num, den, precision)`:
  десятичное деление зависит от движка (ClickHouse сохраняет масштаб делимого на
  `Decimal / Decimal`, и chart-путь отвечает на это приведением к Float64 —
  display-решение, которое governed-метрика с объявленной precision наследовать не
  должна), а `divideDecimal` делит точно и усекает к нулю на объявленной precision, каким
  бы ни был масштаб слагаемых; он читает значения знаменателя ПОД NULL-маской, поэтому
  `NULLIF(den, 0)` сам по себе всё равно даёт `ILLEGAL_DIVISION` — ноль подменяется внутри
  вызова, а строка занулевается охраной снаружи; аргументы обязаны быть Decimal, поэтому
  счётные и целочисленные термы приводятся. **PostgreSQL-семейство** (проверено на
  PostgreSQL 14.24 — реализации `numeric`, которую наследуют Greenplum и Greengage; живого
  Greengage-стенда не было) — `TRUNC(num * 1.000000 / NULLIF(den, 0), precision)`:
  `numeric`-деление точно, но его масштаб выбирается по операндам под ~16 значащих цифр,
  поэтому на большом частном `TRUNC(a / b, p)` дописал бы нули, которых деление не считало;
  умножение делимого на точную единицу с объявленным числом знаков поднимает масштаб, не
  трогая значение (приведение к `numeric(_, p)` округлило бы), а при precision 0 множитель
  всё равно десятичный, иначе `/` над целочисленными термами усечёт раньше `TRUNC`. Здесь
  `/` уважает NULL-маску, поэтому охраны вокруг деления не нужно, а `TRUNC` усекает к нулю
  — как `divideDecimal`, так что одно governed-число читается одинаково на обоих движках. Терм, чей
  агрегат не даёт точного десятичного числа (AVG, Float-колонка), — `sql.ratio_inexact_term`;
  колонка, которой нет в модели, — `sql.term_undeclared`.
  External/reviewed-SQL формула — чужое определение и не пере-выводится.
  Все находки шага advisory: почему метрика поехала, уже оценили diff и impact, и
  вторая оценка дала бы одному изменению два веса в вердикте. Агрегат берётся из
  `agent/sqlgen.aggregate_expr` — той же функции, что и chart-путь.
- Шаг 7 реализован целиком и разделён на чистый слой
  `verification/environment.py` (какие вопросы задать движку, как выглядит ответ и что он
  значит), catalog/plan IO `verification/environment_probe.py` поверх того же read-only
  `RunQuery`, что и интроспекция с advisor, и bounded exact-query IO
  `verification/runtime_probe.py` для runtime cost. Гейт оценивает факты, а не держит
  соединение, и те же факты ложатся в bundle. Вопрос шага 7 — пара (витрина, event-time
  колонка): каждое чтение записывает свою колонку времени, и каждая проверка оценивает
  вопрос только по его чтению. Чтение без однозначной атрибуции —
  `access.reading_unattributed` (review), а не догадка по порядку.
  `dwh.access`: роль умеет только читать (иначе
  **block** — инвариант 3) и
  каждая витрина скоупа есть и читается (иначе block). `dwh.freshness`: возраст самой свежей
  строки против `freshness_max_age` продукта, измеренный от `created_at` прогона, — протухший
  источник **review**. Чистая сводка свежести сообщает число сравнённых вопросов
  (витрина, колонка времени) относительно всего скоупа и полноту покрытия.
  Неатрибутированные, отсутствующие, нечитаемые и закрытые deny-all row security
  вопросы не считаются сравнёнными; положительный вывод относится только к
  сравнённым возрастам. При отсутствии вопросов, допустимых для датировки,
  чистый код — `freshness.not_run`, detail сообщает, что возраст не сравнивался,
  а проверка появляется в `What was not done`. В частности, `exists=False` не
  допускает датировку даже при `readable=True`. Частичное ненулевое покрытие
  сохраняет `freshness.fresh`; findings, status и verdict прежние.
  `dwh.physical`: держит ли движок то, что заявляет кандидатная модель —
  отсутствующая колонка метрики **block**, разъехавшийся тип **review** (точная форма ratio
  из шага 6 выведена из объявленного типа), разъехавшаяся раскладка **review**; сравнивается
  только то, что модель утверждает, и только там, где движок ответил (PG-семейство не отдаёт
  distribution/partition без Greenplum-каталогов, и молчание не читается как несовпадение).
  Чистая сводка `dwh.physical` сообщает число реально сравненных типов колонок и
  заявленных свойств layout относительно всех деклараций скоупа. Счёт ведётся по
  вопросам (витрина, колонка времени); учитываются только однозначно атрибутированные
  существующие читаемые витрины и сообщённые значения. Неполное покрытие и отсутствие
  сравнений названы явно: молчание не подтверждает соответствие. При нуле сравнений
  чистый код — `physical.not_run`, и проверка попадает в `What was not done`.
  Хотя бы один сравнённый объявленный тип или поле layout сохраняет
  `physical.as_declared`, включая неполное покрытие. Пустое сообщённое значение
  layout считается ответом на непустую декларацию; deny-all row security не мешает
  сравнению каталога. Findings сохраняют приоритет; status, verdict и detail прежние.
  **Проверка записи никогда не делается записью**: на ClickHouse решает
  профиль (`readonly=1` отказывает даже при `GRANT INSERT ON *.*` из `users.xml`).
  Отказ или частичный ответ `system.enabled_roles` не равен пустому списку ролей:
  missing/NULL/пустое имя оставляет principals неполными; известные имена сохраняются
  точно в grants query. `readonly=1/2` сохраняет read-only, известный write grant —
  writable evidence; иначе неполные роли блокируют доступ и exact runtime SQL.
  Canonical `ALTER`/`CREATE`/`DROP` совпадают также по продолжению через пробел,
  включая `ALTER UPDATE`, `ALTER DELETE`, `CREATE TABLE`, `DROP TABLE`; exact `ALL`
  включает права записи. Evidence сохраняет названия grants; профиль readonly=1/2
  и partial-revoke правила прежние. Это классификация verb families, не копия всей
  privilege hierarchy; `SHOW CREATE`, `ALTERED`, `SYSTEM DROP ...` не совпадают.
  Роль, выданная пользователю, но не включённая, отсутствует в
  `system.enabled_roles`, поэтому после чистых собственных grants идут ещё два
  вопроса: `system.role_grants` — какие роли сессия может включить (строка с
  `user_name` — роль, выданная пользователю напрямую и принимаемая `SET ROLE`;
  строка с `role_name` — роль, выданная роли и включаемая вместе с ней, на любую
  глубину; уже включённые роли из охвата исключены), и затем один вопрос к
  `system.grants` о grants достижимых ролей. Write access type среди них —
  `access.role_writable`; отказ или неполный ответ любого из двух вопросов —
  block, без утверждения о ненаблюдаемых правах. `readonly=1/2` переключением
  роли не снимается (24.8.14.39: ни `SET ROLE`, ни HTTP-параметр `role` не
  применяют настроек роли), а имя роли в литерале вопроса пишется с удвоенными
  обратной косой чертой и кавычкой: одиночная черта называет другую роль или
  никакую. ClickHouse-гейт-пользователь, read-only по grants, нуждается в `SELECT`
  на `system.role_grants` помимо `system.grants`, иначе `dwh.access` блокируется.
  Column reach неизвестен; пустой policy catalog остаётся `[]`, непустой — unknown.
  Security сообщает `security.not_observed`, не выдумывая unreadable/filtered.
  Unknown reach не доказывает физическое отсутствие скрытой ClickHouse-колонки
  или unenforced classification; известные типы и PostgreSQL absence проверяются.
  При `readonly=1/2` eligible runtime probe сохраняется с security REVIEW. На
  PostgreSQL — `has_table_privilege` плюс проверка на суперпользователя, плюс
  `has_any_column_privilege` для колоночных INSERT/UPDATE по всей витрине: табличный
  вопрос отвечает false роли с грантом `UPDATE (revenue)`, хотя она пишет
  (PostgreSQL 16.15, вживую). Superuser-флаг текущей роли читается из
  `pg_roles.rolsuper`: у current role без LOGIN нет строки в `pg_user`, поэтому
  этот view отвечает NULL (PostgreSQL 16.15, вживую). Отказ или пустой ответ
  табличного или колоночного privilege-вопроса
  не подтверждает read-only: `dwh.access` — block с evidence о неотвеченных правах,
  exact runtime SQL не исполняется. Отсутствующее или NULL поле в непустом privilege-ответе
  также не подтверждает read-only; evidence называет конкретные неотвеченные права
  и витрину. Обе группы вопросов продолжают задаваться по всем витринам скоупа,
  доказанные grants сохраняют приоритет в evidence и table/column deduplication.
  Отказ или пустой ответ identity-вопроса также
  блокирует доступ: роль — `unknown`, superuser и `BYPASSRLS` не установлены,
  `bypasses_row_security=null`; write-privilege вопросы и exact runtime SQL не
  выполняются. Finding описывает неподтверждённый read-only, не приписывая неизвестной
  роли способность писать. Частичная строка, NULL в identity-поле или пустое имя также
  не подтверждают read-only: известное имя сохраняется, evidence называет неотвеченные
  поля, write-privilege вопросы и exact runtime SQL не выполняются. Обход RLS — true при
  доказанном superuser или `BYPASSRLS`, false только при двух известных отрицательных
  флагах, иначе null. Доказанный superuser сохраняет прежнее writable evidence и true
  bypass даже без остальных identity-полей. `RoleFacts.name` и `EnvironmentFinding.subject`
  сохраняют точные непустые identifiers, включая значимые пробелы quoted-имён; общий
  `NonBlankText` не меняется. Privilege-функции отвечают только за `current_user`,
  поэтому один вопрос находит роли, в которые session user может переключиться через
  `SET ROLE` (PostgreSQL 16 — опция `SET`, раньше и в Greenplum/Greengage — членство),
  включая самого session user за role default; superuser-роли остаются в вопросе
  и тогда, когда текущая роль их наследует, — членство не передаёт атрибут `SUPERUSER`.
  Их табличные и колоночные write-права спрашиваются по каждой витрине одним вопросом,
  а достижимый superuser, пишущая роль или неотвеченный вопрос — `access.role_writable`.
  Границы — `SECURITY DEFINER`-функции, BYPASSRLS достижимой роли,
  `CREATEROLE`/`ADMIN OPTION`/`ROLE ADMIN`. Пустая витрина —
  `freshness.unknown`, потому что `max(Date)` по нулю строк в ClickHouse равен `1970-01-01`,
  а не NULL. `dwh.security`: досягаемость роли, через которую собрано evidence, — колонка
  метрики, которую роль не может выбрать, **block** (метрику там не посчитать); row-политика,
  применяющаяся к сессии, **review** (числа реальные, но это числа подмножества); включённый
  row security без permissive-политики для этой роли — **block**, потому что витрина отдаёт
  пустой ответ и не отдаёт ошибки; роль, которая обходит row security при живых политиках, —
  **review** (гейт видел больше, чем потребители продукта); объявленная классификация
  confidential/restricted, которую движок для этой роли ничем не ограничивает, — **review**
  (§10.1: ограничение обязано жить в роли или в детерминированном rewrite, а не в документе);
  недоступный роли access-каталог — **review**, потому что «не вижу политику» не значит «её
  нет». Чистая сводка security считает явные подтверждения чтения запрошенных
  колонок и ответы о row policies отдельно, по вопросам (витрина, колонка времени).
  Учитываются только однозначно атрибутированные существующие читаемые витрины;
  пустой список политик — ответ, пустая карта прав — ноль подтверждённых колонок.
  Покрытие сравнивается со всем скоупом, вывод ограничен наблюдаемыми фактами;
  отсутствие колонки в PostgreSQL-каталоге по-прежнему оценивает `dwh.physical`.
  При нуле фактических наблюдений чистый код — `security.not_run`, и проверка
  появляется в `What was not done`. Хотя бы одно подтверждение запрошенной колонки
  или ответ о политиках сохраняет `security.unfiltered`, включая неполное покрытие.
  Findings сохраняют приоритет; status, verdict и detail прежние.
  Механизмы у движков **разные**, и это проверено на стендах, а не вычитано: ClickHouse
  прячет неразрешённые колонки из `system.columns` (поэтому досягаемость читается из
  `system.grants`, включая partial revoke, а `check_physical` не имеет права называть такую
  колонку отсутствующей) и применяет политику только к тем, кого она называет; PostgreSQL,
  наоборот, показывает все колонки в `pg_attribute`, отвечает `has_column_privilege` и при
  `relrowsecurity` требует хотя бы одну permissive-политику. Отдельно:
  `has_table_privilege(…, 'SELECT')` у роли с колоночными грантами равен **false**, хотя роль
  витрину читает, — поэтому читаемость спрашивается через `has_any_column_privilege`, иначе
  гейт блокировал бы релиз по витрине, которую сам только что прочитал. `dwh.cost` разделяет
  plan и runtime evidence exact reference-запроса шага 6. Чистая сводка считает
  проверенные потолки строк из планов и байтов/времени из exact-query runtime evidence
  относительно всех объявленных потолков. Учитываются только вопросы с однозначным
  чтением существующей доступной витрины, без deny-all и с compiled SQL; нулевой
  counter и подтверждённый enforced bound учитываются, отсутствие факта — нет.
  Неполное и нулевое покрытие названы явно; положительный вывод относится только
  к проверенным потолкам. Clean code — `cost.within_budget` только при хотя бы
  одном допустимом сравнении объявленного потолка; иначе `cost.not_run`, и проверка
  попадает в `What was not done`. Findings, status и verdict от этого не меняются.
  `max_scanned_rows` спрашивается
  **только планом**: `EXPLAIN ESTIMATE` и `EXPLAIN` без `ANALYZE`. План дороже объявленного
  потолка — **review** (число верное, выпускать ли — решение владельца), объявленный потолок
  без оценки — тоже **review** (проверка не прошла, а не прошла успешно). Оба движка умеют
  ответить молчанием, которое читается как ноль, и оба случая воспроизведены на стендах:
  ClickHouse отдаёт **пустую** оценку, когда план не читает ни одного парта — тривиальный
  `count()` из метаданных или витрина на `Memory`, чьи строки запрос всё-таки читает;
  PostgreSQL считает строки скан-узла **после** фильтра (`WHERE store_id = 1` — 600 → 30) и
  вообще не строит скан-узел для витрины, опустошённой row security (`One-Time Filter:
  "false"`). Читается скан-узел, а не корень плана: на стенде корневой Aggregate сказал
  `Plan Rows: 60` над `Seq Scan`, сказавшим 600. `max_scanned_bytes` и `timeout_ms` из планов
  не выводятся. Для них IO-слой запускает exact SELECT только после проверки dialect,
  read-only роли, читаемости витрины и того, что row security её не опустошил. ClickHouse
  добавляет `FORMAT Null`, чтобы не материализовать ответ, применяет `max_bytes_to_read` и
  `max_execution_time` с overflow=`throw`, а после успеха сохраняет доступные поля driver
  summary (`read_bytes`, `elapsed_ns`, `query_id`). Если public command API драйвера summary
  не вернул, завершение под точными engine limits всё равно доказывает ненулевые границы;
  нулевая граница без соответствующего counter остаётся неизмеренной. PostgreSQL выполняет
  SELECT в read-only транзакции, применяет transaction-local `statement_timeout` и
  отбрасывает строки через потоковый cursor; честного exact-query `scanned_bytes` PostgreSQL
  не отдаёт. Это проверено по native API PostgreSQL 14–18: `EXPLAIN (ANALYZE, BUFFERS)`
  показывает non-distinct buffer accesses, `pg_stat_get_xact_blocks_fetched` — block read
  requests текущей транзакции, а `pg_stat_io` — I/O/cache events; появившийся в PostgreSQL 18
  `pg_stat_get_backend_io` сужает те же I/O-счётчики до одного backend, но не превращает
  physical reads и cache hits в байты, логически просканированные одним statement. Ни один
  из этих API не ставит byte ceiling. Поэтому потолок остаётся `cost.not_measured`
  (**review**) без пересчёта planner cost, buffer blocks или I/O bytes в придуманные scanned
  bytes. Возобновление требует named trusted provider/extension с pinned контрактом
  query-scoped scanned bytes и enforcement. Независимый safety timeout 60 секунд не
  считается нарушением более длинного contract timeout. Превышение и отсутствие runtime
  evidence — **review**; успешное engine-enforced выполнение подтверждает границу. Метрика
  с потолком, но без скомпилированного reference-запроса даёт ровно один advisory
  `cost.plan_unavailable`,
  потому что шаг 6 уже назвал этот гэп, и находка на каждый потолок дала бы одной границе
  несколько весов. Typed `executions` сохраняются в evidence рядом с `plans`. Прогон без
  `--dwh` выносит все пять проверок как `check.not_configured` — ответ, а не пропуск: чистый
  bundle не должен читаться как полная верификация.
- «Reject blocking semantic gaps» останавливает последовательность ТОЛЬКО на
  `gap.contract_invalid`: непрочитанный контракт отсутствует в графе, и diff назвал
  бы это удалённой метрикой. Остальные blocking-гэпы (dangling join, отсутствующее
  поле метрики) описывают правдивый граф с названной дырой — diff и impact по нему
  по-прежнему стоит читать, а сам гэп уже блокирует релиз. Пропущенные шаги выходят
  как `check.not_run` (review), не как pass.
- Шаг 5 инвалидирует asset по двум разным причинам, но одной проверкой
  (`assets.invalidated`): семантическое изменение сдвинуло число под ним
  (`asset.stale_snapshot` и коды из diff'а) или его reviewed `reference_query` читает
  витрину или колонку, которой этот snapshot не управляет (`asset.query_ungoverned`,
  `asset.query_ungoverned_field`, §7.5). Вопрос один — перестал ли asset быть
  доказательством, — поэтому и проверка одна, но summary code
  называет решившую причину, а detail — сам повод (та же конвенция, что у
  `results.provenance` и environment-проверок): счёт «1 из 1» одинаков для сдвинувшейся
  формулы и для запроса по чужой витрине, а чинить их надо по-разному, и в owner-отчёт
  (§15.2) попадает именно detail.
- Шаг 8 берёт кандидатные результаты из порта `ResultSource`. Отсутствие ответа —
  `insufficient` (review), никогда pass: сравнение, которого не было, не
  доказывает, что число не поехало. Продукт, у которого вообще нет verified asset
  с ожидаемым результатом, получает `result.no_expectations` (advisory). Раз число
  **приходит**, а не считается здесь, шаг несёт вторую проверку — `results.provenance`
  (§8.4): `results.reconcile` отвечает, сходятся ли два числа, а provenance — про то ли
  это число вообще. Разные вопросы, поэтому и разные проверки: снятое месяц назад на
  другом кластере число сходится идеально.
- Проверка `evidence.bundle` есть в отчёте, но не в bundle: она оценивает
  повторную верификацию самого bundle, а проверка не может адресовать артефакт,
  который валидирует.
- `CheckResult` намеренно без `started_at`/`duration_ms`: стенные часы внутри
  content-addressed артефакта ломают воспроизводимость (§9.1), а наблюдаемость
  живёт в trace (§13), не в evidence.

### 8.3 Semantic diff

| Изменение | Default classification |
|---|---|
| Description/label only | advisory |
| Добавлена необязательная dimension | pass/advisory |
| Formula, numerator/denominator, grain или additivity | review/block по impact |
| Timezone/calendar/null policy | block для affected verified assets |
| Freshness/data-quality preconditions метрики | ослаблены (длиннее окно, неупорядочиваемое окно, снят precondition) — review; ужесточены или то же окно записано иначе — advisory |
| Join path метрики | удалён — block; изменена cardinality — review; добавлен — advisory |
| Удалён field/metric/join path | block |
| Policy/classification ослаблена | block + security finding |
| Physical key/partition изменены | re-run physical gate |
| External field не отобразился в canonical graph | explicit gap, не silent pass |

Изменения названы `metric.freshness_relaxed`, `metric.freshness_tightened` и
`metric.freshness_restated`; одна правка может назвать несколько направлений.
`dwh.freshness` по-прежнему грейдит витрины против `freshness_max_age` продукта.
Изменения join paths метрики названы `metric.join_path_removed` / `metric.join_path_changed` /
`metric.join_path_added`; модельные `joins` сохраняют собственные правила `join.*`.

Impact graph строится по stable refs:

```text
source field -> metric -> verified asset -> chart -> decision product -> owner
```

**Chart-разрешение — evidence, а не грейдинг.** `resolve_impact` доводит цепочку до плитки:
чарт получает `computes_metric`, если его measure воспроизводит термы формулы (numerator на
самой мере, denominator — на её `denominator`; `transform`/`compare` остаются тем же базовым
агрегатом, поэтому плитка по-прежнему считает метрику), и `reads_field`, если запрос просто
называет изменённую колонку. Разные утверждения не смешиваются: в `ChartSpec` нет ссылки на
метрику, поэтому `computes_metric` — вывод, и делается он только по точному совпадению термов
IR-меры, не из произвольного operator SQL; `reads_field` читается из IR или из operator SQL
там, где statement сам атрибутирует колонку (идентификаторы ClickHouse — как написаны).
Термы берутся из **обоих** снапшотов: плитка, считавшая вчерашнее определение, — ровно та,
которую владелец должен открыть, когда определение поехало.

Разрешение идёт по дашбордам продуктов из `products_in_scope`, а не только по изменениям с
непустым `products`. Плитка, читающая колонку, которую не покрывает ни один metric contract,
— незадекларированная зависимость, и по правилу «записывается весь дифф» отчёт обязан
сказать, что видел её. Вердикт при этом не двигается: чарт не поднимает severity и не вводит
продукт в scope, а секция «Whose numbers this reaches» по-прежнему строится по `_reaching`.

Чего резолвер не отвечает — выходит именованным гэпом в `chart_gaps`, а не пустым списком:
`chart.opaque_sql` (операторский `raw_sql` не разобрать), `chart.sql_partial` (statement
выбирает `*`, читающую колонки: один `COUNT(*)` считает строки и колонок не читает,
а `COUNT(DISTINCT *)` зависит от всех колонок источника и остаётся звездой; и не `*` в части
statement, которую никто не исполняет: именованная CTE, которую не открыла
исполняемая ссылка, или scalar `WITH`, чей алиас не прочитала исполняемая колонка, — не
атрибутирует имя, несёт имя таблицы, которое не совпадает с таблицей плитки: голое, или
длиннее двух частей и не catalog-префикс этой таблицы, или читает колонку из table
function / subquery, которую statement не именует), `chart.metric_unread`
(разобранный operator SQL не говорит, считает ли плитка изменённую метрику: агрегат из
произвольного SQL этот срез не читает; отдельный код, потому что это другое утверждение,
чем частичное чтение колонок), `chart.dashboard_spec_missing` (spec не загружен прогоном),
`chart.formula_untyped` (`external_ref`/`reviewed_sql` не несут термов — тот же гэп, что и
у reference SQL шага 6), `chart.metric_unknown` (определение метрики не передано резолверу).
Три metric-derived кода записываются только пока резолвер обходит изменение, которое
достигает продукта в scope: гэп отвечает на вопрос, который задал продукт в scope, а
изменение, от которого никто в scope не зависит, такого вопроса не задаёт — его гэп был бы
шумом в том месте отчёта, куда владельца просят смотреть. `chart.metric_unread` спрашивается
при этом по дашборду: гэп на плитке raw-SQL возникает, только когда продукт в scope,
доставляющий именно этот точный pinned ref, декларирует одну из изменённых метрик —
объединением метрик всех доставляющих продуктов (общий дашборд спрашивает объединение;
смешанное изменение фильтруется по каждому дашборду независимо; две версии одного дашборда —
два ref и не сливаются; порядок продуктов не влияет). Дашборд, который доставляет только
продукт, не декларирующий ни одной изменённой метрики, не теряет ответ «не эта плитка» —
этого вопроса у него никто не задавал. `chart.metric_unknown` и `chart.formula_untyped`
остаются уровнем изменения: их subject — метрика, не плитка. Термы при этом разрешаются для
каждого изменения, поэтому `computes_metric` на недостигающем изменении остаётся evidence
незадекларированной зависимости, а не гэпом. `chart.opaque_sql`, `chart.sql_partial` и
`chart.dashboard_spec_missing` остаются свойствами входов.
Правило скрытого чтения — контракт резолвера, а не перечень конструкций: выражение, которое
он не разворачивает в чтения колонок, оставляет плитку прочитанной частично, а не ответом
«не эта плитка». Пустой ClickHouse `map()` — обычное значение, не шаблон; настоящий
`{{ … }}` оставляет гэп и при наличии `map()` в том же SQL. Сегодня гейт узнаёт
именованный набор случаев этого контракта — `{{ … }}`,
`COLUMNS('re')`, ключ `JOIN … USING`, словарное семейство ClickHouse и семейство
`joinGet` / `joinGetOrNull`; конструкция вне набора — дыра детектора, не гарантия, что
плитка прочитана целиком. Для chart impact `COLUMNS('re')` учитывается только в
исполняемой части statement: селектор в непрочитанной CTE или scalar `WITH` не даёт
`chart.sql_partial`, а открытая ссылка сохраняет гэп. Ключ `JOIN … USING` в chart impact
следует тем же гейтам: непрочитанный JOIN не оставляет гэпа, достижимый оставляет.
Governance reviewed asset SQL на ClickHouse также уточняет достижимость `COLUMNS`
и точный набор ключей `JOIN … USING` через общий обход; общий extractor и
PostgreSQL governance сохраняют whole-AST область.
`WHERE region IN rev` /
`WHERE region IN dm.rev` читается как `IN (SELECT * FROM <name>)`: плитка выбирает `*`
из этой таблицы, поэтому изменение колонки той таблицы — `chart.sql_partial` (звезда),
а не молчание «не эта плитка», в точности как у подзапросной формы. Алиас на том же
месте — не колонка:
`WHERE region IN allowed` при `WITH […] AS allowed` не приписывает `allowed` таблице
плитки и не открывает именованную CTE. Голое имя на правой стороне `IN`, которое не
видимый алиас, открывает именованную CTE того имени, как неквалифицированный `FROM`;
видимый scalar WITH- или SELECT-алиас с тем же именем выигрывает у CTE, и табличная
форма тогда молчит там, где подзапросная открыла бы CTE (F-T-14-3).
ClickHouse `SETTINGS` не добавляет чтения имени или значения настройки и не открывает alias.
Единственное именованное исключение — `additional_table_filters`: его SQL row filter не
разбирается, поэтому добавляет `chart.sql_partial`; `additional_result_filter` — фильтр
результата и чтения колонки таблицы не добавляет.
Словарный вызов — чтение governed-объекта: `dictGet('dm.rev', 'revenue', region)` читает
атрибут `revenue` словаря `dm.rev`, а `extract_column_refs` видит только ключ `region`.
Промолчать о нём значило бы объявить плитку прочитанной целиком и снять её ответом «не эта
плитка» для изменения `dm.rev.revenue`. Поэтому семейство `dict…` — default-deny. Геттер из
закрытого набора (`dictGet`, `dictGetOrDefault`, `dictGetOrNull`, `dictGetAll` и
`dictGet<Type>` / `…OrDefault` / `…OrNull` для целых 8…256, float32/64, decimal32…256, date,
date32, datetime, datetime64, uuid, ipv4, ipv6, string, bool) с литеральными словарём и
атрибутом — или кортежем атрибутов — разворачивается в чтения `<словарь>.<атрибут>` и сводится
как чтение таблицы, в том числе для правила метрики. Любой другой вызов — нелитеральный
аргумент, `dictHas` / `dictIsIn`, иерархия, функция, которую код не знает, — оставляет плитку
прочитанной частично (`chart.sql_partial`) с клаузой «whose dictionary read this run could
not name»: membership-тест тоже читает словарь, только не называет что, а функция, добавленная
в ClickHouse позже, должна стать дырой, а не молчанием. Считаются только вызовы, которые
statement исполняет: вызов в CTE, которую не называет ни FROM/JOIN, ни голое `IN <name>`,
или в scalar `WITH`, чей
алиас не читает исполняемая часть statement, — не чтение и не непрочитанная часть. Обе
формы scalar `WITH` идут за своим алиасом, когда его читает исполняемая часть — голое имя
или обращение к элементу кортежа (`t.1`, `t.name`); именованная CTE, включая
`WITH x AS ((SELECT …))`, достижима только через FROM/JOIN или голое `IN <name>`, которое
не видимый алиас. Запрос, вложенный
в тело scalar `WITH`, — часть этого `WITH`: он исполняется, когда `WITH` жив, и именованные
CTE, которые он называет, и `WITH`, которые он объявляет, идут по тем же правилам. Правило
звезды то же. Алиас, прочитанный только внутри CTE, которую statement не исполняет, scalar
`WITH` живым не делает. Ссылка внутри вложенного scalar `WITH` или named CTE связывается с
ближайшим объявлением, когда такое связывание однозначно: внутреннее `WITH 1 AS a` не
открывает скрытое внешнее `a`, в том числе для dictionary, Join-table и star reachability;
внутренняя named CTE также не открывает внешнюю с тем же именем для `FROM` или `IN <name>`.
ClickHouse сопоставляет эти имена с точным регистром: вложенное `a` не затеняет
внешнее `A`, в том числе для `FROM` и `IN <name>`. Различный регистр не объединяет
соседние объявления; кавычки сохраняют написание имени. Общее правило применяется
к звёздам, `COLUMNS`, `JOIN … USING`, dictionary- и Join-table-вызовам.
Self-reference и дубликаты одноимённых объявлений на одном уровне остаются conservative и
могут пере-сообщать. Обычная двухчастная колонка `t.region` при единственном явном алиасе
именованного табличного источника `FROM`/`JOIN … AS t` своего SELECT читает таблицу, а не scalar `WITH t`.
Tuple-доступ `t.1` и отдельная ссылка на scalar alias по-прежнему открывают его. Database
qualifier `dm.sales_daily.revenue` не открывает `WITH … AS dm`, когда точно называет
единственный неалиасированный именованный источник своего SELECT и имя `dm` не занято
другим источником. При несовпадении, дубликатах, конфликтующем алиасе или источнике с
catalog связывание остаётся conservative. Известные гэпы правила названы, а не скрыты.
Семейство Join-таблицы — тот же контракт, что и словарное: `joinGet('dm.rev', 'revenue', key)`
читает колонку `dm.rev.revenue`, а `extract_column_refs` видит только ключ. Закрытый набор
читателей — `joinGet` и `joinGetOrNull` (без учёта регистра); литеральные таблица и одна
колонка разворачиваются в чтение `<таблица>.<колонка>` и сводятся как чтение таблицы, в том
числе для правила метрики. Кортеж колонок — не сигнатура ClickHouse, поэтому не чтение, а
клауза. Любой другой `joinget…` — нелитеральный аргумент, идентификатор вместо литерала,
неизвестное имя — `chart.sql_partial` с клаузой «whose Join table read this run could not
name». Достижимость та же, что у словаря: считаются только вызовы, которые statement
исполняет.
Разобранный operator SQL, который атрибутирует каждое чтение и не пересекается с изменённым
полем, — ответ «не эта плитка», а не дыра; то же для метрики, чьих колонок-термов statement,
прочитанный целиком, не называет. Голое `FROM sales_daily` при `query.table = dm.sales_daily`
квалифицируется схемой плитки, чтобы id совпал с IR-путём; `FROM cat.dm.sales_daily` —
та же таблица с catalog-префиксом, любое другое многочастное имя — `chart.sql_partial`.
Метрика, типизированная на одной стороне и внешняя на другой, даёт и плитку, и гэп: обе
половины правдивы. Detail проверки `impact.products` раскладывает плитки по коду гэпа,
чтобы четыре числа делили плитки, которых коснулись изменения продукта: названные; из
неназванных — чей SQL не разобрали, прочитанные частично, прочитанные целиком, но не
сведённые с изменённой метрикой. Полностью прочитанная плитка больше не попадает в
«не разрешена».

**Записывается весь дифф, грейдится только дошедшее.** `resolve_impact` оставляет в
payload каждое изменение, включая те, чей список продуктов пуст: отчёт обязан говорить,
что видел. Но прогон проверяет ОДИН продукт, поэтому вердикт и секция «Whose numbers this
reaches» строятся только по изменениям с непустым `products` (`release_gate._reaching`) —
иначе change чужой метрики поднимал бы вердикт продукта, которого он не касается. Сам
change при этом не исчезает: его по-прежнему называет `semantic.diff` — это находка
снапшота, а не продукта.

По той же причине verified assets сужаются до тех, что называют метрики проверяемого
продукта (`use_cases._load_assets`): чужой asset иначе инвалидировался бы на этот вердикт,
а его сверка запечатывала бы в bundle claim о числе, которого продукт не отдаёт. Версии
игнорируются с обеих сторон — резолвится ли пин, спрашивает `decision validate`.

### 8.4 Result reconciliation

- Schema сравнивается до values: names, logical types, nullability и units.
- После schema step каждая колонка читается по имени отдельно в каждом result:
  values, business key, контрактный порядок строк и строка, названная aggregate check.
  Перестановка колонок сама по себе не является difference; native capture по-прежнему
  требует порядок колонок asset.
- Rows канонизируются по объявленному business key; если key не уникален,
  проверка завершается `needs_review` или `block`, а не случайной сортировкой.
- Integer/decimal сравниваются точно; float допускается только с metric-specific
  absolute/relative tolerance.
  Float сравнивается как binary64-значение: точная разность против точного объявленного
  допуска, без приближения разности, отношения или допуска в binary64.
  Нечисловое значение сравнивается по виду: бесконечность равна бесконечности того же
  знака (включая float за пределом binary64), NaN кандидата против числа — `value.changed`,
  ожидаемый NaN не решает свою ячейку (`value.expected_nan`, `insufficient`, если больше
  ничего не доказано), а найденная по business key строка уже решает ячейки ключа.
  По живым фактам ClickHouse 24.8.14.39 даёт `nan` на `0/0` во Float64, PostgreSQL 16.15
  считает `'NaN' = 'NaN'` истиной — гейт не следует ни той, ни другой SQL-семантике.
- Integer/decimal значение business key и `group_by` агрегатной проверки отождествляется
  по числу: `0.4120` и `0.412` — один ключ, две такие строки — неуникальный ключ.
  Non-finite или нечисловое значение и ключ другого типа — по записанной строке;
  NULL сохраняется. Детали называют ключ так, как его записал свой result;
  группу — по первой строке группы, контрактный порядок — по записанным ключам каждого result.
- `NULL`, missing row, zero и empty set — разные состояния.
- Order учитывается только когда он является частью контракта (rank/top-N).
- Bounded/truncated result не может доказывать полную числовую эквивалентность.
- Aggregates получают metamorphic checks: partition conservation, share sum,
  ratio reconstruction и additive decomposition.

Metamorphic checks объявляются явно в `tolerance.aggregate_checks`; comparator не выводит
роль колонки из её имени. Доступны четыре tagged-контракта:

- `partition_conservation`: внутри каждого `group_by` сумма `value_column` равна единственному
  повторённому `total_column`;
- `share_sum`: внутри каждого `group_by` сумма `share_column` равна `expected_sum` (по
  умолчанию `1`);
- `ratio_reconstruction`: в каждой строке `ratio_column = numerator_column /
  denominator_column * factor`;
- `additive_decomposition`: в каждой строке `total_column` равна сумме двух или более
  `component_columns`.

У каждого контракта своя decimal-string `absolute_tolerance` (по умолчанию `0`). Неизвестная
или нечисловая колонка, `NULL`, non-finite value и нулевой denominator дают `insufficient`.
Если инвариант нарушен в reference result, reference нельзя считать доказательством и исход
тоже `insufficient`; нарушение только в candidate означает `different`.

### 8.4.1 Provenance кандидатного числа

Сравнение говорит, сходятся ли два числа, и говорит это про то число, которое ему подали.
Дыры, которой оно не видит, ровно одна: число, снятое месяц назад, на другом кластере,
запросом, который никто не ревьюил, сходится не хуже правильного. Гейт запрос asset'а не
исполняет, поэтому происхождение числа приходит **декларацией** — `ResultAttestation`
(`origin`, `captured_at`, `environment_ref`, `semantic_digest`, `query_digest`,
`executed_by`), и задача проверки `results.provenance` — сделать эту декларацию проверяемой,
а не принятой на веру.

Штатная IO-команда `auto_bi decision capture` создаёт эту декларацию вместе с числом. Она
выбирает один positive asset, повторно пропускает его reviewed SQL через SELECT-only guard,
исполняет точный текст read-only драйвером, сверяет имена, порядок, типы и nullable-границу
ответа с ожидаемым `ResultSet`, затем пишет новый (без overwrite) recorded-results YAML.
Значения канонизируются до строкового контракта `ResultSet`; attestation получает digest
кандидатного semantic snapshot'а, среды и reviewed-запроса. Сам release gate остаётся pure и
читает этот файл как обычный вход.

Три из четырёх её утверждений сверяются с тем, что гейт держит независимо:

| Что сверяется | С чем | Код при расхождении |
|---|---|---|
| snapshot снятия | canonical digest кандидата этого прогона | `provenance.other_snapshot` |
| среда снятия | `environment_ref`, о котором выносится вердикт | `provenance.other_environment` |
| исполненный запрос | digest reviewed `reference_query` asset'а (§7.5) | `provenance.other_query` |

Третья строка держится на том, что сам reviewed-запрос привязан к governed-модели:
`asset.query_ungoverned` и `asset.query_ungoverned_field` (§7.5, шаг 5) ловят случай, когда
`provenance.other_query` молчит именно потому, что число снято тем самым запросом — а
запрос читает чужую витрину или необъявленную колонку своей.

Четвёртое — **что runner действительно выполнил этот запрос** — здесь непроверяемо и не
заявляется. §9.3 теперь аутентифицирует producer и неизменность итогового evidence bundle,
но не превращает входную attestation в показание trusted runner: для этого private key
должен быть изолирован внутри capture/execution boundary. Что проверка убирает уже сейчас —
молчаливый случай: число без attestation называется `provenance.unattested`, а не проходит
через сверку, читающуюся как доказательство.

Остальные коды: `provenance.snapshot_unstated` / `environment_unstated` / `query_unstated`
(не назван тот, с чем есть что сверить), `provenance.hand_entered` и
`provenance.surface_export` (число введено руками или снято с BI-поверхности — это не
доказательство того, что витрина его считает), `provenance.stale_capture` и
`provenance.capture_after_run` (снятие старше объявленного продуктом `freshness_max_age`
или из будущего относительно прогона), `provenance.query_unreviewed` (у asset'а нет
reviewed-запроса — **advisory**: гэп в asset'е, и закрывать его владельцу).

Две границы держатся сознательно:

- **Ни одна находка provenance не блокирует.** «Не можем показать, откуда число» — вопрос
  владельцу; блокирует расхождение чисел, и это уже делает `results.reconcile`. Оценить
  один факт дважды значило бы дать ему двойной вес в вердикте.
- **Оценивается только поданное число.** Asset без кандидатного ответа уже `insufficient` в
  сверке; спрашивать, откуда взялось несуществующее число, — то же удвоение.

Equivalent-сверка по-прежнему выпускает claim, и claim несёт находки provenance
**caveat'ами** и ссылкой на её артефакт. Грейдинг остаётся у `results.provenance`, но
`result.equivalent`, прочитанный вне bundle, не должен уметь скрыть, что происхождение
числа установить не удалось (инвариант 5).

### 8.5 Чистый BI compile seam

Существующий `BIAdapter` смешивает compile и remote mutation. Для release gate
добавляется отдельный port:

```python
class BIContractCompiler(Protocol):
    target: TargetBI

    def capabilities(self) -> TargetCapabilities: ...

    def compile_contract(
        self,
        spec: DashboardSpec,
        model: SemanticModel,
    ) -> CompiledBIContract: ...
```

Реализации переиспользуют pure builders из Superset `form_data`/native filters
и DataLens chart/dataset config. Production `BIAdapter.build()` и новый compiler
должны вызывать одни и те же pure functions; дублирование payload logic запрещено.
Release gate не создаёт datasets/charts/dashboards и не запускает cleanup.

Перед компиляцией gate валидирует каждый поставляемый spec против candidate-модели
тем же `validate_spec`, что и build path перед вызовом BI (инвариант 2,
операторский `raw_sql` разрешён). Spec с ошибками не компилируется и payload'ов
не получает: `bi.contract` — block с `bi.spec_invalid`, evidence называет дашборд
и ошибки валидатора. Валидные дашборды компилируются как прежде.

`TargetCapabilities` **возвращает** compiler, а не принимает (первоначальный
sketch принимал их аргументом): карта viz читается из тех же пиннутых таблиц
адаптера (`VIZ_TYPE`, `VIZ_ID`, `DEGRADED`), а вызывающий, способный передать
capabilities, мог бы объявить поддержку, которой у компилятора нет. Единственный
параметр окружения — `target_version` (версия стенда), он же входит в digest.

Компилируемый контракт намеренно НЕ содержит три вещи, и каждая названа явно, а не
обнаруживается постфактум:

| Не входит | Почему |
|---|---|
| Remote ids datasets/charts | их выдаёт BI на create; контракт использует детерминированные `native_id`-плейсхолдеры, иначе один и тот же вход давал бы разные digest |
| DWH connection payload | несёт пароль: environment binding, не compile artifact (§10.2) |
| Live-measured display scale | `_ru_scale` меряет величину на живом стенде; offline-значения нет — объявляется как `live_measured_display_scale=False` (инвариант 6) |

Payload'ы исключены из dump контракта: контракт несёт content addresses, а байты
уезжают в evidence store блобами (§9.1). Digest артефакта проверяется против
payload, когда payload присутствует, поэтому контракт в памяти не может соврать о
том, что скомпилировал, а прочитанный из evidence — по-прежнему проверяет своё тело.

## 9. Evidence architecture

### 9.1 Канонизация и digest

Manifest канонизируется по JSON Canonicalization Scheme (RFC 8785), затем
хешируется SHA-256. До выбора библиотеки реализация обязана пройти official
test vectors; обычный `json.dumps(sort_keys=True)` не объявляется JCS.

Чтобы избежать неоднозначной number serialization:

- contract decimals хранятся строками;
- non-finite numbers запрещены;
- negative zero отклоняется на границе;
- Unicode не нормализуется неявно;
- bytes хранятся отдельным blob и адресуются digest.

### 9.2 Layout bundle

```text
evidence/<run_id>/
  manifest.json              canonical root metadata
  checks/<check_id>.json     typed check results
  reports/summary.md         derived, never source of truth
  blobs/sha256/<digest>      content-addressed payloads
  restricted/               optional encrypted/raw payload store
```

Public manifest содержит digest и classification restricted payload, но не его
raw contents. Artifact paths строит store, а не пользователь; `..`, absolute
paths и symlink escape запрещены.

`reports/summary.md` пишет `verification/summary.py` — единственный артефакт в
layout, написанный для человека, а не для машины. «Derived» здесь контракт, а не
оговорка: отчёт пересказывает findings уже оценённых check'ов в порядке, нужном
владельцу (что блокирует релиз → чьи числа задеты → что решает человек → что
прогон НЕ сделал), и не вводит ни одного собственного. Два следствия из того, где
он лежит: он не оценивает `evidence.bundle` (эта проверка судит артефакт, внутрь
которого отчёт запечатан, и решается уже после запечатывания), и в нём нет ни
часов, ни зависящего от словаря порядка — одинаковый вход обязан давать те же
байты, потому что байты входят в content address (§9.1). Строка
`result.no_expectations` намеренно попадает в раздел «что не сделано», а не
теряется среди advisory: гейт, который не сравнил ни одного числа, ничего про
числа не проверил.

### 9.3 Integrity и подпись

Подпись opt-in: без signing key сохраняется прежний
`integrity=content_hashed`. С `--signing-key` writer загружает unencrypted Ed25519 PKCS#8
PEM по явному пути, подписывает canonical manifest и только после self-verification
выставляет `integrity=signed`. Private key живёт вне Git, evidence и логов; bootstrap
хранит его только на Mac с mode `0600`. KMS и remote signing этим решением не вводятся.

Trust root — не ключ из manifest, а один или несколько pinned Ed25519 public SPKI PEM,
переданных reader'у повторяемым `--trust-key`. `key_id` имеет вид
`sha256:<64 lowercase hex>` и считается от raw 32-byte public key. Несколько ключей дают
overlap-ротацию; старые public keys хранятся весь retention соответствующего evidence.
Signed bundle без подходящего out-of-band ключа получает blocking
`evidence.signature_untrusted`/`evidence.signature_key_untrusted`; artifact из него не
выдаётся.

Signing payload — domain-separated
`auto-bi.io/evidence-manifest/v1 + NUL + RFC8785(manifest)`. В подписанные данные входят
`integrity=signed`, `algorithm=ed25519`, `key_id` и `signed_at`; только
`signature.value` заменён пустой строкой. Ed25519 signature хранится как canonical
unpadded base64url. `signed_at` равен детерминированному `created_at` и является
утверждением подписанта, а не trusted timestamp. Подробности и границы доверия закреплены
в [ADR 0005](adr/0005-ed25519-evidence-trust-root.md).

### 9.4 Lineage

Внутренний graph хранит типизированные edges, достаточные для impact analysis.
Экспорт использует OpenLineage dataset/run model и column-level lineage facets.
Custom facets получают versioned immutable schema URL и префикс `autoBi`, а не
подменяют стандартные поля.

## 10. Security и governance

### 10.1 Actor и policy context

Каждый run фиксирует actor id, tenant, roles, policy version и DWH role. Prompt
policy не является security boundary. Row/column/tenant restrictions должны
исполняться DWH role или deterministic query rewrite и проверяться negative
tests.

Cache/reuse key включает actor/tenant/policy context. Cross-tenant result cache
запрещён даже при одинаковом SQL digest.

### 10.2 Data minimization

- Каждый check/skill объявляет необходимый уровень данных.
- По умолчанию сохраняются schema, counts, digests и aggregates.
- Raw rows, SQL text и prompt/message bodies управляются отдельной retention и
  classification policy.
- Existing `SEND_SAMPLES=false` и запрет confidential/restricted samples
  сохраняются.
- GenAI input/output telemetry остаётся opt-in: upstream OpenTelemetry прямо
  предупреждает о PII/sensitive content.

### 10.3 Human authority

- metric и decision product имеют owner;
- verified asset имеет reviewer;
- `needs_review` не преобразуется автоматически в pass;
- action mode в MVP — `disabled`;
- proposal-only connectors появляются после отдельного approval/idempotency ADR.

### 10.4 Основные угрозы

| Угроза | Контроль |
|---|---|
| Hallucinated formula/join | governed refs + strict semantic validation |
| Evidence-less narrative | referential-integrity gate |
| Permission leak | actor-bound DWH execution + adversarial fixtures |
| Stale source | snapshot timestamp/version + freshness gate |
| Partial result accepted as full | explicit completeness marker |
| Evidence tampering | RFC 8785 digest + opt-in Ed25519 signature against pinned out-of-band public keys |
| Signing key compromise | private key outside Git/evidence/logs, Mac mode `0600`, explicit path, key rotation |
| Path traversal/artifact overwrite | store-owned paths + confinement |
| BI side effects during CI gate | pure compiler port; no `build()` |
| External schema drift | version pin + conformance fixtures + gap report |
| Duplicate later action | typed idempotency key + postcondition ledger |

## 11. Persistence и execution evolution

### 11.1 MVP

- Decision products и metric contracts — Git-tracked YAML/JSON.
- VQR — Git-tracked fixtures с review metadata.
- Verify — synchronous one-shot CLI.
- Evidence — filesystem `ArtifactStore` с atomic temp-write + rename.
- Existing SQLite schema v9 не меняется в первом contract slice.
- Один process/worker; remote mutation отсутствует.

### 11.2 Pilot

До добавления PostgreSQL вводятся ports `ProductRepository`, `RunRepository`,
`JobStore`, `ArtifactStore` и `Outbox`. Pilot backend может использовать:

- PostgreSQL для registry, runs, leases, approvals и outbox;
- filesystem/S3-compatible object store для больших artifacts;
- `SELECT ... FOR UPDATE SKIP LOCKED` только после lease/CAS tests;
- отдельные API и worker processes.

Redis/Kafka не добавляются без измеренного contention/throughput requirement.

## 12. Interfaces и совместимость

### 12.1 CLI

Первый surface:

```text
auto_bi decision validate <product.yaml>
auto_bi decision verify <product.yaml> --base <ref> --candidate <ref> --evidence <dir>
        [--signing-key <private.pem>]
auto_bi decision evidence <run-or-path> [--trust-key <public.pem>]...
auto_bi decision investigate <plan.yaml> --results <result.yaml> ... --output <dir>
```

Exit codes стабильны и различают verdict и runtime error. Значения зафиксированы
contract-тестом `tests/test_decision_cli.py::test_exit_code_contract_is_stable`
и реализованы в `auto_bi/verification/model.py`:

| Код | Значение |
|---|---|
| 0 | `pass` / `pass_with_advisory` — ничто не блокирует релиз |
| 2 | runtime или usage error (то же, что у argparse и v1 CLI) |
| 3 | `needs_review` — нужен человек, автоматического вердикта нет |
| 4 | `block` — изменение не должно уехать в текущем виде |

`1` намеренно не используется: это код необработанного исключения, и падение не
должно выглядеть как вынесенный verdict (инвариант 8). Прогон, который не нашёл
ни одного decision product, завершается `2`, а не «pass».

Реализовано:

```text
auto_bi decision validate <paths…> [--json]
auto_bi decision capture <assets> --candidate <ref> --dwh <engine> --output <new-file>
        [--asset <id>] [--environment <ref>] [--captured-at <ts>]
auto_bi decision verify <paths…> --base <ref> --candidate <ref> --evidence <dir>
        [--dashboards <dir>] [--assets <path>] [--results <file>]
        [--product <id>] [--environment <ref>] [--created-at <ts>]
        [--signing-key <private.pem>] [--json]
auto_bi decision evidence <run-or-path> [--run <id>] [--artifact <id>]
        [--expect-digest <sha256:…>] [--trust-key <public.pem>]... [--json]
auto_bi decision investigate <plan.yaml> --results <result.yaml> [<result.yaml> ...]
        --output <new-dir>
```

`validate` — offline cross-contract проверка (продукты ↔ metric contracts).
`capture` — отдельная read-only IO-команда §8.4.1: исполняет reviewed SQL ровно одного
asset'а и создаёт новый gate-ready result с attestation; существующий output не
перезаписывается. Ошибка контракта, подключения или запроса завершается операционным кодом
`2`, а не становится verdict гейта.
`verify` — полный гейт §8.2 для ОДНОГО продукта: один run = один продукт, потому
что и idempotency key, и evidence bundle называют один `product_ref`.
`evidence` — чтение уже написанного bundle: пересчёт каждого content address,
список claim'ов и артефактов, при `--artifact` — сам payload. Для signed bundle
повторяемый `--trust-key` задаёт out-of-band trust root; без совпавшего public key
inspection блокируется, а artifact не выдаётся. Его вердикт про целостность и
аутентичность, а не про релиз: целый bundle с вердиктом `block` внутри — это рабочий
bundle, и команда выходит с `0`. `restricted`-payload не печатается никогда (§10.2), а
артефакт, который больше не хешируется в свой адрес, не выдаётся вовсе — это уже не то,
на чём принималось решение.

`investigate` — внутренний offline alpha. Команда принимает один strict plan и ровно
по одному `CanonicalMetricResult` для каждой метрики declared steps. Порядок входных
файлов не влияет на bytes. Application исполняет только четыре version-pinned pure skill,
а causal claim запрещают и тип `Claim`, и brief validator. Output-каталог должен быть
новым и содержать ровно canonical `plan.json`, canonical `results.json`, typed
`brief.json` и deterministic `brief.md`; trust brief всегда `exploratory`, а human
acceptance — `not measured`.

План фиксирует `max_queries=0`. Missing/duplicate metric, scope/budget stop, runtime
budget или claim budget завершают команду кодом `2` до создания каталога. Неоднозначный
или unsupported аналитический вход даёт evidence-backed gap без factual claim. Ни один
путь `investigate` не создаёт DWH/BI client и не вызывает remote mutation.

Формат входов release gate, которые читает `decision/use_cases.py`:

| Вход | Форма |
|---|---|
| semantic ref (`--base`/`--candidate`) | каталог с `model.yaml` и необязательным `metrics/` |
| dashboard spec | `<segment>@<version>.yaml` — имя файла несёт пин, иначе продукт с `@7` молча подхватил бы вёрстку `@8` |
| verified assets | файл или каталог YAML (`VerifiedAsset`) |
| recorded results | `{asset id: ResultSet}` **или** `{asset id: {result, attestation}}` |

Обе формы recorded results валидны, и какая перед ним — `use_cases.py` читает из самого
документа, а не из флага: у `ResultSet` нет ключа `result`, поэтому различение — факт про
документ, а не догадка про намерение. Голая форма остаётся читаемой, потому что файл,
написанный до появления attestation, — по-прежнему годный вход; гейт оценивает его как
`provenance.unattested`, а не отказывается читать (§8.4.1).

`--created-at` задаётся явно, когда прогон должен быть байт-в-байт
воспроизводимым: часы внутри content-addressed артефакта делают одинаковый вход
разным bundle каждый раз.

### 12.2 REST

`/api/v1` не меняется. `/api/v2` появляется после стабилизации Pydantic-моделей
и вызывает те же application use cases, что CLI. HTTP schema не становится
местом бизнес-логики.

### 12.3 MCP

MCP отложен до стабильного REST/policy/evidence contract. Актуальная версия
2026-07-28 стала stateless; долгие операции используют отдельное Tasks
extension. Поэтому будущий MCP adapter возвращает явный run/task handle и
привязывает его к authorization context, но эти transport детали не входят в
domain state machine.

## 13. Observability

Существующие `trace_events` и `llm_calls` сохраняются для v1. DecisionOps event
имеет `run_id`, `product_id`, `check_id`, `semantic_digest`, `policy_digest`,
`status`, `duration` и correlation id.

- Span names и основные attributes — low-cardinality.
- SQL/result/prompt bodies не записываются в spans по умолчанию.
- Audit trail отделён от operational trace и имеет собственную retention.
- Evidence bundle содержит ссылки на traces, но его корректность не зависит от
  доступности telemetry backend.
- Telemetry failure не меняет gate verdict, но фиксируется advisory check, если
  policy требует наблюдаемость.

## 14. Использование текущего кода

| Текущий модуль | Роль в новом проекте | Ограничение |
|---|---|---|
| `ir/spec.py` | presentation/query IR | не расширять lifecycle полями |
| `ir/validate.py` | semantic validation | вызывается до любого SQL/BI seam |
| `semantic/model.py` | native source schema | не выдавать за полный MetricContract |
| `semantic/dbt_import.py` | enrichment baseline | не выдавать за MetricFlow gateway |
| `agent/sqlgen.py` | deterministic query compiler | SQL digest после final render |
| `agent/sql_guard.py` | execution security gate | exact SQL, actor-bound role |
| `agent/query_plan.py` | run-local plan/trial evidence | не reuse между snapshots/runs |
| `advisor/*` | physical/cost checks | finding превращается в CheckResult |
| `adapters/*` | delivery + source pure builders | remote build запрещён release gate |
| `agent/insights.py` | будущие skill primitives | thresholds требуют calibration |
| `store/db.py` | v1/local operational patterns | не наращивать до нового god store |
| `eval/*` | VQR/scenario harness seed | expected results отделены от prompts |
| `api/*` | v1 compatibility | v2 добавляется рядом |

## 15. Verification strategy

### 15.1 Contract layer

- strict unknown-field rejection;
- round-trip без потерь;
- deterministic JCS digest на official vectors;
- stable ids и version invalidation;
- claim/evidence referential integrity;
- path confinement и redaction.

### 15.2 Semantic и result layer

- seeded formula, grain, time, additivity, policy и removal diffs;
- loss-aware native/dbt/Ossie fixtures;
- row-order-independent reconciliation;
- exact decimal и bounded float tolerance;
- null/zero/missing distinctions;
- metamorphic aggregate properties;
- provenance кандидатного числа: по одному неверному полю attestation за раз, и ни одна
  находка не блокирует.

### 15.3 Release scenarios

| Сценарий | Покрыт |
|---|---|
| breaking metric change блокируется с exact impact | ✅ `tests/test_decision_verify.py` — 4 seed'а (formula, grain, event time, removed field) до владельца, дашборда и плитки |
| impact доходит до чарта, а нечитаемая плитка названа | ✅ `tests/test_decision_chart_impact.py` + gate-прогон в `tests/test_decision_verify.py` |
| presentation-only change не блокируется | ✅ там же |
| Superset/DataLens compile contracts используют общий canonical query | ✅ `tests/test_bi_contract.py` — сравнение **объектов** builder'ов, не результатов |
| отсутствие evidence ref ломает bundle validation | ✅ `tests/test_evidence_bundle.py` |
| gate не вызывает remote mutation methods | ✅ socket-запрет + mutation-spy на каждом глаголе обоих BI-клиентов |
| governed query дороже policy получает review | ✅ `dwh.cost` при `--dwh` (`tests/test_environment_checks.py`, `tests/test_environment_probe.py`, `tests/test_runtime_probe.py`): `max_scanned_rows` берётся только из плана; ClickHouse exact SELECT проверяет bytes+timeout с `FORMAT Null`, PostgreSQL потоково проверяет timeout; превышение и missing runtime evidence → review, успешная engine-enforced проверка → `cost.within_budget`. PostgreSQL scanned bytes остаются явно неизмеренными. Не «block»: число остаётся верным, а выпускать ли метрику дороже объявления — решение владельца |
| row/column restriction проверяется negative-тестом, а не декларацией | ✅ `dwh.security` при `--dwh` (`tests/test_environment_checks.py`, `tests/test_environment_probe.py`); прогнано вживую на ClickHouse 24.8.14.39 и PostgreSQL 14.24 — withheld column → block, применённая политика → review, RLS без permissive → block |
| stale source возвращает honest insufficient/block | ✅ stale **verified asset** → `asset.stale_snapshot` (review), недоступный результат → `insufficient`, freshness самого источника → `dwh.freshness` (review) при `--dwh` (`tests/test_environment_checks.py`) |
| поданное число нельзя выдать за проверенное | ✅ `results.provenance` (`tests/test_result_provenance.py` + gate-прогоны в `tests/test_decision_verify.py`) — число без attestation, из чужого snapshot'а, с чужой среды или из не-reviewed запроса названо, но не заблокировано. Ed25519 trust root аутентифицирует итоговый evidence producer, но факт исполнения runner'ом потребует изоляции private key внутри capture boundary |
| signed evidence нельзя прочитать как trusted с чужим ключом | ✅ `tests/test_evidence_signing.py` — self-verification writer'а, pinned public trust root, overlap-ротация, tamper/wrong-key/invalid-base64url negative paths и fail-closed artifact read |

### 15.4 Compatibility gate

После каждого behavioral slice запускаются focused tests нового namespace и
релевантные v1 regression tests. Полный существующий suite остаётся release
gate; live DWH/BI/LLM проверки не повторяются без изменённого seam или явной
acceptance необходимости.

## 16. Последовательность поставки

```text
Architecture bootstrap
  -> contract kernel + validate CLI
  -> native semantic snapshot + diff
  -> VQR + result comparator
  -> one-shot release/evidence gate
  -> pure Superset/DataLens compile conformance
  -> typed offline investigation + exploratory brief
  -> Astra xhigh modelled development review (ADR 0006)
  -> production-certified brief / recurring runs / PostgreSQL
  -> read-only MCP / proposal actions
```

Реальный technical pilot change и успешный synthetic task probe разрешили только
внутренний offline-срез typed investigation: strict plan, canonical supplied results,
deterministic skills, synthetic truth sets и evidence-backed Markdown без фонового
исполнения или удалённых мутаций. Этот срез реализован командой
`auto_bi decision investigate`; synthetic probe и `exploratory` brief не считаются
human outcome.

По решению владельца от 2026-09-12 следующий development gate — только моделирование
через Astra xhigh; человеческое review для продолжения разработки не требуется.
Конкретные срезы scheduler/PostgreSQL, notifications, REST/MCP или actions выбираются
после этого review, с собственными техническими gates. Synthetic review не измеряет
human time, не является human/design-partner evidence и не доказывает production
acceptance. Актуальный порядок —
[ADR 0006](adr/0006-modelled-review-development-gate.md); evidence-ограничения
[ADR 0004](adr/0004-synthetic-proxy-offline-investigation.md) сохраняются.

## 17. Риски и решения

| Риск | Архитектурный ответ |
|---|---|
| Scope explosion | один vertical wedge; later modules gated evidence |
| Fork расходится с v1 | upstream baseline зафиксирован; v1 tests не удаляются |
| Canonical graph становится новым стандартом | private bounded model + adapters |
| Apache Ossie меняется | pin commit/tag + conformance corpus + explicit gaps |
| BI compiler расходится с build | shared pure builders + mutation-spy tests |
| Result comparator даёт false equivalence | typed schema/key/unit/completeness before values |
| Evidence слишком тяжёлый/чувствительный | content-addressed blobs + restricted store |
| Evidence signer скомпрометирован | внешний private-key path на Mac + `0600`; key identity в run id; overlap-ротация trust root |
| SQLite переписывается раньше продукта | file-based MVP + repository ports first |
| LLM создаёт ложную уверенность | deterministic verdict + typed claim classes |

## 18. Открытые продуктовые решения

Эти вопросы не блокируют contract kernel, но требуют owner evidence до
соответствующей фазы:

1. Первый pilot workflow и named owner.
2. Первый внешний semantic source: direct dbt/MetricFlow или Apache Ossie
   artifact после native source.
3. Нужен ли отдельный distribution/package name до `v0.6.0`.
4. Где будет новый remote repository.
5. Нужен ли PostgreSQL в pilot или filesystem/SQLite достаточно.
6. Какой BI contract требует live result reconciliation первым.
7. Какой managed key custody/KMS заменит Mac file key перед production-контуром.

Подпись evidence и bootstrap trust root выбраны в
[ADR 0005](adr/0005-ed25519-evidence-trust-root.md); этот вопрос больше не открыт.

## 19. Bootstrap acceptance

Архитектурный bootstrap завершён, когда:

- отдельный repo существует и не имеет случайного remote;
- исходный `Auto_BI` и его untracked artifacts не изменены;
- стратегический input сохранён с проверенным hash;
- этот документ и ADR задают границы, состояния, data flow, security,
  persistence и verification;
- runtime остаётся byte-equivalent tracked baseline, кроме documentation;
- import smoke и offline focused verification baseline зелёные.

## 20. Источники

Локальные:

- [DecisionOps strategy snapshot](history/DECISIONOPS_STRATEGY_2026-08-21.md)
- [Auto_BI baseline architecture](ARCHITECTURE.md)
- [Current baseline state](CURRENT_STATE.md)
- [BIAdapter ADR](adr/0001-bi-adapter-contract.md)
- [Durable build-attempt ADR](adr/0002-durable-build-attempt-reconciliation.md)
- [DecisionOps additive control plane ADR](adr/0003-decisionops-additive-control-plane.md)
- [Synthetic proxy boundary ADR](adr/0004-synthetic-proxy-offline-investigation.md)
- [Ed25519 evidence trust-root ADR](adr/0005-ed25519-evidence-trust-root.md)

Первичные внешние:

- [Apache Ossie (formerly Open Semantic Interchange)](https://github.com/apache/ossie)
- [OpenLineage column-level lineage facet](https://openlineage.io/docs/spec/facets/dataset-facets/column_lineage_facet/)
- [RFC 8785 — JSON Canonicalization Scheme](https://www.ietf.org/rfc/rfc8785.pdf)
- [OpenTelemetry GenAI semantic attributes](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)
- [MCP specification update 2026-07-28](https://blog.modelcontextprotocol.io/posts/2026-07-28/)
- [MCP Tasks extension](https://tasks.extensions.modelcontextprotocol.io/specification/draft/tasks)
