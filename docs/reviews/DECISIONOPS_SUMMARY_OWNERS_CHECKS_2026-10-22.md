# T-88 — буквальное отображение владельцев в owner-summary

## Контракт

Допустимый `DecisionProduct.metadata.owner` с переводами строк и Markdown
попадал через `ProductImpact.owners` в раздел `Whose numbers this reaches` без
экранирования. При фактическом BLOCK отчёт мог содержать второй Verdict и Owner action.

Теперь существующий `_literal` применяется к каждому владельцу перед соединением
списка. Порядок и разделители сохраняются, управляющие символы видимы. Исходные
значения в impact payload, findings и grading не меняются. Другие поля impact и
summary вне среза; это не полная защита всех свободных полей отчёта.

Verifier **100 → 101**, check **73** прежний. Новые run IDs меняются;
исторические bundles не переписываются. Обычная пунктуация владельцев также
экранируется: например, `commercial-analytics` содержит экранированный дефис
в Markdown, но отображается тем же текстом.

## Проверки

- Red до изменения production-кода: **4 failed, 2 passed**, exit 1. Сквозной
  тест получил два Verdict при BLOCK; raw owner сохранился в impact payload.
- Смежный набор после исправления: **211 passed in 25.43s**, exit 0.
  Шесть новых сценариев проверяют ложные инструкции, пунктуацию Markdown/HTML,
  управляющие символы, Unicode, простой текст и порядок нескольких владельцев.
- Ruff/Black четырёх затронутых Python-файлов чистые, exit 0.
- Strict mypy Linux/Windows: **122 source files**, exit 0 для каждой платформы.
- Docs-as-code: сначала 1 failed / 24 passed из-за ещё не добавленного в Git
  нового документа; после добавления: **25 passed in 0.68s**, exit 0.
  `git diff --check` чистый.
- Выбор среза и финальное ревью кода/тестов — synthetic Astra xhigh по ADR 0006:
  **0 actionable findings**, не human/production acceptance. Прогоны выполнены Codex;
  документация в модельное ревью не входила. Реализация — Codex при
  недоступной `gpt-5.6-luna`.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m pytest -q tests/test_summary_owners.py tests/test_gate_summary.py tests/test_summary_environment.py tests/test_summary_other_products.py tests/test_run_identity.py tests/test_decision_verify.py --tb=short
.venv\Scripts\python.exe -m ruff check auto_bi/verification/summary.py auto_bi/verification/release_gate.py tests/test_summary_owners.py tests/test_gate_summary.py
.venv\Scripts\python.exe -m black --check auto_bi/verification/summary.py auto_bi/verification/release_gate.py tests/test_summary_owners.py tests/test_gate_summary.py
uv run --frozen --with mypy --with types-PyYAML mypy --platform linux --strict auto_bi
uv run --frozen --with mypy --with types-PyYAML mypy --platform win32 --strict auto_bi
```

Полный offline-набор и live DWH/Mac на T-88 не запускались.
Полный baseline T-86 не считается проверкой нового кода.
