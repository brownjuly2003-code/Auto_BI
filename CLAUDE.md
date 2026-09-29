# CLAUDE.md — Auto_BI_new (DecisionOps)

> **Это форк, и продукт здесь другой.** `D:\Auto_BI_new` — не «текст → дашборд», а
> **Analytics Release & Evidence Gate**: проверка, что изменение семантики не ломает
> governed-числа, с content-addressed evidence. v1-раннтайм Auto_BI сохранён без
> функциональных изменений и всё ещё описан ниже — правила v1 (инварианты, стопперы,
> DoD) в силе для кода, который их касается.
>
> **Вход в сессию — `docs/DECISIONOPS_CURRENT_STATE.md`** (состояние и открытые
> решения), затем `docs/DECISIONOPS_ARCHITECTURE.md` (дизайн и обоснования).
> Завершённый bootstrap и подробный журнал лежат в `docs/history/`; читать их
> только когда нужна история решения. `_NEXT_SESSION.md` из исходного репозитория
> сюда **не переносился** — не искать.

## Актуальный порядок работы

Решение владельца 2026-09-12 закреплено в [AGENTS.md](AGENTS.md): простые задачи —
Luna xhigh; содержательное ревью — только моделирование через Astra xhigh;
инфраструктурные стенды — только Mac. Старые модельные стопперы ниже — история
baseline, не действующий порядок эскалации. Человеческое ревью не требуется для
продолжения разработки; модельная оценка не становится human evidence.

## Статус v1 (унаследованное)

> Пооперационный лог сессий и разборы (`docs/history/`, `docs/plans/`) вынесены во
> внутренний репозиторий: это рабочая кухня — стенды, машины, ходы отладки, — и она
> устаревала быстрее, чем окупалась её правка. Всё, что имеет продуктовое значение,
> живёт в поддерживаемых доках: `docs/ARCHITECTURE.md` (дизайн и его обоснования),
> `docs/PLAN.md` (фазы), `CHANGELOG.md` (что и когда поехало).
>
> v1-контекст: агент «текст/раскладка полей → дашборд в выбранной BI» поверх DM-слоя
> DWH — `README.md`, `docs/ARCHITECTURE.md`, `docs/PLAN.md`, `docs/MARKET.md`.

## Скоуп (решение 2026-06-11)

RU-рынок. **v1 = ClickHouse (DM) + Superset (BI)**; v2 = Greengage/Greenplum + DataLens (Public API). Power BI / Tableau / Metabase — вне скоупа. Универсальность держим в швах (IR, `BIAdapter`, rule pack per engine), имплементируем один путь. Демо-DM — на ClickHouse (docker).

## Инварианты дизайна (не нарушать без обновления ARCHITECTURE.md)

1. **IR-first**: LLM генерирует только `DashboardSpec` (pydantic IR). Нативные форматы BI (form_data, конфиги DataLens) — только детерминированный код адаптеров.
2. Spec валидируется против `semantic/model.yaml` ДО любых вызовов BI; неизвестное поле → reject + repair loop (max 3), никаких молчаливых починок.
3. SQL: только SELECT (sqlglot-guard), EXPLAIN + LIMIT-прогон, read-only роль DWH.
4. Уточняющие вопросы — только из grounding report, ≤3 за раунд; однозначный запрос → ноль вопросов.
5. **Feasibility Advisor**: вердикты только из детерминированных findings (ключи/статистика/EXPLAIN) — LLM формулирует, но не «решает»; advisory-only, сборку никогда не блокирует; классы `ok / spec_adjustment / dm_change_request`.
6. **Fields-first** (drag&drop полей) — второй вход в тот же пайплайн (seed для GROUNDING), не отдельный конструктор чартов.
7. Версия Superset запинена в docker-compose; версия DataLens-стенда (контрактные маркеры: gateway v4.10.4, dash schemeVersion=8, chart shared version "4", HC=1, gateway-экшен `us/renameEntry` с entryId-стабильностью для атомарного rebuild) запинена в runbook `internal/2026-06-13-datalens-selfhosted-runbook.md`; обновление любой — отдельная задача с прогоном contract-тестов (Superset viz-сьют / `tests/test_datalens_contract.py`).
8. Изменения промптов мержатся только с прогоном eval-сьюта (с Phase 1).

## LLM

GraceKelly (локальный сервис, должен быть запущен): `POST http://127.0.0.1:8011/api/v1/orchestrate`
`{"prompt": ..., "model": "claude-sonnet-5", "reasoning": true, "decompose": false, "session_id": ..., "metadata": {"app": "auto_bi"}}`
- `prompt` ≤ 40 000 символов → context selection обязателен на больших DM.
- Text-in/text-out: структурированный вывод через JSON-блок → pydantic → repair loop.
- Все вызовы через `llm/LLMClient`-абстракцию, напрямую httpx из бизнес-кода не дёргать.
- `reasoning: true` — на GROUNDING/PROPOSE_SPEC; механические шаги — без reasoning.

## Stack и конвенции

