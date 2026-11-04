# `dwh.security`: код фактического наблюдения — synthetic review

**Дата:** 2026-10-02. **Baseline:** `a02e66a`.
**Выбор:** `gpt-6-astra`, reasoning `xhigh`, по ADR 0006. Реализация — Codex:
указанная правилами `gpt-5.6-luna` отсутствует среди доступных моделей сессии.
Модельная оценка не является human/design-partner или production acceptance.
**Итоговое synthetic Astra xhigh review:** 0 substantive findings по production
diff, тестам и контракту документации; результат независимо проверен Codex.

## Основание и контракт

Verifier 76 выбирает `security.unfiltered` по читаемости витрины, хотя отсутствующая
витрина не даёт наблюдений security. Detail сообщает `no security observation was
made`, но проверка отсутствует в owner-summary `What was not done`.

Код и detail теперь используют общий подсчёт подтверждённых чтений запрошенных
колонок и ответов о политиках. Ноль наблюдений даёт `security.not_run`; хотя бы
одно подтверждение или ответ сохраняет `security.unfiltered`, включая частичное
покрытие. Пустой список политик — ответ; пустая карта прав не подтверждает колонки.
Учитываются только однозначно атрибутированные существующие читаемые витрины.

Findings сохраняют приоритет; status, verdict, detail, схемы и probe прежние.
Общие `_readable`, freshness/physical/cost не меняются.
Verifier 76 → 77, check 52 → 53: меняется машинная классификация CheckResult.
Исторические обзоры и evidence bundles не переписываются.

## Проверки

- Red: `uv run --no-sync pytest -q --tb=short tests/test_security_coverage.py` —
  **3 failed, 36 passed**, exit 1, 13,04 с. Все падения — прежний
  `security.unfiltered` при нуле наблюдений вместо `security.not_run`.
- Green: `uv run --no-sync pytest -q --tb=short tests/test_security_coverage.py
  tests/test_environment_checks.py tests/test_environment_question_identity.py
  tests/test_decision_verify.py` — **224 passed**, exit 0, 58,85 с.
- `uv run --no-sync ruff check auto_bi tests` — чисто, exit 0.
- `uv run --no-sync black --check auto_bi/verification/release_gate.py
  tests/test_security_coverage.py` — два файла чистые, exit 0.
- `uv run --no-sync --with mypy --with types-PyYAML mypy --strict auto_bi` —
  no issues in 120 source files, exit 0.
- Первый полный `uv run --no-sync --with duckdb pytest -q --tb=short` —
  **1 failed, 3898 passed, 5 skipped, 38 deselected**, exit 1, 789,17 с.
  Единственное падение — docs-as-code: ссылка на этот новый файл считалась
  `untracked`. Файл добавлен в индекс явным pathspec.
- Повторный полный `uv run --no-sync --with duckdb pytest -q --tb=short` —
  **3899 passed, 5 skipped, 38 deselected**, exit 0, 210,79 с.
  Все 39 security coverage/outcome сценариев прошли; исходный симптом и
  отображение пропуска в owner-summary проверены через публичный release gate.

## Границы

Живой DWH не запускался. Срез классифицирует уже полученные факты;
новых гарантий исполнения SQL или production acceptance нет.
