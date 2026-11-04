# PostgreSQL current role without LOGIN — synthetic review

**Дата:** 2026-10-03
**Срез:** T-39; verifier 84 / check 60
**Reviewer:** `gpt-6-astra`, reasoning `xhigh`
**Итог:** первый проход — 0 findings.

Модельная оценка не является human/design-partner acceptance, измерением времени
человека или production acceptance. Контракт:
[environment access](../specs/environment-access.md).

Identity-вопрос PostgreSQL-семейства читал superuser-флаг текущей роли из `pg_user` —
view, который перечисляет только роли с LOGIN. Текущая роль сессии входить не обязана:
role default (`ALTER ROLE … SET role = '…'`) ставит сессию на роль без LOGIN сразу при
подключении. Одноразовый кластер PostgreSQL 16.15 на Mac (удалён после прогона) показал:

- сессия `sess_a` с role default на select-only `reader_only NOLOGIN`: `current_user` —
  `reader_only`, `session_user` — `sess_a`; строки этой роли в `pg_user` нет, в `pg_roles`
  она одна; прежний вопрос отвечает `superuser` NULL, `pg_roles.rolsuper` — false;
  `has_table_privilege` и `has_any_column_privilege` на `INSERT` отрицательны, сам
  `INSERT` отклонён (`permission denied`);
- сессия на `su_nologin NOLOGIN SUPERUSER`: прежний вопрос отвечает NULL, `rolsuper` —
  true, `INSERT` проходит;
- сессия на `bypass_nologin NOLOGIN BYPASSRLS`: superuser-флаг — NULL из `pg_user`
  и false из `pg_roles`; `rolbypassrls` — true в обоих вариантах вопроса, потому что
  этот флаг и прежде читался из `pg_roles`;
- роли с LOGIN (`sess_d` без прав записи, `sess_e` superuser): оба каталога отвечают
  одинаково.

Прежний гейт блокировал первую сессию: `dwh.access` — block с `access.role_writable`
и evidence о неотвеченном superuser status, хотя роль только читает. Это ложный отказ,
не пропуск: fail-closed правило о NULL сработало на ответе, который был пробелом вопроса,
а не движка. NOLOGIN superuser блокировался тем же текстом и superuser назван не был.

Теперь identity-вопрос читает `pg_roles.rolsuper` тем же подзапросом по `current_user`,
каким читается BYPASSRLS; псевдонимы колонок и разбор ответа прежние, `pg_user` не читает
ни один вопрос. Select-only роль без LOGIN грейдится по своим write-привилегиям и по
привилегиям ролей, которые session user достигает через `SET ROLE` (в их числе сам
session user): она остаётся read-only и сохраняет exact runtime SQL. Session user
с правом записи за такой ролью по-прежнему блокирует доступ через вопрос о достижимых
ролях. NOLOGIN superuser получает прежнее superuser evidence, block и короткий путь
без privilege-вопросов и runtime SQL. Отказ, пустой, частичный и NULL identity-ответ
сохраняют правила: движок, который действительно отвечает NULL на superuser-флаг,
блокируется как раньше.

Проход Astra сверил реализацию с durable spec и требованиями задачи: точный текст
identity-вопроса, сохранность псевдонимов, BYPASSRLS-подзапроса и разбора ответа; версии
evidence и их исторические комментарии; отказ и NULL identity-ответа, BYPASSRLS, контроли
ролей с LOGIN, точное evidence и отсутствие пишущих SQL; сдвиг четырёх replay-digest
при прежнем числе вопросов. Собственная выборка ревьюера: 120 passed; ещё двенадцать
прогонов `collect`, runtime и `check_access` по четырём именам движка подтвердили, что
select-only роль без LOGIN допускается, а NOLOGIN superuser и пишущий session user
блокируются. Полный набор задачи, strict mypy и red на HEAD ревьюер не повторял.
Findings нет.

Реализация — `gpt-6.1-sol` medium через MyFlow `sol-high`: назначенный исполнитель
GLM-5.3 xhigh был в недельном лимите z.ai, и контроллер передал срез следующему кандидату.
Первый запуск закончился вопросом к постановке: проверка «ни один вопрос не содержит
`pg_has_role`» противоречила существующему вопросу к `pg_policy`. Оркестратор заменил
её на «вопрос о достижимых ролях не задан» и продолжил тот же запуск.

Независимая проверка оркестратора:

- Новый модуль `tests/test_postgres_nologin_identity.py` против копии `auto_bi` из HEAD:
  24 failed / 20 passed (проходят контроли ролей с LOGIN, отказа и NULL identity-ответа
  и запрета записи); на рабочем дереве — 44 passed. Двойник движка отвечает как живой
  сервер: NULL на superuser-флаг, когда вопрос читает `pg_user`, и флаг роли иначе.
- Четыре replay-digest семейства PostgreSQL в `tests/test_environment_question_identity.py`
  сдвинулись вместе с текстом вопроса. Те же четыре ветки, проигранные против копии
  `auto_bi` из HEAD и против рабочего дерева, отличаются ровно одним вопросом — identity;
  число вопросов (20, 8, 16, 18) прежнее, три ClickHouse-ветки совпадают целиком.
- Fast gate MyFlow: Ruff, Black и strict mypy чистые; focused — 996 passed,
  набор задачи — 707 passed.
- Полный offline pytest с duckdb: 4131 passed, 4 skipped, 38 deselected, exit 0.

Границы: идентичность читается по `current_user`; role default и `SET ROLE` внутри
сессии меняют именно его, а session user учитывается вопросом о достижимых ролях.
По документации в ядре PostgreSQL до 9.5 — на 9.4 построены Greenplum 6 и Greengage 6 —
колонки `rolbypassrls` нет; identity-вопрос на таком ядре живьём не проверялся ни до,
ни после этого среза, и срез его чтение BYPASSRLS не менял. `SECURITY DEFINER`-функции,
BYPASSRLS достижимой роли и права администрирования ролей (`CREATEROLE`, `ADMIN OPTION`)
остаются границами PostgreSQL-семейства.
