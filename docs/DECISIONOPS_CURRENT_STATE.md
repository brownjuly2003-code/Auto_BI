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

### `decision validate` (2026-08-24)

- `auto_bi decision validate <paths…> [--json]` — offline cross-contract проверка
  Git-tracked YAML: продукты резолвятся против metric contracts, никаких
  DWH/BI/LLM (тест проверяет, что не открывается ни один сокет).
- `auto_bi/verification/model.py` — `Severity`/`Verdict` и **зафиксированные
  exit codes**: 0 pass/advisory, 2 runtime-usage error, 3 needs_review, 4 block;
  contract-тест написан до CLI (§12.1). Прогон без единого продукта → 2, не 0.
- `auto_bi/decision/validate.py` — стабильные коды находок: `metric_ref.unknown`,
  `metric_ref.version_mismatch`, `metric_ref.duplicate`, `dimension.not_allowed`,
  `metric.retired` (block) / `metric.draft` (review) / `metric.deprecated`
  (advisory), `policy.mismatch` (review), `contract.unreadable`,
  `contract.unknown_kind`, `contract.invalid`, `contract.duplicate_id`.
- Verdict считается кодом из severity находок, а `ValidationReport` отклоняет
  вердикт, противоречащий находкам. Порядок находок детерминирован (CI diff).
- Проверено: 29 новых тестов; mypy --strict, ruff, black чисты. Suite — 1503 теста.

> **Известный локальный flake (не DecisionOps, Windows-only).** Под нагрузкой
> падают ровно два теста, всегда вместе:
> `test_perf_baseline.py::test_sqlite_session_write_and_backup_budgets`
> (таймингвый бюджет) и
> `test_runtime_evidence.py::test_collect_container_snapshot_success` — он
> подменяет глобальный `subprocess.run`, и в это окно кто-то в том же процессе
> вызывает Windows-пробу версии `subprocess.run("ver", shell=True)`, которую
> фейк отвергает. Точный вызывающий не локализован (`platform.system()` в
> изолированном процессе такого вызова не делает). На незагруженной машине оба
> зелёные (1503/1503); в CI (Ubuntu) путь не воспроизводится.

## Что ещё не реализовано

- semantic snapshots/diff и `SemanticSource`;
- Verified Asset Repository и result comparator;
- EvidenceBundle writer/store (есть только схема и canonical digest);
- `auto_bi decision verify` и `auto_bi decision evidence`;
- Analytics Release & Evidence Gate и pure BI compile seam;
- typed investigations, certified brief, scheduler, PostgreSQL, MCP и actions.

## Следующий атомарный slice

Native `SemanticSource` snapshot и semantic diff (bootstrap task 4): immutable
snapshot `model.yaml` с canonical digest и явными `SemanticGap`, затем seeded
diff по formula/grain/time/policy/removal с impact до metric, chart, product и
owner (§8.3). Без DWH: на фикстурах.

## Источники истины

1. Текущий Git status/HEAD этого репозитория.
2. [DecisionOps architecture](DECISIONOPS_ARCHITECTURE.md).
3. [ADR 0003](adr/0003-decisionops-additive-control-plane.md).
4. [Implementation plan](../decisionops-bootstrap.md).
5. Для унаследованного v1 runtime — [ARCHITECTURE.md](ARCHITECTURE.md) и
   [CURRENT_STATE.md](CURRENT_STATE.md).
