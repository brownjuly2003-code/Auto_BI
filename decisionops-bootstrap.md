# DecisionOps Release Gate bootstrap

> **Где стоим (2026-08-25):** закрыты задачи 1–7, открыта только задача 8 — pilot.
> Подробное состояние, карта модулей и список открытых решений владельца —
> [`docs/DECISIONOPS_CURRENT_STATE.md`](docs/DECISIONOPS_CURRENT_STATE.md).

## Goal

Поставить one-shot Analytics Release & Evidence Gate поверх проверенного
Auto_BI v0.5.0 без изменения `DashboardSpec` и remote-mutation контракта
`BIAdapter`.

## Tasks

- [x] Зафиксировать fork, baseline, целевую архитектуру и ADR → Verify: source
  checkout неизменён, target без remote, strategy hash совпадает.
- [x] Добавить strict contract kernel в `auto_bi/decision`,
  `auto_bi/semantic/metric_contract.py` и `auto_bi/evidence` → Verify: сначала
  failing tests, затем round-trip, unknown-field rejection и version-ref tests.
- [x] Реализовать `decision validate` для Git-tracked YAML → Verify: valid
  weekly-margin fixture даёт exit 0, malformed/unknown refs дают стабильный
  non-zero exit без DWH/BI/LLM.
- [x] Реализовать native `SemanticSource` snapshot и semantic diff → Verify:
  seeded formula/grain/time/policy/removal changes дают ожидаемый impact до
  metric, chart, product и owner.
- [x] Добавить Verified Asset Repository и typed result comparator → Verify:
  row-order, decimal tolerance, null/zero/missing и truncated-result fixtures.
- [x] Собрать content-addressed EvidenceBundle → Verify: deterministic digest,
  dangling `evidence_ref` rejection, path confinement и redaction tests.
- [x] Реализовать one-shot `decision verify` и pure Superset/DataLens compile
  contracts → Verify: four breaking seeds block, presentation-only change
  passes, mutation-spy подтверждает ноль remote creates. Шаги §8.2 6–7 (compile
  SQL + DWH-проверки) объявлены `check.not_implemented`, не пропущены молча.
- [ ] Провести pilot gate на одном реальном change → Verify: owner impact точен,
  review time измерен, evidence понятен владельцу; только затем решать вопрос о
  certified brief и recurring runs.
  **Блокер — не код: нужен ответ владельца, какой change берём, на каком стенде и
  чей impact меряем.** Техническая опора готова: гейт выносит вердикт, а
  `auto_bi decision evidence … --artifact report-summary-md` показывает владельцу,
  на чём этот вердикт стоит. Техническая база с тех пор выросла на локальных стендах:
  governed ratio шага 6 закрыт для обоих семейств движков (ClickHouse 24.8.14.39 и
  PostgreSQL 14.24), а шаг 7 наполовину — `dwh.access` и `dwh.freshness` работают под
  `--dwh` и проверены на тех же стендах. Что остаётся за владельцем — какой change, на
  каком стенде и чей impact меряем: это не код.

## Critical path

```text
contracts -> validate -> semantic diff -> VQR/result compare
          -> evidence bundle -> release gate -> pilot evidence
```

## Done When

- [x] Все critical seeded regressions блокируются, safe change не блокируется.
- [x] Supported results reconciled и каждый factual claim имеет evidence
  (claim без валидного `evidence_ref` не проходит сборку bundle).
- [x] Release gate не мутирует BI/DWH и запускается offline на fixtures.
- [x] Существующий v1 focused/full release gate остаётся зелёным (1748 тестов).
- [ ] Есть измеренный pilot outcome или честное решение сузить/остановить scope.

## Non-goals до pilot evidence

Новый dashboard editor, новые BI/DWH adapters, generic multi-agent framework,
PostgreSQL rewrite, scheduler, MCP, write-actions и causal claims.
