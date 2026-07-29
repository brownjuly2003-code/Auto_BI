# Project closure

Дата фиксации scope: 2026-07-27. Владелец повторно открыл остаточные пункты
аудита для финального closure-прохода 2026-07-29.

## Закрываемый scope

Финальный scope проекта — текущий v1 product path:

- text / fields / auto → IR → SQL guard → ClickHouse + Superset;
- offline-контракты Greenplum и DataLens;
- default-deny prompt data policy, SafeError и capability-gated demo;
- atomic Store commit, durable pre-return build-attempt recovery, stable build-token retry
  и adapter lifecycle;
- golden/advisor replay, offline browser E2E, backup/restore и docs-as-code;
- текущий cumulative bounded mutation gate и package-wide `mypy --strict auto_bi`.

После финальной публикации этот scope считается feature-frozen. Новые функции и
исследовательские расширения не являются незакрытым долгом проекта.

## Финальное решение по прежним residual

| Residual | Решение при закрытии |
|---|---|
| Durable outbox до возврата BI adapter | `closed` 2026-07-29: schema v9 + cleanup-only adapter reconciliation (ADR 0002) |
| Полный current/history split ARCHITECTURE | `closed`: current design отделён от `ARCHITECTURE_HISTORY.md`, ADR остаются отдельными решениями |
| `Field(description=)` на каждом Settings key | `closed` 2026-07-29: 66/66 descriptions + generated ENV_REFERENCE ratchet |
| Cumulative bounded mutation gate | `closed` 2026-07-29: настроенные production targets закреплены в CI, weak outcomes запрещены; snapshot evidence — в [operations/SLO.md](operations/SLO.md) |
| Package-wide `mypy --strict auto_bi` | `closed` 2026-07-29: package-wide gate действует в обоих поддерживаемых CI jobs |
| Live p50/p95, process-memory и cold-start campaign | `active closure work`; evidence собирается до публикации |
| Paid live-LLM canary | `budget-gated`; запускать только после отдельного явного лимита расходов |

Новые функции вне этой таблицы по-прежнему требуют отдельного проекта. Строки
`active closure work` — текущий backlog, а не бессрочная future-категория.

## Обязательные внешние closure gates

Проект нельзя отметить полностью закрытым, пока не выполнены все пункты:

- closing commits опубликованы в `main`, CI зелёный на точном SHA;
- открытую очередь Dependabot разобрали по одному PR;
- подготовлен и проверен финальный release/tag;
- public HF demo либо синхронизирован с closing SHA и проверен, либо снят с
  эксплуатации;
- GitHub release, package/image provenance и опубликованный пакет проверены на
  финальной версии.

Владелец разрешил эти внешние действия только после завершения всего локального
closure-плана. До этого момента push/release/deploy не выполняются.

## Сохранённые локальные артефакты

`Auto_BI.html` и `pres.html` оставлены без изменений и не входят в tracked
product scope.
