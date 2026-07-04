# S14 — yoy-KPI: скалярный period-compare примитив (B1 роадмапа)

**Дата:** 2026-07-04 · **Гейт:** 🟠 S4/IR (правка контракта `ir/spec.py`) ·
**Статус:** ✅ РЕАЛИЗОВАНО — владелец выбрал **Вариант A** + «последний присутствующий бакет» (2026-07-04);
офлайн-гейт зелёный (ruff/black · mypy 0/69 · pytest 655 · advisor 9/9) + DuckDB PG-числа + **CH
live-verified** на живом стенде (`scripts/verify_live_clickhouse.py`: yoy-скаляр = ручной расчёт). ·
**Закрывает:** P-5 / B1.

> S4-дисциплина (CLAUDE.md стоппер S4): изменение IR-схемы = STOP, решение владельца. Этот документ —
> S4-артефакт (как `2026-06-29-core-deepening-a2-a5.md` для трека A). Владелец дал «go» на Вариант A.
>
> **Уточнение по имплементации:** контракт `compare` размещён на **`Measure`** (не на `ChartQuery`, как
> в §4 черновика) — рядом с `transform`/`denominator`/`lag_periods`; тогда `is_percent_measure`/
> `measure_alias`/адаптеры подхватывают его без протаскивания query-контекста → **нулевые правки
> адаптеров** (процентный big_number уже рендерится). Всё остальное — по Варианту A.

## 1. Задача

KPI-плитка «**+X % г/г**» на авто-обзоре: когда у витрины ≥2 лет истории, показать не только уровень
(«Выручка: 236 млрд»), но и годовое изменение как отдельный **скаляр** («Выручка, г/г: +12,4 %»).
Сейчас это невыразимо.

## 2. Почему невыразимо сейчас (grounded в коде)

`yoy_pct` — **оконный РЯД**, не скаляр:

- `MeasureTransform.YOY_PCT` компилируется в `lag(periods_per_year)` OVER (ORDER BY time) — одно
  значение **на каждый период** вдоль оси x (`sqlgen._window_expr`, `_generate_windowed_sql`).
- `validate.py:35` — `_TRANSFORM_UNSUPPORTED_VIZ = {BIG_NUMBER, PIVOT, HEATMAP}`: любой transform
  на `big_number` запрещён (нет одной упорядоченной оси).
- `validate.py:270` — `yoy_pct` требует non-day `time_grain`; `_ORDERED_TRANSFORMS` требует первое
  измерение = TIME.
- `_validate_viz_shape` — `big_number` = ровно одна мера, **ноль измерений** (`forbid(dims, series,
  rows, cols)`), адаптер читает одну агрегированную строку (`form_data.py:100`, `MAX` как identity).

Итог: KPI-скаляр «+X % г/г» = **новый примитив сравнения периодов**, а не переиспользование
`yoy_pct` (тот даёт ряд). Это подтверждено ещё в cont.13 (roadmap B1). → S4/владелец.

## 3. Что рендерят оба BI (определяет стоимость вариантов)

- **Superset** `big_number_total`: `metric` (число) + `subheader` (СТАТИЧЕСКАЯ строка, сейчас `""`).
  Нативное сравнение периодов (`comparison_type`/`time_compare`) — отдельная viz-фича, **DataLens
  аналога не имеет**.
- **DataLens** `metric`: одна плитка-значение.
- **ОБА уже умеют процентный big_number** (`is_percent_measure` → Superset d3 `%` / DataLens
  percent-`formatting`) — процентная плитка рендерится сегодня без правок адаптеров.

Вывод для дизайна: путь «значение плитки = само % изменение» — **бесплатен по адаптерам и уже
кросс-BI-верифицирован**. Путь «уровень + % в подзаголовке» — Superset-специфичен и на DataLens
упрётся в engine-limit (как percent-на-оси, память `autobi-bi-engine-limits`).

## 4. Варианты (контракт)

