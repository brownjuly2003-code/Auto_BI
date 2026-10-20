# Platform guards: Windows-only code behind a check mypy reads — проверки автора

**Дата:** 2026-10-19
**Срез:** T-66; verifier 97 / check 72 — без изменений
**Автор:** Claude Opus 5.5 (`claude-opus-5-5`), напрямую, без MyFlow-исполнителей
**Модельное ревью:** не проводилось

После пакета T-56…T-65 владелец попросил продолжить работу; следующий ограниченный срез
по [AGENTS.md](../AGENTS.md) выбрал автор, в том же режиме, что пакет: код, тесты и доки
написал Claude Opus 5.5; MyFlow не запускался, других моделей и субагентов не было,
synthetic Astra xhigh review не было. По [ADR 0006](adr/0006-modelled-review-development-gate.md)
модельное ревью не выполнено, пока нет фактического результата Astra xhigh, — у этого среза
его нет. Всё перечисленное ниже — проверки автора над собственной работой: не независимое
ревью, не human/design-partner acceptance и не production acceptance. Тесты, линтеры и mypy
прогонялись на Mac (Python 3.13.13, 3.12.13 и 3.14.6; mypy 2.4.0); на Windows тесты не
запускались.

CI проверяет типы на Linux: джобы `quality` и `quality-py-latest` в
`.github/workflows/ci.yml` запускают `mypy auto_bi` и `mypy --strict auto_bi` на
`ubuntu-latest`. mypy проверяет одну платформу — по умолчанию ту, на которой запущен, — и
пропускает ветку, про которую может сказать, что там она не исполняется: ветку по
`sys.platform`. `os.name` он не читает. Срез T-63 создавал staging под owner и DACL
заменяемого файла через Windows-API — `ctypes.WinDLL`, `ctypes.WinError`,
`ctypes.get_last_error`, `msvcrt.open_osfhandle`, `os.O_BINARY` — в функции, которую `put`
вызывал под `existing_mode is not None and os.name == "nt"`; typeshed объявляет эти имена
только под `sys.platform == "win32"`. У репозитория нет remote, и CI на этих коммитах не
запускался. mypy, запущенный так, как его запускает CI, с `--platform` каждой платформы
(Mac, mypy 2.4.0), находит столько ошибок (без `--strict` / со `--strict`):

| Дерево | Linux | macOS | Windows |
|---|---|---|---|
| `7036f63` (T-62) | 0 / 0 | 0 / 0 | 0 / 0 |
| `f9905dd` (T-63) | 7 / 9 | 7 / 9 | 0 / 0 |
| `3a37e76` (T-64) | 7 / 9 | 7 / 9 | 0 / 0 |
| `eb40751` (T-65, HEAD) | 7 / 9 | 7 / 9 | 0 / 0 |
| рабочее дерево (T-66) | 0 / 0 | 0 / 0 | 0 / 0 |

Ошибки одни и те же на трёх коммитах, все в `auto_bi/evidence/store.py`: у модуля нет
атрибута — `ctypes.WinDLL` (дважды), `ctypes.WinError`, `ctypes.get_last_error` (дважды),
`msvcrt.open_osfhandle`, `os.O_BINARY`; со `--strict` к ним два `no-any-return`: функция
возвращает `Any`, который дают эти атрибуты.

T-63 и T-64 записали strict mypy чистым; T-64 — на Windows, где эти имена есть. T-65 записал
9 ошибок на macOS как унаследованные и оставил кандидатом. Шаги mypy, как их задают CI и
[SLO](operations/SLO.md), пакет не проходил три среза.

Теперь (`auto_bi/evidence/store.py`):

- `put` выбирает Windows-путь по `sys.platform == "win32" and existing_mode is not None`:
  проверка платформы — левый операнд `and`, единственный, который mypy читает.
- `_open_windows_staged` определена под `if sys.platform == "win32":` на уровне модуля. Тело
  функции изменилось отступом и переносом одного вызова `GetFileSecurityW`, который с
  отступом стал длиннее 100 символов: `git diff -w` показывает только его, `import sys`,
  строку `put` и проверку с комментарием над ней. На Linux и macOS функции нет — её там и
  не вызывали.
- Поведение не изменилось: в CPython `os.name == "nt"` тогда и только тогда, когда
  `sys.platform == "win32"` (у Cygwin и MSYS2 `os.name` — `posix`, а `sys.platform` —
  `cygwin` или `msys`: ложны обе проверки); `existing_mode is not None` побочных эффектов не
  имеет, и порядок операндов ответа не меняет.

Ветку, которую Linux пропускает, CI теперь проверяет отдельно: джоб `quality` получил шаг
`Mypy strict (Windows branches)` — `mypy --platform win32 --strict auto_bi`. Без него тело
`_open_windows_staged` после правки не проверял бы ни один шаг CI.

Новый модуль `tests/test_platform_guards.py` держит пакет в этой форме без mypy: он читает
исходники `auto_bi` и падает на той платформе, на которой автор гоняет тесты, а не только в
CI.

