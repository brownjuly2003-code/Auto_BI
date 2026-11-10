# T-110: Linux offline-проверка после T-109 — 2026-10-24

Результат: **6921 passed, 18 skipped, 38 deselected**, exit 0.
Покрытие `auto_bi`: **96.00%**, порог 90% выполнен.
Ruff, Black, mypy, strict mypy Linux и strict mypy win32 — exit 0. Код и тесты не менялись.

## Проверенное дерево и среда

- Commit: `636772f` — T-109 `5cb2c08` и запись Mac-проверки; код и тесты те же.
- Tree: `73caf7a9be03e0f3cb19ef2dde16e376302ae319`.
- Источник: `git archive HEAD`, без untracked файлов. Git-индекс в контейнере собран из
  точного списка `git ls-files -z`; `git write-tree` совпал с tree выше (сценарий сверяет
  их и иначе завершается до тестов).
- Host: iMac, Intel i5-7500, macOS 13.7.8. Изолированный Colima `autobi-linux`,
  2 CPU / 3 GiB, диск 20 GiB; другие профили не запускались. Профиль запущен перед
  проверкой и остановлен после неё (`colima stop` — exit 0).
- Container: 2 CPU / 2 GiB без swap, Linux x86_64, kernel `6.8.0-100-generic`.
- Python: CPython 3.12.15, образ `python:3.12-slim` по digest
  `sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704` — тот же, что
  в проверке 2026-10-23, из кэша профиля.
- uv 0.13.5 (через pip), Git через apt; зависимости проекта — `uv sync --frozen`,
  pytest 9.1.1.
- Все проверки — от непривилегированного пользователя `gate`, `PYTHONUTF8=1`,
  `PYTHONIOENCODING=utf-8`; pytest — 646.34 s.

## Команды

```text
uv sync --frozen
uv run --no-sync --with duckdb --with pytest-cov pytest -q -rs --tb=short --cov=auto_bi --cov-report=term --cov-fail-under=90
uv run --no-sync ruff check .
uv run --no-sync black --check auto_bi tests
uv run --no-sync --with mypy --with types-PyYAML mypy auto_bi
uv run --no-sync --with mypy --with types-PyYAML mypy --strict auto_bi
uv run --no-sync --with mypy --with types-PyYAML mypy --platform win32 --strict auto_bi
```

Один запуск, без перезапусков. Black проверил 353 файла; каждый режим mypy —
123 source files.

18 skips: 2 browser-модуля без Playwright; 11 Windows junction кейсов; 3 Windows
replacement/ACL кейса; 2 missing-Anthropic-SDK кейса. 38 deselected — штатное исключение
integration/e2e через настройки проекта. Два теста имён, которые файловая система macOS
отвергает (`Illegal byte sequence`), здесь исполнены: 6921 + 18 = 6919 + 20 у Mac.

## Границы и артефакты

Это offline pytest, coverage и статические проверки Linux/Python 3.12, не весь CI:
mutation, eval, browser, package и dependency-audit jobs не запускались.
Linux/Python 3.13–3.14 и live DWH не проверялись. Инструменты `--with`, uv и apt Git
не закреплены lock-файлом проекта; полная воспроизводимость среды не заявляется.

Сценарии: `.tmp/t110/linux_host.sh` (профиль, контейнер, копирование входов и журналов,
очистка) и `.tmp/t110/linux_container.sh`. Журналы: `.tmp/t110/linux-pytest.log` и
`.tmp/t110/linux-container.log`; на Mac — `/tmp/autobi-linux-636772f/`.
Тестовый контейнер удалён (`docker rm` — exit 0).
Mac-проверка того же кода — [отдельный отчёт](DECISIONOPS_MAC_CHECKS_2026-10-24.md).
