# ClickHouse SET ROLE reach — synthetic review

**Дата:** 2026-10-03
**Срез:** T-38; verifier 83 / check 59
**Reviewer:** `gpt-6-astra`, reasoning `xhigh`
**Итог:** первый проход — 0 findings.

Модельная оценка не является human/design-partner acceptance, измерением времени
человека или production acceptance. Контракт:
[environment access](../specs/environment-access.md).

Сессия ClickHouse держит права своего пользователя и включённых ролей, а какие роли
включены — выбор самой сессии. Прежний зонд спрашивал grants пользователя и ролей из
`system.enabled_roles`, поэтому роль, выданную пользователю, но не включённую, не видел.
Одноразовый сервер ClickHouse 24.8.14.39 на Mac (удалён после прогона) показал:

- пользователь с `SELECT` на `dm.sales_daily` и ролью `writer` (`INSERT` на ту же витрину)
  при `SET DEFAULT ROLE NONE` начинает с пустым `system.enabled_roles`; `system.grants`
  по его имени называет только `SELECT`, собственный `INSERT` отклонён (`ACCESS_DENIED`);
  после `SET ROLE writer` тот же `INSERT` проходит, HTTP-параметр `role=writer` даёт то же
  без сессии;
- `SET ROLE` принимает только роль, выданную пользователю напрямую (роль, выданная лишь
  другой роли, — `SET_NON_GRANTED_ROLE`), и включает вместе с ней все роли, выданные ей:
  пользователь с `mid`, которой выдана `deepwriter` с `ALTER UPDATE`, выполнил мутацию
  после `SET ROLE mid`;
- `system.role_grants` отвечает на обе половины: строка с `user_name` — роль пользователя,
  строка с `role_name` — роль роли; условие `user_name = currentUser() OR role_name IS NOT
  NULL` возвращает и role-to-role строки ролей, до которых пользователь не дотягивается;
- каждый каталог требует собственного `SELECT`: пользователь с `SELECT ON system.grants`
  без `SELECT ON system.role_grants` получает отказ на втором и всё равно включает роль
  и пишет;
- `readonly` переключением роли не снимается: при `readonly=1/2` у пользователя, в его
  профиле (также `CONST`) или из настроек default-роли `SET ROLE` в пишущую роль — даже
  созданную с `SETTINGS readonly = 0` — и `SET ROLE NONE` оставляют `readonly`, `INSERT`
  отклонён (`READONLY`); ни `SET ROLE`, ни HTTP-параметр `role` настроек роли не применяют;
- имя роли — произвольная строка: роли `odd'name`, `wri\ter`, `tail\` и ` writer`
  (с пробелом в начале) создаются, выдаются и возвращаются каталогом без изменений.
  Литерал `'wri\ter'` не называет роль `wri\ter` и молча не возвращает строк, хотя эта
  роль держит `ALTER UPDATE` и мутация после `SET ROLE` в неё проходит; `'tail\'` —
  синтаксическая ошибка; литералы с удвоенными обратной косой чертой и кавычкой называют
  свои роли.

Теперь, когда `readonly` не `1`/`2`, `system.grants` ответил, включённые роли известны
и собственные grants не содержат write access type, зонд задаёт один вопрос
к `system.role_grants` и строит охват: роли, выданные пользователю напрямую, и
транзитивно роли, выданные уже достигнутым ролям; каждая роль читается один раз,
включённые роли исключены. Если охват не пуст, один вопрос к `system.grants` называет
все достижимые роли. Write access type по тому же правилу, что и для собственных grants,
делает роль writable: evidence называет access type, роль и `SET ROLE`, `dwh.access` —
block с `access.role_writable`, exact runtime SQL не исполняется. Отказ
`system.role_grants`, строка без `granted_role_name` или без `user_name` и `role_name`,
отказ вопроса о grants достижимых ролей и строка без `role_name` или `access_type`
не подтверждают read-only: evidence называет неотвеченное, полные строки используются,
доказанный write grant сохраняет приоритет. Чистое read-only evidence сообщает, что
`SET ROLE` других ролей не достигает, или называет проверенные роли. Имена ролей
в обоих grants-вопросах пишутся литералом с удвоенными обратной косой чертой и кавычкой.

`readonly=1/2`, отказ `system.grants`, неполные enabled roles и собственный write grant
сохраняют факты, evidence и вопросы; новых вопросов они не шлют. Достижимые роли
не становятся principals: column reach и применимость row policies отвечают за сессию
как она есть. Зонд ничего не переключает и не пишет.

Операционное следствие: пользователю гейта, read-only по grants, а не по `readonly=1/2`,
нужен `SELECT` на `system.role_grants` помимо `system.grants`. Без него `dwh.access`
блокируется с evidence о неотвеченном `system.role_grants`.

Проход Astra сверил реализацию с durable spec и требованиями задачи: условия и порядок
новых вопросов, транзитивный обход и его завершение при взаимно выданных ролях,
исключение включённых ролей, точные имена, сортировку и экранирование в обоих
grants-вопросах, partial revoke, неполные ответы, порядок неотвеченных половин
и приоритет доказанного write grant, сохранность прежних коротких путей, `principals`,
column reach и policy applicability, распространение `ConnectionError`. Собственная
выборка ревьюера: 107 passed. Findings нет.

Реализация — GLM-5.3 xhigh через MyFlow `glm-5.3`; после недельного лимита z.ai срез
закончил `gpt-6.1-sol` medium через MyFlow `sol-high`.

Независимая проверка оркестратора:

- Новый модуль `tests/test_clickhouse_set_role_reach.py` против копии `auto_bi` из HEAD:
  25 failed / 10 passed (проходят контроли прежних коротких путей, запрета записи
  и policy/column reach); на рабочем дереве — 35 passed.
- Fast gate MyFlow: Ruff, Black и strict mypy чистые; focused — 1118 passed,
  набор задачи — 745 passed.
- Полный offline pytest с duckdb: 4087 passed, 4 skipped, 38 deselected, exit 0.

Границы: права администрирования ролей (`ROLE ADMIN`, как и `CREATEROLE` / `ADMIN OPTION`
в PostgreSQL) не спрашиваются; column reach и row policies достижимых ролей
не спрашиваются. Write grant достижимой роли на любой объект делает роль writable,
как и собственный grant: ClickHouse-правило не ограничено витринами скоупа. Правило
`readonly=1/2` опирается на поведение 24.8.14.39; версия, применяющая настройки роли
при переключении, не проверялась. Роли, назначаемые внешним каталогом (LDAP), живьём
не проверялись. Имена витрин в вопросах к каталогу пишутся прежним литералом: они
приходят из модели, а не от движка. `SECURITY DEFINER`-функции, BYPASSRLS достижимой
роли и текущая роль PostgreSQL без LOGIN остаются границами PostgreSQL-семейства.
