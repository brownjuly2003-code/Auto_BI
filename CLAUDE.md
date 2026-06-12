# CLAUDE.md — Auto_BI

Агент «текст/раскладка полей → дашборд в выбранной BI» поверх DM-слоя DWH. Полный контекст: `README.md`, `docs/ARCHITECTURE.md` (дизайн), `docs/PLAN.md` (фазы), `docs/MARKET.md` (рынок/зачем).

## Статус

**Phase 2 НАЧАТА** (2026-06-12, Fable, по «продолжи» после закрытия S6): Phase 1 влита в `main` (merge `a6f2755`), работа в ветке `phase-2/web-ui`. **2.1 HTTP API реализовано**: `auto_bi/api/` (create_app c DI, SessionManager c per-session lock и буфером событий), endpoints `/api/v1/sessions[…]` + SSE `/events`, `auto_bi serve` (uvicorn-wiring), Store потокобезопасен (lock + check_same_thread=False), контракт «правка не теряет сессию» перенесён в HTTP (200+error). **2.5 dm_change_request реализовано**: `auto_bi/dmcr.py` (детерминированный markdown-рендер заявки владельцу DM из store-строки + контекст сессии через JOIN), API `GET/PATCH /api/v1/dm-change-requests[...]` (список по статусу, заявка с markdown, lifecycle open→submitted/accepted/rejected).

**2.4 итерации реализованы**: APPROVED не терминальна — правка словами после сборки патчит spec и возвращает в APPROVE, следующий approve пересобирает; история spec'ов в store append-only; API re-approve сбрасывает SSE-буфер (стрим = одна сборка); CLI-чат после сборки предлагает доработку. In-place обновление существующего дашборда (вместо нового) отложено до живого стенда (нужен update-путь адаптера + contract-тесты).

**2.2 web UI реализован** (`auto_bi/api/static/`, vanilla HTML/CSS/JS без сборочной цепочки, отдаётся из FastAPI на `/`): чат (clarify-вопросы, ошибки правок), превью spec карточками с вердиктами advisor (accent-полоса по severity) и предупреждением о фильтрах, кнопка «Собрать/Пересобрать», SSE-лог сборки, ссылка на дашборд, список dm_change_requests. Спокойный белый layout, тёплые нейтралы, один акцент (teal), system-шрифты. **Проверен в браузере** (Playwright, dev-стенд `scripts/dev_ui_server.py` со скриптованным LLM): clarify → spec → build → правка → rebuild, консоль чистая. pytest **168 passed**, ruff/black clean. Деталь: ARCHITECTURE §3.7.

Live-смоук `serve`+UI против реального GraceKelly/стенда не делался — сделать при первом доступе к стенду. Остаток фазы: **2.3 fields-first** (drag&drop панель полей + структурированный seed для GROUNDING — частично S2/промпты), **2.6 dbt-импорт**, **2.7 enrichment UI**, **2.8 eval до 25** (нужен живой GraceKelly; кейсы fields-first/итераций — после 2.3).

**Phase 0 ЗАВЕРШЕНА и верифицирована** (2026-06-11, ветка `phase-0/vertical-slice`). Exit criteria проверены реальными прогонами: стенд на Mac (`~/auto_bi_stand`, compose: Superset 4.1.2 + CH 24.8, демо-DM 20M строк), интроспекция → `semantic/model.yaml` закоммичен, 4 contract-теста form_data прошли против живого Superset, e2e `auto_bi build "Обзор продаж: …"` собрал дашборд из 3 чартов (`/superset/dashboard/1/`), GraceKelly-вызовы в `logs/llm_calls.jsonl`. Доступ к стенду с Windows — SSH-туннель `ssh -N -L 8123:localhost:8123 -L 8088:localhost:8088 deproject-mac`; Docker ТОЛЬКО на Mac.

Известное ограничение Phase 0 (by design): джойнов в IR нет — запросы с полями из смежных таблиц («топ городов» при city в dm.stores) отклоняются валидацией; промпт предупреждает LLM. Снимается в Phase 1/2 по PLAN.

**S6 ЗАКРЫТ** (2026-06-12, решение пользователя): внешнее ревью заменено аудитом Fable (`fable_audit.md`), все 8 findings исправлены и верифицированы. Phase 0 смержена в `main` (`3f99f35`).

**Phase 1 ПОЧТИ ЗАКРЫТА** (2026-06-12, Fable, ветка `phase-1/mvp-superset-advisor`). Сделано ВСЁ кроме 1.10: **1.1** IR 9 viz (`ad1999d`), **1.2** form_data все 9 viz — 18 integration-тестов против живого Superset 4.1.2 + визуальная проверка рендера всех 6 новых в Explore (`273663b`), **1.3** layout (`d8ada46`), **1.4** state machine GROUNDING→CLARIFY*(вопросы детерминированно только из report, ≤3/раунд, ≤2 раундов)→PROPOSE→APPROVE+правки словами (`77e3149`), **1.5** context selection <40k (стем-скоринг+жадная упаковка, `9f2bf5e`), **1.6** Advisor (`3dad971`), **1.7** нарратив advisor (вердикт решает код, LLM формулирует; `77e3149`), **1.8** `auto_bi chat` (rich), **1.9** SQLite store (`ab82b88`), **1.11** eval-сьют `auto_bi eval` (`9669e56`), **1.12** reasoning-политика `llm/policy.py`. pytest **131 passed / 18 deselected (integration)**.

