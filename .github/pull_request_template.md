<!-- Keep the PR scoped to one change. Fill every section that applies; delete the rest. -->

## Summary

<!-- What changed and why (1–5 sentences). Link the issue / plan_sol step if any. -->

## Type of change

- [ ] Bug fix
- [ ] Feature / enhancement
- [ ] Security hardening
- [ ] Docs only
- [ ] CI / release / infra
- [ ] Dependency bump (Dependabot or manual)

## Security checklist

- [ ] No secrets, tokens, passwords, recovery codes, or live DSN in code, tests, fixtures, logs, or docs
- [ ] No new path that sends DWH *values* to an external LLM unless `AUTO_BI_SEND_SAMPLES` opt-in + classification allow it
- [ ] User-facing errors stay SafeError-shaped (no provider body, URI credentials, cookies, or raw stack traces)
- [ ] New/changed HTTP surface respects auth/RBAC and demo capability gates where relevant
- [ ] Dependency / image pin changes include a reason and, if available, a CVE id

## Data / privacy

- [ ] Samples / top-values / free-text paths still go through `prompt_data` sanitization
- [ ] Classification defaults not weakened (confidential/restricted never send samples)
- [ ] No new logging of sample values or connection secrets

## Docs

- [ ] USER_GUIDE / DEPLOYMENT / ARCHITECTURE / SECURITY / CHANGELOG updated when public behaviour or defaults change
- [ ] `docs/CURRENT_STATE.md` updated when product status or residual roadmap changes
- [ ] `.env.example` matches new Settings fields and defaults
- [ ] After Settings field/default change: regenerate `docs/ENV_REFERENCE.md`
  (`uv run python scripts/generate_env_reference.py`) and keep `test_docs_as_code` green
- [ ] Migration note added if a default flips or a public response shape breaks
- [ ] Documented defaults still match `Settings` (`tests/test_docs_defaults.py` green)
- [ ] No new claim wider than CI release gates (see `tests/test_compatibility_matrix.py`)
- [ ] Version string consistent: `pyproject.toml` ↔ `auto_bi.__version__`

## Release / deploy (if this PR touches publish paths)

- [ ] `uv.lock` / Dockerfile / `deploy/hf-demo/*` / release workflow reviewed together
- [ ] Demo assert / image smoke considered (manual `demo-image` workflow if HF payload changes)
- [ ] No mutable `latest` promotion without scan/SBOM gates (step 6)

## Test plan

```bash
# minimum gate (mirrors CI `quality`)
uv run ruff check .
uv run black --check auto_bi tests
uv run --with mypy --with types-PyYAML mypy auto_bi
uv run --with pytest-cov --with duckdb pytest -q --cov=auto_bi --cov-report=term-missing --cov-fail-under=90
uv run auto_bi eval --suite advisor --model-path semantic/model.yaml
```

- [ ] Above gate green locally (or only docs/workflow-only change — say so)
- [ ] New/changed behaviour covered by unit or contract tests
- [ ] Prompt / advisor change: golden or advisor eval noted above
- [ ] Integration / browser E2E needed? (yes → describe stand + result)

## Risk / rollback

<!-- Blast radius, feature flag, or how to revert. "None" is fine for pure docs. -->
