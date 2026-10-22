# T-86 — запрет перенаправленного каталога evidence run

Дата: 2026-10-22. Исходный HEAD: `a5993ec`.
Выбор и финальное ревью: synthetic `gpt-6-astra` xhigh по ADR 0006,
не human/production acceptance. Реализация: Codex; `gpt-5.6-luna` недоступна.

## Проблема и изменение

Store проверял ссылки внутри run, но не сам `<configured root>/<run_id>`.
Junction этого каталога переносила корень confinement: чтение, запись и exists
принимали внешний файл за артефакт run. Проверка относительно `base.resolve()`
не обнаруживала перенаправление самого base.

Теперь до разрешения пути store проверяет каталог run через `os.lstat`.
Symbolic link или Windows junction дают `ArtifactStoreError`; `exists` возвращает
False согласно прежнему контракту semantic refusal. Проверка не зависит от цели:
наружу, на соседний run или вложенный каталог. Отказ ОС сохраняет исходный
`OSError` в `ArtifactRefusalError`, в том числе для `exists`.
Отсутствующий каталог по-прежнему можно создать при записи.

Явно заданный configured root остаётся доверенной точкой: его junction допустима.
Глобальная семантика `filesystem.is_link` не изменена. Verifier **99**, check **73**
сохранены; формат, grading и байты корректных bundles прежние.

## Проверки

- Red на Windows: **12 failed, 1 passed, 9 skipped**, exit 1. Девять реальных
  junction-сценариев проверили get/put/exists для трёх целей; ещё три выявили
  отсутствие проверки отказа доступа к run-root.
- Первый green: **13 passed, 9 skipped**, exit 0.
- Финальный профиль: **194 passed, 76 skipped**, exit 0, включая сравнение всех
  файлов bundle через обычный configured root и configured-root junction.
- Ruff/Black изменённых Python-файлов и strict mypy Linux/Windows по 122 модулям:
  exit 0. Замечание Ruff к raw regex теста исправлено.
- Synthetic Astra xhigh, один финальный проход по коду и тестам: **0 P1/P2**.
- Документационные контракты и новые регрессии после staging: **39 passed,
  9 skipped**, exit 0. Первый прогон отверг ссылки на ещё untracked checks-док;
  после добавления файла в индекс контракт ссылок прошёл.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m pytest -q tests/test_evidence_run_root.py tests/test_evidence_store.py tests/test_evidence_inspect.py tests/test_evidence_signing.py tests/test_refused_evidence.py tests/test_platform_guards.py --tb=short
.venv\Scripts\python.exe -m pytest -q tests/test_docs_as_code.py tests/test_evidence_run_root.py --tb=short
.venv\Scripts\python.exe -m ruff check auto_bi/evidence/store.py tests/test_evidence_run_root.py
.venv\Scripts\python.exe -m black --check auto_bi/evidence/store.py tests/test_evidence_run_root.py
uv run --frozen --with mypy --with types-PyYAML mypy --platform linux --strict auto_bi
uv run --frozen --with mypy --with types-PyYAML mypy --platform win32 --strict auto_bi
```

Девять новых symlink-сценариев пропущены из-за отсутствия Windows-привилегии;
реальные junction проверены без пропусков. Native POSIX, live DWH/Mac и полный
offline-набор на T-86 не запускались. Полный baseline T-85 не считается проверкой
нового кода. Конкурентная замена путей и остальные виды reparse points не покрыты.
