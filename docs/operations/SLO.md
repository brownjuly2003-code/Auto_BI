# SLO, recovery, quality gates (plan_sol step 12)

Working document for measurable operations. Not a customer SLA contract.

## Recovery: SQLite store

| Control | How |
|---|---|
| Online backup | `Store.backup_to(path)` or `uv run python scripts/store_backup.py backup <src> <dest>` |
| Integrity | `Store.integrity_check()` / `scripts/store_backup.py check` → expects `ok` |
| Restore drill | `scripts/store_backup.py restore-drill <src> [dest]` — backup, open, integrity, session smoke |
| Automated | `tests/test_store_backup.py` (unit) |

Do **not** `cp` a live store file under writers (torn read). Prefer online backup
(same family as `sqlite3 ".backup …"` in DEPLOYMENT §7).

## Performance baseline (offline)

| Probe | Where | Gate |
|---|---|---|
| autospec small/large synthetic | `tests/test_perf_baseline.py` | absolute soft ms + large/small ratio |
| validate_spec after defaults | same | absolute soft ms + ratio |
| SQLite N writes + backup | same | absolute soft ms |
| max_charts bound | same | structural: `len(charts) <= max_charts` |

No paid LLM, no Docker, no DWH in these probes. Local timings may be written to
`tests/performance/last_local_baseline.json` (machine-specific, not a release golden).

## Property / metamorphic quality

| Area | Suite |
|---|---|
| SQL guard allow/deny + multi-statement | `tests/test_property_quality.py` |
| IR validate unknown table/measure, duplicate ids | same |
| normalize idempotence | same |
| RBAC schema filters (membership, filter joins, forbidden monotonicity, raw_sql hatch) | same |
| Superset native filters (spec/wiring equivalence, order invariance, scope partition) | same |

## Mutation smoke

CI installs `mutmut==3.6.0` and mutates the cumulative ordered scope
`auto_bi/agent/sql_guard.py`, `auto_bi/ir/validate.py`. The bounded run uses
`tests/test_property_quality.py`, `tests/test_ir_validate.py`,
`tests/test_x5_raw_sql.py`, `tests/test_p1_6_governance.py`, and
`tests/test_query_plan.py`. The latest cumulative run in a separate Linux
environment produced 696/696 effective kills with no weak outcomes and completed
in 46 seconds wall-clock (mutmut: 21.62 mutations/second).
`scripts/check_mutation_stats.py` rejects surviving, uncovered, skipped, suspicious,
interrupted, and crashed mutants. Timed-out mutants count toward effective kills;
`killed + timeout` must equal `total`.

## Mypy package-wide strict gate

Canonical gate: `mypy --strict auto_bi`. Both supported Python CI jobs run it.
Targeting the package automatically covers current and future modules under
`auto_bi`.

```bash
uv run --with mypy --with types-PyYAML mypy --strict auto_bi
```

Do **not** set `strict = true` as a global or per-module override in `pyproject`
without verifying: on our mypy version that polluted the package-wide check.
The CLI/CI `--strict` gate is intentional; keep the config non-strict and apply
strictness only at the gate.

## Residual (not yet gates)

- Tight p50/p95 production SLO from live stands (Mac/CI integration).
- Extend bounded mutation coverage to dataset planning and ownership cleanup.
- Process memory / cold-start process budget on release image.
