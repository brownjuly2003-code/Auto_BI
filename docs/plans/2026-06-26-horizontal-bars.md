# Horizontal bars for categorical rankings (chart-quality workstream #1)

**Дата:** 2026-06-26
**Контекст:** директива «качество графиков везде». Самый видимый сквозной дефект на живом
стенде — длинные RU-категории на вертикальных барах обрезаются/поворачиваются
(«Бытовая…», «Кондите…», «Мясо и п…»). research.md §"Auto-chart selection" (Rill/Metabase)
подтверждает rule-based выбор: ранжированные категории → горизонтальные бары.

## Проблема

`bar`/`stacked_bar` над категориальным (не-time) измерением рендерится вертикальными
колонками. Длинные текстовые подписи на горизонтальной оси X не помещаются → Superset/
DataLens их обрезают или поворачивают. Это читаемость (не корректность), но самый
заметный дефект продукта на демо.

Конвенция dataviz (Cleveland/Few): ранжированный **bar chart = горизонтальный** (подпись
получает всю ширину строки), временной **column chart = вертикальный** (читается слева
направо по времени).

## Дизайн (display-only, БЕЗ правки IR — не триггерит S4)

Ориентация в текущем IR не выражается (нет поля на `ChartSpec`/`ChartQuery`). Добавлять
поле = правка IR-схемы → стоппер S4. Промпт-правка → S2. Поэтому — **детерминированный
display-предикат**, как B2 (числовая ось DataLens) и B1 (top-N): инвариант 1 цел (LLM
по-прежнему отдаёт только IR), промпты не тронуты, оба адаптера наследуют решение.

**Предикат** `agent/normalize.py::is_horizontal_bar(chart, model) -> bool` (переиспользует
`_is_time_dimension`):
- `viz ∈ {BAR, STACKED_BAR}` (pie/line/area/прочее — нет);
- есть первое измерение;
- первое измерение **НЕ** time-колонка (категориальное) → `True`.

Время-бары (редкость; обычно line) остаются вертикальными. Предикат чистый,
без data-probe, детерминированный.

**Продуктовое решение:** ВСЕ категориальные бары → горизонтальные (а не только «длинные»
— длину подписи offline не знаем; категориальный бар с короткими подписями горизонтально
тоже читается; консистентность + конвенция). Если потребуется вертикаль для малого-N —
точечный твик later.

**Проводка (адаптеры считают предикат от `self._model`, билдеры остаются чистыми):**
- Superset `form_data.build_form_data(chart, dataset_id, *, horizontal=False)`: при
  `horizontal` → `form_data["orientation"] = "horizontal"` (ECharts `echarts_timeseries_bar`).
  `xAxisForceCategorical`/top-N-sort сохраняются (ось измерения остаётся категориальной).
  `adapter.create_chart` считает `is_horizontal_bar(chart, self._model)`.
- DataLens `chart_config.build_chart_shared(..., *, horizontal=False)`: при `horizontal` →
  `viz_id "column" → "bar"` (горизонтальный бар DataLens). Placeholders x/y и B2-дискретизация
  без изменений. `adapter.create_chart` (line 465) считает предикат от `self._model`.

`self._model` опционален → `horizontal = model is not None and is_horizontal_bar(...)`
(нет модели → вертикаль, безопасный дефолт).

## Верификация

**Автономно (Windows, shape-уровень):**
- `is_horizontal_bar` unit-тесты (категор. bar→True; time bar→False; line/pie→False;
  stacked категор.→True; без измерения→False) — `tests/test_normalize.py`.
- Superset: `build_form_data(..., horizontal=True)["orientation"]=="horizontal"`; вертикаль
  без ключа — `tests/test_superset_adapter.py`.
- DataLens: `build_chart_shared(..., horizontal=True)` viz id `"bar"`; дефолт `"column"` —
  `tests/test_datalens_chart.py`.
- Полный гейт: ruff · black · mypy · pytest · advisor-eval.

**🔴 Стенд-gated (Mac, B2-прецедент «доки≠реальность»):** точные payload-ключи требуют
live-реверса/подтверждения. Superset: подтвердить, что `orientation:"horizontal"` реально
переворачивает `echarts_timeseries_bar` (контракт + визуал). DataLens: подтвердить, что
`bar` viz принимает те же placeholders x/y и рендерит горизонтально (реверс из Wizard, как B2).
Визуальная «красота»-планка — за владельцем (скриншоты на ревью).

## Инварианты

1 (IR-first) — цел: LLM отдаёт тот же IR, ориентация решается детерминированным кодом.
4 (вопросы) — н/п. 5 (advisory) — н/п. Промпты не тронуты (инвариант 8 / S2 н/п).
IR-схема и `BIAdapter` Protocol не меняются (S4 н/п) — `horizontal` это внутренний
параметр билдера, не часть Protocol-сигнатуры `build`.
