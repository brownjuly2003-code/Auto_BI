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

- 2026-10-02 — при нуле фактических security-наблюдений чистый код —
  `security.not_run`, поэтому проверка появляется в `What was not done`.
  Частичное ненулевое покрытие сохраняет `security.unfiltered`; findings,
  status, verdict и detail прежние. Verifier 77, check 53.
  Выбор — synthetic Astra xhigh, реализация Codex. Профильный набор: 224 passed;
  Black/Ruff/strict mypy чистые. Итоговое synthetic Astra xhigh review:
  0 substantive findings. Полный offline gate: 3899 passed, 5 skipped,
  38 deselected, exit 0.
  [Проверки и границы](docs/DECISIONOPS_SECURITY_OUTCOME_REVIEW_2026-10-02.md).

- 2026-10-02 — при нуле фактических физических сравнений clean code —
  `physical.not_run`, поэтому проверка появляется в `What was not done`.
  Частичное ненулевое покрытие сохраняет `physical.as_declared`; findings,
  status, verdict и detail прежние. Verifier 76, check 52.
  Выбор — synthetic Astra xhigh, реализация Codex. Профильный набор: 247 passed;
  Black/Ruff/strict mypy чистые. Итоговое synthetic Astra xhigh review:
  0 substantive findings. Полный offline gate: 3887 passed, 5 skipped,
  38 deselected, exit 0.
  [Проверки и границы](docs/DECISIONOPS_PHYSICAL_OUTCOME_REVIEW_2026-10-02.md).

- 2026-10-02 — при нулевом числе допустимых cost-сравнений clean code —
  `cost.not_run`, поэтому проверка появляется в `What was not done`.
  Частичное ненулевое покрытие сохраняет `cost.within_budget`; findings,
  status, verdict и detail прежние. Verifier 75, check 51.
  Выбор — synthetic Astra xhigh, реализация Codex.
  Полный offline gate: 3871 passed, 5 skipped, 38 deselected, exit 0;
  Black/Ruff/strict mypy чистые. Итоговое synthetic Astra xhigh review:
  0 substantive findings.
  [Проверки и границы](docs/DECISIONOPS_COST_OUTCOME_REVIEW_2026-10-02.md).

- 2026-10-02 — чистая сводка `dwh.cost` сообщает фактическое покрытие объявленных
  потолков: строки из планов, байты/время из exact-query runtime evidence.
  Неполное и нулевое покрытие названы явно; findings/codes/verdict прежние.
  Verifier 74, check 50. Выбор — synthetic Astra xhigh, реализация Codex.
  Полный offline gate: 3821 passed, 5 skipped, 38 deselected, exit 0;
  docs-as-code: 24 passed. Black/Ruff/strict mypy чистые. Итоговое synthetic
  Astra xhigh review: 0 substantive findings.
  [Проверки и границы](docs/DECISIONOPS_COST_COVERAGE_REVIEW_2026-10-02.md).

- 2026-10-02 — чистая сводка `dwh.security` считает подтверждённые права чтения
  запрошенных колонок и ответы о row policies по вопросам (витрина, колонка времени).
  Частичное и нулевое покрытие названы явно, findings/codes/verdict прежние.
  Verifier 73, check 50. Выбор — synthetic Astra xhigh, реализация Codex.
  Профильный набор: 306 passed; полный offline gate: 3790 passed, 5 skipped,
  38 deselected, exit 0; docs-as-code: 24 passed. Black/Ruff/strict mypy чистые.
  Итоговое synthetic Astra xhigh review: 0 substantive findings.
  [Проверки и границы](docs/DECISIONOPS_SECURITY_COVERAGE_REVIEW_2026-10-02.md).

- 2026-10-02 — чистая сводка `dwh.freshness` считает сравнения по вопросам
  (витрина, колонка времени) и явно называет неполное покрытие. Положительный вывод
  ограничен сравнёнными возрастами; findings, codes и verdict прежние. Verifier 72,
  check 50. Red: 15 failed / 6 passed; связанный набор: 225 passed.
  Полный offline gate: 3763 passed, 5 skipped, 38 deselected, exit 0;
  docs-as-code: 24 passed; Black/Ruff/strict mypy чистые. Выбор и итоговое review —
  synthetic Astra xhigh (0 substantive findings), реализация Codex.
  [Проверки и границы](docs/DECISIONOPS_FRESHNESS_COVERAGE_REVIEW_2026-10-02.md).

