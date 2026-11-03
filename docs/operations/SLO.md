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
`auto_bi/agent/sql_guard.py`, `auto_bi/ir/validate.py`,
`auto_bi/agent/dataset_plan.py`, `auto_bi/agent/cleanup.py`. The bounded run uses
`tests/test_property_quality.py`, `tests/test_ir_validate.py`,
`tests/test_x5_raw_sql.py`, `tests/test_p1_6_governance.py`,
`tests/test_query_plan.py`, `tests/test_dataset_plan.py`,
`tests/test_native_filters.py`, `tests/test_pipeline.py`,
`tests/test_cli_prune.py`,
`tests/test_adapter_contract.py`,
`tests/test_adapter_factory.py`,
`tests/test_auth.py`,
`tests/test_autospec.py`,
`tests/test_bi_canonical_refusal.py`,
`tests/test_bi_compile_refusal.py`,
`tests/test_bi_contract_validation.py`,
`tests/test_build_attempt_reconciliation.py`,
`tests/test_capture_driver_failures.py`,
`tests/test_clickhouse_session_limits.py`,
`tests/test_datalens_chart.py`,
`tests/test_decision_chart_impact.py`,
`tests/test_decision_chart_impact_dashboard_scope.py`,
`tests/test_decision_chart_impact_dictionary.py`,
`tests/test_decision_chart_impact_join_get.py`,
`tests/test_environment_sql_guard.py`,
`tests/test_generated_sql_bytes.py`,
`tests/test_label_joins.py`,
`tests/test_lifecycle_close.py`,
`tests/test_machine.py`,
`tests/test_postgres_sessions.py`,
`tests/test_probe_completion.py`,
`tests/test_probe_safety.py`,
`tests/test_propose.py`,
`tests/test_result_capture.py`,
`tests/test_result_probe.py`,
`tests/test_result_provenance.py`,
`tests/test_result_reconcile_column_order.py`,
`tests/test_result_tolerance_contract.py`,
`tests/test_runtime_probe.py`,
`tests/test_runtime_setting_refusals.py`,
`tests/test_safe_error.py`,
`tests/test_sql_guard_clickhouse_array_join_paren.py`,
`tests/test_sql_guard_clickhouse_columns_reachability.py`,
`tests/test_sql_guard_clickhouse_cte_case.py`,
`tests/test_sql_guard_clickhouse_in_table.py`,
`tests/test_sql_guard_clickhouse_inline_alias.py`,
`tests/test_sql_guard_clickhouse_join_using_reachability.py`,
`tests/test_sql_guard_clickhouse_settings.py`,
`tests/test_sql_guard_clickhouse_star_reachability.py`,
`tests/test_sql_guard_clickhouse_table_cte_case.py`,
`tests/test_sql_guard_count_star.py`,
`tests/test_sql_guard_cte_visibility.py`,
`tests/test_sql_guard_having_qualify.py`,
`tests/test_sql_guard_mutation_cte_gates.py`,
`tests/test_sql_guard_parse_depth.py`,
`tests/test_sql_guard_postgres_cast_output.py`,
`tests/test_sql_guard_postgres_group_by.py`,
`tests/test_sql_guard_postgres_order_by.py`,
`tests/test_sql_guard_postgres_root_parenthesized.py`,
`tests/test_sql_guard_postgres_table_column_alias.py`,
`tests/test_sqlgen.py`,
`tests/test_sqlgen_compare.py`,
`tests/test_sqlgen_grain.py`,
`tests/test_sqlgen_histogram.py`,
`tests/test_sqlgen_lag.py`,
`tests/test_sqlgen_ratio.py`,
`tests/test_sqlgen_running_share.py`,
`tests/test_sqlgen_transforms.py`,
`tests/test_sqlgen_yoy.py`,
`tests/test_superset_adapter.py`,
`tests/test_verified_assets.py`, and
`tests/test_x3_retention.py`. The selection now contains 72 modules. The preceding
71-module selection measured on 2026-11-03 on
Mac with 4 cores, Python 3.13, mutmut 3.6.0 (`mutmut run --max-children 4`) produced
3011 mutants: 2614 killed, 2 timeout, 356 survived, and 39 no_tests, in
1553 seconds wall-clock (mutmut: 2.08 mutations/second). All weak mutants are in
44 functions of `sql_guard.py`; the other three targets have none. Survivors take
54% of run time because each runs all tests mapped to its function (8.6 seconds
on average versus 0.9 seconds for a killed mutant). Even with zero survivors,
the run would take about 11 minutes. The 45-minute CI timeout allows for the
Linux container with 3 CPUs. Later slices kill survivors and lower both the
baseline and the timeout.

`scripts/check_mutation_stats.py` rejects unchecked, skipped, suspicious,
type-check-caught, interrupted, and crashed mutants. Unknown exit codes are
suspicious. Timed-out mutants count toward effective kills;
`killed + timeout + survived + no_tests` must equal `total`. The gate reads
per-mutant outcomes from `mutants/**/*.meta` and rejects missing or malformed
inputs, empty runs, and any disagreement with `mutants/mutmut-cicd-stats.json`.
The ratchet in `scripts/mutation_baseline.json` limits `survived` and `no_tests`
separately for each function. A function absent from the baseline is allowed zero
weak mutants. Counts at or below the baseline pass; an increase fails with the
function, kind, actual count, and allowed count. The baseline must be lowered in
the same change when a count falls: the gate prints each lower count and also
reports baseline functions that produced no mutants.

## Mypy package-wide strict gate

Canonical gate: `mypy --strict auto_bi`. Both supported Python CI jobs run it.
Targeting the package automatically covers current and future modules under
`auto_bi`.

```bash
uv run --with mypy --with types-PyYAML mypy --strict auto_bi
```

Both jobs run on Linux, and mypy checks the platform it runs on. On Windows or macOS
the gate CI applies is `mypy --platform linux --strict auto_bi`; Windows-only code
stays behind a `sys.platform` check (CONTRIBUTING, "The gate"), which Linux skips, so
the 3.12 job checks it once more: `mypy --platform win32 --strict auto_bi`.

Do **not** set `strict = true` as a global or per-module override in `pyproject`
without verifying: on our mypy version that polluted the package-wide check.
The CLI/CI `--strict` gate is intentional; keep the config non-strict and apply
strictness only at the gate.

## Closing-SHA CI runtime samples

The CI harness records two closing-SHA artifacts with
`evidence_class=ci_sample_not_production_slo`. This evidence is not a production SLO:
it is a descriptive sample and must not be presented as production latency or
capacity.

- `runtime-evidence-latency` runs one warmup followed by `N=3` measured
  deterministic live Superset builds. It reports nearest-rank percentiles, where
  `p95 = max` for N=3, with no latency threshold.
- `runtime-evidence-container` measures release-image cold start from the epoch
  immediately before the named `smoke` container starts until its healthcheck is
  healthy. Exact process RSS comes from `/proc/1/status`; Docker cgroup memory usage
  is recorded separately as context. Catastrophic CI gates are
  cold start <= 90000 ms and process RSS <= 1024 MiB.

The harness is installed, but closing-SHA artifacts remain pending until the
external CI workflow runs. No live values or production-SLO closure are claimed
here.

## Residual (not yet gates)

- Tight p50/p95 production SLO from live stands (Mac/CI integration).
- Production-calibrated process memory and cold-start SLO for the release image.