- Python 3.12 + uv; ruff + black; pytest. FastAPI, pydantic v2, sqlglot, httpx.
- Без LangChain/LangGraph — агент = простая state machine.
- Код/идентификаторы — английский; доки и общение — русский.
- Секреты только в `.env` (в `.gitignore`). Единственное документированное исключение —
  DecisionOps Ed25519 signing key: explicit PKCS#8 path вне checkout/evidence, на Mac с
  mode `0600`. Значения секретов в логи/доки не попадают.
- Стенд только на Mac: `docker-compose up` (Superset + ClickHouse с демо-DM).

## Исторические стопперы переключения модели (заменены AGENTS.md)

Дефолт реализации — **Opus high**. Агент не может сменить модель сам, поэтому стоппер = остановка с протоколом (ниже). Триггеры объективные, «по ощущениям» не останавливаться.

| Код | Триггер (проверяемый) | Действие |
|---|---|---|
| S1 дебаг-цикл | Один и тот же баг/тест: 3 цикла «гипотеза → правка → прогон» без смены симптома (новая ошибка = прогресс, счётчик сбрасывается) | STOP → рекомендация **Fable**; можно взять независимую задачу фазы |
| S2 промпты/eval | Задачи промпт-инжиниринга агента и дизайна eval (в Phase 1 это 1.4–1.7: GROUNDING/CLARIFY/PROPOSE_SPEC, политика уточнений, golden-кейсы) | **Не начинать на Opus в /auto.** STOP до старта → Fable или ручная сессия |
| S3 eval-регрессия | После правки промптов eval ниже базовой на ≥10 п.п. или ниже exit-порога фазы, и 1 итерация починки не вернула | STOP → Fable; промпт-изменение не мержить |
| S4 инварианты | Решение требует менять IR-схему, интерфейс `BIAdapter` или инварианты 1–8 этого файла | STOP всегда — вопрос пользователю, модель не важна |
| S5 form_data | viz_type не проходит contract-тест после 3 вариантов шаблона | STOP → приложить diff «create vs GET»; ручная сессия (вероятно с браузером), модель вторична |
| S6 граница фазы | Exit criteria фазы выполнены и проверены | STOP → предложить `/cxkm`-ревью перед следующей фазой; следующую фазу самому не начинать |
| S7 инфра | GraceKelly / Superset / ClickHouse-стенд недоступен после 2 попыток восстановления | STOP **без** эскалации модели — это не модельная проблема |

**Протокол STOP:** (1) WIP-коммит в текущую ветку `wip(stopper:Sx): <задача>`; (2) одна строка в блок «Статус» этого файла: дата, задача, стоппер, рекомендованная модель; (3) финальное сообщение: причина, что переключить, точка продолжения. После S1/S3/S5 допустимо взять независимую задачу той же фазы; после S2/S4/S6 — только остановка.

## Статус

- 2026-09-29 — PostgreSQL `COUNT`/`SUM`/`AVG`/`MIN`/`MAX` с `FILTER`
  сохраняют неявное имя выхода в `ORDER BY`/`DISTINCT ON` и сортировке set
  operation; аргументы и предикат остаются чтениями. `VERIFIER_VERSION`
  47 → 48, `CHECK_VERSION` 28 → 29. Red-green: ложное чтение
  `dm.sales_daily.sum` воспроизведено, затем 181 тест PostgreSQL order-by
  прошёл. Полный offline pytest: 2937 passed, 21 skipped, 38 deselected;
  strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые. Срез выбран
  синтетическим Astra xhigh review и реализован Luna xhigh; итоговое review:
  0 actionable P1/P2. Живой PostgreSQL не запускался; это не human/production
  acceptance.
- 2026-09-29 — прямые PostgreSQL `AVG`, `MIN` и `MAX` без alias дают неявные
  выходные имена для `ORDER BY`/`DISTINCT ON` и левой ветки set operation;
  чтения аргументов сохраняются. `VERIFIER_VERSION` 46 → 47,
  `CHECK_VERSION` 27 → 28. Red-green: 18 падений, затем 137 passed;
  полный offline pytest: 2893 passed, 21 skipped, 38 deselected;
  strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые.
  Синтетическое Astra xhigh review: 0 actionable P1/P2;
  live PostgreSQL не запускался, не human/production acceptance.
- 2026-09-29 — PostgreSQL-алиас таблицы со списком выходных имён оставляет
  `asset.query_fields_unchecked` для ссылок на эти имена: snapshot не доказывает
  физический ordinal lineage. Покрыты именованная таблица, скобочный `JOIN`,
  голое и квалифицированное имя, корреляция; соседние физические поля проверяются.
  `ORDER BY` сохраняет приоритет выхода SELECT, `GROUP BY` внутри LATERAL —
  локального выхода. `VERIFIER_VERSION` 45 → 46, `CHECK_VERSION` 26 → 27.
  Синтетические Astra xhigh review выявили четыре P2, закрытые red-green
  тестами; итоговое review: 0 actionable P1/P2. Полный offline pytest:
  2868 passed, 21 skipped, 38 deselected; strict mypy: 0 ошибок в 120 модулях,
  Ruff и Black чистые. Это не human/production acceptance.
