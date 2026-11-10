# Release gate — decision://sales/weekly_margin@1

**Verdict:** block
**Run:** run-5a210ec990a382f9
**Base semantics:** sha256:fd45b72f519890722167bb3289b4ff78534cdb8e3514c7588e71dd5ce785ef7c
**Candidate semantics:** sha256:fd45b72f519890722167bb3289b4ff78534cdb8e3514c7588e71dd5ce785ef7c
**Environment:** pilot-ch
**Created:** 2026-08-25T21:00:00Z by verifier 2

Derived from the checks below (§9.2): this report restates what they found and adds
no finding of its own. The verdict above is the one those checks carry — the bundle's
own re-verification is graded after this report is sealed inside it, so it is reported
with the run, not here.

## What stops the release

- **results.reconcile** — `result.not_equivalent` — 0 of 1 expected result(s) reconciled

## What needs a human

- **dwh.freshness** — `freshness.stale` — dm.sales_daily is dated 2026-08-24 (45.0h before 2026-08-25T21:00:00Z, read as 2026-08-24T00:00:00Z while the metric declares timezone Europe/Moscow), older than the PT6H this product declares

## What was not done

- every step of §8.2 produced evidence in this run

## Every check

| check | status | outcome | detail |
|---|---|---|---|
| `contracts.validate` | pass | `contracts.clean` | 2 metric contract(s) resolved, 0 issue(s) |
| `semantic.gaps` | pass | `semantic.representable` | both snapshots represent their source completely |
| `semantic.diff` | pass | `semantic.unchanged` | 0 semantic change(s) between the snapshots |
| `impact.products` | pass | `impact.none` | 0 of 0 change(s) reach this product; 0 escalated from review to block because it is governed |
| `assets.invalidated` | pass | `assets.valid` | 0 of 1 verified asset(s) can no longer stand as evidence |
| `results.reconcile` | block | `result.not_equivalent` | 0 of 1 expected result(s) reconciled |
| `sql.compile` | pass | `sql.compiled` | 1 of 1 metric(s) compiled to a reference query; 0 moved between the snapshots, 0 not compiled offline |
| `dwh.access` | pass | `access.read_only` | gate_ro reads every in-scope mart on 127.0.0.1:8124/dm, and only reads |
| `dwh.freshness` | review | `freshness.stale` | dm.sales_daily is dated 2026-08-24 (45.0h before 2026-08-25T21:00:00Z, read as 2026-08-24T00:00:00Z while the metric declares timezone Europe/Moscow), older than the PT6H this product declares |
| `dwh.physical` | pass | `physical.as_declared` | every column the in-scope metrics read is on 127.0.0.1:8124/dm with the declared type, and the layout matches what the model claims |
| `dwh.security` | pass | `security.unfiltered` | nothing narrows what gate_ro reads on 127.0.0.1:8124/dm: no row policy applies to it and it may read every column the in-scope metrics are made of |
| `dwh.cost` | pass | `cost.within_budget` | every governed query stays inside the ceiling its metric declares on 127.0.0.1:8124/dm |
| `bi.contract` | pass | `bi.compiled` | 1 dashboard(s) compiled without touching the BI |
