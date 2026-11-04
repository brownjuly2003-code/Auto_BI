# ADR 0008: первый внешний источник формул метрик — dbt `semantic_manifest.json`

**Статус:** Accepted (ревью дизайна Astra xhigh по [ADR 0006](0006-modelled-review-development-gate.md):
первое — P1 и семь P2, исправлены; повторное по правкам — без P1 и P2)
**Дата:** 2026-11-04
**Основание:** решение владельца 2026-11-04 открыть один этап — внешний источник формул;
открытый вопрос [ARCHITECTURE §18](../DECISIONOPS_ARCHITECTURE.md#18-открытые-продуктовые-решения)
(прежде п. 2);
[ADR 0003](0003-decisionops-additive-control-plane.md), п. 10 (внешние источники — через
loss-aware, version-pinned adapters); проверка S0 (ниже).

## Контекст

Формула `external_ref` («определение живёт во внешнем источнике, ревизия закреплена») есть в
`MetricContract`, но gate её не читает: snapshot даёт `gap.opaque_metric_formula` (review),
impact — `chart.formula_untyped`, reference SQL для такой метрики не строится
(`auto_bi/verification/sql_contract.py`, `_OPAQUE`). Для команды, у которой метрики живут в dbt,
почти каждый ответ gate — review, а изменение агрегата или колонки в dbt не доходит до плитки.

`dbt parse` в dbt-core 1.12.6 пишет два артефакта семантического слоя: `semantic_manifest.json`
(типизированная модель MetricFlow) и `osi_document.json` (документ Apache Ossie, конвертер
`metricflow/converters/msi_to_osi.py`). Выбрать нужно один из них.

**Проверка S0 (2026-11-04, Mac).** dbt-core 1.12.6, dbt-postgres 1.11.0, metricflow 0.213.0
(`dsi_package_version` артефакта — 0.213.0), OSI 0.1.1; `dbt parse` без БД. Два dbt-проекта повторяют pilot-витрину
`dm.sales_daily` (`docs/pilot/2026-08-25-ch-tz-margin/base`):

- legacy (`semantic_models` + `measures`, метрика ссылается на measure) — восемь метрик:
  `revenue`, `gross_margin_total`, ratio `gross_margin_pct`, `active_stores`
  (count_distinct), `cost_of_sales` (выражение), `revenue_cum` (cumulative без окна),
  `revenue_north` (фильтр), `margin_derived` (derived);
- V2 (`semantic_model: true` у модели, агрегат и выражение прямо в метрике) — те же, кроме
  `margin_derived`, а у `revenue_cum` окно 7 дней.

Conversion- и private-метрик и natural entities в корпусе нет. Агрегаты в нём — только `sum` и
`count_distinct`; у всех measures `expr` задан. Числа S0 не считал — только разбор артефактов.
Артефакты станут fixtures первого среза.

## Решение

1. **Источник — `semantic_manifest.json`**, оба диалекта. `osi_document.json` gate как
   источник формул не читает (почему — ниже).
2. **Роль — resolver формул, не источник схемы.** Таблицы, поля, joins, grain, event time,
   dimensions, additivity, unit, budgets по-прежнему задают `model.yaml` и native metric
   contracts. Из dbt берётся только формула контракта с `formula.kind: external_ref` и
   `external.source: dbt_semantic_manifest`; `external.ref` — имя метрики dbt. Модели,
   колонки и метрики dbt, которых нет в `model.yaml`, в граф не добавляются — та же политика,
   что у `semantic/dbt_import.py`.
3. **Совместимость с контрактом проверяется до разрешения.** Известное противоречие — block,
   недоказанное соответствие — review без типизированной формулы:
   - **Relation.** Native-таблица называется `schema.table` без database, dbt-relation —
     `database`, `schema_name`, `alias`. Вместе с путём к артефакту объявляется ожидаемая
     dbt-`database` (пустая у движков с двухчастными именами). Отбросить database — не
     доказательство: другая database — block `gap.external_relation_mismatch`, не
     объявленная при непустой database в relation — review `gap.external_relation_unproven`.
     `schema_name.alias`, не равный `grain.table` контракта, — block
     `gap.external_relation_mismatch`.
   - **Время.** Действующий `agg_time_dimension` (V2 — `metric_aggregation_params`; legacy —
     у measure, иначе `defaults` semantic model) ведёт к dimension, а та — к колонке (`expr`
     dimension, без него — её имя). Колонка, не равная `time.event_time_column`, — block
     `gap.external_time_mismatch`. `expr` dimension не из одной колонки или гранулярность не
     `day` — review: контракт гранулярность не объявляет, совпадение не доказать.
   - **Ratio.** Входы из разных semantic models или с разным временем агрегации — review:
     `typed_ratio` с одной grain-таблицей их не выражает.
   - **Additivity.** Агрегат, который не складывается суммой (`count_distinct`, `min`, `max`,
     `average`), под `additivity: additive` — review `gap.external_additivity_unproven`: ни
     принять, ни отвергнуть это сочетание автоматически нельзя.
   - **Private.** Метрика контракта с `is_private: true` — review. Private-вход публичного
     ratio допустим и входит в замыкание. Private здесь — видимость метрики в dbt, не
     классификация данных.
4. **Пин.** `external.source_revision` для этого источника — `sha256:<hex>` канонического JSON
   замыкания определения метрики: версия схемы, `project_configuration` целиком (в нём
   `time_spines`), сама метрика, её входные метрики по всей глубине, measures и semantic
   models, которые они затрагивают. Объекты берутся целиком, `null` и значения по умолчанию —
   как в артефакте; метрики и semantic models в замыкании упорядочены по имени. Gate считает
   digest сам и сравнивает с пином; несовпадение — block `gap.external_revision_mismatch` с
   обоими digest. Правка вне включённых объектов пин не сдвигает; правка внутри включённого
   объекта сдвигает, даже если она касается другой метрики (например, чужой measure той же
   legacy semantic model). Тег или коммит dbt-проекта как пин этого источника не принимается:
   по артефакту его не проверить. Значение для пина печатает CLI. Digest байтов всего
   артефакта — отдельная величина, он входит в source digest snapshot.
5. **Канонизация.** Замыкание канонизирует `auto_bi/evidence/canonical.py` (RFC 8785) с
   `allow_float=True`: это внешний JSON, float в нём законен. До канонизации reader отвергает
   повторяющиеся ключи, `NaN`/`Infinity` и целые за пределами `2**53 - 1` — это block
   `gap.external_artifact_invalid`, а не молчаливое приведение к double.
6. **Версия схемы.** `project_configuration.dsi_package_version` (`major_version`,
   `minor_version`, `patch_version`) сверяется с явным списком проверенных версий; первая —
   0.213.0. Это маркер совместимости пакета, а не гарантия стабильной схемы. Другая версия —
   block `gap.external_schema_unsupported` без попытки разбора; он касается формул, которые
   ссылаются на этот артефакт, а не native-формул. Новая версия добавляется отдельной задачей с
   contract-тестами на артефактах этой версии dbt, как версии стендов в инварианте 7.
7. **Поддерживаемое подмножество.** Кандидаты на типизированную формулу:
   - simple-метрика без фильтра, агрегат `sum`, `count`, `count_distinct`, `min`, `max`,
     `average` (→ `avg`), `agg_params` по умолчанию, без `non_additive_dimension`,
     `join_to_timespine: false`, без `fill_nulls_with`, выражение — голое имя колонки
     (legacy — `expr` measure) → `typed_aggregate`;
   - ratio, где числитель и знаменатель — такие simple-метрики, а входы без `filter`, `alias`,
     `offset_window`, `offset_to_grain` → `typed_ratio`.

   Гарантия — структурный перевод: агрегат, колонка, таблица, числитель и знаменатель. Что
   MetricFlow и reference SQL контракта дают одно число, проверяет матрица паритета (S2): на
   одних строках сравниваются nullable-колонка под `count`, пустые и целиком NULL группы, нулевой
   и NULL знаменатель, даты без строк (time spine). Агрегат или форма ratio получают
   типизированную формулу только после своей строки матрицы; до того — review. Точность
   `unit.precision` — свойство native-контракта, матрица сравнивает значения до неё. Ratio
   наследует ограничения reference SQL: вход, для которого он даёт advisory
   `sql.ratio_inexact_term` (avg, float), остаётся с этим advisory. Measure без `expr` — review,
   пока fixture не покажет, что dbt пишет для него в артефакт.

   Результат проходит те же проверки, что native typed formula: валидатор `MetricFormula` и
   проверки узла (`gap.metric_field_missing` для колонки или таблицы, которых нет в
   `model.yaml`).
8. **Всё остальное — именованный гэп, а не тихий проход.** Выражение не из одной колонки,
   фильтр метрики или входа, cumulative, derived, conversion, percentile/median/sum_boolean,
   `non_additive_dimension`, offset, alias, `fill_nulls_with`, `join_to_timespine: true`
   дают review `gap.external_construct_unsupported` с путём конструкции; формула узла остаётся
   `external_ref`, как сейчас. Severity review — та же, что у сегодняшнего opaque external
   formula: этап не делает ни один ответ хуже текущего и не превращает непонятое в ok.
   Block: нет артефакта при контракте, который на него ссылается
   (`gap.external_source_missing`); артефакт не читается, не соответствует своей версии схемы,
   держит два объекта с одним именем или ссылку на отсутствующий объект
   (`gap.external_artifact_invalid`); метрики `ref` в нём нет (`gap.external_ref_missing`);
   relation, время, пин и версия — п. 3, 4, 6.
9. **Без ложной эквивалентности.** Узел метрики несёт binding: source, ref, revision и digest
   замыкания. Любое изменение определения в dbt — поддержанное или нет — сдвигает canonical
   digest, и diff его называет. Неподдержанная конструкция никогда не сводится к типизированной
   формуле, которая её не видит: это ровно та ошибка, которую S0 нашёл в OSI.
10. **Snapshot: объявление отдельно от разрешения.** Разрешённая формула кладётся в
    `MetricNode.formula`: impact (`auto_bi/decision/impact.py`) и reference SQL
    (`sql_contract.py`) читают формулу из узла, поэтому типизированный путь работает для них
    без отдельной ветки. Объявление контракта (`external_ref` с пином) хранится в binding узла.
    Сверка snapshot с validated-контрактом (`release_gate.py`, вызовы `metric_node(contract)`)
    для связанной метрики сравнивает объявление и пин, а не разрешённую формулу; остальные
    свойства и исключение для base — как сейчас. Иначе одинаковые base и candidate давали бы
    ложный block `metric_ref.candidate_content_mismatch`. Форма графа меняется:
    `NATIVE_SOURCE_VERSION` 3 → 4, версии verifier и check поднимаются в срезе, который меняет
    граф.
11. **Capture, verify и evidence.** Capture и verify загружают артефакт одним загрузчиком;
    CLI требует его для обеих команд, если контракты на него ссылаются. Расхождение `canonical_digest`
    кандидатного графа уже даёт review `provenance.other_snapshot`; расхождение только
    source digest при том же графе эта проверка не видит. Evidence хранит по каждой
    стороне каноническое замыкание каждой связанной метрики, а не весь артефакт: тот описывает
    весь dbt-проект. Verifier проверяет целостность и то, что digest сохранённого замыкания
    равен digest в binding сохранённого snapshot. Повторный вывод всего snapshot из исходников
    offline не обещается — как и для native сегодня: verifier проверяет наличие, hash и размер
    артефактов, а snapshot не пересобирает.

## Почему не `osi_document.json`

S0 на тех же проектах:

- **Ложная эквивалентность.** `revenue_cum` превращается в `SUM(sales_daily.revenue)` — то же
  выражение, что у `revenue`: в V2 с окном 7 дней, в legacy без окна. Появление или смена окна
  gate не увидел бы. Конвертер заводит на эту потерю `ConverterIssue`
  `CUMULATIVE_SEMANTICS_LOSS` (исходник `metricflow/converters/msi_to_osi.py` пакета
  metricflow 0.213.0, строки 96–98), а в документ она не попадает.
- **Фильтр ссылается на несуществующую колонку:** `revenue_north` даёт
  `SUM(CASE WHEN sale__region = 'north' …)`, а `sale__region` — имя dimension MetricFlow, не
  колонка `dm.sales_daily`.
- **Выражение теряет таблицу:** `cost_of_sales` → `SUM(revenue - gross_margin)` без префикса.
- **Метрика — строка SQL, а не тип.** Агрегат и колонку пришлось бы угадывать по тексту.
- **Потери не видны в документе.** По исходнику конвертер выбрасывает conversion- и
  private-метрики (тот же файл, строки 86–95) и natural entities (строки 360–365) с
  `ConverterIssue` `*_DROPPED`; в корпусе S0 таких нет.
  Сам документ держит только `version`, `dialects` и `semantic_model` и о потерях ничего не
  говорит.

`semantic_manifest.json` хранит агрегат, выражение, ссылки ratio, окно cumulative и шаблон
фильтра отдельными полями и несёт версию своей схемы. Ossie остаётся направлением из §7.4:
отдельный adapter и отдельный ADR, когда спецификация и конвертер перестанут терять смысл
метрики.

## Последствия

- Вопрос из §18 ARCHITECTURE закрыт этим ADR и убран из списка; §7.4 и §6 ссылаются на него.
- Этап режется на срезы:
  - S1 — строгий reader (оба диалекта, список версий, повторяющиеся ключи и имена, числа,
    битые ссылки), канонизация и замыкание; fixtures из S0 и новые — `count`, `min`, `max`,
    `average`, measure без `expr`, private, conversion;
  - S2 — разрешение поддержанного и гэпы неподдержанного вместе, проверки совместимости
    (п. 3), матрица паритета на Mac;
  - S3 — binding в узле, сверка с validated-контрактом по объявлению, diff и инвалидация
    verified assets, версии source, verifier и check, совместимость сериализации;
  - S4 — CLI (артефакт и database каждой стороны, печать пина), capture и verify, замыкания в
    evidence, pilot-сценарий;
  - S5 — расширение корпуса, инвентарь неподдержанных конструкций, финальное ревью.
- Каждый апгрейд dbt со сменой `dsi_package_version` блокирует внешние формулы, пока версию не
  добавят в список. Это цена отказа от угадывания.
- Пин по объектам целиком консервативен: правка включённой semantic model или
  `project_configuration`, не меняющая число (например, новое описание dimension), тоже
  потребует перепина. Сужение замыкания — только с тестом, что исключённое поле число не
  меняет.
- dbt и MetricFlow gate не запускает: он читает уже собранный артефакт. Jinja-шаблоны
  фильтров и SQL-выражения не разбираются.
- Native-формулы, CLI без артефакта и уже записанные evidence не меняются.
