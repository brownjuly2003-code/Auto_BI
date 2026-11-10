# Формулы публичных dbt-проектов — F4 (ADR 0010), 2026-11-10

Пункт F4 [финишной черты](../adr/0010-finish-line.md): прочитать формулы на публичных
dbt-проектах из известного источника и проверить, что каждая форма, которую гейт не читает,
поддерживается или стоит строкой [инвентаря](../specs/dbt-formula-inventory.md). Исполнитель —
Claude; запуск чужого кода (`dbt deps`, `dbt parse`) одобрен владельцем вручную.

## Проекты

| Проект | Ревизия | `require-dbt-version` | Лицензия |
|---|---|---|---|
| MetricFlow tutorial — `dbt-metricflow/dbt_metricflow/cli` репозитория `dbt-labs/metricflow` (`mf_tutorial_project` с `sample_dbt_models`) | `fe4b8c1` | нет | Apache-2.0 (`dbt-metricflow/LICENSE`) |
| `dbt-labs/jaffle-sl-template` | `c54c8fb` | `>=1.6.0rc2` | файла лицензии нет |
| `dbt-labs/jaffle-shop` | `5beb145` | `>=2.0.0` | файла лицензии нет |

Артефакты dbt остались на стенде: у обоих jaffle-репозиториев лицензии нет, и артефакт,
построенный из их кода, в репозиторий не кладётся. В репозитории — записи прогона
(`tests/fixtures/dbt_semantic_manifest/public/`): источник с ревизией, флаги, объявленный
`require-dbt-version`, версии, дайджест артефакта или отказ dbt, проблемы ридера и ответ
резолвера по каждой метрике. Записи несут имена метрик, моделей и колонок проектов и
предупреждения dbt — столько, сколько нужно, чтобы проверить итог.

## Как читалось

`scripts/dbt_corpus.py public` на Mac-стенде: копия проекта во временном каталоге (без `.git`,
`target`, `logs`, `dbt_packages` в корне), профиль корпуса S5b вместо своего (PostgreSQL; база
не достигается), `dbt deps`, если у проекта есть пакеты, затем `dbt parse --no-partial-parse`.
dbt видит из окружения только `PATH`, временный `HOME`, `LANG`, `PYTHONUTF8` и выключатели
телеметрии — секретов в нём нет; `dbt run` не запускается. `dbt deps` ходит в сеть за пакетами,
закреплёнными `package-lock.yml` проекта: у jaffle-shop — `dbt_utils` 1.4.1 с dbt Hub и
`audit_helper` из git (`71817f0`), у jaffle-sl-template — `dbt_utils` 1.0.0 и `dbt_date` 0.8.1
с dbt Hub; у tutorial пакетов нет.

Версии — как у корпуса S5b: dbt-core 1.12.6, dbt-postgres 1.11.0, metricflow 0.213.0. dbt
запускается с `--no-version-check`, как каждый вызов корпуса: jaffle-shop объявляет
`require-dbt-version: ">=2.0.0"` — dbt 2, — и dbt-core 1.12.6 читает его в обход этой проверки.
Его артефакт — чтение проекта для dbt 2 через dbt-core 1.12.6, не через dbt, которого проект
просит.

Метрика разрешается против контракта на `dm.public_grain` с событийным временем `date` —
таблицы, которой в проекте нет. Строки, которые решает такой контракт, — `relation.table` и
`time.column-name`/`time.column-expr` (таблица и колонка времени контракта), — гэпы контракта;
каждая метрика без гэпов проекта встаёт на `relation.table`. Остальные гэпы — проекта, паритет
тоже: до него метрика чужой таблицы не доходит.

## Отказ dbt

jaffle-sl-template в написании авторов dbt-core 1.12.6 не принимает: `Semantic Manifest
validation failed`. Единственная названная метрика — `orders_last_7_days`: `window` стоит прямо
в `type_params`, а флаг поведения `require_nested_cumulative_type_params` по умолчанию `true`.
С этим флагом в `false` — документированный флаг dbt, им же корпус S5b пишет такую метрику, и
единственная правка копии `dbt_project.yml` — проект читается (запись
`jaffle_sl_template_unnested`).

## Итог

