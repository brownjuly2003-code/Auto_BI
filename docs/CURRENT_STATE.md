# Auto_BI — текущее состояние

Единая точка входа «что сейчас правда» для операторов и агентов.

- [ARCHITECTURE.md](ARCHITECTURE.md) — текущий дизайн и обязательные инварианты.
- [ARCHITECTURE_HISTORY.md](ARCHITECTURE_HISTORY.md) — история эволюции дизайна
  и доказательств поставки.
- [PLAN.md](PLAN.md) — фазовая история; [adr/](adr/) — принятые решения.
- Операторский roadmap-аудит (внутренний) — `plan_sol_23_07_26.md` в корне
  (gitignored hygiene, не публичный).

**Версия пакета:** см. `auto_bi.__version__` / `pyproject.toml` (ratchet в
`tests/test_docs_defaults.py`).

**Режим:** software release **v0.5.0** complete на exact `main`/tag SHA
`e78076d2d00ddc1748bf6e22f13cf7cb93fc6515`. Локальный/software closure scope
закрыт; пять вынесенных external validations получили exact live evidence
2026-07-29. HF Space остаётся `owner-de-scoped` (не mandatory). Scope и
disposition — [PROJECT_CLOSURE.md](PROJECT_CLOSURE.md).

## Продукт

| Слой | Статус | Gate |
|---|---|---|
| Text / fields / auto → IR → SQL-guard → BI | production (v1 path) | unit + golden replay + live-stand E2E (Superset/CH) |
| ClickHouse + Superset | **v1 release-gated** | CI quality + integration |
| Greenplum advisor + golden | **offline contract in CI** | advisor GP + golden GP replay |
| Greenplum live DWH | experimental | operator stand |
| DataLens compile path | offline contracts **passed** | contract suite |
| DataLens live stand | **`closed`** 2026-07-29: Mac-only self-hosted contract **15/15 passed**; текущий image seed использует workbook `z4wtz6tg5194o`, переданный через supported env override | experimental / non-default / **non-closure** |
| Public HF demo | **`owner-de-scoped`** | not a project-closure target; **no** sync/publish/decommission. Last recorded evidence is **historical only**: Space served `0.4.0`, `demo_auto_only=false`, no capabilities (stale vs v0.5.0; not a verified current launch path) |

## Безопасность и runtime (plan_sol 1–4)

- Demo profile capabilities honest (`/health.capabilities`, auto-only default).
- `send_samples` default **false**; classification gate for any opt-in samples.
- `SafeError` + secret redaction on HTTP / SSE / Store / logs.
- Compose binds DWH/BI to loopback; `AUTO_BI_PROFILE=local|demo|production`.

## Governance / release (plan_sol 5–6)

- GitHub `main` + `v*` tag rulesets **active without bypass**; Dependabot open
  queue empty after sequential disposition.
