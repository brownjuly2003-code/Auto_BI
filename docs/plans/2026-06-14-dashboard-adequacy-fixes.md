# План: адекватность собираемого дашборда (DataLens + Superset)

Дата: 2026-06-14. По «насколько адекватен дашборд DataLens → чинить + графики
масштабируются автоматически на уровне кода; ситуацию с Superset тоже проверить».

Контекст: оценка реальных live-рендеров DataLens (`D:\.playwright-mcp\datalens_*_rendered.png`,
`datalens_selector_*.png`) показала: пайплайн корректен (реальные CH-данные, верный
viz/role-маппинг, рабочий селектор), но как **аналитический артефакт** дашборд сыроват.
Ниже — что уже сделано, что выявила сверка с Superset, и backlog с приоритетами.

---

## Уже сделано (эта сессия)

- **S6-ревью B1+B2 (субагент `code-reviewer`, отчёт `fable_audit_dashboard_adequacy.md`): 0 P1 / 0 P2 / 7 P3, вердикт — принять как есть.** Инварианты 1–8 соблюдены, промпты не тронуты (S2 ок), IR/`BIAdapter` не менялись (S4 ок). Cheap-P3 закрыты сразу: лог-строка прозрачности о нормализации в `compile_and_build` (P3-1: preview/advisor видят до-нормализационный spec) + недостающие тесты `normalize.py` (P3-3 детект меры по label, P3-4 multi-measure→measures[0], P3-5 qualified-измерение, P3-6 defensive unknown col/table) + B2 boolean-измерение не кастится (P3-7). **В бэклог (P3-2):** `superset/form_data.py::_ordering_measure` смотрит только `order_by[0]` без label → корнер `[store_id asc, sum_revenue desc]` (B1 законно скипует как уже-top-N) Superset рисует стеной; предсуществующая узость Superset (B1 её не вводил) — согласовать с резолюцией SQL_GEN отдельным заходом. pytest **351 / 32 deselected**, ruff/black clean.
- **B1 — default top-N для категориальных чартов (ЗАКРЫТ, BI-agnostic).** Новый чистый модуль
  `auto_bi/agent/normalize.py::apply_chart_defaults(spec, model)` вызывается в начале
  `compile_and_build` (ДО `validate_spec`/SQL_GEN/адаптера) → нормализованный spec видят и
  SQL-валидация, и обе BI (Superset+DataLens наследуют из одного места). Для viz ∈
  {bar, stacked_bar, pie} с непустыми `dimensions`, чья первичная ось НЕ time-колонка и где
  нет `order_by` по мере: `order_by = [первая мера desc]`, `limit = min(limit, cap)` (pie cap=12,
  иначе 25). Идемпотентно, чисто (model_copy), только дополняет/ужимает. **Уточнение vs план:**
  сигнатура `(spec, model)`, а не bare `(spec)` — модель нужна, чтобы пропускать column-time-series
  (bar/stacked_bar по `date`): форсить там top-N-по-значению молча отрезало бы хвост хронологии.
  Явный top-N автора (order_by по мере — по raw-колонке/алиасу/лейблу, зеркало SQL_GEN) не трогается.
  12 юнит-тестов (`tests/test_normalize.py`, в т.ч. что `generate_chart_sql` отдаёт ORDER BY+LIMIT
  после нормализации). pytest **342 / 32 deselected**, ruff/black clean. 🔴 Визуальная проверка на
  живом стенде DataLens — отложена (не блокер: трансформация на уровне IR, детерминирована).
- **Авто-масштаб виджетов DataLens** (merge `22c32a0`, код `2d4e690`). `build_dashboard_data`
  игнорировал `chart.layout_hint` и клал всем `h=4` → KPI без числа, таблица без строк,
  низкие графики. Теперь тайл размеряется из `layout_hint` (ширина 12-кол → 24-кол сетки ×2,
  как Superset) + viz-aware «пол» высоты (table/pivot=12, charts=9, big_number=6; явный hint.h
  выше дефолта поднимает) + шельф-пакинг (перенос по ширине / `layout_hint.row`). Юнит-тесты,
  330 passed. 🔴 Визуальная проверка сайзинга — на живом стенде (отложена).

---

## Сверка с Superset (по запросу) — что выяснилось

