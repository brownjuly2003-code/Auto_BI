# Обогащение #2 — производные метрики (PoP / доля / running total) в IR

**Дата:** 2026-06-25 · **Ветка:** `feat/derived-metrics-pop` · **Статус:** код + offline + числовая
верификация PG-пути готовы; **CH-путь за live-verify gate (см. §6) → в `main` НЕ мержить без него.**

## 1. Зачем

`Measure` умел только `колонка + агрегат`. Самый частый недостающий аналитический паттерн —
сравнение периодов, доля от целого, накопленный итог. Эти меры теперь выражаются в IR одним
полем `Measure.transform`, детерминированно компилируются в оконные функции (без LLM — инвариант
1/D5) и рендерятся обоими адаптерами.

Вектор A из `_NEXT_SESSION.md` (главный кандидат обогащения). Сделано по фазам: сначала весь
механизм + 4 transform end-to-end на уровне кода/тестов, числовая сверка PG-пути; live-сверка
обоих BI — следующий шаг (gate).

## 2. Что добавлено

`MeasureTransform` (StrEnum): `pop_abs`, `pop_pct`, `share_of_total`, `running_total`.
`Measure.transform: MeasureTransform | None = None` (None = обычный агрегат, общий случай).

| transform        | смысл                          | SQL (над базовым агрегатом `agg`)                         |
|------------------|--------------------------------|----------------------------------------------------------|
| `pop_abs`        | абс. изменение к пред. периоду | `agg - lag(agg) OVER (ORDER BY time)`                     |
| `pop_pct`        | отн. изменение к пред. периоду | `(agg - lag(agg)) / NULLIF(lag(agg), 0)`                  |
| `share_of_total` | доля от суммы по колонке        | `agg / NULLIF(sum(agg) OVER (), 0)`                       |
| `running_total`  | накопленный итог по времени     | `sum(agg) OVER (ORDER BY time ROWS UNBOUNDED…CURRENT ROW)`|

## 3. Архитектура — «derived-as-column», минимальная инвазивность

Производная мера вычисляется в SQL_GEN как именованная колонка (`measure_alias`). Поскольку оба
адаптера и так адресуют меры по `measure_alias`, а поле датасета DataLens строится из того же
alias, derived-мера подхватывается обоими BI почти без правок адаптеров.

- **`ir/spec.py`** — `MeasureTransform` + `Measure.transform`; `measure_alias` для transform-меры
  без label даёт `pop_pct_sum_revenue` (не коллизирует с базовой `sum_revenue` на том же чарте);
  `is_percent_measure` (pop_pct/share → процент); `is_compact_number` исключает проценты.
- **`agent/sqlgen.py`** — двухуровневый SELECT, когда есть transform-меры: **inner** GROUP BY
  считает базовые агрегаты под приватными алиасами `__src_i`; **outer** применяет оконные функции
  поверх них, обычные меры проходят насквозь. Не-transform путь (`_generate_flat_sql`) **не
  тронут** — без регрессии для 429 существующих тестов и контрактов. Per-dialect рендеринг —
  через sqlglot: `exp.Lag` → CH `lagInFrame` / PG `LAG` автоматически; `SUM() OVER ()` идентичен.
- **`ir/validate.py`** — pop_*/running требуют первое измерение = TIME (ось-x окна); share требует
  ≥1 измерение; transform запрещён на big_number/pivot/heatmap (нет единой упорядоченной оси).
  Ошибки actionable для repair-петли.
- **`adapters/datalens/dataset.py`** — `_measure_user_type`: процентная мера → `float`.
- **`adapters/superset/form_data.py`** + **`adapters/datalens/chart_config.py`** — percent-формат
  (`.1%` / `formatting{format:"percent"}`) рядом с существующим compact-форматом.

Инварианты 1–8 целы (LLM генерит только IR; адаптеры детерминированы; SQL_GEN без LLM).

## 4. Ключевые тонкости (зафиксированы тестами)

- **Приоритет операторов pop_pct.** `/` связывается раньше `-`: без скобок CH считает
  `src - (lag/lag)`. Числитель `(src - lag)` обёрнут в `exp.paren` (PG спасал случайный CAST, на
  него полагаться нельзя). Тест `test_pop_pct_numerator_is_parenthesized` + числовая сверка.
- **CH `lagInFrame` frame-bounded.** Добавлен явный фрейм `ROWS BETWEEN 1 PRECEDING AND CURRENT
  ROW`, чтобы CH читал ровно предыдущую строку; PG `LAG` фрейм игнорирует (безопасно). Это и есть
  главный CH-специфичный риск → §6.