- 2026-10-02 — `dwh.physical` считает фактически сравненные типы колонок и заявленные
  свойства layout по каждому вопросу (витрина, колонка времени). Неполное и нулевое
  покрытие названы явно; недоступные и неизвестные факты не подтверждают соответствие.
  Findings, summary codes и verdict прежние; verifier 71, check 50.
  Выбор — synthetic Astra xhigh; реализация Codex, не human/production acceptance.
  Профильный набор 204 passed, docs-as-code 24 passed; Black/Ruff/strict mypy чистые.
  Итог Astra: 0 незакрытых substantive findings. Полный offline gate: 3741 passed,
  1 failed (SQLite timing budget); изолированная диагностика: 1 passed за 1,25 с.
  [Проверки и границы](docs/DECISIONOPS_PHYSICAL_COVERAGE_REVIEW_2026-10-02.md).

- 2026-10-02 — составные canonical ClickHouse write grants (`ALTER ...`,
  `CREATE ...`, `DROP ...`) и `ALL` блокируют grant-based read-only и runtime SQL.
  Evidence сохраняет точные имена; профили readonly=1/2 и partial revokes прежние.
  Источник canonical имён — pinned ClickHouse 24.8.14.39. Версии evidence 69 → 70 /
  49 → 50. Выбор — synthetic Astra xhigh, реализация Codex.
  Red: 26 failed / 15 passed, отдельно ALL — 2 failed. Полный offline pytest:
  3723 passed, 5 skipped, 38 deselected; Ruff, Black и strict mypy чистые.
  Итоговое synthetic Astra review: 0 substantive findings.
  [Проверки и границы](docs/DECISIONOPS_WRITE_GRANTS_REVIEW_2026-10-02.md).

- 2026-10-02 — неполный/неотвеченный список ClickHouse enabled roles не подтверждает
  grant-based read-only и effective column/policy reach. Профиль `readonly=1/2`
  и доказанные write grants сохраняют приоритет; иначе доступ и runtime блокируются.
  Unknown reach даёт security REVIEW, без ложных physical absence/classification claims.
  Версии evidence 68 → 69 / 48 → 49. Выбор — synthetic Astra xhigh, реализация Codex.
  Red: 23 failed / 11 passed. Human/production acceptance не заявляется.
  Полный offline pytest с duckdb: 3679 passed, 5 skipped, 38 deselected, exit 0;
  Ruff, Black и strict mypy чистые. Итоговое synthetic Astra review: 0 substantive findings.
  [Проверки и границы](docs/DECISIONOPS_ENABLED_ROLES_REVIEW_2026-10-02.md).

- 2026-10-02 — missing/NULL поля PostgreSQL-family write-privilege ответов не
  подтверждают read-only. `dwh.access` — BLOCK, exact runtime SQL не выполняется;
  evidence называет права и витрину, доказанные grants сохраняют приоритет.
  Версии evidence 67 → 68 / 47 → 48. Выбор и synthetic review — Astra xhigh,
  реализация Codex. Red: 74 failed / 4 passed; связанная группа — 451 passed.
  Ruff, Black и strict mypy чистые; итоговое review — 0 substantive findings.
  Полный offline pytest с duckdb: 3645 passed, 5 skipped, 38 deselected, exit 0.
  [Проверки и границы](docs/DECISIONOPS_PARTIAL_PRIVILEGES_REVIEW_2026-10-02.md).

- 2026-10-02 — частичный PostgreSQL-family identity-ответ, NULL в поле или пустое имя
  не подтверждают read-only. Известные имя и положительные superuser/BYPASSRLS
  сохраняются; false bypass требует двух известных отрицательных флагов, иначе null.
  Неполный identity блокирует `dwh.access` и runtime SQL; write-privilege вопросы
  не выполняются, evidence называет неотвеченные поля. Доказанный superuser сохраняет
  прежнее writable evidence даже при неполной строке. SQL-вопросы и normalizer прежние.
  Точные непустые имя и subject refs сохраняют пробелы quoted-идентификаторов;
  исправлены только два локальных поля, общий `NonBlankText` прежний.
  Версии evidence 66 → 67 / 46 → 47. Выбор — Astra xhigh, тесты — Luna xhigh,
  реализация и независимая проверка — Codex. Red: 40 failed / 40 passed;
  связанная группа до quoted-name поправки — 353 passed; quoted-name red — 20 failed.
  Итоговый полный offline pytest с duckdb: 3567 passed, 5 skipped, 38 deselected;
  Ruff, Black и strict mypy чистые. Synthetic Astra xhigh review — 0 substantive findings:
  [review и границы](docs/DECISIONOPS_PARTIAL_IDENTITY_REVIEW_2026-10-02.md).

