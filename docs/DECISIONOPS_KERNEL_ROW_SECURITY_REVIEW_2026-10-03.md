# PostgreSQL-family kernel without row-level security — synthetic review

**Дата:** 2026-10-03
**Срез:** T-41; verifier 86 / check 62
**Reviewer:** `gpt-6-astra`, reasoning `xhigh`
**Итог:** первый проход — 0 findings.

Модельная оценка не является human/design-partner acceptance, измерением времени
человека или production acceptance. Контракт:
[kernel without row-level security](specs/postgres-kernel-row-security.md).

v2-скоуп называет Greenplum и Greengage рядом с PostgreSQL; Greenplum 6 и Greengage 6
работают на ядре PostgreSQL 9.4. Row-level security появилась в PostgreSQL 9.5
(`server_version_num` 90500): до неё нет ни `pg_roles.rolbypassrls`, ни `pg_policy`, ни
`pg_class.relrowsecurity`. Шаг 7 спрашивает все три: identity-вопрос читает
`rolbypassrls`, а каждая витрина получает вопросы о row policies и о переключателе
row security.

Живые факты, ядро PostgreSQL 9.4.26 (ванильная сборка из исходников, одноразовый кластер
на Mac, psycopg 3.3.4, 2026-10-03; стенд Greenplum или Greengage не запускался):

- identity-вопрос отвечает `UndefinedColumn`, SQLSTATE `42703`, `column "rolbypassrls"
  does not exist`. Это не отказ, поэтому `collect` на HEAD `03da0a3` поднимал исключение
  на первом же вопросе — для select-only роли, для `NOINHERIT`-члена пишущей группы и для
  superuser одинаково; шаг окружения не давал ни одного факта;
- `server_version_num` — целое `90426`; identity без подзапроса `rolbypassrls` отвечает
  именем и superuser-флагом;
- вопрос о row policies — `42P01` (`pg_policy` нет), вопрос о переключателе — `42703`
  (`relrowsecurity` нет); `ALTER TABLE … ENABLE ROW LEVEL SECURITY` и
  `CREATE ROLE … BYPASSRLS` — синтаксические ошибки (`42601`);
- остальные вопросы коллектора отвечают: табличные и колоночные write-права, роли,
  достижимые через `SET ROLE` (по членству), и их права, наличие витрины, чтение колонок,
  readable, reading, типы колонок, `EXPLAIN (FORMAT JSON)` и точный runtime `SELECT` под
  `SET TRANSACTION READ ONLY` и `SET LOCAL statement_timeout`;
- `NOINHERIT`-член группы с `INSERT` получил отказ на собственный `INSERT` (`42501`),
  `SET ROLE` в группу и тот же `INSERT` ошибки не дали; вопрос достижимости назвал группу.

Контроль на PostgreSQL 16.15 (тот же сценарий): вопросы и их порядок прежние, версия не
спрашивается, `server_version_num` в фактах — null.

Теперь коллектор задаёт identity-вопрос как раньше и только на undefined-column ошибке
(SQLSTATE `42703`, либо текст `column "rolbypassrls" does not exist` у драйвера без кода)
спрашивает `server_version_num`. Целое неотрицательное значение ниже `90500` означает ядро
без row-level security: identity спрашивается без `BYPASSRLS`, роль грейдится по прежним
правилам (отказ и пустой ответ — `unknown`, частичный ответ называет неотвеченные поля без
`BYPASSRLS`, superuser — прежнее evidence, иначе прежние privilege- и reach-вопросы),
`bypasses_row_security` — null, вопросы о row policies и переключателе не задаются
(`row_policies=[]`, `row_security_enabled=null`), версия записана в
`EnvironmentFacts.server_version_num`, а чистая сводка `dwh.security` с покрытием называет
ядро; сводка окружения, где ни одна витрина не прочитана, сохраняет прежний текст.
Во всех остальных случаях — версия не отвечена, не целое, отрицательная, `90500` и выше —
исходная ошибка поднимается как прежде. Ядро, ответившее на identity-вопрос, получает
ровно прежние вопросы в прежнем порядке.

