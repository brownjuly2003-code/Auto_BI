# S6-ревью — Phase 4 / Observability-трек (`phase-4/observability`)

Дата: 2026-06-14. Ревьюер: субагент `code-reviewer` (Opus). Диф: `git diff main..phase-4/observability` (5 коммитов, 21 файл, ~+867/−71).
Контекст: CLAUDE.md (инварианты §1–8, статус observability-трека), `docs/plans/2026-06-14-phase4-observability.md`, `docs/ARCHITECTURE.md §3.6/§3.9`, `fable_audit_phase3.md`/`fable_audit_phase4.md` (формат + severity P1/P2/P3). Формат — зеркало предыдущих аудитов.

## Вердикт

**Мержить — ДА.** P1-блокеров нет. P2 нет. Инварианты 1–8 соблюдены. Трек реализован аккуратно и точно по плану: durable-трейс шагов на сессию + честный LLM-usage-дашборд на измеримых сигналах (вызовы/латентность/символы), без выдуманных токенов/$. Трейсинг best-effort и реально не роняет пайплайн; миграция схемы v1→2 идемпотентна; API/UI не текут чувствительными данными (store хранит только `prompt_sha256`+`prompt_chars`, не текст промпта); XSS нет (всё динамическое через `textContent`). Найдено 5×P3 (косметика/устойчивость к будущему) — все можно вести бэклогом, ни один не блокирует merge.

## Что проверено по приоритетам ревью

1. **Миграция v1→2 (`store/db.py:105-131`)** — корректна и идемпотентна. Новые БД: `executescript(_SCHEMA)` строит полную v2-схему (llm_calls со `step`/`completion_chars`, `trace_events`, индекс) → `_migrate` стампит version 0→2. Существующие v1: `CREATE TABLE IF NOT EXISTS` не трогает старый `llm_calls`, создаёт `trace_events`/индекс; `_migrate` видит version 1 → `_add_column` (guarded по `PRAGMA table_info`) добавляет обе колонки → стамп 2. `_add_column` идемпотентен (повторное открытие v2 — no-op). Тест `test_migrates_v1_db_to_v2` покрывает: версия, наличие колонок, выживание старой строки с back-fill дефолтов, рабочий `trace_events`. Конкурентность сохранена: один shared-коннекшн + `check_same_thread=False` + `threading.Lock()`; `add_trace_event` считает `seq` и инсертит под одним `with self._lock, self._db` (атомарно, гонок seq нет). Дедлоков нет: `llm_usage_summary` НЕ держит лок при вызове `_db_one`/`_breakdown`→`_rows` (каждый берёт/отпускает лок отдельно; `Lock` нереентрантный, но вложенного захвата нет). Потеря данных невозможна — only-additive ALTER + новая таблица.

2. **Best-effort трейсинг** — заявление держится. `AgentSession._trace` (`machine.py:301-313`) оборачивает `add_trace_event` в `try/except Exception` + `logger.exception`, не пробрасывает. `_trace_build` в `api/app.py:291-305` — то же. GraceKelly store-логирование (`gracekelly.py:201`) уже было best-effort, completion_chars/step добавлены в ту же защищённую ветку. **Важная корректная деталь:** `_timed` (CM) НЕ глотает исключение самого шага — при ошибке записывает `error`-событие и **re-raises** (пайплайн падает как раньше, трейс лишь фиксирует исход). Это правильно: «трейс не роняет пайплайн» ≠ «трейс глотает ошибки агента». Подтверждено `test_trace_records_clarify_and_grounding_error` (BoomLLM → error-событие + проброс RuntimeError).

3. **Тайминг и исход (`machine.py`)** — корректны. `_ms` = `monotonic`-дельта в мс (правильный источник для латентности). Шаги: grounding/propose/patch/advisor — через `_timed` (status ok + detail после работы, либо error+`str(exc)[:200]`); clarify — отдельное событие ПОСЛЕ закрытия `_timed("grounding")` (нет двойного учёта латентности); approve — `_trace("approve")` после перевода в APPROVED; build_start/done/error — в `_build` с латентностью от общего `started`. `seq` монотонен и contiguous (тест `test_trace_records_agent_steps`: `[1..n]`, kinds `[grounding, propose, advisor, approve, patch, advisor]`). Пропусков/двойного учёта нет. Тонкость, проверенная и верная: advisor-событие пишется всегда, когда `_advisor is not None` (т.к. `_timed("advisor")` оборачивает `review()`, который всегда выполняется), даже если findings пусты и `narrate_findings` короткозамкнётся без LLM-вызова — это семантически корректно (шаг advisor отработал).

