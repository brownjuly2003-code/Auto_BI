# `dwh.cost`: фактическое покрытие — synthetic review

**Дата:** 2026-10-02. **Baseline:** `4b6cc77`.
**Выбор:** `gpt-6-astra`, reasoning `xhigh`, по ADR 0006. Реализация — Codex:
указанная правилами `gpt-5.6-luna` отсутствует среди доступных моделей сессии.
Модельная оценка не является human/design-partner или production acceptance.
**Итоговое synthetic Astra xhigh review:** 0 substantive findings;
независимо изучены production diff, все 31 новых public-теста и документация.

## Основание и контракт

Прежняя чистая сводка cost обещала соблюдение потолков всеми governed queries,
если присутствовал хотя бы один plan/runtime факт. Но `check_cost` пропускает
неатрибутированные, отсутствующие, нечитаемые и закрытые deny-all вопросы.
Проверенная часть могла поэтому выдаваться за проверку всех запросов.

Теперь знаменатели включают все объявленные потолки по metric-вопросам;
числители требуют `reading_for`, exists/readable, отсутствие deny-all и compiled SQL.
Строки учитываются по `scanned_rows`, байты/время — по exact-query counter либо
соответствующему `checked_bounds`/`exceeded_bound`. Ноль — наблюдение, NULL — нет.
Enforced bound не называется измеренным counter; planner и runtime разделены.
First-match `facts.plan/execution` и правила row-security bypass сохраняются.
Незапрошенные metric refs и необъявленные потолки не увеличивают покрытие.
Неполное и нулевое покрытие названы явно; положительный вывод ограничен
проверенными потолками. No-budget detail остаётся прежним.

Findings, severity, `_priced`, summary codes и verdict прежние. В частности,
supplied facts для недоступного вопроса могут сохранить `cost.within_budget`,
но detail сообщает нулевое покрытие без положительного вывода. Отсутствующую
витрину блокирует `dwh.access`. Verifier 73 → 74, check 50 без изменений.

## Проверки

- Red: `uv run --no-sync pytest -q --tb=short tests/test_cost_coverage.py` —
  22 failed, 6 passed, exit 1; падения на прежней безусловной clean-сводке.
- `uv run --no-sync pytest -q --tb=short tests/test_cost_coverage.py
  tests/test_environment_question_identity.py tests/test_environment_checks.py
  tests/test_environment_probe.py tests/test_runtime_probe.py
  tests/test_decision_verify.py tests/test_docs_as_code.py` — 303 passed / 1 failed:
  все 280 кодовых сценариев прошли; docs сообщила о ещё не созданном review artifact.
  После создания потребовалась его регистрация в Git (`git add -N`).
- `uv run --no-sync pytest -q --tb=short tests/test_docs_as_code.py` —
  24 passed, exit 0 после регистрации artifact.
- `uv run --no-sync black --check auto_bi/verification/release_gate.py
  tests/test_cost_coverage.py` — два файла чистые, exit 0.
- `uv run --no-sync ruff check auto_bi tests` — чисто, exit 0.
- `uv run --no-sync --with mypy --with types-PyYAML mypy --strict auto_bi` —
  no issues in 120 source files, exit 0.
- Полный offline gate: `uv run --no-sync --with duckdb pytest -q --tb=short` —
  **3821 passed, 5 skipped, 38 deselected**, exit 0, 225,53 с.

## Границы

SQL/probe, grading, public schemas, historical bundles и timing budgets
не менялись. Живой DWH для детерминированного текста не запускался.