Срез выбран оркестратором по v2-скоупу; в выборе synthetic Astra xhigh от 2026-10-03 он
назван альтернативой №2 (P2). Версия спрашивается после падения identity-вопроса, а не
первой: иначе у каждого ядра менялся бы набор вопросов и сдвигались бы replay digests.

Проход Astra сверил реализацию с durable spec и требованиями задачи: identity без
`BYPASSRLS` после undefined column, проверку версии и сохранение исходных исключений,
правила роли и допуск runtime-запроса; каждый путь возврата витрины — требуемые факты
и отсутствие вопросов к RLS-каталогам на ядре без row-level security; версии evidence,
отклонение отрицательной версии и согласованность документации. Собственная выборка
ревьюера: read-only pytest — 164 passed, 6 deselected, все семь прежних SQL replay
digests подтверждены; пять веток gate с хранилищем в памяти — точный detail чистой
сводки и сводки `security.not_run`, прежний review при неполных наблюдениях,
`server_version_num` во всех пяти environment payloads; три проверки обработки
настоящих исключений установленного psycopg 3.3.4. Полный набор задачи, шесть тестов
с файловыми артефактами, red на HEAD и живой PostgreSQL ревьюер не повторял.
Findings нет.

Реализация — `gpt-6.1-sol` medium через MyFlow `sol-high`: назначенный исполнитель
GLM-5.3 xhigh был в недельном лимите z.ai, и контроллер передал срез следующему кандидату.
Оркестратор вынес identity-логику из `collect` в хелперы `_pg_identity`,
`_pg_undefined_bypassrls` и `_pg_kernel_version` и вернул сводке окружения без
читаемых витрин прежний текст: писатель провёл её через сводку с покрытием, чего
задача не заказывала; spec и тест закрепляют прежний текст.

Независимая проверка оркестратора:

- Новый модуль `tests/test_postgres_kernel_row_security.py` против копии `auto_bi` из HEAD:
  151 failed / 12 passed (проходят контроли, где исходная ошибка поднимается как и
  раньше: потерянное соединение, другой текст без кода состояния, другой код состояния);
  на рабочем дереве — 163 passed.
- До реализации правило было собрано прототипом на копии пакета: 17 модулей окружения
  на копии с HEAD `40e7a80` — 865 passed; прогон полного набора против прототипа
  остановлен по нехватке памяти на 84 % без единого падения и не повторялся.
- Итоговый код вживую (Mac, psycopg 3.3.4, одноразовые кластеры, удалены). Ядро
  PostgreSQL 9.4.26: select-only роль — read-only, 12 вопросов, runtime-запрос
  исполнен; `NOINHERIT`-член пишущей группы — `access.role_writable` с evidence
  `granted INSERT on dm.sales_daily through SET ROLE writer`, 13 вопросов; superuser —
  прежнее superuser-evidence, 9 вопросов; у всех трёх `server_version_num` 90426 и
  `bypasses_row_security` null. HEAD `03da0a3` на том же ядре поднимал `UndefinedColumn`
  после первого вопроса для каждой из трёх ролей. PostgreSQL 16.15: те же роли дают
  прежние факты и прежние 12, 13 и 9 вопросов, версия не спрашивается.
- Fast gate MyFlow: Ruff, Black и strict mypy чистые; focused — 1195 passed, набор задачи — 739 passed.
- Полный offline pytest с duckdb: 4313 passed, 4 skipped, 38 deselected, exit 0.

Границы: живые факты сняты на ванильном ядре PostgreSQL 9.4.26 — собственные каталоги
Greenplum и Greengage (distribution policy, уровни партиций) и их вывод `EXPLAIN` этим
срезом не покрыты. PostgreSQL 9.5 и 9.6 имеют row-level security, но не
`pg_policy.polpermissive` (PostgreSQL 10): на них вопрос о row policies по-прежнему падает
с undefined column, и коллектор поднимает ошибку. Ядро старше 9.4 не покрыто:
`to_regclass` появился в 9.4. Версия спрашивается только после падения identity-вопроса,
поэтому факты ядра, ответившего на него, версию не записывают.
