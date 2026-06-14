# Auto_BI — План до полноценного продукта

Дата: 2026-06-11 (переработан под скоуп «RU-рынок, v1 = ClickHouse + Superset», см. ARCHITECTURE §1.1).
Оценки — в неделях фуллтайм-эквивалента (FTE); вечерами умножать на ~2.

## Definition of «полноценный продукт»

- [ ] Пользователь без SQL описывает дашборд словами **или раскладкой полей** и получает рабочий дашборд по DM-слою.
- [ ] Агент задаёт уточнения только при реальных расхождениях запроса с данными.
- [ ] Feasibility Advisor: прямые вердикты с измеренным evidence («фильтр мимо ключа сортировки — скан 96%»), классы `ok / spec_adjustment / dm_change_request`; никогда не блокирует.
- [ ] Превью состава дашборда до сборки; правки словами; итерации после сборки.
- [ ] BI-таргеты: Superset (v1) + DataLens (v2) из одного spec.
- [ ] Движки: ClickHouse (v1) + Greengage/Greenplum (v2).
- [ ] Eval-сьют ≥ 40 golden-кейсов, «дашборд без ручных правок» ≥ 80%; advisor без false-positive на чистых кейсах.
- [ ] Семантическая модель версионируется, gaps report + enrichment workflow работают.
- [ ] Безопасность: read-only DWH, SQL-guard, секреты вне репо.

---

## Phase 0 — Скелет + вертикальный срез (1–2 нед)

Цель: доказать сквозной путь «текст → дашборд в Superset» на минимальном наборе.

| # | Задача | Результат |
|---|---|---|
| 0.1 | Repo scaffold: uv, ruff, pytest, pre-commit, .env.example | каркас по структуре из ARCHITECTURE §3 |
| 0.2 | docker-compose: Superset (пин версии) + **ClickHouse** с демо-DM (звезда sales/stores/products на MergeTree с осмысленным sorting_key/partition_key, генератор синтетики ~100M строк в факте) | локальный стенд одной командой |
| 0.3 | `introspect/clickhouse.py`: system.tables/columns + sorting_key/partition_key/rows + комментарии → черновик `model.yaml` (вкл. `physical`) | семантическая модель демо-DM |
| 0.4 | `llm/gracekelly.py`: клиент `/orchestrate` + structured-output loop (JSON → pydantic → repair, max 3) | надёжный `complete(prompt, schema)` |
| 0.5 | Минимальный IR (line, bar, big_number) + промпт «запрос+модель → spec» | spec по описанию |
| 0.6 | SQL_GEN (диалект ClickHouse) + валидация (sqlglot SELECT-only, EXPLAIN, LIMIT-прогон) | проверенный SQL на чарт |
| 0.7 | Superset-адаптер: auth, ensure_database/dataset, create_chart (3 viz), assemble_dashboard (простая сетка) | дашборд по API |
| 0.8 | CLI: `auto_bi build "<описание>"` — однострочный happy path, без диалога | демо |

**Exit criteria:** одна команда строит в локальном Superset дашборд из 2–3 чартов по текстовому описанию на демо-DM (ClickHouse). GraceKelly-вызовы логируются.

**Риск фазы:** form_data Superset. Снять первым — задача 0.7 начинается с реверса (создать чарт руками → GET → зафиксировать шаблон).

---

## Phase 1 — MVP на Superset + Advisor v1 (4–6 нед)

Цель: пользоваться самой на реальном DM каждый день; advisor ловит реальные анти-паттерны.

