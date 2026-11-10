# `dwh.freshness`: фактическое покрытие — synthetic review

**Дата:** 2026-10-02. **Baseline:** `ba3bafa`.
**Выбор:** `gpt-6-astra`, reasoning `xhigh`, по ADR 0006. Реализация — Codex:
указанная правилами `gpt-5.6-luna` отсутствует среди доступных моделей сессии.
Модельная оценка не является human/design-partner или production acceptance.
**Итоговое synthetic Astra xhigh review:** 0 substantive findings;
независимо изучены фактический diff и весь новый регрессионный файл.

## Основание и контракт

`check_freshness` пропускает отсутствующие, нечитаемые, неатрибутированные и
закрытые deny-all row security вопросы. `_dated` проверяет наличие хотя бы одного
доступного вопроса, но прежний clean-detail говорил «every in-scope mart».
Свежая доступная часть скоупа поэтому описывалась как проверка всего скоупа.

В clean-ветке detail считает вопросы через `reading_for`, exists/readable и
`denies_all_rows`. Отсутствие freshness findings означает, что каждый такой
вопрос прошёл сравнение возраста; повторного парсинга времени нет. Единица
покрытия — пара (витрина, колонка времени), а не уникальное имя витрины.
Сводка называет N из M сравнений и complete/incomplete; положительный вывод
относится только к сравнённым возрастам. При нуле сравнений это названо явно.

Findings, severity, `_dated`, summary codes и verdict сохраняются. Прежний
`freshness.not_run` сохраняет текст об отсутствии датировки. Противоречивые
supplied facts `exists=False/readable=True` по-прежнему могут дать машинный код
`freshness.fresh`, но текст сообщает ноль сравнений; исправление этого кода
потребует отдельного grading-среза. Доступ при этом блокирует `dwh.access`.
Evidence verifier 71 → 72, check 50 без изменений.

## Проверки

- Red: `uv run --no-sync pytest -q --tb=short tests/test_freshness_coverage.py` —
  15 failed, 6 passed, exit 1. Все падения на прежнем безусловном clean-detail.
- Green: `uv run --no-sync pytest -q --tb=short tests/test_freshness_coverage.py
  tests/test_environment_question_identity.py tests/test_decision_verify.py
  tests/test_environment_checks.py tests/test_physical_coverage.py` —
  225 passed, exit 0.
- Новые публичные сценарии: два часа одной витрины, полное/частичное покрытие,
  unreadable/absent/missing/unattributed/duplicate/deny-all, оба порядка readings,
  постороннее чтение и ноль сравнений. Stale/unknown/invalid timestamp/calendar
  window сохраняют код, severity и точный finding detail.
- Ruff: `uv run --no-sync ruff check auto_bi tests` — чисто, exit 0.
- Black: `uv run --no-sync black --check auto_bi/verification/release_gate.py
  tests/test_freshness_coverage.py` — два файла чистые, exit 0.
- Docs-as-code: `uv run --no-sync pytest -q --tb=short tests/test_docs_as_code.py`
  — 24 passed, exit 0. Первый запуск: 23 passed / 1 failed, поскольку новый
  review-файл ещё не был в индексе; после scoped intent-to-add ссылки разрешены.
- Strict mypy: `uv run --no-sync --with mypy --with types-PyYAML mypy --strict auto_bi`
  — no issues in 120 source files, exit 0.
- Полный offline gate: `uv run --no-sync --with duckdb pytest -q --tb=short` —
  **3763 passed, 5 skipped, 38 deselected**, exit 0, 841,39 с.
  SQLite и CTE timing benchmarks прошли в составе полного набора.

## Границы

Timing-диагностика предыдущего среза не установила причину SQLite 9,4 с против
лимита 5 с: benchmark измеряет wall-clock 240 отдельных durable транзакций,
лог содержит только суммарное время. Изолированный зелёный запуск уже был
выполнен ранее и не повторялся как попытка закрыть gate. Store, бюджеты и
timing-тесты не менялись. SQL/probe, public schemas и historical bundles прежние.
Текущий полный gate зелёный; это не устанавливает причину прежнего замедления
и не доказывает устранение зависимости wall-clock benchmarks от среды.
