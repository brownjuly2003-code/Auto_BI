# T-67 — отказ драйвера при capture

Дата: 2026-10-22. Реализация: Codex. Windows, Python 3.13.7; нативные проверки.
Контракт: [unanswered-engine](specs/unanswered-engine.md).

## Изменение

`decision capture` отвечает на ошибки драйвера одной строкой, exit 2, без stdout и
файла результата. После `ClickHouse|PostgreSQL connection|query failed:` теперь указан
класс исключения и его текст с пробельными последовательностями, сжатыми до пробела.
Пустое сообщение оставляет только класс.

ClickHouse-адаптер ловит `ClickHouseError` и `StreamFailureError` при соединении,
основном запросе, metadata fallback и чтении `result_rows`. Последнее читает ленивый
поток драйвера; раньше оно было вне `try`. Обрыв после первого блока не публикует
частичный ответ и не повторяет запрос. PostgreSQL-адаптер сохраняет `psycopg.Error`
как границу перехвата. Остальные исключения в новый catch не добавлены.

Успешный путь, SQL, metadata, attestation и версии verifier 97 / check 72 сохранены.
Драйверный префикс сохранён; добавление класса — намеренное изменение текста ошибки.

## Проверки

- До правки: `test_result_probe.py` + `test_result_capture.py` — 13 passed.
- Новый `test_capture_driver_failures.py` на исходном коде — 30 failed, 9 passed:
  реальные `StreamFailureError`, неперехваченная материализация и многострочный вывод.
- После правки: новый модуль и два исходных capture-модуля — 52 passed.
  Позднее добавлены два случая `ConnectionTimeout`.
- Итоговый смежный прогон девяти модулей: **584 passed, 1 skipped**, exit 0,
  126.88 s. Включает реальные CLI-процессы для всех четырёх engine aliases.
  Единственный пропуск — существующий symlink-тест: Windows не разрешила создать ссылку.
- `python -m ruff check auto_bi tests` — exit 0.
- `python -m black --check auto_bi/verification/result_probe.py
  tests/test_capture_driver_failures.py tests/test_unanswered_engine.py` — exit 0.
- `uv run --frozen --no-sync --with mypy --with types-PyYAML mypy
  --platform linux --strict auto_bi` и та же команда с `--platform win32` —
  обе exit 0, 122 source files.

Смежный набор: `test_capture_driver_failures`, `test_result_probe`, `test_result_capture`,
`test_unanswered_engine`, `test_decision_cli`, `test_decision_verify`, `test_stated_run_facts`,
`test_unreadable_run_inputs`, `test_result_provenance` (все в `tests/`, расширение `.py`).
Команда: `.venv/Scripts/python.exe -m pytest -q <эти девять файлов> --tb=short`,
`PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8`.

Первый смежный запуск остановлен после задержки соединения и дополнительного ожидания
60 секунд. Второй, с `PGCONNECT_TIMEOUT=2`, завершился: 575 passed, 1 skipped, 7 failed.
Все семь падений — ожидание точного `OperationalError` вместо реально полученного
`ConnectionTimeout: connection timeout expired` (подкласс `psycopg.OperationalError`).
Фикстура закрытого порта теперь ограничивает ожидание libpq; сетевые проверки допускают
оба класса PostgreSQL и только `OperationalError` ClickHouse. Проверки exit, одной строки,
непустой причины и отсутствия результата сохранены. Отдельные детерминированные тесты
проверяют точное имя каждого класса, включая `ConnectionTimeout`.

## Модельное review и границы

Synthetic Astra xhigh выбрала срез и проверила код, тесты и контракт: 0 P1/P2.
Это чтение diff, не human/production acceptance; исполнение тестов проверено Codex.
Вторая узкая проверка Astra подтвердила допустимость `ConnectionTimeout` и сохранение
проверяемого контракта: 0 P1/P2. Обе модельные проверки были read-only, без запуска тестов.
Указанная в правилах `gpt-5.6-luna` недоступна, поэтому реализацию выполнил Codex.

Проверки используют драйверные исключения, настоящий ClickHouse `QueryResult` с
падающим генератором блоков и CLI-процессы на закрытом loopback-порту. Живой DWH,
полный offline suite, контейнеры и production acceptance в этом срезе не проверялись.
Настройки, ошибки environment probe, `readonly=1`, закрытие клиентских сессий и
таймауты production-соединений не изменялись.
