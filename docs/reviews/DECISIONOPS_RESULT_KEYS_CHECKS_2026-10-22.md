# T-84 — ключи поставленных результатов

Дата: 2026-10-22. Исходный HEAD: `8e35e56`.
Выбор и уточнение дизайна: synthetic `gpt-6-astra` xhigh по ADR 0006.
Реализация: Codex; требуемая `gpt-5.6-luna` недоступна среди исполнителей.
Это модельная оценка, не human/production acceptance.

## Проблема и изменение

Результат под неизвестным ключом молча игнорировался: рядом с корректным ответом
CLI возвращал exit 0 с evidence; вместо корректного ответа — exit 3 с
`provenance.no_results`. Это было явно описано как граница, а не запрещено прежним
контрактом. Synthetic design review выбрало ранний отказ вместо молчаливой потери.

После прежней проверки схем всех значений ключи сверяются с точными `metadata.id`
полного переданного asset repository, прочитанного один раз. Первый неизвестный
ключ даёт `VerifyError`, CLI exit 2, пустой stdout и одну строку stderr до probe
и evidence. Непечатаемые символы пути и ключа экранируются.

Известные assets других продуктов допустимы в общем файле результатов, но не
становятся claims текущего продукта. Пустая map, отсутствие файла результатов
и недостающие ответы сохраняют прежний grading. Без `--assets` непустая map
отвергается. Bare/attested формы и приоритет malformed value сохранены.
URI не разбирается, пробелы и версии не отбрасываются.

Verifier/check 98/73 прежние: это preflight filesystem use case, не новая finding.
Прямые `run_gate`/`RecordedResults` callers вне этой границы.
[Контракт](../specs/recorded-result-keys.md).

## Проверки

- Red до реализации: **12 failed, 7 passed**, exit 1. Неизвестный ключ достигал
  probe либо завершённого CLI-прогона с evidence.
- Первый green: **19 passed**, exit 0; смежные шесть модулей: **416 passed, 1 skipped**.
- Дополнительные проверки точного ключа (пробел/version suffix): **23 passed**.
- Первое Astra review: один P2 — Unicode-разделитель строки в имени файла ломал
  однострочную диагностику. Red на реально читаемом Windows-пути: **2 failed,
  2 passed**. Исправлена только новая ветка через `filesystem.display_path`.
- SHA-256 всех **15 файлов** корректного контрольного bundle совпали до и после
  membership-правки; run id также совпал.
- Финальный профиль после исправления P2: **422 passed, 1 skipped**, exit 0;
  включает все 25 сценариев нового модуля.
- Ruff/Black изменённых Python-файлов и strict mypy Linux/Windows по 122 модулям:
  exit 0. Второе ограниченное Astra review: **0 открытых P1/P2**;
  reviewer независимо проверил Unicode-путь на Windows.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m pytest -q tests/test_recorded_result_keys.py tests/test_decision_verify.py tests/test_decision_cli.py tests/test_unreadable_run_inputs.py tests/test_run_input_schema_refusals.py tests/test_result_capture.py --tb=short
.venv\Scripts\python.exe -m ruff check auto_bi/decision/use_cases.py tests/test_recorded_result_keys.py
.venv\Scripts\python.exe -m black --check auto_bi/decision/use_cases.py tests/test_recorded_result_keys.py
uv run --frozen --with mypy --with types-PyYAML mypy --platform linux --strict auto_bi
uv run --frozen --with mypy --with types-PyYAML mypy --platform win32 --strict auto_bi
```

Полный offline baseline 6551 passed относится к T-83, до этого изменения.
Live DWH/Mac не проверялись; пропуски не считаются проверенным поведением.
