# `dwh.physical`: фактическое покрытие — synthetic review

**Дата:** 2026-10-02. **Baseline:** `96873fb`.
**Выбор:** `gpt-6-astra`, reasoning `xhigh`, по ADR 0006. Реализация — Codex.
**Итоговое synthetic Astra xhigh review:** 0 незакрытых substantive findings.
Модельная оценка не является human/design-partner или production acceptance.

## Основание и контракт

Astra воспроизвела чистую сводку «every column … declared type» и «the layout
matches» при `columns=None` и `LayoutFacts()` со всеми NULL. Тот же текст появлялся
при известном table_engine и неотвеченном, но объявленном sorting_key.
Причина — `_physical_clean` считала наличие объекта layout полным сравнением,
а утверждение о колонках было безусловным.

Теперь сводка сообщает число реально сравненных типов колонок и заявленных
свойств layout относительно всех деклараций скоупа. Единица счёта — поле вопроса
(витрина, колонка времени): общая колонка двух вопросов учитывается дважды.
`reading_for`, exists/readable и сообщённое значение определяют покрытие.
Неполнота и отсутствие сравнений названы явно. Дополнительные ненужные колонки,
необъявленные свойства layout и неатрибутированные чтения не увеличивают покрытие.
Пустое сообщённое layout-значение — ответ, NULL — отсутствие ответа.
Пустая строка после соединения объявленного sorting/distribution key, включая
список `[""]`, не является декларацией: критерий совпадает с `_layout_findings`.

Findings, severity, summary codes и verdict прежние. Поэтому `physical.as_declared`
и PASS сохраняются при чистом частичном сравнении, но detail ограничивает вывод
сравнёнными фактами. При недоступной витрине причина BLOCK остаётся в `dwh.access`.
Это изменение evidence: verifier 70 → 71; check 50 без изменений.

## Проверки

- Red: `uv run --no-sync pytest -q --tb=short tests/test_physical_coverage.py` —
  15 failed, exit 1; падения на прежних безусловных утверждениях detail.
- После реализации: coverage, environment question identity, decision verify и
  environment checks — 200 passed, exit 0.
- После добавления публичного случая двух колонок времени:
  `uv run --no-sync pytest -q --tb=short tests/test_physical_coverage.py
  tests/test_environment_question_identity.py` — 61 passed, exit 0.
- Astra нашла P2: список `[""]` ошибочно считался объявленным layout-полем.
  До исправления `-k empty_joined` — 2 failed, 17 deselected, exit 1.
  После исправления итоговая связанная группа — 204 passed, exit 0;
  Astra независимо подтвердила 2 passed, 17 deselected, exit 0.
- `uv run --no-sync black --check auto_bi/verification/release_gate.py
  tests/test_physical_coverage.py tests/test_environment_question_identity.py` —
  три файла чистые, exit 0.
- `uv run --no-sync ruff check auto_bi tests` — чисто, exit 0.
- `uv run --no-sync --with mypy --with types-PyYAML mypy --strict auto_bi` —
  no issues in 120 source files, exit 0.
- Промежуточный полный прогон: 3 failed, 3737 passed, 5 skipped, 38 deselected.
  Исправлены старая версия в архитектуре и неиндексированный review-файл;
  `uv run --no-sync pytest -q --tb=short tests/test_docs_as_code.py` — 24 passed,
  exit 0. Третий сбой — существующий wall-clock тест
  `test_cte_gate_walk_on_a_long_chain_stays_linear`: 12,10× при лимите 8×;
  код обхода и этот тест не менялись.
- Итоговый `uv run --no-sync --with duckdb pytest -q --tb=short`:
  **1 failed, 3741 passed, 5 skipped, 38 deselected**, exit 1, 1190,78 с.
  Единственный сбой — неизменённый `test_sqlite_session_write_and_backup_budgets`:
  9401,3 мс записи при лимите 5000 мс. CTE benchmark и docs-as-code прошли.
- Узкая диагностика после полного прогона:
  `uv run --no-sync pytest -q --tb=short --durations=1
  tests/test_perf_baseline.py::test_sqlite_session_write_and_backup_budgets` —
  **1 passed**, exit 0, весь вызов теста 1,25 с. Это подтверждает переменное
  время исполнения, но не устанавливает его причину и не заменяет зелёный полный gate.

## Границы

Другие `dwh.*` сводки, SQL/probe, public schemas и historical bundles не менялись.
Живые движки для изменения детерминированного текста не запускались.
Полный gate остаётся неуспешным из-за timing benchmark; его код и пороги
не менялись. Изменение сводки подтверждено отдельным полным профильным набором
из 204 тестов и независимым Astra review. Повтор полного suite не выполнялся
после изолированной диагностики. Следующий срез требует отдельного выбора;
наблюдаемая нестабильность wall-clock gates — актуальная диагностическая граница.
