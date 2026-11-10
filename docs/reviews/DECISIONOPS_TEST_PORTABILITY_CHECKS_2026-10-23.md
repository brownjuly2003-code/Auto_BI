# T-96 — тесты T-74, T-75 и T-82 на macOS

Дата: 2026-10-23. Исходный HEAD: `84537ce`. Реализация: Claude Opus 5.5.
Ревью: synthetic Astra xhigh по ADR 0006, не human/production acceptance.

## Дефект

Полный прогон T-95 на Mac под Python 3.12, 3.13 и 3.14 давал **6 failed**; в своих
модулях те же шесть падали и на `7363962`, до T-95. Тесты T-74, T-75 и T-82
запускались только на Windows и опирались на его поведение.

- Тесты T-82 строили manifest с вложенностью 3000, а JSON-декодер CPython отказывает
  на ней только на Windows. Замер глубочайшей вложенности, которую `json.loads` ещё
  разбирает: Windows 3.13.7 — 2998; macOS 3.12.13 — 9997, 3.13.13 — 9998, 3.14.6 —
  37286 (3.14 считает стек, а не уровни). На macOS такой manifest разбирался, и его
  отвергала валидация `EvidenceBundle`. Поэтому
  `test_artifact_read_refuses_manifest_beyond_json_parser_depth` (четыре варианта)
  падал на проверке причины. CLI-тест
  `test_evidence_artifact_reports_manifest_beyond_json_parser_depth` проходил, не дойдя
  до ветки `RecursionError`, ради которой написан, а
  `test_artifact_digest_pin_precedes_manifest_depth_refusal` проходил, не проверяя, что
  отказ digest pin опережает отказ парсера. По исходникам CPython 3.12/3.13 лимит вне
  Windows тот же, что на macOS, так что Linux CI в том же положении; запуском на Linux
  не проверено.
- `test_encodable_output_paths_keep_artifact_bytes` (имя output с одиночным суррогатом
  U+DCFF) и `test_readable_unicode_input_names_keep_the_artifact_bytes` (кейс
  `surrogateescape`). На POSIX такой суррогат доходит до файловой системы байтом вне
  UTF-8. ext4 хранит такое имя, APFS отвергает его (`Errno 92 Illegal byte sequence`),
  и файла с таким именем на macOS не бывает. NTFS хранит сам суррогат.

Продуктовый код не менялся, и на macOS его ответы управляемые: reader отвечает тем же
`EvidenceReadError` «no readable manifest.json», а запись в отвергнутое имя
кончается отказом записи output.

## Изменение

- Три теста T-82 — reader, CLI и приоритет digest pin — строят manifest общим
  помощником `manifest_past_json_parser_depth`: вложенность 100 000 и предусловие —
  `json.loads` этого интерпретатора на нём даёт `RecursionError`. Если будущий
  интерпретатор разберёт такую глубину, тест упадёт на предусловии, а не проверит
  другую ветку.
- Суррогатные кейсы сначала создают и удаляют каталог с тем же именем
  (`skip_where_the_filesystem_refuses`). Кейс пропускается с причиной только при
  `EILSEQ`; любая другая ошибка пробрасывается. На NTFS и ext4 кейсы выполняются
  как раньше.

Версии verifier 108/check 73, evidence и артефакты не меняются.

## Проверки

- Windows, Python 3.13.7: пять затронутых модулей — **96 passed**, без пропусков; после
  правки теста pin `test_evidence_inspect` и `test_decision_cli` — **59 passed**.
  Ruff и Black пяти файлов чистые.
- iMac, дерево `0196d42` (пять тестовых файлов, ещё без правки теста pin),
  Python 3.13.13, 3.12.13 и 3.14.6: пять затронутых модулей — каждый раз **94 passed,
  2 skipped**. Оба пропуска — суррогатные кейсы с причиной «the filesystem refuses
  this name: Illegal byte sequence».
- Мутант reader без перехвата `RecursionError` (`except ValueError` вместо
  `except (ValueError, RecursionError)` в `auto_bi/evidence/inspect.py`), Python 3.13:
  новые depth-тесты — **6 failed**, четыре библиотечных и оба CLI. Тесты `84537ce`
  на том же мутанте — **4 failed, 2 passed**: библиотечные падали и без мутанта, а оба
  CLI-теста мутанта не замечали. После возврата файла `git status` пуст.
- Полный прогон на Mac с DuckDB, то же дерево `0196d42`: 3.13 — **6800 passed,
  19 skipped, 38 deselected in 265.74s**, exit 0; 3.12 — **6800 passed, 19 skipped**
  in 280.63s, exit 0; 3.14 — **6800 passed, 19 skipped** in 252.08s, exit 0. Black
  (`auto_bi`, `tests`), Ruff по всему репозиторию, mypy `--platform linux`, strict Linux
  и strict win32 (122 source files) — exit 0. Против T-95 четыре depth-теста прошли, а
  два суррогатных кейса пропущены: 6796 + 6 + 17 = 6800 + 19.
- После правки теста pin, дерево `90232d6`: пять модулей под 3.13, 3.12 и 3.14 —
  каждый раз **94 passed, 2 skipped**. Мутант, который разбирает manifest до проверки
  pin (валидация модели остаётся после неё): новый тест pin — **1 failed**
  (`EvidenceReadError` вместо `EvidenceDigestError`), тест `84537ce` — **1 passed**,
  мутанта не замечал. После возврата файлов `git status` пуст.
- Итоговое дерево отличается от `0196d42` строкой теста pin и документами. Модули с
  этой строкой прогнаны на `90232d6` под тремя интерпретаторами, docs-тесты — на
  Windows на итоговом дереве (**25 passed**). Полного прогона на итоговом дереве нет.
- Полный прогон на Windows для T-96 не повторялся: изменены только тесты пяти модулей,
  и на Windows они прошли. Последний полный прогон Windows — T-95.
- Финальное synthetic ревью Astra xhigh по ADR 0006 (read-only, профиль cw):
  **1 actionable finding, P2** — тест приоритета digest pin по-прежнему брал
  вложенность 3000. Исправлено тем же помощником, мутант выше это подтверждает;
  повторного ревью не было. Вопросы о том, закрепляют ли тесты контракты
  T-74/T-75/T-82, об узости skip и о точности документов — без находок. Безопасность
  глубины 100 000 на всех сборках и настройках стека чтением кода не доказана; её
  подтверждают только записанные прогоны Windows и macOS. Не human/production
  acceptance.

```bash
# Mac: /tmp/autobi_t47/run.sh t96 /tmp/autobi_t47/scratch/t96_mac.sh
uv run --no-sync python -m pytest -q -p no:randomly -rs tests/test_evidence_inspect.py \
  tests/test_decision_cli.py tests/test_investigation_cli.py \
  tests/test_investigation_output_encoding.py tests/test_investigation_path_encoding.py
uv run --no-sync --with duckdb python -m pytest -q -p no:randomly
uv run --no-sync black --check auto_bi tests
uv run --no-sync ruff check . --output-format concise
uv run --no-sync --with mypy --with types-PyYAML mypy --platform linux --strict auto_bi
uv run --no-sync --with mypy --with types-PyYAML mypy --platform win32 --strict auto_bi
```

Live DWH и Linux не запускались.
