# T-68 — безопасный отказ загрузки настроек DecisionOps

Дата: 2026-10-22. Реализация: Codex. Windows, Python 3.13.7; нативные проверки.
Контракт: [unanswered-engine](specs/unanswered-engine.md).

## Изменение

`decision verify --dwh` и `decision capture` возвращают exit 2, пустой stdout и одну
строку `error: cannot load settings: …`, когда `get_settings()` поднимает
`ValidationError`, `OSError` или `UnicodeError`. DWH-фабрика не строится, соединение
не открывается, evidence и captured result не создаются.

Валидация называет до трёх известных полей и кодов ошибок, например
`ch_port (int_parsing)`, затем число остальных. Неизвестная позиция заменяется
`<settings>`. Значения, сообщения, context, URL и вложенные позиции не выводятся.
Для I/O и Unicode выводится только класс исключения, без сообщения и пути.

Локальный helper в `cli.py` ограничивает перехват вызовом `get_settings()`;
capture теперь строит target внутри существующего обработчика `VerifyError`.
Общий загрузчик настроек, v1-команды, offline verify и версии verifier 97 / check 72
не изменены. Неожиданные исключения загрузчика сохраняются.

## Проверки

- Исходные `test_decision_cli.py` и `test_result_capture.py`: 36 passed, exit 0.
- Новый `test_decision_settings.py` до правки: 32 failed, 3 passed, exit 1.
  Узкий traceback подтвердил `ValidationError` и печать некорректного значения порта.
  После правки: 35 passed, exit 0, 13.81 s.
- Смежный набор семи модулей: **506 passed, 1 skipped**, exit 0, 229.74 s.
  Пропущен существующий symlink-тест: Windows не разрешила создать ссылку.
  Новый модуль включает восемь процессов CLI: обе команды и четыре engine aliases;
  настоящие environment/dotenv Settings, отказ `open` для `.env`, невалидный UTF-8,
  ограничение числа сообщений, отсутствие фабрик и файлов, успешный offline verify
  и сохранение неожиданного исключения.
- `python -m ruff check auto_bi tests`: exit 0.
- `python -m black --check auto_bi/cli.py tests/test_decision_settings.py`: exit 0.
- `uv run --frozen --no-sync --with mypy --with types-PyYAML mypy
  --platform linux --strict auto_bi` и та же команда с `--platform win32`:
  обе exit 0, 122 source files.
- `git diff --check`: exit 0.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m pytest -q tests/test_decision_settings.py tests/test_decision_cli.py tests/test_result_capture.py tests/test_capture_driver_failures.py tests/test_unanswered_engine.py tests/test_stated_run_facts.py tests/test_unreadable_run_inputs.py --tb=short
```

Первичная проверка стиля нашла одну длинную строку helper; исправлен перенос.
`git diff --check` обнаружил CR у изменённой строки в исходно смешанных концах строк
`cli.py`; только эта строка переведена в LF. Остальной файл не переформатирован.

## Модельное review и границы

Synthetic Astra xhigh выбрала срез и проверила итоговый код, тесты и контракт:
0 P1/P2. Ревью read-only, без запуска тестов; исполнение проверено Codex.
Это не human/production acceptance. Указанная `gpt-5.6-luna` недоступна,
поэтому реализацию выполнил Codex.

Полный offline suite и живые DWH не запускались. Ошибки самой environment probe
(`unsafe identifier`), ClickHouse `readonly=1` и `SettingsError` от complex/custom
settings sources вне среза. Текущие поля Settings — scalar; новые ограничения
значений и проверка доступности движка не добавлены.
