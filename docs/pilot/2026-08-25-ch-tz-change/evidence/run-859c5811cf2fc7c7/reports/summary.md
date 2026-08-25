# Release gate — decision://sales/weekly_margin@1

**Verdict:** block
**Run:** run-859c5811cf2fc7c7
**Base semantics:** sha256:7409530315409d0ec69e2b06afedd7fc2d5ecd432e6895bb5f67df79645f110a
**Candidate semantics:** sha256:348bb39a3e37d09babc2e0e1eace1798837717a9ec5b5015892deb0cf076c44d
**Environment:** pilot-ch
**Created:** 2026-08-25T21:00:00Z by verifier 1

Derived from the checks below (§9.2): this report restates what they found and adds
no finding of its own. The verdict above is the one those checks carry — the bundle's
own re-verification is graded after this report is sealed inside it, so it is reported
with the run, not here.

## What stops the release

- **impact.products** — `impact.reaches_product` — 1 change(s) reach this product; 1 escalated from review to block because it is governed

## Whose numbers this reaches

- **block** `metric.time_changed` — metric://sales/gross_margin_pct
  - time changed
  - dashboards: dashboard://weekly_margin@7
  - owners: commercial-analytics
  - raised from review to block because a governed product depends on it

## What needs a human

- **semantic.diff** — `semantic.changed` — 1 semantic change(s) between the snapshots
- **dwh.freshness** — `freshness.stale` — dm.sales_daily is dated 2026-08-24 (45.0h before 2026-08-25T21:00:00Z, read as 2026-08-24T00:00:00Z while the metric declares timezone UTC), older than the PT6H this product declares

## What was not done

- **results.reconcile** — `result.no_expectations` — no verified asset declares an expected result for this product

## Every check

| check | status | outcome | detail |
|---|---|---|---|
| `contracts.validate` | pass | `contracts.clean` | 1 metric contract(s) resolved, 0 issue(s) |
| `semantic.gaps` | pass | `semantic.representable` | both snapshots represent their source completely |
| `semantic.diff` | review | `semantic.changed` | 1 semantic change(s) between the snapshots |
| `impact.products` | block | `impact.reaches_product` | 1 change(s) reach this product; 1 escalated from review to block because it is governed |
| `assets.invalidated` | pass | `assets.valid` | 0 of 0 verified asset(s) can no longer stand as evidence |
| `results.reconcile` | advisory | `result.no_expectations` | no verified asset declares an expected result for this product |
| `sql.compile` | pass | `sql.compiled` | 1 of 1 metric(s) compiled to a reference query; 0 moved between the snapshots, 0 not compiled offline |
| `dwh.access` | pass | `access.read_only` | gate_ro reads every in-scope mart on 127.0.0.1:8124/dm, and only reads |
| `dwh.freshness` | review | `freshness.stale` | dm.sales_daily is dated 2026-08-24 (45.0h before 2026-08-25T21:00:00Z, read as 2026-08-24T00:00:00Z while the metric declares timezone UTC), older than the PT6H this product declares |
| `dwh.physical` | pass | `physical.as_declared` | every column the in-scope metrics read is on 127.0.0.1:8124/dm with the declared type, and the layout matches what the model claims |
| `dwh.security` | pass | `security.unfiltered` | nothing narrows what gate_ro reads on 127.0.0.1:8124/dm: no row policy applies to it and it may read every column the in-scope metrics are made of |
| `dwh.cost` | pass | `cost.within_budget` | every governed query stays inside the ceiling its metric declares on 127.0.0.1:8124/dm |
| `bi.contract` | pass | `bi.compiled` | 1 dashboard(s) compiled without touching the BI |
