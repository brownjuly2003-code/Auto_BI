# Документация Auto_BI_new

В репозитории сосуществуют два слоя:

1. **DecisionOps** — текущий продукт: Analytics Release & Evidence Gate.
2. **Auto_BI v0.5.0** — унаследованный baseline «запрос → дашборд».

Это разделение важно: опубликованный пакет `autobi-agent==0.5.0` относится только к
baseline. DecisionOps пока запускается из локального checkout и не имеет выбранной
package identity или remote.

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
| [DECISIONOPS_CURRENT_STATE.md](DECISIONOPS_CURRENT_STATE.md) | Короткая актуальная сводка, остаток pilot и решения владельца | current |
| [DECISIONOPS_ARCHITECTURE.md](DECISIONOPS_ARCHITECTURE.md) | Нормативная целевая архитектура и инварианты | current |
| [DECISIONOPS_PILOT_2026-08-25.md](DECISIONOPS_PILOT_2026-08-25.md) | Воспроизводимая репетиция gate на ClickHouse | evidence record |
| [ADR 0003](adr/0003-decisionops-additive-control-plane.md) | Почему DecisionOps добавлен как отдельный control plane | decision |
| [ADR 0004](adr/0004-synthetic-proxy-offline-investigation.md) | Граница synthetic proxy и offline investigation alpha | decision |
| [Bootstrap plan](history/DECISIONOPS_BOOTSTRAP.md) | Исходные задачи 1–8 и acceptance criteria | history; owner outcome открыт |
| [Implementation history](history/DECISIONOPS_IMPLEMENTATION_HISTORY.md) | Подробный хронологический журнал реализации | archive |
| [Strategy snapshot](history/DECISIONOPS_STRATEGY_2026-08-21.md) | Исследование и шестимесячная концепция на 2026-08-21 | archive |

Директории `docs/pilot/` содержат tracked входы и evidence репетиции. Каталог
`.pilot-work/` в корне хранит локальные технические прогоны и не является частью
публичного doc graph.

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
