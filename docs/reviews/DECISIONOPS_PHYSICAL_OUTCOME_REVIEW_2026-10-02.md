# `dwh.physical`: код фактически выполненного сравнения — synthetic review

**Дата:** 2026-10-02. **Baseline:** `36086b1`.
**Выбор:** `gpt-6-astra`, reasoning `xhigh`, по ADR 0006. Реализация — Codex:
указанная правилами `gpt-5.6-luna` отсутствует среди доступных моделей сессии.
Модельная оценка не является human/design-partner или production acceptance.
**Итоговое synthetic Astra xhigh review:** 0 substantive findings по production
diff, тестам и контракту документации; результат независимо проверен Codex.
Отмеченная синхронизация версий в архитектуре выполнена.

## Основание и контракт

Verifier 75 выбирает `physical.as_declared` по читаемости витрины, даже если
движок не сообщил ни одного запрошенного типа или свойства layout. Detail при
этом сообщает `no physical comparison was made`, но проверка отсутствует в
owner-summary `What was not done`.

Код и detail теперь используют общий подсчёт фактически сравнённых типов и
свойств layout. Ноль сравнений даёт `physical.not_run`; хотя бы одно сравнение
сохраняет `physical.as_declared`, включая неполное покрытие.
Учитываются только `reading_for`, exists/readable и сообщённые значения
запрошенных типов или непустых деклараций layout. Пустой reported layout —
ответ, способный породить существующий drift finding. Deny-all не мешает
проверке каталога. Неатрибутированные и дублированные readings не засчитываются.

Findings сохраняют приоритет; status, verdict, detail, схемы и probe прежние.
Общие `_readable`, `_dated`, freshness/security/cost не меняются.
Verifier 75 → 76, check 51 → 52: меняется машинная классификация CheckResult.
Исторические обзоры и evidence bundles не переписываются.

## Проверки

- Red: `uv run --no-sync pytest -q --tb=short tests/test_physical_coverage.py` —
  **5 failed, 30 passed**, exit 1, 7,00 с. Все падения — прежний
  `physical.as_declared` при нуле сравнений вместо `physical.not_run`.
- Green: `uv run --no-sync pytest -q --tb=short tests/test_physical_coverage.py
  tests/test_environment_checks.py tests/test_environment_question_identity.py
  tests/test_security_coverage.py tests/test_decision_verify.py` —
  **247 passed**, exit 0, 45,53 с.
- `uv run --no-sync ruff check auto_bi tests` — чисто, exit 0.
- `uv run --no-sync black --check auto_bi/verification/release_gate.py
  tests/test_physical_coverage.py` — два файла чистые, exit 0.
- `uv run --no-sync --with mypy --with types-PyYAML mypy --strict auto_bi` —
  no issues in 120 source files, exit 0.
- Полный `uv run --no-sync --with duckdb pytest -q --tb=short` —
  **3887 passed, 5 skipped, 38 deselected**, exit 0, 615,77 с.
  Все 35 physical coverage/outcome сценариев прошли; исходный симптом и
  отображение пропуска в owner-summary проверены через публичный release gate.

## Границы

Живой DWH не запускался. Срез классифицирует уже полученные факты;
новых гарантий исполнения SQL или production acceptance нет.
