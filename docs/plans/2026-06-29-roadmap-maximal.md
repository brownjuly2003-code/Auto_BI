# Auto_BI — максимальный план доработки (roadmap)

> Составлен 2026-06-29 (после cont. 11). Полный ландшафт открытых направлений с явным
> гейт-статусом каждого, чтобы любая следующая сессия сразу видела, что можно брать
> автономно, а что требует владельца/стенда/S2. Канонический статус — `CLAUDE.md`;
> оперативный handoff — `_NEXT_SESSION.md`; история фаз — `docs/PLAN.md`.

## Где мы сейчас (база)

origin/main = `8415afa`, CI зелёный. Закрыто и в main:
- **Пайплайн:** три входа (text-first / fields-first / auto-overview), один путь
  `validate → normalize → SQL-guard → adapter`; инварианты 1–8.
- **IR-ядро:** меры + агрегаты, JOIN по рёбрам модели, derived-метрики
  (`pop_abs`/`pop_pct`/`share_of_total`/`running_total`), **ratio-мера** (`denominator`),
  **`time_grain`** (day/week/month/quarter/year), **`yoy_pct`** — всё CH-live-verified.
- **Авто-обзор (`autospec`):** KPI → динамика (грейн-aware) → **yoy-линия (≥2 года)** →
  топ-N разрезы → доля (не pie) → детализация; insight-слой «Что видно» (тренд/разворот/
  темп/сезонность/аномалия/лидер/концентрация/разброс/доля).
- **Адаптеры:** Superset (полный), DataLens (полный, кроме percent-на-оси). Greenplum —
  интроспекция + advisor (не BI-таргет).
- **Advisor (ров):** диагностика анти-паттернов + **remediation-DDL** (projection/денорм-
  витрина/distribution).
- **Продукт:** web UI (3 режима + observability-панель), auth/RBAC (opt-in), Store v3,
  CLI, eval-сьют (55 кейсов), live-verify репо-скрипт `scripts/verify_live_clickhouse.py`.

**Чистая автономная code-линия АВТО-ОБЗОРА исчерпана; observability-трек закрыт (E2 ✅ cont.12 —
реальный токен-учёт по Anthropic-пути).** Остаются автономные 🟢-кандидаты в eval/insight/hardening:
**B1** yoy-KPI · **B2** eval-кейс авто-обзора · **B3** percent-aware нарратив yoy · **D1** advisor-правило ·
**H1** флейк-харден — ниже помечены 🟢.

> **АУДИТ ПОСТ-cont.16 (2026-06-29, `6d0c8da`):** на вопрос «всё ли возможное реализовано» —
> роадмап ФИЧ исчерпан, но адверсариальный аудит самого кода нашёл реальные баги в недавних
> примитивах A3/A4 (0 HIGH · 3 MED · 3 LOW). Исправлено 5 offline-верифицируемых багфиксов
> (running_share reversed Pareto в normalize · histogram NULL dialect-split в sqlgen · histogram+join
> валидация · insights running_share/share_of_total · measure-alias uniqueness), CH live-verify 11/11.
> Осталось стенд-gated: **C7** DataLens histogram bucket sort (ниже). **Урок: «фичи исчерпаны» ≠
> «код без багов» — адверсариальный аудит обязателен перед выводом «брать нечего».**

## Гейт-легенда

- 🟢 **АВТО** — детерминированно, offline- или stand-optional-верифицируемо, без правки
  промптов/контракта → можно в Opus `/auto`.
- 🟡 **S2** — меняет промпт `propose`/`grounding` → обязателен прогон eval-сьюта; по
  правилам проекта это Fable/ручная сессия, НЕ Opus/auto.
- 🟠 **S4/IR** — меняет IR-контракт (`ir/spec.py`) → решение владельца («предложи варианты»).
- 🔵 **СТЕНД** — нужен живой Mac-стенд / внешние креды.
- 🟣 **ВЛАДЕЛЕЦ** — продуктовое решение (scope/приёмка).
- 🔴 **НАРУЖУ** — деплой/публикация/внешний ключ.

---

