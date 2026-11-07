# ADR 0008: первый внешний источник формул метрик — dbt `semantic_manifest.json`

**Статус:** Accepted (ревью дизайна Astra xhigh по [ADR 0006](0006-modelled-review-development-gate.md):
первое — P1 и семь P2, исправлены; повторное по правкам — без P1 и P2; ревью поправки п. 7
под паритет ClickHouse, S5c, — P1, пять P2 и P3, исправлены)
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
     `gap.external_time_mismatch`. Review `gap.external_time_unproven`: `agg_time_dimension`
     не задан, dimension не `time`, её `expr` не из одной колонки, гранулярность не `day` или
     в артефакте её нет — контракт гранулярность не объявляет, совпадение не доказать. V2
     берёт время только из `metric_aggregation_params`: `null` там — review, `defaults`
     semantic model не подставляются (как MetricFlow заполняет его для V2, S2 не проверял).
   - **Ratio.** Входы из разных semantic models или с разным временем агрегации — review
     `gap.external_ratio_unproven`: `typed_ratio` с одной grain-таблицей их не выражает.
   - **Additivity.** Агрегат, который не складывается суммой (`count_distinct`, `min`, `max`,
     `average`), и любой ratio под `additivity: additive` — review
     `gap.external_additivity_unproven`: ни принять, ни отвергнуть это сочетание автоматически
     нельзя.
   - **Private.** Метрика контракта с `is_private: true` — review
     `gap.external_private_metric`. Private-вход публичного ratio допустим и входит в
     замыкание. Private здесь — видимость метрики в dbt, не классификация данных.
4. **Пин.** `external.source_revision` для этого источника — `sha256:<hex>` канонического JSON
   замыкания определения метрики: версия схемы, `project_configuration` целиком (в нём
   `time_spines`), сама метрика, её входные метрики по всей глубине, measures и semantic
   models, которые они затрагивают. Объекты берутся целиком, `null` и значения по умолчанию —
   как в артефакте; метрики и semantic models в замыкании упорядочены по имени. Gate считает
   digest сам и сравнивает с пином; несовпадение — block `gap.external_revision_mismatch` с
   обоими digest. Правка вне включённых объектов пин не сдвигает; правка внутри включённого
   объекта сдвигает, даже если она касается другой метрики (например, чужой measure той же
   legacy semantic model). `filter` на метрике замыкания или на любом её входе (measure,
   `input_measures`, numerator/denominator, `metrics[]`, метрика cumulative), как и
   conversion-метрика в замыкании, делает замыканием все метрики и все semantic models
   артефакта: шаблон фильтра может назвать измерения и метрики любой semantic model,
   conversion соединяет через entity, а эти зависимости reader не разбирает. Saved queries в
   замыкание не входят. Пин адресует определение формулы, а не результат произвольного
   grouped-запроса MetricFlow: joins и группировку задаёт native-контракт (п. 2), и правка
   entity другой semantic model пин не сдвигает. Списки внутри объектов идут в порядке
   артефакта: их перестановка сдвигает пин, даже если смысла не меняет. Тег или коммит
   dbt-проекта как пин этого источника не принимается: по артефакту его не проверить.
   Значение для пина печатает CLI. Digest байтов всего артефакта — отдельная величина, он
   входит в source digest snapshot.
5. **Канонизация.** Замыкание канонизирует `auto_bi/evidence/canonical.py` (RFC 8785) с
   `allow_float=True`: это внешний JSON, float в нём законен. До канонизации reader отвергает
   повторяющиеся ключи, `NaN`/`Infinity`, целые за пределами `2**53 - 1`, BOM и строки или
   ключи с одиночным суррогатом — это block `gap.external_artifact_invalid`, а не молчаливое
   приведение. Числа — binary64, как в RFC 8785: `1`, `1.0` и `1e0`, как и `0` и `-0`, дают
   один пин.
