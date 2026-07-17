# P1-2 — Advisor в auto-пути + effective filters

## Goal

Advisor запускается во всех entrypoints (CLI auto, API auto), а его filter-правила считают
dashboard-level `spec.filters` наравне с `query.filters` — там и только там, где фильтр
реально сужает конкретный чарт.

## Что из аудита подтвердилось, а что устарело (проверено кодом 17.07)

Аудит `audit_gpt_11_07_26.md` §P1-2 писал: «все 8 charts получают `no_filter_on_large_fact`».
**Это устарело**: P1-1 (`agent/autospec.py` §«P1-1: bake the overview period») запекает период
в `query.filters` КАЖДОГО чарта → в auto-пути правило и так молчит. Ложного срабатывания на
auto-обзоре сегодня нет.

Подтвердилось и осталось:

1. **Advisor не вызывается в auto-пути.** `cli.py::_build_auto()` идёт прямо в
   `compile_and_build()`; `agent/machine.py::adopt_spec()` выставляет `verdicts=[]`.
   Докстринг `adopt_spec` при этом утверждает, что «the deterministic findings still show in
   the CLI/offline path» — **неверно**, `_build_auto` их не считает вовсе.
2. **Правила слепы к `spec.filters`.** `advisor/clickhouse.py::RuleContext.filter_columns`
   читает только `query.filters`. Докстринг модуля (стр. 12–15) сам предупреждал: правила
   ложно сработают, «when native dashboard filters land (Phase 2)». Они приземлились —
   `adapters/superset/native_filters.py` и `adapters/datalens/adapter.py`. Условие наступило.

Живой эффект (2): LLM-спека, где период задан ТОЛЬКО дашбордным фильтром, получает
`no_filter_on_large_fact` CRITICAL на чартах, которые на самом деле открываются суженными.

## Ключевое ограничение — фильтр применим НЕ ко всем чартам

Оба адаптера кодируют одно правило (datalens: «mirrors superset.native_filters»):

- фильтр сужает чарт, только если его колонка входит в grain чарта (`query.group_columns()`);
- сужение происходит только при непустом `default` (пустой → нейтральная маска → чарт
  открывается НЕсуженным).

Значит effective filters = `query.filters` + применимые dashboard-фильтры. «Все `spec.filters`
скопом» дало бы ложный негатив вместо ложного позитива — правило обязано быть scope-aware.
Тип фильтра брать по `ColumnRole` модели, а НЕ по `DashboardFilter.type` (ненадёжен —
см. докстринг `native_filters.py`).

## Tasks — 8 из 8

- [x] ~~1. `advisor/effective.py`: `effective_filters(chart, spec, model)`~~ → 8 юнитов
- [x] ~~2. `Advisor.review_chart(chart, spec=None)` прокидывает spec~~ → 52 старых теста зелёные без правок
- [x] ~~3. EXPLAIN по effective query~~ → тест + live: EXPLAIN отработал на всех 8 чартах стенда
- [x] ~~4. Advisor в `cli._build_auto()` через `worst_verdicts()`~~ → live-вывод ниже
- [x] ~~5. Advisor в `machine.adopt_spec()`~~ → 3 теста (падают на старом коде с `ValueError`)
- [x] ~~6. Устаревшие докстринги (`clickhouse.py`, `adopt_spec`)~~
- [x] ~~7. Scope-aware тесты (гасит in-scope / не гасит out-of-scope)~~
- [x] ~~8. **Не планировалось**: `explain_high_scan_fraction` при multi-pass~~ — см. ниже

**Сверх плана (найдено live).** Включённый в auto-путь advisor на первом же прогоне выдал
`query reads ~146% of dm.sales_daily (29132263 of 20000000 rows)`. Не баг счёта: SQL
compare-KPI сканирует таблицу дважды (якорный подзапрос ~9,5M + внешний расширенный скан
~19,5M — расширение окна из `5c06cc1`), а `EXPLAIN ESTIMATE` суммирует проходы. Находка
честная, но «доля таблицы = 146%» читается как сломанное число, и до P1-2 её никто не видел —
advisor в auto-пути не запускался. Выше 1 теперь сообщаются проходы, сырое отношение остаётся
в `evidence`. Пороги правила не трогала.

## Done When

- [x] ~~pytest 805 passed / 18 skipped (было 792), ruff/black/mypy 0~~
- [x] ~~advisor 9/9, replay 37/37 + 16/16~~
- [x] ~~live против CH-стенда (20M строк): auto-сборка печатает вердикт; EXPLAIN не деградировал
      молча ни на одном чарте; ложный full-scan исчез (`[no_filter_on_large_fact,
      explain_high_scan_fraction]` в одиночку → `[]` в составе дашборда)~~

## Совет на compare-KPI — РЕШЕНО (вариант «в»)

`explain_high_scan_fraction` советовал «narrow the time range or add a filter», хотя фильтр
уже стоит, а расширение окна — by design. Решение: **правило продолжает срабатывать, меняется
совет.**

- **Молчать нельзя** — 29M строк на обновление реальная стоимость; молчание = ложный негатив,
  а он хуже ложного позитива, который весь P1-2 и чинит.
- **Правило само масштабируется честно**: на DM с 10 годами истории yoy-KPI прочитает ~20% и
  промолчит. Здесь срабатывает потому, что окно сравнения правда покрывает почти весь стенд.
- **Неверен был только совет.** Для period-compare: второй проход неустраним, честный рычаг —
  предагрегированный rollup в DM. Признак — `Measure.compare` (НЕ `transform`; проверено:
  `_is_period_compare=True` ровно у `auto2` из восьми чартов автоспека).

## Дока — ревизия 17.07

- `ARCHITECTURE.md` §3.3 — effective query + advisor во всех entrypoints
- `CHANGELOG.md` §Unreleased — три записи P1-2
- `USER_GUIDE.md` — секция Advisor в выводе `build --auto`; вердикты в превью авто-пути
- `CLAUDE.md` §Статус — **указатель**, что лог не поддерживается, источник истины =
  `_NEXT_SESSION.md` + CHANGELOG (сам лог не переписывался — решение прошлой сессии)
- **Дрейф нумерации исправлен**: 6 докстрингов advisor'а ссылались на «ARCHITECTURE §3.6»,
  а §3.6 давно = LLM Layer; advisor живёт в §3.3. Ссылка в `config.py` на §3.6 верна
  (там про GraceKelly) — не трогала. `docs/history/` — снимки, не трогала.

## Notes

- Инвариант 5 (advisory-only, сборку не блокирует) не трогаем — Advisor остаётся советующим.
- IR не меняем → S4-стоппер не наступает.
- Не в скоупе (отдельные задачи): объединение evidence Advisor + `LiveSQLValidator` (сейчас два
  EXPLAIN одного SQL) — оптимизация, риск отдельный; eval-кейсы golden-сьюта.
