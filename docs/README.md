# Документация Auto_BI_new

В репозитории сосуществуют два слоя:

1. **DecisionOps** — текущий продукт: Analytics Release & Evidence Gate.
2. **Auto_BI v0.5.0** — унаследованный baseline «запрос → дашборд».

Это разделение важно: опубликованный пакет `autobi-agent==0.5.0` относится только к
baseline. DecisionOps запускается из локального checkout как `autobi-agent` версии
`0.6.0.dev0`, не публикуется и не имеет remote
([ADR 0009](adr/0009-local-package-identity-no-publication.md)).

## С чего начать

| Задача | Документ |
|---|---|
| Понять продукт за несколько минут | [README проекта](../README.md) |
| Узнать, что работает сейчас и что открыто | [DecisionOps current state](DECISIONOPS_CURRENT_STATE.md) |
| Понять контракты и границы release gate | [DecisionOps architecture](DECISIONOPS_ARCHITECTURE.md) |
| Запустить DecisionOps CLI | [Быстрая проверка](DECISIONOPS_CURRENT_STATE.md#быстрая-проверка) |
| Запустить baseline локально | [Local BYOK](LOCAL_BYOK.md) |
| Пользоваться baseline CLI и web UI | [User guide](USER_GUIDE.md) |
| Эксплуатировать baseline | [Deployment](DEPLOYMENT.md) |

## DecisionOps

| Документ | Назначение | Статус |
|---|---|---|
| [DECISIONOPS_CURRENT_STATE.md](DECISIONOPS_CURRENT_STATE.md) | Короткая сводка: что работает, что не реализовано, что дальше, решения владельца | current |
| [DECISIONOPS_ARCHITECTURE.md](DECISIONOPS_ARCHITECTURE.md) | Нормативная целевая архитектура и инварианты | current |
| [DECISIONOPS_PILOT_2026-08-25.md](reviews/DECISIONOPS_PILOT_2026-08-25.md) | Воспроизводимая репетиция gate на ClickHouse | evidence record |
| [Owner-gate simulation 2026-09-12](reviews/DECISIONOPS_OWNER_GATE_SIMULATION_2026-09-12.md) | Synthetic owner review и статус findings R1–R4 | proxy evidence; human outcome не измеряется |
| [ADR 0003](adr/0003-decisionops-additive-control-plane.md) | Почему DecisionOps добавлен как отдельный control plane | decision |
| [ADR 0004](adr/0004-synthetic-proxy-offline-investigation.md) | Граница synthetic proxy и offline investigation alpha | decision |
| [ADR 0005](adr/0005-ed25519-evidence-trust-root.md) | Ed25519 trust root для evidence manifest | decision |
| [ADR 0006](adr/0006-modelled-review-development-gate.md) | Продолжение разработки через моделирование на Astra xhigh | decision |
| [ADR 0007](adr/0007-human-outcome-out-of-scope.md) | Человеческий owner outcome не входит в цели проекта | decision |
| [ADR 0008](adr/0008-dbt-semantic-manifest-external-formulas.md) | Первый внешний источник формул метрик — dbt `semantic_manifest.json` | decision |
| [ADR 0009](adr/0009-local-package-identity-no-publication.md) | Локальная идентичность пакета форка, без публикации | decision |
| [ADR 0010](adr/0010-finish-line.md) | Финишная черта DecisionOps: пункты F1–F6 и заморозка | decision |
| [DECISIONOPS_BACKLOG.md](DECISIONOPS_BACKLOG.md) | Находки после финишной черты; срез не открывают | backlog |
| [Bootstrap plan](history/DECISIONOPS_BOOTSTRAP.md) | Исходные задачи 1–8 и acceptance criteria | history; owner outcome не измеряется |
| [Implementation history](history/DECISIONOPS_IMPLEMENTATION_HISTORY.md) | Подробный хронологический журнал реализации | archive |
| [Status log](history/DECISIONOPS_STATUS_LOG.md) | Запись каждого среза, сверху новые; пополняется | history |
| [Slice state history](history/DECISIONOPS_SLICE_STATE_HISTORY.md) | Прежние абзацы CURRENT_STATE о срезах до 2026-11-04 | archive |
| [Strategy snapshot](history/DECISIONOPS_STRATEGY_2026-08-21.md) | Исследование и шестимесячная концепция на 2026-08-21 | archive |
| [Typed investigation MVP](history/TYPED_INVESTIGATION_MVP.md) | Журнал закрытого среза offline investigation alpha | archive; закрыт |
| [Chart-level impact](history/CHART_LEVEL_IMPACT.md) | Журнал закрытого среза «impact до плитки» | archive; закрыт |
| [T-64 empty model](history/empty-model-consistency.md) | План и проверки T-64: один отказ для `model.yaml` без mapping | archive; закрыт |
| [Evidence signing plan](history/evidence-signing.md) | Чек-лист реализации ADR 0005 | archive; закрыт |
| [Записи ревью и проверок](reviews/) | Ревью, проверки и pilot-записи отдельных срезов `DECISIONOPS_*` | archive |

Директории `docs/pilot/` содержат tracked входы и evidence репетиции, включая
[chart-impact overlay 2026-09-12](pilot/2026-09-12-owner-review-chart-impact/README.md).
Каталог
`.pilot-work/` в корне хранит локальные технические прогоны и не является частью
публичного doc graph.

## Поведенческие контракты DecisionOps

`docs/specs/` — основное место для требований и сценариев поведения гейта, которые держат
тесты. ARCHITECTURE хранит дизайн и его обоснования, CURRENT_STATE — сводку со ссылками.

| Spec | О чём |
|---|---|
| [asset-query-governance](specs/asset-query-governance.md) | Governance of a reviewed reference query |
| [bi-contract-validation](specs/bi-contract-validation.md) | Validation of a delivered dashboard before its BI contract is compiled |
| [chart-impact-resolution](specs/chart-impact-resolution.md) | Resolving a semantic change to a chart |
| [contract-files-once](specs/contract-files-once.md) | A contract file is read once, however many validated paths reach it |
| [contract-read-order](specs/contract-read-order.md) | A snapshot freezes its contracts in an order of what they hold |
| [dashboard-spec-paths](specs/dashboard-spec-paths.md) | Where `decision verify` reads the spec of a delivered dashboard |
| [dbt-formula-inventory](specs/dbt-formula-inventory.md) | What keeps a dbt formula `external_ref` |
| [environment-access](specs/environment-access.md) | The read-only role an environment is reached through |
| [environment-readings](specs/environment-readings.md) | Attribution of environment readings to the questions they answer |
| [event-time-readings](specs/event-time-readings.md) | The newest event time a DWH reports is read in one of three forms |
| [evidence-format-versions](specs/evidence-format-versions.md) | Evidence format versions — what each verifier and check version changed |
| [evidence-ref-ids](specs/evidence-ref-ids.md) | The ids of evidence named after a ref |
| [generated-sql](specs/generated-sql.md) | Generated SQL preserves identifier and string characters |
| [investigation-exact-arithmetic](specs/investigation-exact-arithmetic.md) | Exact arithmetic of the offline investigation skills |
| [metric-freshness-snapshot](specs/metric-freshness-snapshot.md) | A metric's declared freshness in the canonical snapshot and its diff |
| [metric-join-paths-snapshot](specs/metric-join-paths-snapshot.md) | A metric's declared join paths in the canonical snapshot and its diff |
| [metrics-links](specs/metrics-links.md) | A symbolic link below `metrics/` is frozen as the text git commits for it |
| [model-declarations-snapshot](specs/model-declarations-snapshot.md) | What a model declares that the canonical graph cannot hold |
| [one-line-answers](specs/one-line-answers.md) | A decision command answers in lines, whatever they name |
| [one-line-errors](specs/one-line-errors.md) | A decision command says why it stopped in one line |
| [postgres-kernel-row-security](specs/postgres-kernel-row-security.md) | A PostgreSQL-family kernel without row-level security |
| [probe-execution-safety](specs/probe-execution-safety.md) | Read-only execution with finite probe limits |
| [product-metric-pins](specs/product-metric-pins.md) | A product's metric pins in the candidate snapshot |
| [recorded-result-keys](specs/recorded-result-keys.md) | Recorded results name supplied verified assets |
| [repeated-delivery-entries](specs/repeated-delivery-entries.md) | An entry repeated in the delivery of a decision product |
| [repeated-metric-contracts](specs/repeated-metric-contracts.md) | Two contracts of one metric id in a semantic ref |
| [result-reconciliation](specs/result-reconciliation.md) | Reconciliation of a supplied result against its expected result |
| [run-identity](specs/run-identity.md) | Run identity — the idempotency key covers every static gate input |
| [schema-refused-products](specs/schema-refused-products.md) | The run of a product its schema refuses says why |
| [stated-run-facts](specs/stated-run-facts.md) | What a command line states of a run |
| [unanswered-engine](specs/unanswered-engine.md) | An engine a run was to ask and that did not answer |
| [unencodable-output](specs/unencodable-output.md) | An answer on a stream that encodes less |
| [unreadable-inputs](specs/unreadable-inputs.md) | Files a command cannot read |
| [utc-timestamps](specs/utc-timestamps.md) | A moment evidence carries has one RFC 3339 spelling |
| [validated-path-names](specs/validated-path-names.md) | A validated path is named by its place, wherever it lies |
| [validation-issue-scope](specs/validation-issue-scope.md) | The validation issues a run grades |
| [validation-report-paths](specs/validation-report-paths.md) | The validation report is one report on every system |
| [verified-asset-metric-pins](specs/verified-asset-metric-pins.md) | A verified asset's metric pins in the candidate snapshot |

## Auto_BI v0.5.0 baseline

| Документ | Назначение |
|---|---|
| [CURRENT_STATE.md](CURRENT_STATE.md) | Состояние закрытого baseline-релиза |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Поддерживаемая архитектура baseline |
| [USER_GUIDE.md](USER_GUIDE.md) | Установка, CLI, web UI и конфигурация |
| [LOCAL_BYOK.md](LOCAL_BYOK.md) | Первый локальный запуск с Anthropic key |
| [ONBOARDING_DWH.md](ONBOARDING_DWH.md) | Подключение ClickHouse или Greenplum |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Production-профиль, TLS, backup и recovery |
| [ENV_REFERENCE.md](ENV_REFERENCE.md) | Полный generated inventory переменных окружения |
| [EVAL_FIXTURES.md](EVAL_FIXTURES.md) | Record/replay golden fixtures |
| [MIGRATION_SEND_SAMPLES.md](MIGRATION_SEND_SAMPLES.md) | Миграция безопасного default для sample data |
| [operations/SLO.md](operations/SLO.md) | SLO, recovery и verification gates |

## История и справочные материалы

- [PLAN.md](PLAN.md) — история фаз 0–4 baseline, не текущий backlog.
- [ARCHITECTURE_HISTORY.md](ARCHITECTURE_HISTORY.md) — эволюция архитектуры baseline.
- [PROJECT_CLOSURE.md](PROJECT_CLOSURE.md) — зафиксированный scope релиза v0.5.0.
- [AUTO_BI_IMPLEMENTATION_PLAN.md](history/AUTO_BI_IMPLEMENTATION_PLAN.md) — архив
  execution/audit-шагов S01–S18.
- [MARKET.md](MARKET.md) — исторический рыночный срез baseline.
- [REAUDIT_plan_sol_23_07_26.md](operations/REAUDIT_plan_sol_23_07_26.md) —
  audit evidence baseline.

## Правила поддержки

- Текущий статус меняется в одном current-state документе соответствующего слоя.
- Хронологические логи и завершённые планы живут в `docs/history/`, а не в корне.
- Архитектурное решение оформляется ADR; реализационные детали не дублируются в README.
- `ENV_REFERENCE.md` генерируется скриптом и не редактируется вручную.
- Относительные Markdown-ссылки проверяет `tests/test_docs_as_code.py`.
