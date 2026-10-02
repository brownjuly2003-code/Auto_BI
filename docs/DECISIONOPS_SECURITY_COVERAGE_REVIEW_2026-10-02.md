# `dwh.security`: фактическое покрытие — synthetic review

**Дата:** 2026-10-02. **Baseline:** `35a7b0f`.
**Выбор:** `gpt-6-astra`, reasoning `xhigh`, по ADR 0006. Реализация — Codex:
указанная правилами `gpt-5.6-luna` отсутствует среди доступных моделей сессии.
Модельная оценка не является human/design-partner или production acceptance.
**Итоговое synthetic Astra xhigh review:** 0 substantive findings;
независимо изучены фактический diff и весь новый регрессионный файл.

## Основание и контракт

Прежняя чистая сводка security сообщала «nothing narrows» и «every column»,
если хотя бы один вопрос был читаемым. При этом `check_security` пропускает
недоступные и неатрибутированные вопросы. PostgreSQL `_pg_column_reads` намеренно
возвращает только существующие колонки: отсутствующей занимается `dwh.physical`.
Поэтому существующий probe-путь мог дать `security.unfiltered` и обещание чтения
всех колонок одновременно с `physical.column_absent` и итоговым BLOCK.

Теперь clean-detail показывает два счётчика относительно всего скоупа:
явные `True` для запрошенных колонок и сообщённые `row_policies` по вопросам
(витрина, колонка времени). Атрибуция — `reading_for`, затем exists/readable.
Общая колонка разных вопросов учитывается отдельно. Незапрошенные колонки и
посторонние readings не увеличивают покрытие. `[]` — один ответ о политиках;
несколько политик другой роли — тоже один ответ, а `{}` — ноль подтверждений
чтения колонок. Вывод ограничен наблюдаемыми фактами; неполнота и оба нулевых
счётчика названы явно.

Findings, severity, `_readable`, summary codes и verdict прежние. В частности,
неполная карта прав не становится новым security finding: физическое отсутствие
колонки по-прежнему оценивается своей проверкой. Противоречивые supplied facts
`exists=False/readable=True` могут сохранить `security.unfiltered`, но detail
называет ноль наблюдений; `dwh.access` блокирует отсутствие витрины.
Verifier 72 → 73, check 50 без изменений.

## Проверки

- Red: `uv run --no-sync pytest -q --tb=short tests/test_security_coverage.py` —
  18 failed, 7 passed, exit 1; все падения на прежнем безусловном clean-detail.
- Дополнительный red для политик другой роли: та же команда с
  `-k other_role_policies` — 2 failed, 25 deselected, exit 1.
- Публичные регрессии охватывают два времени одной витрины, полное/частичное
  покрытие, unreadable/absent/missing/unattributed/duplicate, partial/empty maps,
  посторонние readings и колонки, оба порядка чтений, нулевые наблюдения.
- PostgreSQL `collect` с отсутствующей колонкой сохраняет physical BLOCK,
  security PASS и сообщает неполное покрытие. Контроли unknown catalogs,
  запрещённой колонки, фильтрации, deny-all и bypass сохраняют findings и detail.
- Green: `uv run --no-sync pytest -q --tb=short tests/test_security_coverage.py
  tests/test_freshness_coverage.py tests/test_physical_coverage.py
  tests/test_environment_question_identity.py tests/test_environment_checks.py
  tests/test_environment_probe.py tests/test_decision_verify.py` — 306 passed, exit 0.
- Docs-as-code: `uv run --no-sync pytest -q --tb=short tests/test_docs_as_code.py` —
  24 passed, exit 0.
- Strict mypy: `uv run --no-sync --with mypy --with types-PyYAML mypy --strict auto_bi`
  — no issues in 120 source files, exit 0.
- Black: `uv run --no-sync black --check auto_bi/verification/release_gate.py
  tests/test_security_coverage.py` — два файла чистые, exit 0.
- Ruff: `uv run --no-sync ruff check auto_bi tests` — чисто, exit 0.
  Первоначальное E501 исправлено переносом строкового литерала без изменения текста.
- Полный offline gate: `uv run --no-sync --with duckdb pytest -q --tb=short` —
  **3790 passed, 5 skipped, 38 deselected**, exit 0, 886,99 с.

## Границы

SQL/probe, grading, public schemas, historical bundles и timing budgets
не менялись. Живой DWH для детерминированного текста не запускался.
