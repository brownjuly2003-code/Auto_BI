# ClickHouse own-grant row without an access type — synthetic review

**Дата:** 2026-10-04
**Срез:** T-42; verifier 87 / check 63
**Reviewer:** `gpt-6-astra`, reasoning `xhigh`
**Итог:** первый проход — 0 findings.

Модельная оценка не является human/design-partner acceptance, измерением времени
человека или production acceptance. Контракт:
[environment access](specs/environment-access.md), требование «a ClickHouse own-grant
row without an access type is not an answer».

Шаг 7 читает собственные grants ClickHouse-сессии одним вопросом к `system.grants` и
брал тип доступа как `str(row.get("access_type", ""))`. Строка ответа без `access_type` —
ключа нет, NULL или пустая строка — поэтому была «не write grant» и «не `SELECT`».
Воспроизведено на HEAD `eed7a5b` двойником движка из `tests/test_environment_probe.py`,
`readonly=0`, без включённых ролей:

- ответ из одной такой строки: роль read-only по grants, `dwh.access` проходит, exact
  runtime SQL исполняется;
- та же строка рядом с типизированным `SELECT`: чтение колонок названо установленным,
  хотя строка могла быть `SELECT` или его partial revoke.

Grants ролей, достижимых через `SET ROLE`, уже читались fail-closed
(`_ch_reached_role_grants`: строка без имени или типа — пробел в ответе); собственные
grants сессии — нет.

Живые факты, ClickHouse 24.8.14.39 (одноразовый сервер на Mac, 2026-10-03, удалён):
`system.grants.access_type` — не-nullable `Enum16`, `is_partial_revoke` — `UInt8`;
`user_name`, `role_name`, `database`, `table` и `column` — `Nullable(String)`. Движок
строку без типа доступа вернуть не может: до коллектора она доходит, только когда ответ
доставлен не целиком слоем между движком и коллектором. Срез закрывает неполный ответ,
а не поведение движка.

Теперь строка собственных grants без типа доступа — пробел в ответе, что бы ни говорил её
`is_partial_revoke`. Прежние ветки и их порядок сохранены: `readonly=1/2` устанавливает
read-only; отказ `system.grants`, write access type на типизированной строке, не
являющейся partial revoke, и неполные enabled roles сохраняют своё evidence. Иначе роль
не read-only с evidence `readonly=<x> and system.grants returned a row with no access
type, so read-only access is not established`; вопросы о ролях, достижимых через
`SET ROLE`, не задаются, exact runtime SQL не исполняется, `dwh.access` — block через
`access.role_writable`. `column_reads` каждой присутствующей витрины при такой строке —
null при любом `readonly`. `dwh.security` тогда сообщает `security.not_observed` по
прежнему правилу, колонка вне отфильтрованного каталога не называется физически
отсутствующей, классифицированная — неограниченной. Row-policy факты читаются из
principals и не меняются. Ответ, где у каждой строки есть тип доступа, читается как
прежде; вопросы, их текст и порядок не меняются — replay digests ClickHouse прежние.

Срез — альтернатива №1 (P1) выбора synthetic Astra xhigh от 2026-10-03, давшего T-40.

Проход Astra сопоставил все десять сценариев требования с реализацией и новым тестовым
модулем: приоритеты `readonly=1/2`, отказа grants-вопроса, доказанного write grant и
неполных enabled roles; блокировку доступа и runtime-запроса при `readonly=0` и
отсутствие reach-вопросов; неизвестный column reach при любом `readonly` и при partial
revoke без ложных unreadable, physical absence и unenforced classification; контроли
типизированного ответа и отказа grants-вопроса, row-policy факты, версии evidence и
согласованность документации. Собственная выборка ревьюера: сфокусированный pytest —
76 passed, 8 deselected (новый модуль, `tests/test_environment_probe.py` и проверка
replay digests; тесты через `Case(tmp_path)` пишут на диск, ревьюер их прочитал), и
восемь вариантов этих сценариев, исполненных в памяти: facts, evidence, findings,
runtime-вызовы и отсутствие reach-вопросов. Red на HEAD и полный набор задачи ревьюер
не повторял. Findings нет.

Реализация — `gpt-6.1-sol` medium через MyFlow `sol-high`: назначенный исполнитель
GLM-5.3 xhigh был в недельном лимите z.ai, и контроллер передал срез следующему кандидату.
Требование в durable spec написал оркестратор до запуска исполнителя.

Независимая проверка оркестратора:

- Новый модуль `tests/test_clickhouse_untyped_grants.py` против копии `auto_bi` из HEAD:
  15 failed / 8 passed (проходят контроли: пять типизированных ответов, отказ
  grants-вопроса, неполные enabled roles и row-policy факты типизированного ответа);
  на рабочем дереве — 23 passed.
- До реализации правило было собрано прототипом на копии пакета: проба из 21 случая —
  11 failed / 10 passed до правила, 21 passed с правилом.
- Fast gate MyFlow: Ruff, Black и strict mypy чистые; focused — 1123 passed, набор
  задачи — 400 passed.
- Полный offline pytest с duckdb: 4336 passed, 4 skipped, 38 deselected, exit 0.

Границы: строка с типом доступа, которого гейт не знает как пишущий, читается по
прежнему правилу write-семейств; ответ, из которого строки выпали целиком, от полного не
отличим; NULL в `database`, `table` или `column` — grant шире объекта и читается как
прежде; отсутствующий или NULL `is_partial_revoke` на типизированной строке по-прежнему
читается как «не partial revoke». Живой ClickHouse дал этому срезу только типы колонок
`system.grants`: неполный ответ движок вернуть не может, поэтому сценарии сняты
двойником движка.