- 2026-09-29 — PostgreSQL-семейство оставляет `asset.query_fields_unchecked` для
  `NATURAL JOIN`: ключи берутся из совпадающих выходных имён источников,
  которые проверка не разворачивает и которые могут зависеть от физической схемы.
  Явные соседние поля грейдятся отдельно, вложенные join учитываются.
  `VERIFIER_VERSION` 44 → 45, `CHECK_VERSION` 25 → 26. Red-green тесты закрыли
  пропуск и неточную причину для производных источников. Полный offline pytest:
  2847 passed, 21 skipped, 38 deselected; strict mypy: 0 ошибок в 120 модулях.
  Синтетическое Astra xhigh review: 0 actionable P1/P2 после исправления P2;
  не human/production acceptance.
- 2026-09-29 — PostgreSQL `SUM(...)` без alias даёт неявное имя `sum` для
  целого `ORDER BY`/`DISTINCT ON` и левой ветки set operation. Ложные field
  review/advisory уходят; аргументы сохраняют чтения. `AS`, квотированный
  регистр и унарный `+` сохраняют границы; `GROUP BY` не использует выход.
  `VERIFIER_VERSION` 43 → 44, `CHECK_VERSION` 24 → 25. PostgreSQL 16
  проверен на Mac. Синтетическое Astra xhigh review: 0 actionable P1/P2.
  Полный offline pytest: 2839 passed, 21 skipped, 38 deselected; strict mypy,
  Ruff и Black чистые. Не human/production acceptance.
- 2026-09-29 — PostgreSQL `ORDER BY`/`DISTINCT ON` распознаёт неявное имя
  `count` у прямого `COUNT(...)` без alias, также в левой ветке set operation.
  Ложные field review/advisory уходят, чтения аргументов сохраняются; `AS` и
  унарный `+` сохраняют границы; `"Count"(...)` сохраняет регистр имени.
  `VERIFIER_VERSION` 42 → 43,
  `CHECK_VERSION` 23 → 24. PostgreSQL 16.15 проверен на Mac. Срез выбран по
  синтетическому Astra xhigh review; повторное ревью: 0 actionable P1/P2.
  Полный offline pytest: 2830 passed, 21 skipped, 38 deselected; strict mypy,
  Ruff и Black чистые. Это не human/production acceptance.
- 2026-09-28 — корневые read-only `INTERSECT`/`EXCEPT` проходят SQL guard на
  PostgreSQL и ClickHouse; обе ветки грейдятся для governance. Скобочный корень
  PostgreSQL с внешней сортировкой тоже принимается. `SELECT INTO` отклоняется;
  scalar `LIMIT`/`OFFSET` на set operation сохраняет чтения подзапроса.
  Синтаксис обеих операций проверен на Mac в PostgreSQL 16.15 и ClickHouse 24.8.14.39.
  `VERIFIER_VERSION` 41 → 42 из-за перехода operator SQL от `chart.opaque_sql` к
  `reads_field`; `CHECK_VERSION` 22 → 23 из-за изменённой оценки прежних запросов.
  Синтетическое Astra xhigh review: P1/P2 исправлены, не human/production acceptance.
  Полный offline pytest: 2807 passed, 21 skipped, 38 deselected; strict mypy,
  Ruff и Black чистые.
- 2026-09-28 — корневой PostgreSQL `(SELECT …)` с внешними `ORDER BY`, `LIMIT`,
  `OFFSET` проходит guard и сохраняет чтения для governance; неподдержанные формы
  остаются отказом. Evidence versions 41/22 без изменений: ранее вход не принимался.
  Реализовано Luna xhigh; синтетическое Astra xhigh review: 0 actionable P1/P2,
  не human/production acceptance. Полный offline pytest: 2774 passed, 21 skipped,
  38 deselected; strict mypy, Ruff и Black чистые.
- 2026-09-28 — скобочный элемент ClickHouse `ARRAY JOIN (tags AS t)` читает `tags`,
  а `t` — алиас. Внешний `AS a` заменяет внутренний алиас; настоящий скобочный
  SELECT сохраняет свой scope. Поиск таблиц и chart impact при `IN <name>` используют
  ту же нормализацию. Ложные field review и chart reads уходят, валидный запрос
  больше не обрывает governance. `VERIFIER_VERSION` 40 → 41,
  `CHECK_VERSION` 21 → 22.
  Реализовано как Codex companion Luna xhigh; синтетическое Astra xhigh review:
  0 actionable P1/P2, не human/production acceptance. Целевой прогон T-25:
  303 passed; полный offline pytest: 2751 passed, 21 skipped, 38 deselected.
  Strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые.
