# Auto_BI — текущее состояние

Единая точка входа «что сейчас правда» для операторов и агентов.
Фазовая история — [PLAN.md](PLAN.md); дизайн — [ARCHITECTURE.md](ARCHITECTURE.md);
ADR — [adr/](adr/); операторский roadmap-аудит (внутренний) — `plan_sol_23_07_26.md`
в корне (gitignored hygiene, не публичный).

**Версия пакета:** см. `auto_bi.__version__` / `pyproject.toml` (ratchet в
`tests/test_docs_defaults.py`).

## Продукт

| Слой | Статус | Gate |
|---|---|---|
| Text / fields / auto → IR → SQL-guard → BI | production (v1 path) | unit + golden replay + live-stand E2E (Superset/CH) |
| ClickHouse + Superset | **v1 release-gated** | CI quality + integration |
| Greenplum advisor + golden | **offline contract in CI** | advisor GP + golden GP replay |
| Greenplum live DWH | experimental | operator stand |
| DataLens compile path | offline unit | contract suite |
| DataLens live stand | **experimental** (Mac-only) | not default release gate |
| Public HF demo | live Space | auto-only default; assert_demo_profile |

## Безопасность и runtime (plan_sol 1–4)

- Demo profile capabilities honest (`/health.capabilities`, auto-only default).
- `send_samples` default **false**; classification gate for any opt-in samples.
- `SafeError` + secret redaction on HTTP / SSE / Store / logs.
- Compose binds DWH/BI to loopback; `AUTO_BI_PROFILE=local|demo|production`.

## Governance / release (plan_sol 5–6)

- GitHub main + `v*` tag rulesets **active**; Dependabot security updates on.
- Release workflow: build → Trivy → image SBOM → push `:version` → attest;
  `:latest` + GH Release only in `finalize`. Residual: evidence on next live `v*` tag.
- Demo install uses `uv.lock` (`uv sync --frozen`).

## Архитектурные контракты (plan_sol 7–8)

- BI adapter: `BuildContext` / `BuildResult`, required `delete_artifact` / `close`.
- Atomic Store commit for build + session + ledger; `delivered_pending` on ledger
  fault after BI delivery. Residual: stable build_token idempotency; BI pre-return outbox.

## Eval / CI matrix (plan_sol 9–10)

| Suite | Count | Mode |
|---|---:|---|
| Golden fixtures (CH + GP) | **53** | offline replay 100% + fingerprints |
| Golden cases CH | 37 | model.yaml |
| Golden cases GP | 16 | model_gp.yaml |
| Advisor CH | 9 | offline |
| Advisor GP | 6 | offline |

CI: Python 3.12 primary quality, 3.13 latest, Windows CLI smoke, dependency-resolution
matrix, offline browser E2E (text / failed-build retry / SSE late-connect / **reload
resume**). Residual: fields DnD E2E; live GHA after merge PR path.

## Docs-as-code (plan_sol 11)

| Artifact | Role |
|---|---|
| **This file** | current product + residual roadmap |
| [ENV_REFERENCE.md](ENV_REFERENCE.md) | **generated** full Settings env inventory |
| [USER_GUIDE.md](USER_GUIDE.md) | operator how-to + curated env table |
| [DEPLOYMENT.md](DEPLOYMENT.md) | profiles, release, protection |
| [EVAL_FIXTURES.md](EVAL_FIXTURES.md) | replay / record / sentinel procedure |
| `tests/test_docs_defaults.py` | defaults / version / CLI ratchet |
| `tests/test_docs_as_code.py` | env ref freshness, internal links, eval counts |

## SLO / recovery (plan_sol 12 core)

| Control | Status |
|---|---|
| Online SQLite backup + integrity | `Store.backup_to` / `integrity_check`; `scripts/store_backup.py` |
| Restore drill | script + `tests/test_store_backup.py` |
| Offline perf baseline | `tests/test_perf_baseline.py` (soft abs + relative ratios) |
| Property / metamorphic | `tests/test_property_quality.py` (guard, validate, normalize) |
| Ops doc | [operations/SLO.md](operations/SLO.md) |

Residual step 12: live p50/p95, full mutation score (mutmut), mypy-strict module roll-out,
process memory / cold-start on release image.

## Re-audit (plan_sol 13 core offline)

Evidence: [operations/REAUDIT_plan_sol_23_07_26.md](operations/REAUDIT_plan_sol_23_07_26.md).
Offline score **8.8/10** (was 8.2). Finding R1 fixed (SafeError test fake vs `build(spec, ctx)`).
Live Space / GHA / `v*` tag / mutmut remain residual.

## Открытый residual (не блокирует core claims)

1. **Release live evidence** — next `v*` tag: image digest ↔ SBOM ↔ provenance.
2. **Dependabot PR triage** — no bulk-merge; one-by-one after CI green.
3. **Step 8 residual** — stable build_token from approve; durable outbox before adapter return.
4. **Step 10 residual** — fields drag-drop E2E (UI resume after reload **done**).
5. **Step 11 residual** — full ARCHITECTURE current/history split; optional Field(description=) on every Settings key.
6. **Step 12 residual** — live SLO, mutmut score, mypy-strict modules (core offline gates done).
7. **Step 13 residual** — live integration/browser/Space/tag evidence on pushed SHA.

## Что не является source of truth

- Root `plan_*.md`, `audit_*.md`, `_NEXT_SESSION.md` — рабочие/внутренние (gitignore + hygiene gate).
- `docs/PLAN.md` — **фаза 0–4 history**, не «что делать завтра».
- Корневой `plan.md` — legacy public stub; prefer this file for current status.
