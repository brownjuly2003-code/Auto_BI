# Project closure

Дата фиксации scope: 2026-07-27.

## Закрываемый scope

Финальный scope проекта — текущий v1 product path:

- text / fields / auto → IR → SQL guard → ClickHouse + Superset;
- offline-контракты Greenplum и DataLens;
- default-deny prompt data policy, SafeError и capability-gated demo;
- atomic Store commit, durable pre-return build-attempt recovery, stable build-token retry
  и adapter lifecycle;
- golden/advisor replay, offline browser E2E, backup/restore и docs-as-code;
- текущий bounded mutation gate и mypy-strict boundary allowlist.

После финальной публикации этот scope считается feature-frozen. Новые функции и
исследовательские расширения не являются незакрытым долгом проекта.

## Финальное решение по прежним residual

| Residual | Решение при закрытии |
|---|---|
| Durable outbox до возврата BI adapter | `closed` 2026-07-29: schema v9 + cleanup-only adapter reconciliation (ADR 0002) |
| Полный current/history split ARCHITECTURE | `retired`; CURRENT_STATE остаётся source of truth |
| `Field(description=)` на каждом Settings key | `retired`; generated ENV_REFERENCE закрывает публичный контракт |
| Mutation coverage шире SQL guard | `future`; текущий security boundary gate остаётся обязательным |
| Дальнейшее расширение mypy-strict allowlist | `future`; зафиксированный allowlist остаётся обязательным |
| Live p50/p95, process-memory и cold-start campaign | `future operations`; не часть offline product claim |
| Регулярный paid live-LLM canary | `won't-run`; проект не создаёт постоянные расходы после закрытия |

`future` здесь означает новый отдельно санкционированный проект, а не активный
backlog Auto_BI.

## Обязательные внешние closure gates

Проект нельзя отметить полностью закрытым, пока не выполнены все пункты:

- closing commits опубликованы в `main`, CI зелёный на точном SHA;
- открытую очередь Dependabot разобрали по одному PR;
- подготовлен и проверен финальный release/tag;
- public HF demo либо синхронизирован с closing SHA и проверен, либо снят с
  эксплуатации;
- GitHub release, package/image provenance и опубликованный пакет проверены на
  финальной версии.

Эти действия требуют отдельного разрешения владельца на push/release/deploy.

## Сохранённые локальные артефакты

`Auto_BI.html` и `pres.html` оставлены без изменений и не входят в tracked
product scope.
