# ClickHouse enabled roles — synthetic review

**Дата:** 2026-10-02. **Baseline:** `8f7c426`.
**Выбор и итоговое review:** `gpt-6-astra`, reasoning `xhigh`, по ADR 0006.
**Итог:** 0 substantive findings. Реализация и проверки — Codex.
Модельное review не является human/design-partner или production acceptance.

## Дефект и контракт

Astra воспроизвела отказ `system.enabled_roles`: collector считал список ролей
пустым, отрицал role-only policy, допускал grant-based read-only и runtime callback.
При профиле readonly=1 неполные grants также давали ложный column_unreadable.
Missing/NULL/пустое role_name повторяли дефект. Это offline evidence поведения
collector, не наблюдение production-сессии или попытка записи в DWH.

Теперь incomplete role set отличается от успешного `[]`. Известные имена сохраняются
точно и входят в прежний grants query. Профиль readonly=1/2 по-прежнему доказывает
read-only; без него положительный write grant сохраняет evidence, иначе неизвестные
роли блокируют доступ и exact runtime SQL. Column reach неизвестен. Пустой policy
catalog сохраняет `[]`, непустой либо отказавший оставляет applicability неизвестной.
Это `security.not_observed` REVIEW, без выдуманных unreadable/filtered outcomes.

Два зависимых pure-rule guard необходимы для распространения unknown:
classification требует известного column reach; отсутствие колонки в фильтрованном
ClickHouse catalog с неизвестным reach не доказывает physical.column_absent.
Известные типы и PostgreSQL absence сохраняют правила. Контроли реального отсутствия
ClickHouse-колонки теперь явно задают известный reach; assertions сохранены.
При readonly=1/2 eligible runtime сохраняется даже с security REVIEW.
Контракт: [environment access](../specs/environment-access.md).

## Проверки

- Итоговый `uv run --no-sync --with duckdb pytest -q --tb=short`:
  3679 passed, 5 skipped, 38 deselected за 343.70 с, exit 0.
- До production-правки `uv run --no-sync pytest -q --tb=short
  tests/test_clickhouse_enabled_roles.py`: 23 failed, 11 passed, exit 1.
  Матрица: refusal/missing/NULL/empty/mixed, readonly 0/1/2/unset, публичный gate,
  runtime callback, точные имена и unknown reach. Отдельно — known-empty, grants,
  PostgreSQL controls и connection error propagation.
- Первая связанная группа: 281 passed, два старых physical fixture-контроля требовали
  известного reach. После исправления входов `uv run --no-sync pytest -q --tb=short
  tests/test_clickhouse_enabled_roles.py tests/test_environment_checks.py`:
  100 passed за 8.44 с, exit 0.
- Astra независимо проверила четыре сочетания readonly=1/2 с INSERT grant или отказом
  grants: 4 passed, exit 0. Полный suite агентом не дублировался.
- `uv run --no-sync ruff check auto_bi tests`: чисто, exit 0.
- Black --check на environment.py, environment_probe.py, release_gate.py,
  test_environment_checks.py и test_clickhouse_enabled_roles.py: чисто, exit 0.
- `uv run --no-sync --with mypy --with types-PyYAML mypy --strict auto_bi`:
  no issues in 120 source files, exit 0.

## Границы

Версии verifier/check — 69/49. Исторические bundles не переписаны. SQL shapes,
публичные схемы и PostgreSQL collector прежние. Живые БД и новая инфраструктура
не запускались. Этот срез не расширяет полноту иных ClickHouse catalog-вопросов.
