# T-78 — буквальное отображение значений в investigation Markdown

Дата: 2026-10-22. Реализация: Codex; требуемая `gpt-5.6-luna` недоступна.
Выбор и review: synthetic Astra xhigh по ADR 0006, не human/production acceptance.

## Изменение и границы

На исходном `c333258` допустимые category/unit могли добавлять структуру отчёта.
Например, `A\n\n## Human acceptance\n\naccepted` создавал дополнительный раздел;
ссылки, backticks и HTML также интерпретировались как разметка.

Теперь при рендеринге variance/contribution экранируется ASCII-пунктуация по
CommonMark, а непечатаемые символы получают видимое escape-представление.
Обработка ограничена блоком с единственным связанным output соответствующего
типа и body, точно совпадающим с исходным шаблоном компилятора. Custom prose и
неоднозначная связь сохраняют поведение; произвольный Markdown не санитизируется.

Raw body, canonical inputs, Claim и skill evidence сохраняют исходные значения.
Causal policy действует до рендеринга: `due\n to` по-прежнему даёт exit 2 до
создания output, а допустимый `because_retail` проходит JSON round-trip.
JSON bytes/digests, версии skills и verifier 98/check 73 не меняются. Для специальных
значений меняется только `brief.md`. Прежние compiler-produced brief защищаются
при новом рендеринге, включая старые ID; сохранённые `.md` не переписываются.

## Проверки

- До изменения production-кода: **18 failed, 2 passed**, exit 1. Проверки реального
  CommonMark-дерева и CLI выявили изменение структуры отчёта.
- После переноса обработки в renderer: investigation — **327 passed in 14.42s**,
  exit 0. Затем добавлены три compatibility cases: старые ID, custom body и
  неоднозначная связь evidence; они включены в полный прогон.
- Обычный fixture сохранил SHA-256 всех четырёх payload. `brief.json`:
  `9ecf93c1728a160a88936ba1cbe4fe2e477b17d531f5e257224446a2703e4567`;
  `brief.md`: `d6a2370a029a3f8d2e7d5704ea0284a620a345334dfb918915eb04a0c509d340`.
- Шесть комбинаций category/unit с обычным значением, внедрённым заголовком и
  `because_retail`: canonical brief JSON побайтно совпал с compiler исходного HEAD.
- Ruff/Black изменённых Python-файлов и package-wide strict mypy Linux: exit 0,
  **no issues found in 122 source files**.
- Полный offline-прогон с duckdb на финальных Python-файлах: **6436 passed,
  109 skipped, 38 deselected in 1282.17s**, exit 0, включая все 25 новых cases.
  SQLite budget прошёл. `faulthandler_timeout=60` дважды вывел диагностический
  стек; соответствующие тесты `test_stated_run_facts` и `test_surrogate_escapes`
  завершились успешно за 108.16s и 81.86s. Прогон не прерывался и не повторялся.
- После обновления документации: `tests/test_docs_as_code.py` — **25 passed
  in 0.87s**, exit 0.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m pytest -q tests/test_investigation_markdown_literals.py --tb=line
$investigationTests = @(rg --files tests -g 'test_investigation_*.py')
.venv\Scripts\python.exe -m pytest -q @investigationTests --tb=short
.venv\Scripts\python.exe -m ruff check auto_bi/investigation/claims.py tests/test_investigation_markdown_literals.py
.venv\Scripts\python.exe -m black --check auto_bi/investigation/claims.py tests/test_investigation_markdown_literals.py
uv run --frozen --no-sync --with mypy --with types-PyYAML mypy --platform linux --strict auto_bi
uv run --frozen --no-sync --with duckdb python -m pytest -q --tb=short --maxfail=3 --durations=5 -o faulthandler_timeout=60
```

## Review и прежняя нестабильность

Первый synthetic review подтвердил P2 промежуточной реализации: экранирование
сохранялось в body и меняло causal-wording validation. Обработка перенесена в renderer;
второй review актуального кода и тестов: **0 открытых P1/P2**. Это не human acceptance.

Старый SQLite timing failure T-77 не воспроизведён. Один cProfile-прогон реального
теста записи/backup/восстановления: wall **0.390s**, CPU **0.109s**, exit 0;
240 commit через `Connection.__exit__` заняли 0.277s. Настройки прежние:
`journal_mode=delete`, `synchronous=2`, `busy_timeout=5000`. Причина прежних 25.66s
не установлена; порог 5s, durability, тесты и SQLite production-код не менялись.
