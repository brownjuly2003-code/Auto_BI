# T-87 — буквальное отображение среды в owner-summary

## Контракт

`environment_ref` законно содержит переводы строк и другие Unicode-символы
([stated-run-facts](../specs/stated-run-facts.md)). Его вставка как сырого Markdown
позволяла добавить ложные строки Verdict и Owner action даже при реальном BLOCK.
Тот же вход попадал в detail `results.provenance` при неизвестной или иной среде
attestation; сама среда attestation давала аналогичный эффект.

Заголовок Environment и весь detail `results.provenance` теперь отображаются
через существующий `_literal`: пунктуация экранируется, управляющие символы видимы.
Правило действует в severity/undone/table; таблица не экранирует pipe дважды.
Обрабатывается весь provenance detail, без разбора шаблонов. Другие свободные поля
summary этим срезом не охватываются.

Исходное имя среды, findings, claims, JSON payload и grading не меняются.
Verifier **99 → 100**, check **73** прежний. Новые run IDs меняются; исторические
bundles не переписываются. В Markdown меняется также обычная пунктуация этих полей.
Пустая среда по-прежнему не добавляет заголовок; простой текст и Unicode сохраняются.

## Проверки

- Первый запуск нового теста остановился на неверном импорте `CheckStatus`;
  после исправления импорта red заголовка: **5 failed, 2 passed**, exit 1.
- Дополнительный red трёх provenance-сценариев: **3 failed**, exit 1.
  В отчёте появлялись три или четыре Verdict вместо одного.
- Смежный набор: **342 passed in 52.32s**, exit 0, включая десять новых сценариев.
  Gate-тесты проверяют фактический verdict, коды findings и исходный JSON detail;
  Markdown-тесты — один verdict/owner action и однократное экранирование pipe.
- Strict mypy Linux/Windows: **122 source files**, exit 0 для обеих платформ.
- Финальные Ruff/Black трёх Python-файлов и diff check чистые.
- После форматирования и обновления документации: **35 passed in 1.39s**, exit 0
  (`test_docs_as_code.py` и `test_summary_environment.py`).
- Выбор, уточнение scope и финальное ревью — synthetic Astra xhigh:
  **0 actionable findings**, не human/production acceptance.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m pytest -q tests/test_summary_environment.py tests/test_gate_summary.py tests/test_summary_other_products.py tests/test_stated_run_facts.py tests/test_run_identity.py tests/test_decision_verify.py --tb=short
.venv\Scripts\python.exe -m ruff check auto_bi/verification/summary.py auto_bi/verification/release_gate.py tests/test_summary_environment.py
.venv\Scripts\python.exe -m black --check auto_bi/verification/summary.py auto_bi/verification/release_gate.py tests/test_summary_environment.py
uv run --frozen --with mypy --with types-PyYAML mypy --platform linux --strict auto_bi
uv run --frozen --with mypy --with types-PyYAML mypy --platform win32 --strict auto_bi
```

Полный offline-набор на T-87 и live DWH/Mac не запускались. Полный baseline T-86
не считается проверкой нового кода.
