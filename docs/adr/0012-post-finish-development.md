# ADR 0012: развитие после финиша — пункты D1–D6

**Статус:** Accepted
**Дата:** 2026-11-13
**Основание:** решение владельца 2026-11-13: «продолжи развитие проекта - по плану / бэклогу.
комить в новую локальную ветку» и «начни с глубокого рисерча, насколько проект актуален. что
сейчас требуют на рынке»; новая работа после финишной черты — по пункту 4
[ADR 0010](0010-finish-line.md).

## Контекст

Финиш ADR 0010 пройден 2026-11-10, код опубликован ([ADR 0011](0011-public-repository.md)).
Находки после финиша лежат в [бэклоге](../DECISIONOPS_BACKLOG.md). Перед выбором работ сделан
рыночный срез (2026-11-13, публичные источники); из него для плана важно следующее.

- Доверие к данным — приоритет у 83 % участников dbt State of Analytics Engineering 2026
  (66 % годом раньше); 71 % опасаются, что неверные данные дойдут до стейкхолдеров
  ([dbt, 2026-04-14](https://www.getdbt.com/blog/new-dbt-labs-report-finds-ai-driven-acceleration-is-outpacing-trust-and-governance)).
- Инструменты этой ниши живут в pull request: dbt Advanced CI, Recce, Datafold, Paradime
  выносят вердикт комментарием или проверкой PR. До дашборда в PR доходит только Paradime и
  только для Looker, Tableau и ThoughtSpot
  ([Paradime](https://docs.paradime.io/app-help/documentation/bolt/ci-cd/lineage-diff));
  для Superset и DataLens такого инструмента не найдено.
- Семантический слой стал входом для AI-агентов через MCP: dbt MCP server, Cube, MCP-сервис
  Superset 6.1 ([Preset](https://preset.io/blog/apache-superset-6-1-release/)).
- В dbt Semantic Layer для ClickHouse MetricFlow помечен «Not yet»
  ([ClickHouse docs](https://clickhouse.com/docs/integrations/connectors/data-ingestion/etl-tools/dbt/dbt-core-v2-fusion-and-platform)),
  а на трёх публичных проектах dbt-labs формулы без гэпов у 4 метрик из 54
  ([отчёт F4](../reviews/DECISIONOPS_DBT_PUBLIC_2026-11-10.md)).
- Сигнала спроса на подписанное evidence для данных не найдено; регуляторный срок EU AI Act
  для high-risk систем перенесён на 2027-12-02.

Вывод: CLI без точки встраивания в PR и с ложными `block`/`review` команда не включит —
первая ложная блокировка в PR отключает гейт. Поэтому сначала чинятся ложные вердикты из
бэклога, потом строятся точки встраивания.

## Решение

1. **Работа идёт по пунктам D1–D6, в этом порядке:**
   - **D1. Типы.** Написания типов PostgreSQL вне правила F2 (имя со схемой или в кавычках,
     `serial`-семейство, `bit` без длины, `integer array`, `national character varying`,
     точность `timestamp`/`time` по умолчанию), `dec` в правиле точности `sql_contract`,
     псевдонимы типов ClickHouse (`BIGINT` — `Int64`). Таблица равенства берётся живьём у
     движков (PostgreSQL 16, ядро 9.4.26, ClickHouse 24.8), а не из документации.
   - **D2. Отношения и отказы.** Внешняя таблица PostgreSQL как витрина; права на запись в
     matview; matview `WITH NO DATA`; отказ роли читать каталог колонок — review
     `physical.not_observed`; отказ `system.tables` на ClickHouse — живая проверка, затем
     исправление или запись о невоспроизводимости.
   - **D3. Pull request.** Отчёт `decision verify` для комментария PR (вердикт, затронутые
     продукты и чарты, главные находки, digest evidence) и composite GitHub Action, который
     его публикует в PR пользователя. Сам проект ничего не публикует.
   - **D4. Покрытие dbt.** Derived- и ratio-метрики MetricFlow и V2-строки паритета; мера —
     доля метрик публичных проектов F4 без гэпов проекта.
   - **D5. MCP.** Read-only MCP-сервер поверх существующих команд (impact, verify без записи
     в BI, проверка evidence).
   - **D6. Инвентарь Superset.** Чарты, датасеты, метрики и поля из Superset REST API вместо
     ручного описания продуктов решений.
2. **Заморозка как в ADR 0010.** Находки вне D1–D6 идут в бэклог; сразу чинится только
   неверный вердикт по числам. Ревью — r1 открытое, r2 закрывающее; что осталось — в бэклог.
3. **Ветка.** Работа коммитится в локальную ветку `decisionops-backlog`. Push, pull request и
   публикация — только по отдельному слову владельца (ADR 0011: `main` меняется через PR).
4. **Вне плана:** Apache Ossie (спецификация v0.1), Snowflake/BigQuery/Databricks, KMS,
   column-level lineage, scheduler.
5. После D6 новая работа — снова только новым решением владельца и новым ADR.

## Последствия

- Каждый пункт — срез с прежним порядком: мутанты, ревью, полный offline-гейт на Mac, запись
  в журнале статуса.
- Ревьюер, пока у владельца нет лимита Codex, — независимый Opus-субагент
  ([CURRENT_STATE](../DECISIONOPS_CURRENT_STATE.md), «Решения»); Astra им не заявляется.
- D1 и D2 меняют грейдинг — `CHECK_VERSION`; D3–D6 добавляют входы и выходы, не меняя
  вердиктов.