- 2026-09-28 — SQL guard переводит `RecursionError` sqlglot на запросе глубже parser depth в
  обычный отказ `SQLGuardError`: `guard_sql`, `ReferenceQuery` и verified-asset registry
  больше не выпускают аварию интерпретатора, а читаемые запросы и chart impact сохраняют
  прежнее поведение. `VERIFIER_VERSION=40`, `CHECK_VERSION=21` без изменений. Реализовано как
  Codex companion Luna xhigh. Целевой прогон T-26: 190 passed, включая docs-as-code;
  полный offline pytest: 2745 passed, 21 skipped, 38 deselected. Strict mypy:
  0 ошибок в 120 модулях, Ruff и Black чистые.
- 2026-09-27 — PostgreSQL `ORDER BY` и `DISTINCT ON` разрешают целое голое имя
  через выход SELECT с учётом регистра, а имя внутри выражения читают как input-колонку.
  `ORDER BY` set operation называет выход левой ветки; корневые `INTERSECT` и
  `EXCEPT` guard по-прежнему отклоняет. Оконные, `WITHIN GROUP` и агрегатные
  сортировки, остальные формы `GROUP BY`, ClickHouse и chart impact не менялись.
  `VERIFIER_VERSION` 39 → 40, `CHECK_VERSION` 20 → 21. Реализовано `gpt-6-sol` high
  через MyFlow-кандидат `sol-high`; унарный `+` читает input и не даёт имени
  неалиасированной проекции, сортировка после скобочного запроса принадлежит его телу,
  а `(column)` сохраняет имя выхода. Полный offline pytest: 2737 passed, 20 skipped, 38
  deselected; strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые. Правила сверены
  вживую на PostgreSQL 16.15 (одноразовый кластер на Mac); ClickHouse 24.8.14.39 форму
  `(SELECT …) ORDER BY …` отвергает синтаксически. Синтетическое ревью `gpt-6-astra` xhigh
  через MyFlow: проход 1 — три находки (унарный `+`, сортировка после скобочного запроса,
  `(column)`) исправлены, проход 2 — F-T-24-7 (`+dm.stores.id`) исправлена, проход 3 — 0
  findings; не human/production acceptance.
- 2026-09-27 — PostgreSQL `GROUP BY` разрешает целое голое имя сначала через локальные
  FROM-колонки, затем через выходной столбец SELECT: при отсутствии объявления в
  governed-источниках ложный review заменён advisory; без источников выходное имя не
  оставляет остатка. Выражения, агрегатные и оконные выходы и квалифицированные имена
  сохраняют input-чтение; ClickHouse и chart impact не менялись. `VERIFIER_VERSION`
  38 → 39, `CHECK_VERSION` 19 → 20. Реализовано `gpt-6-sol` high через MyFlow-кандидат
  `sol-high`; полный offline pytest: 2684 passed, 20 skipped, 38 deselected; strict mypy:
  0 ошибок в 120 модулях, Ruff и Black чистые. Правило сверено вживую на PostgreSQL 16.15
  (одноразовый кластер на Mac). Синтетическое ревью `gpt-6-astra` xhigh через MyFlow:
  F-T-23-1 (имя `GROUP BY` в LATERAL без собственного FROM) исправлена, второй проход
  — 0 findings; не human/production acceptance.
- 2026-09-27 — governance reviewed SQL теперь называет ключи `JOIN … USING` как
  advisory `asset.query_fields_unchecked` на ClickHouse и PostgreSQL-семействе:
  общий ключ нельзя приписать одной таблице, а явные соседние поля грейдятся отдельно.
  `VERIFIER_VERSION` 37 → 38, `CHECK_VERSION` 18 → 19. Реализовано `gpt-5.6-luna`
  xhigh; финальное синтетическое `gpt-6-astra` xhigh review: 0 actionable P1/P2,
  не human/production acceptance. Полный offline pytest: 2655 passed, 21 skipped,
  38 deselected; docs-as-code: 24 passed; strict mypy: 0 ошибок в 120 модулях,
  Ruff и Black чистые.
- 2026-09-26 — F-T-11-13: обычная `dm.sales_daily.revenue` с точным единственным
  неалиасированным именованным источником своего SELECT больше не открывает scalar
  `WITH dm`. Ложные chart reads и `chart.sql_partial` исчезают; несовпадение,
  дубликаты, конфликтующие имена и catalog-источник остаются conservative.
  `VERIFIER_VERSION` 36 → 37, `CHECK_VERSION` остаётся 18. Синтетическое review —
  `gpt-6-astra` xhigh: 3 P2 закрыты red-green тестами, не human/production acceptance.
  Полный offline pytest: 2647 passed, 21 skipped, 38 deselected; docs-as-code: 24 passed;
  strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые.
- 2026-09-26 — F-T-11-7: `t.region` при однозначном явном `FROM`/`JOIN … AS t` своего
  SELECT больше не открывает одноимённый scalar `WITH t` в операторском SQL. Ложные
  chart reads и `chart.sql_partial` исчезают; реальный table read, tuple `t.1` и
  отдельное чтение scalar alias сохраняются. `VERIFIER_VERSION` 35 → 36,
  `CHECK_VERSION` остаётся 18. Итоговое синтетическое `gpt-6-astra` xhigh review:
  0 actionable findings после закрытия P2; не human/production acceptance.
  Полный offline pytest: 2641 passed, 21 skipped,
  38 deselected; chart-impact suite: 260 passed; docs-as-code: 24 passed;
  strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые.
