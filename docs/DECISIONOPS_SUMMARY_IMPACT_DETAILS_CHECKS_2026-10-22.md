# T-91 — буквальные пояснения semantic impact в summary

## Контракт

Свободные timezone/calendar метрики попадали в `metric.time_changed`, затем в
`ProductImpact.detail`. Raw detail добавлял второй Verdict/Owner action и инструкцию
о выпуске при фактическом BLOCK. Теперь весь `ProductImpact.detail` проходит через
существующий `_literal`: управляющие символы видимы, Markdown/HTML экранируется.
Raw semantic/impact payload и grading сохранены; остальные свободные поля вне среза.
Обычная пунктуация также экранируется, отображаемый текст сохраняется.

Verifier **103 → 104**, check **73** прежний; новые run IDs меняются.
Исторические bundles не переписываются.

## Проверки

- Red до изменения production-кода: **5 failed, 2 passed**, exit 1.
  Два сквозных теста подтвердили одинаковые check statuses/codes, BLOCK,
  неизменность остальных полей impact и точный raw detail перед проверкой summary.
- Пять renderer-кейсов: Markdown/HTML, controls, Unicode, обычный текст;
  проверяется неизменность остальных байтов отчёта.
- Смежный набор: **303 passed in 31.93s**, exit 0.
- Strict mypy Linux/Windows: **122 source files**, exit 0 для каждой платформы.
- Black первоначально потребовал форматирование нового теста; оно применено.
- Финальные Ruff/Black: чисто, exit 0. `git diff --check` чистый.
- Docs-as-code сначала отклонил ссылку на ещё не staged checks-документ;
  после добавления явных путей финальный docs/new tests: **32 passed**, exit 0.
- Выбор среза и финальное ревью — synthetic Astra xhigh по ADR 0006:
  **0 actionable findings**, не human/production acceptance.
  Реализация — Codex при недоступной `gpt-5.6-luna`.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m pytest -q tests/test_summary_impact_details.py tests/test_summary_chart_gap_details.py tests/test_summary_chart_refs.py tests/test_gate_summary.py tests/test_summary_environment.py tests/test_summary_owners.py tests/test_summary_other_products.py tests/test_decision_verify.py tests/test_decision_chart_impact.py tests/test_run_identity.py --tb=short
.venv\Scripts\python.exe -m pytest -q tests/test_docs_as_code.py tests/test_summary_impact_details.py --tb=short
.venv\Scripts\python.exe -m ruff check auto_bi/verification/summary.py auto_bi/verification/release_gate.py tests/test_summary_impact_details.py
.venv\Scripts\python.exe -m black --check auto_bi/verification/summary.py auto_bi/verification/release_gate.py tests/test_summary_impact_details.py
uv run --frozen --with mypy --with types-PyYAML mypy --platform linux --strict auto_bi
uv run --frozen --with mypy --with types-PyYAML mypy --platform win32 --strict auto_bi
```

Полный offline-набор и live DWH/Mac на T-91 не запускались.
Полный baseline T-86 не считается проверкой нового кода.
