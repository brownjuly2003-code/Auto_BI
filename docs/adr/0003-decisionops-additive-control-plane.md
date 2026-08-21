# ADR 0003: отдельный DecisionOps fork и additive control plane

**Статус:** Accepted
**Дата:** 2026-08-21
**Связано:** [целевая архитектура](../DECISIONOPS_ARCHITECTURE.md),
[стратегический вход](../../decisionops-strategy-2026-08-21.md)

## Context

Auto_BI v0.5.0 — закрытый и проверенный dashboard compiler с сильными швами:
`DashboardSpec`, semantic model, SQL guard, physical advisor, `BIAdapter`,
durable build attempts и eval fixtures. Стратегическое исследование предлагает
DecisionOps control plane, но рекомендует по умолчанию эволюцию текущего
репозитория. Владелец отдельно потребовал новый проект в `D:\Auto_BI_new`.

Есть четыре риска:

1. rewrite уничтожит уже доказанные safety/recovery свойства;
2. развитие в старом checkout смешает feature-frozen v0.5.0 и новый product bet;
3. ранние scheduler/PostgreSQL/MCP/actions отвлекут от проверки release-gate
   ценности;
4. расширение `DashboardSpec` или `BIAdapter` новым lifecycle поведением сломает
   v1 contracts и downstream tests.

## Decision

1. Создать отдельный локальный Git fork с полной историей из exact baseline
   `b4bfa1d47db013b4529c8355da57c5e637a102d9`.
2. Не переносить untracked/ignored `.env`, кеши, логи, runtime data и локальные
   presentation artifacts.
3. Удалить inherited remote из нового fork. Новый remote добавит владелец
   отдельным решением.
4. Сохранить package `auto_bi` и весь v1 runtime до появления проверенного v2
   vertical slice; rename package не входит в bootstrap.
5. Строить DecisionOps как additive modular monolith в новых namespaces.
6. Оставить `DashboardSpec` presentation/query IR. `DecisionProductSpec`
   оборачивает или ссылается на него.
7. Оставить `BIAdapter` remote-mutation contract. Для release gate добавить
   отдельный pure `BIContractCompiler`, использующий общие payload builders.
8. Первый product slice — synchronous Analytics Release & Evidence Gate:
   contracts → semantic diff → result/physical/BI checks → evidence bundle.
9. Не добавлять scheduler, distributed workers, PostgreSQL, MCP и actions до
   доказанного recurring/pilot use.
10. Native `model.yaml` реализовать первым semantic source; dbt/MetricFlow и
    Apache Ossie подключать через loss-aware, version-pinned adapters.

## Consequences

Положительные:

- исходный release checkout остаётся неизменным и пригодным как upstream
  evidence;
- новый product bet может менять docs/API/versioning без ложного reopening
  v0.5.0 closure;
- regressions ловит существующий suite;
- первый коммерчески проверяемый результат не требует platform rewrite;
- release gate не создаёт remote BI side effects.

Стоимость:

- два локальных репозитория потребуют явного upstream reconciliation;
- package и часть документации временно сохраняют старое имя;
- новые domain ports добавят небольшую abstraction cost;
- pure BI compile seam потребует аккуратного выделения общих функций, чтобы
  compile и build не расходились.

Ограничения:

- этот ADR не утверждает public `v2alpha1` schema окончательной;
- наличие архитектуры не означает, что DecisionOps runtime уже существует;
- external semantic standards не считаются стабильными без tag/commit pin и
  conformance fixtures;
- evidence с hash без проверенной подписи не называется подписанным.

## Alternatives considered

### A. Продолжать в `D:\Auto_BI`

Меньше repository overhead, но противоречит явному требованию владельца и
смешивает новый scope с feature-frozen release history. Отклонено.

### B. Переписать продукт как новый service/microservices stack

Дало бы чистые имена, но потеряло бы главные активы: IR, adapters, SQL guard,
advisor, recovery и tests. Отклонено.

### C. Сразу расширить `DashboardSpec` и `BIAdapter`

Меньше новых типов, но смешивает presentation, decision lifecycle и CI
verification с remote mutation. Отклонено.

### D. Создать только копию без архитектурных границ

Быстро, но новый fork немедленно начнёт расходиться без правил совместимости,
данных и проверки. Отклонено.

## Verification

- source и target HEAD совпадают в точке bootstrap;
- target не имеет remotes;
- target не содержит локальный `.env` и source-only untracked artifacts;
- v1 import smoke остаётся зелёным;
- новые docs проходят link/encoding checks;
- первый code slice обязан добавить failing contract tests до моделей.
