# T-75 — отказ кодирования output-пути investigation

Дата: 2026-10-22. Реализация: Codex при недоступной `gpt-5.6-luna`.
Выбор и review: synthetic Astra xhigh по ADR 0006, не human/production acceptance.
Контракт: [публикация investigation](specs/unreadable-inputs.md).

## Изменение

На исходном `f75a953` ошибка `UnicodeEncodeError` при создании output-parent или
публикации через `os.rename` выходила за обработчик `OSError`. Во втором случае уже
записанные четыре файла оставались во временном каталоге, а CLI завершался traceback.
Теперь эта ошибка проходит существующую очистку и даёт exit 2, пустой stdout и
`error: cannot write output directory <path>: UnicodeEncodeError`.
Текст исходного исключения не печатается. Непечатаемые символы output-пути
экранируются через `filesystem.display_path`; это сохраняет одну строку и при LF/CR.
Та же обработка имени применяется к staging-пути в диагностике отказа очистки.

При успешной очистке исходная ошибка остаётся прямой причиной; при отказе очистки
прямой причиной остаётся её ошибка. Существующий output не удаляется. Читаемые
Unicode-пути и U+DCFF сохраняют успешную запись и точные байты четырёх артефактов.
`ValueError` и `UnicodeTranslateError` сохраняют прежние границы. Версии 98/73 прежние.

## Проверки

- Red до production-правки: **7 failed, 5 passed in 0.70s**, exit 1; все семь отказов —
  необработанный `UnicodeEncodeError` на `mkdir` или `os.rename`.
- После первого исправления: **270 passed in 25.96s**, exit 0 в профильном наборе.
- Astra независимо выполнила начальный новый модуль: **12 passed in 0.87s**, exit 0.
- Review обнаружило P2: LF в output-пути разбивал диагностику на две строки.
  Red для добавленных LF/CR-кейсов: **8 failed, 12 deselected in 7.39s**, exit 1.
  Исправлено отображление пути только в новой Unicode-ветке; модуль содержит 20 кейсов.
- Финальный профильный прогон: **278 passed in 544.32s**, exit 0.
- Финальный Ruff: **All checks passed**, exit 0. Black: **2 files unchanged**, exit 0.
- Финальные package-wide strict mypy Linux/Windows: **no issues found in 122 source
  files**, оба exit 0.
- Документационные проверки: **25 passed in 7.36s**, exit 0. После них изменена
  только эта запись результата; staged diff проверен через `git diff --cached --check`.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m pytest -q tests/test_investigation_output_encoding.py tests/test_investigation_cli.py tests/test_investigation_inputs.py tests/test_investigation_path_encoding.py tests/test_unencodable_output.py --tb=short
.venv\Scripts\python.exe -m ruff check auto_bi/investigation/application.py tests/test_investigation_output_encoding.py
.venv\Scripts\python.exe -m black --check auto_bi/investigation/application.py tests/test_investigation_output_encoding.py
uv run --frozen --no-sync --with mypy --with types-PyYAML mypy --platform linux --strict auto_bi
uv run --frozen --no-sync --with mypy --with types-PyYAML mypy --platform win32 --strict auto_bi
.venv\Scripts\python.exe -m pytest -q tests/test_docs_as_code.py --tb=short
```

Ошибки файловой системы в тестах вводятся только в выбранную операцию `mkdir` или
`os.rename`; входы читаются и четыре staging-файла записываются реально. Проверены
strict ASCII/UTF-8 stderr, отсутствие output/staging, повторная запись, причина ошибки,
отказ очистки и сохранность существующего output. Успешные Unicode-пути читаются и
пишутся реально на Windows/Python 3.13.7. Это не живой POSIX-прогон некодируемого пути.

## Review и границы

Финальный synthetic Astra xhigh review кода, тестов и контракта: **0 открытых P1/P2**.
Во втором проходе ревьюер проверил diff чтением, без повторного запуска тестов.
Status/checks-документы проверены Codex, в модельное ревью не входили.

Полный offline-прогон с duckdb (`uv run --frozen --no-sync --with duckdb python -m pytest
-q --tb=short --maxfail=5 --durations=5`) начат после первого исправления, до правки
отображения LF/CR. Codex остановил его примерно на 9% без показанных failures:
загруженный код предшествовал финальному исправлению, прогон шёл медленно. Процесс
завершился exit 1; итоговой pytest-сводки нет. Полный набор на финальном коде не проверен.
Живые DWH-стенды в этом срезе не запускались.