6. **Версия схемы.** `project_configuration.dsi_package_version` (`major_version`,
   `minor_version`, `patch_version`) сверяется с явным списком проверенных версий; первая —
   0.213.0. Компоненты — строки из 1–9 цифр; другой компонент или `null` вместо версии —
   `gap.external_artifact_invalid`, отсутствующая версия — pydantic-умолчание 0.0.0, его в
   списке нет. Это маркер совместимости пакета, а не гарантия стабильной схемы. Другая версия —
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

   Колонка legacy-метрики — `expr` measure; у measure без `expr` dbt пишет `expr: null`, и
   колонкой MetricFlow читает имя measure. `expr` legacy-метрики (dbt пишет его для
   `create_metric`) принимается, только если говорит то же, что measure.

   Гарантия — структурный перевод: агрегат, колонка, таблица, числитель и знаменатель. Что
   MetricFlow и reference SQL контракта дают одно число, проверяет матрица паритета (S2, S5c,
   `scripts/metricflow_parity.py`). Строка матрицы — один способ разрешения в своём контексте:
   `<движок>.<событийное время>:<форма>`. Движок — тот, что нативная модель объявляет для
   grain-таблицы; событийное время — класс типа его колонки, если она ключ grain (иначе
   `outside_grain`): MetricFlow группирует по дню времени агрегации, reference SQL — по ключам
   grain, и строки у них одни, только когда колонка — ключ, хранящий дни. Форма — агрегат
   диалекта над классом типа колонки (`legacy.sum.decimal`), ratio из двух таких
   (`ratio.legacy.sum.decimal/legacy.count.integer`), колонка по имени measure
   (`legacy.measure_without_expr`), `expr` метрики (`legacy.metric_expr`). Класс говорит, в чём
   движок считает агрегат над колонкой и что такое день колонки времени. Классы PostgreSQL —
   `decimal`, `integer`, `float`, `date` (имена типов и их псевдонимы — `_PG_CLASSES`).
   Классы ClickHouse — по типу, как его пишет движок в `system.columns`
   (`CLICKHOUSE_CLASSES`): `decimal` — `Decimal(P, 2)` при P ≤ 38, `integer` — `Int8`,
   `Int16`, `Int32`, `Int64`, `float` — `Float64`, `date` — `Date`; `Nullable(T)` колонки
   агрегата — класс `T`. Член класса считает агрегаты матрицы в тех же типах, что проверенный
   тип класса (`Decimal(18, 2)`, `Int64`): `sum` — в `Decimal(38, 2)` и `Int64`, `avg` — во
   `Float64`, `min` и `max` — в типе колонки, и reference SQL ratio читает член как его класс.
   Прочее — `unclassified`: `UInt8` … `UInt64` (`sum` над ними — `UInt64`, а отрицательных
   значений данных они не держат), `Decimal` другого масштаба или с P > 38, синонимы
   `Decimal32(S)` … `Decimal256(S)`, `Int128` и шире, `LowCardinality(…)`, `Float32`, `Date32`,
   `Nullable(Date)` событийного времени, псевдонимы SQL вроде `BIGINT` и написание не так, как
   пишет движок (`Nullable( Int64 )`, `Decimal(18,2)`): reference SQL читает тип по написанию.
   Типы `sum`, `avg`, `min` и `max` над каждым членом классов `decimal`, `integer`, `float` и
   над `UInt8` … `UInt64` прогон записывает. У другого движка классов нет; нет типа —
   `undeclared`. Формула опирается на строки своих термов, формы и способа назвать колонку;
   строка, которой нет в доказанных (`PROVEN_PARITY` в
   `auto_bi/semantic/metricflow_formula.py`), — review `gap.external_parity_unproven`, формулы
   нет. Этот шаг последний: он спрашивается только у формулы без других гэпов.

   Сторона MetricFlow в обоих контекстах — его SQL для PostgreSQL на PostgreSQL: metricflow
   0.213.0 ClickHouse не рендерит (в его `SqlEngine` ClickHouse нет). Reference SQL — в
   диалекте контекста на своём движке, PostgreSQL 16 или ClickHouse 24.8 (версии записаны в
   результате прогона), над теми же строками. Так проверяется перевод определения на общих
   данных, а не рендерер MetricFlow для ClickHouse, которого нет. Строка доказана, если каждая
   построенная для неё метрика (и для строк, на которые та ещё опирается) дала на одних данных
   одни ключи grain и по каждому ключу одно число по правилу метрики: агрегат — равенство;
   `average` в контексте ClickHouse — правило `double`: |reference − MetricFlow| ≤
   |reference|·10⁻¹⁵ + 10⁻³⁰⁰ (`avg` ClickHouse возвращает `Float64` над любой колонкой;
   допуск — правило этого рецепта на его данных, где суммы точны, а не оценка ошибки `avg` на
   любых данных); ratio — double MetricFlow лежит, в пределах ошибки double, среди значений,
   которые при отсечении на `unit.precision` дают значение reference (зеркально ниже нуля).
   Паритет — равенство определений, а не точность double: на 2026-01-04, store 1, reference
   даёт `142857142857.142857`, MetricFlow — `142857142857.14285`, и это совпадение. В данных —
   nullable-колонка под `count`, группы, где колонка целиком NULL, нулевой и NULL знаменатель,
   отрицательные значения, большой числитель, повторы под `count_distinct`, дата без строк
   (time spine не участвует: оба запроса её не дают) и группа, где среднее в double не равно
   точному, у decimal- и integer-колонки. Контрольные пары разных агрегатов и форм обязаны
   разойтись числом — хотя бы на одном ключе, где значение есть у обеих сторон, — и среди них
   `average` колонки против `average` её NOT NULL-двойника: правило `double` не прячет NULL,
   прочитанный как ноль (2026-01-01, store 1: 13.5 против 10.125). Колонки — `numeric(18,2)`,
   `bigint`, `double precision` (на ClickHouse — `Nullable(Decimal(18, 2))`,
   `Nullable(Int64)`, `Nullable(Float64)`), и у каждой есть NOT NULL-двойник с теми же
   значениями, где NULL — ноль: на ClickHouse reference SQL ratio приводит целый числитель к
   `Decimal(38, 0)` или `Nullable(Decimal(38, 0))` по тому, может ли терм быть NULL — `sum`,
   `min` и `max` над nullable-колонкой могут, `count` и `count_distinct` нет, — а класс этого
   не называет. Ratio — над любой парой колонок одной nullability, включая одну и ту же
   колонку. Ratio наследует ограничения reference SQL: терм `average` и float-терм под `sum`,
   `min` или `max` дают advisory `sql.ratio_inexact_term` без SQL, а `count` и
   `count_distinct` над float-колонкой — SQL. Поэтому float-терм ratio в матрице — только
   `count` и `count_distinct`, а формы ratio с `average` в ней есть и не доказаны. Доказано 328
   строк: 164 в контексте `postgres.date`, 164 в `clickhouse.date`.
   Матрицей не проверены Greenplum, событийное время `timestamp`, вне ключей grain или с NULL
   (на PostgreSQL его класс — `date`: тип nullability не называет), колонки `text` и `date`
   под агрегатом. На ClickHouse вне доказательства и значения, которые два движка при одном
   определении считают по-разному: `count(DISTINCT)` над `Float64` различает −0.0 и 0.0
   (PostgreSQL — нет), NaN и бесконечности; значения, которые не помещаются в типы ClickHouse,
   — агрегата (`sum` и `avg` над целым членом копят в `Int64`, `sum` над `Decimal(P, 2)` — в
   `Decimal(38, 2)`), приведения к `Decimal(38, 0)`, частного `divideDecimal` с точностью
   `unit.precision`: ClickHouse там переполняется молча или отказывает, PostgreSQL считает в
   `numeric`. Данные их не держат, а float-суммы данных точны при любом порядке сложения.
   Доказательство — для настроек, которые прогон задаёт и записывает, — значений ClickHouse
   24.8 по умолчанию: `count_distinct_implementation = uniqExact` (иначе `count(DISTINCT)`
   бывает приближённым), `aggregate_functions_null_for_empty = 0`,
   `decimal_check_overflow = 1`, `cast_keep_nullable = 0`; сервер с другими — вне его. V2 в
   матрицу не входит: dbt-core 1.12.6 не пишет гранулярность time dimension V2 (п. 8), и ни
   одна V2-метрика не разрешается. Прогон 2026-11-07 (PostgreSQL 16.15, ClickHouse 24.8.14.39,
   metricflow 0.213.0) и его границы —
   [отчёт](../reviews/DECISIONOPS_METRICFLOW_PARITY_CLICKHOUSE_2026-11-07.md); прогон
   2026-11-04, только PostgreSQL, по прежнему рецепту (его fixtures — в дереве `8fd576c`) —
   [отчёт](../reviews/DECISIONOPS_METRICFLOW_PARITY_CHECKS_2026-11-04.md). Результат с digest
   рецепта (файлы проекта, данные, нативные модели обоих контекстов, grain обеих сторон,
   контракты, правила сравнения и их допуски, мост к MetricFlow, настройки ClickHouse,
   контроли), digest reference SQL каждой метрики в каждом контексте и артефакт — fixtures,
   тесты сверяют с ними `PROVEN_PARITY`, рецепт, записанные типы и настройки, нынешнее
   разрешение и SQL, который компилятор пишет сейчас.

   Результат проходит те же проверки, что native typed formula: валидатор `MetricFormula`
   (отказ — review `gap.external_construct_unsupported` на пути метрики, например grain-таблица
   не в нижнем регистре) и проверки узла (`gap.metric_field_missing` для колонки или таблицы,
   которых нет в `model.yaml`).
