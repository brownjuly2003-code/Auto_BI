# T-97 — буквальный step id в заголовке GAP investigation Markdown

Дата: 2026-10-23. Исходный HEAD: `9109a12`. Реализация: Claude Opus 5.5.
Ревью: synthetic Astra xhigh по ADR 0006, не human/production acceptance.

## Дефект

Компилятор brief называет блок GAP `Gap: {step_id}`, и `brief.md` выводил этот
заголовок как есть. Грамматика step id (`^[a-z0-9][a-z0-9._-]*\Z`) допускает `_`
между `-` или `.` и буквой, а CommonMark читает такое подчёркивание как границу
выделения: `## Gap: null-_dimension_-check` показывался как
«Gap: null-<em>dimension</em>-check» — курсивом и без подчёркиваний, и заголовок
расходился со step id в строке Evidence. Ссылок, HTML и новых блоков грамматика
step id не допускает; остальные идентификаторы brief выводятся в code span.

## Изменение

`render_markdown` распознаёт заголовок GAP так же, как detail с T-79: блок ссылается
на один `InvestigationGap`, и title точно совпадает с шаблоном `Gap: {step_id}`.
Тогда step id проходит то же CommonMark-экранирование, что и detail. Title сверяется
с шаблоном отдельно от body: custom body не отключает экранирование заголовка.
Custom title и несколько evidence references выводятся как сохранены; это не общий
Markdown sanitizer.

Экранируется весь step id, поэтому байты заголовка меняются у любого step id с `_`,
`-` или `.` (`weekly_margin` → `weekly\_margin`), а видимый текст — только там, где
раньше возникало выделение. Raw title в `brief.json`, canonical JSON/digests,
`plan.json`, `results.json`, causal policy и версии verifier 108/check 73 прежние.
Сохранённый compiler brief защищён при повторном рендеринге, включая legacy ID;
записанные `.md` не переписываются.

## Проверки

- Windows, Python 3.13.7: новый модуль `tests/test_investigation_gap_titles.py` —
  **9 passed**, после правки по ревью — **11 passed**; все `tests/test_investigation_*.py` —
  **384 passed in 10.61s**, после правки — **386 passed in 12.55s**. Ruff и Black двух
  изменённых Python-файлов чистые.
- iMac, дерево `69c7118` (код и тест до правки по ревью, без документов).
  Новый модуль на `claims.py` из `9109a12` — **5 failed, 4 passed**: оба step id с выделением,
  legacy ID, custom body и CLI. Проходят тесты, закрепляющие прежнее поведение: step id
  без пунктуации, custom title, несколько evidence references и causal-отказ step id
  `because` до создания output. После возврата файла `git status` пуст.
- Пять планов, по четыре payload через `write_offline_investigation` с `claims.py` из
  `9109a12` и с новым, сравнены отдельной Python-пробой: обычный fixture и GAP со step
  id `contribution`/`concentration`, `null-_dimension_-check`/`share-_top_`,
  `a._b_.c`/`concentration`, `weekly_margin`/`x-y.z`. `brief.json`, `plan.json` и
  `results.json` совпали побайтно во всех пяти; `brief.md` совпал у fixture и step id
  без пунктуации, у остальных трёх отличаются только пять строк `## Gap:`. Четыре
  payload fixture сохранили SHA-256 T-79:
  `plan.json`: `cfbedd5368d2edeba94f2a17c19a4eebc4079b5bd048c46c93825ff41b696fcb`;
  `results.json`: `447af2146aedb6e7348d92d0bb28631d881825437a51d16777d0741cf102fa40`;
  `brief.json`: `9ecf93c1728a160a88936ba1cbe4fe2e477b17d531f5e257224446a2703e4567`;
  `brief.md`: `d6a2370a029a3f8d2e7d5704ea0284a620a345334dfb918915eb04a0c509d340`.
- Все `tests/test_investigation_*.py` на Mac, то же дерево, под Python 3.13.13, 3.12.13
  и 3.14.6 — каждый раз **382 passed, 2 skipped**; пропущены суррогатные кейсы имён на
  APFS (T-96).
- Полный прогон на Mac с DuckDB, то же дерево `69c7118`: 3.13 — **6809 passed,
  19 skipped, 38 deselected in 256.31s**, exit 0; 3.12 — **6809 passed, 19 skipped**
  in 277.97s, exit 0; 3.14 — **6809 passed, 19 skipped** in 250.62s, exit 0. Black
  (`auto_bi`, `tests`), Ruff по всему репозиторию, mypy `--platform linux`, strict Linux
  и strict win32 (122 source files) — exit 0. Против T-96 прибавились девять тестов
  нового модуля: 6800 + 9 = 6809.
- Правка по ревью (ниже) заменяет тест байтов заголовка без пунктуации
  параметризованным тестом точных строк заголовка: `contribution`, `weekly_margin`,
  `x-y.z`. Windows: модуль на `claims.py` из `9109a12` — **7 failed, 4 passed**
  (добавились `weekly_margin` и `x-y.z`); мутант, экранирующий в step id только `_`, —
  **1 failed** (`x-y.z`), **10 passed**, а модуль до правки на том же мутанте —
  **9 passed**. После возврата файлов они совпали с исходными байтами. Mac, дерево
  `dc40fd6` (итоговые код и тесты): модуль под 3.13, 3.12 и 3.14 — каждый раз
  **11 passed**; полный gate 3.13 — **6811 passed, 19 skipped, 38 deselected in
  254.56s**, exit 0, Black, Ruff и mypy (Linux, strict Linux, strict win32) — exit 0.
  Против прогона до правки: 6809 − 1 + 3 = 6811.
- Итоговые код и тесты совпадают с `dc40fd6`, после него менялись только документы;
  docs-тесты на Windows на итоговом дереве — **25 passed**. Полный прогон на Windows для
  T-97 не повторялся: изменены renderer и один тестовый модуль, и все investigation-модули
  на Windows прошли. Последний полный прогон Windows — T-95.
- Финальное synthetic ревью Astra xhigh по ADR 0006 (read-only, профиль cw):
  **1 actionable finding, P2** — тесты проверяли видимый текст заголовка, а точные байты
  только у step id без пунктуации, и экранирование одного `_` прошло бы их вопреки
  заявленному контракту. Исправлено тестом выше; повторного ревью не было. Вопросы о
  грамматике step id и CommonMark, совместимости, JSON и causal policy, документах и
  других вставках `brief.md` — без находок. Ревьюер отметил: авторский title, случайно
  совпавший с шаблоном, тоже экранируется — граница совпадения по значению, как в T-79.
  Не human/production acceptance.

```bash
# Mac: /tmp/autobi_t47/run.sh t97 /tmp/autobi_t47/scratch/t97_mac.sh,
# после правки по ревью — run.sh t97b …/t97b_mac.sh (модуль и тот же gate)
git checkout HEAD~1 -- auto_bi/investigation/claims.py  # red; затем git checkout HEAD -- …
uv run --no-sync python -m pytest -q -p no:randomly tests/test_investigation_gap_titles.py
uv run --no-sync python -m pytest -q -p no:randomly -rs tests/test_investigation_*.py
uv run --no-sync --with duckdb python -m pytest -q -p no:randomly
uv run --no-sync black --check auto_bi tests
uv run --no-sync ruff check . --output-format concise
uv run --no-sync --with mypy --with types-PyYAML mypy --platform linux --strict auto_bi
uv run --no-sync --with mypy --with types-PyYAML mypy --platform win32 --strict auto_bi
```

Live DWH и Linux не запускались.