- 2026-10-02 — отказ и пустой ответ PostgreSQL-family identity-вопроса:
  роль `unknown`, `read_only=false`, `bypasses_row_security=null`; write-privilege
  вопросы и exact runtime SQL не выполняются. `dwh.access` — BLOCK через прежний
  `access.role_writable`, detail описывает неподтверждённый read-only без утверждения
  о способности неизвестной роли писать. Ответившие роли и ClickHouse сохраняют
  правила; ошибки соединения пробрасываются. Версии evidence 65 → 66 / 45 → 46.
  Выбор — synthetic Astra xhigh по ADR 0006, реализация Codex. Red:
  16 failed / 16 passed; связанная группа — 305 passed. Полный offline pytest
  с duckdb: 3499 passed, 5 skipped, 38 deselected; Ruff, Black и strict mypy чистые.
  [Итоговое synthetic Astra review](docs/DECISIONOPS_ROLE_IDENTITY_REVIEW_2026-10-02.md):
  0 substantive findings; human/production acceptance не заявляется.

- 2026-10-02 — отказ или пустой ответ PostgreSQL-family табличного write-privilege
  вопроса не подтверждает read-only при отрицательных колоночных ответах.
  `dwh.access` — BLOCK, exact runtime SQL не выполняется; evidence называет
  неотвеченные табличные INSERT/UPDATE/DELETE/TRUNCATE и витрину. Колоночный вопрос
  продолжается, доказанные grants сохраняют своё evidence. API, набор SQL,
  superuser и ClickHouse без изменений. Версии evidence 64 → 65 / 44 → 45.
  Выбор среза — synthetic Astra xhigh по ADR 0006, реализация Codex.
  Red: 2 failed / 4 passed; связанная группа — 188 passed. Полный offline pytest
  с duckdb — 3467 passed, 5 skipped, 38 deselected; Ruff, Black и package-wide
  strict mypy чистые. [Synthetic Astra xhigh review](docs/DECISIONOPS_TABLE_PRIVILEGES_REVIEW_2026-10-02.md):
  0 substantive findings; не human/production acceptance.

- 2026-10-02 — PostgreSQL-family role probe спрашивает колоночные INSERT/UPDATE
  через `has_any_column_privilege` по всей витрине скоупа. Только колоночный
  write-grant теперь делает роль writable: `dwh.access` — BLOCK с
  `access.role_writable`, exact runtime SQL не исполняется. Табличный grant
  сохраняет одно evidence; read-only evidence называет табличные и колоночные
  права. Отказ или пустой ответ колоночного privilege-вопроса не подтверждает
  read-only и также блокирует доступ и exact runtime SQL. Superuser и ClickHouse
  сохраняют свои правила.
  Версии evidence: `VERIFIER_VERSION` 63 → 64, `CHECK_VERSION` 43 → 44;
  исторические bundles не переписаны. Реализовано `gpt-6.1-sol` high через
  MyFlow `sol-high` (T-33), завершено и независимо проверено Codex.
  Новые refused/empty регрессии: red 3 failed / 1 passed; полный offline pytest
  с duckdb — 3461 passed, 5 skipped, 38 deselected. Ruff, Black и package-wide
  strict mypy чистые. [Synthetic Astra xhigh review](docs/DECISIONOPS_COLUMN_PRIVILEGES_REVIEW_2026-10-02.md):
  P1 исправлен, итог 0 substantive findings; не human/production acceptance.

