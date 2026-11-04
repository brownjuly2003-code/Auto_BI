# T-105: Windows offline-проверка после T-104

Результат: **6786 passed, 118 skipped, 38 deselected**, exit 0.
Покрытие `auto_bi`: **96.15%**, порог 90% выполнен.
Ruff, Black и три режима mypy — exit 0. Код и тесты не менялись.

## Проверенное состояние

- Commit: `0d17bf30340582b13dbea5a2e8bc28179fdbd5f4`.
- Tree: `bbcd1c46e40c91a495f7e2723c45ed9145307da3`.
- Нативный Windows, Python 3.13.7; без WSL, Docker или VM.
- Проверялся рабочий checkout: tracked-файлы чистые до и после прогона.
  Чужие untracked `.claude/`, `.myflow/`, `docs/SESSION_HANDOFF.md` сохранены.
- Перед запуском свободно 6851 MiB из 15894 MiB; тяжёлые проверки последовательные.
  Это начальный замер, не измерение пикового потребления.
- `PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8`.
- pytest 9.1.1, pytest-cov 7.1.0, coverage 7.17.0, DuckDB 2.0.0.
- Один полный pytest, 931.48 s; перезапусков не было.

## Команды и результаты

```text
uv run --with pytest-cov --with duckdb pytest -q -rs --tb=short --cov=auto_bi --cov-report=term --cov-fail-under=90
uv run ruff check .
uv run black --check auto_bi tests
uv run --with mypy --with types-PyYAML mypy --platform linux auto_bi
uv run --with mypy --with types-PyYAML mypy --platform linux --strict auto_bi
uv run --with mypy --with types-PyYAML mypy --platform win32 --strict auto_bi
```

Каждая команда завершилась с exit 0. Black проверил 351 файл;
каждый режим mypy — 122 source files. Pytest запущен attached-обёрткой
`.tmp/t105_gate.py`, которая сохраняет stdout/stderr в UTF-8 и exit code.
`COVERAGE_FILE` направлен в `.tmp/t105/.coverage`.
Полный журнал: `.tmp/t105/pytest.log`; код выхода: `.tmp/t105/pytest.exit`.
Инструменты `--with` получены отдельно от lock-файла проекта.

## Пропуски и границы

118 skips: 27 проверок без права создавать symlink; 86 проверок POSIX modes/отказов
доступа, не воспроизводимых в этой Windows-среде; 1 named pipe; 2 browser-модуля без
Playwright; 2 проверки отсутствующего SDK при установленном Anthropic SDK.
38 deselected — штатное исключение integration/e2e через `pyproject.toml`.
DuckDB присутствовал; численные проверки SQL не пропущены из-за его отсутствия.

Это Windows offline gate, не весь CI: отдельные browser, mutation, eval,
dependency-audit и package jobs не запускались. Mac/Linux runtime и live DWH не
проверялись; `mypy --platform linux` проверяет типы, а не исполнение Linux-кода.
Версии evidence 108/73 сохранены. Изменения этого среза — только документация.

Выбор среза и synthetic Astra xhigh review отчёта: 0 actionable findings;
это не human/production acceptance. После добавления отчёта в Git-индекс
`uv run pytest tests/test_docs_as_code.py -q` — 25 passed, exit 0.
Первый docs-прогон дал 1 failed/24 passed: ссылки на новый ещё untracked отчёт;
код и тесты для устранения этого отказа не менялись.
