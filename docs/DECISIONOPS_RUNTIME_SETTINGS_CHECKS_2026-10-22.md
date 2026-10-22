# T-70 — отказ runtime-настройки ClickHouse оставляет бюджет неизмеренным

Дата: 2026-10-22. Реализация: Codex; выбор среза — synthetic Astra xhigh.
Контракт: [unanswered-engine](specs/unanswered-engine.md).

## Изменение

`ProgrammingError: Setting <name> is unknown or readonly` для одной из переданных
runtime-настроек возвращает `ExecutionFacts` без counters, checked bounds и exceeded
bound. `dwh.cost` выдаёт `cost.not_measured` (review) для каждого объявленного потолка.
Факт называет отказанную настройку. Запрос без ограничений не повторяется; каталог
остаётся в evidence. Сам `readonly=1` не запрещает попытку: драйвер допускает
настройку, уже равную требуемому значению.

Четыре возможных имени: `max_execution_time`, `timeout_overflow_mode`,
`max_bytes_to_read`, `read_overflow_mode`; последние два передаются только при
объявленном байтовом потолке. Посторонние имена, классы и сообщения не перехватываются.
Настоящее превышение требует server code 159 для времени или 307 для байтов с
сообщением о превышении лимита чтения. Строки и размер результата не доказывают
scanned-byte excess. `.code` приоритетен; для прежних драйверов читается `Code:`
из известной формы ответа. Safety timeout сохраняет прежнюю семантику.

Версии повышены до verifier 98 / check 73: меняется evidence существующей проверки.
Исправлено прежнее ошибочное описание readonly refusal как usage error. Старые
bundles и исторические отчёты не переписаны.

## Проверки

- Первичный regression red: **30 failed, 2 passed**, exit 1. Перед этим исправлены
  две ошибки импорта в новом тестовом модуле; они не считаются доказательством дефекта.
- Проверки реальных сообщений движка выявили дополнительные **3 failed, 1 passed**
  до уточнения кода 307 и текстового fallback. Старый fixture ошибочно называл код 158;
  его исправили по живому ответу, сохранив assertion о превышении байтов.
- Финальный смежный набор: **368 passed**, exit 0, 70.10 s, Windows/Python 3.13.7.
  Он проверяет отказ каждой настройки, zero/safety/bytes-only budgets, отсутствие
  повторного command, прочие driver errors, structured-code precedence, запечатанный
  cost artifact и сохранение catalog facts.
- Ruff и Black по четырём изменённым Python-файлам: exit 0.
- Package-wide strict mypy для `linux` и `win32`: exit 0, по 122 source files.
- Документационные проверки: **25 passed**, exit 0; `git diff --check` чистый.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m pytest -q tests/test_runtime_setting_refusals.py tests/test_runtime_probe.py tests/test_environment_probe.py tests/test_environment_question_identity.py tests/test_environment_checks.py tests/test_unanswered_engine.py tests/test_decision_cli.py tests/test_decision_verify.py --tb=short
uv run --frozen --no-sync --with mypy --with types-PyYAML mypy --platform linux --strict auto_bi
uv run --frozen --no-sync --with mypy --with types-PyYAML mypy --platform win32 --strict auto_bi
```

## Живая проверка и границы

Одноразовый ClickHouse **24.8.14.39**, только loopback, запущен на Mac через SSH;
на Windows стенд не запускался. Временный server завершён, его каталоги удалены
контекстом `TemporaryDirectory`. Итоговый процесс: exit 0, `T70_LIVE_COMPLETE`.

На исходном runtime при `readonly=1` `SELECT 1` дал ложный `exceeded_bound=timeout_ms`.
После исправления: пустые checked bounds и отсутствующий exceeded bound. При
`readonly=2` тот же SELECT завершился и подтвердил оба ненулевых потолка.
`numbers(1000000)` под byte limit 1 дал code 307, `TOO_MANY_BYTES`, с текстом
`Limit for rows or bytes to read exceeded, max bytes: 1.00 B`.
Агрегация `numbers(1000000000000)` с timeout 1 ms дала code 159, `TIMEOUT_EXCEEDED`.
Финальный runtime вернул соответствующие typed exceeded bounds для обоих отказов.
Первый запуск сервера не подтвердил readiness; следующий диагностический
запуск состоялся. Прогоны после правок проверяли изменённый классификатор.

Финальное synthetic Astra xhigh review кода, тестов и документации: **0 P1/P2**.
Ревью read-only; тесты независимо запускал Codex. `gpt-5.6-luna` недоступна,
поэтому реализацию выполнил Codex. Полный offline suite, PostgreSQL и production-стенд
не запускались. Результат не является human/production acceptance.
