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

CI installs `mutmut==3.6.0` and mutates only
`auto_bi/agent/sql_guard.py`. The bounded run uses
`tests/test_property_quality.py`, `tests/test_x5_raw_sql.py`, and
`tests/test_query_plan.py`; the evidence run killed all 106 generated mutants.
`scripts/check_mutation_stats.py` rejects surviving, uncovered, skipped,
suspicious, interrupted, and crashed mutants. Timed-out mutants count toward
effective kills; `killed + timeout` must equal `total`.

## Mypy strict modules (plan_sol step 12 residual)

Package-wide: `mypy auto_bi` (default flags in `pyproject.toml`).

**Strict allowlist** (CI job `Mypy strict (boundary modules)`):

| Module | Why first |
|---|---|
| `auto_bi/auth.py` | pure RBAC + passwords; security-sensitive |
| `auto_bi/adapters/artifacts.py` | pure build namespace / naming |
| `auto_bi/adapters/base.py` | shared adapter types, contract validation, and lifecycle protocol |
| `auto_bi/errors.py` | public/store/SSE/log redaction and provider error boundary |
| `auto_bi/config.py` | security-sensitive settings defaults and env typo detection |
| `auto_bi/api/sessions.py` | session snapshot, restart hydration, and registry boundary |
| `auto_bi/store/db.py` | durable session, build, and BI artifact persistence boundary |
| `auto_bi/ir/validate.py` | pure semantic-model and dashboard-spec validation boundary |
| `auto_bi/ir/spec.py` | typed dashboard-spec schema and alias boundary |
| `auto_bi/semantic/model.py` | typed semantic-model schema and lookup boundary |
| `auto_bi/semantic/select.py` | deterministic semantic-context selection boundary |
| `auto_bi/semantic/render.py` | deterministic semantic-model prompt rendering boundary |
| `auto_bi/semantic/prompt_data.py` | sample classification and prompt-data policy boundary |
| `auto_bi/semantic/dbt_import.py` | typed dbt artifact enrichment boundary |
| `auto_bi/agent/sql_guard.py` | SELECT-only, complexity, and table-access security boundary |
| `auto_bi/agent/query_plan.py` | compiled query-plan and runtime probe boundary |
| `auto_bi/agent/dataset_plan.py` | dataset ownership and native-filter planning boundary |
| `auto_bi/agent/pipeline.py` | compile/build and ownership-cleanup orchestration boundary |
| `auto_bi/deployment_profile.py` | fail-closed deployment-profile and serve-time safety boundary |
| `auto_bi/llm/budget.py` | fail-closed LLM spend/call budget boundary at the provider-call seam |
| `auto_bi/api/ratelimit.py` | fail-closed login/session quota and SSE-concurrency boundary |
| `auto_bi/api/schemas.py` | API request/response and session-hydration schema boundary |

```bash
uv run --with mypy --with types-PyYAML mypy --strict \
  auto_bi/auth.py auto_bi/adapters/artifacts.py auto_bi/adapters/base.py \
  auto_bi/errors.py auto_bi/config.py auto_bi/api/sessions.py auto_bi/store/db.py \
  auto_bi/ir/validate.py auto_bi/ir/spec.py auto_bi/semantic/model.py \
  auto_bi/semantic/select.py auto_bi/semantic/render.py \
  auto_bi/semantic/prompt_data.py auto_bi/semantic/dbt_import.py \
  auto_bi/agent/sql_guard.py auto_bi/agent/query_plan.py \
  auto_bi/agent/dataset_plan.py auto_bi/agent/pipeline.py auto_bi/deployment_profile.py \
  auto_bi/llm/budget.py auto_bi/api/ratelimit.py auto_bi/api/schemas.py
```

Do **not** set `strict = true` as a global or per-module override in `pyproject`
without verifying: on our mypy version that polluted the package-wide check.
Grow the allowlist module-by-module after each target is clean under `--strict`.

## Residual (not yet gates)

- Tight p50/p95 production SLO from live stands (Mac/CI integration).
- Extend bounded mutation coverage to `ir/validate`, dataset planning, and
  ownership cleanup; SQL guard is already gated.
- Process memory / cold-start process budget on release image.
- Expand mypy-strict allowlist beyond `auth` + `artifacts` + `errors` + `config`
  + `adapters/base` + `api/sessions` + `store/db` + `ir/validate` + `ir/spec`
  + `semantic/model` + `semantic/select`
  + `semantic/render` + `semantic/prompt_data` + `semantic/dbt_import`
  + `agent/sql_guard` + `agent/query_plan` + `agent/dataset_plan`
  + `agent/pipeline` + `deployment_profile` + `llm/budget` + `api/ratelimit` + `api/schemas`.