- 2026-10-01 — `results.reconcile` сравнивает нечисловое значение по виду:
  бесконечности одного знака равны при разных записях, включая float за пределом
  binary64, без float-допуска. NaN кандидата против числа или бесконечности —
  `value.changed`; ожидаемый NaN, включая decimal `sNaN`, не решает ячейку:
  `value.expected_nan`, `insufficient` / REVIEW, если больше ничего не доказано.
  Доказанное различие в membership, другом value, порядке или candidate aggregate
  сохраняет `different`; ожидаемые NaN перечисляются после value/order differences,
  перед candidate aggregate issues. NULL сохраняет свои коды; ячейка любого business
  key решается сопоставлением строки, float key `nan` больше не сравнивается сам с собой.
  Версии evidence: `VERIFIER_VERSION` 62 → 63, `CHECK_VERSION` 42 → 43;
  исторические bundles не переписаны.
  Реализовано `gpt-6.1-sol` high через MyFlow `sol-high`.
  Red на HEAD: 58 новых случаев падали, 77 контролей проходили; после правки все 135
  и связанный focused gate прошли: 403 passed. Gate-level тест проверяет REVIEW
  с `result.not_equivalent`, `insufficient` / `value.expected_nan` в payload и отсутствие
  sealed `result.equivalent` claim. Полный offline pytest: 3424 passed, 20 skipped,
  38 deselected; strict mypy — 0 ошибок в 120 модулях, Ruff и Black чистые.
  Срез — альтернатива №2 синтетического Astra xhigh выбора, давшего T-29; политику
  определил оркестратор по делегированию владельца. Итоговое ревью Astra xhigh:
  0 findings. Живые факты сняты на Mac одноразовыми ClickHouse 24.8.14.39 и
  PostgreSQL 16.15 (стенды удалены); гейт не следует SQL-семантике ни одного движка.
  Human/production acceptance не заявляется.

- 2026-10-01 — `results.reconcile` сравнивает точную разность конечных binary64-значений
  с точным объявленным decimal-допуском: absolute напрямую, relative через умножение
  на max(|a|, |b|), без приближения разности, отношения или допуска. Превышение,
  скрытое погрешностью binary64, теперь даёт `value.changed`; точное попадание
  в relative-допуск, отвергавшееся приближённым отношением, теперь даёт `equivalent`.
  Перенос float-колонки
  сохраняет её допуск; signed zero и разные записи одного binary64-значения равны.
  Absolute-допуск за пределом binary64 больше не делает бесконечность равной числу
  или бесконечности другого знака (прежде он сам становился бесконечностью).
  Версии evidence: `VERIFIER_VERSION` 61 → 62, `CHECK_VERSION` 41 → 42;
  исторические bundles не переписаны.
  Реализовано `gpt-6.1-sol` high через MyFlow `sol-high`.
  Red на HEAD: 9 новых случаев падали, 17 контролей проходили; после правки 26 новых
  случаев и связанный focused gate прошли: 268 passed. Gate-level воспроизводитель
  переводит `results.reconcile` из PASS в BLOCK, валидный контроль остаётся PASS.
  Полный offline pytest: 3289 passed, 20 skipped, 38 deselected; strict mypy — 0 ошибок
  в 120 модулях, Ruff и Black чистые. Срез — альтернатива №1 синтетического
  Astra xhigh выбора, давшего T-29. Итоговое ревью Astra xhigh: проход 1 —
  F-T-31-1 (допуск за пределом binary64) отклонена воспроизведением и описана в доках,
  проход 2 подтвердил отклонение: 0 actionable P1/P2.
  Живая БД не запускалась; human/production acceptance не заявляется.

- 2026-10-01 — environment-чтения записывают `event_time_column` своего вопроса
  (витрина, колонка времени). Общий resolver атрибутирует чтение для access,
  freshness, physical, security, cost и допуска runtime probe; порядок не скрывает
  протухшие часы / denied column и не сообщает свежие часы протухшими.
  Неоднозначное чтение — `access.reading_unattributed` (review); legacy без колонки
  сохраняет grading только для единственных часов и единственного чтения витрины.
  `freshness.stale` называет часы; evidence facts несут `event_time_column`.
  Версии evidence: `VERIFIER_VERSION` 60 → 61, `CHECK_VERSION` 40 → 41.
  Реализовано `gpt-6.1-sol` high через MyFlow `sol-high`.
  Red на HEAD: 37 новых случаев падали, 7 контролей проходили; после правки
  44 новых случая и связанный focused gate прошли: 365 passed. Новые offline-тесты
  покрывают обе семьи движков, неизменность SELECT replay, gate-level REVIEW/BLOCK
  и bundle facts. Полный offline pytest: 3263 passed, 20 skipped, 38 deselected;
  strict mypy — 0 ошибок в 120 модулях, Ruff и Black чистые.
  Выбор среза и итоговое ревью — синтетические Astra xhigh: 0 actionable P1/P2.
  Живая БД не запускалась; human/production acceptance не заявляется.