### Вариант A — «Скаляр-% как отдельная плитка» (self-contained, conditional aggregation) ✅ РЕКОМЕНДУЮ

Новый скалярный примитив: KPI, **значение которого = само годовое изменение** (%), считается
условной агрегацией по последнему периоду и периоду год назад — БЕЗ окна, БЕЗ измерения, одна строка.

**Контракт (`ir/spec.py`):**
```python
class ScalarCompareKind(StrEnum):
    YOY = "yoy"   # vs тот же период год назад
    POP = "pop"   # vs предыдущий период

class ScalarCompare(BaseModel):
    column: str                     # time-колонка витрины, напр. "date"
    grain: TimeGrain                # month/quarter/year — что считать «периодом»
    kind: ScalarCompareKind = YOY
    output: Literal["pct","abs"] = "pct"   # значение плитки: % изменение или абс. дельта

class ChartQuery(...):
    compare: ScalarCompare | None = None   # ТОЛЬКО для big_number (валидация)
```

**SQL (`_generate_compare_kpi_sql`, новый путь, триггер `query.compare`):** одна строка через
условную агрегацию (переиспользует `_safe_div`/Float64-каст derived-metrics):
```sql
SELECT
  (sumIf(m, bucket = p_cur) - sumIf(m, bucket = p_prev))
    / NULLIF(sumIf(m, bucket = p_prev), 0)  AS "yoy_sum_revenue"      -- output=pct
FROM t, (SELECT max(toStartOf<Grain>(date)) AS p_cur,
                max(toStartOf<Grain>(date)) - INTERVAL 1 <YEAR|Grain> AS p_prev FROM t) b
```
`p_cur` = последний присутствующий бакет; `p_prev` = год назад (для POP — предыдущий бакет).
`agg` — любой (sum/avg/count/count_distinct через `_AGG_FUNC`, `avgIf`/`countIf`). Возвращает одну
строку → big_number-форма («ноль измерений») сохраняется буквально.

**Рендер:** `output=pct` → `is_percent_measure`-ветка (оба BI, готово); `output=abs` → compact-число.
`measure_alias` получает префикс `yoy_`/`pop_` (напр. `yoy_sum_revenue`), не коллизирует с уровневым KPI.

**Autospec:** при `yoy_on` (уже вычисляется, `_yoy_applicable`, ≥13 мес) рядом с P1-уровнем героя
добавить плитку «`<герой>`, г/г» (`compare=ScalarCompare(column=time, grain=MONTH, kind=YOY)`).
Пользователь видит две плитки: «Выручка: 236 млрд» и «Выручка, г/г: +12,4 %».

**Плюсы:** big_number остаётся истинным скаляром (одна строка, ноль измерений); адаптеры не трогаем
(процентный KPI готов и кросс-BI-верифицирован); универсально (любой agg, любой grain, yoy И pop);
честно на обоих BI. **Минусы:** новый SQL-путь (но простой — условная агрегация, не окно) + новый
nested-контракт `ScalarCompare`. **Оценка:** M.

### Вариант B — «Window-reduce»: `yoy_pct` на big_number, свёрнутый до последней точки

Разрешить `transform=yoy_pct/pop_pct` на big_number; переиспользовать `_generate_windowed_sql`, но
свернуть ряд до последней строки (`ORDER BY time DESC LIMIT 1`). Требует, чтобы big_number нёс
time-колонку как **внутреннюю ось окна, не отображаемое измерение** → исключение из «big_number =
ноль измерений».

**Плюсы:** переиспользует существующие `yoy_pct` + `time_grain` (меньше нового SQL). **Минусы:**
дырявит инвариант формы big_number (скрытое измерение только для окна); свёртка `LIMIT 1` завязана на
порядок; концептуально мутнее (KPI-скаляр через оконный ряд). **Оценка:** M (но грязнее A).

### Вариант C — «Comparison-subheader»: уровень + % в подзаголовке плитки

