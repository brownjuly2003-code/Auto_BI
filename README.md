# Auto_BI

Агент «запрос → дашборд» поверх DM-слоя DWH. Принимает запрос **текстом или drag&drop-раскладкой полей витрин**, уточняет детали только при реальных расхождениях с данными, честно предупреждает о не предусмотренных витриной паттернах (engine-aware **Feasibility Advisor** — вплоть до «это запрос на новую витрину»), строит дашборд в выбранной BI и возвращает ссылку.

**Скоуп v1 (RU-рынок):** ClickHouse (DM) + Apache Superset (BI). v2: Greengage/Greenplum + Yandex DataLens (self-hosted OSS-стенд). Универсальность — в швах (IR, адаптеры), не в имплементации.
**LLM:** Sonnet 4.6 thinking через GraceKelly API (`http://127.0.0.1:8011/api/v1/orchestrate`).

## Статус

**Phase 0–3 завершены; Phase 4 (hardening) идёт.** Работает end-to-end: текст/поля →
spec → валидация → сборка дашборда. v1-стек (ClickHouse + Superset) и v2-стек
(Greenplum/Greengage интроспекция+advisor; self-hosted DataLens-адаптер) live-проверены;
web UI с двумя режимами ввода, итерациями, Feasibility Advisor, заявками владельцу DM и
панелью наблюдаемости. Детальный текущий статус и история фаз — в [CLAUDE.md](CLAUDE.md).

## Как пользоваться

Установка, команды CLI, web UI, конфигурация — [docs/USER_GUIDE.md](docs/USER_GUIDE.md).
Подключение новой витрины DWH за ≤ 1 ч — [docs/ONBOARDING_DWH.md](docs/ONBOARDING_DWH.md).

```bash
pip install -e .                                  # консольная команда auto_bi
auto_bi introspect --output semantic/model.yaml   # DWH -> черновик модели
auto_bi build "Выручка по магазинам за июнь 2026"  # текст -> дашборд
auto_bi serve                                     # web UI на http://127.0.0.1:8200
```

## Документация

| Файл | Что внутри |
|---|---|
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | Руководство пользователя: установка, команды CLI, web UI, два режима ввода, advisor, наблюдаемость, конфигурация |
| [docs/ONBOARDING_DWH.md](docs/ONBOARDING_DWH.md) | Подключение нового DWH за ≤ 1 ч: доступы, `.env`, интроспекция, обогащение, проверка (ClickHouse + Greenplum) |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Архитектура: скоуп, IR-first, семантическая модель с физическим слоем, агент, Feasibility Advisor, адаптеры, LLM-слой, решения D1–D10, риски |
| [docs/PLAN.md](docs/PLAN.md) | План: Phase 0–4, задачи, exit criteria; полезный продукт после Phase 2 (~2.5–3 мес FTE) |
| [docs/MARKET.md](docs/MARKET.md) | Рынок на 06.2026: RU (СУБД, BI, AI-фичи конкурентов, статус Superset) + глобальный контекст |
| [CLAUDE.md](CLAUDE.md) | Правила работы над проектом для Claude Code |

## Суть архитектуры в одном абзаце

LLM никогда не генерирует нативные форматы BI. Пайплайн: запрос (текст или раскладка полей) → grounding по семантической модели (`model.yaml`, включая физический слой движка) → уточнения при необходимости → **DashboardSpec** (BI-агностичный JSON, жёстко валидируется по модели) → SQL с проверкой (sqlglot/EXPLAIN/LIMIT) → детерминированный компилятор-адаптер строит дашборд через API выбранной BI. Параллельно детерминированный **Feasibility Checker** сверяет запрос с физикой витрины (ключи сортировки/партиции, размеры, EXPLAIN) — advisor прямо говорит, когда дашборд витриной не предусмотрен, и умеет оформить заявку владельцу DM. Один spec — N платформ.
