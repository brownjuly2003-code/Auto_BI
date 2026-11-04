# T-93 — буквальные пояснения всех проверок в summary

## Контракт

Данные role/target/evidence из environment facts входили в `CheckResult.detail`,
который, кроме provenance, выводился как Markdown. Writable-role finding добавлял
ложные Verdict/Owner action при BLOCK; роль попадала также в положительный security
detail. Поэтому правило едино для всех checks/statuses, а не только BLOCK.

Все details теперь проходят существующий `_literal`: findings/undone, таблица,
Product scope intro. Управляющие символы видимы, Markdown/HTML и pipe экранируются
один раз. Удалён отдельный режим table и неиспользуемый `_cell`; consumers проверены.
Raw facts/findings, JSON payload и grading сохранены. Остальные свободные поля
summary вне среза. Обычная пунктуация экранируется, отображаемый текст сохраняется.

Verifier **105 → 106**, check **73** прежний; новые run IDs меняются.
Исторические bundles не переписываются.

## Проверки

- Red до изменения production-кода: **24 failed, 10 passed**, exit 1.
- Три сквозных gate-кейса: name/evidence/target, BLOCK, одинаковые check
  statuses/codes и сохранение raw facts/findings.
- Renderer: пять сочетаний status/section, шесть входов (Markdown/HTML, controls,
  Unicode, обычный текст), отдельный Product scope intro. Проверяются точные строки
  таблицы и неизменность остальных байтов отчёта; provenance покрыт прежними тестами.
- Существующий тест Product scope обновлён только под буквальные скобки.
- Ruff/Black четырёх затронутых Python-файлов чистые, exit 0.
- Смежный набор: **348 passed in 68.74s**, exit 0.
- Strict mypy Linux/Windows: **122 source files**, exit 0 для каждой платформы.
- Docs/new tests: **59 passed**, exit 0; diff check чистый.
- Выбор среза и финальное ревью — synthetic Astra xhigh по ADR 0006:
  **0 actionable findings**, не human/production acceptance.
  Реализация — Codex при недоступной `gpt-5.6-luna`.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m pytest -q tests/test_summary_check_details.py tests/test_summary_chart_details.py tests/test_summary_impact_details.py tests/test_summary_chart_gap_details.py tests/test_summary_chart_refs.py tests/test_gate_summary.py tests/test_summary_environment.py tests/test_summary_owners.py tests/test_summary_other_products.py tests/test_decision_verify.py tests/test_decision_chart_impact.py tests/test_run_identity.py --tb=short
.venv\Scripts\python.exe -m pytest -q tests/test_docs_as_code.py tests/test_summary_check_details.py --tb=short
.venv\Scripts\python.exe -m ruff check auto_bi/verification/summary.py auto_bi/verification/release_gate.py tests/test_summary_check_details.py tests/test_gate_summary.py
.venv\Scripts\python.exe -m black --check auto_bi/verification/summary.py auto_bi/verification/release_gate.py tests/test_summary_check_details.py tests/test_gate_summary.py
uv run --frozen --with mypy --with types-PyYAML mypy --platform linux --strict auto_bi
uv run --frozen --with mypy --with types-PyYAML mypy --platform win32 --strict auto_bi
```

Полный offline-набор и live DWH/Mac на T-93 не запускались.
Полный baseline T-86 не считается проверкой нового кода.
