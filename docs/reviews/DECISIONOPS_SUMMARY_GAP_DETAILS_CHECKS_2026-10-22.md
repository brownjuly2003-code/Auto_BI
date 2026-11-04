# T-90 — буквальные пояснения пробелов анализа в impact summary

## Контракт

Quoted SQL-имя с переводами строк и Markdown проходит через `reads.unattributed`
в `_sql_partial_detail`, затем в `ChartGap.detail`. При выводе сырого detail
оно добавляло второй Verdict/Owner action при фактическом BLOCK.

Теперь существующий `_literal` применяется ко всему `ChartGap.detail` для всех
типов гэпов, без разбора шаблонов. Это защищает также имена из `unreconciled`.
Управляющие символы видимы; Markdown/HTML-пунктуация экранируется. Raw details,
subjects, gap codes, SQL-анализ и grading не меняются. Другие impact/check details
и свободные поля summary остаются вне среза.

Verifier **102 → 103**, check **73** прежний. Новые run IDs меняются;
исторические bundles не переписываются. Обычная пунктуация пояснений тоже
экранируется в Markdown, отображаемый текст сохраняется.

## Проверки

- Первичная диагностическая проба имела ошибочный импорт `auto_bi.sql_guard`;
  после исправления на `auto_bi.agent.sql_guard` парсер сохранил имя целиком.
- Red до изменения production-кода: **10 failed, 6 passed**, exit 1.
  Сквозной gate-тест получил два Verdict при BLOCK. До проверки отчёта подтверждены
  одинаковые статусы/коды, gap subjects/codes и точный raw detail.
- Ещё 15 сценариев покрывают Markdown/HTML, controls, Unicode и обычный текст
  для трёх типов гэпа; проверяется неизменность остальных байтов renderer-отчёта.
- Ruff/Black трёх затронутых Python-файлов чистые, exit 0.
- Смежный набор: **296 passed in 34.43s**, exit 0.
- Strict mypy Linux/Windows: **122 source files**, exit 0 для каждой платформы.
- Docs-as-code: **25 passed in 0.97s**, exit 0; diff check чистый.
- Выбор среза и финальное ревью — synthetic Astra xhigh по ADR 0006:
  **0 actionable findings**, не human/production acceptance.
  Реализация — Codex при недоступной `gpt-5.6-luna`.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m pytest -q tests/test_summary_chart_gap_details.py tests/test_summary_chart_refs.py tests/test_gate_summary.py tests/test_summary_environment.py tests/test_summary_owners.py tests/test_summary_other_products.py tests/test_decision_verify.py tests/test_decision_chart_impact.py tests/test_run_identity.py --tb=short
.venv\Scripts\python.exe -m pytest -q tests/test_docs_as_code.py --tb=short
.venv\Scripts\python.exe -m ruff check auto_bi/verification/summary.py auto_bi/verification/release_gate.py tests/test_summary_chart_gap_details.py
.venv\Scripts\python.exe -m black --check auto_bi/verification/summary.py auto_bi/verification/release_gate.py tests/test_summary_chart_gap_details.py
uv run --frozen --with mypy --with types-PyYAML mypy --platform linux --strict auto_bi
uv run --frozen --with mypy --with types-PyYAML mypy --platform win32 --strict auto_bi
```

Полный offline-набор и live DWH/Mac на T-90 не запускались.
Полный baseline T-86 не считается проверкой нового кода.