## Трек A — Аналитическое ядро (IR / SQL-gen)

| ID | Гейт | Что | Точки входа · verify · оценка |
|----|------|-----|-------------------------------|
| A1 | ✅ готово | **mom/wow** — month/week-over-month уже работает композицией `time_grain`+`pop_pct` (отдельный примитив НЕ нужен; mom покрыт live-verify cont.9). | — |
| A2 | ✅ готово (cont.14) | **Лаг на произвольные N периодов** — `Measure.lag_periods: int\|None` (pop_abs/pop_pct vs N периодов назад). Переиспользует frame-bounded `lag(k)` (k=1 → SQL байт-в-байт); валидация только pop_abs/pop_pct; alias `_lag<N>`. CH live-verified (lag3 на 24 мес). Дизайн: `docs/plans/2026-06-29-core-deepening-a2-a5.md`. | `ir/spec.py`, `sqlgen._window_expr`, `ir/validate.py`. M |
| A3 | ✅ готово (cont.15) | **Кумулятивная доля / Pareto** — `MeasureTransform.RUNNING_SHARE` (категории ранжир. по мере убыв., накопл. доля от итога: `SUM(src) OVER (ORDER BY src DESC ROWS …) / SUM(src) OVER ()`). Окно по МЕРЕ, не по времени → НЕ в `_ORDERED_TRANSFORMS`; требует измерение, не time-ось. CH live-verified (4200 магазинов, закрывается на 1.0). | `ir/spec.py`, `sqlgen._window_expr`, `ir/validate.py`. M |
| A4 | ✅ готово (cont.16) | **Гистограмма/распределение** — `Viz.HISTOGRAM` + `ChartQuery.bins`: числовая колонка role=measure бьётся на `bins` равноширинных корзин, мера=COUNT строк. Отдельный `_generate_histogram_sql` (min/width-подзапрос + CROSS JOIN + bucket-выражение, Float64-каст против Decimal-мисбиннинга, квалиф. против alias-shadow); рендер=бар по корзинам в обоих адаптерах (бинирование в SQL, без реверса движка). CH live-verified (8 корзин по products.price, все 2000 строк). | `ir/spec.py`, `sqlgen`, `validate`, оба адаптера. L |
| A5 | 🟠 S4/IR | **Когортный/retention** анализ — самый сложный, доменно-чувствительный. | новый модуль; verify: стенд. L |

> A5 = изменение контракта → НЕ автономно. Брать только по «предложи варианты» с
> владельцем (как трио cont.9). Приоритет низкий — ядро уже покрывает 90% паттернов.
> **A2 (`lag_periods`, cont.14) + A3 (`running_share`/Pareto, cont.15) + A4 (`Viz.HISTOGRAM`,
> cont.16) закрыты** — owner открыл трек A2–A5 «решай сам», реализованы три инкремента по
> design-доку `docs/plans/2026-06-29-core-deepening-a2-a5.md`, все CH live-verified. Histogram
> рендерится баром по корзинам (бинирование в SQL → без стенд-gated реверса движка, как
> опасались). Осталось: **A5 когорты** — отложить (нет клиентской сущности в демо → невериф +
> риск universal-not-retail). **Трек A автономно ИСЧЕРПАН** (A2–A4 done, A5 нужна модель с entity).

## Трек B — Авто-обзор (`autospec`) + insight-слой (display-only)

