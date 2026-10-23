# Linux offline-проверка после T-97 — 2026-10-23

Результат: **6813 passed, 17 skipped, 38 deselected**, exit 0.
Покрытие `auto_bi`: **95.93%**, порог 90% выполнен.
Ruff, Black, mypy, strict mypy Linux и strict mypy win32 — exit 0.
Продуктовый код, тесты и версии evidence 108/73 не менялись.

## Проверенное дерево и среда

- Commit: `d5e9aafa7c9b1f825a4942c09930f9154e781a96`.
- Tree: `445345a30627a421eb44b60b1b4608254979961a`.
- Источник: `git archive HEAD`, без untracked файлов. Git-индекс в контейнере
  восстановлен из точного списка `git ls-files -z`; `git write-tree` совпал с tree выше.
- Host: `imac-julia`, Intel Mac. Изолированный Colima `autobi-linux`, 2 CPU / 3 GiB,
  диск 20 GiB, без Kubernetes, mount и port forwarding; другие профили не запускались.
- Container: 2 CPU / 2 GiB, Linux x86_64, kernel `6.8.0-100-generic`.
- Python: CPython 3.12.15, образ `python:3.12-slim`, полученный digest:
  `sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704`.
- Зависимости проекта: `uv sync --frozen`; Git установлен через apt.
- Финальный pytest выполнен непривилегированным пользователем `gate`,
  `PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8`; длительность 622.19 s.

## Команды

```text
uv run --with duckdb --with pytest-cov pytest -q -rs --cov=auto_bi --cov-report=term --cov-fail-under=90
uv run ruff check .
uv run black --check auto_bi tests
uv run --with mypy --with types-PyYAML mypy auto_bi
uv run --with mypy --with types-PyYAML mypy --strict auto_bi
uv run --with mypy --with types-PyYAML mypy --platform win32 --strict auto_bi
```

Black проверил 344 файла; каждый режим mypy — 122 source files.
Статические проверки выполнены в первом прогоне на том же исходном дереве.
Первый pytest: 6726 passed, 4 failed, 100 skipped; все четыре сбоя — отсутствие
`git` в slim-образе. После установки Git и восстановления индекса полный набор
повторён от обычного пользователя и прошёл. Тесты не удалялись и не отключались.

17 skips: 2 browser-модуля без Playwright; 10 Windows junction кейсов;
3 Windows replacement/ACL кейса; 2 missing-Anthropic-SDK кейса.
38 deselected — штатное исключение integration/e2e через настройки проекта.

## Границы и артефакты

Это offline pytest, coverage и статические проверки Linux/Python 3.12, не весь CI.
Отдельные mutation, eval, browser, package и dependency-audit jobs не запускались.
Linux/Python 3.13–3.14, live DWH и Windows runtime этим прогоном не подтверждены;
`--platform win32` проверяет только типы Windows-ветвей.
Образ был выбран по tag; digest записан по факту. Инструменты `--with`, uv и apt Git
не закреплены lock-файлом проекта; полная воспроизводимость среды не заявляется.

Журналы на Mac: `/tmp/autobi-linux-d5e9aaf/results-final/` и
`/tmp/autobi-linux-d5e9aaf/session.log`; локальная копия pytest:
`.tmp/linux-isolated-final.log`. Сценарии в `.tmp/linux-gate.sh` и
`.tmp/linux-gate-retry.sh` — временные артефакты этой проверки.
Тестовый контейнер удалён; `colima stop autobi-linux` завершился с exit 0.
Выделенный профиль сохранён остановленным для последующих проверок.
Предыдущая попытка в общем профиле описана в
[отдельном отчёте](DECISIONOPS_LINUX_GATE_ATTEMPT_2026-10-23.md).

Synthetic Astra xhigh review сценария и финального отчёта: 0 P1/P2;
это не human/production acceptance. После обновления документации локальный
`pytest tests/test_docs_as_code.py -q`: 25 passed.
