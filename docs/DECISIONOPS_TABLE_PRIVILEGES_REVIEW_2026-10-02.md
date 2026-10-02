# PostgreSQL table-write privilege replies — synthetic review

**Дата:** 2026-10-02
**Выбор среза:** `gpt-6-astra`, reasoning `xhigh`, по ADR 0006 после T-33.
**Основание:** выявленный при T-33 прежний дефект табличного privilege-вопроса.
**Итоговое review:** `gpt-6-astra` xhigh, 0 substantive findings относительно `151d2e7`.

Модельная оценка не является human/design-partner acceptance, измерением времени
человека или production acceptance. Контракт:
[environment access](specs/environment-access.md).

Отказ или пустой табличный INSERT/UPDATE/DELETE/TRUNCATE ответ превращался в `{}`.
При отрицательных колоночных ответах это подтверждало read-only и разрешало exact
runtime SELECT. Колоночные INSERT/UPDATE не доказывают отсутствие табличных
DELETE/TRUNCATE. Это противоречит контракту evidence о проверенных правах.

Astra выбрала ограниченную правку существующего `unreported`: неотвеченный
табличный вопрос не подтверждает read-only и блокирует доступ. Колоночный запрос
продолжается; положительный grant сохраняет прежнее evidence. Полные отрицательные
ответы сохраняют read-only/PASS. API, набор SQL, ClickHouse и superuser не меняются.
Версии нового среза — verifier 65 / check 45; исторические bundles сохраняются.

Независимая проверка Codex:

- На `151d2e7` до исправления `python -m pytest -q --tb=short
  tests/test_postgres_column_write_privileges.py -k unanswered_table`:
  2 failed, 4 passed, 22 deselected; exact runtime SELECT выполнялся при неизвестных правах.
- После исправления связанная группа environment/column-write тестов: 188 passed.
- Полный offline `uv run --no-sync --with duckdb pytest -q --tb=short`:
  3467 passed, 5 skipped, 38 deselected за 222.89 с, exit 0.
- `ruff check auto_bi tests`: чисто.
- `uv run --no-sync --with mypy --with types-PyYAML mypy --strict auto_bi`:
  no issues in 120 source files.

- Black на четырёх изменённых Python-файлах и `git diff --check`: чисто.

`NOINHERIT` / `SET ROLE`, отказ вопроса об identity и живой Greengage вне среза.
