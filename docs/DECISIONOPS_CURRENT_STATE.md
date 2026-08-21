# Auto_BI DecisionOps — текущее состояние

**Дата:** 2026-08-21
**Стадия:** architecture bootstrap
**Ветка:** `main`
**Исходный baseline:** `D:\Auto_BI` @
`b4bfa1d47db013b4529c8355da57c5e637a102d9`

## Что сейчас правда

- Создан отдельный локальный Git-репозиторий `D:\Auto_BI_new` с сохранённой
  историей и без configured remotes.
- Runtime-код пока совпадает с tracked baseline Auto_BI v0.5.0.
- Локальные секреты, окружения, кеши, логи и untracked presentation artifacts
  исходного checkout не переносились.
- Стратегический файл сохранён в корне как
  [`decisionops-strategy-2026-08-21.md`](../decisionops-strategy-2026-08-21.md);
  SHA-256 копии совпадает с `D:\New_steps\bi.md`.
- Целевая архитектура зафиксирована в
  [DECISIONOPS_ARCHITECTURE.md](DECISIONOPS_ARCHITECTURE.md).
- Решение о fork/additive design зафиксировано в
  [ADR 0003](adr/0003-decisionops-additive-control-plane.md).
- Короткий исполнимый план находится в
  [`decisionops-bootstrap.md`](../decisionops-bootstrap.md).

## Что ещё не реализовано

- `DecisionProductSpec` и `MetricContract` runtime schemas;
- semantic snapshots/diff;
- Verified Asset Repository и result comparator;
- EvidenceBundle writer/validator;
- команды `auto_bi decision ...`;
- Analytics Release & Evidence Gate;
- typed investigations, certified brief, scheduler, PostgreSQL, MCP и actions.

## Следующий атомарный slice

Contract kernel: сначала failing tests, затем strict Pydantic-модели
`DecisionProductSpec`, `MetricContract`, `EvidenceBundle` и canonical
round-trip/hash boundary без изменения `DashboardSpec`, `BIAdapter` или Store
schema.

## Источники истины

1. Текущий Git status/HEAD этого репозитория.
2. [DecisionOps architecture](DECISIONOPS_ARCHITECTURE.md).
3. [ADR 0003](adr/0003-decisionops-additive-control-plane.md).
4. [Implementation plan](../decisionops-bootstrap.md).
5. Для унаследованного v1 runtime — [ARCHITECTURE.md](ARCHITECTURE.md) и
   [CURRENT_STATE.md](CURRENT_STATE.md).