- 2026-09-26 — однозначная ссылка на named CTE в операторском SQL теперь связывается с
  ближайшим объявлением: внутренняя CTE не открывает одноимённую внешнюю и не добавляет
  ложные chart reads или `chart.sql_partial`. `FROM` и bare `IN <name>` следуют одному
  правилу; отдельная внешняя ссылка сохраняет реальный гэп. Self-reference и одноуровневые
  дубликаты остаются conservative. `VERIFIER_VERSION` 34 → 35, `CHECK_VERSION` остаётся 18.
  Полный offline pytest до трёх дополнительных граничных тестов: 2628 passed, 21 skipped,
  38 deselected; после них chart-impact suite: 179 passed. Strict mypy: 0 ошибок в 120
  модулях. Синтетическое ревью `gpt-6-astra` xhigh: дефектов кода нет, один пробел покрытия
  закрыт тестами; не human/production acceptance.
- 2026-09-26 — chart impact учитывает `JOIN … USING` только в исполняемой части SQL:
  непрочитанная named CTE или scalar `WITH` не создаёт ложный `chart.sql_partial`,
  достижимый JOIN сохраняет гэп. `VERIFIER_VERSION` 33 → 34, `CHECK_VERSION` остаётся 18.
  Полный offline pytest: 2623 passed, 21 skipped, 38 deselected; strict mypy: 0 ошибок
  в 120 модулях, Ruff и Black чистые. Синтетическое ревью `gpt-6-astra` xhigh:
  0 actionable findings, не human/production acceptance.
- 2026-09-26 — chart impact учитывает ClickHouse `COLUMNS('regex')` только в исполняемой
  части операторского SQL: непрочитанные named CTE и scalar `WITH` больше не дают ложный
  `chart.sql_partial`, а открытая ссылка сохраняет гэп. Governance reviewed asset SQL
  остаётся whole-AST; `VERIFIER_VERSION` 32 → 33, `CHECK_VERSION` остаётся 18. Полный
  offline pytest: 2618 passed, 21 skipped, 38 deselected; strict mypy: 0 ошибок в 120
  модулях, Ruff и Black чистые. Синтетическое ревью `gpt-6-astra` xhigh:
  0 actionable findings, не human/production acceptance.
- 2026-09-26 — ClickHouse `COLUMNS('regex')` больше не даёт false-clean governance:
  `asset.query_fields_unchecked` называет физические колонки, не перечисленные SQL;
  соседние именованные поля продолжают грейдиться. PostgreSQL-семейство и chart impact
  без изменений. `VERIFIER_VERSION` 31 → 32, `CHECK_VERSION` 17 → 18. Выбор среза —
  синтетическая оценка `gpt-6-astra` xhigh: 0 actionable findings, не human/production
  acceptance. Полный offline pytest: 2612 passed, 21 skipped, 38 deselected; strict mypy:
  0 ошибок в 120 модулях, Ruff и Black чистые.
- 2026-09-26 — восстановлен package-wide strict mypy для SQL AST: типы `Expr`/`Selectable`,
  nullable cache/scope и коллекций согласованы с sqlglot без изменения SQL-поведения.
  Версии evidence остаются 31/17. Strict mypy: 0 ошибок в 120 модулях; полный offline pytest:
  2603 passed, 21 skipped, 38 deselected. Синтетическое ревью `gpt-6-astra` xhigh:
  0 actionable findings, не human/production acceptance.
- 2026-09-26 — однозначная ссылка внутри вложенного scalar `WITH` теперь связывается с
  ближайшим объявлением: внутренний алиас не открывает внешние `dictGet`, `joinGet` или `*`.
  Self-reference, дубликаты на одном уровне и shadowing именованных CTE остаются conservative;
  `VERIFIER_VERSION` 30 → 31, `CHECK_VERSION` остаётся 17. Итоговое synthetic/modelled review
  `gpt-6-astra` xhigh — 0 actionable findings; полный offline pytest — 2603 passed, 21 skipped,
  38 deselected. Это не human evidence и не production/design-partner acceptance.
- 2026-09-26 — девять offline-тестов ClickHouse alias read-through больше не зависят от
  порога `elapsed < 2.0`: бюджет Python-вызовов прерывает реальную работу парсера и reader'а,
  семантические assertions сохранены. Production-код и версии evidence не менялись.
  Моделированное ревью `gpt-6-astra` xhigh: 0 actionable findings, не human/production
  acceptance. Полный offline pytest: 2596 passed, 21 skipped, 38 deselected; Ruff и Black
  чистые.