| # | Задача |
|---|---|
| 1.1 | IR полный: 9 viz-типов, dashboard-фильтры, layout_hint; JSON Schema в промпт |
| 1.2 | Библиотека form_data-шаблонов на все 9 viz + contract-тесты «create → GET → assert» |
| 1.3 | Layout generator (12-колоночная сетка, ряды по layout_hint) |
| 1.4 | Agent state machine целиком: GROUNDING → CLARIFY (≤3 вопросов, только из grounding report) → PROPOSE_SPEC → правки словами → APPROVE |
| 1.5 | Context selection под 40k-лимит: отбор релевантных таблиц модели под запрос |
| 1.6 | **Advisor v1 — ClickHouse rule pack** (~8–12 правил-механизмов: фильтр мимо префикса sorting_key, фильтр мимо партиций, large-large join, high-cardinality GROUP BY, point lookup, FINAL…) + универсальный EXPLAIN-слой (`EXPLAIN indexes=1`, `ESTIMATE`) как evidence. _Факт Phase 1: 6 правил; `join_large_large` и `point_lookup_pattern` отложены до поддержки джойнов (docstring advisor/clickhouse.py)_ |
| 1.7 | Advisor в диалоге: findings с severity → LLM-нарратив вердиктов (прямой тон, классы `ok/spec_adjustment/dm_change_request`) в PROPOSE_SPEC |
| 1.8 | CLI-чат `auto_bi chat` (rich): диалог, превью spec текстом, вердикты advisor'а, подтверждение, лог сборки |
| 1.9 | Store (SQLite): sessions, messages, specs, builds, llm_calls, dm_change_requests |
| 1.10 | Подключение реального DWH (read-only) + интроспекция реального DM (вкл. physical) + первый gaps report |
| 1.11 | Eval-сьют: 15 golden-кейсов на демо-DM (однозначные, неоднозначные, невыполнимые) + 5 анти-паттерн-кейсов для advisor'а; прогон одной командой |
| 1.12 | Reasoning-политика: thinking на GROUNDING/PROPOSE_SPEC, без thinking на механических шагах |

**Exit criteria:**
- 15 golden-кейсов: ≥80% дашбордов без ручных правок; 0 лишних уточнений на однозначных запросах; невыполнимые корректно отклоняются с объяснением.
- Advisor: ловит все 5 подсаженных анти-паттернов (с правильным правилом и evidence), 0 false-positive на чистых кейсах.
- Реальный кейс: рабочий дашборд по своему DM, собранный диалогом, с хотя бы одним полезным вердиктом advisor'а.

---

## Phase 2 — Продукт: web UI с двумя режимами + итерации (3–4 нед)

Цель: продуктовый контур — может пользоваться не-инженер. **После этой фазы продукт реально полезен** — контрольная точка по стратегии.

| # | Задача |
|---|---|
| 2.1 | FastAPI backend поверх agent core (sessions, SSE для стрима шагов сборки) |
| 2.2 | Web UI: чат (text-first), превью spec карточками до сборки с вердиктами advisor'а, селектор BI, лог, ссылка на дашборд (белый спокойный layout) |
| 2.3 | **Fields-first режим**: панель полей витрин, drag&drop в черновые группы → структурированный seed для GROUNDING; ответ — варианты дашборда + анализ раскладки |
| 2.4 | Режим итераций: «добавь фильтр», «замени на heatmap» → patch spec → пересборка изменённого |
| 2.5 | `dm_change_request`: генерация структурированной заявки владельцу DM, хранение, список в UI |
| 2.6 | dbt-импорт (manifest/catalog → descriptions, relationships) |
| 2.7 | Enrichment UI: gaps report → правка описаний/ролей → commit model.yaml |
| 2.8 | Eval до 25 кейсов, включая fields-first и итерации |

**Exit criteria:** не-инженер собирает и дорабатывает дашборд через web UI обоими режимами; fields-first раскладка даёт spec того же качества, что текст; dm_change_request генерируется по critical-вердикту.

### Межфазное (после Phase 2, до Phase 3)

- [x] **Joins в IR** (2026-06-13, снимает ограничение Phase 0): qualified-измерения из смежных таблиц + явный `query.joins`, валидация по рёбрам semantic model, LEFT JOIN в SQL_GEN. Меры — только базовая таблица; multi-hop нет. Деталь: ARCHITECTURE §3.4.
- [x] **Native dashboard filters (Superset)** (2026-06-13, снимает предупреждение «фильтры не переносятся» и advisor-F3): **scope-to-applicable** — фильтр выводится только на чарты, чей grain содержит колонку (KPI без колонки остаётся в `scope.excluded`); `filterType` по роли колонки (time→`filter_time`, иначе `filter_select`); top-N `LIMIT` для чартов в scope уезжает из SQL в form_data `row_limit` (иначе ре-ранжирование/опции считались бы по пре-обрезанному топ-N). Формат реверснут с живого стенда; contract-тест round-trip + браузерная проверка (city-фильтр реально фильтрует, KPI цел, опции = все значения). Деталь: ARCHITECTURE §3.5.

