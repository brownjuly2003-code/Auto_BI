# T-74 — отказ кодирования имени входа investigation

Дата: 2026-10-22. Реализация: Codex при недоступной `gpt-5.6-luna`.
Выбор и review — synthetic Astra xhigh по ADR 0006.
Контракт: [unreadable-inputs](../specs/unreadable-inputs.md).

## Изменение

На исходном `c710bbb` загрузчик `decision investigate` пропускал `UnicodeEncodeError`
при чтении plan/result: например, имя с U+D800 не кодируется файловой системой POSIX.
Теперь `_load_input` переводит этот отказ в `OfflineInvestigationError` с исходной
причиной. CLI возвращает exit 2, пустой stdout и одну строку
`error: cannot read <path>: UnicodeEncodeError`. Текст исходного исключения не печатается;
путь проходит через существующий encoding-safe вывод. Более поздние входы не читаются,
выходной каталог и его отсутствующий parent не создаются.

Обработка относится только к фактическому отказу загрузки plan/result. Проверки символов
в именах заранее нет; читаемые Unicode-имена и U+DCFF сохраняют поведение и байты
артефактов. `RuntimeError` и `UnicodeTranslateError` по-прежнему пробрасываются.
Output paths, другие команды и версии verifier 98 / check 73 не изменены.

## Проверки

- Red до production-правки: **6 failed, 5 passed**, exit 1; все шесть отказов —
  необработанный `UnicodeEncodeError`.
- Первичная проверка после исправления: **4 failed, 8 passed**. Четыре отказа были
  сравнением Windows CRLF с ожидаемым LF в тестовой обвязке; добавлен явный
  `newline="\n"` у `TextIOWrapper`.
- Финальный профильный набор: **258 passed in 122.33s**, exit 0, Windows/Python 3.13.7.
  Новый модуль включает 12 тестов: точные байты strict ASCII/UTF-8 stderr, порядок
  чтения, отсутствие output, сохранение cause, проброс неожиданных ошибок и
  идентичность артефактов для доступных Unicode-имён и U+DCFF.
- Ошибка чтения в регрессионных тестах вводится только для выбранного пути через
  `Path.read_text`; остальные файлы читаются реально. Проверки доступных имён
  выполняют настоящее чтение и запись на Windows.
- Ruff: **All checks passed**; Black: **2 files unchanged**, exit 0.
  До этого исправлены SIM117 и форматирование теста.
- Package-wide strict mypy `linux` и `win32`: **no issues found in 122 source files**,
  оба exit 0.
- Документационные проверки: **25 passed in 0.81s**, exit 0; `git diff --cached --check`
  чистый. После проверки добавлена только эта запись результата.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m pytest -q tests/test_investigation_path_encoding.py tests/test_investigation_inputs.py tests/test_investigation_cli.py tests/test_unencodable_output.py --tb=short
.venv\Scripts\python.exe -m ruff check auto_bi/investigation/application.py tests/test_investigation_path_encoding.py
.venv\Scripts\python.exe -m black --check auto_bi/investigation/application.py tests/test_investigation_path_encoding.py
uv run --frozen --no-sync --with mypy --with types-PyYAML mypy --platform linux --strict auto_bi
uv run --frozen --no-sync --with mypy --with types-PyYAML mypy --platform win32 --strict auto_bi
.venv\Scripts\python.exe -m pytest -q tests/test_docs_as_code.py --tb=short
```

## Review и границы

Synthetic Astra xhigh проверила код, тесты, спецификации и архитектуру:
**0 actionable P1/P2**. Записи статуса и этот отчёт проверены Codex.
Это не human/production acceptance; ревьюер не повторял профильный набор.

Read-only stdlib-проба на Mac с Python **3.9.6**, `utf-8/surrogateescape`, подтвердила
реальный `UnicodeEncodeError` для имён plan/result с U+D800; SSH завершился с exit 0,
файлы не создавались. Это не проверка проекта на поддерживаемом Python 3.12+.

Полный baseline `python -m pytest -q --tb=short --maxfail=5` на исходном HEAD
намеренно прерван Codex до production-правки после примерно семи минут и около 10%
набора, без показанных failures; процесс завершился exit 1. Полного результата нет.
Живые DWH-стенды в этом срезе не запускались.