- 2026-09-25 — один `COUNT(*)` считает строки, а `COUNT(DISTINCT *)` зависит от всех
  колонок источника. Governance снимает advisory `asset.query_fields_unchecked`, когда его
  единственной причиной была звезда `COUNT(*)`; chart impact добавляет `chart.sql_partial` для
  плитки с `COUNT(DISTINCT *)`, которая раньше была «не этой плиткой» либо была названа только
  по другим чтениям. VERIFIER_VERSION 29 → 30, CHECK_VERSION 16 → 17. Реализовано
  `gpt-5.6-terra` xhigh (Codex companion, MyFlow-кандидат `terra-xhigh`); ревью —
  `gpt-6-astra` xhigh через MyFlow: моделированное ревью, не human evidence и не
  production/design-partner acceptance. Итог ревью — один проход `gpt-6-astra` xhigh через
  MyFlow (2026-09-25): 0 findings.
- 2026-09-25 — ClickHouse `SETTINGS` не читает имя или значение настройки как поле на любом
  query level. Уходят phantom review `asset.query_ungoverned_field`, advisory
  `asset.query_fields_unchecked`, chart `reads_field` и `chart.sql_partial` на имени
  настройки; `additional_table_filters` вместо phantom review даёт advisory
  `asset.query_fields_unchecked` и `chart.sql_partial`, потому что его row-filter SQL не
  читается. VERIFIER_VERSION 28 → 29, CHECK_VERSION 15 → 16. Реализовано
  `gpt-5.6-terra` xhigh (Codex companion, MyFlow-кандидат `terra-xhigh`); ревью —
  `gpt-6-astra` xhigh через MyFlow: моделированное ревью, не human evidence и не
  production/design-partner acceptance. Итог ревью — один проход `gpt-6-astra` xhigh через
  MyFlow (2026-09-25): 0 findings.
- 2026-09-25 — голые input-колонки `HAVING` и `QUALIFY` читаются как из любой другой
  клаузы: PostgreSQL не видит output alias в `HAVING`, а ClickHouse сначала применяет
  видимый alias/read-through; `PARTITION BY` и `ORDER BY` окна в `QUALIFY` тоже читаются.
  VERIFIER_VERSION 27 → 28, CHECK_VERSION 14 → 15: направление одно — добавляются review
  `asset.query_ungoverned_field`, advisory `asset.query_fields_unchecked` и chart
  `reads_field`, ни одна находка не снимается. Реализовано `gpt-5.6-terra` xhigh (Codex
  companion, MyFlow-кандидат `terra-xhigh`); ревью — `gpt-6-astra` xhigh через MyFlow:
  моделированное ревью, не human evidence и не production/design-partner acceptance. Итог
  ревью — один проход `gpt-6-astra` xhigh через MyFlow (2026-09-25): одна находка (голое
  имя над единственным подзапросом или CTE) отклонена воспроизведением — её запрос
  отвергают оба движка, а имя читается так же, как в любой другой клаузе.
- 2026-09-22 — `chart.metric_unread` спрашивается по дашборду: гэп на плитке raw-SQL
  возникает, только когда продукт в scope, доставляющий именно этот точный pinned ref
  дашборда, декларирует одну из изменённых метрик — объединением деклараций всех
  доставляющих продуктов, с независимой фильтрацией смешанного изменения по каждому
  дашборду, без слияния версий дашборда и независимо от порядка продуктов. Ложный
  `chart.metric_unread` на дашборде, который доставляет только продукт, не читающий
  изменённую метрику, исчез; чтения полей, IR `computes_metric` и структурные гэпы
  остались всем изменением. VERIFIER_VERSION 25 → 26, CHECK_VERSION остаётся 13.
  Реализовано GLM 5.3 xhigh (изолированный профиль, попытка продолжена после rate
  limit); ревью — synthetic Astra xhigh read-only по ворк-ордеру, направившее правку
  (восстановлен `reaches_product` на `_match_raw`, фильтрация `computed` — в
  `charts_for`): моделированное ревью, не human evidence и не production/design-partner
  acceptance. Финальный post-implementation review — один проход `gpt-6-astra` xhigh через
  MyFlow (2026-09-24), 0 findings.
- 2026-09-24 — ClickHouse inline- и ARRAY JOIN-алиасы читаются как алиасы, не как поля
  таблицы: `ARRAY JOIN tags AS a` читает `tags`, но не phantom `dm.sales_daily.a`; inline
  alias виден на своём query level, включая ссылку до текстового объявления, и не виден в
  подзапросе. Верхнеуровневый SELECT-алиас сохраняет приоритет, а alias справа от `IN` не
  становится таблицей. VERIFIER_VERSION 26 → 27, CHECK_VERSION 13 → 14: направления оба —
  уходят ложные findings на именах алиасов, но inline-определение в HAVING или QUALIFY,
  которое прежняя атрибуция пропускала, теперь грейдится на месте и может назвать реальное
  поле (`internal_cost_raw` в `(internal_cost_raw AS y)`). Реализовано `gpt-5.6-terra`
  xhigh (Codex companion, MyFlow-кандидат `terra-xhigh`); ревью — `gpt-6-astra` xhigh
  через MyFlow: моделированное ревью, не human evidence и не production/design-partner
  acceptance. Итог ревью — шесть проходов `gpt-6-astra` xhigh через MyFlow (2026-09-24):
  девять находок, восемь исправлены, одна (порог по wall-clock) отклонена с доказательством,
  последний проход — 0 findings.