8. **Всё остальное — именованный гэп, а не тихий проход.** Выражение не из одной колонки,
   фильтр метрики или входа, cumulative, derived, conversion, percentile/median/sum_boolean,
   `non_additive_dimension`, offset, alias, `fill_nulls_with`, `join_to_timespine: true`,
   `time_granularity` метрики не `null` дают review `gap.external_construct_unsupported` с
   путём конструкции; формула узла остаётся `external_ref`, как сейчас. Severity review — та
   же, что у сегодняшнего opaque external formula: этап не делает ни один ответ хуже текущего
   и не превращает непонятое в ok.
   Block: нет артефакта при контракте, который на него ссылается
   (`gap.external_source_missing`); артефакт не читается, не соответствует своей версии схемы,
   держит два объекта с одним именем, метрику, которая зависит от себя, или ссылку на
   отсутствующий объект из тех, что reader разрешает (`gap.external_artifact_invalid`);
   метрики `ref` в нём нет (`gap.external_ref_missing`); relation, время, пин и версия —
   п. 3, 4, 6. Reader разрешает входные метрики и measures метрики,
   `metric_aggregation_params.semantic_model` с его `agg_time_dimension` и `agg_time_dimension`
   semantic model и её measures. Не разрешает: `entity` и `constant_properties` conversion,
   `non_additive_dimension`, гранулярности `time_granularity`, offset, окон и `grain_to_date`,
   `primary_entity` (dbt пишет и виртуальную entity), шаблоны фильтров, saved queries. Каждая
   из этих конструкций в формуле — review выше; saved queries в формулу не входят.
   Для S2: V2-артефакт dbt 1.12.6 пишет `fill_nulls_with: null` при `fill_nulls_with: 0` в
   YAML и `type_params: null` у time dimension (гранулярности в артефакте нет). S2 читает
   артефакт, а не YAML: заполнения в нём нет, а гранулярность time dimension V2 не доказать —
   review п. 3. `input_measures` ratio dbt пишет как меры числителя, затем знаменателя, каждую
   один раз: у ratio одной метрики на саму себя мера одна (прогон матрицы); иной список —
   review конструкции.
   Инвентарь (S5): каждая причина reader и резолвера — строка
   [`docs/specs/dbt-formula-inventory.md`](../specs/dbt-formula-inventory.md) с гэпом, severity
   и формой JSON-пути; `tests/test_dbt_formula_inventory.py` разрешает по случаю на строку и
   сверяет таблицы с кодом: каждый код гэпа, каждое место модуля, которое называет или создаёт
   гэп (по позиции вызова; иное упоминание — алиас, импорт под другим именем, вызов на
   другом объекте или через модуль, имя строкой — и копия объекта — `copy`,
   `dataclasses.replace`, `__class__`, вызов результата вызова — отвергаются статически: это
   известные тесту формы, не любые), каждый ключ, читаемый как конструкция (у входа ratio — с
   каждой стороны; ключ `type_params` — у каждого вида метрики, который не берёт из него число,
   по схеме, а не по таблицам резолвера), каждый агрегат, ключ `agg_params` и тип метрики схемы,
   которых резолвер не разрешает, каждое значение перечисления схемы там, где резолвер его
   сравнивает, и у ключей-конструкций (`datatype`, `grain_to_date` — у simple и у ratio), пустое
   значение своего вида (пустая строка, 0, пустой список, пустой объект) у ключей-конструкций и
   `agg_params`, пустую строку там, где выражение или имя заменяет колонку, и в
   `agg_time_dimension` (reader её отвергает, если dimension с таким именем у модели нет), оба
   источника колонки (`expr` и имя — у measure и у time dimension) и три места, где резолвер
   заканчивает чтение раньше (тип метрики вне simple/ratio, ratio без стороны или над
   неподходящим входом, simple без способа агрегации или с двумя).
   Корпус (S5b): `scripts/dbt_corpus.py` строит для строк, которые может нести dbt-проект,
   записи — конструкцию так, как её пишет автор, в legacy или в V2, — и разбирает их dbt-core
   1.12.6: каждую отдельно, потом принятые вместе; метрика в общем артефакте разрешается так
   же, как на своём проекте, иначе прогон останавливается. Артефакты принятых записей и ответ
   резолвера по каждой лежат в `tests/fixtures/dbt_semantic_manifest/corpus`
   ([отчёт](../reviews/DECISIONOPS_DBT_CORPUS_2026-11-07.md)). Из 87 строк dbt пишет 63 и
   отвергает 10: незнакомые ключи legacy и V2 — JSON-схемой, остальное — собственными
   проверками. 3 строки он принимает, но не доносит: `cumulative_type_params` legacy
   simple-метрики и `fill_nulls_with` V2 пишет как `null`, а до паритета не доходит ни одна
   V2-метрика. Ещё 11 строк зависят от артефакта целиком или от контекста, который корпус
   держит одним. Строки, которых dbt-core 1.12.6 не пишет, остаются: артефакт другой версии
   или другого автора может их нести.
