# T-83 — Unicode в свободном тексте evidence claim

Дата: 2026-10-22. Исходный HEAD: `e04bb2e`.
Выбор и review: synthetic Astra xhigh по ADR 0006.
Реализация: Codex; требуемая `gpt-5.6-luna` недоступна.
Это модельная оценка, не human/production acceptance.

## Проблема и изменение

Одиночный суррогат в JSON-escape поля `Claim.unit` или элемента `Claim.caveats`
проходил schema validation. Полная inspection затем бросала `CanonicalizationError`;
unsigned artifact reader мог выдать payload из такого manifest. У signed manifest
с настоящим trust root отказ также возникал при построении signing bytes.

Локальный validator `Claim` теперь отвергает эти значения до canonicalization.
Полная inspection возвращает `evidence.manifest_invalid`, block, CLI exit 4;
`--artifact` — exit 2, одну строку stderr, пустой stdout. Неверный digest pin
сохраняет приоритет (exit 4). Диагностика CLI не раскрывает текст claim.
Прямой `Claim.model_validate` теперь выдаёт `ValidationError` раньше, чем writer
дойдёт до отказа canonicalization. Приоритет causal refusal сохранён.

Пустые строки, пробелы, NFD и non-BMP символы сохраняются без нормализации.
Допустимая JSON surrogate pair декодируется в один Unicode scalar и читается;
два буквальных surrogate codepoint в Python-строке отвергаются, как и прежде
при canonicalization. Канонические bytes, подписи и версии verifier/check 98/73
не меняются для допустимого ввода. Проверка выбранного artifact по-прежнему
не требует канонического написания manifest или проверки всех остальных payload.

## Проверки

- Red: **14 failed, 5 passed**, exit 1 — до изменения production-кода.
- Green: **19 passed**, exit 0.
- Evidence bundle/inspection/CLI/signing/store/refused: **232 passed, 67 skipped**,
  exit 0. Проверены unsigned и signed manifest с настоящим trust root,
  отсутствие/совпадение/несовпадение digest pin, неизменность файлов.
- Investigation brief/plan и новые регрессии: **53 passed**, exit 0.
- После добавления двух положительных roundtrip-сценариев: **21 passed**, exit 0.
  JSON surrogate pair сохраняет canonical bytes и проверку подписи; неканоническое
  написание даёт только прежний `evidence.manifest_not_canonical`.
- SHA-256 всех четырёх файлов обычного `full_bundle` совпали до и после исправления.
- Ruff и Black изменённых Python-файлов: exit 0.
- Docs-as-code/defaults: **32 passed**, exit 0 после добавления отчёта в Git index;
  первый запуск нашёл только ссылки на ещё untracked новый отчёт (1 failed, 31 passed).
- Astra xhigh просмотрела production diff и исходные 19 тестовых случаев:
  **0 открытых P1/P2**; прогоны не повторяла. Два дополнительных положительных
  сценария проверены Codex после review.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m pytest -q tests/test_evidence_claim_unicode.py tests/test_evidence_bundle.py tests/test_evidence_inspect.py tests/test_decision_cli.py tests/test_evidence_signing.py tests/test_evidence_store.py tests/test_refused_evidence.py --tb=short
.venv\Scripts\python.exe -m pytest -q tests/test_investigation_brief.py tests/test_investigation_plan.py tests/test_evidence_claim_unicode.py --tb=short
.venv\Scripts\python.exe -m pytest -q tests/test_evidence_claim_unicode.py --tb=short
.venv\Scripts\python.exe -m ruff check auto_bi/evidence/model.py tests/test_evidence_claim_unicode.py
.venv\Scripts\python.exe -m black --check auto_bi/evidence/model.py tests/test_evidence_claim_unicode.py
```

Пропуски не считаются проверенным поведением. Полный offline suite, отдельный
type-check и live DWH/Mac в этом срезе не запускались. Последний полный baseline
6523 passed относится к коду до T-82 и T-83.
