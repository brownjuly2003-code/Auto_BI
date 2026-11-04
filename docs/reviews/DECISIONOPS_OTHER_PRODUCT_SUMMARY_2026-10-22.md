# T-85 — проблемы других продуктов в сводке

Дата: 2026-10-22. Исходный HEAD: `aa6e298`.
Выбор: synthetic `gpt-6-astra` xhigh по ADR 0006, не human/production acceptance.
Реализация: Codex; `gpt-5.6-luna` недоступна среди исполнителей.

## Проблема и изменение

`contracts.validate` сохранял `other_product_issues` в JSON и показывал их
количество в detail, но владелец не видел в `reports/summary.md`, какие это
продукты и проблемы. Это документированный пробел представления, не ошибка grading.

Сводка теперь условно добавляет `Validation issues for other products` после
`What was not done` и до `How to read this evidence`. Раздел прямо говорит,
что эти проблемы не оцениваются для текущего продукта и не меняют его verdict.
Каждая запись сохраняет source, code, reported severity, subject, message
и исходный порядок, включая повторные записи.

Свободные строки выводятся буквальным Markdown-текстом: пунктуация экранируется,
непечатаемые символы показываются явно. Старые разделы не изменены. Если список
пуст, новый раздел отсутствует. Используется прежний `_issues_by_product`.
Payload, grading, claims, owner action и subject_refs не меняются.

Verifier **98 → 99**, check **73** прежний. Новые run IDs и manifest digests
меняются из-за версии; при наличии чужих проблем меняется также тело сводки.
Исторические bundles не переписываются.

## Проверки

- Red: **7 failed**, exit 1; end-to-end тест не находил раздел, unit-тесты —
  новый keyword argument. Первый green: **7 passed**.
- После добавления смешанного собственного/чужого случая: **134 passed**,
  exit 0 (summary, validation scope, verify и восемь новых сценариев).
- Старый рендерер из Git HEAD и новый дали побайтно одинаковый текст без чужих
  issues на четырёх verdict-профилях при одинаковых `SummaryFacts`.
- Ruff изменённых Python-файлов чистый; Black отформатировал новый тестовый модуль.
- Финальные Ruff/Black и strict mypy Linux/Windows по 122 модулям: exit 0.
- Два ограниченных synthetic Astra review (код/тесты, затем контракт/документация):
  **0 открытых P1/P2**. Полный набор reviewer не запускал.
- Полный offline-набор с duckdb на финальном коде T-85, Windows/Python 3.13.7:
  **6584 passed, 109 skipped, 38 deselected in 538.85s**, exit 0.
  Один полный прогон, включает T-84 и T-85. 109 skipped не считаются проверенным
  поведением; 38 deselected исключены конфигурацией `not integration and not e2e`.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m pytest -q tests/test_summary_other_products.py tests/test_gate_summary.py tests/test_validation_issue_scope.py tests/test_decision_verify.py --tb=short
uv run --frozen --with duckdb pytest -q --tb=short
.venv\Scripts\python.exe -m ruff check auto_bi/verification/summary.py auto_bi/verification/release_gate.py tests/test_summary_other_products.py
.venv\Scripts\python.exe -m black --check auto_bi/verification/summary.py auto_bi/verification/release_gate.py tests/test_summary_other_products.py
uv run --frozen --with mypy --with types-PyYAML mypy --platform linux --strict auto_bi
uv run --frozen --with mypy --with types-PyYAML mypy --platform win32 --strict auto_bi
```

Live DWH/Mac не проверялись; изменение касается локального производного отчёта.
