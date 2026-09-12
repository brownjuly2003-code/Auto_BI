# Release gate — decision://sales/weekly_margin@1

**Verdict:** block
**Run:** run-a7c5f8da2860617b
**Base semantics:** sha256:fd45b72f519890722167bb3289b4ff78534cdb8e3514c7588e71dd5ce785ef7c
**Candidate semantics:** sha256:ab007cee20f15a1df21abb681945f2bb94ba64c114a65c976cf286ee31328123
**Environment:** synthetic-offline
**Created:** 2026-09-12T16:00:00Z by verifier 14

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
- **assets.invalidated** — `asset.metric_changed` — asset://sales/weekly_margin_by_region@2: re-review required: metric.time_changed on metric://sales/gross_margin_pct
- **results.provenance** — `provenance.unattested` — asset://sales/weekly_margin_by_region@2: the candidate result was supplied without an attestation, so nothing records which query produced it, against which semantics, or where

## What was not done

- **dwh.access** — `check.not_configured` — no engine was configured for this run, so the release was not verified against a live environment (§8.2 step 7)
- **dwh.freshness** — `check.not_configured` — no engine was configured for this run, so the release was not verified against a live environment (§8.2 step 7)
- **dwh.physical** — `check.not_configured` — no engine was configured for this run, so the release was not verified against a live environment (§8.2 step 7)
- **dwh.security** — `check.not_configured` — no engine was configured for this run, so the release was not verified against a live environment (§8.2 step 7)
- **dwh.cost** — `check.not_configured` — no engine was configured for this run, so the release was not verified against a live environment (§8.2 step 7)

## Every check

| check | status | outcome | detail |
|---|---|---|---|
| `contracts.validate` | pass | `contracts.clean` | 2 metric contract(s) resolved, 0 issue(s) |
| `semantic.gaps` | pass | `semantic.representable` | both snapshots represent their source completely |
| `semantic.diff` | review | `semantic.changed` | 1 semantic change(s) between the snapshots |
| `impact.products` | block | `impact.reaches_product` | 1 of 1 change(s) reach this product; 1 escalated from review to block because it is governed; 1 delivered chart(s) named, 1 the IR could not resolve |
| `assets.invalidated` | review | `asset.metric_changed` | asset://sales/weekly_margin_by_region@2: re-review required: metric.time_changed on metric://sales/gross_margin_pct |
| `results.reconcile` | pass | `result.equivalent` | 1 of 1 expected result(s) reconciled |
| `results.provenance` | review | `provenance.unattested` | asset://sales/weekly_margin_by_region@2: the candidate result was supplied without an attestation, so nothing records which query produced it, against which semantics, or where |
| `sql.compile` | pass | `sql.compiled` | 1 of 1 metric(s) compiled to a reference query; 0 moved between the snapshots, 0 not compiled offline |
| `dwh.access` | advisory | `check.not_configured` | no engine was configured for this run, so the release was not verified against a live environment (§8.2 step 7) |
| `dwh.freshness` | advisory | `check.not_configured` | no engine was configured for this run, so the release was not verified against a live environment (§8.2 step 7) |
| `dwh.physical` | advisory | `check.not_configured` | no engine was configured for this run, so the release was not verified against a live environment (§8.2 step 7) |
| `dwh.security` | advisory | `check.not_configured` | no engine was configured for this run, so the release was not verified against a live environment (§8.2 step 7) |
| `dwh.cost` | advisory | `check.not_configured` | no engine was configured for this run, so the release was not verified against a live environment (§8.2 step 7) |
| `bi.contract` | advisory | `bi.compiled` | 1 dashboard(s) compiled without touching the BI |