| Запись | Метрик | Без гэпов проекта | Строки инвентаря (метрик с таким гэпом) |
|---|---|---|---|
| `jaffle_shop` | 23 | 3 | `metric.filter` 4; `type.cumulative` 1; `type.derived` 3; `v2.agg.median` 1; `v2.expr` 9; `v2.no-expr` 6 |
| `jaffle_sl_template_unnested` | 18 | 0 | `legacy.agg.percentile` 1; `legacy.agg_params.percentile / legacy.agg_params.use_approximate_percentile / legacy.agg_params.use_discrete_percentile` 1; `legacy.expr` 6; `measure-input.fill_nulls_with` 3; `measure-input.join_to_timespine` 1; `metric.filter` 3; `time.expression / time.name` 13; `type.cumulative` 2; `type.derived` 3 |
| `metricflow_tutorial` | 13 | 1 | `legacy.agg.sum_boolean` 1; `legacy.expr` 8; `metric.filter` 2; `type.cumulative` 2; `type.derived` 1 |

Строку называет причина резолвера. Через « / » — строки, которые различает только значение в
артефакте (выражение времени и имя, ключи `agg_params`): по записи без артефакта их не
различить. По артефакту на стенде гэпы времени шаблона — `time.expression`
(`cast(ordered_at as DATE)`, `cast(first_ordered_at as DATE)`). Таблицу держит
`tests/test_dbt_public.py`.

- Ридер прочитал все три артефакта без проблем.
- Каждый гэп проекта — одна строка инвентаря (или группа, которую различает только артефакт).
  Непрочитанных форм вне инвентаря нет: новых строк и новой поддержки F4 не добавляет.
- Из 54 метрик гэпов проекта нет у четырёх: `average_tax_rate`, `customers`, `revenue`
  (jaffle-shop) и `transaction_amount` (tutorial) — их останавливает только контракт на чужой
  таблице. С контрактом на своей таблице разрешилась бы одна, `transaction_amount`: три
  V2-метрики jaffle-shop остановились бы на `parity.v2` (review) — V2-строк в матрице паритета
  нет. Это проверено на артефактах стенда, не тестом.
- Остальные 50 останавливает гэп проекта: конструкция вне подмножества, из которого гейт строит
  типизированную формулу (`gap.external_construct_unsupported` — V2-метрика с выражением или
  без него, мера с выражением, derived и cumulative, фильтр метрики, `median`, `percentile`,
  `sum_boolean`, `fill_nulls_with`, `join_to_timespine`), или время из выражения
  (`gap.external_time_unproven`); оба — review. На учебных проектах dbt-labs подмножество
  узкое; это не неверный вердикт — метрика остаётся `external_ref` с гэпом, — а граница охвата,
  она в [бэклоге](../DECISIONOPS_BACKLOG.md).

## Что F4 поправил в инвентаре

jaffle-shop пишет время V2 как `granularity: day` с `dimension: {type: time}`, и dbt-core
1.12.6 записывает для такого измерения `type_params.time_granularity: day`. Инвентарь говорил,
что 1.12.6 не пишет гранулярность ни одному V2-измерению времени, — это верно для написания
корпуса (`dimension: time`), не для каждого. Поэтому время V2-метрику такого проекта больше не
останавливает: без других гэпов она доходит до `parity.v2` — так три метрики jaffle-shop на
своей таблице. Строки `time.no-granularity` и
`parity.v2` и раздел «What the artifact does not show» исправлены; вердикт не меняется —
`parity.v2` остаётся review, пока V2-строка паритета не прогнана.

## Границы

- Одна версия dbt и три проекта dbt-labs — учебные и шаблонные, не промышленные; jaffle-shop
  прочитан не тем dbt, которого он просит.
- Записи — снимок ответа резолвера на дату прогона. Тест держит их против инвентаря, но метрики
  не перерешивает: артефактов в репозитории нет. После правки резолвера — новый прогон
  `scripts/dbt_corpus.py public` на стенде.
- Тест не ловит конструкцию, которую резолвер молча пропустил бы у разрешимой метрики: у
  четырёх метрик без гэпов проекта это проверил по артефактам ревьюер r1 (модель), не тест.
- Гэпы контракта говорят о контракте на чужой таблице, не о проекте.
