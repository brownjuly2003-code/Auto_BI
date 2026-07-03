# Аудит Phase 1 (S6-ревью), 2026-06-12

Ревьюер: субагент code-reviewer (Fable), по решению пользователя («запроси ревью у субагента»).
Дифф: `git diff main...HEAD`, ветка `phase-1/mvp-superset-advisor`, HEAD=`eab434a`.
Контекст: CLAUDE.md (инварианты §1–8), docs/PLAN.md Phase 1, docs/ARCHITECTURE.md, phase-1-completion.md.
Ограничение: живой стенд (Superset+CH, Mac) недоступен — live/contract/golden-прогоны не перепроверялись, exit criteria приняты по документации; локально ruff + pytest (140 passed, 18 deselected).

## Резюме

Phase 1 реализована добротно и соответствует плану: все 12 задач закрыты, инварианты CLAUDE.md §1–8 соблюдены — **P1-нарушений нет**. Архитектурная дисциплина выдержана: LLM генерирует только pydantic-схемы (GroundingReport / DashboardSpec / текст нарратива), вердикты advisor решает детерминированный код (`worst_verdicts`), advisory-only подтверждён по коду (вердикты нигде не блокируют `compile_and_build`), вопросы CLARIFY генерируются механически из report с капами ≤3/раунд и ≤2 раундов без тупиков (после капа — propose «с тем что есть»). Тесты честные (скриптованный LLM, без фиктивных assert'ов), eval-сьют структурно соответствует exit criteria. Найдено 2 P2 (оба латентные) и 8 P3. **Phase 2 открывать можно**; P2 закрыть до или в начале Phase 2 — особенно F2, т.к. Phase 2 (web UI, итерации) обострит оба.

## Findings

**F1 [P2] `auto_bi/agent/propose.py:176` (patch_spec), также `:153`** — Контекст-селекция для PATCH_SPEC выполняется по тексту правки (`edit_request`), а не по содержимому текущего spec. На DM, не влезающем в 40k, короткая правка («переименуй дашборд») может выкинуть из суб-модели таблицы, на которых построены нетронутые чарты → `validate_spec` против суб-модели завалит ранее валидный spec, а repair-промпт (model_text без этих таблиц + правило «используй ТОЛЬКО таблицы выше») будет подталкивать LLM пересадить чарты на другие таблицы — тихая подмена источника данных. Сейчас латентно (демо-DM и model_x5 влезают целиком), но это прямой блокер заявленной работы «на больших DM» из 1.5. Рекомендация: в `patch_spec` скорить селекцию по `edit_request + таблицы/колонки текущего spec`, а pinned-таблицы spec включать безусловно; валидировать против полной модели.

**F2 [P2] `auto_bi/adapters/superset/adapter.py:117`** — `spec.filters` (dashboard-фильтры, объявлены в IR задачей 1.1 и разрешены промптом/валидацией) на сборке молча скипаются с log.warning «not wired in Phase 0». `spec_summary` (machine.py:45) их тоже не показывает. Пользователь утверждает spec с time_range-фильтром и получает дашборд без него — построенное ≠ утверждённому, по духу то же «молчаливое расхождение», которое инвариант 2 запрещает на входе. Рекомендация: до Phase 2 либо компилировать native filters, либо явно отклонять/предупреждать в PROPOSE-превью — но не на уровне лога сборки.

**F3 [P3] `auto_bi/advisor/clickhouse.py:136` (no_filter_on_large_fact), также `:115`** — Правила смотрят только на `query.filters` чарта и игнорируют dashboard-level `spec.filters`. Spec с dashboard time_range-фильтром получил бы critical «no filters» — false positive. Сейчас согласовано с F2 (фильтры всё равно дропаются), но при починке F2 (native filters) правила надо учить учитывать применимые dashboard-фильтры.

**F4 [P3] `auto_bi/semantic/select.py:71-100` + `auto_bi/llm/gracekelly.py:127`** — Две дыры в бюджетировании: (а) cost считается только по таблицам — текст `Джойны:`/`Метрики:` из `render_model` в бюджет не входит; (б) «top table всегда включается даже сверх бюджета» противоречит жёсткому `raise LLMError` в `_call` при prompt>40k — вместо деградации flow умирает с LLMError. Рекомендация: включить joins/metrics в бюджет, а для over-budget одиночной таблицы — обрезать колонки или дать внятную ошибку до вызова.

**F5 [P3] `auto_bi/agent/machine.py:164-177`** — `_propose_turn` вызывается на каждый propose И каждую словесную правку; dm_change_request вставляется в store заново на каждом ходе, пока finding жив → дубли заявок в пределах сессии (карта спроса на изменения DM искажается). Рекомендация: дедуп по (session_id, table, rule).

**F6 [P3] `auto_bi/cli.py:203-205`** — Любая ошибка `patch_spec`/`compile_and_build` (включая `SpecValidationError` после неудачной правки) убивает всю сессию: предыдущий валидный spec жив, но REPL выходит во внешний цикл и начинает новую сессию. Рекомендация: ловить ошибку внутри APPROVE-цикла и возвращать пользователя к текущему spec. Рядом: `GraceKellyClient` (и его `httpx.Client`) создаётся на каждый запрос REPL и не закрывается.

**F7 [P3] `auto_bi/agent/propose.py:97-108`** — `VALIDATION_FEEDBACK_PROMPT` не содержит JSON Schema (в отличие от schema-repair `REPAIR_PROMPT`). Модель чинит модельные ошибки «по памяти» о схеме; при session_id=None (eval golden) → лишние schema-repair-циклы. Рекомендация: добавить `{schema}`, пересчитать margin.

**F8 [P3] `auto_bi/introspect/gaps.py:192-209`** — `_time_grain`: (а) weekly-проверка через `toStartOfWeek(col)` с дефолтным mode=0 (воскресенье) — DM, агрегированный к понедельникам, классифицируется как «fine» (false negative); UNCERTAIN — зависит от конвенции DM; (б) имена таблиц/колонок из model.yaml интерполируются в SQL в backticks без экранирования; (в) исключения run_query не ловятся → `auto_bi gaps` падает целиком вместо деградации одного finding. Ещё: all-NULL time-колонка даёт дубль `column_all_null` (из `_check_degenerate_columns` и `_check_time_grain`).

**F9 [P3] `auto_bi/ir/validate.py:59-64`** — Для measure запрещена только роль TIME; `SUM` по dimension String-колонке проходит валидацию — падает поздно, на `compile_and_build` → `SQLGuardError` EXPLAIN, т.е. потерянная сессия (в связке с F6). Рекомендация: численные agg (sum/avg/min/max) только role=measure (count/uniq — любым).

**F10 [P3] `auto_bi/store/db.py:77`** — `REFERENCES` объявлены, но `PRAGMA foreign_keys=ON` не включён — FK не enforced; нет версии схемы (`PRAGMA user_version`). Для single-user CLI приемлемо, но дёшево закрыть сейчас.

**Plan-deviation (не баг)** — rule pack = 6 правил против «~8–12» из PLAN 1.6: `join_large_large` обоснованно отсутствует (нет джойнов, задокументировано), но `point_lookup_pattern` из плана не реализован и нигде не помечен как отложенный. Зафиксировать в PLAN/ARCHITECTURE.

## ruff / pytest (факт ревью)

- `ruff check .`: 11 ошибок — все в untracked `.tmp/` скретч-скриптах; tracked-код чистый.
- `pytest -q`: 140 passed, 18 deselected (integration), 0.89s — совпадает с CLAUDE.md.

## Пробелы покрытия тестами

- `patch_spec` под давлением контекст-селекции (F1) — нет теста «большая модель + короткая правка».
- Дроп dashboard-фильтров (F2) — ни одного теста на судьбу `spec.filters` при сборке.
- `_time_grain`: ветка weekly никогда не исполняется в тестах.
- «top table over budget» (F4) — тест проверяет селекцию, но не судьбу вызова GK.
- Ошибочные пути CLI-чата (F6) не покрыты; при переносе цикла в FastAPI (Phase 2) логику надо тестировать.
- Store: нет тестов на повторное открытие существующей базы и FK-целостность.
