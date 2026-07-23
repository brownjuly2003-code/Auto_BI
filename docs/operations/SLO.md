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

## Mypy strict modules (plan_sol step 12 residual)

Package-wide: `mypy auto_bi` (default flags in `pyproject.toml`).

**Strict allowlist** (CI job `Mypy strict (boundary modules)`):

| Module | Why first |
|---|---|
| `auto_bi/auth.py` | pure RBAC + passwords; security-sensitive |
| `auto_bi/adapters/artifacts.py` | pure build namespace / naming |
| `auto_bi/errors.py` | public/store/SSE/log redaction and provider error boundary |

```bash
uv run --with mypy --with types-PyYAML mypy --strict \
  auto_bi/auth.py auto_bi/adapters/artifacts.py auto_bi/errors.py
```

Do **not** set `strict = true` as a global or per-module override in `pyproject`
without verifying: on our mypy version that polluted the package-wide check.
Grow the allowlist module-by-module after each target is clean under `--strict`.

## Residual (not yet gates)

- Tight p50/p95 production SLO from live stands (Mac/CI integration).
- Full mutmut/cosmic-ray mutation score for `ir/validate`, `sql_guard`, dataset
  planning, ownership cleanup — characterization via property tests only for now.
- Process memory / cold-start process budget on release image.
- Expand mypy-strict allowlist beyond `auth` + `artifacts` + `errors`.
