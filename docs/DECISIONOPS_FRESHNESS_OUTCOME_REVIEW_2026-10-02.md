# `dwh.freshness`: код отсутствующего сравнения — synthetic review

**Дата:** 2026-10-02. **Baseline:** `1220e5a`.
**Выбор:** `gpt-6-astra`, reasoning `xhigh`, по ADR 0006. Реализация — Codex:
указанная правилами `gpt-5.6-luna` отсутствует среди доступных моделей сессии.
Модельная оценка не является human/design-partner или production acceptance.
**Итоговое synthetic Astra xhigh review:** 0 substantive findings по production
diff, тестам и контракту документации; результат независимо проверен Codex.

## Основание и контракт

Verifier 77 выбирает `freshness.fresh` для противоречивых supplied facts
`exists=False/readable=True`, хотя `check_freshness` пропускает отсутствующую
витрину, а clean-detail сообщает ноль сравнений. Проверка отсутствует в
owner-summary `What was not done`. Этот остаток явно отложен в
[предыдущем coverage-срезе](DECISIONOPS_FRESHNESS_COVERAGE_REVIEW_2026-10-02.md).

`_dated` теперь требует существования витрины наряду с однозначной атрибуцией,
читаемостью и отсутствием deny-all. Без допустимых вопросов чистый код —
`freshness.not_run`, detail использует существующую формулировку `nothing was aged`.
Хотя бы один допустимый вопрос в clean-ветке сохраняет `freshness.fresh` и
прежний detail частичного или полного покрытия. Неизвестное время, пустая
витрина, несопоставимое окно и устаревшие данные сохраняют собственные findings.

Findings, status, verdict, схемы и probe прежние. `_readable` и другие DWH checks
не меняются. Verifier 77 → 78, check 53 → 54: меняется машинная классификация
CheckResult. Исторические обзоры и evidence bundles не переписываются.

## Проверки

- Red: `uv run --no-sync pytest -q --tb=short tests/test_freshness_coverage.py` —
  **3 failed, 30 passed**, exit 1, 9,10 с. Все падения — прежний `freshness.fresh`
  при отсутствии витрины вместо `freshness.not_run`.
- Green: `uv run --no-sync pytest -q --tb=short tests/test_freshness_coverage.py
  tests/test_environment_checks.py tests/test_environment_question_identity.py
  tests/test_decision_verify.py` — **218 passed**, exit 0, 20,48 с.
- `uv run --no-sync ruff check auto_bi tests` — чисто, exit 0.
- `uv run --no-sync black --check auto_bi/verification/release_gate.py
  tests/test_freshness_coverage.py` — два файла чистые, exit 0.
- `uv run --no-sync --with mypy --with types-PyYAML mypy --strict auto_bi` —
  no issues in 120 source files, exit 0.
- Полный `uv run --no-sync --with duckdb pytest -q --tb=short` —
  **3911 passed, 5 skipped, 38 deselected**, exit 0, 207,03 с.
  Все 33 freshness coverage/outcome сценария и docs-as-code прошли;
  исходный симптом и отображение пропуска в owner-summary проверены через
  публичный release gate.

## Границы

Живой DWH не запускался. Срез классифицирует уже полученные факты;
новых гарантий исполнения SQL или production acceptance нет.
