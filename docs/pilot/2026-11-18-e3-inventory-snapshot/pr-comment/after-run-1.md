<!-- auto-bi-decision-gate:cde7d76ca177c4b5 -->
### Auto_BI release gate: pass_with_advisory

- **Product:** `decision://sales/weekly_margin@1`
- **Verdict:** pass_with_advisory, exit code 0 (checks: 0 block, 0 review, 7 advisory, of 15)
- **Run:** `run-d876cd78cd433d80`, created `2026-11-18T17:32:31Z`
- **Evidence manifest:** `sha256:5c4827d8e9e320e399e6156830c7429a7c2916b6fcc94bb97b208cbbe5c571d3` (`evidence.verified`)
- **Semantics:** base `sha256:d017cca0ab3352e2b139f1a43d3ee3829bd761cd9d0387d8619870f9a09e51e2`, candidate `sha256:d017cca0ab3352e2b139f1a43d3ee3829bd761cd9d0387d8619870f9a09e51e2`

Release is not blocked by this gate. Read the advisory findings before release.

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
| `semantic.diff` | pass | `semantic.unchanged` |
| `impact.products` | pass | `impact.none` |
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

Derived from this run's checks and the `impact.products` payload sealed in its evidence; it adds no finding of its own. The bundle is the record, and its `reports/summary.md` the whole report. Check the bundle with `auto_bi decision evidence <evidence> --expect-digest sha256:5c4827d8e9e320e399e6156830c7429a7c2916b6fcc94bb97b208cbbe5c571d3`.

Commit `fbfd6c0b21a8bcd7260a6ffe1e2df71d224eb289` · [workflow run](https://github.com/brownjuly2003-code/Auto_BI/actions/runs/44359744528)
