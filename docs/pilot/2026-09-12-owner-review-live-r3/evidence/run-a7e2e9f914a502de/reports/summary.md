# Release gate — decision://sales/weekly_margin@1

**Verdict:** block
**Run:** run-a7e2e9f914a502de
**Base semantics:** sha256:6686e782cde5f3c53f30793052f736c9a41a1722090ee43ef2d480b391f3df7e
**Candidate semantics:** sha256:07a6d4053a8c71f8b96f99a17c2713a1b9b62a02192945d28d6133ccae5258a2
**Environment:** owner-review-mac-native-ch24.8.14.39
**Created:** 2026-09-12T15:38:21Z by verifier 14

Derived from the checks below (§9.2): this report restates what they found and adds
no finding of its own. The verdict above is the one those checks carry — the bundle's
own re-verification is graded after this report is sealed inside it, so it is reported
with the run, not here.

## Owner action

Do not release this candidate. Resolve the blocking findings in this report, then run the release gate again.

## What stops the release

- **impact.products** — `impact.reaches_product` — 1 of 1 change(s) reach this product; 1 escalated from review to block because it is governed; 1 delivered chart(s) named, 1 the IR could not resolve

## Whose numbers this reaches

- **block** `metric.time_changed` — metric://sales/gross_margin_pct
  - time changed: timezone Europe/Moscow -> UTC
  - dashboards: dashboard://weekly_margin@7
  - owners: commercial-analytics
  - chart `dashboard://weekly_margin@7#margin_by_region` — computes_metric: measure sum_gross_margin_per_sum_revenue reproduces the formula terms of metric://sales/gross_margin_pct
  - raised from review to block because a governed product depends on it

Charts this run could not resolve — a tile missing from the lists above may be one of these, not an absence:

- `chart.opaque_sql` dashboard://weekly_margin@7#operator_table — operator SQL: the IR does not say which columns this tile reads

## What needs a human

- **semantic.diff** — `semantic.changed` — 1 semantic change(s) between the snapshots
- **assets.invalidated** — `asset.metric_changed` — asset://sales/weekly_margin_by_region@3: re-review required: metric.time_changed on metric://sales/gross_margin_pct
- **dwh.freshness** — `freshness.stale` — dm.sales_daily is dated 2026-09-12 (15.6h before 2026-09-12T15:38:21Z, read as 2026-09-12T00:00:00Z while the metric declares timezone UTC), older than the PT6H this product declares

## What was not done

- every step of §8.2 produced evidence in this run

## Every check

| check | status | outcome | detail |
|---|---|---|---|
| `contracts.validate` | pass | `contracts.clean` | 1 metric contract(s) resolved, 0 issue(s) |
| `semantic.gaps` | pass | `semantic.representable` | both snapshots represent their source completely |
| `semantic.diff` | review | `semantic.changed` | 1 semantic change(s) between the snapshots |
| `impact.products` | block | `impact.reaches_product` | 1 of 1 change(s) reach this product; 1 escalated from review to block because it is governed; 1 delivered chart(s) named, 1 the IR could not resolve |
| `assets.invalidated` | review | `asset.metric_changed` | asset://sales/weekly_margin_by_region@3: re-review required: metric.time_changed on metric://sales/gross_margin_pct |
| `results.reconcile` | pass | `result.equivalent` | 1 of 1 expected result(s) reconciled |
| `results.provenance` | pass | `provenance.established` | 1 of 1 candidate result(s) traced to the reviewed query, snapshot and environment of this run |
| `sql.compile` | pass | `sql.compiled` | 1 of 1 metric(s) compiled to a reference query; 0 moved between the snapshots, 0 not compiled offline |
| `dwh.access` | pass | `access.read_only` | gate_ro reads every in-scope mart on 127.0.0.1:18123/dm, and only reads |
| `dwh.freshness` | review | `freshness.stale` | dm.sales_daily is dated 2026-09-12 (15.6h before 2026-09-12T15:38:21Z, read as 2026-09-12T00:00:00Z while the metric declares timezone UTC), older than the PT6H this product declares |
| `dwh.physical` | pass | `physical.as_declared` | every column the in-scope metrics read is on 127.0.0.1:18123/dm with the declared type, and the layout matches what the model claims |
| `dwh.security` | pass | `security.unfiltered` | nothing narrows what gate_ro reads on 127.0.0.1:18123/dm: no row policy applies to it and it may read every column the in-scope metrics are made of |
| `dwh.cost` | pass | `cost.within_budget` | every governed query stays inside the ceiling its metric declares on 127.0.0.1:18123/dm |
| `bi.contract` | advisory | `bi.compiled` | 1 dashboard(s) compiled without touching the BI |