| ID | Гейт | Что | Точки входа · verify · оценка |
|----|------|-----|-------------------------------|
| B1 | 🟠 S4/IR | ~~yoy-KPI big_number~~ **— НЕ 🟢 АВТО (проверено cont.13).** Невыразимо без правки контракта: `_TRANSFORM_UNSUPPORTED_VIZ` запрещает transform на `big_number`, а `yoy_pct` требует НЕ-day `time_grain` (оконный РЯД, не скаляр) — `ir/validate.py:35,242`. yoy-KPI = новый скалярный примитив сравнения-периодов в IR → решение владельца, как трио cont.9. | `ir/spec.py` (новый scalar-compare), `sqlgen`, `autospec`. M |
| B2 | ✅ покрыто | **eval-кейс авто-обзора — НЕ нужен (проверено cont.13).** `build_auto_spec` уже зафиксирован юнит-тестами `tests/test_autospec.py`: каждая P-секция + `validate_spec==[]` + идемпотентность (`model_dump` дважды) + реальная 8-чарт-раскладка `model.yaml`. eval-сьют — LLM/advisor-only (autospec детерминирован, golden был бы дублем). Остаток аудита закрыт этим покрытием. | — |
| B3 | 🟢 АВТО (спорно) | **Percent-aware нарратив yoy-линии** в insight-слое — вместо skip давать «год-к-году ускоряется/замедляется». ⚠️ cont.12-ревью: **частично реверсит осознанное cont.11-решение** «перцентная линия сама и есть инсайт» (`_observe_chart` skip); маржинальная ценность = пере-чтение последней точки чарта ИЛИ субтильный second-order сигнал (риск шума). Брать ТОЛЬКО под реальный запрос аналитика, не как автономный padding. | `agent/insights.py`; +offline-тесты. M |
| B4 | 🟢 АВТО (low) | **Волатильность** (CV детрендир. остатков) как наблюдение — низкий headline-приоритет, рискует отрезаться cap'ом. Брать только если есть запрос. | `agent/insights.py`. S |
| B5 | 🔵 СТЕНД | **Преднастроенный период** обзора (last 90d по умолчанию) — нужен реверс `defaultDataMask` Superset / DataLens на живом стенде. | `adapters/.../native_filters.py`, `autospec`. M |

> **Трек B автономно ИСЧЕРПАН (cont.13):** B1 = S4/владелец (контракт), B2 = уже покрыто,
> H1 = закрыт. Остаются только B3 (спорный — под запрос, не padding) и B4 (low/под запрос).

## Трек C — BI-адаптеры

| ID | Гейт | Что | Заметка |
|----|------|-----|---------|
| C1 | 🔵 СТЕНД (hard) | **DataLens percent на оси** — известное ограничение движка (charts-engine не применяет percent к рендеру оси; числа верны, Superset % работает). Нужен сетевой перехват chart-config payload на стенде. **НЕ реверсить заново вслепую** (память `autobi-bi-engine-limits`). | план §6.3 derived-metrics |
| C2 | 🔵 СТЕНД | **DataLens B2** — форс категориальной оси числового измерения (Superset решает через `xAxisForceCategorical`; DataLens — нет). Реверс механизма на стенде. | `adapters/datalens/chart_config.py` |
| C3 | 🟣/✋ | **DataLens B4** — косметика осей (шрифт/локаль/SI-суффикс «B/M» vs «млрд») = тема/локаль инстанса движка, **НЕ наш код**. Преимущественно non-actionable. | память `autobi-bi-engine-limits` |
| C4 | 🔵 СТЕНД | **Luxms-адаптер** — GO-with-stand (полный REST/CRUD source→cube→dashlet→dashboard, JWT, нативный CH). Реализация по зеркалу DataLens-трека. **Gate:** демо-креды `sandbox.demo.luxmsbi.com` ИЛИ self-hosted Docker-стенд на Mac. | `docs/plans/2026-06-14-luxms-adapter-plan.md`. L |
| C5 | 🔵 ЛИЦЕНЗИЯ | **Visiology-адаптер** — NO-GO автономно (нет public REST для авторинга, только UI-Designer). **Gate:** лицензионный стенд v3 от заказчика. | `docs/plans/2026-06-14-visiology-spike.md` |
| C6 | 🟣 ВЛАДЕЛЕЦ | **Новый BI-движок** (Metabase / Apache Superset Cloud / …) — по запросу, через фабрику `adapters/factory`. | — |
| C7 | 🔵 СТЕНД | **DataLens histogram bucket sort** (аудит пост-cont.16) — корзины гистограммы рендерятся ЛЕКСИКОГРАФИЧЕСКИ («50» после «350»): категориальная ось без `sort`-поля (`is_horizontal_bar`=False для HISTOGRAM → ветка `sort` не ставится). SQL корректен (`ORDER BY bucket`), Superset ОК → adapter-инконсистентность. Фикс (sort по числовой корзине, ASC) требует проверки sort-семантики DataLens на живом стенде — не реверсить вслепую (`autobi-bi-engine-limits`). | `adapters/datalens/chart_config.py`; verify: стенд. S |