- **Деление на 0/NULL** → `NULLIF(den, 0)` → результат NULL, не ошибка.
- **Mixed чарт** (база + PoP той же меры): inner считает оба `__src_0`/`__src_1`, outer проводит
  базу насквозь и оборачивает только transform.

## 5. Верификация (выполнено автономно на Windows)

- **Числовая сверка PG-пути end-to-end в DuckDB** (postgres-семантика окон = Greenplum):
  сгенерированный реальным `generate_chart_sql(dialect="postgres")` SQL прогнан на синтетике,
  числа всех 4 transform совпали с независимым ручным расчётом (`test_transform_numbers_match_hand_calc`).
  DuckDB — эфемерная test-dep (`--with duckdb` в CI, `importorskip` локально).
- **Форма SQL обоих диалектов** (lagInFrame vs LAG, inner/outer, фреймы) — unit-тесты.
- **Offline на реальной `semantic/model.yaml`**: дашборд из 3 transform-чартов (PoP%, running
  total, share) → `validate_spec`=0, SQL + payload обоих адаптеров строятся.
- **Гейт:** mypy 0/65 · pytest 456 passed (+27) / 32 deselected · ruff · black.

## 6. 🔴 LIVE-VERIFY GATE (нужен Mac-стенд) — до этого в `main` НЕ мержить

PG-путь сверен численно через DuckDB. **ClickHouse `lagInFrame` — frame-bounded, его поведение с
заданным фреймом не воспроизводится без живого CH.** Перед merge на стенде:

1. **CH-числа PoP/running.** `auto_bi build --auto`/ручной spec с pop_abs/pop_pct/running на
   `dm.sales_daily` по `date` → сверить ряд с ручным CH-запросом (как B-серия адекватности).
   Особое внимание: первая строка PoP = NULL; `lagInFrame` с фреймом `1 PRECEDING` даёт именно
   предыдущую строку (а не peer по RANGE).
2. **share по категории** — суммируется в 100% на стенде.
3. **Рендеринг формата процента в обоих BI.** Superset `.1%` и **DataLens
   `formatting{format:"percent"}`** — точная форма DataLens-payload реверснута из демо, требует
   подтверждения на стенде («доки ≠ реальность» — урок Phase 3).
4. Прогнать на **обоих** BI (Superset + DataLens), сверить числа с прямым CH.

## 6.1 Итог live-verify (2026-06-26, Mac-стенд)

Гейт **поймал 3 реальных бага CH-пути**, которые числовая сверка PG-пути в DuckDB не ловила
(Postgres-семантика окон ≠ ClickHouse). Все исправлены и перепроверены на живом CH +
собраны на обоих BI.

1. **`pop_abs` первая строка = `agg[0]` вместо NULL.** CH `lagInFrame` для значения вне фрейма
   возвращает дефолт типа (`0`), а не NULL как PG `LAG` → PoP первой строки выходил `agg[0]−0`.
   Фикс: источник lag обёрнут в `toNullable` ТОЛЬКО на CH (PG `LAG` и так даёт NULL) →
   `lagInFrame(toNullable(__src_0))`. Подтверждено пробой: `toNullable` → `\N` на первой строке.
2. **`pop_pct` / `share_of_total` усекались до scale 2 → 0.00.** CH `Decimal/Decimal` сохраняет
   масштаб делимого (Decimal(18,2)) → доля `0.0084` округлялась в `0.00`, доли категорий не
   суммировались в 1 (давали 0.92). Фикс: числитель кастится в Float64 (`_safe_div` →
   `CAST(num AS DOUBLE)`), деление становится плавающим в обоих диалектах (PG numeric и так точен).
3. Оба фикса **кросс-диалектны**, не-transform путь и PG-путь не тронуты.

**Верификация (живой CH через `ssh→docker exec clickhouse-client`):** реальный код
`generate_chart_sql(dialect="clickhouse")` прогнан на `dm.sales_daily`, ряды pop_abs/pop_pct/
running/share сверены поэлементно с независимым расчётом из базового GROUP BY: **PASS** (730 дат;
share-по-категории = 1.000000). Гейт: mypy 0/65 · pytest **457** · ruff · black.

**Live-build обоих BI:**
- **Superset** (`/superset/dashboard/16/`): 3 transform-чарта прошли EXPLAIN+LIMIT на CH;
  payload корректен — pop_pct/share `y_axis_format = ".1%"` (рендерится как процент),
  running_total `.3~s` (компакт, рубли). ✅ ЗАКРЫТО.