| Аспект | Superset | DataLens | Вывод |
|---|---|---|---|
| Сайзинг виджетов (`layout_hint`) | honor (`build_position_json`/`_pack_rows`) | **теперь** honor (this session) | паритет достигнут |
| `order_by`/`limit` из spec | применяет (`row_limit=q.limit`, `_ordering_measure`) | применяет (`generate_chart_sql`) | паритет; **оба только honor, не enforce** |
| Числовое измерение на оси (store_id) | **форсит категориальную** (`xAxisForceCategorical=True`, form_data.py:116) | **НЕ форсит** → непрерывная ось 0–4K | **DataLens-specific пробел** (B2) |
| Высокая кардинальность | advisor `group_by_high_cardinality` предупреждает | то же (advisor engine-agnostic) | паритет (только warn) |
| Джойн id→имя | нет (IR без джойна → сырые числа) | нет | общий пробел (B3) |

Главный инсайт сверки: **B2 (категориальная ось) уже решён в Superset, в DataLens — нет.**
B1/B3 — общие (propose/IR-слой), чинятся один раз для обоих BI.

---

## Backlog правок

### B1 — Default top-N для категориальных чартов (стена баров) — BI-agnostic, P1 — ✅ ЗАКРЫТ (см. «Уже сделано»)
- **Проблема:** bar/column/stacked/pie над высоко-кардинальным измерением (`store_id` ~4000)
  без `order_by`/тесного `limit` → до `limit`-дефолта (**5000**) неупорядоченных строк = стена
  баров. Видно на `datalens_adapter_bar_rendered.png`.
- **Текущее состояние:** propose-промпт правило 5 уже просит LLM `order_by desc + limit 10–50`
  (мягко, без гарантии); advisor `group_by_high_cardinality` предупреждает; SQL дефолт-limit=5000.
  Реальный LLM-spec обычно ставит limit (напр. «топ» → 15), но enforcement'а нет → тест/ручной
  IR и непослушный LLM дают стену.
- **Фикс (детерминированный, на уровне кода, чинит ОБА BI):** нормализация spec после propose —
  для viz ∈ {bar, column, stacked_bar, pie} c непустыми `dimensions` и без `order_by`,
  ссылающегося на меру: проставить `order_by = [первая мера desc]` и ужать `limit` до разумного
  (напр. 25; pie ≤ 12). Новый чистый модуль, напр. `auto_bi/agent/normalize.py::apply_chart_defaults(spec)`,
  вызвать в `compile_and_build`/pipeline ДО адаптера. Не трогает спеки, где автор уже задал top-N.
- **Объём/риск:** ~небольшой, чистая трансформация IR. Риск низкий (идемпотентно, только дополняет).
- **Верификация:** юнит-тесты (Windows) на нормализацию + что `generate_chart_sql` отдаёт
  ORDER BY+LIMIT; прогон существующих golden/eval, чтобы не сломать кейсы с явным top-N.
- **Опционально усилить:** правило 5 промпта (S2 — не на Opus в /auto, + прогон eval-сьюта).

### B2 — Категориальная ось для числового измерения в DataLens — DataLens-specific, P1 — ✅ ЗАКРЫТ

**РЕЗУЛЬТАТ (live-реверс + фикс + visual).** Механизм реверснут на стенде (`/api/run` highcharts-payload):
числовое DIMENSION-поле на категориальном placeholder column-чарта (X / color) с `data_type/cast="integer"`
рендерится на непрерывной оси (точки `{"x":293},{"x":443}…` = сырые store_id, тонкие бары 0–4K); при
`data_type/cast="string"` на уровне chart-placeholder ответ несёт `categories:["293","443",…]` + `x=[0,1,2]`
(индексы категорий) = дискретная ось. **Фикс — `chart_config.py`:** для viz ∈ {bar, stacked_bar} (→ DataLens
`column`) поля X и color/breakdown, если это числовое DIMENSION (`_is_numeric_dimension`), кастятся в string в
placeholder'е (`_field_item(as_string=True)`); датасет НЕ трогается → SQL/селектор не задеты. line/area
(читают вдоль непрерывной оси) и date/string/measure — без изменений. **Live-verify через shipped
`adapter.create_chart`:** bar/store_id → `categories` ✓; line/store_id → непрерывная (x=сырые) ✓ (scope);
stacked_bar (date X + store_id series) → 4 различных серии «1/2/3/4», X=date целы ✓ (color-дискретизация, не
градиент). **Visual:** скриншот Wizard `D:\.playwright-mcp\datalens_b2_store_id_categorical.png` — 10
равномерных полноширотных баров на категориях 293…3942 (не тонкие на 0–4K). 4 unit-теста, pytest 346, ruff/black clean.

#### (исходная постановка)
- **Проблема:** `store_id` (integer dimension) в DataLens column-чарте падает на непрерывную ось
  (0…4K), а не категории — тонкие бары на числовых позициях. Superset это уже решает
  (`xAxisForceCategorical`), DataLens — нет.