---

## Phase 3 — Второй движок + второй BI (3–5 нед)

| # | Задача |
|---|---|
| 3.1 | **Спайк DataLens Public API (2–3 дня, go/no-go)**: `api.datalens.tech` (Preview), IAM-auth, createDataset → Wizard-чарты → createDashboard руками-программно — _2026-06-13: Yandex Cloud недоступен (нет аккаунта/кредов) → поднят **self-hosted open-source DataLens** на Mac (`HC=1`, UI :8080 admin/admin, туннель :8090, логин проверен); тот же формат чартов. Реверс API (createConnection/Dataset/EditorChart-HC/Dashboard) — следующий шаг. Runbook `2026-06-13-datalens-selfhosted-runbook.md`_ |
| 3.2 | DataLens-адаптер: компиляция IR (capability matrix + деградации), workbook «Auto_BI» |
| 3.3 | **Greengage/Greenplum**: интроспектор (PG-каталоги + distribution key, партиции; стенд в docker), диалект SQL_GEN — _✅ demo-level 2026-06-13: live GP 6.25, `introspect/greenplum.py` + dialect seam (`engine.py`), `model_gp.yaml`; **скейл ≥10M live-валидирован** (`stand_scale_gp_dm.sql`, интроспектор читает reltuples=10.3M); **multi-level/list-партиции** — интроспектор читает все уровни (`partition_key='date, region'`, live+unit); остаток — сам Greengage (runbook)_ |
| 3.4 | Advisor: Greengage rule pack (distribution skew, broadcast motion, partition pruning) + EXPLAIN-слой — _✅ demo-level 2026-06-13: `advisor/greenplum.py` + per-engine dispatch, live motion/partition findings; **`distribution_skew` + `no_filter_on_large_fact` live на 10.3M** (`gp_scale_validate.py`: no-filter → оба, date-filter → только skew)_ |
| 3.5 | Eval-кейсы на GP-демо и DataLens-сборку — _GP-часть ✅ 2026-06-13: GP advisor-сьют (`GP_ADVISOR_CASES`, детерм., 6/6 offline) + engine-dispatch; **GP golden — 14 кейсов (`GP_GOLDEN_CASES`), live 14/14 через GraceKelly/Sonnet** (8 clear + 1 iteration + 2 ambiguous + 3 infeasible; 2 GK-флейка прошли на ретрае; авторил субагент — Fable отключён); **DataLens-сборка ✅ 2026-06-14: live contract-сьют `tests/test_datalens_contract.py` (integration-gated) против self-hosted стенда — 10 viz (все VIZ_ID-ветки + heatmap→pivotTable + join) компилируются IR→dataset→chart и `/api/run` отдаёт реальные данные CH; full `build()` с селектором идемпотентен (build×2). LLM-golden для DataLens не нужны: IR BI-агностичен (инвариант 1), их покрывает общий golden-сьют. Попутно адаптер захардена: `safe_entry_name` (charts-engine/US валидируют charset имени entry — `[]`/`/`/`?` роняли create)_ |

**Exit criteria — ВЫПОЛНЕНЫ (2026-06-14):** ✅ один и тот же spec собирается в Superset и DataLens (параллельные live contract-сьюты обеих сторон); ✅ advisor выдаёт осмысленные вердикты на GP-витрине (live на 10.3M). No-go спайка DataLens → Visiology/Luxms по спросу или остаёмся Superset-only до спроса (НЕ актуально — спайк GO, адаптер построен). **🏁 PHASE 3 ЗАВЕРШЕНА — S6: перед Phase 4 предложить ревью (`/cxkm`).**

---

## Phase 4 — Hardening до продукта (2–4 нед + ongoing)