4. **Честность метрик** — соблюдена. `completion_chars = len(output)` (`gracekelly.py:147`), `output = data.get("output_text") or ""` (None-safe). Нигде токены/$ не выдаются. Плановая формулировка «size-прокси» явно проброшена в UI (`index.html:97-99` obs-hint: «объёмы показаны в символах (size-прокси), а не в токенах или деньгах») и в docstring обоих эндпоинтов + `Store.llm_usage_summary`/`db.py` модульный docstring + ARCHITECTURE §3.6/§3.9. `reasoning_calls`/латентность — реально измеримы.

5. **API** — корректно. `/observability/llm` и `/trace` идут через `_store()` → 503 без Store (тест `test_observability_requires_store`). Агрегаты: totals (`ok` = `status='completed'`, `failed` = `calls − ok` — консистентно с тем, что store пишет `completed`/`transport_error`/`unknown`), breakdown по model/step/status, латентность total/avg(ROUND→INT)/max, объёмы prompt/completion, success-rate выводим в UI. Тяжёлых запросов на поллинг нет: это GROUP BY/агрегаты по одной таблице `llm_calls` (+ индекс `ix_trace_events_session` под `/trace`); N+1 отсутствует (один totals-запрос + 3 breakdown'а + 2 чтения trace/llm_calls). Утечки нет: `llm_calls` хранит `prompt_sha256` (хеш) и `prompt_chars` (счётчик), НЕ текст промпта; креды в `llm_calls`/`trace_events` не попадают. `/trace` на неизвестную сессию → пустые списки (не 404) — допустимо и задокументировано.

6. **UI (`app.js`/`app.css`)** — XSS нет. Все динамические значения вставляются через `textContent` (`statCell`, `renderUsage`, `renderTrace`), включая `e.detail` (может содержать `str(exc)[:200]` от LLM/исключения) и `r.step`/`e.kind`. Единственный `innerHTML` (`app.js:130`) — присвоение `""` (очистка) в пред-существующем verdict-коде, не из диффа, не данные. Рефреш не течёт обработчиками: `refreshObservability` пере-`replaceChildren()` контейнеры (не аккумулирует узлы) и не вешает новых listener'ов на ход/сборку; единственный `addEventListener` на `obs.toggle` навешен один раз при инициализации. SSE `done`/`error`/send/submitSeed дёргают `refreshObservability()` — это re-fetch+re-render, без накопления.

7. **Регрессии** — нет. Контракт `complete()` расширен `step: str = ""` с дефолтом (Protocol `base.py:22`, GraceKelly, все тест-дубли `ScriptedLLM`/`FakeLLM`/`FlakyLLM`/`DevLLM` обновлены) — обратносовместимо, существующие call-site без `step` валидны. Superset/DataLens-пути не тронуты (диф адаптеров не касается; `build`-трейс висит в `api/app.py` поверх `builder`, не внутри адаптера). Инвариант 8 (промпты) не задет — шаблоны промптов в диффе не менялись, `step` — чисто метаданные логирования.

## Находки

### P1 (блокеры)
Нет.

### P2 (закрыть до merge)
Нет.

### P3 (косметика / устойчивость к будущему — бэклог)

**F1 [P3] `auto_bi/store/db.py:118-121` — ранний `return` в ветке `version == 0` пропустит ALTER для легаси-БД version-0-со-старой-схемой.** Ветка `if version == 0:` стампит на 2 и возвращается, полагаясь на то, что `executescript` уже построил полную схему. Это верно для НОВЫХ БД. Но существует узкое легаси-окно: БД, созданные коммитом `ab82b88` (Phase 1.9, ДО `a3452f6`/F10, который ввёл `PRAGMA user_version = 1`), лежат на version=0 со СТАРОЙ `llm_calls` (без `step`/`completion_chars`). При открытии новым кодом `executescript` не тронет существующий `llm_calls`, `_migrate` увидит version 0 → стамп 2 + early-return БЕЗ ALTER → первый `log_llm_call` упадёт `no such column: step`. Риск низкий (single-user локальный инструмент, репо без remote, окно ab82b88→a3452f6 кратко), но «честнее» обработать. Фикс: убрать early-return — пусть version-0 проходит ту же `version < 2`-ветку (guarded `_add_column` идемпотентен и на свежей БД — no-op, т.к. колонки уже есть): `if version < 2: add columns; stamp = max`. Либо явно задокументировать «version 0 = только свежесозданная этим кодом» как инвариант.

**F2 [P3] `auto_bi/store/db.py:351-354` + `:355-357` (`_rows`/`_db_one`) — две разные конвенции передачи params.** `_rows(self, sql, *params)` (варарги) vs `_db_one(self, sql, params: tuple)` (готовый кортеж). Работает корректно (`_breakdown` зовёт `_rows(sql, *params)`, totals зовёт `_db_one(sql, params)`), но рассинхрон сигнатур — мина для будущего вызова (легко передать кортеж в `_rows` и получить «один параметр-кортеж»). Косметика. Фикс: унифицировать на варарги (`_db_one(self, sql, *params)`) или на кортеж в обоих.

**F3 [P3] `auto_bi/agent/machine.py` (`_trace` для approve, latency_ms=0) — несимметрия с `_timed`-шагами.** approve/clarify пишутся через `_trace(... latency_ms=0)` (это события-вехи, не таймированные блоки) — корректно по смыслу, но в UI трейсе они показываются без «N мс», тогда как соседние grounding/propose/advisor — с латентностью. Не баг (approve/clarify не имеют значимой длительности), но визуально неоднородно. Косметика; можно оставить или пометить вехи иначе в UI.

**F4 [P3] `auto_bi/store/db.py:280` (`status='completed'`) — «ok»-критерий захардкожен литералом.** Определение успеха (`ok = SUM(CASE WHEN status='completed')`) дублирует строковый литерал статуса, который порождается в `gracekelly.py` (`data.get("status")`). Если оркестратор когда-нибудь сменит токен успешного статуса, агрегат `ok`/`failed` молча поедет, а тесты (которые сами пишут `"completed"`) этого не поймают. Латентно. Фикс: вынести `OK_STATUS = "completed"` константой, разделяемой store и gracekelly, либо комментарий-якорь у обоих.

**F5 [P3] контрактный пин трейс-`kind`/llm-`step` словарей.** `STEP_LABELS` в `app.js` и набор `kind` в `machine.py`/`api/app.py` (+ `step` в call-site) — два независимых списка строк, синхронизируемых вручную. Неизвестный `kind`/`step` UI отрендерит «как есть» (фоллбек `STEP_LABELS[x] || x` — деградирует мягко, не ломается), так что это не баг, но при добавлении шага легко забыть лейбл. Косметика; можно оставить (фоллбек безопасен) либо добавить тест, сверяющий, что все эмитимые `kind` имеют лейбл.

## Инварианты 1–8: соблюдены/нарушены

- **#1 IR-first** — ✅ не задет. Трек не трогает генерацию spec; LLM по-прежнему эмитит только `DashboardSpec`. `trace_events`/`llm_calls` — чисто наблюдательные метаданные.
- **#2 Spec валидируется до BI** — ✅ не задет. Путь валидации/build не изменён; build-трейс висит поверх `builder`, не внутри.
- **#3 SQL только SELECT, EXPLAIN+LIMIT, read-only** — ✅ не задет (SQL-генерация/guard не в диффе).
- **#4 Вопросы только из grounding report, ≤3/раунд** — ✅ не задет. `clarify`-трейс пишется ПОСЛЕ `clarify_questions(report)`, логика уточнений не тронута.
- **#5 Advisor — вердикты из детерминированных findings, advisory-only** — ✅ не задет. `_timed("advisor")` оборачивает `review()`+`narrate`, не меняя их семантику; сборку трейс не блокирует.
- **#6 Fields-first — второй вход в тот же пайплайн** — ✅ не задет (seed-путь не менялся; `submitSeed` лишь дёргает `refreshObservability` после).
- **#7 Версия Superset/DataLens запинена** — ✅ не задет. Новых внешних версионных зависимостей трек не вводит (трейс/usage — внутренний store).
- **#8 Промпты мержатся только с прогоном eval** — ✅ соблюдён. Шаблоны промптов в диффе НЕ менялись (`step` — метаданные логирования, не часть промпта); eval-сьют не затронут.

## Резюме

Чистый, узко-скоупленный наблюдательный слой. Реализация совпадает с планом построчно, «честность по данным» выдержана (никаких выдуманных токенов/$), best-effort действительно best-effort (без проглатывания ошибок агента), миграция идемпотентна, API/UI безопасны. P1/P2 нет. Пять P3 — устойчивость к будущему и косметика, все бэклогом. **Observability-трек закрывать и мержить в main можно.**
