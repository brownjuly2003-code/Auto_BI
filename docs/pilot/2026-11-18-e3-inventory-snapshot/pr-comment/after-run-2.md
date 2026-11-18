<!-- auto-bi-decision-gate:cde7d76ca177c4b5 -->
### Auto_BI release gate: block

- **Product:** `decision://sales/weekly_margin@1`
- **Verdict:** block, exit code 4 (checks: 1 block, 1 review, 7 advisory, of 15)
- **Run:** `run-ef964cc8a6ce7f3c`, created `2026-11-18T17:33:37Z`
- **Evidence manifest:** `sha256:03113fdd2c30fd7f2658ddccce04b9a0e2f410cd50d54516b6caedb8478c308a` (`evidence.verified`)
- **Semantics:** base `sha256:d017cca0ab3352e2b139f1a43d3ee3829bd761cd9d0387d8619870f9a09e51e2`, candidate `sha256:524d638e4aef23edaf16534bb739d80f101f44c28030d638b11b6263cfc71996`

Do not release this candidate. Resolve the blocking findings in this report, then run the release gate again.

#### What stops the release

- `impact.products` · `impact.reaches_product` — 1 of 1 change\(s\) reach this product\; 1 escalated from review to block because it is governed\; 0 delivered chart\(s\) named \(0 read only in part\)\; of the tiles not named\: 0 whose SQL could not be read\, 2 read only in part\, 0 read in whole but not settled against a changed metric

#### Whose numbers this reaches

- **block** `metric.time_changed` · `metric://sales/gross_margin_pct` — raised from review because a governed product depends on it
  - time changed\: timezone Europe\/Moscow \-\> UTC
  - dashboards: `dashboard://weekly_margin@7`
  - owners: `commercial-analytics`

Charts this run could not resolve in whole: `chart.metric_unread` 2, `chart.sql_partial` 2. A tile missing above may be one of them; the evidence summary lists them.

#### What needs a human

- `semantic.diff` · `semantic.changed` — 1 semantic change\(s\) between the snapshots

#### Not done in this run

- `results.reconcile` · `result.no_expectations` — no verified asset declares an expected result for this product
- `results.provenance` · `provenance.no_results` — no candidate result was supplied\, so there is no origin to establish
- `dwh.access` · `check.not_configured` — no engine was configured for this run\, so the release was not verified against a live environment \(§8\.2 step 7\)
- `dwh.freshness` · `check.not_configured` — no engine was configured for this run\, so the release was not verified against a live environment \(§8\.2 step 7\)
- `dwh.physical` · `check.not_configured` — no engine was configured for this run\, so the release was not verified against a live environment \(§8\.2 step 7\)
- `dwh.security` · `check.not_configured` — no engine was configured for this run\, so the release was not verified against a live environment \(§8\.2 step 7\)
- `dwh.cost` · `check.not_configured` — no engine was configured for this run\, so the release was not verified against a live environment \(§8\.2 step 7\)

<details>
<summary>Every check (15)</summary>

| check | status | outcome |
|---|---|---|
| `contracts.validate` | pass | `contracts.clean` |
| `semantic.gaps` | pass | `semantic.representable` |
| `semantic.diff` | review | `semantic.changed` |
| `impact.products` | block | `impact.reaches_product` |
| `assets.invalidated` | pass | `assets.valid` |
| `results.reconcile` | advisory | `result.no_expectations` |
| `results.provenance` | advisory | `provenance.no_results` |
| `sql.compile` | pass | `sql.compiled` |
| `dwh.access` | advisory | `check.not_configured` |
| `dwh.freshness` | advisory | `check.not_configured` |
| `dwh.physical` | advisory | `check.not_configured` |
| `dwh.security` | advisory | `check.not_configured` |
| `dwh.cost` | advisory | `check.not_configured` |
| `bi.contract` | pass | `bi.compiled` |
| `evidence.bundle` | pass | `evidence.verified` |

</details>

---

Derived from this run's checks and the `impact.products` payload sealed in its evidence; it adds no finding of its own. The bundle is the record, and its `reports/summary.md` the whole report. Check the bundle with `auto_bi decision evidence <evidence> --expect-digest sha256:03113fdd2c30fd7f2658ddccce04b9a0e2f410cd50d54516b6caedb8478c308a`.

Commit `c4997b37b82956a6617e51ce8b056e12e6886cfc` · [workflow run](https://github.com/brownjuly2003-code/Auto_BI/actions/runs/44359980051)
