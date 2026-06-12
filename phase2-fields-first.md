# Task 2.3 — Fields-first режим

## Goal
Второй вход в тот же пайплайн (инвариант 6 / D8): drag&drop-раскладка полей витрин → структурированный seed для GROUNDING → тот же CLARIFY/PROPOSE/APPROVE. Без второго конструктора, без изменений IR.

Решение по «вариантам дашборда» из §3.7: один spec (как в text-first) + **детерминированный анализ раскладки** (код сравнивает seed и spec: какие поля групп не вошли, какие группы слиты/разбиты) — LLM не «решает», зеркало D5. Отклонение фиксируется в ARCHITECTURE.

## Tasks
- [x] 1. `auto_bi/agent/seed.py`: `FieldsSeed` (groups: label?, fields=["dm.t.col"...]; comment), `validate_seed(seed, model) -> list[str]`, `render_seed_request(seed) -> str` (текст для grounding/propose/scoring) → Verify: новые unit-тесты `tests/test_seed.py`
- [x] 2. Machine: `AgentSession.start(request="", seed=None)`; `_full_request()` включает рендер seed; seed-таблицы → `pinned` в селекцию grounding/propose → Verify: test_machine (scripted LLM): start с seed проходит до APPROVE, clarify-петля работает
- [x] 3. Промпты: `build_grounding_prompt`/`propose_spec` принимают `pinned`; advisory-инструкция живёт в рендере seed (шаблоны промптов не тронуты) → Verify: test_seed проверяет текст grounding-промпта
- [x] 4. Анализ раскладки (детерминированный): `seed_analysis(seed, spec) -> list[str]` — дропнутые поля, группы→чарты; в message (CLI) и `AgentTurn.notes` (web UI) → Verify: unit-тесты
- [x] 5. API: `GET /api/v1/model/fields`; `POST /sessions` — `request`/`seed`, хотя бы один; невалидный seed → 422 → Verify: test_api
- [x] 6. Web UI: вкладки «Текстом | Полями», панель полей (role-бейджи T/D/M), HTML5 DnD + клик-фоллбек, группы, комментарий, превью с «Анализ раскладки» → Verify: Playwright через dev_ui_server (полный сценарий, консоль чистая)
- [x] 7. `scripts/dev_ui_server.py`: вторая таблица в MODEL — fields-first сценарий показывает анализ → Verify: Playwright
- [x] 8. Eval-гейт: golden-кейсы f1/f2 (fields-first) добавлены (GOLDEN_CASES=17); живой прогон GK f1,f2,g1,a1 — 4/4 PASS, регрессии нет (шаблоны промптов не менялись) → Verify: вывод `auto_bi eval`
- [x] 9. Docs: CLAUDE.md статус, ARCHITECTURE §3.7 (реализация 2.3 + отклонение «варианты → один spec + анализ») → Verify: diff

## Done When
- [x] pytest зелёный (181 passed), ruff/black clean
- [x] Playwright: fields-first сценарий drag&drop → spec → build на dev-стенде, консоль чистая
- [x] Golden-eval без регрессии (живой GK, 4/4 PASS)

## Notes
- IR/BIAdapter не трогаем (S4 не задевается). Seed-валидация — детерминированная, до LLM.
- UI строит панель из модели → неизвестное поле в seed = protocol misuse (422), не clarify.
- Live-смоук против реального стенда — по-прежнему ждёт Mac (как у 2.1/2.2).
