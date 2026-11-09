# Release gate — decision\:\/\/sales\/weekly\_margin\@1

**Verdict:** pass_with_advisory
**Run:** run-d13507ded3ecfb04
**Base semantics:** sha256:a06579581d5bc9ace529da90b739cd7b846c97fd81ac3e36e288ebdc6cc96732
**Candidate semantics:** sha256:a06579581d5bc9ace529da90b739cd7b846c97fd81ac3e36e288ebdc6cc96732
**Environment:** synthetic\-offline
**Created:** 2026-11-06T12:00:00Z by verifier 133

Derived from the checks below (§9.2): this report restates what they found and adds
no finding of its own. The verdict above is the one those checks carry — the bundle's
own re-verification is graded after this report is sealed inside it, so it is reported
with the run, not here.

## Owner action

Release is not blocked by this gate. Read the advisory findings before release.

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
| `contracts.validate` | pass | `contracts.clean` | 1 metric contract\(s\) resolved\, 0 issue\(s\)\; 1 of 1 metric pin\(s\) match the candidate snapshot |
| `semantic.gaps` | pass | `semantic.representable` | both snapshots represent their source completely |
| `semantic.diff` | pass | `semantic.unchanged` | 0 semantic change\(s\) between the snapshots |
| `impact.products` | pass | `impact.none` | 0 of 0 change\(s\) reach this product\; 0 escalated from review to block because it is governed\; 0 delivered chart\(s\) named \(0 read only in part\)\; of the tiles not named\: 0 whose SQL could not be read\, 0 read only in part\, 0 read in whole but not settled against a changed metric |
| `assets.invalidated` | pass | `assets.valid` | 0 verified asset\(s\) still stand as evidence |
| `results.reconcile` | advisory | `result.no_expectations` | no verified asset declares an expected result for this product |
| `results.provenance` | advisory | `provenance.no_results` | no candidate result was supplied\, so there is no origin to establish |
| `sql.compile` | pass | `sql.compiled` | 1 of 1 metric\(s\) compiled to a reference query\; 0 moved between the snapshots\, 0 not compiled offline |
| `dwh.access` | advisory | `check.not_configured` | no engine was configured for this run\, so the release was not verified against a live environment \(§8\.2 step 7\) |
| `dwh.freshness` | advisory | `check.not_configured` | no engine was configured for this run\, so the release was not verified against a live environment \(§8\.2 step 7\) |
| `dwh.physical` | advisory | `check.not_configured` | no engine was configured for this run\, so the release was not verified against a live environment \(§8\.2 step 7\) |
| `dwh.security` | advisory | `check.not_configured` | no engine was configured for this run\, so the release was not verified against a live environment \(§8\.2 step 7\) |
| `dwh.cost` | advisory | `check.not_configured` | no engine was configured for this run\, so the release was not verified against a live environment \(§8\.2 step 7\) |
| `bi.contract` | pass | `bi.compiled` | 1 dashboard\(s\) compiled without touching the BI |
