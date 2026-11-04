# T-76 — идентичность evidence в offline investigation

Дата: 2026-10-22. Реализация: Codex при недоступной `gpt-5.6-luna`.
Выбор и review: synthetic Astra xhigh по ADR 0006, не human/production acceptance.

## Изменение

На исходном `2017624` валидные `metric://sales/revenue@5` и
`metric://sales-revenue@5` в одном шаге `health` получали одинаковый evidence ID.
Та же коллизия возникала между `health-sales` / `metric://revenue@5` и
`health` / `metric://sales-revenue@5`. Компилятор brief и CLI завершались
необработанной `ValidationError: duplicate evidence artifact ids`.

Теперь ID — `result-<hex>` с полным SHA-256 canonical JSON массива
`[step_id, metric_ref]`. Claims и blocks сохраняют ссылки на свой output.
В Markdown рядом с evidence указаны шаг и полная ссылка на метрику.
ID не зависит от других outputs или порядка их поступления.

Новые `brief.json` и `brief.md` меняют bytes также для ранее допустимых входов.
Digest fixture brief теперь
`sha256:9ecf93c1728a160a88936ba1cbe4fe2e477b17d531f5e257224446a2703e4567`.
Совместимость прежнего JSON проверяется восстановлением старых ID и точного digest
`sha256:e07680ef35e98144a7ca1ffcea738fdfb3552e911ddeaa166a1914e79908f4b4`.
`plan.json`, `results.json`, skill-output digests и версии verifier 98 / check 73
не меняются. Существующие сохранённые артефакты не переписываются.

## Проверки

- Red до production-правки: **3 failed, 1 passed**, exit 1: две коллизии компилятора
  и CLI; разные версии одной метрики — успешный контроль.
- Первый профильный набор: **140 passed**, exit 0.
- Расширение на все investigation-модули: **2 failed, 293 passed** — два сравнения
  с прежним golden digest. Эталон обновлён в тесте и спецификации.
- Review выявило потерю читаемой связи Markdown с метрикой после замены ID хешем.
  Red подписей блока: **3 failed, 1 passed**, exit 1; legacy JSON уже проходил.
  Подписи исправлены, финальный investigation-набор: **296 passed in 9.73s**, exit 0.
- Финальные Ruff и Black: чистые, exit 0 (четыре изменённых Python-файла).
- Package-wide strict mypy для Linux и Windows: **no issues found in 122 source
  files**, оба exit 0 на финальном коде.
- Документация, канонизация и contract refs: **76 passed in 0.85s**, exit 0.
  Первый запуск: 1 failed, 75 passed — новый checks-документ ещё не был git-tracked;
  после добавления файла в index требование внутренних ссылок выполнено.
- Полный offline-набор с duckdb запущен на финальном коде и прерван Codex примерно
  на **16%** из-за медленного выполнения. До прерывания failures не показаны;
  процесс завершился exit 1 без итоговой pytest-сводки. Это не полный green gate.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
$investigationTests = @(rg --files tests -g 'test_investigation_*.py')
.venv\Scripts\python.exe -m pytest -q @investigationTests --tb=short
.venv\Scripts\python.exe -m ruff check auto_bi/investigation/claims.py tests/test_investigation_brief.py tests/test_investigation_cli.py tests/test_investigation_inputs.py
.venv\Scripts\python.exe -m black --check auto_bi/investigation/claims.py tests/test_investigation_brief.py tests/test_investigation_cli.py tests/test_investigation_inputs.py
uv run --frozen --no-sync --with mypy --with types-PyYAML mypy --platform linux --strict auto_bi
uv run --frozen --no-sync --with mypy --with types-PyYAML mypy --platform win32 --strict auto_bi
uv run --frozen --no-sync --with duckdb python -m pytest -q --tb=short --maxfail=5 --durations=5
.venv\Scripts\python.exe -m pytest -q tests/test_docs_as_code.py tests/test_evidence_canonical.py tests/test_contract_refs.py --tb=short
```

Проверяются обе исходные коллизии, версия метрики, уникальность evidence/claim/block ID,
ссылки на соответствующий output, одинаковые bytes при перестановке outputs и входных
файлов, стабильность ID при удалении соседнего результата. CLI реально читает YAML и
пишет четыре артефакта. Markdown проверяется отдельно для каждого блока.

## Review и границы

Финальное synthetic Astra xhigh review кода, тестов, архитектурного контракта и
обновлённого golden digest: **0 открытых P1/P2**. В первом проходе — чтение diff и
focused-проба Markdown, во втором — чтение финального diff без повторного pytest.
Status/checks-документы проверяет Codex; это не human/production acceptance.
Полный offline-набор на финальном коде остаётся непроверенным.
Живые DWH-стенды для изменения идентичности offline evidence не запускались.
