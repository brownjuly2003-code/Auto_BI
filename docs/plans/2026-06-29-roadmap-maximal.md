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

**Чистая автономная code-линия АВТО-ОБЗОРА исчерпана.** Остаются автономные кандидаты в
ДРУГИХ треках (observability, eval, hardening) — ниже помечены 🟢.

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
| A2 | 🟠 S4/IR | **Лаг на произвольные N периодов** (обобщение yoy: «vs N периодов назад»). Расширяет `MeasureTransform`/новый параметр. | `ir/spec.py`, `agent/sqlgen._window_expr`; verify: DuckDB + CH live. M |
| A3 | 🟠 S4/IR | **Кумулятивная доля / Pareto** (running-share для ABC-анализа) — новый transform или композиция running+share. | `ir/spec.py`, `sqlgen`; verify: CH live. M |
| A4 | 🟠 S4/IR | **Гистограмма/распределение** (бакетинг меры в корзины) — новый viz + bin-логика. | `ir/spec.py` (Viz), `sqlgen`, оба адаптера. L |
| A5 | 🟠 S4/IR | **Когортный/retention** анализ — самый сложный, доменно-чувствительный. | новый модуль; verify: стенд. L |

> Все A2–A5 = изменение контракта → НЕ автономно. Брать только по «предложи варианты» с
> владельцем (как трио cont.9). Приоритет низкий — ядро уже покрывает 90% паттернов.

## Трек B — Авто-обзор (`autospec`) + insight-слой (display-only)

| ID | Гейт | Что | Точки входа · verify · оценка |
|----|------|-----|-------------------------------|
| B1 | 🟢 АВТО | **yoy-KPI** «итог за период vs прошлый год» — big_number с одним числом «+X% г/г» рядом с KPI меры (комплемент yoy-линии; срабатывает при ≥2 годах). | `autospec` (P1.5); verify: offline + CH live (есть скрипт). S |
| B2 | 🟢 АВТО | **eval-кейс авто-обзора** — детерминированный golden (spec из `build_auto_spec` стабилен) ИЛИ зафиксировать, что unit+offline+live достаточно. Закрывает остаток аудита. | `auto_bi/eval/`, `tests/test_autospec.py`. S |
| B3 | 🟢 АВТО | **Percent-aware нарратив yoy-линии** в insight-слое — вместо текущего skip давать «год-к-году ускоряется/замедляется» с корректным %-форматом (новый kind или percent-ветка `_observe_line`). Реальная фича, НЕ padding. | `agent/insights.py`; +offline-тесты. M |
| B4 | 🟢 АВТО (low) | **Волатильность** (CV детрендир. остатков) как наблюдение — низкий headline-приоритет, рискует отрезаться cap'ом. Брать только если B1–B3 закрыты и есть запрос. | `agent/insights.py`. S |
| B5 | 🔵 СТЕНД | **Преднастроенный период** обзора (last 90d по умолчанию) — нужен реверс `defaultDataMask` Superset / DataLens на живом стенде. | `adapters/.../native_filters.py`, `autospec`. M |

> B1–B3 = единственные оставшиеся 🟢 автономные code-инкременты с реальной ценностью.
> Рекомендованный порядок если «продолжай автономно»: **B1 → B2 → B3**.

## Трек C — BI-адаптеры

