# T-69 — однострочный отказ quoting в environment probe

Дата: 2026-10-22. Реализация: Codex. Windows, Python 3.13.7; нативные проверки.
Контракт: [unanswered-engine](../specs/unanswered-engine.md).

## Изменение

`decision verify --dwh` возвращает exit 2, пустой stdout и одну строку
`error: cannot build environment query: unsafe identifier: …`, если `_ident`
коллектора не принимает временную колонку или компонент имени таблицы.
Evidence не создаётся; SELECT подсчёта строк и свежести не отправляется.

`UnsafeIdentifierError` — подкласс `ValueError`. Условие допуска и сообщение с
`repr` имени сохранены. CLI перехватывает только этот тип вокруг `collect`;
соединение остаётся вне нового обработчика. Ошибка не становится
`EnvironmentProbeError`: локальный отказ построения SQL отделён от отказа драйвера.
Прямые вызовы коллектора сохраняют совместимость с обработчиком `ValueError`.

Порядок соединения, чтения каталогов и quoting не менялся. Отсутствие таблицы и
ранняя нечитаемость PostgreSQL остаются фактами окружения. Неожиданные `ValueError`
и `RuntimeError`, даже с тем же текстом, пробрасываются. Offline verify, допустимые
идентификаторы, grading и версии verifier 97 / check 72 сохранены.

## Проверки

- Новый `test_environment_identifiers.py` до изменения кода: **20 failed, 8 passed**,
  exit 1, 4.95 s. Все 20 отказов — `ValueError` из `_ident`, а не ошибка fixture.
- После изменения: **42 passed**, exit 0, 4.72 s. Четыре engine aliases;
  временная колонка с пробелом и переносами, схема с пробелом, таблица с дефисом,
  пустой компонент; отсутствие stdout/evidence и неотправленный freshness SELECT.
  Дополнительно проверены границы исключений, прямой collector, отсутствующая
  таблица, нечитаемая PostgreSQL-таблица и offline verify.
- Смежные девять модулей: **406 passed**, exit 0, 85.59 s. Набор включает успешные
  пробы, ошибки драйверов, runtime cost, settings и application/CLI release gate.
- После форматирования и обновления документации: новый модуль вместе с
  `test_docs_as_code.py` и `test_docs_defaults.py` — **74 passed**, exit 0, 21.14 s.
- `python -m ruff check auto_bi tests`: exit 0.
- `python -m black --check auto_bi/cli.py auto_bi/verification/environment_probe.py
  tests/test_environment_identifiers.py`: exit 0.
- `uv run --frozen --no-sync --with mypy --with types-PyYAML mypy
  --platform linux --strict auto_bi` и та же команда с `--platform win32`:
  обе exit 0, 122 source files.
- `git diff --check`: exit 0.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m pytest -q tests/test_environment_identifiers.py tests/test_environment_probe.py tests/test_environment_question_identity.py tests/test_environment_checks.py tests/test_runtime_probe.py tests/test_unanswered_engine.py tests/test_decision_cli.py tests/test_decision_settings.py tests/test_decision_verify.py --tb=short
```

Первичная проверка стиля выявила длинную строку вызова и порядок импортов нового
теста; исправлены. У новых строк исходно смешанного `cli.py` убраны CR, остальной
файл не переформатирован. Финальный diff меняет только границу ошибки.

## Модельное review и границы

Synthetic Astra xhigh выбрала срез и проверила итоговые код, тесты и контракт:
**0 P1/P2**. Ревью read-only, без запуска тестов; исполнение проверено Codex.
Это не human/production acceptance. Указанная `gpt-5.6-luna` недоступна,
поэтому реализацию выполнил Codex.

Полный offline suite и живые DWH не запускались. Соединение и часть catalog queries
могут предшествовать отказу quoting. Поддержка новых SQL-идентификаторов, прочие
ошибки пробы/SQL guard и ClickHouse `readonly=1` остаются вне среза.
