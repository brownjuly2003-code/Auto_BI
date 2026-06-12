# CLAUDE.md — Auto_BI

Агент «текст/раскладка полей → дашборд в выбранной BI» поверх DM-слоя DWH. Полный контекст: `README.md`, `docs/ARCHITECTURE.md` (дизайн), `docs/PLAN.md` (фазы), `docs/MARKET.md` (рынок/зачем).

## Статус

**Phase 0 ЗАВЕРШЕНА и верифицирована** (2026-06-11, ветка `phase-0/vertical-slice`). Exit criteria проверены реальными прогонами: стенд на Mac (`~/auto_bi_stand`, compose: Superset 4.1.2 + CH 24.8, демо-DM 20M строк), интроспекция → `semantic/model.yaml` закоммичен, 4 contract-теста form_data прошли против живого Superset, e2e `auto_bi build "Обзор продаж: …"` собрал дашборд из 3 чартов (`/superset/dashboard/1/`), GraceKelly-вызовы в `logs/llm_calls.jsonl`. Доступ к стенду с Windows — SSH-туннель `ssh -N -L 8123:localhost:8123 -L 8088:localhost:8088 deproject-mac`; Docker ТОЛЬКО на Mac.

Известное ограничение Phase 0 (by design): джойнов в IR нет — запросы с полями из смежных таблиц («топ городов» при city в dm.stores) отклоняются валидацией; промпт предупреждает LLM. Снимается в Phase 1/2 по PLAN.

**S6 ЗАКРЫТ** (2026-06-12, решение пользователя): внешнее ревью заменено аудитом Fable (`fable_audit.md`), все 8 findings исправлены и верифицированы. Phase 0 смержена в `main` (`3f99f35`).

**Phase 1 ПОЧТИ ЗАКРЫТА** (2026-06-12, Fable, ветка `phase-1/mvp-superset-advisor`). Сделано ВСЁ кроме 1.10: **1.1** IR 9 viz (`ad1999d`), **1.2** form_data все 9 viz — 18 integration-тестов против живого Superset 4.1.2 + визуальная проверка рендера всех 6 новых в Explore (`273663b`), **1.3** layout (`d8ada46`), **1.4** state machine GROUNDING→CLARIFY*(вопросы детерминированно только из report, ≤3/раунд, ≤2 раундов)→PROPOSE→APPROVE+правки словами (`77e3149`), **1.5** context selection <40k (стем-скоринг+жадная упаковка, `9f2bf5e`), **1.6** Advisor (`3dad971`), **1.7** нарратив advisor (вердикт решает код, LLM формулирует; `77e3149`), **1.8** `auto_bi chat` (rich), **1.9** SQLite store (`ab82b88`), **1.11** eval-сьют `auto_bi eval` (`9669e56`), **1.12** reasoning-политика `llm/policy.py`. pytest **131 passed / 18 deselected (integration)**.

**Exit criteria Phase 1 проверены живыми прогонами** (2026-06-12): advisor-сьют **9/9** (6 подсаженных анти-паттернов с правильными правилами + 3 clean без false positives); golden-сьют через живой GraceKelly/Sonnet: **clear 9/9 (0 лишних вопросов), ambiguous 3/3, infeasible 3/3** (единичные FAIL в прогонах — инфра-флейки браузерного провайдера GraceKelly, PASS на ретрае; промпт-фиксы по итогам прогонов: конвенция имён колонок `cbc4727`/`07a487b`, repair-промпт со схемой `44a7e39`); **e2e диалог живьём**: запрос с анти-паттерном → critical-вердикт advisor (оба правила, EXPLAIN-evidence) → правка «ограничь июнем 2026» → вердикты исчезли → approve → дашборд `/superset/dashboard/2/` собран и проверен скриншотом; store записал messages/specs(proposed→approved)/builds/llm_calls.