KPI показывает уровень героя, а % г/г — в `subheader` (Superset) / второй строкой (DataLens).

**Минусы (блокирующие):** Superset `subheader` — статическая строка, % считается на запросе → пришлось
бы дёргать значение на этапе build (лишний запрос в адаптере, ломает «адаптер детерминирован от IR»).
Нативный Superset `time_compare` — viz-специфичен, **на DataLens аналога нет** → percent-в-подзаголовке
= engine-limit на DataLens (тот же класс, что percent-на-оси). Нарушает кросс-BI-паритет. **Отклонить**
как основной; эквивалент «уровень + %» достигается двумя плитками из Варианта A без этих проблем.

### Отклонено сразу
- **Superset-native `comparison_type`** — Superset-only, ломает universal-not-retail/оба-BI.
- **Ничего не делать** — не закрывает P-5 (KPI «+X % г/г» — явный запрос).

## 5. Рекомендация

**Вариант A** (скаляр-% как отдельная плитка, conditional aggregation). Единственный, который:
(1) держит big_number истинным скаляром; (2) не трогает адаптеры (готовый процентный KPI, оба BI);
(3) универсален (yoy И pop, любой agg/grain); (4) даёт эффект «уровень + %» парой плиток в autospec
без Superset-специфики. B — запасной (грязнее форму), C — отклонить (не кросс-BI).

## 6. План имплементации (после «go», Вариант A)

1. **`ir/spec.py`** — `ScalarCompareKind`, `ScalarCompare`, `ChartQuery.compare`; `measure_alias`/
   `is_percent_measure` учитывают compare-меру (префикс `yoy_`/`pop_`, output=pct → процент).
2. **`ir/validate.py`** — `compare` только на `big_number` + только одна мера; `column` = TIME-колонка
   витрины; `grain` ∈ {week,month,quarter,year}; взаимоисключимо с `transform`/`denominator`/`bins`;
   несовместимо с dimensions/series/rows/columns. Actionable-ошибки для repair-петли.
3. **`agent/sqlgen.py`** — `_generate_compare_kpi_sql` (условная агрегация, одна строка; Float64-каст
   как `_safe_div`; per-dialect `toStartOf*`/`date_trunc` через существующий `_time_grain_expr`;
   `INTERVAL 1 year` кросс-диалектно через sqlglot). Роутинг в `generate_chart_sql` рядом с `bins`.
4. **`agent/autospec.py`** — при `yoy_on` добавить плитку «`<герой>`, г/г» (P1b, рядом с уровнем);
   бюджет чартов не ломать (плитка компактна, `_MAX_KPIS`).
5. **Тесты** — sqlgen shape+числовая сверка (DuckDB, PG-семантика) + validate + autospec + alias.
6. **CH live-verify** — `scripts/verify_live_clickhouse.py`: yoy-скаляр по `dm.sales_daily.revenue`
   (последний месяц vs год назад) = независимый ручной расчёт. **Gate: живой Mac-стенд** (как
   derived-metrics §6 — CH-only баги реальны: lagInFrame-NULL, Decimal-truncation). Без стенда —
   офлайн-гейт зелёный, merge держать до CH-сверки (дисциплина проекта).
7. **Доки** — ARCHITECTURE §3.4 (новый примитив), USER_GUIDE.

## 7. Открытые под-вопросы (решить при выборе)

- **«Последний период» может быть неполным** (текущий месяц — пара дней) → yoy неполного к полному
  вводит в заблуждение. Дефолт A: последний ПРИСУТСТВУЮЩИЙ бакет (демо 24 полных мес — ок); опция
  «последний ПОЛНЫЙ бакет» / YTD — будущий инкремент, не в этой итерации.
- **output**: только `pct` в v1 (KPI «+X %»), `abs` — тривиальное расширение, но нужен ли сразу?
- **kind**: yoy достаточно для P-5; pop — почти бесплатно тем же путём (оставить в контракте, autospec
  использует только yoy).
