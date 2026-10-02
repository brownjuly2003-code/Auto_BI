# PostgreSQL SET ROLE reach — synthetic review

**Дата:** 2026-10-02
**Срез:** T-34; verifier 79 / check 55
**Reviewer:** `gpt-6-astra`, reasoning `xhigh`
**Итог:** F-T-34-1 исправлена; второй проход — 0 новых findings.

Модельная оценка не является human/design-partner acceptance, измерением времени
человека или production acceptance. Контракт:
[environment access](specs/environment-access.md).

PostgreSQL-функции привилегий отвечают только за `current_user` и унаследованные
им права. Роль, в которую session user может переключиться через `SET ROLE`,
могла скрыть право записи. На одноразовых кластерах PostgreSQL 16.15 роль
с `NOINHERIT` отвечала read-only, но писала после `SET ROLE` в пишущую группу —
напрямую, через промежуточную роль, в группу только с `UPDATE (revenue)`
или в `pg_write_all_data`; так же писал session user за role default после
`SET ROLE NONE`. Теперь один вопрос находит достижимые роли,
ещё один на витрину спрашивает их табличные и колоночные write-права. Достижимая
пишущая роль, достижимый superuser или неотвеченный вопрос блокируют `dwh.access`
через `access.role_writable`; exact runtime SQL не выполняется.

Первый проход Astra выявил F-T-34-1 (severity 9): вопрос достижимости исключал роли,
которые текущая роль наследует (`pg_has_role(current_user, …, 'USAGE')`), а членство
не передаёт атрибут `SUPERUSER`. Оркестратор воспроизвёл находку на одноразовом
PostgreSQL 16.15: наследующий член `SUPERUSER`-группы без собственных grants отвечал
false на все write-вопросы, вопрос достижимости возвращал 0 строк, а `SET ROLE`
в группу и следом `INSERT` писали. При `WITH INHERIT TRUE, SET FALSE` вопрос даёт
0 строк, и переключение отклоняется. Исправление оставляет superuser-роли в вопросе:
`(r.rolsuper OR NOT pg_has_role(current_user, r.oid, 'USAGE'))`.

В том же ремонте закрыто наблюдение оркестратора: строка ответа о правах с NULL
или пустым именем больше не отвечает за роль, буквально названную `None`.

Второй проход Astra судил только дельту ремонта и признал F-T-34-1 исправленной:
фильтр сохраняет достижимого superuser при `USAGE` = true, регрессия `su_grp`
подтверждает `access.role_writable` BLOCK без runtime-выполнения. Новых findings нет.

Реализация — GLM-5.3 xhigh через MyFlow `glm-5.3`. Ремонт начал GLM-5.3;
после лимита z.ai его закончил `gpt-6.1-sol` medium через MyFlow `sol-high`.

Независимая проверка оркестратора:

- На дереве первого прохода новые тесты суперюзер-группы и строки без имени падают:
  34 failed / 53 passed вместе с тестами, которым fake отвечает только на новый
  текст вопроса. С одним лишь новым текстом вопроса падает только тест строки
  без имени: 1 failed / 42 passed.
- Удаление только вопроса достижимости возвращает исходные counts и SHA-256
  четырёх Greengage-веток replay; ClickHouse не изменён.
- Fast gate MyFlow: Ruff, Black и strict mypy чистые; focused — 207 passed,
  набор задачи — 710 passed.
- `uv run --no-sync --with duckdb pytest -q --tb=short -p no:randomly`: 3955 passed,
  4 skipped, 38 deselected; exit 0, 229.87 s.

Границы: `SECURITY DEFINER`-функции, ClickHouse-роли, выданные, но не включённые,
BYPASSRLS достижимой роли и права администрирования ролей (`CREATEROLE`,
`ADMIN OPTION`) не спрашиваются. Текущая роль без LOGIN даёт ложный block:
`pg_user.usesuper` для неё NULL. Поведение до PostgreSQL 16 (`MEMBER`) описано
по документации и живьём не проверялось; Greengage проверен через offline
dispatch, не живой стенд.
