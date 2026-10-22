# T-71 — справка CLI при ограниченной кодировке вывода

Дата: 2026-10-22. Реализация: Codex; выбор — synthetic Astra xhigh по ADR 0006.
`gpt-5.6-luna` недоступна. Контракт: [unencodable-output](specs/unencodable-output.md).

## Изменение

Документированная граница T-60 воспроизведена: root `--help` и `eval --help`
падают на тире под ASCII/Latin-1. `_HelpArgumentParser.print_help` сначала пишет
исходный текст, а после `UnicodeEncodeError` печатает его с `backslashreplace`
в кодировке потока (ASCII, если она не названа). Вложенные parser наследуют класс.
Форматирование, перевод строки и выбранный поток сохраняются. Нормальная справка
завершается exit 0 с пустым stderr; другие ошибки записи пробрасываются, включая
`OSError`/`BrokenPipeError`, которые стандартный argparse подавлял.

Только `print_help`: usage/errors/version и тела v1/decision-команд не менялись.
Глобальные stdout/stderr не заменяются и не перенастраиваются. JSON и evidence
payload сохраняют прежние отдельные правила. Версии verifier 98 / check 73 прежние.

## Проверки

- Новый модуль на исходном коде: **9 failed, 47 passed**, exit 1. Семь отказов
  связаны с кодировкой, два — с подавлением OSError/BrokenPipeError в argparse.
- После исправления: **56 passed**, exit 0. Пять кодировок, root/nested help,
  точное сравнение байтов, коды и потоки parser messages, отсутствие изменения
  streams, unnamed stream, прочие ошибки записи. ASCII/Latin-1 проверены также
  настоящими процессами `python -m auto_bi.cli --help`.
- Первый смежный прогон: **1 failed, 241 passed**. AST-проверка T-60 ожидала
  только пять команд, хотя T-68 добавил `_decision_settings`. Один и тот же
  отказ подтверждён отдельно на исходнике HEAD, извлечённом через `git show`.
  Helper добавлен в проверяемый список; запрет прямой печати сохранён для всех.
- После review добавлены два случая явного `print_help(file=stream)`: ASCII и UTF-8,
  точный эталон из стандартного argparse, stdout/stderr пусты. Финальный смежный
  набор: **244 passed**, exit 0, 22.76 s, Windows/Python 3.13.7.
- Ruff и Black по трём изменённым Python-файлам: exit 0. Black первоначально
  запросил форматирование нового тестового файла; после форматирования чисто.
- Package-wide strict mypy `linux` и `win32`: exit 0, по 122 source files.
- Все десять вариантов help/version/usage/error отдельно сравнены с кодом HEAD:
  на Unicode-потоке текст stdout/stderr и коды выхода полностью совпали.
- Документационные проверки: **32 passed**, exit 0. Первое падение link-check
  требовало зарегистрировать новый отчёт в Git; после intent-to-add ссылки разрешены.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m pytest -q tests/test_parser_output_encoding.py tests/test_unencodable_output.py tests/test_cli_offline.py tests/test_decision_cli.py tests/test_investigation_cli.py tests/test_platform_guards.py --tb=short
uv run --frozen --no-sync --with mypy --with types-PyYAML mypy --platform linux --strict auto_bi
uv run --frozen --no-sync --with mypy --with types-PyYAML mypy --platform win32 --strict auto_bi
```

## Границы

Финальное synthetic Astra xhigh review: **0 P1/P2**. Единственный дополнительный
acceptance про явный поток вывода закрыт двумя тестами выше; прочих изменений review
не потребовало. Проверки независимо выполнял Codex.

Ограниченная кодировка stderr вызывающего процесса по-прежнему может отказать
при выводе ошибки аргументов; срез исправляет только справку. Не гарантируется
поведение нестандартного потока, который пишет часть текста до UnicodeEncodeError.
Полный offline suite и живые стенды не запускались; выбор кодировки настоящей
Windows-консолью не проверялся. Это не human/production acceptance.