- **DataLens** (`/bwyp.../` и т.д.): числа корректны, чарты рендерят реальные данные, компакт
  работает (running `300B`). 🔴 **Percent-ось показывает сырое 0..1 (`0.25`, не `25%`)** —
  placeholder `formatting{format:"percent"}` НЕ переключает тип формата на стенде (компакт
  `format:"number"` там работает). Проверены 3 формы payload (placeholder; dataset-поле
  `result_schema.formatting`; field-only без placeholder-shadow) — все рендерят сырой ratio.
  **Вывод: known-limitation DataLens-адаптера** (числа верны, дефект только в отображении %);
  правильная форма требует авторитетного реверса из Wizard (UI-popup не поддаётся a11y-автоматизации).
  Код возвращён в исходное состояние (эксперименты откатаны), комментарий у `_PERCENT_FORMATTING`
  фиксирует находку. Открытый пункт.

**Итог:** числовое ядро (§6.1/§6.2) ЗАКРЫТО на обоих BI и обоих диалектах; percent-РЕНДЕР закрыт
на Superset, на DataLens — known-limitation. Merge derived-metrics в main разблокирован по числам;
DataLens percent-формат — отдельный реверс.

## 6.3 DataLens percent — РЕВЕРС ПРОДВИНУТ, но рендер в чарте не закрыт (2026-06-26 cont. 2)

Авторитетный реверс из Wizard **dataset field editor** (Fields → поле → «Display settings» →
Format: Number|Percent — popup ДОСТУПЕН через a11y, в отличие от chart-field popup). Снята
точная форма percent-формата поля датасета:

```
ui_settings : '{"numberFormatting":{"format":"percent","showRankDelimiter":true,"prefix":"","postfix":"","precision":2}}'
formatting  : null    ← percent НЕ здесь
```

**КЛЮЧЕВАЯ НАХОДКА:** percent-формат живёт в поле `ui_settings` (JSON-СТРОКА, ключ
`numberFormatting`) result_schema-поля датасета, **НЕ в `formatting`** (которое остаётся null) и
**НЕ на chart-placeholder** (где прошлая сессия пробовала `formatting:{format:"percent"}` — оттого
и провал: пробовали неверный ключ на неверном уровне).

**НО реализация через dataset `ui_settings` НЕ ДАЛА percent в рендере чарта** (проверено на стенде,
2 сборки):
- `createDataset` ПРИНИМАЕТ и СОХРАНЯЕТ `ui_settings` percent (verified: `us/getEntry` свежесозданного
  датасета вернул наш `ui_settings` дословно);
- но рендер — **сырой ratio во всех viz**: line/bar Y-ось `0.25` (не `25%`), flatTable-колонка `0,00`
  (не `0,3%`). charts-engine при рендере НЕ применяет dataset-field `ui_settings`.
- chart-placeholder `formatting:{format:"number",unit,precision}` РАБОТАЕТ (компакт 277,0M), а
  `{format:"percent"}` — нет → DataLens placeholder formatting поддерживает только number-тип, не percent.

**Стена:** percent в чарте задаётся форматом **поля внутри чарта** (Wizard: клик на поле в Y →
popover «Number format») — а этот popover, как и отметила прошлая сессия, **НЕ берётся Playwright
a11y** (snapshot его не видит; gear секции Y открывает только add-field, не формат). Эксперимент
(dataset `ui_settings`) ОТКАЧЕН — не оставляем код, не дающий рабочего percent в чарте.

**Следующий шаг (нужен человек ИЛИ сетевой перехват):** задать percent полю **внутри чарта** в
Wizard руками → перехватить `us/updateEntry`/chart `data` payload (browser_network_requests при
Save) → увидеть, КАКОЙ ключ chart-config несёт percent (вероятно `ui_settings`/`formatting` на
chart-field, не на placeholder) → реплицировать в `build_chart_shared`. Альтернатива — поднять
DataLens charts-engine на чтение dataset-field `ui_settings` (вне нашего кода). До этого DataLens
percent = known-limitation (числа верны; Superset % работает).

## 7. Дальше (по фазам, не в этой сессии)

- `yoy`/`mom` (lag по календарю на N периодов, не lag(1)) — нужен period-matching по дате; отдельный
  инкремент.
- Промпт `propose`: научить LLM предлагать transform из текста запроса («динамика», «доля»,
  «нарастающим итогом»). Правка промпта = S2 (eval обязателен; не Opus в /auto) — детерминированный
  путь (CLI/fields) уже работает.
- v2-формат процента «50,0 %» с локалью на DataLens-стенде (как B5 compact).
