# PostgreSQL partial write privileges — synthetic review

**Дата:** 2026-10-02. **Baseline:** `3921a87`.
**Выбор и итоговое review:** `gpt-6-astra`, reasoning `xhigh`, по ADR 0006.
**Результат:** 0 substantive findings; реализация и проверки — Codex.

Модельная оценка не является human/design-partner или production acceptance.
Запрошенная проектом `gpt-5.6-luna` отсутствовала в доступном списке моделей;
замена исполнителя другой Luna-моделью не выполнялась.

## Дефект и контракт

Astra воспроизвела 12 комбинаций missing/NULL в четырёх табличных и двух колоночных
полях. Непустой ответ ошибочно подтверждал read-only, не давал access findings и
допускал runtime callback. Это воспроизведение обработки ответов через offline seam,
не доказательство записи или частоты таких ответов в native PostgreSQL.

Теперь каждое отсутствующее/NULL поле не подтверждает read-only, включая `[{}]`.
Evidence называет конкретные неизвестные права и витрину. Обе группы вопросов
продолжают задаваться по всем витринам; доказанные grants сохраняют приоритет
и table/column deduplication. `dwh.access` блокирует через `access.role_writable`,
exact runtime SQL не исполняется. Полные отрицательные ответы сохраняют правила.
Контракт: [environment access](specs/environment-access.md).

## Проверки

До production-правки:

- `uv run --no-sync pytest -q --tb=short tests/test_postgres_column_write_privileges.py
  -k partial_write_privileges`: 72 failed, 4 passed, 28 deselected, exit 1.
- Дополнительный `-k second_mart_name_each_unknown`: 2 failed, 104 deselected, exit 1.
  Регрессии падали на ложном `read_only=True`.

После правки:

- Итоговый `uv run --no-sync --with duckdb pytest -q --tb=short`:
  3645 passed, 5 skipped, 38 deselected за 195.64 с, exit 0.
  Первый полный прогон: 3643 passed и два падения version-sync проверок,
  выполненных до обновления номеров в документации. После обновления отдельный
  `tests/test_docs_as_code.py` дал 24 passed, exit 0; итоговый полный прогон выше
  подтверждает исправление. Проверка ссылок также потребовала intent-to-add нового
  review-файла, после чего все ссылки разрешаются.
- `uv run --no-sync pytest -q --tb=short tests/test_postgres_column_write_privileges.py
  tests/test_postgres_role_identity.py tests/test_environment_probe.py
  tests/test_environment_checks.py tests/test_environment_question_identity.py
  tests/test_decision_verify.py tests/test_runtime_probe.py`:
  451 passed за 68.31 с, exit 0.
- `uv run --no-sync ruff check auto_bi tests`: чисто, exit 0.
- `uv run --no-sync black --check auto_bi/verification/environment_probe.py
  auto_bi/verification/release_gate.py tests/test_environment_probe.py
  tests/test_postgres_column_write_privileges.py`: четыре файла чистые, exit 0.
- `uv run --no-sync --with mypy --with types-PyYAML mypy --strict auto_bi`:
  no issues in 120 source files, exit 0.

Матрица покрывает все шесть privilege-полей, четыре PostgreSQL-family alias,
публичный gate и runtime callback; отдельно — второй mart, несколько неизвестных
полей и приоритет известных grants. Общая fixture теперь явно отвечает на
column-write поля; SELECT-ответ сохранён.

## Границы

Версии verifier/check — 68/48; исторические bundles не переписаны. SQL-вопросы,
общий boolean-normalizer, identity и ClickHouse не менялись. `NOINHERIT` / `SET ROLE`,
ClickHouse enabled-roles и живой Greengage вне этого среза. Живые БД не запускались.