| ID | Гейт | Что | Заметка |
|----|------|-----|---------|
| C1 | 🔵 СТЕНД (hard) | **DataLens percent на оси** — известное ограничение движка (charts-engine не применяет percent к рендеру оси; числа верны, Superset % работает). Нужен сетевой перехват chart-config payload на стенде. **НЕ реверсить заново вслепую** (память `autobi-bi-engine-limits`). | план §6.3 derived-metrics |
| C2 | 🔵 СТЕНД | **DataLens B2** — форс категориальной оси числового измерения (Superset решает через `xAxisForceCategorical`; DataLens — нет). Реверс механизма на стенде. | `adapters/datalens/chart_config.py` |
| C3 | 🟣/✋ | **DataLens B4** — косметика осей (шрифт/локаль/SI-суффикс «B/M» vs «млрд») = тема/локаль инстанса движка, **НЕ наш код**. Преимущественно non-actionable. | память `autobi-bi-engine-limits` |
| C4 | 🔵 СТЕНД | **Luxms-адаптер** — GO-with-stand (полный REST/CRUD source→cube→dashlet→dashboard, JWT, нативный CH). Реализация по зеркалу DataLens-трека. **Gate:** демо-креды `sandbox.demo.luxmsbi.com` ИЛИ self-hosted Docker-стенд на Mac. | `docs/plans/2026-06-14-luxms-adapter-plan.md`. L |
| C5 | 🔵 ЛИЦЕНЗИЯ | **Visiology-адаптер** — NO-GO автономно (нет public REST для авторинга, только UI-Designer). **Gate:** лицензионный стенд v3 от заказчика. | `docs/plans/2026-06-14-visiology-spike.md` |
| C6 | 🟣 ВЛАДЕЛЕЦ | **Новый BI-движок** (Metabase / Apache Superset Cloud / …) — по запросу, через фабрику `adapters/factory`. | — |

## Трек D — Advisor / Feasibility (ров)

| ID | Гейт | Что | Заметка |
|----|------|-----|---------|
| D1 | 🟢 АВТО | **Новые advisor-правила** — детерминированные анти-паттерны + remediation + eval-кейсы (тем же паттерном, что projection/денорм/distribution). Брать конкретное правило под реальный кейс, НЕ плодить false-positive (`point_lookup_pattern` — осознанный non-goal: «нет реального кейса для тюнинга»). | `advisor/findings.py`, `advisor/core.py`, eval. M |
| D2 | 🟠 по движку | **Advisor для новых движков** — если добавится DWH/BI. | `advisor/<engine>.py` |

## Трек E — Продукт / деплой / observability

| ID | Гейт | Что | Точки входа · оценка |
|----|------|-----|----------------------|
| E1 | 🔴 НАРУЖУ | **Деплой публичного демо** на синтетике (Render/Fly/VPS) — закрывает PMF-разрыв BCG-аудита (delivery=D). Anthropic-клиент влит (снимает GraceKelly-SPOF) + Dockerfile есть → технически разблокировано. **Gate:** «деплой» + Anthropic-ключ. | `llm/anthropic.py`, Dockerfile. M |
| E2 | 🟢 АВТО | **Token/$-учёт по Anthropic-пути** — `llm/anthropic.py` сейчас пишет только `completion_chars` (size-прокси), хотя Anthropic API возвращает `usage.input_tokens/output_tokens`. Захватить usage из ответа → колонки `llm_calls` (Store v3→v4, идемпотентная миграция как v2) → реальные токены в observability-панели (вместо size-прокси) на Anthropic-провайдере. GraceKelly usage не отдаёт → там остаётся прокси. **Закрывает задокументированный отложенный gap** (ARCHITECTURE §3.9). | `llm/anthropic.py`, `llm/_structured.py`, `store.py`, `api` observability; verify: mock-ответ с usage + offline-тест. M |
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
| H1 | 🟢 АВТО | **Флейк `test_demo_golden_path`** под активным `--cov` на Windows (subprocess-smoke ~28× медленнее, 410s vs 14s; вне cov 551/551; на Linux-CI нет). Захарденить: поднять `timeout`/пометить `no_cover`/вынести smoke. | `tests/test_*demo*`, `pyproject` cov-конфиг. S |
| H2 | 🟢 АВТО | **Coverage-добор** при реальных пробелах (cli/адаптеры) — не добивать числом ради числа. | `tests/`. S |

---

## Рекомендованная последовательность

**Если «продолжай автономно» (Opus/auto), по убыванию ценности:**
1. **E2 token/$-учёт по Anthropic-пути** — закрывает реальный gap observability, детерминир.,
   offline-верифицируемо. Самый весомый из оставшихся 🟢.
2. **B1 yoy-KPI** + **B2 eval-кейс авто-обзора** — мелкие, доводят авто-обзор.
3. **B3 percent-aware нарратив yoy** — реальная фича в insight-слое.
4. **D1** новое advisor-правило — только под конкретный реальный кейс.
5. **H1** флейк-харден.

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