- [x] **Auth/мульти-юзер + RBAC по DWH-схемам** (2026-06-14): opt-in (`AUTO_BI_AUTH_ENABLED`, дефолт OFF → single-user без изменений). stdlib pbkdf2 хэш паролей + bearer/cookie-токены; RBAC ограничивает пользователя его схемами DWH (модель скоупится на grounding, build гейтится, enrichment-PATCH гейтится, сессии owner-bound). Web UI логин (cookie-сессия для SSE). S6-ревью пройдено (`fable_audit_phase4_auth.md`: 0 P1, 2 P2 закрыты). Деталь: `docs/USER_GUIDE.md` §7.
- [x] **Observability** (2026-06-14): трейс шагов агента на сессию (`trace_events`: grounding/clarify/propose/patch/advisor/approve + build-фазы, тайминг+исход) + дашборд расходов LLM (вызовы/латентность/объёмы; разбивка по шагу). GraceKelly не отдаёт токены/стоимость → объёмы в символах = size-прокси (не токены/$). API `GET /sessions/{id}/trace` + `/observability/llm`; UI-панель «Наблюдаемость». Деталь: ARCHITECTURE §3.9.
- [x] **Eval до 40+ кейсов** (2026-06-14): счётчик достигнут — **40 golden** (26 CH + 14 GP) + **15 advisor** (9 CH + 6 GP) = 55 кейсов (`auto_bi/eval/cases.py`). Дальше — не добивать числом, а наполнять при реальных пробелах; прогон перед каждым изменением промптов остаётся постоянной практикой.
- [x] **Пользовательская документация + onboarding нового DWH за ≤1 час** (2026-06-14): `docs/USER_GUIDE.md` (установка, все команды CLI, web UI, два режима, advisor, наблюдаемость, конфиг) + `docs/ONBOARDING_DWH.md` (пошагово подключить новый DWH с бюджетом ≤1ч; CH через CLI, GP через `GreenplumIntrospector` API) + обновлён README (статус Phase 0→4, quickstart). Команды/сниппеты smoke-проверены.
- Продуктовые опции по спросу:
  - **Visiology-адаптер** — спайк сделан (2026-06-14, `docs/plans/2026-06-14-visiology-spike.md`): **NO-GO автономно** (нет publicREST для авторинга дашбордов — только UI-Designer; нет free/email-only стенда). Gate: лицензионный стенд v3 от заказчика.
  - **Luxms-адаптер** — спайк+дизайн сделаны (2026-06-14, `docs/plans/2026-06-14-luxms-adapter-plan.md`): **GO-with-stand** (полный REST/CRUD source→cube(SQL)→dashlet→dashboard, JWT/cookie-auth, нативный CH; живой публичный API проверен). Gate: демо-креды `sandbox.demo.luxmsbi.com` или self-hosted Docker-стенд → затем реализация (зеркало DataLens-трека).
  - Реестровая упаковка, новые движки — по конкретному запросу.
  - **Адекватность собираемого дашборда** (2026-06-14, план `docs/plans/2026-06-14-dashboard-adequacy-fixes.md`):
    авто-масштаб виджетов DataLens — **сделано** (merge `22c32a0`); backlog по «go» — B1 default
    top-N для категориальных чартов (BI-agnostic), B2 категориальная ось для числового измерения
    в DataLens (Superset уже решает через `xAxisForceCategorical`), B3 джойн id→имя (S2+eval),
    B4 косметика осей. B2/B4 требуют живого DataLens-стенда; B1/B3-промпт — S2 + eval.

---

## Сводка

| Фаза | FTE | Накопительно |
|---|---|---|
| 0 — вертикальный срез (CH+Superset) | 1–2 нед | 2 нед |
| 1 — MVP + Advisor v1 (ClickHouse) | 4–6 нед | 8 нед |
| 2 — web UI (2 режима) + итерации | 3–4 нед | 12 нед |
| 3 — Greengage + DataLens | 3–5 нед | 17 нед |
| 4 — hardening | 2–4 нед | ~21 нед |

**Рабочий полезный продукт (свой стек, web UI) — после Phase 2: ~2.5–3 мес FTE (вечерами 5–6 мес). Полный скоуп с Greengage и DataLens — ~4–5 мес FTE.** После каждой фазы — контрольная точка: пересмотр приоритетов (Phase 3 двигается, если CH+Superset закрывают реальную потребность; следить за эволюцией Нейроаналитика DataLens).

## Порядок работы

- Реализация в /auto на Opus high; модельные стопперы S1–S7 — см. `CLAUDE.md`, раздел «Стопперы переключения модели».
- Ветки `phase-N/...`, маленькие коммиты, тесты зелёные перед merge.
- Изменения промптов — только с прогоном eval-сьюта.
- Каждая фаза заканчивается обновлением `ARCHITECTURE.md` (если дизайн уточнился) и `CLAUDE.md` (текущий статус).
