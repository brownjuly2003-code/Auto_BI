# T-80 — управляемый отказ SQL guard в environment probe

Дата: 2026-10-22. Исходный HEAD: `e5f7121`.
Реализация: Codex; требуемая `gpt-5.6-luna` недоступна.
Выбор: synthetic Astra xhigh по ADR 0006, не human/production acceptance.

## Изменение и границы

Закрыта документированная operational boundary T-65/T-69: `SQLGuardError` из
`collect` в `decision verify --dwh` даёт exit 2 и ровно одну строку
`error: cannot execute environment query: SQLGuardError`. Stdout пуст, evidence
не создаётся. Текст исключения скрыт, поскольку parser diagnostics включают SQL,
переносы строк и ANSI. Отказ не превращается в измерение, verdict или повтор.
Одна строка гарантирована для обычного CLI logging: `NullHandler` у `sqlglot`
убирает fallback `logging.lastResort`, также способный напечатать SQL. Установленные
приложением handlers, levels и propagation сохранены; они получают parser records,
и их вывод не входит в гарантию. Handler добавляется один раз перед `collect`,
после catalog connection factory.

Перехват ограничен `collect`; catalog connection factory остаётся снаружи.
Прямые collector/runtime вызовы сохраняют `SQLGuardError`; посторонние исключения,
включая `sqlglot.errors.TokenError`, пробрасываются. Guard, порядок соединений,
catalog queries, расчёты и версии verifier 98/check 73 не менялись.
Каталоги и создание runtime connection могут уже состояться до отказа.

Воспроизведение передаёт запрещённый SQL через `PlanQuestion`, использует fake DWH
и настоящий runtime guard. Доказан отказ интерфейса пробы; accepted CLI input,
из которого обычный typed compiler выдаёт такой SQL, не установлен.
Запрещённый runtime SQL не исполняется. Это не live-DWH acceptance.

## Проверки

- Red до правки production-кода: **12 failed, 18 passed**, exit 1. Remote table
  function, незавершённый FROM и несколько statements × четыре engine aliases.
- Первый red использовал незакрытую строку: она даёт `TokenError`, а не
  `SQLGuardError`. Для parser-ветки вход уточнён до `SELECT 'private-input'\nFROM`;
  посторонний `TokenError` сохранён отдельным контролем, guard не расширялся.
- После правки: **30 passed in 2.23s**, exit 0. Проверены точная диагностика,
  отсутствие stdout/evidence/runtime execution, прямой collector, factory boundary
  и идентичность неожиданных исключений.
- Первый synthetic Astra review выявил P2: `DROP private_input_marker` печатал SQL
  через sqlglot warning до исключения. Red без pytest root handlers: **4 failed**,
  exit 1. После добавления `NullHandler`: **16 passed, 26 deselected**, exit 0;
  проверены все aliases, сохранение настроенного logging и граница factory.
- Ruff/Black изменённых Python-файлов: exit 0.
- Финальный смежный набор: **448 passed in 84.76s**, exit 0; десять модулей,
  включая 42 T-80 cases, успешные пробы, driver/settings/identifier ошибки,
  runtime cost и application/CLI gate.
- Strict mypy Linux и Windows: **122 source files**, оба exit 0.
- Native comparison исполнил исходную `HEAD:_environment_probe` и новую функцию
  с одними inputs и fake DWH. Для каждого из четырёх engine aliases — completed run,
  exit 4 и **20 файлов bundle побайтно совпали**. Это сохранённый BLOCK verdict,
  а не ошибка запуска или production acceptance.
- Docs-as-code/defaults: **32 passed in 0.79s**, exit 0.
- Synthetic Astra xhigh review, два целевых прохода: найденный P2 закрыт,
  **0 открытых P1/P2**. Независимая повторная проба подтвердила отсутствие SQL
  warning в stderr, ожидаемый `VerifyError` и 0 runtime-команд с fake DWH.
  Это модельное review, не human/production acceptance.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m pytest -q tests/test_environment_sql_guard.py --tb=short
.venv\Scripts\python.exe -m pytest -q tests/test_environment_sql_guard.py -k command-fallback --tb=short
.venv\Scripts\python.exe -m pytest -q tests/test_environment_sql_guard.py -k 'command-fallback or configured_parser or connection_factory' --tb=short
.venv\Scripts\python.exe -m pytest -q tests/test_environment_sql_guard.py tests/test_environment_identifiers.py tests/test_environment_probe.py tests/test_environment_question_identity.py tests/test_environment_checks.py tests/test_runtime_probe.py tests/test_unanswered_engine.py tests/test_decision_cli.py tests/test_decision_settings.py tests/test_decision_verify.py --tb=short
.venv\Scripts\python.exe -m pytest -q tests/test_docs_as_code.py tests/test_docs_defaults.py --tb=short
.venv\Scripts\python.exe -m ruff check auto_bi/cli.py tests/test_environment_sql_guard.py
.venv\Scripts\python.exe -m black --check auto_bi/cli.py tests/test_environment_sql_guard.py
uv run --frozen --no-sync --with mypy --with types-PyYAML mypy --platform linux --strict auto_bi
uv run --frozen --no-sync --with mypy --with types-PyYAML mypy --platform win32 --strict auto_bi
```

Полный offline suite и живые DWH не запускались. До изменений baseline
investigation прошёл: 348 passed; его код этим срезом не меняется.
