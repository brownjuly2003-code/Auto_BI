# PostgreSQL role identity replies — synthetic review

**Дата:** 2026-10-02
**Выбор среза:** `gpt-6-astra`, reasoning `xhigh`, по ADR 0006 на `2f80633`.
**Итоговое review:** `gpt-6-astra` xhigh, 0 substantive findings относительно `2f80633`.

Модельная оценка не является human/design-partner acceptance, измерением времени
человека или production acceptance. Контракт:
[environment access](../specs/environment-access.md).

Astra независимо воспроизвела отказ SQLSTATE `42501` и пустой identity-ответ:
при полных отрицательных table/column grants получались `name=unknown`,
`read_only=true`, `bypasses_row_security=false`, отсутствие access findings и один
exact runtime вызов. Это некорректное подтверждение read-only и допуск runtime;
реальный superuser exploit не демонстрировался.

Выбран early return после неотвеченного identity-вопроса: роль `unknown`,
`read_only=false`, `bypasses_row_security=null`, evidence называет identity,
superuser, `BYPASSRLS` и неподтверждённый read-only. Write-privilege вопросы в этой
ветке не запускаются; положительный grant ещё не наблюдался. Exact runtime SQL
не исполняется, публичный `dwh.access` блокируется через `access.role_writable`.
Detail finding описывает неподтверждённый read-only, не утверждая, что неизвестная
роль умеет писать. Ответившие роли и ClickHouse сохраняют прежние правила;
ошибки соединения вне refusal-контракта продолжают пробрасываться.

Версии — verifier 66 / check 46; исторические bundles сохраняются.

Проверка Codex:

- До production-правки `uv run --no-sync pytest -q --tb=short
  tests/test_postgres_role_identity.py`: 16 failed, 16 passed за 20.32 с, exit 1.
  При отрицательных grants неизвестная роль получала read-only и runtime вызов;
  приготовленный положительный grant не исправлял ложный известный bypass-статус.
- После правки `uv run --no-sync pytest -q --tb=short
  tests/test_postgres_role_identity.py tests/test_postgres_column_write_privileges.py
  tests/test_environment_probe.py tests/test_environment_checks.py
  tests/test_environment_question_identity.py tests/test_decision_verify.py
  tests/test_runtime_probe.py`: 305 passed за 26.39 с, exit 0.
  Все четыре PostgreSQL-family имени движка проверены на отказе/пустом ответе,
  runtime withholding и публичном BLOCK; отрицательные grants сохраняют PASS,
  superuser/BYPASSRLS и ошибки соединения сохраняют прежние правила.
- `uv run --no-sync ruff check auto_bi tests`: чисто, exit 0.
- Black на четырёх изменённых Python-файлах: чисто, exit 0.
- `uv run --no-sync --with mypy --with types-PyYAML mypy --strict auto_bi`:
  no issues in 120 source files, exit 0.

- После добавления нового review-файла в Git-индекс
  `uv run --no-sync pytest -q --tb=short tests/test_docs_as_code.py
  -k internal_markdown_links_resolve`: 1 passed, 23 deselected, exit 0.
- Полный `uv run --no-sync --with duckdb pytest -q --tb=short`:
  3499 passed, 5 skipped, 38 deselected за 732.97 с, exit 0.
  Первый полный прогон упал только на ссылке на ещё не tracked review-файл;
  после explicit добавления файла в Git-индекс повторный прогон прошёл.

Astra независимо проверила фактический diff, весь новый regression-файл,
fixtures и изменения контракта:
0 substantive findings. Полный/scoped pytest ревьюер не повторял; перечисленные
проверки выполнены Codex, модельное review их не подменяет.

Частично заполненные identity-строки и NULL внутри успешного ответа,
`NOINHERIT` / `SET ROLE`, новая SQL-интроспекция и живой Greengage вне среза.