**Остаток Phase 1:** только **1.10** (подключение реального DWH + интроспекция + первый gaps report). Разблокировано 2026-06-12: реальный DWH = DV2/X5 Retail Hero из DE_project, перегружаем срез 10–15M в CH стенда (Lima-кластер с оригиналом не совместим со стендом на 8GB iMac). Решения пользователя: срез, DM «как есть» (3 марта + BV) → gaps report → достройка fact/dim. Исполнение — по runbook `docs/plans/2026-06-12-1.10-real-dwh-x5-runbook.md` (подходит для Opus-сессии). S6 (граница фазы) объявлять после 1.10. Push невозможен — у репо нет remote.

> 2026-06-12: `/cxkm` по диффу `main...HEAD` запущен — оба внешних ревьюера недоступны (CX `codex app-server exited unexpectedly`; KM/mco kimi `LLM not set` — провайдер не сконфигурирован). Postflight локально зелёный: `ruff` clean, `pytest` 55 passed / 4 deselected (live-тесты гейтятся стендом). S6-ревью НЕ пройдено → Phase 1 не начинать. Re-run: `codex-prompt-phase0-review.md` (root, untracked) + готовые `.tmp/diff.patch`, `.tmp/km-prompt.md`. Hygiene: `.gitignore` теперь покрывает `.env.*` (коммит `5836695`).

> 2026-06-12: внешнее ревью Phase 0 проведено локальной моделью (Fable) → 8 findings в `fable_audit.md` (3×P2 + 5×P3). **Все закрыты в коде той же сессией** (по явному запросу пользователя): F1 SQL-инъекция через `measure.label` в form_data (экранирование), F2 `order_by` по мере → невалидный CH-SQL (маппинг на алиас + `measure_alias` в `ir/spec.py`), F3 мёртвый `AUTO_BI_SEND_SAMPLES` (проброшен в `render_model`, ARCHITECTURE §4 уточнён), + P3: пустой `IN`, connect-retries клиентов, ранний выход repair-петель, уникальность имени dataset, defensive `validate_spec` в pipeline. `ruff`/`black` clean, `pytest` **65 passed / 4 deselected**. Закоммичено в ветку: `f7a6780`. **Live-смоук P2 против работающего CH 24.8 стенда (Mac) пройден**: F2-fixed `ORDER BY "sum_revenue"` → 5 строк rc=0; F2-buggy контроль `ORDER BY "revenue"` → `Code 215 NOT_AN_AGGREGATE` (баг подтверждён, фикс снимает); F1 escaped-label → SUM факта (236e9), не `system.numbers` (инъекции нет). **S6 по-прежнему открыт** — внешнее CX/KM-ревью не пройдено; Phase 1 не начинать.

## Скоуп (решение 2026-06-11)

RU-рынок. **v1 = ClickHouse (DM) + Superset (BI)**; v2 = Greengage/Greenplum + DataLens (Public API). Power BI / Tableau / Metabase — вне скоупа. Универсальность держим в швах (IR, `BIAdapter`, rule pack per engine), имплементируем один путь. Демо-DM — на ClickHouse (docker).

## Инварианты дизайна (не нарушать без обновления ARCHITECTURE.md)

1. **IR-first**: LLM генерирует только `DashboardSpec` (pydantic IR). Нативные форматы BI (form_data, конфиги DataLens) — только детерминированный код адаптеров.
2. Spec валидируется против `semantic/model.yaml` ДО любых вызовов BI; неизвестное поле → reject + repair loop (max 3), никаких молчаливых починок.
3. SQL: только SELECT (sqlglot-guard), EXPLAIN + LIMIT-прогон, read-only роль DWH.
4. Уточняющие вопросы — только из grounding report, ≤3 за раунд; однозначный запрос → ноль вопросов.
5. **Feasibility Advisor**: вердикты только из детерминированных findings (ключи/статистика/EXPLAIN) — LLM формулирует, но не «решает»; advisory-only, сборку никогда не блокирует; классы `ok / spec_adjustment / dm_change_request`.
6. **Fields-first** (drag&drop полей) — второй вход в тот же пайплайн (seed для GROUNDING), не отдельный конструктор чартов.
7. Версия Superset запинена в docker-compose; обновление — отдельная задача с прогоном contract-тестов.
8. Изменения промптов мержатся только с прогоном eval-сьюта (с Phase 1).

