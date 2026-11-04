# T-82 — глубокий JSON manifest при чтении артефакта

Дата: 2026-10-22. Исходный HEAD: `52d4621`.
Выбор среза: synthetic Astra xhigh по ADR 0006.
Реализация: Codex; требуемая `gpt-5.6-luna` недоступна.
Модельная оценка не является human/production acceptance.

## Проблема и граница

Manifest с 3000 вложенными массивами или объектами вызывает `RecursionError`
в JSON decoder. Полная inspection уже отвечает `evidence.manifest_invalid`,
verdict `block`, CLI exit 4. Но `decision evidence --artifact` перехватывал
при разборе только `ValueError`, поэтому завершался необработанным исключением.

Теперь существующий обработчик разбора manifest принимает также `RecursionError`:
библиотека выдаёт `EvidenceReadError`, CLI — exit 2, одну строку stderr и пустой
stdout. Диагностика не содержит содержимого manifest; файлы не меняются.
При неверном `--expect-digest` по-прежнему раньше срабатывает отказ digest pin
(exit 4). Общий перехват исключений не добавлен; операции вне разбора manifest
сохраняют свои обработчики. Новый фиксированный предел вложенности не вводится.

Версии verifier/check 98/73 прежние: writer, формат evidence, подписи, grading
и успешный экспорт не менялись. Полная inspection сохраняет свой exit 4;
проверка только выбранного payload не превращается в проверку всего bundle.

## Проверки

- Red до исправления: **6 failed, 1 passed**, exit 1; причиной отказов был
  необработанный `RecursionError` в библиотечном reader и CLI.
- Профиль evidence inspection, CLI, signing, store и refused evidence:
  **190 passed, 67 skipped in 12.13s**, exit 0.
- После добавления assertions полной CLI inspection и уточнения regex теста:
  **7 passed, 52 deselected in 18.30s**, exit 0. Проверены массив/объект,
  отсутствие pin и правильный pin, приоритет неверного pin, stdout/stderr,
  сохранность manifest и прежний full-inspection verdict.
- Ruff и Black трёх изменённых Python-файлов: exit 0.
- Synthetic Astra xhigh review точного diff кода и тестов: **0 открытых P1/P2**;
  reviewer не повторял прогоны. Это не human/production acceptance.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m pytest -q tests/test_evidence_inspect.py tests/test_decision_cli.py tests/test_evidence_signing.py tests/test_evidence_store.py tests/test_refused_evidence.py --tb=short
.venv\Scripts\python.exe -m pytest -q tests/test_evidence_inspect.py tests/test_decision_cli.py -k 'parser_depth or depth_refusal' --tb=short
.venv\Scripts\python.exe -m ruff check auto_bi/evidence/inspect.py tests/test_evidence_inspect.py tests/test_decision_cli.py
.venv\Scripts\python.exe -m black --check auto_bi/evidence/inspect.py tests/test_evidence_inspect.py tests/test_decision_cli.py
```

Пропущенные проверки не считаются пройденными. Полный offline suite, отдельный
type-check и live DWH/Mac этим срезом не запускались. Полный baseline 6523 passed
в предыдущем отчёте относится к коду до T-82.
