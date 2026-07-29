# Adversarial re-audit (plan_sol step 13) — offline evidence

**Date:** 2026-07-23
**Repo:** Auto_BI (`brownjuly2003-code/Auto_BI`)
**Evidence SHA:** `6e926fe` (local main; parent `5cfb518`).
**Package version:** `0.4.0` (`auto_bi.__version__` / `pyproject.toml`)
**Prior audit:** root `audit_gpt_23_07_26.md` (internal/gitignored), base `5fc5c7d`, score **8.2/10**.
**Size (tracked):** ~187 `*.py`, ~19.4k LOC `auto_bi/`, ~23.6k LOC `tests/`, ~308 tracked files.

## 1. Verdict

Offline + API-verified governance evidence supports **production use of the v1 path
(ClickHouse + Superset)** with **accepted residual risks** listed in Â§5. Public demo and
local/production profiles are fail-closed relative to the July 22 audit P0s.

**Updated score (offline re-audit): 8.8 / 10** (was 8.2).

| Area | Jul 22 | Jul 23 offline | Notes |
|---|---:|---:|---|
| Architecture / contracts | 8.7 | **9.1** | Adapter Protocol + BuildResult; atomic build commit |
| Correctness / tests | 9.4 | **9.4** | Golden 53/53 replay; advisor 9+6; regression fixed in reaudit |
| Security / privacy | 7.3 | **8.7** | samples default off, SafeError, profiles, leak-canary green |
| Release / supply chain | 7.7 | **8.5** | Trivy-before-`:latest`, lock in demo; live tag residual |
| Operations | 7.5 | **8.4** | production profile, backup/restore drill, soft SLO |
| Maintainability | 7.8 | **8.0** | property tests + docs-as-code ratchets |
| Documentation | 6.3 | **8.6** | CURRENT_STATE, ENV_REFERENCE, USER_GUIDE fixes |

**Confidence:** high for offline Python/CI graph/docs; medium for live Space, live GHA
on unpushed commits, DataLens/GP live stands, paid LLM sentinel.

This is **not** a claim of zero residual risk. Step 13 is **core offline** here; live
integration/browser/Space/tag evidence remains residual until a release SHA is on
`origin/main` and stands are exercised.

## 2. Evidence matrix (this session)

| Check | Result | Notes |
|---|---|---|
| `ruff check .` | PASS | |
| `black --check auto_bi tests` | PASS after reformat `test_web_e2e_offline.py` | |
| `mypy auto_bi` | PASS, 80 files | non-strict baseline |
| `check_repo_hygiene.py` | PASS, 307 tracked | |
| Security/contract suite (164) | PASS after fix | safe_error, prompt_data, deployment_profile, adapter_contract, session_snapshot, store_backup, docs_*, release_promotion, github_protection, compatibility, property, perf |
| Advisor CH | **9/9** | |
| Advisor GP | **6/6** | |
| Golden replay CH | **37/37** | fingerprint enforced |
| Golden replay GP | **16/16** | |
| Settings defaults | `send_samples=False`, `auth_enabled=False`, `profile=local`, empty DataLens password | |
| ENV_REFERENCE `--check` | PASS (66 keys) | |
| Store restore-drill script | PASS | `.tmp` scratch |
| GitHub rulesets API | **active** main `19601118`, tags `19601122` | |
| Dependabot security updates | **enabled** | |
| Secret scanning + push protection | **enabled** | |
| pypi env `prevent_self_review` | false (solo, documented) | accepted residual |
| Live integration E2E @ this SHA | **not run** | residual (Mac/CI after push) |
| Live browser E2E @ this SHA | **not run** | residual |
| Public Space assert @ this SHA | **not run** | residual |
| Live `v*` release digestâ†”SBOM | **not run** | residual step 6 |
| Paid live LLM sentinel | **not run** | protocol: no paid without budget |
| Full pytest+cov 90% | **not run this session** | memory budget; CI quality job remains SoT |
| mutmut score | **not run** | residual step 12 |

## 3. Finding fixed during re-audit

### R1 â€” SafeError BI HTTP path broken for step-7 adapter signature (test + signal)

- **Symptom:** `tests/test_safe_error.py::test_pipeline_store_channel_strips_marker_on_adapter_error`
  expected `bi.http_error`, got `internal.error`.
- **Root cause:** pipeline always calls `adapter.build(spec, ctx)` (plan_sol step 7).
  The leak-canary fake still used `build(self, spec)` only â†’ `TypeError` â†’ mapped to
  `internal.error`, so the suite no longer proved SupersetAPIError â†’ store channel mapping.
