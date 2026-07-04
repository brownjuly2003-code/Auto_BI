# Changelog

Формат по [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версии — по [SemVer](https://semver.org/lang/ru/).

## [Unreleased]

## [0.2.0] - 2026-07-04

Первый версионированный релиз. До него проект жил без тегов/CHANGELOG/GHCR-публикации
(`version = "0.1.0"` с основания репозитория) — этот релиз фиксирует накопленный функционал
Phase 0–4 и последующего hardening-трека и вводит сам процесс релизов.

### Added

- **Сборка дашборда из естественного языка** (текст → уточнения по расхождениям с
  DM → `DashboardSpec` (IR) → сборка) поверх ClickHouse/Greenplum, с engine-aware
  **Feasibility Advisor** (детерминированные вердикты `ok`/`spec_adjustment`/`dm_change_request`).
- **Fields-first режим** (drag&drop полей витрины) и **auto-overview** (курируемый
  дашборд по одной витрине без LLM) — второй и третий вход в тот же пайплайн.
- Аналитическое ядро IR: ratio-меры, произвольный `time_grain`, `yoy`/`pop`/лаг-N,
  `running_share` (Pareto/ABC), `histogram`.
- Два движка DWH (ClickHouse, Greenplum/Greengage) и два BI-адаптера (Apache Superset,
  Yandex DataLens self-hosted) за одним BI-агностичным IR.
- Web UI: чат, превью спецификации, вердикты advisor'а, режим итераций (патч-правки
  словами), заявки владельцу DM (`dm_change_request`), панель наблюдаемости (токены/
  латентность по шагам агента), панель «Что видно» (детерминированные инсайты без LLM).
- Прямой Anthropic Messages API как дефолтный LLM-провайдер (`ANTHROPIC_API_KEY`);
  GraceKelly — документированная опция.
- Auth/RBAC по схемам DWH (opt-in), с security-hardening: secure-cookie, rate-limit на
  login с растущим бэкоффом, токены хранятся как sha256-хэш, периодический purge.
- Ops-hardening: `GET /api/v1/ready` (store + DWH + BI healthcheck), структурные логи
  (`--log-format json`), устойчивая запись оборванных билдов после рестарта процесса.
- CI: офлайн-сьют (ruff/black/mypy/pytest/advisor-eval) + отдельный `integration`-job,
  поднимающий живой ClickHouse+Superset стенд в GitHub Actions на каждый push/PR.
- `docs/DEPLOYMENT.md` — гайд по продакшен-развёртыванию (reverse-proxy/TLS, бэкап
  SQLite, ротация логов, чеклист секретов).
- Релизный конвейер (этот релиз): `docker build` на каждый PR (job `docker` в
  `ci.yml`), публикация образа в GHCR по тегу `vX.Y.Z` (`.github/workflows/release.yml`),
  `auto_bi --version`, поле `version` в `/api/v1/health`, coverage-бейдж, генерируемый CI.

[Unreleased]: https://github.com/brownjuly2003-code/Auto_BI/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/brownjuly2003-code/Auto_BI/releases/tag/v0.2.0
