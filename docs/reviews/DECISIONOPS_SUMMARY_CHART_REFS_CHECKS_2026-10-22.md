# T-89 — буквальные ссылки на чарты в impact summary

## Контракт

`ChartSpec.id` допускает произвольный текст. ID попадал в `ChartImpact.ref` и
`ChartGap.subject`, а затем в summary: одинарные backticks вокруг ref не защищали
от ID с backtick и переводами строк; subject вставлялся без экранирования.
В обоих случаях допустимый ID добавлял второй Verdict/Owner action при BLOCK.

Теперь `_literal` применяется к целому ref и subject; внешние backticks вокруг
ref удалены, поскольку CommonMark не экранирует пунктуацию внутри code span.
Управляющие символы отображаются явно. Исходные refs/subjects, chart IDs,
relations, gap codes, payload и grading сохранены. Остальные поля и details
summary вне среза. Включены все `ChartGap.subject`, в том числе гэпы без chart ID.

Verifier **101 → 102**, check **73** прежний. Новые run IDs меняются;
исторические bundles не переписываются. Обычные ссылки тоже получают экранирование
пунктуации, а распознанный chart ref больше не имеет моноширинного оформления.

## Проверки

- Red до изменения production-кода: **7 failed**, exit 1. Два сквозных сценария
  получили по два Verdict: `computes_metric` и `chart.metric_unread`.
  До assertion отчёта проверены BLOCK, неизменные статусы/коды и raw refs в payload.
- Пять дополнительных сценариев проверяют обе позиции вывода: Markdown/HTML,
  управляющие символы, backticks, Unicode, обычный текст.
- Strict mypy Linux/Windows: **122 source files**, exit 0 для каждой платформы.
- Смежный набор: **280 passed in 47.24s**, exit 0.
- Первый Ruff/Black выявил три длинные строки в двух файлах; Black перенёс строки.
- Финальные Ruff/Black четырёх затронутых Python-файлов чистые, exit 0.
- После форматирования и обновления документации: **32 passed in 2.50s**, exit 0
  (`tests/test_docs_as_code.py` и `tests/test_summary_chart_refs.py`). Diff check чистый.
- Выбор среза и финальное ревью — synthetic Astra xhigh по ADR 0006:
  **0 actionable findings**, не human/production acceptance.
  Реализация — Codex при недоступной `gpt-5.6-luna`.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m pytest -q tests/test_summary_chart_refs.py tests/test_gate_summary.py tests/test_summary_environment.py tests/test_summary_owners.py tests/test_summary_other_products.py tests/test_decision_verify.py tests/test_decision_chart_impact.py tests/test_run_identity.py --tb=short
.venv\Scripts\python.exe -m pytest -q tests/test_docs_as_code.py tests/test_summary_chart_refs.py --tb=short
.venv\Scripts\python.exe -m ruff check auto_bi/verification/summary.py auto_bi/verification/release_gate.py tests/test_summary_chart_refs.py tests/test_decision_verify.py
.venv\Scripts\python.exe -m black --check auto_bi/verification/summary.py auto_bi/verification/release_gate.py tests/test_summary_chart_refs.py tests/test_decision_verify.py
uv run --frozen --with mypy --with types-PyYAML mypy --platform linux --strict auto_bi
uv run --frozen --with mypy --with types-PyYAML mypy --platform win32 --strict auto_bi
```

Полный offline-набор и live DWH/Mac на T-89 не запускались.
Полный baseline T-86 не считается проверкой нового кода.
