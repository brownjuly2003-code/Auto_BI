# Project closure

Дата фиксации scope: 2026-07-27. Владелец повторно открыл остаточные пункты
аудита для финального closure-прохода 2026-07-29.

## Закрываемый scope

Финальный scope проекта — текущий v1 product path:

- text / fields / auto → IR → SQL guard → ClickHouse + Superset;
- offline-контракты Greenplum и DataLens;
- default-deny prompt data policy, SafeError и capability-gated demo;
- atomic Store commit, durable pre-return build-attempt recovery, stable build-token retry
  и adapter lifecycle;
- golden/advisor replay, offline browser E2E, backup/restore и docs-as-code;
- текущий cumulative bounded mutation gate и package-wide `mypy --strict auto_bi`.

После финальной публикации этот scope считается feature-frozen. Новые функции и
исследовательские расширения не являются незакрытым долгом проекта.

## Финальное решение по прежним residual

| Residual | Решение при закрытии |
|---|---|
| Durable outbox до возврата BI adapter | `closed` 2026-07-29: schema v9 + cleanup-only adapter reconciliation (ADR 0002) |
| Полный current/history split ARCHITECTURE | `closed`: current design отделён от `ARCHITECTURE_HISTORY.md`, ADR остаются отдельными решениями |
| `Field(description=)` на каждом Settings key | `closed` 2026-07-29: 66/66 descriptions + generated ENV_REFERENCE ratchet |
| Cumulative bounded mutation gate | `closed` 2026-07-29: настроенные production targets закреплены в CI, weak outcomes запрещены; snapshot evidence — в [operations/SLO.md](operations/SLO.md) |
| Package-wide `mypy --strict auto_bi` | `closed` 2026-07-29: package-wide gate действует в обоих поддерживаемых CI jobs |
| Live p50/p95, process-memory и cold-start campaign | `closed` 2026-07-29: descriptive CI samples from run [30481369602](https://github.com/brownjuly2003-code/Auto_BI/actions/runs/30481369602) — p50 2353.619 ms, p95 2359.740 ms, container cold start 6341 ms, PID1 RSS 89.594 MiB, cgroup memory 74.93 MiB (**descriptive CI samples, not production SLO guarantees**) |
| Paid live-LLM canary | `budget-gated`; **not run** — нет отдельного approved budget; запускать только после явного лимита расходов |
| DataLens offline contracts | `closed` (offline passed); live stand unavailable (Mac-only stand absent); experimental / non-default release gate |
| Public HF demo vs closing SHA | `active closure work` / mandatory external gate: live health reports version **0.4.0**, `demo_auto_only=false`, no capabilities object; live assertion fails; v0.5.0 publish dry-run succeeds; actual sync blocked (no local `HF_TOKEN`, no repository HF secret/workflow). **Sync to closing SHA or decommission** — do not decommission in this pass |

Новые функции вне этой таблицы по-прежнему требуют отдельного проекта.

## Внешние closure gates

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

### Remaining mandatory gate

Проект остаётся **closure candidate**, не fully closed, пока:

- public HF demo **не** синхронизирован с closing SHA (и live assertion fails на
  stale 0.4.0 profile). Mandatory: **sync to closing SHA or decommission**.
  Sync currently blocked by missing HF token/secret/workflow; decommission not
  performed.

## Сохранённые локальные артефакты

`Auto_BI.html` и `pres.html` оставлены без изменений и не входят в tracked
product scope.
