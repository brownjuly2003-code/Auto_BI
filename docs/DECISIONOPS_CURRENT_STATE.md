# Auto_BI DecisionOps — текущее состояние

**Дата:** 2026-08-24
**Стадия:** contract kernel (bootstrap task 2 закрыт)
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

### Contract kernel (2026-08-24)

- Реализован первый исполняемый слой: `auto_bi/decision/spec.py`
  (`DecisionProductSpec`), `auto_bi/semantic/metric_contract.py` (`MetricContract`),
  `auto_bi/evidence/model.py` (`EvidenceBundle`/`Claim`/`EvidenceArtifact`),
  shared kernel `auto_bi/refs.py` и `auto_bi/scalars.py`.
- `auto_bi/evidence/canonical.py` — RFC 8785 JCS + SHA-256; проходит официальный
  векторный корпус (`tests/fixtures/jcs`, cyberphone/json-canonicalization,
  Apache-2.0), включая UTF-16-порядок ключей и ES6-числа.
- Границы контракта: binary float в contract payload отклоняется (decimal-строки),
  non-finite/negative zero/unsafe integer/lone surrogate — тоже.
- Инварианты, закрытые парсингом, а не соглашением: dangling `evidence_ref`,
  path confinement артефактов, `integrity=signed` без проверенной подписи,
  causal claim, semi-additive без `non_additive_axes`, unpinned external formula,
  unpinned metric/policy/dashboard ref, `actions.mode != disabled`.
- Проверено: 112 новых тестов, весь suite зелёный, `mypy --strict auto_bi` чист,
  ruff/black чисты. `DashboardSpec`, `BIAdapter` и схема Store не тронуты.
- YAML-фикстуры продукта и метрики: `tests/fixtures/decision/`; sketch §7.2
  архитектуры проверяется docs-as-code тестом.

## Что ещё не реализовано

- semantic snapshots/diff и `SemanticSource`;
- Verified Asset Repository и result comparator;
- EvidenceBundle writer/store (есть только схема и canonical digest);
- команды `auto_bi decision ...`;
- Analytics Release & Evidence Gate и pure BI compile seam;
- typed investigations, certified brief, scheduler, PostgreSQL, MCP и actions.

## Следующий атомарный slice

`decision validate` для Git-tracked YAML (bootstrap task 3): cross-contract
валидация (product → metric contracts → policy refs) и CLI-команда со стабильными
exit codes, без DWH/BI/LLM. Валидная weekly-margin фикстура → exit 0;
malformed/unknown refs → стабильный non-zero.

## Источники истины

1. Текущий Git status/HEAD этого репозитория.
2. [DecisionOps architecture](DECISIONOPS_ARCHITECTURE.md).
3. [ADR 0003](adr/0003-decisionops-additive-control-plane.md).
4. [Implementation plan](../decisionops-bootstrap.md).
5. Для унаследованного v1 runtime — [ARCHITECTURE.md](ARCHITECTURE.md) и
   [CURRENT_STATE.md](CURRENT_STATE.md).
