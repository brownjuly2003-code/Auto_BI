# T-94 — буквальные subjects semantic impact в summary

## Контракт

Имя колонки допускает произвольный текст. Когда колонка входит в dimensions метрики,
изменение её типа достигает governed-продукта. Raw `ProductImpact.subject` добавлял
ложные Verdict/Owner action при BLOCK. Теперь весь subject проходит существующий
`_literal`: управляющие символы видимы, Markdown/HTML экранируется.
Raw subject, привязки impact и grading сохранены. Metrics/dashboard refs вне среза.
Обычная пунктуация экранируется, отображаемый текст сохраняется.

Verifier **106 → 107**, check **73** прежний; новые run IDs меняются.
Исторические bundles не переписываются.

## Проверки

- Первоначальная гипотеза о добавленном неиспользуемом поле/таблице не подтвердилась:
  `_reaching` правильно исключает их из summary. Production-код тогда не менялся.
- Подтверждённый red до исправления: **4 failed, 2 passed**, exit 1.
  Поле добавлено в оба snapshots и dimensions метрики; candidate меняет его тип.
  Проверены BLOCK, одинаковые check statuses/codes, точный raw subject и остальные
  поля impact, включая привязку к продукту. Summary содержал два Verdict.
- Пять renderer-кейсов: Markdown/HTML, controls, Unicode, обычный текст;
  проверяется неизменность остальных байтов отчёта.
- Существующий тест отображения метрики обновлён только под буквальную пунктуацию.
- Ruff/Black четырёх затронутых Python-файлов чистые, exit 0.
- Смежный набор: **354 passed in 35.72s**, exit 0.
- Strict mypy Linux/Windows: **122 source files**, exit 0 для каждой платформы.
- Docs/new tests: **31 passed**, exit 0; diff check чистый.
- Выбор среза и финальное ревью — synthetic Astra xhigh по ADR 0006:
  **0 actionable findings**, не human/production acceptance.
  Реализация — Codex при недоступной `gpt-5.6-luna`.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m pytest -q tests/test_summary_impact_subjects.py tests/test_summary_check_details.py tests/test_summary_chart_details.py tests/test_summary_impact_details.py tests/test_summary_chart_gap_details.py tests/test_summary_chart_refs.py tests/test_gate_summary.py tests/test_summary_environment.py tests/test_summary_owners.py tests/test_summary_other_products.py tests/test_decision_verify.py tests/test_decision_chart_impact.py tests/test_run_identity.py --tb=short
.venv\Scripts\python.exe -m pytest -q tests/test_docs_as_code.py tests/test_summary_impact_subjects.py --tb=short
.venv\Scripts\python.exe -m ruff check auto_bi/verification/summary.py auto_bi/verification/release_gate.py tests/test_summary_impact_subjects.py tests/test_gate_summary.py
.venv\Scripts\python.exe -m black --check auto_bi/verification/summary.py auto_bi/verification/release_gate.py tests/test_summary_impact_subjects.py tests/test_gate_summary.py
uv run --frozen --with mypy --with types-PyYAML mypy --platform linux --strict auto_bi
uv run --frozen --with mypy --with types-PyYAML mypy --platform win32 --strict auto_bi
```

Полный offline-набор и live DWH/Mac на T-94 не запускались.
Полный baseline T-86 не считается проверкой нового кода.
