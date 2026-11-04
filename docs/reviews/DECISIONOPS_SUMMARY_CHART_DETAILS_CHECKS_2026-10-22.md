# T-92 — буквальные пояснения chart impact в summary

## Контракт

Произвольный `Measure.label` возвращается из `measure_alias` и попадает в пояснение
`computes_metric` в `ChartImpact.detail`. Raw detail добавлял второй Verdict/Owner
action при фактическом BLOCK. Теперь весь detail проходит существующий `_literal`
для обеих relations: управляющие символы видимы, Markdown/HTML экранируется.
Raw payload, relations, chart gaps и grading сохранены. Остальные поля вне среза.
Обычная пунктуация также экранируется, отображаемый текст сохраняется.

Verifier **104 → 105**, check **73** прежний; новые run IDs меняются.
Исторические bundles не переписываются.

## Проверки

- Red до изменения production-кода: **7 failed, 4 passed**, exit 1.
  Сквозной тест подтвердил BLOCK, одинаковые check statuses/codes, пустые chart gaps,
  точный raw detail и неизменность остальных полей chart перед проверкой summary.
- Десять renderer-кейсов: обе relations, Markdown/HTML, controls, Unicode, обычный
  текст; проверяется неизменность остальных байтов отчёта.
- Смежный набор: **314 passed in 35.13s**, exit 0.
- Strict mypy Linux/Windows: **122 source files**, exit 0 для каждой платформы.
- Первичный Ruff обнаружил E501; строка разбита на два соседних литерала.
- Финальные Ruff/Black чистые, exit 0; diff check чистый.
- После разбиения строки docs/new tests: **36 passed**, exit 0.
- Выбор среза и финальное ревью — synthetic Astra xhigh по ADR 0006:
  **0 actionable findings**, не human/production acceptance.
  Реализация — Codex при недоступной `gpt-5.6-luna`.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m pytest -q tests/test_summary_chart_details.py tests/test_summary_impact_details.py tests/test_summary_chart_gap_details.py tests/test_summary_chart_refs.py tests/test_gate_summary.py tests/test_summary_environment.py tests/test_summary_owners.py tests/test_summary_other_products.py tests/test_decision_verify.py tests/test_decision_chart_impact.py tests/test_run_identity.py --tb=short
.venv\Scripts\python.exe -m pytest -q tests/test_docs_as_code.py tests/test_summary_chart_details.py --tb=short
.venv\Scripts\python.exe -m ruff check auto_bi/verification/summary.py auto_bi/verification/release_gate.py tests/test_summary_chart_details.py
.venv\Scripts\python.exe -m black --check auto_bi/verification/summary.py auto_bi/verification/release_gate.py tests/test_summary_chart_details.py
uv run --frozen --with mypy --with types-PyYAML mypy --platform linux --strict auto_bi
uv run --frozen --with mypy --with types-PyYAML mypy --platform win32 --strict auto_bi
```

Полный offline-набор и live DWH/Mac на T-92 не запускались.
Полный baseline T-86 не считается проверкой нового кода.
