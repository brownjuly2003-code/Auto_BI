# Performance baseline (plan_sol step 12)

Offline, local-only measurements — no DWH, BI, LLM, or Docker.

## What runs in CI

`tests/test_perf_baseline.py` asserts **soft absolute ceilings** and **relative
ratios** (large synthetic model vs small). Failures mean catastrophic regression,
not a tight production SLO.

## CI live samples (not a production SLO)

The closing-SHA CI harness labels its descriptive output
`evidence_class=ci_sample_not_production_slo`; it is **not a production SLO**.
The `runtime-evidence-latency` artifact records one warmup and `N=3` measured
deterministic live Superset builds. It uses nearest-rank percentiles, so
`p95 = max` for N=3, and applies no timing threshold.

The `runtime-evidence-container` artifact measures release-image cold start from
the epoch immediately before the named `smoke` container starts until it becomes
healthy. Exact process RSS comes from `/proc/1/status`, while Docker cgroup memory usage
is retained as context. The catastrophic CI gates are cold start
<= 90000 ms and process RSS <= 1024 MiB.

The harness is installed, but both closing-SHA artifacts remain pending until
external CI runs. No observed live values or production-SLO closure are claimed.

## Local artifact

On each green run of `test_autospec_and_validate_budgets`, the test writes
`last_local_baseline.json` in this directory (gitignored timings are machine-
specific; the file may appear untracked — do not treat it as a golden).

## Operator restore drill

```bash
uv run python scripts/store_backup.py restore-drill data/auto_bi.sqlite
uv run python scripts/store_backup.py backup data/auto_bi.sqlite /backup/auto_bi.sqlite
uv run python scripts/store_backup.py check /backup/auto_bi.sqlite
```

See `docs/DEPLOYMENT.md` §7 and `docs/operations/SLO.md`.