- 2026-10-01 — `results.reconcile` отождествляет integer/decimal business key
  и `group_by` проверок `share_sum` / `partition_conservation` по числу.
  Другой scale находит строку; две записи одного числа дают `insufficient` /
  `key.not_unique`; группы на разных scales объединяются. Non-finite,
  нечисловые значения, NULL и ключи других типов сохраняют записанную форму.
  Детали используют ключ своего result; чтение по имени и неизменность входов сохранены.
  Версии evidence: `VERIFIER_VERSION` 59 → 60, `CHECK_VERSION` 39 → 40.
  Реализовано `gpt-6.1-sol` high через MyFlow `sol-high`.
  Red: 24 новых случая падали, 11 контролей проходили.
  После правки 35 новых случаев и связанный focused gate прошли: 242 passed.
  Полный offline pytest: 3219 passed, 20 skipped, 38 deselected; strict mypy —
  0 ошибок в 120 модулях, Ruff и Black чистые.
  Срез — альтернатива №2 того же синтетического Astra xhigh выбора; итоговое
  ревью Astra xhigh: 0 actionable P1/P2. Живая БД не запускалась;
  human/production acceptance не заявляется.

- 2026-10-01 — `results.reconcile` после schema step читает supplied columns
  по имени: values, business key, контрактный порядок строк и ключ строки
  failed aggregate check. Permuted named values больше не дают ложный pass
  или block; NULL и float tolerance следуют своей колонке, ResultSet не меняются.
  Native capture сохраняет порядок колонок asset; исторические bundles не переписаны.
  Версии evidence: `VERIFIER_VERSION` 58 → 59, `CHECK_VERSION` 38 → 39.
  Реализовано `gpt-6.1-sol` high через MyFlow `sol-high`. Red-green: на comparator
  из HEAD 22 новых теста падали, включая два gate-level; после правки 29 новых
  регрессий прошли, связанный gate: 269 passed.
  Полный offline pytest: 3184 passed, 20 skipped, 38 deselected; strict mypy —
  0 ошибок в 120 модулях, Ruff и Black чистые.
  Выбор среза и итоговое ревью — синтетические Astra xhigh: 0 actionable P1/P2.
  Живая БД не запускалась; human/production acceptance не заявляется.

- 2026-10-01 — table extraction и SQL guard исключают bare-ссылку на CTE,
  только если query-владелец объявления входит в ancestors этой ссылки.
  Вложенная или соседняя CTE больше не скрывает внешнюю физическую таблицу
  от governance/raw-SQL RBAC и не даёт исключение для denylisted bare name.
  Наследование внешнего WITH, root WITH над UNION, whole-AST extraction,
  правила сравнения имён и консервативные same-WITH/recursive/scalar правила сохранены.
  Версии evidence: 57 → 58 / 37 → 38. Red: 19 падений, 33 passed;
  после Luna xhigh fix: 52 passed, включая 34 новые регрессии.
  Независимый полный offline pytest: 3154 passed, 21 skipped, 38 deselected;
  strict mypy — 0 ошибок в 120 модулях, Ruff и Black чистые.
  Выбор среза и итоговое ревью — синтетические Astra xhigh: 0 actionable P1/P2.
  Живая БД не запускалась; human/production acceptance не заявляется.
- 2026-09-30 — ClickHouse table extraction и SQL guard исключают bare-ссылку
  на CTE только по точному регистру. `WITH A … FROM a` больше не скрывает
  физическую таблицу от governance и raw-SQL RBAC; mismatch не обходит denylist.
  Точное совпадение, whole-AST область, lexical resolution и PostgreSQL сохраняют
  правила. Версии evidence: 56 → 57 / 36 → 37.
  Luna xhigh: red-green 9 падений, затем 59 passed; 18 новых регрессий,
  соседний ClickHouse/raw-SQL gate: 144 passed.
  Независимый полный offline pytest: 3120 passed, 21 skipped, 38 deselected;
  strict mypy — 0 ошибок в 120 модулях, Ruff и Black чистые.
  Выбор среза и итоговое ревью — синтетические Astra xhigh: 0 actionable P1/P2.
  Живая БД не запускалась; human/production acceptance не заявляется.