- 2026-09-18 — на ClickHouse `x IN <table>` читается ровно как `x IN (SELECT * FROM
  <table>)` — в governance-проверке и в достижимости chart impact: ложной находки поля для
  `IN rev` больше нет, а неуправляемая таблица справа от `IN` теперь даёт review
  `asset.query_ungoverned` там, где был advisory (`IN dm.rev`) или ничего (`IN rev`).
  Остатки F-T-14-3 и F-T-14-1 названы в доках. VERIFIER_VERSION 24 → 25, CHECK_VERSION
  12 → 13. Начал grok xhigh (`grokw`), после лимитов обоих grok-аккаунтов закончил
  opus-high (`claude -p`, claude-opus-5, effort high), ранний выход добавил оркестратор;
  ревью — один проход Fable (claude-fable-5-1, effort medium), записанный через MyFlow
  как внешний; Opus-ревьюеры пропущены (тот же движок, что у автора), GLM-5.3 и Codex в
  rate limit: моделированное ревью, не Astra по ADR 0006 и не human evidence.
- 2026-09-18 — ClickHouse-алиас читается насквозь, а не как колонка: колонки
  `WITH <expr> AS a` читаются там, где `a` используется, а ссылка на алиас — не колонка
  таблицы плитки. Имя, объявленное и WITH-, и SELECT-алиасом на одном уровне, читается
  как колонка (F-T-13-17, риск принят). VERIFIER_VERSION 23 → 24, CHECK_VERSION 11 → 12.
  Реализовано grok xhigh (`grokw`), ревью — Opus через MyFlow (`opus-sub`), три прохода,
  без второго голоса; GLM-5.3 и Codex недоступны: моделированное ревью, не Astra по
  ADR 0006 и не human evidence.
- 2026-09-18 — какая часть operator SQL исполняется, решает один обход гейтов CTE,
  общий для словаря, Join-таблицы и звезды: именованная CTE открывается исполняемой
  неквалифицированной ссылкой, scalar `WITH` — исполняемым чтением алиаса (голое имя
  или кортеж); вложенный запрос в живой части исполняется, мёртвый читатель ничего не
  открывает. Shadowing, table alias и database qualifier с тем же именем — известные
  пере-сообщения. Оба пункта, отложенные в T-11, закрыты. VERIFIER_VERSION 22 → 23,
  CHECK_VERSION остаётся 11. Реализовано grok xhigh (`grokw`), ревью — Opus через
  MyFlow (`opus-sub`), без второго голоса; GLM-5.3 и Codex недоступны: моделированное
  ревью, не Astra по ADR 0006 и не human evidence.
- 2026-09-18 — `joinGet` / `joinGetOrNull` с литеральными Join-таблицей и одной колонкой
  называют чтение `<таблица>.<колонка>`; любой другой `joinget…` оставляет плитку
  прочитанной частично (`chart.sql_partial`) с клаузой «whose Join table read this run
  could not name». VERIFIER_VERSION 21 → 22, CHECK_VERSION остаётся 11. Реализовано
  grok xhigh (`grokw`), ревью — Opus через MyFlow (`opus-sub`), без второго голоса;
  GLM-5.3 и Codex недоступны: моделированное ревью, не Astra по ADR 0006 и не human
  evidence.
- 2026-09-18 — словарное правило доведено: закрытый набор типизированных геттеров
  (`dictGet<Type>` по целым 8…256, float, decimal, date/datetime, uuid, ip, string, bool)
  называет чтение, scalar `WITH (SELECT …) AS alias` считается, когда алиас прочитан, клауза
  непрочитанного вызова правдива для каждого случая. Пере-сообщение через алиас из
  неисполняемой CTE и асимметрия правила звезды отложены в T-11. VERIFIER_VERSION 20 → 21,
  CHECK_VERSION остаётся 11. Реализовано grok xhigh (`grokw`), ревью — Opus через MyFlow
  (`opus-sub`, второй голос `fable`; GLM-5.3 и Codex недоступны): моделированное ревью, не
  Astra по ADR 0006 и не human evidence.
- 2026-09-18 — metric-derived гэпы (`chart.metric_unread`, `chart.metric_unknown`,
  `chart.formula_untyped`) записываются только для изменения, которое достигает продукта в
  scope; per-product scoping гэпа остаётся открытым. VERIFIER_VERSION 19 → 20, CHECK_VERSION
  остаётся 11. Реализовано grok xhigh (`grokw`), ревью — Opus через MyFlow (`opus-sub`, второй
  голос `fable`; GLM-5.3 и Codex недоступны): моделированное ревью, не Astra по ADR 0006 и не
  human evidence.
