# T-72 — диагностика argparse на строгом stderr

Дата: 2026-10-22. Реализация: Codex; выбор — synthetic Astra xhigh по ADR 0006.
`gpt-5.6-luna` недоступна. Контракт: [unencodable-output](specs/unencodable-output.md).

## Изменение

Документированная граница T-71 воспроизведена на исходном `452c6d1`: ошибочная
Unicode-команда или option вызывает `UnicodeEncodeError` при strict stderr.
Цепочка argparse `error → exit → _print_message` успевает вывести usage, но теряет
диагностику и штатный exit 2. Обычный stderr Python уже использует backslash-escape;
дефект относится к строгому потоку вызывающего приложения.

Узкий override `_print_message` сначала вызывает стандартный printer, а после
`UnicodeEncodeError` повторяет только отказавшее сообщение с `backslashreplace`
в кодировке effective stream (ASCII, если кодировка не названа). Выбор потока
`file or sys.stderr` и подавление `AttributeError`/`OSError` сохраняются на обеих
попытках. Usage/errors/version сохраняют форматирование, направления и exit codes.
`print_help` сохраняет отдельный T-71 контракт, включая проброс прочих ошибок записи.
Потоки не заменяются и не перенастраиваются; версии verifier 98 / check 73 прежние.

## Проверки

- До исправления: **27 failed, 69 passed**, exit 1. Unicode root/nested choice,
  неизвестная option, неверный integer, lone surrogate; strict stderr пяти кодировок.
- После исправления: **96 passed**, exit 0. Точное сравнение байтов, exit codes,
  stdout/stderr, явный и неназванный поток, неизменность настроек streams,
  non-encoding errors; два настоящих процесса с явно строгим ASCII/Latin-1 stderr.
- Смежные CLI, decision, investigation и platform checks: **282 passed**, exit 0,
  Windows/Python 3.13.7, 30.57 s.
- Ruff и Black по двум изменённым Python-файлам: exit 0. Первоначальная длинная строка
  исправлена форматированием; production-изменение ограничено подклассом parser.
- Package-wide strict mypy `linux` и `win32`: exit 0, по 122 source files.
- Документационные проверки: **32 passed**, exit 0; `git diff --check` чистый.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m pytest -q tests/test_parser_output_encoding.py tests/test_unencodable_output.py tests/test_cli_offline.py tests/test_decision_cli.py tests/test_investigation_cli.py tests/test_platform_guards.py --tb=short
uv run --frozen --no-sync --with mypy --with types-PyYAML mypy --platform linux --strict auto_bi
uv run --frozen --no-sync --with mypy --with types-PyYAML mypy --platform win32 --strict auto_bi
```

## Review и границы

Итоговое synthetic Astra xhigh review: **0 P1/P2**, срез принят в согласованных границах.
Ревьюер независимо проверил пять сценариев: четыре типа ошибки на повторной записи и
выбор stderr при falsey `file`; exit 0. Основные проверки выполнял Codex.
Это не human/production acceptance.

Полный offline suite и живые стенды не запускались; выбор кодировки настоящей
Windows-консолью не проверялся. Не гарантируется поведение нестандартного потока,
который пишет часть сообщения до `UnicodeEncodeError`. Обработчики команд, JSON
и содержимое evidence этим срезом не менялись.
