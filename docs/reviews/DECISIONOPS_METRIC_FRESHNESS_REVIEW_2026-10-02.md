# DecisionOps — объявленная freshness метрики в canonical snapshot и semantic diff: проверки и границы

**Дата:** 2026-10-02
**Срез:** T-36, verifier 81 / check 57
**Ревьюер:** `gpt-6-astra` xhigh через MyFlow (synthetic review по ADR 0006)
**Итог:** F-T-36-1 (проход 1: окно длиннее предела `timedelta` роняло gate)
и F-T-36-3 (проход 2: компонент длиннее предела одного вызова `int()`) воспроизведены
и исправлены. Проход 2 подтвердил устранение F-T-36-1 воспроизведением через
`run_gate`, проход 3 — исправление F-T-36-3 прогоном модуля среза (48 passed);
новых findings третий проход не дал.

## Что было

§7.3 [архитектуры](../DECISIONOPS_ARCHITECTURE.md) относит freshness и data-quality
preconditions к минимальной части metric contract, которая должна дожить до canonical
form, а принцип 6 требует явного gap для семантики, которую canonical graph не несёт.
Metric node canonical graph оставлял `freshness` контракта без gap. На HEAD `1b04c8c`
(offline, gate в памяти, fixture-продукт с валидным дашбордом) ослабление `max_age`
с `PT6H` до `P7D`, ужесточение `P7D` → `PT6H` и снятие precondition `dq.orders_loaded`
давали прежний canonical digest. `semantic.diff` проходил с `semantic.unchanged`
и «0 semantic change(s) between the snapshots», вердикт — `pass_with_advisory`.
Правку, после которой потребитель метрики под обещанием шести часов получает недельные
числа, гейт не видел.

## Что изменилось

Metric node несёт `freshness`: `max_age` как объявлен, quality preconditions
отсортированы без повторов, поэтому их порядок и повторы в контракте digest не двигают.
Native projection version 1 → 2: canonical digest каждого native snapshot с метриками
сдвинулся, snapshot без метрик сохраняет digest.

`semantic.diff` сравнивает freshness метрики, которая есть в обоих snapshots,
по направлению — как сдвинутый потолок стоимости:

- `metric.freshness_relaxed` (review) — окно длиннее (оба окна фиксированной длины,
  сравнение в точных целых секундах), окна разной записи нельзя упорядочить
  (годы или месяцы), снят precondition;
- `metric.freshness_tightened` (advisory) — окно короче, добавлен precondition;
- `metric.freshness_restated` (advisory) — то же окно другой записью (`PT6H` и `PT360M`).

Одна правка может дать и relaxed, и tightened; detail называет каждое перемещение
со старым и новым значением. Ослабление (review) достигает verified assets метрики как
`asset.metric_changed` прежним правилом, как и ослабленный потолок стоимости.
`dwh.freshness` по-прежнему грейдит витрины против `freshness_max_age` продукта.

Окна фиксированной длины сравниваются в точных целых секундах (F-T-36-1).
`duration_seconds` считал через `timedelta`, который не вмещает больше 999 999 999 дней:
допустимый `max_age: P1000000000D` поднимал `OverflowError` в `semantic.diff`
и `run_gate`. Тот же разбор окна и до среза обслуживал `dwh.freshness` и правило
stale-capture в provenance для `freshness_max_age` продукта, и они падали так же.
Теперь `duration_seconds` возвращает точное целое число секунд: приём `Duration`
прежний, значения обычных окон прежние, а такие входы получают оценку вместо аварии.
Float задачу не решал бы: за пределом `2^53` секунд он теряет отдельные секунды,
а дальше сливает и окна с разницей в день — `P1000000000000000000D`
и `P1000000000000000001D` стали бы одним числом, и diff назвал бы более длинное окно
«тем же».

Компонент окна может быть записан большим числом цифр, чем Python превращает в `int`
за один вызов: 4300 по умолчанию и не меньше 640, если лимит понижен (F-T-36-3).
`PT`, 4300 нулей и `6H` — допустимая запись шести часов, а разбор поднимал `ValueError`
в diff, gate, `dwh.freshness` и provenance. Теперь цифры разбираются делением пополам
на блоки не длиннее 600 цифр, точно и без глобальной настройки интерпретатора.

Требования и сценарии — в durable spec
[`docs/specs/metric-freshness-snapshot.md`](../specs/metric-freshness-snapshot.md).

## Проверки

- Red на HEAD: 13 из 15 исходных тестов `tests/test_metric_freshness_snapshot.py`
  падали; проходили два контроля — неизменная freshness в diff и valid control
  на уровне gate.
- F-T-36-1: с HEAD-версией `duration_seconds`, подменённой во всех модулях-потребителях,
  падают 14 из 17 новых тестов ремонта; проходят три контроля `None` для окон с годами
  и месяцами, исходные 15 тестов тоже проходят.
- F-T-36-3: с версией после прохода 1 (точные целые, один вызов `int()` на компонент)
  падают 11 новых тестов, включая разбор при лимите, пониженном до 640 цифр; пять тестов
  границ блоков (599–1201 цифра) проходят и на ней — это контроли разбиения. После правки
  модуль среза — 48 passed.
- Проба сравнила `_build_graph` на HEAD и в рабочем дереве: у snapshot без метрик digest
  прежний, у snapshot с метрикой — сдвинут.
- Профильный набор задачи: 363 passed; тесты environment и provenance вместе
  с модулем среза: 187 passed. Black, Ruff и strict mypy чистые.
- Полный offline pytest с duckdb: 4023 passed, 4 skipped, 38 deselected, exit 0.
- Существующие тесты не менялись.

## Границы

- Verified assets и result attestations, привязанные к digest snapshot с метриками,
  получают `asset.stale_snapshot` / `provenance.other_snapshot` (review) до повторной
  верификации. Исторические bundles не переписаны.
- Окна разной записи, которые нельзя упорядочить, всегда считаются ослаблением, даже
  когда по смыслу окно короче (`P1Y` → `P1M`): гейт не выбирает календарь.
- Сравнивается freshness метрики, которая есть в обоих snapshots; добавленная или
  удалённая метрика сохраняет свои коды.
- `dwh.freshness` окно метрики к данным не применяет; изменился только счёт секунд
  объявленного окна.
- Разбор очень длинной записи растёт быстрее линейного: 100 000 цифр — около 0,1 с,
  миллион — около 4 с.
- `auto_bi/investigation/skills.py` разбирает окно продукта своим парсером с тем же
  пределом одного вызова `int()`. Это путь investigation, не release gate; срез его
  не трогал.
- BI и DWH не запускались: проверка offline.
- Реализация — `gpt-6.1-sol` medium через MyFlow `sol-high`. Это моделированное ревью,
  не human evidence; human/production acceptance не заявляется.
