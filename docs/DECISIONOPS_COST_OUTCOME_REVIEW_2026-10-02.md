# `dwh.cost`: код фактически выполненной проверки — synthetic review

**Дата:** 2026-10-02. **Baseline:** `e8bdeba`.
**Выбор:** `gpt-6-astra`, reasoning `xhigh`, по ADR 0006. Реализация — Codex:
указанная правилами `gpt-5.6-luna` отсутствует среди доступных моделей сессии.
Модельная оценка не является human/design-partner или production acceptance.
**Итоговое synthetic Astra xhigh review:** 0 substantive findings по production
diff, всем новым тестовым случаям и документации. Независимая проверка Codex
дополнительно обнаружила неверное ожидание Markdown-разметки в тесте:
`What was not done` выделяет имя проверки жирным, а не обратными кавычками.
Исправлено только ожидание существующего формата, production code прежний.

## Основание и контракт

Verifier 74 сообщает нулевое покрытие в detail, но `_priced` ещё считает
supplied plan/runtime facts независимо от доступности и атрибуции вопроса.
Получается `cost.within_budget` одновременно с `no cost comparison was made`.
Такой результат отсутствует в разделе owner-summary `What was not done`,
поскольку этот раздел выбирает `cost.not_run` по коду проверки.

Выбранный контракт: `_priced` учитывает только вопросы с `reading_for`,
exists/readable, без deny-all и с compiled SQL — как `check_cost` и `_cost_clean`.
При отсутствии допустимых сравнений clean code становится `cost.not_run`.
Хотя бы один проверенный объявленный потолок сохраняет `cost.within_budget`:
это включает частичное покрытие, нулевые counters и enforced bounds без counters.
First-match facts, bypass и no-budget answer сохраняются.

Findings, status, severity, verdict, detail, схемы и probe не меняются.
Отсутствующее измерение, превышение лимита и некомпилируемый SQL сохраняют
свои findings и коды; новые findings не добавляются.
Verifier 74 → 75, check 50 → 51: изменяется машинная классификация CheckResult.
Исторический coverage-review и evidence bundles не переписываются.

## Проверки

- Red: `uv run --no-sync pytest -q --tb=short tests/test_cost_coverage.py` —
  50 failed, 31 passed, exit 1, 560,36 с. Все падения — прежний `cost.within_budget`
  для пропущенных вопросов вместо `cost.not_run`.
- Первый полный `uv run --no-sync --with duckdb pytest -q --tb=short` —
  49 failed, 3822 passed, 5 skipped, 38 deselected, exit 1, 281,53 с.
  Все 49 падений — неверное ожидание обратных кавычек вместо жирного имени
  проверки в разделе `What was not done`; production checks уже имели нужный код.
- После исправления assertion: `uv run --no-sync pytest -q --tb=short
  'tests/test_cost_coverage.py::test_ineligible_cost_facts_are_reported_as_not_done[rows-absent]'`
  — 1 passed, exit 0, 11,33 с.
- `uv run --no-sync ruff check auto_bi tests` — чисто, exit 0.
- `uv run --no-sync black --check auto_bi/verification/release_gate.py
  tests/test_cost_coverage.py` — два файла чистые, exit 0.
- `uv run --no-sync --with mypy --with types-PyYAML mypy --strict auto_bi` —
  no issues in 120 source files, exit 0.
- Итоговый полный `uv run --no-sync --with duckdb pytest -q --tb=short` —
  **3871 passed, 5 skipped, 38 deselected**, exit 0, 230,06 с.
  Все 81 cost coverage/outcome сценария прошли, включая partial coverage, bypass,
  first-match, zero counters, enforced bounds, прежние findings и owner-summary.

## Границы

Живой DWH для исправления классификации не запускался. Это проверка
атрибуции уже поставленных фактов, не новая гарантия исполнения SQL.