- **Фикс:** в `chart_config.py::build_chart_shared` для bar/column/stacked_bar пометить X/категорию
  как дискретную. Механизм DataLens **нужно реверснуть на стенде** (кандидаты: каст измерения в
  string, отдельный `visualization`-флаг discrete, или поле-роль grouping) — доки≠реальность, как
  с прочим DataLens. До реверса payload не пишем (урок DataLens).
- **Объём/риск:** средний; **требует живого стенда** для реверса + визуальной проверки.
- **Верификация:** live на стенде Mac — bar по store_id рисует дискретные категории, не 0–4K.
- **Гейт:** живой DataLens-стенд (сейчас погашен ради RAM, `docker start` на Mac).

### B3 — Джойн id→имя для интерпретируемости — BI-agnostic, P2
- **Проблема:** `store_id` показывается сырыми числами вместо названий магазинов. IR джойны
  умеет (`JoinSpec`, `interphase/ir-joins`), но propose их для этого не задействует.
- **Фикс (промпт-first):** усилить правило 1/5 промпта — если измерение это id-колонка, у которой
  в модели есть ребро-джойн к таблице с именем/лейблом, группировать по имени (через join), а не
  по id. Мягко; S2 + eval.
- **Опционально (детерминированно):** авто-подстановка join id→name в нормализации, если в
  semantic model есть FK-ребро id→{name|title|label}-колонка. Более инвазивно (меняет grain
  отображения), нужен аккуратный дизайн + eval. Отдельным заходом после B1.
- **Объём/риск:** промпт-вариант мал; детерминированный — средний. Риск семантический → eval обязателен.
- **Гейт модели:** S2 (промпт не на Opus в /auto) + прогон eval-сьюта до/после.

### B4 — Косметика осей DataLens (наложение подписей Y «430M/230M») — P3 — ✅ ЗАКРЫТ (resolved-by-verification, без правок кода)
- На `datalens_dashboard_e2e_rendered.png` подписи оси Y сливаются; вероятно частично артефакт
  масштаба скрина. При необходимости — формат чисел/оси в `chart_config.py`. **Стенд-verify.** Низкий приоритет.
- **РЕЗУЛЬТАТ:** наложение «430M/230M» было симптомом плоского `h=4` (чарт ~40px высотой), а НЕ формата
  чисел. Уже устранено авто-масштабом виджетов (merge `22c32a0`, h→9+). Live-проверка текущим кодом
  (дашборд B1+B2+авто-масштаб, скриншот `D:\.playwright-mcp\datalens_b4_check_dashboard.png`): Y-подписи
  обоих чартов («100M/50M/0» и «500M/400M/300M/200M») чёткие, разнесённые, без коллизии; bar — категориальный
  (B2). Реального остатка нет → **код не трогаем** (фикс под несуществующую проблему был бы лишним).

### Не-задачи (зафиксировать, чтобы не путать в след. сессии)
- **Шумные суффиксы в заголовках** («Auto_BI dash E2E 1781397339», «… sel 1781401771») —
  артефакт E2E-тест-харнесса (уникальность имён), **НЕ продукт**: продукт берёт `spec.title`/
  `chart.title` напрямую через `safe_entry_name` (charset-чистка, без суффиксов). Правок не требует.
- **Сайзинг** — закрыт этой сессией (см. выше).

---

## Порядок и стоп-факторы

1. ~~**B1** (default top-N, BI-agnostic, код+юнит на Windows)~~ — ✅ ЗАКРЫТ (модуль `normalize.py`).
2. **B3-промпт** + **B1-усиление промпта** — вместе, ОДИН прогон eval-сьюта (S2: промпт не на
   Opus в /auto; менять промпт только с eval — `auto_bi eval`, golden+advisor).
3. ~~**B2** (категориальная ось DataLens)~~ — ✅ ЗАКРЫТ (string-cast числового измерения в `chart_config.py`).
4. **B3-детерминированный** (авто-join) — по решению владельца (correctness-тренодофф: группировка по
   имени вместо id может слить строки при неуникальных именах; «group-by-id / display-name» IR/адаптеры
   не умеют — нужен явный owner-выбор по этому тренодоффу). ~~**B4**~~ — ✅ ЗАКРЫТ (resolved-by-verification).

Стоп-факторы: (a) любые правки рендеринга чартов DataLens (B2/B4) визуально подтверждаются ТОЛЬКО
на живом стенде (Mac, `docker start auto_bi_*` + туннель); (b) изменения промпта (B1-усиление/B3) —
S2-стоппер модели + обязательный eval; (c) репозиторий без remote → push невозможен, мерж в main
локально.