- 2026-09-30 — governance ClickHouse учитывает точный набор достижимых ключей
  `JOIN … USING`: непрочитанные named CTE и scalar `WITH` больше не добавляют
  эту причину partial-field advisory; при соседнем живом JOIN остаются его ключи.
  Используется общий с chart impact обход гейтов CTE; общий extractor сохраняет
  whole-AST область. Версии evidence: 55 → 56 / 35 → 36.
  Luna xhigh: red-green 3 падения, затем 20 новых регрессий; связанный gate: 262 passed.
  Независимо: 20 passed, strict mypy — 0 ошибок в 120 модулях, Ruff и Black чистые.
  Полный offline pytest: 3102 passed, 21 skipped, 38 deselected.
  Итоговое синтетическое Astra xhigh review: 0 actionable P1/P2.
  Живой ClickHouse не запускался; human/production acceptance не заявляется.
- 2026-09-30 — governance ClickHouse учитывает `COLUMNS(...)` только в достижимой
  части SQL: непрочитанные named CTE и scalar `WITH` больше не оставляют ложную
  причину partial-field advisory, живой селектор её сохраняет. Общий extractor
  остаётся whole-AST; используется прежний общий с chart impact обход гейтов CTE.
  Regex не раскрывается по snapshot-полям; именованные чтения, неизвестные
  таблицы/поля, звёзды, `JOIN … USING`, PostgreSQL и chart matcher сохраняют правила.
  Версии evidence: 54 → 55 / 34 → 35. Реализация Luna xhigh: red-green 2 падения,
  затем 17 новых регрессий. Независимый целевой gate: 242 passed; полный offline
  pytest: 3082 passed, 21 skipped, 38 deselected; strict mypy: 0 ошибок в 120 модулях,
  Ruff и Black чистые. Выбор среза — синтетическое Astra xhigh review; итоговое
  синтетическое Astra xhigh review: 0 actionable P1/P2. Живой ClickHouse не запускался;
  human/production acceptance не заявляется.
- 2026-09-30 — общий обход достижимости ClickHouse связывает named CTE и scalar
  `WITH` с точным регистром: внутреннее `a` не скрывает внешнее `A`, а ссылка
  на `a` не открывает непрочитанное соседнее `A`. Звёзды, `COLUMNS`, `JOIN … USING`,
  dictionary- и Join-table-вызовы используют одно правило, включая named CTE в `IN`.
  Точное затенение, self-reference, дубликаты, квалификаторы и `SETTINGS` сохранены.
  Версии evidence: 53 → 54 / 33 → 34. Red-green: 15 новых падений и одно в прежнем
  граничном тесте; 28 новых регрессий прошли, связанный gate: 610 passed.
  Полный offline pytest: 3065 passed, 21 skipped, 38 deselected; strict mypy:
  0 ошибок в 120 модулях, Ruff и Black чистые.
  Синтетическое Astra xhigh review: 0 actionable P1/P2. ClickHouse 24.8.14.39 на Mac
  подтвердил различие регистра и точное затенение в `FROM`/`IN`; стенд удалён.
  Human/production acceptance не заявляется.
- 2026-09-30 — governance ClickHouse сообщает `it selects *` только для звезды,
  читающей колонки в достижимой части SQL: непрочитанные named CTE и scalar
  `WITH` больше не создают эту причину advisory. Общий с chart impact обход
  гейтов CTE перенесён в SQL guard без изменения алгоритма; общий extractor
  сохраняет whole-AST `ColumnReads.starred` и не добавляет обход в chart matcher.
  Именованные чтения, неизвестные таблицы и поля, `COLUMNS`, `JOIN … USING` и
  PostgreSQL сохраняют правила. Версии evidence: 52 → 53 / 32 → 33.
  Red-green: 4 падения, затем 17 passed; расширенные регрессии: 23 passed,
  связанный ClickHouse/chart gate: 498 passed. Полный offline pytest:
  3037 passed, 21 skipped, 38 deselected; strict mypy: 0 ошибок в 120 модулях,
  Ruff и Black чистые. Синтетическое Astra xhigh review: 0 actionable P1/P2;
  живой ClickHouse не запускался, human/production acceptance не заявляется.