- 2026-09-18 — словарный вызов ClickHouse в исполняемом scope либо называет чтение
  `<словарь>.<атрибут>` (литеральный геттер, кортеж — по чтению на атрибут), либо оставляет
  плитку прочитанной частично (`chart.sql_partial`), `dictHas`/`dictIsIn` включительно.
  VERIFIER_VERSION 18 → 19, CHECK_VERSION остаётся 11. Реализовано grok xhigh (`grokw`),
  ревью — Opus через MyFlow (`opus-cli`, на части прогонов молча отдавший haiku, затем
  `opus-sub`; второй голос `fable`; GLM-5.3 и Codex недоступны): моделированное ревью, не
  Astra по ADR 0006 и не human evidence.
- 2026-09-17 — операторский `raw_sql` читается из SQL: `reads_field` там, где statement сам
  атрибутирует чтение, никогда `computes_metric`; `chart.opaque_sql` — неразобранный SQL,
  `chart.sql_partial` — частичное чтение, `chart.metric_unread` — невыведенная связь с
  метрикой; `impact.products` раскладывает плитки по коду гэпа. VERIFIER_VERSION 17 → 18,
  CHECK_VERSION остаётся 11. Реализовано grok xhigh (`grokw`), ревью — Opus (`opus-cli`)
  через MyFlow: моделированное ревью, не Astra по ADR 0006 и не human evidence.
- 2026-09-17 — durable spec и комментарии версий записывают, что имя внутри тела LATERAL
  может сдвинуть грейдинг в любую сторону, `needs_review` включительно; три сценария
  фиксируют направления, у которых не было покрытия. Поведение не менялось. Реализовано
  grok xhigh (`grokw`), ревью — Opus (`opus-cli`) через MyFlow: моделированное ревью, не
  Astra по ADR 0006 и не human evidence.
- 2026-09-17 — предшествующие FROM-элементы LATERAL доходят до каждого уровня тела до стены
  derived table/CTE; кандидаты с охватывающих уровней рядом с ближайшими, не упорядочены
  по уровню. VERIFIER_VERSION 16 → 17, CHECK_VERSION 10 → 11. Реализовано grok xhigh
  (`grokw`), ревью — Opus (`opus-cli`) через MyFlow: моделированное ревью, не Astra по
  ADR 0006 и не human evidence.
- 2026-09-17 — голое имя разрешается только по источникам, которые scope читает; имя в
  коррелированном подзапросе, которого локальный источник не объявляет, остаётся
  `asset.query_fields_unchecked`. VERIFIER_VERSION 15 → 16, CHECK_VERSION 9 → 10.
  Реализовано grok xhigh (`grokw`), ревью — Opus (`opus-cli`) через MyFlow: моделированное
  ревью, не Astra по ADR 0006 и не human evidence.
- 2026-09-12 — полная evidence inspection использует один manifest snapshot для
  digest, подписи и возвращённой модели; замена файла между проверками больше не
  смешивает версии. Формат evidence и writer не изменены.
- 2026-09-12 — исправлен обход `--expect-digest` в `decision evidence --artifact`:
  неверный manifest pin блокирует выдачу payload с exit `4`; проверки подписи и
  classification сохранены. Срез реализован Luna xhigh и одобрен Astra xhigh.
- 2026-09-12 — по synthetic Astra xhigh review R3/R4 owner-summary объясняет границы
  reconciliation, attestation, compiled-reference cost и подписи; verifier 15, check 9.
  Исторические signed bundles не изменены; human/production acceptance не заявляется.
- 2026-09-12 — техническая часть R3 закрыта tracked live owner package: Mac, нативный
  ClickHouse 24.8.14.39 на изолированных портах, attested capture, все пять DWH checks и
  signed bundle `run-a7e2e9f914a502de`; pinned-key inspection проходит. Предыдущий S7 снят
  для этого среза без Docker/Colima; human/design-partner outcome остаётся внешним gate.
- 2026-09-12 — решение 3 пересмотрено: canonical evidence manifest поддерживает opt-in
  Ed25519 signature; trust root — pinned public SPKI PEM, signing identity входит в
  `run_id`. Pilot-only private key создан вне checkout на Mac с mode `0600`; production key
  и инфраструктура на Windows не создавались; ADR 0005.
- 2026-09-12 — PostgreSQL `max_scanned_bytes`: native API PostgreSQL 14–18 не
  предоставляет ни exact-query scanned-byte counter, ни byte ceiling; buffer/cache/I/O
  counters измеряют другой контракт. До named trusted provider/extension остаётся честный
  `cost.not_measured`.
- 2026-09-12 — `dwh.cost`: runtime-границы exact reference-запроса проверяются под
  engine limits — ClickHouse для bytes и timeout, PostgreSQL для timeout; недоступные в
  PostgreSQL scanned bytes остаются явно неизмеренными.
- 2026-09-11 — `decision capture`: capture-путь реализован; live-контракты проверены на PostgreSQL-семействе и ClickHouse 24.8.14.39, включая UTC RFC 3339 и metadata пустого результата.

## Definition of done (фаза/задача)

- Тесты зелёные (pytest), ruff чистый.
- Exit criteria фазы из PLAN.md проверены реальными прогонами, не «на глаз».
- Обновлены: статус в этом файле; ARCHITECTURE.md — если дизайн уточнился.