**Exit criteria Phase 1 проверены живыми прогонами** (2026-06-12): advisor-сьют **9/9** (6 подсаженных анти-паттернов с правильными правилами + 3 clean без false positives); golden-сьют через живой GraceKelly/Sonnet: **clear 9/9 (0 лишних вопросов), ambiguous 3/3, infeasible 3/3** (единичные FAIL в прогонах — инфра-флейки браузерного провайдера GraceKelly, PASS на ретрае; промпт-фиксы по итогам прогонов: конвенция имён колонок `cbc4727`/`07a487b`, repair-промпт со схемой `44a7e39`); **e2e диалог живьём**: запрос с анти-паттерном → critical-вердикт advisor (оба правила, EXPLAIN-evidence) → правка «ограничь июнем 2026» → вердикты исчезли → approve → дашборд `/superset/dashboard/2/` собран и проверен скриншотом; store записал messages/specs(proposed→approved)/builds/llm_calls.

**Phase 1 ЗАКРЫТА ПОЛНОСТЬЮ, S6 ОБЪЯВЛЕН** (2026-06-12, Fable). **1.10 выполнено**: реальный DWH = DV2/X5 Retail Hero из DE_project, срез 15.05M line items (покупки ≥2019-02-15) / 2.6M заказов / 400k клиентов / 43k товаров загружен в CH стенда (БД `rv` + `marts`; Lima-кластер с оригиналом не совместим со стендом на 8GB iMac). Марты DE_project материализованы «как есть» (branch_pnl / customer_360 / returns_velocity, tax rates 0.20/0.05/0.12 сошлись). Доступ Auto_BI — только `auto_bi_ro` (GRANT SELECT на rv/marts). Интроспекция → `semantic/model_x5.yaml` (3 таблицы, 37 колонок; реальный DWH выловил 2 бага интроспектора на Nullable(Nothing) — исправлены). Новая команда **`auto_bi gaps`** (детерминированный аудит DM: описания, изоляция, all-NULL/constant колонки, прёагрегированная грануляция) → `docs/gaps_report_marts_x5.md`: **18 findings (1 critical / 14 warn / 3 info), 10 кандидатов в dm_change_request**. Живой кейс по реальному DM: 4 чарта, advisor с реальным EXPLAIN-evidence (scan 100%, GROUP BY 401k), дашборд `/superset/dashboard/3/` (скриншот). pytest **140 passed**. Стенд-скрипты: `scripts/stand_load_bv_mat.sh`, `scripts/stand_create_marts.sql`; runbook `docs/plans/2026-06-12-1.10-real-dwh-x5-runbook.md`. Демо-`dm` не тронута. **Phase 2 НЕ начинать** (протокол S6): сначала ревью фазы (например `/cxkm`) по решению пользователя. Естественное продолжение после ревью: достройка DM по gaps report (fact_order_lines + dims из rv) через dm_change_request-workflow. Push невозможен — у репо нет remote.

> 2026-06-12 (позже): **S6-ревью Phase 1 ПРОЙДЕНО** — по решению пользователя («запроси ревью у субагента») проведено субагентом code-reviewer (Fable), отчёт `fable_audit_phase1.md`: P1 нет, 2×P2 + 8×P3, вердикт «Phase 2 открывать можно, P2 закрыть до начала». **Все findings закрыты той же сессией** (план `phase1-audit-fixes.md`): F1 patch_spec — pinned-таблицы spec + скоринг по spec-полям + бюджет учитывает spec JSON; F2 — dashboard-фильтры объявляются в превью approve (spec_summary), не молча дропаются; F4 — joins/metrics в бюджете селекции, over-budget mandatory-таблица обрезает колонки по релевантности вместо LLMError; F5 — дедуп dm_change_request по (table, rule) в сессии; F6 — ошибка правки не убивает chat-сессию, GraceKellyClient один на REPL; F7 — JSON Schema в VALIDATION_FEEDBACK_PROMPT; F8 — gaps: экранирование backtick-идентификаторов, обе недельные конвенции (Sun/Mon), упавшая проба деградирует в INFO-finding, дедуп column_all_null; F9 — sum/avg/min/max только на role=measure; F10 — PRAGMA foreign_keys=ON + user_version=1. F3 (advisor не видит dashboard-фильтры) задокументирован как coupled с native filters → Phase 2 (docstring advisor/clickhouse.py); deviation 1.6 (point_lookup_pattern отложен) зафиксирован в PLAN.md. pytest **155 passed / 18 deselected**, ruff/black clean. Live-прогоны не перепроверялись (стенд недоступен) — exit criteria Phase 1 приняты по документации. **Phase 2 можно открывать по команде пользователя.**

> 2026-06-12: `/cxkm` по диффу `main...HEAD` запущен — оба внешних ревьюера недоступны (CX `codex app-server exited unexpectedly`; KM/mco kimi `LLM not set` — провайдер не сконфигурирован). Postflight локально зелёный: `ruff` clean, `pytest` 55 passed / 4 deselected (live-тесты гейтятся стендом). S6-ревью НЕ пройдено → Phase 1 не начинать. Re-run-материалы (`codex-prompt-phase0-review.md`, `.tmp/`) удалены после того, как ревью прошло субагентом (см. заметку выше). Hygiene: `.gitignore` теперь покрывает `.env.*` (коммит `5836695`).

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
