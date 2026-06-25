# Авто-режим: обзорный дашборд из витрины

## Goal
Третий вход в пайплайн: `витрина (model.yaml) → детерминированный autospec → DashboardSpec` —
из ролей/кардинальности витрины собирается **курируемый** обзорный дашборд (не «все возможные
графики»). Без LLM, без новых инвариантов. Переиспользует validate / normalize / advisor / адаптеры.

## Рецептура (детерминированная, из семантической модели)
Скелет, обрезаемый до `max_charts` по приоритету:
1. **KPI** — `big_number` по каждой мере (`role=measure`, agg из модели). [P1]
2. **Динамика** — главная мера `line` по time-колонке (если есть `role=time`). [P2]
3. **Топ-N разрезы** — главная мера `bar` по «хорошим разрезам»: dimension-колонки с
   кардинальностью в [2..CARD_MAX], включая атрибуты смежных dim-таблиц через JOIN
   (city/region/format/category/brand). FK-id базовой таблицы → имя через существующий B3. [P3]
4. **Структура** — `pie` по самому низкокардинальному разрезу (format/region). [P4]
5. **Детализация** — `table` главная мера по топ-разрезу. [P5 опц.]

Жёсткие стопы (то, что делает дашборд готовым, а не свалкой):
- агрегируем только `role=measure` (или синтетический COUNT, если мер нет); id не агрегируем;
- разрез только если кардинальность ∈ [2..CARD_MAX]; иначе выкинуть (manager_id=16825 → нет);
- JOIN только по рёбрам `model.joins` (инвариант 2);
- общий лимит `max_charts` (деф. 8) + приоритет P1..P5.

## Tasks
- [x] T1: `auto_bi/agent/autospec.py::build_auto_spec(model, table, *, max_charts=8, target_bi)` → DashboardSpec
- [x] T2: классификация колонок + поиск «хороших разрезов» (база + join-атрибуты) по кардинальности (manager_id=16825 отброшен)
- [x] T3: сборка чартов по рецептуре с явными order_by(desc)+limit и корректными JoinSpec; pie≠bar дедуп
- [x] T4: юнит-тесты `tests/test_autospec.py` (11) — все зелёные
- [x] T5: офлайн-прогон на закоммиченной `semantic/model.yaml` → `validate_spec`=0 ошибок (обе витрины)

## Wiring (сделано — Wiring CLI + UI)
- [x] CLI `auto_bi build --auto <table> [--max-charts N]` (`cli.py::_build_auto`); DWH-free dispatch-тесты `tests/test_cli_build_auto.py` (4)
- [x] UI: вкладка «Авто» + панель выбора витрины (`index.html`/`app.js`/`app.css`); эндпоинт `POST /api/v1/sessions/auto` (`api/app.py`) → `AgentSession.adopt_spec` (APPROVE без LLM) → тот же approve/build/iterate путь; API-тесты `tests/test_api.py` (+4); Playwright-проверка (вкладка→витрина→превью 7 чартов→сборка→`/superset/dashboard/3/`, консоль чистая, скрин `D:\.playwright-mcp\autobi_auto_mode_built.png`)

## Follow-up (по «go»)
- [x] Live-сборка на стенде Mac реальным `auto_bi build --auto dm.sales_daily` → 8/8 чартов EXPLAIN+LIMIT на CH (20M) → дашборд `/superset/dashboard/14/` (Superset API: 8 slices; данные реальные — revenue 236 149 963 687 ₽). Скрин дашборда — опц. (логин Superset, не делал ради нераскрытия кредов).
- eval-кейс авто-обзора.
- Решить связку F3: толкать ли дефолтный time-фильтр в чарты факта (per-period KPI) или оставить dashboard-фильтр.
- v2: опциональное LLM-ранжирование заголовков/порядка (S2 — не на Opus в /auto).
- Коммит/ветка + ревью (`/cxkm`) — по слову владельца (сейчас всё в рабочем дереве, не закоммичено).

## Done When
- [ ] `build_auto_spec` на демо-модели даёт валидный spec (0 ошибок), осмысленный набор
      (KPI выручка/заказы/позиции + динамика по дням + bar по городу/категории/формату + pie),
      pytest зелёный, ruff чистый.

## Notes
- НЕ трогает инварианты 1–8; LLM не участвует в v1 (детерминированно → снимает GraceKelly-SPOF,
  на который ругался BCG-аудит, continuity=D).
- Опирается на реальные структуры: `Physical.cardinality`, `Column.fk/role/agg`, `model.joins`,
  `apply_label_joins`/`apply_chart_defaults`, `validate_spec`, `Advisor`.
