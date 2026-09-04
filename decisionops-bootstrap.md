# DecisionOps Release Gate bootstrap

> **Где стоим (2026-09-04):** задачи 1–7 и технический Mac v6 pilot закрыты; в задаче 8
> остался только owner review time/clarity.
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
  passes, mutation-spy подтверждает ноль remote creates. Шаг §8.2 6 (compile SQL)
  компилируется там, где семантика точна офлайн, и называет гэп там, где нет; шаг 7
  (DWH-проверки) закрыт целиком — все пять проверок работают под `--dwh` и говорят
  `check.not_configured` без него, а не молчат.
- [ ] Провести pilot gate на одном реальном change → Verify: owner impact точен,
  review time измерен, evidence понятен владельцу; только затем решать вопрос о
  certified brief и recurring runs.
  Change, стенд и impact уже определены: `revenue` timezone change проверен на Mac против
  ClickHouse 24.8.14.39 для зависимого `weekly_revenue` и независимого `weekly_orders`.
  Финальный технический прогон verifier 6 лежит в
  `.pilot-work/2026-09-02-mac-revenue-tz/evidence-v6-revenue-catalog`
  (`run-d4fbae2250ae82d8`,
  `sha256:03bbfa06def6ce00843a72aa968f10915ae3c66db02304ae554722a0de5240ac`, exit 4 /
  `block`) и `evidence-v6-control-catalog` (`run-0d5046d273b62f86`,
  `sha256:b433671ce72d1da5bbf65d580dae6db4c68774b2e6be34d6e5c77d3c75ed3293`, exit 3 /
  `needs_review`). Оба bundle содержат по 19 artifacts, дают 0 problems при независимом
  чтении и фиксируют `dwh.security=security.unfiltered`. После proxy-review контрольный
  отчёт явно показывает `impact.none` перед review-находками и поясняет, что review не
  означает изменения чисел этого продукта. Это улучшает отчёт, но не измеряет человеческое
  понимание: остаток task 8 — владелец читает его, сообщает время чтения и понятность.
  **2026-08-25 — репетиция проведена** (`docs/DECISIONOPS_PILOT_2026-08-25.md`): четыре
  прогона против живого ClickHouse на графе из двух продуктов — контроль, тот же change для
  зависимого и для независимого продукта, и расхождение числа против verified asset, чьё
  ожидаемое значение снято со стенда. Гейт ~1.6 с, детерминирован, все bundle
  перепроверяются из `docs/pilot/`. Репетиция нашла и починила два дефекта грейдинга
  (чужие impact и assets вменялись проверяемому продукту) и тихую перезапись запечатанного
  bundle. Не измерено и не может быть измерено агентом: время чтения отчёта владельцем и
  его понятность — это и есть остаток task 8.

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
- [x] Существующий v1 focused/full release gate остаётся зелёным: v1-тесты не правились,
  полный suite 1906 passed / 20 skipped на 2026-08-25.
- [ ] Есть измеренный pilot outcome или честное решение сузить/остановить scope.

## Non-goals до pilot evidence

Новый dashboard editor, новые BI/DWH adapters, generic multi-agent framework,
PostgreSQL rewrite, scheduler, MCP, write-actions и causal claims.
