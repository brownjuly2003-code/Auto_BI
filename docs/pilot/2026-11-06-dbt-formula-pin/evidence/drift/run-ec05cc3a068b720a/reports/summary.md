# Release gate — decision\:\/\/sales\/weekly\_margin\@1

**Verdict:** block
**Run:** run-ec05cc3a068b720a
**Base semantics:** sha256:a06579581d5bc9ace529da90b739cd7b846c97fd81ac3e36e288ebdc6cc96732
**Candidate semantics:** sha256:b0f38b5bbf990294096851cd14ea57d620d489d6eadca1e431b1bdb3ef9f4d01
**Environment:** synthetic\-offline
**Created:** 2026-11-06T12:00:00Z by verifier 133

Derived from the checks below (§9.2): this report restates what they found and adds
no finding of its own. The verdict above is the one those checks carry — the bundle's
own re-verification is graded after this report is sealed inside it, so it is reported
with the run, not here.

## Owner action

Do not release this candidate. Resolve the blocking findings in this report, then run the release gate again.

## What stops the release

- **semantic.gaps** — `semantic.gaps_present` — 2 gap\(s\) across both snapshots\, 1 blocking
- **semantic.diff** — `semantic.changed` — 5 semantic change\(s\) between the snapshots
- **impact.products** — `impact.reaches_product` — 2 of 5 change\(s\) reach this product\; 2 escalated from review to block because it is governed\; 0 delivered chart\(s\) named \(0 read only in part\)\; of the tiles not named\: 0 whose SQL could not be read\, 0 read only in part\, 0 read in whole but not settled against a changed metric

## Whose numbers this reaches

- **block** `metric.formula_changed` — metric\:\/\/sales\/gross\_margin\_pct
  - formula changed
  - dashboards: dashboard\:\/\/weekly\_margin\@7
  - owners: commercial\-analytics
  - raised from review to block because a governed product depends on it
- **block** `metric.binding_changed` — metric\:\/\/sales\/gross\_margin\_pct
  - external definition changed\: closure sha256\:318854cc18766c041ff79d50c440841402e225ef54bd64521e6178be2e29b2db \-\> sha256\:e13a91d635e199637759b448c2db2f59a602d74a8fc3127197e243d5d9d9a53c
  - dashboards: dashboard\:\/\/weekly\_margin\@7
  - owners: commercial\-analytics
  - raised from review to block because a governed product depends on it

Charts this run could not resolve in whole — a tile missing from the lists above may be one of these rather than an absence, and a tile named above may appear here because part of its query was not read, or because its SQL does not settle whether it computes a governed metric:

- `chart.formula_untyped` metric\:\/\/sales\/gross\_margin\_pct — the formula carries no typed terms\, so no tile can be shown to compute it

## What was not done

- **results.reconcile** — `result.no_expectations` — no verified asset declares an expected result for this product
- **results.provenance** — `provenance.no_results` — no candidate result was supplied\, so there is no origin to establish
- **sql.compile** — `sql.not_compiled` — 0 of 1 metric\(s\) compiled to a reference query\; 0 moved between the snapshots\, 1 not compiled offline
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
| `contracts.validate` | pass | `contracts.clean` | 1 metric contract\(s\) resolved\, 0 issue\(s\)\; 1 of 1 metric pin\(s\) match the candidate snapshot |
| `semantic.gaps` | block | `semantic.gaps_present` | 2 gap\(s\) across both snapshots\, 1 blocking |
| `semantic.diff` | block | `semantic.changed` | 5 semantic change\(s\) between the snapshots |
| `impact.products` | block | `impact.reaches_product` | 2 of 5 change\(s\) reach this product\; 2 escalated from review to block because it is governed\; 0 delivered chart\(s\) named \(0 read only in part\)\; of the tiles not named\: 0 whose SQL could not be read\, 0 read only in part\, 0 read in whole but not settled against a changed metric |
| `assets.invalidated` | pass | `assets.valid` | 0 verified asset\(s\) still stand as evidence |
| `results.reconcile` | advisory | `result.no_expectations` | no verified asset declares an expected result for this product |
| `results.provenance` | advisory | `provenance.no_results` | no candidate result was supplied\, so there is no origin to establish |
| `sql.compile` | advisory | `sql.not_compiled` | 0 of 1 metric\(s\) compiled to a reference query\; 0 moved between the snapshots\, 1 not compiled offline |
| `dwh.access` | advisory | `check.not_configured` | no engine was configured for this run\, so the release was not verified against a live environment \(§8\.2 step 7\) |
| `dwh.freshness` | advisory | `check.not_configured` | no engine was configured for this run\, so the release was not verified against a live environment \(§8\.2 step 7\) |
| `dwh.physical` | advisory | `check.not_configured` | no engine was configured for this run\, so the release was not verified against a live environment \(§8\.2 step 7\) |
| `dwh.security` | advisory | `check.not_configured` | no engine was configured for this run\, so the release was not verified against a live environment \(§8\.2 step 7\) |
| `dwh.cost` | advisory | `check.not_configured` | no engine was configured for this run\, so the release was not verified against a live environment \(§8\.2 step 7\) |
| `bi.contract` | pass | `bi.compiled` | 1 dashboard\(s\) compiled without touching the BI |
