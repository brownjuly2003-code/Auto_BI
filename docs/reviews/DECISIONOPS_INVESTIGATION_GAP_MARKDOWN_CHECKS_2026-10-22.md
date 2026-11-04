# T-79 — буквальный detail GAP в investigation Markdown

Дата: 2026-10-22. Исходный HEAD: `6899c15`.
Реализация: Codex; требуемая `gpt-5.6-luna` недоступна.
Выбор и review: synthetic Astra xhigh по ADR 0006, не human/production acceptance.

## Дефект и изменение

Допустимое имя dimension попадало в `InvestigationGap.detail` через
`dimension {dimension!r} contains NULL`. При имени со ссылкой или HTML
`input.null_dimension` обоих grouping skills публиковался с интерпретируемой
разметкой. `repr` сохраняет пунктуацию Markdown и не защищает этот вывод.

Renderer распознаёт GAP по одному связанному output и точному совпадению body
с исходным шаблоном. Весь detail проходит существующее CommonMark-экранирование;
непечатаемые символы получают видимые escape-последовательности. Код GAP сохраняет
code span. Это меняет `brief.md` также у обычных GAP с пунктуацией в detail.

Raw body/detail, skill evidence, canonical JSON/digests, causal policy и версии
verifier 98/check 73 прежние. Custom prose и несколько evidence references
сохраняют отображение; это не общий Markdown sanitizer. Сохранённый compiler brief
защищён при повторном рендеринге, включая legacy ID; старые `.md` не переписываются.

## Проверки

- До правки production-кода: **8 failed, 10 passed, 25 deselected**, exit 1.
  Реальные `input.null_dimension` для contribution/concentration меняли
  CommonMark-структуру; CLI публиковал HTML вместо буквального имени.
- После правки: Markdown-модуль — **43 passed in 1.21s**, exit 0; весь набор
  investigation — **348 passed in 36.22s**, exit 0.
- Новые 18 cases покрывают ссылки, HTML/комментарии, emphasis/backticks,
  escape-последовательности, JSON round-trip, legacy ID, custom body,
  неоднозначную связь, четыре CLI-файла и прежние causal-policy отказы.
- Четыре payload обычного fixture сохранили SHA-256 исходного HEAD:
  `plan.json`: `cfbedd5368d2edeba94f2a17c19a4eebc4079b5bd048c46c93825ff41b696fcb`;
  `results.json`: `447af2146aedb6e7348d92d0bb28631d881825437a51d16777d0741cf102fa40`;
  `brief.json`: `9ecf93c1728a160a88936ba1cbe4fe2e477b17d531f5e257224446a2703e4567`;
  `brief.md`: `d6a2370a029a3f8d2e7d5704ea0284a620a345334dfb918915eb04a0c509d340`.
- Пять GAP-входов с обычным именем, ссылкой, HTML, `because_retail` и переводами
  строк: brief JSON и digest побайтно совпали с компилятором исходного HEAD;
  Markdown изменился. Сравнение выполнено отдельной native Python-пробой, exit 0.
- Ruff/Black изменённых Python-файлов: exit 0. Package-wide strict mypy Linux:
  **no issues found in 122 source files**, exit 0.
- Docs-as-code: **25 passed in 0.77s**, exit 0. Первый прогон отверг ссылки
  на новый untracked отчёт; после добавления этого файла в Git index проверка прошла.
- Synthetic Astra xhigh, один проход review: **0 открытых P1/P2**. Независимая
  compiler-проба reviewer с переводами строк и HTML-комментарием в detail также
  сохранила JSON и убрала разметку. Это не human/production acceptance.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m pytest -q tests/test_investigation_markdown_literals.py --tb=line
$investigationTests = @(rg --files tests -g 'test_investigation_*.py')
.venv\Scripts\python.exe -m pytest -q @investigationTests --tb=short
.venv\Scripts\python.exe -m ruff check auto_bi/investigation/claims.py tests/test_investigation_markdown_literals.py
.venv\Scripts\python.exe -m black --check auto_bi/investigation/claims.py tests/test_investigation_markdown_literals.py
uv run --frozen --no-sync --with mypy --with types-PyYAML mypy --platform linux --strict auto_bi
.venv\Scripts\python.exe -m pytest -q tests/test_docs_as_code.py --tb=short
```

Полный offline-набор на T-79 не повторялся: изменение ограничено renderer,
профильные проверки прошли. Последний полный green — T-78: 6436 passed,
109 skipped, 38 deselected; этот результат не заявляется проверкой T-79.