## LLM

GraceKelly (локальный сервис, должен быть запущен): `POST http://127.0.0.1:8011/api/v1/orchestrate`
`{"prompt": ..., "model": "claude-sonnet-4-6", "reasoning": true, "decompose": false, "session_id": ..., "metadata": {"app": "auto_bi"}}`
- `prompt` ≤ 40 000 символов → context selection обязателен на больших DM.
- Text-in/text-out: структурированный вывод через JSON-блок → pydantic → repair loop.
- Все вызовы через `llm/LLMClient`-абстракцию, напрямую httpx из бизнес-кода не дёргать.
- `reasoning: true` — на GROUNDING/PROPOSE_SPEC; механические шаги — без reasoning.

## Stack и конвенции

- Python 3.12 + uv; ruff + black; pytest. FastAPI, pydantic v2, sqlglot, httpx.
- Без LangChain/LangGraph — агент = простая state machine.
- Код/идентификаторы — английский; доки и общение — русский.
- Секреты только в `.env` (в `.gitignore`); в логи/доки не попадают.
- Локальный стенд: `docker-compose up` (Superset + ClickHouse с демо-DM).

## Стопперы переключения модели (/auto)

Дефолт реализации — **Opus high**. Агент не может сменить модель сам, поэтому стоппер = остановка с протоколом (ниже). Триггеры объективные, «по ощущениям» не останавливаться.

| Код | Триггер (проверяемый) | Действие |
|---|---|---|
| S1 дебаг-цикл | Один и тот же баг/тест: 3 цикла «гипотеза → правка → прогон» без смены симптома (новая ошибка = прогресс, счётчик сбрасывается) | STOP → рекомендация **Fable**; можно взять независимую задачу фазы |
| S2 промпты/eval | Задачи промпт-инжиниринга агента и дизайна eval (в Phase 1 это 1.4–1.7: GROUNDING/CLARIFY/PROPOSE_SPEC, политика уточнений, golden-кейсы) | **Не начинать на Opus в /auto.** STOP до старта → Fable или ручная сессия |
| S3 eval-регрессия | После правки промптов eval ниже базовой на ≥10 п.п. или ниже exit-порога фазы, и 1 итерация починки не вернула | STOP → Fable; промпт-изменение не мержить |
| S4 инварианты | Решение требует менять IR-схему, интерфейс `BIAdapter` или инварианты 1–8 этого файла | STOP всегда — вопрос пользователю, модель не важна |
| S5 form_data | viz_type не проходит contract-тест после 3 вариантов шаблона | STOP → приложить diff «create vs GET»; ручная сессия (вероятно с браузером), модель вторична |
| S6 граница фазы | Exit criteria фазы выполнены и проверены | STOP → предложить `/cxkm`-ревью перед следующей фазой; следующую фазу самому не начинать |
| S7 инфра | GraceKelly / Superset / ClickHouse-стенд недоступен после 2 попыток восстановления | STOP **без** эскалации модели — это не модельная проблема |

**Протокол STOP:** (1) WIP-коммит в текущую ветку `wip(stopper:Sx): <задача>`; (2) одна строка в блок «Статус» этого файла: дата, задача, стоппер, рекомендованная модель; (3) финальное сообщение: причина, что переключить, точка продолжения. После S1/S3/S5 допустимо взять независимую задачу той же фазы; после S2/S4/S6 — только остановка.

## Definition of done (фаза/задача)

- Тесты зелёные (pytest), ruff чистый.
- Exit criteria фазы из PLAN.md проверены реальными прогонами, не «на глаз».
- Обновлены: статус в этом файле; ARCHITECTURE.md — если дизайн уточнился.