9. **Без ложной эквивалентности.** Узел метрики несёт binding: source, ref, revision и digest
   замыкания. Любое изменение определения в dbt — поддержанное или нет — сдвигает canonical
   digest, и diff его называет. Неподдержанная конструкция никогда не сводится к типизированной
   формуле, которая её не видит: это ровно та ошибка, которую S0 нашёл в OSI.
   Реализация (S3): `MetricNode.binding` — `FormulaBinding` с `source`, `ref` и
   `source_revision` объявления и `closure_digest` — digest замыкания в прочитанном артефакте,
   `null`, если артефакт его не дал (не передан, отвергнут, метрики `ref` в нём нет); заданный,
   от пина он отличается ровно при `gap.external_revision_mismatch`. Гэпы резолвера snapshot пишет с
   путём `metrics.<id>.formula`, а при пути в артефакте — `metrics.<id>.formula:<JSON path>`;
   значение гэпа — digest замыкания. Любой сдвиг binding diff называет review
   `metric.binding_changed` с impact метрики — и через него инвалидирует её verified assets;
   detail говорит, что сдвинулось: пин, замыкание (`not read` для `null`) или то, что формулу
   стал или перестал определять dbt. Так называется и правка, которую типизированная формула не
   показывает (описание semantic model), и правка конструкции, оставившей формулу `external_ref`.
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
    Реализация (S3): `declared_node(node)` — связанный узел в объявленной форме: формула —
    `external_ref` с пином, поля — ключи grain и event time, binding нет; это ровно
    `metric_node(contract)`. `_content_differences` сравнивает в этой форме и кандидата, и
    base. Узел без binding пишется без этого ключа, поэтому граф без связанных метрик и его
    canonical digest — те же, что в версии 3, и payload версии 3 читаются; сам snapshot несёт
    `source_version` 4, как любой snapshot этого источника. Артефакт и dbt
    database принимает `NativeModelSource(semantic_manifest=, dbt_database=)`; байты артефакта
    целиком входят в source digest под ключом `dbt_semantic_manifest`. Контекст строки паритета
    (п. 7) — движок и типы grain-таблицы, как их держит граф: поле, объявленное дважды и
    по-разному, в граф не входит и даёт `undeclared`. Версии: source 4, verifier 128, check 82.
