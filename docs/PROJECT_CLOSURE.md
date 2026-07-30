# Project closure

Дата фиксации scope: 2026-07-27. Владелец повторно открыл остаточные пункты
аудита для финального closure-прохода 2026-07-29. Docs-only reconciliation
24 historical rows — 2026-07-29 (branch `release/verify-v0.5.0-20260729`).

## Закрываемый scope

Финальный scope проекта — текущий v1 product path:

- text / fields / auto → IR → SQL guard → ClickHouse + Superset;
- offline-контракты Greenplum и DataLens;
- default-deny prompt data policy, SafeError и capability-gated demo;
- atomic Store commit, durable pre-return build-attempt recovery, stable build-token retry
  и adapter lifecycle;
- golden/advisor replay, offline browser E2E, backup/restore и docs-as-code;
- текущий cumulative bounded mutation gate и package-wide `mypy --strict auto_bi`.

После software release **v0.5.0** этот scope feature-frozen. Новые функции и
исследовательские расширения не являются незакрытым долгом проекта. Hugging Face
Space **не** входит в closing scope (owner-de-scoped).

## Финальное решение по прежним residual

| Residual | Решение при закрытии |
|---|---|
| Durable outbox до возврата BI adapter | `closed` 2026-07-29: schema v9 + cleanup-only adapter reconciliation (ADR 0002) |
| Полный current/history split ARCHITECTURE | `closed`: current design отделён от `ARCHITECTURE_HISTORY.md`, ADR остаются отдельными решениями |
| `Field(description=)` на каждом Settings key | `closed` 2026-07-29: 66/66 descriptions + generated ENV_REFERENCE ratchet |
| Cumulative bounded mutation gate | `closed` 2026-07-29: настроенные production targets закреплены в CI, weak outcomes запрещены; snapshot evidence — в [operations/SLO.md](operations/SLO.md) |
| Package-wide `mypy --strict auto_bi` | `closed` 2026-07-29: package-wide gate действует в обоих поддерживаемых CI jobs |
| Live p50/p95, process-memory и cold-start campaign | `closed` 2026-07-29: descriptive CI samples from run [30481369602](https://github.com/brownjuly2003-code/Auto_BI/actions/runs/30481369602) — p50 2353.619 ms, p95 2359.740 ms, container cold start 6341 ms, PID1 RSS 89.594 MiB, cgroup memory 74.93 MiB (**descriptive CI samples, not production SLO guarantees**) |
| Paid live-LLM canary | `externally-blocked`; **not run** — credential Mistral заявлена владельцем; в tracked sentinel/runtime нет **прямого** Mistral provider и mapping секрета; tracked providers — `anthropic`/`gracekelly`; внешний маршрут GraceKelly→Mistral **не** верифицирован; exact live run not done; requires explicit owner budget approval; optional validation, **non-closure** |
| DataLens offline contracts | `closed` (offline passed) |
| DataLens live stand | `externally-blocked`; **not run** — каталоги/конфиг Mac-стенда на месте; на read-only probe 2026-07-29 сервисы остановлены/не ready; exact live contract не гонялся; experimental / non-default / **non-closure** (not a release or project-closure gate) |
| Public HF demo vs closing SHA | `owner-de-scoped`: HF is **not** a project-closure target; **no** sync / publish / decommission. Last recorded live evidence is **historical only** — Space served **0.4.0**, `demo_auto_only=false`, no capabilities object (incompatible with v0.5.0; not a verified current demo path) |

Новые функции вне этой таблицы по-прежнему требуют отдельного проекта.

## Software release vs optional external validation

### Completed (software release v0.5.0)

Exact `main`/tag SHA: `e78076d2d00ddc1748bf6e22f13cf7cb93fc6515`.

- post-merge CI, CodeQL, Gitleaks, Demo image — passed;
- Dependabot open queue empty after sequential disposition;
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
- `main` and `v*` tag rulesets active without bypass;
- live Superset/browser integration passed.

Software/local audit closure: **complete** for the v1 path above where exact
live/manual checks have durable run evidence. Mapping of the 24 historical
plan_sol residual rows — root `plan_audit_closure_29_07.md`
(counts: `closed` 16 · `owner-de-scoped` 3 · `externally-blocked` 5 ·
`still-open` 0). No locally actionable `still-open` residual remains.
Unperformed optional live/manual validations are **not** claimed completed.

### Optional external validation (not software-closure blockers)

These exact live/manual checks were **not** run (or are owner-de-scoped) and
must not be overclaimed as closed:

| Gate | Disposition | Gate requirement / partial evidence |
|---|---|---|
| DataLens live | `externally-blocked` | каталоги/конфиг Mac-стенда на месте; на read-only probe 2026-07-29 сервисы остановлены/не ready; exact live contract **not run**; старт стенда и stateful integration suite — только с отдельной explicit owner authorization; experimental / non-default; offline contracts GREEN |
| Paid live-LLM canary/sentinel | `externally-blocked` | credential Mistral заявлена владельцем; в tracked sentinel/runtime нет **прямого** Mistral provider и mapping секрета; tracked providers — `anthropic`/`gracekelly`; внешний маршрут GraceKelly→Mistral **не** верифицирован; exact live run **not run**; explicit budget approval required |
| Protected tag retag rejection smoke | `externally-blocked` | Intentional live retag of protected `v*`; partial: ruleset `19601122` active + successful `v0.5.0` create |
| Live Trivy-fail before `:latest` promotion | `externally-blocked` | Intentional failing Trivy release experiment; partial: happy-path Trivy-before-push + offline scan-before-push graph |
| Process restart mid-delivery live smoke | `externally-blocked` | Live BI/runtime process kill mid-delivery; partial: RR-4 in-process unit only |
| Public HF Space | `owner-de-scoped` | Owner removed from closure; no sync/publish/decommission |
| Live same-SHA demo-image rebuild (HF path) | `owner-de-scoped` | HF demo image de-scoped with HF; partial: one Demo image pass + frozen-lock unit |

## Сохранённые локальные артефакты

`Auto_BI.html` и `pres.html` оставлены без изменений и не входят в tracked
product scope.
