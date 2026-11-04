# PostgreSQL column-write privileges — synthetic review

**Дата:** 2026-10-02
**Срез:** T-33; verifier 64 / check 44
**Reviewer:** `gpt-6-astra`, reasoning `xhigh`
**Итог:** 0 substantive findings после исправления P1.

Модельная оценка не является human/design-partner acceptance, измерением времени
человека или production acceptance. Контракт:
[environment access](../specs/environment-access.md).

PostgreSQL-family collector спрашивает табличные INSERT/UPDATE/DELETE/TRUNCATE
и колоночные INSERT/UPDATE по всей витрине скоупа. Колоночный grant делает роль
writable, `dwh.access` — BLOCK; exact runtime SELECT не выполняется. Табличный
grant сохраняет одно evidence. Superuser и ClickHouse сохраняют свои правила.

Первый проход Astra выявил P1: отказ колоночного privilege-вопроса трактовался
как отсутствие прав и разрешал runtime SELECT. После независимого red-check
отказ и пустой ответ делают read-only неподтверждённым: `read_only=False`,
evidence называет неотвеченные права и витрину, публичный gate блокирует доступ.
Доказанный табличный grant сохраняет своё evidence.

Независимая проверка Codex:

- На исходном `d8e80e5` модуль T-33: 17 failed, 3 passed; исходный write-grant
  ошибочно подтверждал read-only.
- На WIP новые refused/empty регрессии: 3 failed, 1 passed до исправления.
- `python -m pytest -q --tb=short tests/test_postgres_column_write_privileges.py
  tests/test_environment_probe.py tests/test_environment_checks.py
  tests/test_environment_question_identity.py`: 182 passed.
- Четыре PostgreSQL replay goldens включают два дополнительных SELECT. Удаление
  только этих запросов возвращает исходные counts и SHA-256; ClickHouse не изменён.
- `ruff check auto_bi tests`: чисто.
- `uv run --no-sync --with mypy --with types-PyYAML mypy --strict auto_bi`:
  no issues in 120 source files.

- `uv run --no-sync --with duckdb pytest -q --tb=short`: 3461 passed,
  5 skipped, 38 deselected; exit 0, 302.37 s.
- Black на пяти изменённых Python-файлах и `git diff --check`: чисто.

Границы: живой DWH в этой сессии не запускался; PostgreSQL 16.15 наблюдения
унаследованы из спеки. Greengage проверен через существующий offline dispatch,
не живой стенд. `NOINHERIT` / `SET ROLE` вне среза. Унаследованный отказ
табличного privilege-вопроса остаётся отдельным дефектом для следующего выбора
Astra, когда колоночные ответы отрицательны.
