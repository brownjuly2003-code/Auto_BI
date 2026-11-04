# T-110: Mac offline-проверка после T-109

Результат под каждым из трёх интерпретаторов: **6919 passed, 20 skipped, 38 deselected**,
exit 0. Black, Ruff и три режима mypy — exit 0. Код и тесты не менялись.

## Проверенное состояние

- Commit: `5cb2c08d679e2a0ea0be07467f347320cf8037c8` (T-109).
- Tree: `87fd74c`; на Mac — тот же tree поверх `5cb2c08`, `git status` до и после чистый.
- iMac Intel i5-7500, macOS 13.7.8; нативно, без Docker, Colima или VM.
- Python 3.13.13, 3.12.13 и 3.14.6 — три отдельных venv, собранных заново из `uv.lock`
  в этой сессии; DuckDB добавлен через `--with` и присутствовал.
- Рабочий каталог `/tmp/autobi_t47` пересобран: ежедневная очистка `/tmp` в macOS удалила
  файлы прежних venv (каталоги остались), и первый запуск завершился за 44 s без тестов.
  Его exit codes — отказ среды, не результат проверки.

## Команды и результаты

```text
uv run --no-sync --with duckdb python -m pytest -q -p no:randomly -rs --tb=short --basetemp=<tmp>
uv run --no-sync black --check auto_bi tests
uv run --no-sync ruff check . --output-format concise
uv run --no-sync --with mypy --with types-PyYAML mypy --platform linux auto_bi
uv run --no-sync --with mypy --with types-PyYAML mypy --platform linux --strict auto_bi
uv run --no-sync --with mypy --with types-PyYAML mypy --platform win32 --strict auto_bi
```

pytest запускался под 3.13, 3.12 и 3.14 (`UV_PROJECT_ENVIRONMENT` и `UV_PYTHON` на
соответствующий venv): 282 s, 302 s и 283 s, exit 0 каждый. Линтеры и mypy — под 3.13:
Black проверил 353 файла, каждый режим mypy — 123 source files. Скрипт —
`.tmp/t110/full_mac.sh`, журнал на Mac — `/tmp/autobi_t47/logs/full109.log`.

## Пропуски и границы

20 skips, одинаковые под тремя интерпретаторами: 11 Windows junction, 2 Windows file ACL,
1 Windows read-only replacement, 2 имени, которые файловая система macOS отвергает
(`Illegal byte sequence`), 2 browser-модуля без Playwright, 2 проверки отсутствующего
Anthropic SDK. 38 deselected — штатное исключение integration/e2e через `pyproject.toml`.
Полный Windows-прогон T-109 (тот же код и тесты) пропускает 91 тест: 86 проверок POSIX
modes и отказов доступа, 1 named pipe, 2 browser-модуля и 2 проверки SDK. Первые 87 здесь
исполнены. Для тестов T-98…T-109 это первый прогон вне Windows: прежние
Mac- и Linux-проверки относились к дереву T-97.

Это offline gate на macOS, не весь CI: coverage, browser, mutation, eval, dependency-audit
и package jobs не запускались. `mypy --platform linux` проверяет типы, а не исполнение на
Linux: Linux/Python 3.12 проверен отдельно —
[отчёт](DECISIONOPS_LINUX_CHECKS_2026-10-24.md). Live DWH не проверялись. Версии
evidence 109/74.