## Трек D — Advisor / Feasibility (ров)

| ID | Гейт | Что | Заметка |
|----|------|-----|---------|
| D1 | 🟢 АВТО | **Новые advisor-правила** — детерминированные анти-паттерны + remediation + eval-кейсы (тем же паттерном, что projection/денорм/distribution). Брать конкретное правило под реальный кейс, НЕ плодить false-positive (`point_lookup_pattern` — осознанный non-goal: «нет реального кейса для тюнинга»). | `advisor/findings.py`, `advisor/core.py`, eval. M |
| D2 | 🟠 по движку | **Advisor для новых движков** — если добавится DWH/BI. | `advisor/<engine>.py` |

## Трек E — Продукт / деплой / observability

| ID | Гейт | Что | Точки входа · оценка |
|----|------|-----|----------------------|
| E1 | 🔴 НАРУЖУ | **Деплой публичного демо** на синтетике (Render/Fly/VPS) — закрывает PMF-разрыв BCG-аудита (delivery=D). Anthropic-клиент влит (снимает GraceKelly-SPOF) + Dockerfile есть → технически разблокировано. **Gate:** «деплой» + Anthropic-ключ. | `llm/anthropic.py`, Dockerfile. M |
| E2 | ✅ готово | **Token-учёт по Anthropic-пути** — реальные `usage.input_tokens/output_tokens` захвачены в nullable-колонки `llm_calls.input_tokens/output_tokens` (Store v4→v5, идемпотентная миграция как v2); `llm_usage_summary` суммирует NULL-игнорируя + `token_calls`; панель «Наблюдаемость» показывает токен-ячейки при наличии данных, символы остаются универсальным прокси; GraceKelly usage не отдаёт → его строки NULL. **$-стоимость = осознанный non-goal** (нужна поддерживаемая таблица цен; токены — дрейф-устойчивая правда). Закрыл gap ARCHITECTURE §3.9. Смержено cont.12 (`6a19002`), гейт 0/66·555·9/9 + UI live-verified. | done |
| E3 | 🟣 ВЛАДЕЛЕЦ | **Демо-GIF/видео** для README/landing — owner/asset-gated. | — |
| E4 | 🟣 по запросу | **Auth/RBAC продуктизация** — реализована (opt-in `AUTO_BI_AUTH_ENABLED`); дальше только по спросу (single-user — осознанное решение). | `auth.py`, USER_GUIDE §7 |

## Трек F — LLM-режимы (text-first / fields-first) — S2

| ID | Гейт | Что | Заметка |
|----|------|-----|---------|
| F1 | 🟡 S2 | **LLM-включение трио** (ratio/grain/yoy) в text-first `propose` — чтобы «средний чек», «выручка по месяцам», «год-к-году» собирались словами. Сейчас трио доступно только через fields-first/программный spec. Промпт + обязательный eval. **Самый ценный пользовательский анлок.** | `agent/prompts`, eval golden-кейсы. M (Fable) |
| F2 | 🟡 S2 | **DataLens B1/B3** — default top-N для high-card без хинта (нормализация после propose) + джойн id→имя в propose. Промпт + eval. | план dashboard-adequacy §B1/B3 |
| F3 | 🟡 S2 | **v2 LLM-ранжирование** заголовков/порядка авто-обзора — детерминированный v1 самодостаточен; только по запросу. | `autospec` + LLM-слой |
| F4 | 🟡 S2 | **LLM-формулировка инсайтов** (D9-нарратив поверх посчитанных кодом фактов) — детерминированная проза самодостаточна; по запросу. | `agent/insights.py` + LLM |

## Трек G — cont.8 (непринятый дашборд)