- Windows-only — то, что typeshed объявляет только под `sys.platform == "win32"`: модули
  `msvcrt`, `winreg`, `winsound`, `_winapi`, `nt` и 28 имён `ctypes`, `os`, `subprocess` и
  `signal`, — и то, что модуль пакета сам определяет только за такой проверкой.
- Windows-веткой тест считает то же, что mypy: `sys.platform == "win32"`,
  `sys.platform.startswith("win")`, их отрицание через `!=` и `not` (тогда Windows — `else`);
  у `and` и `or` — только левый операнд.
- Каждое обращение к `os.name` — атрибут или `from os import name` — находка: mypy его не
  читает, и ветка по нему проверяется на любой платформе. `getattr(os, "O_BINARY", 0)` — не
  находка: атрибут, названный строкой, mypy не проверяет.

[CONTRIBUTING](../CONTRIBUTING.md) («The gate») и [SLO](operations/SLO.md) говорят, что CI
проверяет типы на Linux, а Windows-ветки — отдельным шагом; на Windows и macOS mypy для
Linux запускается с `--platform linux`. `tests/test_docs_as_code.py` держит это рядом с CI:
шаги mypy — только на `ubuntu-latest`, с `--platform` — только шаг для Windows, а
CONTRIBUTING и SLO называют обе команды.

Проверки автора — на рабочем дереве среза, на Mac:

- mypy — таблица выше: на рабочем дереве 0 ошибок на Linux, macOS и Windows, со `--strict` и
  без. Шаг для Windows находит и обратное: в пробе `os.geteuid()` и `fcntl.LOCK_EX` вне
  проверки mypy для Windows отмечает, а `os.getuid()` под `sys.platform != "win32"` — нет;
  для Linux проба чиста.
- Списки модулей и имён теста сверены с typeshed через mypy: на каждое имя — своя строка;
  для Linux и macOS mypy отмечает все 33 строки (5 модулей — по одному их атрибуту — и
  28 имён), для Windows — ни одной.
- Red на HEAD `eb40751` с новым модулем рабочего дерева и `tests/test_evidence_store.py`:
  1 failed, 49 passed, 3 skipped. Падает проверка пакета: 8 находок в `store.py` — `os.name`
  в `put`; `import msvcrt`, `ctypes.WinDLL` дважды, `ctypes.WinError`, `ctypes.get_last_error`
  дважды и `os.O_BINARY` в Windows-функции — на тех же строках, где mypy для Linux находит
  свои 7 ошибок. Остальные 23 теста модуля — чтение фрагментов и то, что пакет прочитан, —
  проходят и на HEAD. На рабочем дереве оба модуля — 50 passed, 3 skipped (Windows-тесты
  store).
- Ручные мутанты — 16: `store.py` определяет Windows-функцию на любой платформе, ветвится по
  `os.name`, ставит проверку платформы справа от `and`; тест читает правый операнд `and`;
  читает `or` как `and`; не читает `not`; читает `!=` как `==`; не читает `startswith`; не
  находит `os.name`; не находит своё Windows-определение вне проверки; считает Windows-only
  определение, которое есть и вне проверки; не читает `else` у проверки «не Windows»; не
  переносит проверку на вложенное; не находит Windows-имя, импортированное по имени; не
  читает пакет; не считает `msvcrt` Windows-only. Убиты все 16 на первом прогоне.
- Контроль на tracked pilot-пакетах: 10 прогонов `decision verify` на HEAD и на рабочем
  дереве. Вердикт, статус, каждая проверка, `run_id` и каждый файл каждого bundle совпадают во
  всех десяти.
- Black и Ruff чистые. Концы строк: новые файлы — LF.
- Полный offline gate с duckdb на итоговом дереве, Python 3.13.13: 6211 passed, 7 skipped,
  38 deselected, exit 0. Полный набор под 3.12.13 и под 3.14.6 — те же 6211 passed,
  7 skipped, 38 deselected, exit 0.

Границы. Windows не прогонялся: Windows-ветка `store.py` на Mac не исполняется, её проверил
mypy с `--platform win32`, а тело функции изменилось только отступом и переносом. Тест —
эвристика над перечисленным: он находит `os.name` и имя из списков вне проверки, но не всё,
что typeshed объявляет только для Windows, не модуль, импортированный под другим именем
(`import ctypes as c`), не `from … import *` и не определение за проверкой глубже верхнего
уровня модуля. Гейт остаётся за mypy на Linux и для Windows. Обратное направление — POSIX-only API,
достигнутый на Windows, — тест не читает; его находит шаг mypy для Windows. Тесты CI mypy не
проверяет (`mypy auto_bi`), и тест читает только `auto_bi`. Независимого ревью у среза нет —
ни модельного, ни человеческого.

Кандидаты T-65, которые остаются: ClickHouse-профиль `readonly=1` на exact-запросе
runtime-потолка; traceback у настроек, которые не читаются, и у сбоя самой пробы;
`decision capture` — `StreamFailureError` не пойман, отказ psycopg двумя строками. Кандидат
«strict mypy на macOS: Windows-API `store.py` без `sys.platform`-guard» этот срез закрыл.
