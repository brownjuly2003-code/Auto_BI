# T-77 — управляемый отказ causal-wording policy

Дата: 2026-10-22. Реализация: Codex при недоступной `gpt-5.6-luna`.
Выбор и review: synthetic Astra xhigh по ADR 0006, не human/production acceptance.

## Изменение

На исходном `276c842` категория `Cause` проходит входную схему и pure skills,
но подстановка в brief нарушает существующий запрет причинных формулировок.
Необработанная `ValidationError` доходила до CLI. Аналогичный отказ возникает
для unit, содержащего `because`.

Теперь compiler переводит только эту policy-ошибку в `BriefCompilationError`.
Существующая application-граница возвращает `OfflineInvestigationError`, CLI —
exit 2 и ровно одну строку:

```text
error: causal wording is forbidden in the offline investigation alpha
```

Входные значения не попадают в диагностику; output и отсутствующий parent не
создаются. Остальные ошибки валидации и неожиданные исключения не подавляются.
Проверка формулировок и допустимость исходных labels не меняются: `Cause` по-прежнему
вызывает отказ. Успешные артефакты и версии verifier 98 / check 73 сохраняются.

## Проверки

- До правки production-кода: **6 failed, 2 passed**, exit 1. Отказы compiler,
  application и CLI для category/unit; контроль других исключений уже проходил.
- После правки весь investigation-набор: **304 passed in 22.31s**, exit 0.
- После дополнительного контрольного теста: investigation + документация,
  канонизация и contract refs — **381 passed in 94.78s**, exit 0
  (305 investigation и 76 смежных тестов).
- Ruff и Black для двух изменённых Python-файлов: чистые, exit 0.
- Package-wide strict mypy для Linux: **no issues found in 122 source files**, exit 0.
- Все четыре fixture payload после правки совпали с SHA-256, снятыми до изменения
  production-кода. Digest `brief.json`:
  `9ecf93c1728a160a88936ba1cbe4fe2e477b17d531f5e257224446a2703e4567`;
  `brief.md`: `d6a2370a029a3f8d2e7d5704ea0284a620a345334dfb918915eb04a0c509d340`.
- Полный offline baseline с duckdb, запущенный до изменения production-кода:
  **1 failed, 6401 passed, 109 skipped, 38 deselected in 1387.67s**, exit 1.
  Единственный отказ — `test_sqlite_session_write_and_backup_budgets`: запись
  **25656.9 ms** при лимите **5000 ms**. Этот тест не затрагивает investigation.
  Отдельная диагностическая проверка того же теста: **1 passed in 1.30s**, exit 0;
  вся call-фаза заняла **0.84s**. Код SQLite и лимиты не менялись. Повтор полного
  набора не выполнялся; причина нестабильности времени окончательно не установлена.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m pytest -q tests/test_investigation_causal_policy.py --tb=line
$investigationTests = @(rg --files tests -g 'test_investigation_*.py')
.venv\Scripts\python.exe -m pytest -q @investigationTests --tb=short
.venv\Scripts\python.exe -m pytest -q @investigationTests tests/test_docs_as_code.py tests/test_evidence_canonical.py tests/test_contract_refs.py --tb=short
.venv\Scripts\python.exe -m ruff check auto_bi/investigation/claims.py tests/test_investigation_causal_policy.py
.venv\Scripts\python.exe -m black --check auto_bi/investigation/claims.py tests/test_investigation_causal_policy.py
uv run --frozen --no-sync --with mypy --with types-PyYAML mypy --platform linux --strict auto_bi
uv run --frozen --no-sync --with duckdb python -m pytest -q --tb=short --maxfail=3 --durations=5 -o faulthandler_timeout=60
.venv\Scripts\python.exe -m pytest -q tests/test_perf_baseline.py::test_sqlite_session_write_and_backup_budgets --tb=short --durations=1
```

## Review и границы

Synthetic Astra xhigh review production-кода и тестов: **0 открытых P1/P2**.
По неблокирующей рекомендации добавлен отдельный контроль единственной обычной
ошибки валидации, наряду с несколькими ошибками и `RuntimeError`: исходные объекты
исключений должны сохраняться. Документацию и проверки завершает Codex.
Это не human/production acceptance.

Полный baseline не является green gate и был начат до правки; полный набор на
финальном коде не заявляется пройденным. Финальный код проверен профильным и смежным
набором, типизацией, линтерами и сравнением успешных payload. Перед baseline тест
CTE-линейности у прежней отметки прерывания 16% отдельно прошёл за 2.54s.

Живые DWH-стенды и внешние действия этому offline-срезу не требуются.