| ID | Гейт | Что |
|----|------|-----|
| G1 | 🟣 ВЛАДЕЛЕЦ | **cont.8** (`wip/cont8-dashboard-heatmap`, `178135b`, НЕ запушено) — разнообразие графиков + heatmap, Юля визуально НЕ приняла. Решение: **выбросить** (`git branch -D wip/cont8-dashboard-heatmap`) · **оставить запаркованным** · **доделать** (тогда определить, что = «качественно», и ревью по скринам). Автономно НЕ трогаю (память: автономия ≠ пуш забракованного). |

## Трек H — Тесты / качество / hardening

| ID | Гейт | Что | Точки входа · оценка |
|----|------|-----|----------------------|
| H1 | ✅ готово | **Флейк `test_demo_golden_path` под `--cov` захарднен (cont.13).** Замер: НЕ воспроизводится (1.4с под `--cov` vs 1.8с без — subprocess не наследует COV-env). Изоляция была случайной (этот pytest-cov не форвардит `COVERAGE_PROCESS_START`); сделана **явной**: `env` подпроцесса вычищает `COVERAGE_PROCESS_START`+`COV_CORE_*` → smoke быстр под ЛЮБОЙ версией pytest-cov, исторический ~28×-slowdown вернуться не может. | `tests/test_demo_golden_path.py`. |
| H2 | 🟢 АВТО | **Coverage-добор** при реальных пробелах (cli/адаптеры) — не добивать числом ради числа. | `tests/`. S |

---

## Рекомендованная последовательность

**Если «продолжай автономно» (Opus/auto):** 🟢-БЭКЛОГ ПО СУТИ ИСЧЕРПАН (cont.12–13).
- ~~**E2 token-учёт по Anthropic-пути**~~ — ✅ **cont.12** (`6a19002`), gap observability закрыт.
- ~~**H1 флейк-харден**~~ — ✅ **cont.13**: не воспроизводится (1.4с под `--cov`), изоляция сделана явной.
- ~~**B1 yoy-KPI**~~ — пере-классифицирован в 🟠 **S4/IR** (cont.13): big_number+yoy невыразим без правки контракта → владелец.
- ~~**B2 eval-кейс**~~ — ✅ **уже покрыто** (cont.13): `test_autospec.py` фиксирует spec+валидность+идемпотентность.
- **D1** новое advisor-правило — ТОЛЬКО под конкретный реальный кейс (не плодить false-positive), сейчас кейса нет.
- **B3** percent-aware нарратив yoy / **B4** волатильность — под РЕАЛЬНЫЙ запрос, не автономный padding (B3 частично реверсит cont.11-решение).

> **Вывод (cont.13): чистая автономная code-линия исчерпана.** Оставшаяся ценность —
> владелец (S4-ядро A2–A5, B1-скаляр, cont.8), стенд (C-адаптеры, B5), S2 (F1 трио в text-first,
> главный пользовательский анлок), наружу (E1 деплой). Автономно без нового запроса брать нечего.

**Если открывается стенд:** C4 Luxms (наибольший продуктовый прирост) → C2 DataLens B2 → B5 преднастроенный период → C1 percent-перехват.

**Если Fable/ручная сессия (S2):** F1 трио в text-first (главный пользовательский анлок) → F2 DataLens B1/B3.

**Стратегически (владелец):** E1 деплой публичного демо (PMF) → A2–A5 углубление ядра по «предложи варианты» → C6 новые движки.

## Принципы (не нарушать)

- **Универсальность** (`autobi-universal-not-retail`): примитивы domain-neutral; НЕ зашивать
  «продажи»/«средний чек»; демо `sales_daily` = витрина для verify, не предмет продукта.
- **Дашборд ≠ презентация** (`dashboard-not-presentation`): нарратив — отдельным слоем, не
  внутри дашборда.
- **Live-verify IR-числовых правок** на CH перед merge (дисциплина проекта: ловила реальные
  CH-only баги — alias-shadow, Decimal-truncation, lagInFrame-NULL).
- **Гейт-дисциплина:** S2 → eval+Fable; S4 → владелец; стенд-правки → render-verify; забракованное
  не пушить.
