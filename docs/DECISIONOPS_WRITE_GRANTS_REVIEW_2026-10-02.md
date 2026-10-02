# ClickHouse canonical write grants — synthetic review

**Дата:** 2026-10-02. **Baseline:** `08c540a`.
**Выбор:** `gpt-6-astra`, reasoning `xhigh`, по ADR 0006. Реализация — Codex.
**Итоговое synthetic Astra xhigh review:** 0 substantive findings.
Модельная оценка не является human/design-partner или production acceptance.

## Основание

Astra воспроизвела семь canonical leaf privileges через offline collector:
ALTER UPDATE/DELETE/ADD COLUMN, CREATE TABLE/VIEW, DROP TABLE/VIEW.
Каждый ошибочно давал read_only=true, пустой access finding и runtime callback.
Причина — точное пересечение с набором групп ALTER/CREATE/DROP вместо распознавания
конкретного access type. Источник имён — ClickHouse v24.8.14.39-lts:

- [AccessType.h](https://github.com/ClickHouse/ClickHouse/blob/v24.8.14.39-lts/src/Access/Common/AccessType.h#L33):
  leaf types; строка 244 — ALL/full access.
- [StorageSystemGrants.cpp](https://github.com/ClickHouse/ClickHouse/blob/v24.8.14.39-lts/src/Storages/System/StorageSystemGrants.cpp#L27):
  Enum16 и выдача access_flags.toAccessTypes().
- [StorageSystemPrivileges.cpp](https://github.com/ClickHouse/ClickHouse/blob/v24.8.14.39-lts/src/Storages/System/StorageSystemPrivileges.cpp#L59):
  labels берутся из toString(AccessType).
- [AccessType.cpp](https://github.com/ClickHouse/ClickHouse/blob/v24.8.14.39-lts/src/Access/Common/AccessType.cpp#L38):
  замена underscore пробелом. SQL aliases не являются catalog labels.

## Контракт

Сохраняются прежние exact write types, добавляется exact ALL. ALTER/CREATE/DROP
совпадают точно или по продолжению через пробел. Это verb-family правило, включая
CREATE/ALTER/DROP USER/ROLE/NAMED COLLECTION, не реконструкция upstream hierarchy.
SHOW CREATE, ALTERED и SYSTEM DROP не совпадают. Профиль readonly=1/2 сохраняет
приоритет; partial revoke не является положительным grant; известный write grant
сохраняет evidence при incomplete roles. Evidence содержит точные отсортированные
имена без повторов. SQL, public schema, mart scope и PostgreSQL не меняются.
Контракт: [environment access](specs/environment-access.md).

## Проверки

- Итоговый `uv run --no-sync --with duckdb pytest -q --tb=short`:
  3723 passed, 5 skipped, 38 deselected за 311.06 с, exit 0.
- До правки `uv run --no-sync pytest -q --tb=short
  tests/test_clickhouse_write_grants.py`: 26 failed, 15 passed, exit 1.
- Перед добавлением ALL отдельный `-k ALL`: 2 failed, 41 deselected, exit 1.
- Связанная группа перед ALL: 280 passed за 41.09 с, exit 0 — write grants,
  enabled roles, environment probe/checks, decision verify и runtime probe.
- Окончательный `tests/test_clickhouse_write_grants.py`: 44 passed за 4.99 с, exit 0.
- Astra независимо проверила ALL × readonly=0/1/2 × partial revoke:
  6 passed, exit 0; полный suite агентом не дублировался.
- `uv run --no-sync ruff check auto_bi tests`: чисто, exit 0.
- `uv run --no-sync black --check auto_bi/verification/environment_probe.py
  auto_bi/verification/release_gate.py tests/test_clickhouse_write_grants.py`:
  три файла чистые, exit 0 после форматирования expression в environment_probe.py.
- `uv run --no-sync --with mypy --with types-PyYAML mypy --strict auto_bi`:
  no issues in 120 source files, exit 0.

## Границы

Версии verifier/check — 70/50; исторические bundles не переписаны. Доказательство
в этом срезе — pinned source и offline seam; свежий live ClickHouse не запускался.
Остальные семейства, включая SYSTEM/ACCESS MANAGEMENT, не объявляются полностью
проверенными. Это исправление конкретного пропуска canonical write verbs и ALL.
