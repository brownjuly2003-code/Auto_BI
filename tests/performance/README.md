# Performance baseline (plan_sol step 12)

Offline, local-only measurements — no DWH, BI, LLM, or Docker.

## What runs in CI

`tests/test_perf_baseline.py` asserts **soft absolute ceilings** and **relative
ratios** (large synthetic model vs small). Failures mean catastrophic regression,
not a tight production SLO.

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