- **Fix:** update fake to `build(self, spec, ctx=None)` + required `delete_artifact`/`close`.
- **Impact:** test defect after step 7; production adapters already use the new signature.
  No production SafeError mapping bug for real SupersetAPIError (unit-proved separately).
- **Status:** fixed in same commit as this reaudit evidence.

## 4. Closed audit P0/P1 themes (vs Jul 22)

| Theme | Status |
|---|---|
| P0 demo profile honesty | Closed (code + assert script; live secret operator residual) |
| P0 send_samples default | Closed (`False` + classification) |
| P0 SafeError / redaction | Closed (leak-canary suite green after R1) |
| P0 Compose / profiles | Closed (loopback + production fail-closed) |
| P1 GitHub required checks | Closed (rulesets active) |
| P1 release promotion graph | Closed in workflow; live tag residual |
| P1 adapter contract | Closed |
| P1 atomic build state | Closed, including RR-4 pre-return cleanup recovery (2026-07-29 follow-up) |
| P1 eval fingerprint trust | Closed offline |
| P1 compatibility matrix | Closed core offline |
| P2 docs drift | Closed core (CURRENT_STATE + ENV_REFERENCE + ratchets) |
| P2 SLO / restore | Closed core offline |

## 5. Residual risks (owner = maintainer unless noted)

| ID | Risk | Severity | Compensating control | Next |
|---|---|---|---|---|
| RR-1 | Live tag never exercised new release graph | P2 | Offline workflow tests; no `:latest` before finalize | Next `v*` cut |
| RR-2 | Solo pypi self-review allowed | P2 | Documented; admins_bypass exists | Second reviewer |
| RR-3 | Stable build_token idempotency incomplete | P2 | New token per attempt; session 409 rules | Step 8 residual |
| RR-4 | **Closed 2026-07-29:** durable attempt before adapter return + exact adapter cleanup | Closed | Schema v9 / ADR 0002 / process-death test | Maintain contract |
| RR-5 | Browser UI no session hydrate after reload | P2 | API resume unit green | Step 10 residual |
| RR-6 | Fields DnD E2E missing | P3 | Manual / unit paths | Step 10 residual |
| RR-7 | Live GHA / Space not proven on unpushed SHA | P2 | Offline gates; PR path after push | Push via PR |
| RR-8 | mutmut / live p50 not in CI | P3 | Property + soft perf baselines | Step 12 residual |
| RR-9 | Open Dependabot PRs not triaged | P3 | Groups configured; no bulk-merge | Manual triage |
| RR-10 | HF Space secret may still force demo text mode | P2 | Code default auto-only; assert script | Operator HF UI |

## 6. Production-readiness statement

**Ship v1 (CH + Superset) offline-ready:** yes, for deployments that:

1. set `AUTO_BI_PROFILE=production` (or demo with known profile rules);
2. do not enable `SEND_SAMPLES` without classification review;
3. run online SQLite backup (`scripts/store_backup.py` / `Store.backup_to`);
4. accept residuals RR-1..RR-10 as non-blocking for the offline contract.

**Do not claim:** zero residual, DataLens/GP live as release-gated, or browser resume/fields E2E.

## 7. How to reproduce evidence

```bash
git rev-parse HEAD
uv run ruff check .
uv run black --check auto_bi tests
uv run --with mypy --with types-PyYAML mypy auto_bi
uv run python scripts/check_repo_hygiene.py
uv run pytest tests/test_safe_error.py tests/test_prompt_data.py \
  tests/test_deployment_profile.py tests/test_adapter_contract.py \
  tests/test_session_snapshot.py tests/test_store_backup.py \
  tests/test_docs_as_code.py tests/test_docs_defaults.py \
  tests/test_release_promotion.py tests/test_github_protection.py \
  tests/test_compatibility_matrix.py tests/test_property_quality.py \
  tests/test_perf_baseline.py -q
uv run auto_bi eval --suite advisor --model-path semantic/model.yaml
uv run auto_bi eval --suite advisor --model-path semantic/model_gp.yaml
uv run auto_bi eval --suite golden --llm-mode replay \
  --fixtures-dir tests/fixtures/golden_llm --model-path semantic/model.yaml
uv run auto_bi eval --suite golden --llm-mode replay \
  --fixtures-dir tests/fixtures/golden_llm --model-path semantic/model_gp.yaml
uv run python scripts/apply_github_protection.py --status
uv run python scripts/generate_env_reference.py --check
```

Live residual (after PR merge; Mac/CI as appropriate):

```bash
# CI quality + integration + browser E2E on the merged SHA
# deploy/hf-demo/assert_demo_profile.py https://<space>.hf.space
# next v* tag: image digest â†” SBOM â†” provenance
```
