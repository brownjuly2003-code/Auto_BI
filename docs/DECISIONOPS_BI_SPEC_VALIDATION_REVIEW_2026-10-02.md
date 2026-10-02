# DecisionOps — валидация поставляемого spec перед `bi.contract`: проверки и границы

**Дата:** 2026-10-02
**Срез:** T-35, verifier 80 / check 56
**Ревьюер:** `gpt-6-astra` xhigh через MyFlow (synthetic review по ADR 0006)
**Итог:** 0 findings с первого прохода

## Что было

`bi.contract` компилировал каждый поставляемый дашборд в payload'ы его BI (§8.5), но spec
перед этим не валидировал. Build path так не делает: `auto_bi/agent/pipeline.py` вызывает
`validate_spec` до адаптера (инвариант 2), а операторский SQL допускает только как один
SELECT (инвариант 3). На HEAD `b3c5b3a` (offline, gate в памяти, fixture-продукт с одним
дашбордом) chart `kpi` с мерой `SUM(ghost_revenue)` на `dm.sales_daily` компилировался для
Superset и для DataLens, и `bi.contract` давал pass с `bi.compiled` — как валидный
`SUM(revenue)`. Так же проходили неизвестная таблица `dm.ghost`, неизвестное измерение
`ghost_region`, операторский `raw_sql`, который SELECT-only guard отвергает, и операторский
`raw_sql` на DataLens. Фильтр дашборда на необъявленной колонке `dm.sales_daily.ghost` давал
только advisory `bi.filter_unwired`. Релиз выпустил бы дашборд, который build path собрать
отказывается, а gate сообщал, что он скомпилирован.

## Что изменилось

Перед компиляцией `bi.contract` валидирует каждый поставляемый spec против candidate-модели
тем же `validate_spec`, что и build path, с разрешённым операторским `raw_sql`. Spec
с ошибками не компилируется: компилятор не вызывается, payload'ов нет. Проверка — block
с `bi.spec_invalid`; evidence получил список `invalid` (ref дашборда и ошибки валидатора
дословно, в порядке поставки, пустой при отсутствии таких дашбордов), detail называет
невалидные refs. Приоритет кода: `bi.spec_invalid`, затем `bi.spec_missing`, затем
`bi.compiled`. Валидные дашборды компилируются как прежде. Валидатор, компиляторы,
`_compile`, запись payload'ов и `impact.products` не менялись.

Требования и сценарии — в durable spec
[`docs/specs/bi-contract-validation.md`](specs/bi-contract-validation.md).

## Проверки

- Red на HEAD: 20 новых тестов `tests/test_bi_contract_validation.py` падали. Двенадцать
  невалидных случаев — на статусе (pass, advisory или review вместо block), восемь контролей —
  только на новом ключе `invalid` в evidence. После правки — 20 passed.
- Тексты ошибок валидатора закреплены по каждому сценарию durable spec; подмена `_compile`
  показывает, что для невалидного spec компилятор не вызывается.
- Колонка, которую удаляет candidate-модель, блокирует: валидация идёт по candidate-модели,
  не по base.
- Digests payload'ов валидного дашборда для Superset и DataLens совпадают с HEAD `b3c5b3a`,
  в том числе когда рядом поставляется невалидный дашборд.
- Повторный прогон на тех же входах даёт побайтно тот же evidence `bi.contract`.
- Существующие тесты не менялись. Два теста `tests/test_decision_verify.py` с операторским
  SQL `this is not SQL` проверяют `impact.products` (`chart.opaque_sql`) и остались зелёными.
- Профильный набор задачи: 310 passed. Black, Ruff и strict mypy (120 модулей) чистые.
- Полный offline pytest с duckdb: 3975 passed, 4 skipped, 38 deselected, exit 0.

## Границы

- Операторский `raw_sql` разбирается в диалекте ClickHouse — так же, как в build path
  (`validate_spec` → `guard_sql`) и в `impact.products`. SELECT с синтаксисом только
  PostgreSQL, который ClickHouse-парсер не принимает, теперь блокирует `bi.contract`,
  как build path отказался бы его собирать.
- Правила валидатора не менялись и попадают в evidence как есть, включая подсказку
  для голого имени колонки фильтра.
- Остальные проверки, `impact.products` включительно, читают поставляемые дашборды как
  прежде; невалидный spec по-прежнему участвует в оценке влияния.
- Исторические bundles не переписаны.
- BI и DWH не запускались: проверка offline, compile seam BI не трогает.
- Реализация — `gpt-6.1-sol` medium через MyFlow `sol-high`, тексты ошибок в тестах
  закрепил оркестратор. Это моделированное ревью, не human evidence; human/production
  acceptance не заявляется.
