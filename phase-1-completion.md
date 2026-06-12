# Phase 1 completion plan (2026-06-12, Fable)

## Goal
Закрыть оставшиеся задачи Phase 1 (MVP + Advisor v1) в ветке `phase-1/mvp-superset-advisor`. S2-задачи разблокированы (модель = Fable). Стенд Mac жив (CH+Superset healthy, 26h).

## Tasks
- [x] 1.2 form_data 6 новых viz — `273663b`; 18 integration-тестов pass против живого Superset 4.1.2, все 6 viz визуально проверены в Explore (скриншоты)
- [x] 1.5 Context selection <40k — `9f2bf5e`; жадный отбор по стем-скорингу, сэмплы режутся раньше таблиц, бюджет от фиксированной части промпта
- [x] 1.9 Store (SQLite) — `ab82b88`; 6 таблиц, обвязка pipeline/GraceKelly/CLI
- [x] 1.4 Agent state machine — `77e3149`; GROUNDING→CLARIFY*(детерминированные вопросы только из report, ≤3, ≤2 раундов)→PROPOSE→APPROVE+правки словами (patch_spec)
- [x] 1.7 Advisor в диалоге — `77e3149`; вердикт решает код (worst finding), LLM формулирует; фолбэк на механические titles; dm_change_request → store
- [x] 1.8 CLI-чат `auto_bi chat` — `77e3149`
- [x] 1.12 Reasoning-политика — `77e3149`; `llm/policy.py`, unit-тесты флагов
- [x] 1.11 Eval-сьют — `9669e56`; advisor 9/9 (6 подсаженных анти-паттернов + 3 clean), 15 golden, `auto_bi eval`
- [x] Exit criteria PLAN.md: advisor 9/9 ✓; golden — clear 9/9 (0 лишних вопросов), ambiguous 3/3, infeasible 3/3 (фейлы прогонов = инфра-флейки GraceKelly, PASS на ретрае) ✓; диалоговый кейс на демо-DM: анти-паттерн → вердикт → правка → чистый spec → дашборд `/superset/dashboard/2/` ✓ (скриншот)
- [ ] 1.10 Реальный DWH — РАЗБЛОКИРОВАНО (2026-06-12): DWH = DV2/X5 из DE_project, решения приняты (срез 10–15M; DM «как есть» → gaps → достройка). Полный runbook: `docs/plans/2026-06-12-1.10-real-dwh-x5-runbook.md`. С ним закрывается «реальный кейс по своему DM» и объявляется S6

## Done When
- pytest зелёный, ruff/black clean, контракт-тесты против живого Superset на 9 viz.
- Exit criteria Phase 1 проверены реальными прогонами; CLAUDE.md/ARCHITECTURE.md обновлены.

## Notes
- 1.10 (реальный DWH) — требует креды/доступ от пользователя → отложено, спросить в конце.
- Порядок: 1.2 первой (стенд может умереть), затем код-задачи, затем промптовые.
- Инварианты CLAUDE.md §1–8 не трогать (S4). Коммиты маленькие, локальные (remote нет).