- 2026-09-30 — PostgreSQL `CAST(expr AS type)` и `expr::type` сохраняют
  известное сильное имя выхода колонки, пяти агрегатов или оконного
  `ROW_NUMBER` для целого `ORDER BY`/`DISTINCT ON` и левой ветки set operation.
  Аргументы и оконные поля остаются чтениями; `AS`, унарный `+`,
  `GROUP BY` и ClickHouse сохраняют границы. Нормализованные sqlglot
  функции `Cast` не наследуют имя аргумента. Версии evidence: 51 → 52 / 31 → 32.
  Red-green подтвердил исходные ложные чтения и найденный Astra пропуск;
  целевой gate: 539 passed, включая 40 CAST-регрессий. Полный offline pytest:
  3014 passed, 21 skipped, 38 deselected; strict mypy: 0 ошибок в 120 модулях,
  Ruff и Black чистые. PostgreSQL 16.15 на Mac подтвердил контракт casts,
  унарный `+` и границу пользовательских функций; временные кластеры удалены.
  Итоговое синтетическое Astra xhigh review: 0 actionable P1/P2;
  human/production acceptance не заявляется.
- 2026-09-29 — PostgreSQL `ROW_NUMBER() OVER (…)` без явного alias даёт
  неявный выход `row_number` для целого `ORDER BY`/`DISTINCT ON` и
  сортировки set operation по левой ветке; поля `PARTITION BY` и внутреннего
  `ORDER BY` остаются чтениями. `VERIFIER_VERSION` 50 → 51,
  `CHECK_VERSION` 30 → 31: прежний reviewed-запрос мог получить ложный
  governance finding. Red-green: исходный тест упал на ложном
  `dm.sales_daily.row_number`, затем 15 граничных тестов прошли; целевой
  gate: 497 passed, полный offline pytest: 2974 passed, 21 skipped,
  38 deselected. Strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые.
  Итоговое синтетическое Astra xhigh review: 0 actionable P1/P2; живой
  PostgreSQL не запускался, human/production acceptance не заявляется.
- 2026-09-29 — PostgreSQL `COUNT`/`SUM`/`AVG`/`MIN`/`MAX` с `OVER`,
  включая `FILTER (WHERE …)` внутри оконного вызова, сохраняют неявное имя
  выхода для целого `ORDER BY`/`DISTINCT ON` и сортировки set operation по
  левой ветке. Аргументы, предикат `FILTER`, `PARTITION BY` и внутренний
  `ORDER BY` остаются чтениями. `VERIFIER_VERSION` 49 → 50,
  `CHECK_VERSION` 29 → 30: прежний reviewed-запрос мог получить ложный
  governance finding. Red-green: исходный тест упал на ложном `dm.sales_daily.sum`;
  целевой gate: 482 passed, полный offline pytest: 2959 passed, 21 skipped,
  38 deselected. Strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые.
  Итоговое синтетическое Astra xhigh review: 0 actionable P1/P2; живой
  PostgreSQL не запускался, human/production acceptance не заявляется.
- 2026-09-29 — пустой ClickHouse `map()` в operator SQL больше не считается
  шаблоном Superset `{{ … }}`: ложный `chart.sql_partial` уходит, а настоящий
  шаблон остаётся частично прочитанным и рядом с `map()`. Явные чтения колонок
  сохраняются. `VERIFIER_VERSION` 48 → 49, `CHECK_VERSION` остаётся 29;
  меняется chart evidence, но не грейдинг проверок. Red-green подтвердил ложные
  `chart.sql_partial` и `chart.metric_unread`; целевой gate: 161 passed. Полный
  offline pytest: 2941 passed, 21 skipped, 38 deselected; strict mypy: 0 ошибок
  в 120 модулях, Ruff и Black чистые. Синтетическое Astra xhigh review:
  0 actionable P1/P2. Живой ClickHouse не запускался; это не human/production
  acceptance.
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
  ищет этот селектор во всём AST; `VERIFIER_VERSION` 32 → 33, `CHECK_VERSION` остаётся 18. Полный
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