- Protected-tag mutation rejection **live-проверен 2026-07-29**: canary
  [`v-retag-smoke-20260729`](https://github.com/brownjuly2003-code/Auto_BI/tree/v-retag-smoke-20260729)
  создан на remote `main` `13fc855`; попытка force-update на другой remote
  object отклонена GitHub с HTTP 422 (`Cannot update this protected ref` /
  `Cannot force-push to this tag`), ref остался неизменным; release workflow
  для canary не запускался.
- Intentional Trivy-fail-before-promotion **live-проверен** run
  [30512999822](https://github.com/brownjuly2003-code/Auto_BI/actions/runs/30512999822):
  Trivy отклонил **23** исправимых HIGH/CRITICAL findings в probe-image,
  workflow завершился ожидаемым failure, а GHCR `:latest` сохранил digest
  `sha256:bff75bcef9d894e86c2be63a584284c02c2b2546425ac11a15e83ad83e4e84f1`;
  временная remote branch удалена.
- **v0.5.0 external evidence (complete):**
  - post-merge CI, CodeQL, Gitleaks, Demo image — passed on closing SHA
    `e78076d2d00ddc1748bf6e22f13cf7cb93fc6515`;
  - protected annotated tag `v0.5.0`;
  - release run
    [30488281836](https://github.com/brownjuly2003-code/Auto_BI/actions/runs/30488281836)
    passed preflight, PyPI trusted publish, Trivy-before-push, source/image SBOM,
    Python/image provenance, latest promotion, GitHub Release, release status gate;
  - public release
    [v0.5.0](https://github.com/brownjuly2003-code/Auto_BI/releases/tag/v0.5.0);
  - PyPI `autobi-agent` 0.5.0 wheel + sdist;
  - GHCR `0.5.0` and `latest` share digest
    `sha256:bff75bcef9d894e86c2be63a584284c02c2b2546425ac11a15e83ad83e4e84f1`;
  - live Superset/browser integration passed.
- Demo install uses `uv.lock` (`uv sync --frozen`).

## Архитектурные контракты (plan_sol 7–8)

- BI adapter: `BuildContext` / `BuildResult`, required `reconcile_build_attempt` /
  `delete_artifact` / `close`.
- Store schema v9: durable spec snapshot + fingerprint written before remote build;
  startup cleanup-only reconciliation precedes the legacy reaper. Atomic success commits
  attempt + build + session + ledger; `delivered_pending` handles a post-delivery ledger
  fault. Stable `build_token` per `(session, spec_row)` keeps delivered retry idempotent.

## Eval / CI matrix (plan_sol 9–10)

| Suite | Count | Mode |
|---|---:|---|
| Golden fixtures (CH + GP) | **53** | offline replay 100% + fingerprints |
| Golden cases CH | 37 | model.yaml |
| Golden cases GP | 16 | model_gp.yaml |
| Advisor CH | 9 | offline |
| Advisor GP | 6 | offline |

CI: Python 3.12 primary quality, 3.13 latest, Windows CLI smoke, dependency-resolution
matrix, offline browser E2E (text / failed-build retry / SSE late-connect / reload
resume / **fields DnD seed**). Post-merge CI on v0.5.0 closing SHA passed.

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
| Pre-return BI crash recovery | RR-4 durable attempts + exact Superset/DataLens cleanup ([ADR 0002](adr/0002-durable-build-attempt-reconciliation.md)); live DataLens process-death smoke: child exit 97, remote delivery confirmed, discovered/deleted 3/3, session failed |
| Offline perf baseline | `tests/test_perf_baseline.py` (soft abs + relative ratios) |
| Property / metamorphic | `tests/test_property_quality.py` (guard, validate, normalize, **RBAC**, Superset native-filter scope) |
| Cumulative bounded mutation gate | CI targets `auto_bi/agent/sql_guard.py`, `auto_bi/ir/validate.py`, `auto_bi/agent/dataset_plan.py`, `auto_bi/agent/cleanup.py`; weak outcomes rejected; detailed snapshot evidence in [operations/SLO.md](operations/SLO.md) |
| Mypy package-wide strict | package-wide `mypy --strict auto_bi` in both supported CI jobs |
| Ops doc | [operations/SLO.md](operations/SLO.md) |

Residual step 12: closed on 2026-07-29. Descriptive CI samples (run
[30481369602](https://github.com/brownjuly2003-code/Auto_BI/actions/runs/30481369602);
**not** production SLO guarantees): p50 **2353.619 ms**, p95 **2359.740 ms**,
container cold start **6341 ms**, PID1 RSS **89.594 MiB**, cgroup memory
**74.93 MiB**.

## Re-audit (plan_sol 13 core offline)

Evidence: [operations/REAUDIT_plan_sol_23_07_26.md](operations/REAUDIT_plan_sol_23_07_26.md).
Offline score **8.8/10** (was 8.2). Finding R1 fixed (SafeError test fake vs `build(spec, ctx)`).
GHA / protected `v0.5.0` tag / package-image publish evidence complete. HF Space
is **owner-de-scoped** (historical stale profile only; no sync/publish/decommission).

## Closure disposition

Прежний open-ended residual больше не является активным local backlog.
Disposition tokens: `closed` / `owner-de-scoped` / `externally-blocked` /
`still-open`; исходная 24-row mapping сохранена в root
`plan_audit_closure_29_07.md`, а текущая disposition зафиксирована в
[PROJECT_CLOSURE.md](PROJECT_CLOSURE.md): **closed 21 · owner-de-scoped 3 ·
externally-blocked 0 · still-open 0**. Software release v0.5.0 complete;
локально actionable `still-open` строк **нет**. Exact live/manual checks without
durable run evidence are **not** labelled `closed`.

External validation re-audit (**completed with real runs; not software-release
blockers**):

- DataLens live — `closed`: Mac process-table exhaustion устранён после
  owner-authorized остановки runaway `~/atv2/main.py`; self-hosted stand поднят
  read-only к demo-DM. Первый contract выявил только response-layout drift
  (**12/15**); evidence-backed dual-layout assertions сохранили behavioral
  проверки, финальный contract — **15/15 passed in 51.39 s**. Current image seed
  создал workbook `z4wtz6tg5194o`; historical default `ra7f79yirtumb` для этого
  stand не использовался;
- paid live-LLM canary/sentinel — `closed`: уже предоставленный Mistral
  credential найден по существующему secure route без чтения/вывода значения.
  `mistral-large-latest` прошёл **3/3** sentinel cases, **4** provider calls,
  **11,341** input + **1,491** output tokens, estimated cost **$0.007907** при
  hard cap **$1.50**;
- live Trivy-fail before `:latest` — `closed`: run
  [30512999822](https://github.com/brownjuly2003-code/Auto_BI/actions/runs/30512999822)
  ожидаемо failed после отклонения **23** исправимых HIGH/CRITICAL findings;
  `:latest` digest не изменился;
- process restart mid-delivery live smoke — `closed`: реальный child process
  завершён через `os._exit(97)` после DataLens delivery и до pipeline commit;
  remote dashboard/URL подтверждены, startup reconcile обнаружил и удалил
  **3/3** owned artifacts, ownership ledger не был преждевременно записан,
  session status = `failed`;
- public HF Space — `owner-de-scoped` (removed from closure scope);
- live same-SHA demo-image rebuild (HF path) — `owner-de-scoped` (with HF).

## Что не является source of truth

- Root `plan_*.md`, `audit_*.md`, `_NEXT_SESSION.md` — рабочие/внутренние (gitignore + hygiene gate).
- `docs/PLAN.md` — **фаза 0–4 history**, не «что делать завтра».
- Корневой `plan.md` — legacy public stub; prefer this file for current status.
