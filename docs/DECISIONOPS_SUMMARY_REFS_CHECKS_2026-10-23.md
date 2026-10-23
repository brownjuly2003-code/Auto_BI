# T-95 — буквальные refs продукта, метрик и дашбордов в summary

## Контракт

Грамматика ref (`kind://segment/…@N`, сегменты `[a-z0-9][a-z0-9_-]*`) допускает
`_` и `-`. CommonMark читает `_` между `-` и буквой как открытие курсива. Поэтому
валидный `decision://sales/margin-_weekly_-view@1` в заголовке summary показывался
без подчёркиваний и с курсивом, а в строке `metrics:` курсив открывался в одном ref
и закрывался в соседнем. Владелец видел и копировал не тот ref, о котором evidence.

Теперь ref продукта в заголовке и каждый ref в строках `metrics:` и `dashboards:`
проходят существующий `_literal`, как owners. Raw refs в payload impact, пропуск
строки metrics, равной subject, и grading не меняются.
Verifier **107 → 108**, check **73** прежний; новые run IDs меняются.
Исторические bundles не переписываются.

Без `_literal` в summary остаются машинные значения и закрытые наборы: коды
изменений, гэпов и проверок, check ID, статусы и severity, run ID, digests,
timestamp (его проверяет `_check_moment`) и версия verifier.

## Найдено по ходу: полный набор на T-94 был красным

T-93 сделал все `CheckResult.detail` в summary буквальными, а 13 тестов в шести
модулях продолжали искать в summary сырой detail: `test_bi_compile_refusal` (8),
`test_product_metric_pins`, `test_product_metric_pin_contents`,
`test_product_metric_pin_rules`, `test_validation_issue_scope`,
`test_verified_asset_metric_pins`. Их не было в смежных наборах T-93 и T-94, а
полный прогон после T-86 не выполнялся. На чистом worktree `7363962` эти шесть
модулей дают **13 failed, 218 passed**.

Исправление — отдельный коммит, только тесты. Helper `shown()` в
`tests/test_decision_verify.py` снимает экранирование пунктуации, и тест проверяет
содержание detail после снятия escapes. Это не CommonMark-рендеринг: такие assertions
не заметят пропавшего экранирования. Его держат отдельные тесты `_literal`
(`tests/test_summary_check_details.py`) и CommonMark-проверки этого среза.
Production-код в этом коммите не менялся.

## Проверки

- Проба на HEAD через `markdown-it` (CommonMark): заголовок рендерился как
  `decision://sales/margin-<em>weekly</em>-view@1`, строка метрик — как
  `metric://sales/gross-<em>margin, metric://sales/net</em>-pct`.
- Red на HEAD: `tests/test_summary_refs.py` — **5 failed, 1 passed**. Сквозной тест
  падает на заголовке summary после того, как проверки payload прошли. Прошедший
  тест — страж: метрика, равная subject, по-прежнему не повторяется строкой metrics.
- Сквозной тест: тот же прогон с обычными refs и с refs `-_x_-` даёт одинаковые
  verdict BLOCK и статусы/коды проверок; payload `impact.products` хранит raw refs.
  В HTML по CommonMark заголовок и строки metrics/dashboards совпадают с refs,
  `<em>` нет.
- Unit-кейсы: заголовок (три ref), строки metrics/dashboards с двумя refs каждая;
  остальные байты отчёта не меняются.
- Два существующих теста `test_gate_summary` обновлены только под буквальную
  пунктуацию refs.
- Ruff по всему репозиторию и Black по `auto_bi`/`tests` чистые.
- Strict mypy Linux/Windows: **122 source files**, без ошибок для каждой платформы.
- Первый полный прогон (Windows, Python 3.13.7) собран до правки шести модулей:
  **24 failed, 6688 passed, 107 skipped, 38 deselected in 2356.78s**. Из них 13 —
  тесты из раздела выше, 2 — версии в доках, ещё не обновлённых, 9 — подпроцессы с
  кодом `0xC0000142` (`STATUS_DLL_INIT_FAILED`): машина в это время была на пределе
  памяти, и Claude Code снимал процессы. Перепрогон этих модулей вместе с шестью
  исправленными, `test_decision_verify`, `test_summary_refs` и `test_gate_summary`:
  **520 passed**.
- Финальный полный прогон на итоговом дереве (хэш `git diff HEAD` до и после совпал):
  **9 failed, 6703 passed, 107 skipped, 38 deselected in 1577.51s**. Тесты от T-93 и
  docs-тесты прошли. Все 9 — среда: 8 подпроцессов с `0xC0000142` и
  `test_sqlite_session_write_and_backup_budgets` (запись 11394 ms при бюджете 5000 ms);
  прогон шёл 26 мин вместо обычных ~10, Claude Code снимал фоновые оболочки из-за
  нехватки памяти. Эти три модуля отдельно: **170 passed**. Целиком зелёного полного
  прогона одним запуском на T-95 нет.
- Коммит с тестами отдельно, на коде HEAD `7363962` (worktree с семью исправленными
  тестовыми файлами): шесть модулей, `test_decision_verify`, исходный
  `test_gate_summary` и docs-тесты — **352 passed**.
- Финальное synthetic ревью Astra xhigh по ADR 0006 (read-only, профиль cw):
  **1 actionable finding, P2** — docstring `shown()` и этот документ называли снятие
  escapes «текстом, который видит читатель CommonMark». Исправлено формулировкой
  docstring и раздела выше; повторного ревью не было. Вопросы о полноте
  экранирования summary, сохранности payload и grading, версии verifier и
  устаревших assertions с raw refs — без находок. Не human/production acceptance.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m pytest -q tests/test_summary_refs.py tests/test_gate_summary.py --tb=short
.venv\Scripts\python.exe -m pytest -q --tb=short
.venv\Scripts\python.exe -m ruff check . --output-format concise
.venv\Scripts\python.exe -m black --check auto_bi tests
uv run --frozen --with mypy --with types-PyYAML mypy --platform linux --strict auto_bi
uv run --frozen --with mypy --with types-PyYAML mypy --platform win32 --strict auto_bi
```

Live DWH и Mac не запускались.
