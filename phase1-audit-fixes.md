# Phase 1 audit fixes (2026-06-12, Fable)

## Goal
Закрыть findings S6-ревью Phase 1 (субагент code-reviewer): 2×P2 + P3. Стенд недоступен — верификация offline (pytest/ruff). Коммиты локальные в `phase-1/mvp-superset-advisor`.

## Tasks
- [x] F0: записать отчёт ревью в `fable_audit_phase1.md` → Verify: файл в корне, формат как fable_audit.md
- [x] F1 [P2] `agent/propose.py`: patch_spec — селекция по `edit_request` + таблицы/колонки текущего spec, таблицы spec pinned безусловно → Verify: новый тест «huge model + короткая правка → таблицы spec в суб-модели»
- [x] F2 [P2] `adapters/superset/adapter.py` + `agent/machine.py`: spec.filters показывать в spec_summary и явно предупреждать в PROPOSE-превью («dashboard-фильтры не поддерживаются адаптером»), не молча дропать на сборке → Verify: тест на warning в summary; F3 задокументировать как coupled (advisor-правила учитывают фильтры после wire-up)
- [x] F4 [P3] `semantic/select.py`: joins/metrics в бюджет; over-budget одиночная таблица → внятная ошибка/обрезка колонок до вызова GK → Verify: тест huge-model с joins/metrics влезает в 40k
- [x] F5 [P3] `agent/machine.py`: дедуп dm_change_request по (session, table, rule) → Verify: тест — повторный propose не плодит дубли
- [x] F6 [P3] `cli.py`: ошибка patch_spec/build не убивает сессию (возврат к текущему spec); GraceKellyClient не пересоздавать на каждый запрос → Verify: pytest зелёный (CLI-цикл — ручная логика, покрыть unit'ом обработчик если выделяется)
- [x] F7 [P3] `agent/propose.py`: JSON Schema в VALIDATION_FEEDBACK_PROMPT, пересчитать margin → Verify: существующие budget-тесты + новый assert схемы в промпте
- [x] F8 [P3] `introspect/gaps.py`: экранирование backtick-идентификаторов; try/except вокруг run_query → деградация одного finding; дедуп all-NULL/time; weekly mode — задокументировать UNCERTAIN → Verify: тесты на escaping и на упавший run_query
- [x] F9 [P3] `ir/validate.py`: sum/avg/min/max только role=measure (count/uniq — любым) → Verify: тест SUM по String-dimension → SpecValidationError
- [x] F10 [P3] `store/db.py`: `PRAGMA foreign_keys=ON` + `PRAGMA user_version=1` → Verify: тест FK enforced
- [x] Гигиена: `.tmp/` в ruff exclude (pyproject) → Verify: `ruff check .` = 0 ошибок
- [x] Финал: pytest полный + ruff, обновить CLAUDE.md (статус ревью S6) и PLAN-deviation (point_lookup_pattern отложен), коммиты по группам

## Done When
- pytest зелёный (≥140 passed + новые), ruff check . чистый.
- Все findings закрыты или явно помечены deferred с причиной.
- CLAUDE.md статус: S6-ревью пройдено (субагент), findings закрыты.

## Notes
- F3 кодом не править: правила консистентны с текущим (фильтры дропаются); связать с задачей native filters Phase 2.
- Native filters НЕ компилировать — нужен живой Superset contract-тест (S5-риск).
- Инварианты §1–8 не трогать (S4).
