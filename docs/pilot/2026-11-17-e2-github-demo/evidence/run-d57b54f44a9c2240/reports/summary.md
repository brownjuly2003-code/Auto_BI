# Release gate — decision\:\/\/sales\/weekly\_margin\@1

**Verdict:** block
**Run:** run-d57b54f44a9c2240
**Base semantics:** sha256:d017cca0ab3352e2b139f1a43d3ee3829bd761cd9d0387d8619870f9a09e51e2
**Candidate semantics:** sha256:524d638e4aef23edaf16534bb739d80f101f44c28030d638b11b6263cfc71996
**Environment:** github\-demo
**Created:** 2026-11-18T02:07:48Z by verifier 139

Derived from the checks below (§9.2): this report restates what they found and adds
no finding of its own. The verdict above is the one those checks carry — the bundle's
own re-verification is graded after this report is sealed inside it, so it is reported
with the run, not here.

## Owner action

Do not release this candidate. Resolve the blocking findings in this report, then run the release gate again.

## What stops the release

- **impact.products** — `impact.reaches_product` — 1 of 1 change\(s\) reach this product\; 1 escalated from review to block because it is governed\; 1 delivered chart\(s\) named \(0 read only in part\)\; of the tiles not named\: 0 whose SQL could not be read\, 0 read only in part\, 1 read in whole but not settled against a changed metric

## Whose numbers this reaches

- **block** `metric.time_changed` — metric\:\/\/sales\/gross\_margin\_pct
  - time changed\: timezone Europe\/Moscow \-\> UTC
  - dashboards: dashboard\:\/\/weekly\_margin\@7
  - owners: commercial\-analytics
  - chart dashboard\:\/\/weekly\_margin\@7\#margin\_by\_region — computes_metric: measure sum\_gross\_margin\_per\_sum\_revenue reproduces the formula terms of metric\:\/\/sales\/gross\_margin\_pct
  - raised from review to block because a governed product depends on it

Charts this run could not resolve in whole — a tile missing from the lists above may be one of these rather than an absence, and a tile named above may appear here because part of its query was not read, or because its SQL does not settle whether it computes a governed metric:

- `chart.metric_unread` dashboard\:\/\/weekly\_margin\@7\#operator\_table — this tile\'s SQL does not say whether it computes a governed metric\: the resolver does not read an aggregate\'s shape out of arbitrary SQL

## What needs a human

- **semantic.diff** — `semantic.changed` — 1 semantic change\(s\) between the snapshots

## What was not done

- **results.reconcile** — `result.no_expectations` — no verified asset declares an expected result for this product
- **results.provenance** — `provenance.no_results` — no candidate result was supplied\, so there is no origin to establish
- **dwh.access** — `check.not_configured` — no engine was configured for this run\, so the release was not verified against a live environment \(§8\.2 step 7\)
- **dwh.freshness** — `check.not_configured` — no engine was configured for this run\, so the release was not verified against a live environment \(§8\.2 step 7\)
- **dwh.physical** — `check.not_configured` — no engine was configured for this run\, so the release was not verified against a live environment \(§8\.2 step 7\)
- **dwh.security** — `check.not_configured` — no engine was configured for this run\, so the release was not verified against a live environment \(§8\.2 step 7\)
- **dwh.cost** — `check.not_configured` — no engine was configured for this run\, so the release was not verified against a live environment \(§8\.2 step 7\)

## How to read this evidence

- If `results.reconcile` is `result.equivalent`, it means only that the supplied result rows agree; it is not semantic approval.
- When `results.provenance` is established, it means a supplied attestation matches the reviewed query and semantic snapshot, and, when this run declares one, the environment. It does not independently prove that SQL executed.
- `dwh.cost` bounds concern compiled metric reference SQL; they do not measure the captured reviewed asset SQL.
- Unavailable numeric counters stay unknown. Successful execution under engine-enforced limits can confirm bounds without an exact numeric measurement; neither `cost.not_measured` nor a skipped check is a pass.
- A successfully verified signature authenticates the producer and bundle integrity, but it does not prove SQL execution or production sign-off. Verify it separately with `auto_bi decision evidence` and a trusted public key.
- The report is sealed before the bundle verification check. It does not assert that this bundle is signed or verified.

## Every check

| check | status | outcome | detail |
|---|---|---|---|
| `contracts.validate` | pass | `contracts.clean` | 2 metric contract\(s\) resolved\, 0 issue\(s\)\; 1 of 1 metric pin\(s\) match the candidate snapshot |
| `semantic.gaps` | pass | `semantic.representable` | both snapshots represent their source completely |
| `semantic.diff` | review | `semantic.changed` | 1 semantic change\(s\) between the snapshots |
| `impact.products` | block | `impact.reaches_product` | 1 of 1 change\(s\) reach this product\; 1 escalated from review to block because it is governed\; 1 delivered chart\(s\) named \(0 read only in part\)\; of the tiles not named\: 0 whose SQL could not be read\, 0 read only in part\, 1 read in whole but not settled against a changed metric |
| `assets.invalidated` | pass | `assets.valid` | 0 verified asset\(s\) still stand as evidence |
| `results.reconcile` | advisory | `result.no_expectations` | no verified asset declares an expected result for this product |
| `results.provenance` | advisory | `provenance.no_results` | no candidate result was supplied\, so there is no origin to establish |
| `sql.compile` | pass | `sql.compiled` | 1 of 1 metric\(s\) compiled to a reference query\; 0 moved between the snapshots\, 0 not compiled offline |
| `dwh.access` | advisory | `check.not_configured` | no engine was configured for this run\, so the release was not verified against a live environment \(§8\.2 step 7\) |
| `dwh.freshness` | advisory | `check.not_configured` | no engine was configured for this run\, so the release was not verified against a live environment \(§8\.2 step 7\) |
| `dwh.physical` | advisory | `check.not_configured` | no engine was configured for this run\, so the release was not verified against a live environment \(§8\.2 step 7\) |
| `dwh.security` | advisory | `check.not_configured` | no engine was configured for this run\, so the release was not verified against a live environment \(§8\.2 step 7\) |
| `dwh.cost` | advisory | `check.not_configured` | no engine was configured for this run\, so the release was not verified against a live environment \(§8\.2 step 7\) |
| `bi.contract` | advisory | `bi.compiled` | 1 dashboard\(s\) compiled without touching the BI |