11. **Capture, verify и evidence.** Capture и verify загружают артефакт одним загрузчиком;
    CLI требует его для обеих команд, если контракты на него ссылаются. Расхождение `canonical_digest`
    кандидатного графа уже даёт review `provenance.other_snapshot`; расхождение только
    source digest при том же графе эта проверка не видит. Evidence хранит по каждой
    стороне каноническое замыкание каждой связанной метрики, а не весь артефакт: тот описывает
    весь dbt-проект. Verifier проверяет целостность и то, что digest сохранённого замыкания
    равен digest в binding сохранённого snapshot. Повторный вывод всего snapshot из исходников
    offline не обещается — как и для native сегодня: verifier проверяет наличие, hash и размер
    артефактов, а snapshot не пересобирает.
    Реализация (S4): `NativeModelSource.read()` возвращает `SnapshotRead` — snapshot и
    замыкания по dbt ref каждого binding с `closure_digest`, из одного чтения байтов
    артефакта; `snapshot()` — его snapshot. `decision verify` берёт артефакт и базу каждой
    стороны (`--base-semantic-manifest`, `--base-dbt-database`, `--candidate-…`), `decision
    capture` — кандидата. Сторона, чей контракт берёт формулу из dbt, без артефакта —
    usage error (exit 2) до gate и до запроса к движку: ошибка называет метрики и опцию.
    База без артефакта — тоже usage error; артефакт без dbt-контрактов допустим.
    `decision pin <artifact> [<metric>…] [--json]` печатает пин — значение
    `external.source_revision` — тем же строгим reader. `GateInputs.base_closures` и
    `candidate_closures` — ровно замыкания refs, которые связывает snapshot стороны, и
    каждое хешируется в свой `closure_digest`; иное `GateInputs` отвергает. В идентичность
    прогона они не входят: snapshot адресует их digest. Bundle хранит каждое как
    `closure-<сторона>-<slug>-<hex>` (kind `formula_closure`, хеш массива `[dbt ref]`), байты
    — RFC 8785-форма. `verify_bundle` (а с ним `evidence.bundle` и `decision evidence`)
    сверяет их со snapshot, прочитанным как JSON: block `evidence.closure_missing`,
    `evidence.closure_mismatch`, `evidence.closure_unbound` и
    `evidence.closure_unverified` (snapshot цел, но binding в нём не прочесть — замыкания
    стороны не проверены). Classification замыкания — `internal`, как у snapshot; при
    `filter` или conversion замыкание по п. 4 — весь семантический проект артефакта без
    saved queries (ARCHITECTURE §9.2). Версии: verifier 129, check 83 — смысл
    `evidence.bundle` расширился. Pilot —
    [`docs/pilot/2026-11-06-dbt-formula-pin`](../pilot/2026-11-06-dbt-formula-pin/README.md):
    перепин изменения dbt, тот же dbt без перепина и контроль.

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
    `average`, measure без `expr`, private, conversion, custom granularity, `create_metric`,
    saved query с export; дамп pydantic-моделей metricflow 0.213.0 как эталон схемы;
  - S2 — разрешение поддержанного и гэпы неподдержанного вместе, проверки совместимости
    (п. 3), матрица паритета на Mac;
  - S3 — binding в узле, сверка с validated-контрактом по объявлению, diff и инвалидация
    verified assets, версии source, verifier и check, совместимость сериализации;
  - S4 — CLI (артефакт и database каждой стороны, печать пина), capture и verify, замыкания в
    evidence, pilot-сценарий;
  - S5 — расширение корпуса, инвентарь неподдержанных конструкций, паритет ClickHouse (S5c),
    финальное ревью.
- Каждый апгрейд dbt со сменой `dsi_package_version` блокирует внешние формулы, пока версию не
  добавят в список. Это цена отказа от угадывания.
- Пин по объектам целиком консервативен: правка включённой semantic model или
  `project_configuration`, не меняющая число (например, новое описание dimension), тоже
  потребует перепина. Сужение замыкания — только с тестом, что исключённое поле число не
  меняет.
- dbt и MetricFlow gate не запускает: он читает уже собранный артефакт. Jinja-шаблоны
  фильтров и SQL-выражения не разбираются.
- Native-формулы, граф и canonical digest прогонов без контракта на `dbt_semantic_manifest` и
  уже записанные evidence не меняются; новый прогон несёт source version 4 и новые версии
  verifier и check, поэтому его run id другой, как при любом их подъёме. Записанные bundle
  verifier перечитывает как прежде: замыканий в них нет, а binding их snapshot не несут. С S4
  CLI передаёт артефакт каждой стороны, и прогон с dbt-контрактом без артефакта
  останавливается usage error до gate. `NativeModelSource` без артефакта по-прежнему даёт
  block `gap.external_source_missing` рядом с review opaque — как требует п. 8.
