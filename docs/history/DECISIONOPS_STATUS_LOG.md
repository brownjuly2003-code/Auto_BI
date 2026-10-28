# DecisionOps — журнал статуса срезов

> **История, не действующие правила.** До 2026-10-05 этот журнал был блоком «Статус» в
> [CLAUDE.md](../../CLAUDE.md): он грузился в контекст каждой сессии целиком (105 тыс. знаков
> из 112 тыс.) и рос с каждым срезом. Записи перенесены дословно; изменены только
> относительные ссылки. Читать, когда нужна история решения. Состояние и открытые решения —
> [DECISIONOPS_CURRENT_STATE.md](../DECISIONOPS_CURRENT_STATE.md); правила —
> [CLAUDE.md](../../CLAUDE.md) и [AGENTS.md](../../AGENTS.md).
>
> Новая запись среза добавляется сверху, полным текстом. В `CLAUDE.md` остаются короткие
> записи трёх последних срезов.

## 2026-10-28 — T-121: validation report один на любой ОС

Срез взят Claude по кандидату handoff на «продолжи доработку» владельца (не synthetic
Astra-выбор): `source` issue validation report — `str(path)` с разделителем ОС, и тот же каталог
на Windows и на POSIX — два run id. `validate_paths` (`auto_bi/decision/validate.py`), общий для
`decision validate` и `decision verify`, называл файл в issue — не загрузился, не прочитался, id
определён в двух файлах, путь не найден, каталог не перечислить — и файл продукта, отвергнутого
схемой, `str(path)`: `contracts\sub\a.yaml` на Windows, `contracts/sub/a.yaml` на POSIX;
содержимое validated-каталога шло в порядке `sorted` путей, а `Path` на Windows сравнивает без
регистра. Validation report входит в idempotency key целиком (`_static_inputs`), поэтому одни и
те же файлы на двух ОС давали два report и два run id, а из двух файлов одного id прогон получал
контракт, прочитанный первым, — на Windows другой. Воспроизведено на HEAD `9ef1811`
(`.tmp/t121/repro.py`, `runid.py`): `contracts/sub/a.yaml` и `B.yaml` — issues в обратном порядке
и с `\`; один id в `contracts/m/B.yaml` (team-b) и `contracts/m/a.yaml` (team-a) — Windows держал
`a.yaml`, macOS — `B.yaml`; `decision verify` над одними байтами (LF) — `run-42d88e9a70ec9b55`
на Windows, `run-32f45c3e4019b6ef` на macOS. Теперь файл называется `Path.as_posix()`
(`_spelled`) в `source` issue, в `subject` у `contract.unreadable` и в `source` отвергнутого
продукта (`subject` у `contract.invalid` прежний: объявленный id или имя файла), а файлы каталога
и подкаталоги, которые не удалось перечислить, идут в порядке `Path.parts` (`_path_order`) — имя
за именем, по code point с регистром; пути, данные команде, — в своём порядке, как прежде. На
POSIX report прежний. С фиксом те же байты дают `run-e5aebfe5ee3db71f` и на Windows, и на macOS.
Verifier 118; check 79 и native source 3 прежние: ни одна проверка, gap или digest не значит
нового. Отвергнуто: `str(path).replace("\\", "/")` — на POSIX обратная косая черта — часть имени
(мутант M16 на Windows неотличим, на Mac пойман тестом с `contracts/a\b.yaml`); порядок без
регистра на всех ОС — на ФС, хранящей регистр, `M.yaml` и `m.yaml` — два файла, и их порядок
снова зависел бы от листинга. Тесты 10 модулей, ожидавшие `str(path)`, переписаны на
`as_posix()`. Границы: путь в report есть только в issue, которое называет файл, — где оно есть,
разные абсолютные пути и два пути к одному каталогу, которые `pathlib` пишет по-разному,
по-прежнему дают два report и два run id; файлы с CRLF и с LF — разные байты и два snapshot; без
нормализации Unicode; на ФС, складывающей регистр, `M.yaml` и `m.yaml` — один файл; класс отказа
(`PermissionError`) — как называет ОС; путь вне validated-файлов — как пишет ОС; повтор
`contract.unreadable` на каждый путь к файлу и hard link в `metrics/` — по-прежнему; порядок
snapshot не менялся. Spec — [validation report paths](../specs/validation-report-paths.md);
границы [contract read order](../specs/contract-read-order.md),
[contract files once](../specs/contract-files-once.md),
[one-line errors](../specs/one-line-errors.md),
[unreadable inputs](../specs/unreadable-inputs.md), [run identity](../specs/run-identity.md);
§7.4 и §11.1 архитектуры, состояние.

Прогоны: мутанты (`.tmp/t121/mutants.py`, `mutants2_win.log`) — 19 из 20 пойманы на Windows
(M18 и M19 — имя в NFC или NFD перед сравнением — добавлены по ревью); M16 (`\` → `/` в тексте
пути) на Windows неотличим и пойман на Mac. Mac (APFS), итоговое дерево: модули среза и соседние
(15 модулей с `test_docs_as_code`) — 722 passed, 3 skipped на Python 3.12, 3.13 и 3.14.6
(`t121d.log` на Mac; до правок ревью — 720, `t121b.log`); тесты регистра этого среза и T-120 на
APFS пропускаются, на одноразовом case-sensitive образе APFS — 40 passed, 0 skipped (`t121c.log`;
до правок — 38). Первый полный Windows gate (до ревью): black, Ruff, `ruff format --check`
изменённых файлов, mypy ×4 чистые; pytest — 1 failed, 7135 passed, 96 skipped, покрытие 96.23%,
за 71 мин на загруженной машине: таймаут 30 с подпроцесса CLI
`test_decision_settings.py::…[clickhouse-verify]`; модуль отдельно — 35 passed, самый долгий
вызов 1.68 с (`.tmp/t121/pytest_v1.log`). Полный Windows gate после ревью: black, Ruff,
`ruff format --check` изменённых файлов, mypy ×4 чистые (`.tmp/t121/checks.log`); pytest — 7138
passed, 96 skipped, покрытие 96.24%, exit 0, за 67 мин (`.tmp/t121/pytest.log`).

Ревью synthetic Astra xhigh (`.tmp/t121/astra_review.out`; не human acceptance): P1 — spec
обещала два report для разных абсолютных путей всегда, хотя путь в report есть только в issue
файла, и требовала `/` в `subject` всех issue, хотя `subject` у `contract.invalid` — id или имя
файла: переписано в spec, состоянии и журнале. P2 — сценарий «id в двух файлах» сливал две
раскладки в одну — разделён. P3 — порядок Unicode не закреплён — тест `e` + U+0301 и `é` против
`f.yaml`, мутанты M18 и M19. Повторное ревью по diff исправлений (`astra_review2.out`): P1–P3
закрыты, новых P1 и P2 нет; P3 — числа Mac были с дерева до правок — заменены итоговыми.
Третьего ревью не было.

## 2026-10-27 — T-120: snapshot берёт контракты в порядке того, что они держат

Срез взят Claude по кандидату handoff на «продолжи доработку» владельца (не synthetic
Astra-выбор): порядок gaps контрактов и узлов по раскладке файлов и сортировке ОС.
`NativeModelSource.snapshot` (`auto_bi/semantic/gateway.py`) брал контракты в порядке
`_contract_files()` — `sorted` путей: каталог раньше имени, на Windows без регистра. Source
digest хранит digest контракта под именем файла, без каталога (T-111), но `gap.contract_invalid`,
гэпы `_metric_node` (`gap.opaque_metric_formula` — REVIEW, бывает в проходящих прогонах) и узлы
id, который определяют два контракта, стояли в порядке листинга. Snapshot запечатывается целиком
и входит в idempotency key, поэтому перенос файла в подкаталог `metrics/` или тот же ref на
другой ОС давали другой snapshot и run id: при прежних source и canonical digest, если
переставлялись только гэпы, и с другим canonical digest, если узлы одного id. Воспроизведено
на HEAD `17f59f8` (`.tmp/t120/repro.py`): `x.yaml`, `y.yaml` и `z/x.yaml`, `y.yaml` с двумя
контрактами reviewed SQL — gaps в обратном порядке, два run id у `decision verify`.
Теперь все файлы читаются (source digest прежний), затем разбираются в порядке (имя файла по code
point, без нормализации Unicode, затем digest байтов), а загруженные контракты идут в
`_build_graph` в порядке (metric id, версия числом, digest байтов). Порядок групп прежний: файлы,
модель, контракты, `gap.metric_duplicate` по id. Все 11 tracked ref (pilot и `semantic/`) дают
snapshot, равный снятому gateway `17f59f8` (`pilot_same.py`). Проверки, которые пропускает
дублированный id, называют причиной то, что ref не говорит, какой из контрактов сравнивать, а не
«последний в порядке файлов». Verifier 117; check 79 и native source 3 прежние (прецедент — T-111).
Отвергнуто: сортировка списка gaps в конце — ломает порядок гэпов одного контракта и групп (M13);
контракты по имени файла — граф и canonical digest зависели бы от того, какой файл держит какой
контракт дублированного id; правка `decision validate` — его issue называет файл путём, раскладка
там часть входа. Два теста T-112, закреплявшие старую границу, переписаны. Границы: имя — текст
листинга (две формы нормализации Unicode — два имени); validation report входит в run id и
называет файл путём в написании ОС только в issue файла (продукт — ref'ом) — где такой issue
есть, перенос файла между validated-каталогами, а при каталоге в пути — и другая ОС, по-прежнему
дают другой прогон; hard link и symlink в `metrics/` — второй файл. Spec —
[contract read order](../specs/contract-read-order.md); §7.4, §7.6 и §8.2 архитектуры, границы
[two contracts of one metric id](../specs/repeated-metric-contracts.md), состояние.

Прогоны: red — первая версия `tests/test_contract_read_order.py` на HEAD: 11 failed, 1 passed
(тест регистра красный только там, где регистр сохраняется), 1 skipped (`.tmp/t120/red.log`). Мутанты: 16 из 16
пойманы (`mutants3.log`; M15 и M16 — имя в NFC или NFD перед сравнением — добавлены по ревью).
Mac, Python 3.13.13, APFS: модули среза и `test_verified_asset_metric_pins` — 230 passed,
1 skipped (POSIX-тест регистра: APFS складывает регистр; оба Unicode-теста выполнены), с
gateway `17f59f8` — 11 failed (`mac3.log`). Первый полный Windows gate (до ревью) —
7113 passed, 94 skipped, покрытие 96.21%, exit 0 (`pytest_v1.log`; прогон перед ним убит
окружением на 94%, exit 143). После первого ревью: black, Ruff, mypy (default, strict, strict
Linux, strict win32) чистые (`checks.log`; `ruff format --check` по-прежнему называет
`gateway.py` из-за двух неявных конкатенаций, которых diff не касается, — так же на HEAD,
форматтер проекта — black); полный Windows gate — 7114 passed, 94 skipped, 38 deselected,
покрытие 96.21%, exit 0 (`.tmp/t120/pytest.log`). Исправления повторного ревью — тексты,
docstring и второй параметр Unicode-теста — проверены модулями, которые читают доки, и
модулями среза — 2041 passed, 87 skipped, exit 0 (`docmods.log`).

Ревью synthetic Astra xhigh (`.tmp/t120/astra_review.out`; не human acceptance): три P1 и P3.
P1 — причина пропуска проверок у дублированного id в evidence говорила о «последнем в порядке
файлов» — переписана. P1 — граница run id между ОС: report называет файл путём только в issue,
каталога в validated paths мало — переписано в spec, архитектуре, состоянии. P1 — обещание
прежнего snapshot не учитывало digest при равных id и версии, а комментарий версии смешивал
перестановку гэпов (canonical digest прежний) с перестановкой узлов (другой) — разделено. P3 —
нормализация Unicode не закреплена — тест `e` + U+0301 и `f.yaml`, мутант M15. Повторное ревью
по diff исправлений (`astra_review2.out`): P1 нет; P2 — условие другого run id: issue продукта
называет его ref'ом, путь файла без каталога одинаков на всех ОС — уточнено; P2 — §8.2
архитектуры и docstring теста T-112 говорили о порядке файлов — переписаны; P3 — тест не ловил
NFD — добавлено составленное `é`, мутант M16. Третьего ревью не было.

## 2026-10-26 — T-119: контрактный файл читается один раз, сколько бы путей до него ни доходили

Срез взят Claude по кандидату handoff (аудит T-111) на «продолжи доработку» владельца (не
synthetic Astra-выбор): из двух оставшихся кандидатов — ложный `duplicate_id` у пересекающихся
путей и порядок gaps по раскладке каталогов — первый давал ложный `block` с неверными словами,
второй меняет только run id прогона, который и так зависит от путей в отчёте валидации.
`decision validate` и `decision verify` читали файлы каждого validated path, как нашли, и файл,
до которого доходили два пути, читался дважды: второе чтение — `contract.duplicate_id`
(«defined in more than one file»). Воспроизведено на HEAD `c14994d` на контрактах pilot
`2026-08-25-ch-control` (`.tmp/t119/head_repro.py`, `head_repro.log`): каталог и файл в нём,
каталог дважды (issue на каждый из четырёх контрактов), `./`, относительный и абсолютный путь,
на Windows — заглавными; везде `block`.
Теперь `_iter_contract_files` (`auto_bi/decision/validate.py`) отдаёт файлы через
`_each_file_once`: файл узнаёт `filesystem.file_identity` — `(st_dev, st_ino)` из `os.stat`
(ссылки пройдены) — или равный текст пути, как его пишет `pathlib` (`.` и двойные разделители
убраны, регистр сохранён); файл читается под первым дошедшим путём, в порядке нахождения, ключи
повтора тоже запоминаются. Путь без идентичности (нет, отказ во взгляде, NUL в имени,
`st_ino == 0`, именованный поток NTFS `файл:поток` — у потоков номер их файла) — один файл только
с путём того же текста: другой регистр — другой путь, файл может прочитаться дважды, два файла за
один — нет. Копия с равными байтами — второй файл. Прогон по каталогу и файлу в нём — тот же
report, run id, evidence digest и verdict, что по каталогу. Verifier 116, check 79 (прецедент —
правило 74: другой набор прочитанных файлов); native source 3 прежний.
Отвергнуто: ключ по тексту `normcase(abspath)` — складывает `..` через symlink лексически и
может склеить два разных файла, молча потеряв контракт (тест POSIX и мутант M3); `resolve()` —
не узнаёт hard link (M4); ключ по пути как есть — не узнаёт `..`, относительный и абсолютный
(M2); та же правка в snapshot semantic ref — меняет смысл source digest («повтор столько раз,
сколько прочитан»), отдельный срез. Границы: идентичность — номера файловой системы как есть
(клон диска рядом с оригиналом может дать двум файлам одну); `x.yaml::$DATA` рядом с `x.yaml` —
два чтения; semantic ref `metrics/` читает hard link и symlink как второй файл
(`gap.metric_duplicate`); непройденный путь — issue на каждый назвавший его путь.
Spec — [contract files once](../specs/contract-files-once.md); §11.1 и §7.6 архитектуры,
USER_GUIDE (таблица команд), состояние.

Прогоны: red — первая версия `tests/test_contract_files_once.py` на копии HEAD (`git archive`,
`PYTHONPATH` на копию): 25 failed, 1 passed (копия — два файла и на HEAD), 1 skipped
(`.tmp/t119/red.log`). Мутанты переписаны под итоговый код: 20 (M1–M20), 19 пойманы на Windows
(`mutants3.log`), M18 — двоеточие считается потоком на любой системе — ловит POSIX-тест, пойман
на Mac (`mac3.log`). Mac, Python 3.13.13, APFS: модули среза и `test_platform_guards` — 213
passed, 1 skipped (тест NTFS).
Граница semantic ref проверена hard link: `gap.metric_duplicate`, а `decision validate` того же
каталога — `pass` (`gateway_probe.log`). Первый полный Windows gate (до ревью) — 7095 passed,
92 skipped, покрытие 96.20% (`v1_pytest.log`); второй, после ревью, — 1 failed: проверка потока
стояла за `os.name`, а `test_platform_guards` требует ветку `sys.platform`, которую читает mypy, —
исправлено (`v2_pytest.log`). Итоговый: black, Ruff, `ruff format --check`,
mypy (default, strict, strict Linux, strict win32) чистые (`checks.log`); полный Windows gate —
7101 passed, 93 skipped, exit 0, покрытие 96.20% (`.tmp/t119/pytest.log`).

Ревью synthetic Astra xhigh (`.tmp/t119/astra_review.out`; не human acceptance): два P1, два P2
и P3 исправлены. P1 — путь без идентичности сравнивался как `WindowsPath`, без регистра: в
регистрозависимом каталоге Windows `a.yaml` и `A.yaml` без идентичности склеивались, и контракт
молча терялся — ключ теперь текст пути с регистром (M15). P1 — именованный поток NTFS
(`contract.yaml:other.yaml`) имеет file ID своего файла, и поток молча выпадал (подтверждено
`ads_probe.py`: поток равных байтов — `pass`) — путь с двоеточием в последнем компоненте на
Windows идентичности не имеет (тест NTFS, M17; M18 на Mac), ветка — `sys.platform == "win32"`. P2 — тест первого пути писал
`root/./x`, который `Path` пишет как путь листинга, и «последний путь на месте первого» проходил
— теперь `sub/..` (M14). P2 — один путь с идентичностью и без неё получал два ключа и читался
дважды — текстовый ключ есть у каждого пути, ключи повтора запоминаются (M16, M19). P3 —
гарантия идентичности без предпосылок — docstring и границы spec называют их. Повторное ревью по
diff исправлений (`astra_review2.out`): P1/P2 нет; P3 — не закреплено, что идентичность повтора
запоминается (`[A без идентичности, A с ней, B с ней]`) — добавлены тест и мутант M20, третьего
ревью нет. Свой брокер Codex на `D:/Auto_BI_new` погашен.

## 2026-10-25 — T-118: момент evidence — одно написание RFC 3339 UTC

Срез выбран Claude на «реши сам, как лучше… в фокусе — качество финального продукта» владельца
(не synthetic Astra-выбор). Из кандидатов — ложный `duplicate_id` у пересекающихся путей, порядок
gaps по раскладке каталогов, `Timestamp` — взят последний: только он запечатывал в evidence
текст, который не момент. `Timestamp` (`auto_bi/scalars.py`) обещал RFC 3339 UTC, а брал любой
текст с `T` и `Z` в конце, который читал `datetime.fromisoformat` с `+00:00` вместо `Z`: момент
без секунд, без разделителей, неделей и днём, с запятой перед дробью. Перед смещением CPython
перестаёт читать дробь на шестой цифре, и всё между ней и `Z` бралось — слово, управляющий
символ, перевод строки и строка вида `verdict: pass`. `24:00:00` читает только Python 3.14:
держит ли bundle момент, зависело от интерпретатора. Воспроизведено на HEAD `3e75954`
(`.tmp/t118/head_repro.py`, `head_repro.log`, копия HEAD через `git archive`): attestation
записанного результата `2026-08-25T08:00:00.123456`, перевод строки, `verdict: passZ` — `decision
verify` exit 0, provenance запечатан со второй строкой; `--created-at …123456abcZ` — момент
manifest; verified asset с `reviewed_at` без секунд — exit 0. На дереве все три — exit 2 одной
строкой (`tree_repro.log`).
Теперь `_utc_timestamp` проверяет форму грамматикой `YYYY-MM-DDTHH:MM:SS[.дробь]Z` на `[0-9]`
(не `\d`: `\d` берёт цифры любой письменности, и `int` их читает), а календарь — конструктором
`datetime` из групп: день есть, час 00–23, минута и секунда 00–59, год 0001–9999. Текст берётся
как написан. Отказ — `must be an RFC 3339 UTC timestamp such as 2026-08-25T09:00:00Z`;
`date`/`datetime` (их строит YAML из момента без кавычек) — `… as text, such as
'2026-08-25T09:00:00Z' in quotes: YAML reads an unquoted one as a date`; `_check_moment`
(`--created-at`, `--captured-at`) говорит те же слова. Каждая запись отказывает там, где на любое
значение своей схемы: attestation и verified asset — exit 2, метрический контракт —
`contract.invalid` (`contracts.validate`) или `gap.contract_invalid` (`semantic.gaps`), данные
investigation — `CanonicalMetricResult`, manifest — `evidence.manifest_invalid`. Контракт, который
проходил, теперь блокирует: verifier 115, check 78 (прецедент T-115); native source 3 прежний.
Отвергнуто: нормализовать текст момента (меняет hash контрактов, которые проходили, и текст,
записанный владельцем; decimal нормализуется ради сравнения чисел, момент не сравнивается
текстом); календарь через `fromisoformat` после грамматики (ответ снова от интерпретатора —
мутант M7); принимать смещения и переписывать в `Z` (два написания одного evidence); переводить
потребителей (`provenance`, окружение, investigation) с `fromisoformat` на свой разбор — строгая
форма читается одинаково на 3.12–3.14, дробь — до микросекунды (граница spec).
Spec — [UTC timestamps](../specs/utc-timestamps.md);
[stated run facts](../specs/stated-run-facts.md) (требование, сценарии, граница «шире RFC 3339»
закрыта), абзац в
[unreadable inputs](../specs/unreadable-inputs.md), §7.1 и §7.6 архитектуры, USER_GUIDE,
граница «`Timestamp` шире RFC 3339» среза 2026-10-05 в состоянии.

Прогоны: red — четыре модуля (`test_utc_timestamps`, `test_stated_run_facts`,
`test_run_input_schema_refusals`, `test_unanswered_engine`) с кодом HEAD: 143 failed, 216 passed
(`.tmp/t118/red.log`). Мутанты (`.tmp/t118/mutants.py`): в первом прогоне выжили M7 (календарь
через `fromisoformat` — на 3.13 он отвергает то же) и M13 (год из четырёх и больше цифр —
конструктор отвергает `12026`); добавлены тест с подменой `scalars.datetime` и годы `02026`,
месяц `008`. Первая форма валидатора прошла все тесты, но не mypy (`value` не сужался до `str`):
переписана через walrus и распаковку шести групп, мутанты прогнаны заново. На итоговой форме
пойманы все: валидатор HEAD, `\d`, `$` вместо `\Z`, дробь до шести цифр и без цифры, без
календаря, календарь через `fromisoformat`, нормализация дроби, без учёта регистра, дата как её
isoformat и с общим отказом, пробел вместо `T`, год из 4+ цифр, `+00:00` вместо `Z`, запятая,
секунды по желанию, старые слова `_check_moment`, версии 114 и 77, после ревью — `\d` только в
дроби (M20; `mutants3.log`, затем M0–M20 — `mutants4.log`). Модули `test_utc_timestamps`,
`test_stated_run_facts`, `test_run_input_schema_refusals` на Python 3.12 и 3.14 (scratch venv из
`uv export --frozen`) — 316 passed каждый, до переписывания и после (`other_pythons.log`,
`other_pythons2.log`). Производители момента проверены: часы CLI пишут `%Y-%m-%dT%H:%M:%SZ`,
`signed_at` — `created_at` manifest, остальное — заявленное или из файла. Pilot bundles
репозитория `decision evidence` читает как на HEAD: пять — exit 0, `owner-review-live-r3` — exit 4
(`evidence.signature_untrusted` без trust root), вывод побайтно как у HEAD. Black, Ruff,
`ruff format --check` изменённых файлов, mypy (default, strict, strict Linux, strict win32) чистые
(`.tmp/t118/checks.log`). Полный Windows gate — 7062 passed, 91 skipped, exit 0, покрытие 96.20%
(`.tmp/t118/pytest.log`); первый запуск автор остановил, когда mypy нашёл ошибку типа, и гейт
прошёл с начала на исправленном коде. Ревью вернулось, пока шёл гейт: docstring `_utc_timestamp`,
доки и три теста модуля момента правились во время прогона, поэтому после него прогнаны модули,
которые читают доки, и модули момента — 2236 passed, 87 skipped (`.tmp/t118/post_review.log`).

Ревью synthetic Astra xhigh (`.tmp/t118/astra_review.out`; не human acceptance): P1 нет, ошибок
диапазона и производителей, пишущих отвергаемое, нет; P2 и два P3 исправлены. P2 — spec,
USER_GUIDE и состояние говорили, что не читается любой bundle с моментом другого написания, а
reader проверяет только моменты manifest (`created_at`, `signed_at`), артефакт — по digest и
размеру: граница сужена, новый тест держит, что bundle с attestation `2026-08-25T08:00:00,000Z` в
payload `results.provenance` читается с exit 0. P3 — цифра другой письменности в дроби не была
закреплена: добавлены `.` + U+0661 и `.123456` + U+0661, мутант M20 пойман. P3 — «читает любую
форму ISO 8601» неточно (`fromisoformat` не читает, например, ordinal date): docstring, spec и §7.1
говорят «формы ISO 8601, которых нет в RFC 3339». Повторное ревью Astra xhigh по diff исправлений
(`astra_review2.out`): замечаний нет; регрессия P2 не прошла бы, если бы `decision evidence`
валидировал attestation внутри артефакта.

## 2026-10-25 — T-117: причина остановки decision-команды — одна строка

Срез взят Claude по кандидату handoff T-116 на «продолжи доработку» владельца (не synthetic
Astra-выбор). Граница T-116: суффикс `; <n> path(s) could not be read: <путь> (<сообщение>)`
отказа `decision verify` печатал путь как есть, и путь с переводом строки делил ошибку на две
строки. Шире — тот же класс у всех decision-команд: `verify`, `capture` и `evidence` печатали
`error: {exc}` как есть, и ключ `"bad\ekey"` в файле продукта, `model.yaml` или verified asset
выводил в терминал сырой ESC (воспроизведено на HEAD `880e452`: путь — две строки stderr, ESC —
у продукта и `model.yaml`). Только `decision investigate` уже записывал непечатаемые символы
escape-последовательностью.
Теперь `cli._one_line` записывает каждый символ, для которого `str.isprintable()` ложно, как
`ascii(char)[1:-1]`, остальные — как есть (обратная косая черта одна); `cli._say_error` печатает
`error: ` и эту строку через `_say` в stderr. Через него идут все строки `error:` команд
`capture`, `verify`, `evidence` (VerifyError, EvidenceReadError, EvidenceSignatureError,
EvidenceDigestError с exit 4, артефакт не текст, артефакт, который поток не кодирует) и
`investigate`; `decision runs` печатает `_one_line(str(exc))` без `error: `, как прежде. Коды
выхода и потоки прежние. `_unread_paths` записывает каждый путь через `display_path`, как
T-116 — файл отвергнутого продукта: только этот текст `VerifyError` и меняется. Файл `cli.py` —
со смешанными CRLF/LF; правка по байтам, конец каждой строки сохранён (`.tmp/t117/patch.py`).
Отвергнуто: `display_path` в каждом месте, где назван путь из обхода (ключи и текст валидатора
остались бы сырыми); экранирование в `VerifyError.__init__` (меняет текст для приложения и не
покрывает ошибки evidence). Verifier 114, check 77, native source 3 прежние.
Spec — [one-line errors](../specs/one-line-errors.md); границы в
[schema-refused products](../specs/schema-refused-products.md) закрыты, абзацы в
[unreadable inputs](../specs/unreadable-inputs.md) и
[unencodable output](../specs/unencodable-output.md), §12.1 архитектуры, USER_GUIDE.

Срез начало другое окно Claude; оно завершилось на полном гейте (18:24, прогон дошёл до 33%,
`.tmp/t117/pytest_interrupted.log`), и срез доведён в новой сессии на том же дереве.

Прогоны: red на HEAD `880e452` — 17 failed, 2 passed (проходят неизменные `decision investigate`
и версии; прогон был до того, как сообщение теста получило U+202E и combining accent, и до одной
проверки use case); на дереве 19 passed. Девятнадцать мутантов (`.tmp/t117/mutants.py`,
`mutants.log`; `mutants2.py`, `mutants2.log`) пойманы: путь как есть; экранирован только LF;
`repr`, `unicode_escape` и `ascii` всего текста; только ASCII-управляющие; сырая строка у verify,
capture, evidence digest, evidence read, артефакта не текст, артефакта, который поток не кодирует,
runs и investigate; ошибка в stdout; без `error: `; каждый путь в кавычках; экранирование в
`VerifyError.__init__` (отвергнутая альтернатива, M18); `print` вместо `_say` (M19). Black, Ruff,
`ruff format --check` изменённых файлов, кроме `cli.py` (его смешанные концы строк не проходят
проверку и на HEAD), mypy (default, strict, strict Linux, strict win32) чистые
(`.tmp/t117/checks.log`). Полный Windows gate — 6958 passed, 91 skipped, exit 0, покрытие 96.19%
(`.tmp/t117/pytest.log`). После гейта менялись только доки и одна константа теста: модули,
которые читают доки, с `test_decision_cli.py` и новым модулем — 2136 passed, 87 skipped
(`.tmp/t117/post_docs.log`, затем `post_review.log`).

Ревью synthetic Astra xhigh (`.tmp/t117/astra_review.out`; не human acceptance): P1 нет, ошибок
реализации нет; два P2 и три P3 исправлены. Spec обещал, что путь отличим от escape по кавычкам,
— но кавычки бывают и в самом имени, а `display_path` и `repr` пишут и другие поля: граница
переписана (строка — для чтения, исходный текст по ней восстанавливается не всегда), та же
оговорка — в USER_GUIDE и состоянии. Граница `unanswered-engine.md` обещала печать target и
ответа драйвера как есть — теперь текст хранит их как есть, а строка экранирует. В USER_GUIDE
стояла сама буква вместо её escape (инструмент правки раскодировал последовательность;
переписано по байтам); в §12.1 — текст исключения меняется только в записи непрочитанных путей.
Тест не держал категорию Zs: в сообщение добавлен no-break space, мутант M20 (экранирует C*, Zl
и Zp, но не Zs) выживал (`m20_before_fix.log`) и теперь пойман; M1–M20 прогнаны заново
(`mutants3.log`). Повторное ревью Astra xhigh по diff исправлений (`astra_review2.out`):
замечаний нет.

## 2026-10-25 — T-116: отказ прогона называет продукт, отвергнутый схемой

Файл `kind: DecisionProduct`, не прошедший схему, — issue `contract.invalid` validation report и
не продукт путей. `decision verify` отказывал его прогону, как прогону продукта, которого нет:
`no decision product contract found in the given paths` или с `--product` `no decision product
'<id>' in the given paths (<прочитанные>)`, exit 2, — а почему, говорил только `decision
validate`. Так было для каждого отказа схемы: повтор в `delivery.dashboards` (T-115), в
`scope.metrics`, файл без `metadata`. Теперь `LoadedContracts` держит отвергнутые схемой файлы
продуктов отдельно (`RefusedProduct`: объявленный id, файл, причина `schema_reason` одной
строкой), в порядке чтения, и `_select_product` продолжает usage error словами `; <n> decision
product(s) failed the schema: '<id>' in <файл> (<причина>)` — с `--product` только файлы с этим
id, без него каждый (первые три и ` (+<k> more)`); непрочитанные пути идут после, как прежде.
Файл без id называется только без `--product` и одним файлом; id — `repr`, путь —
`display_path`. Validation report, гейт и bundle не меняются: verifier 114, check 77, native
source 3 прежние. Spec — [schema-refused products](../specs/schema-refused-products.md);
граница в [repeated delivery entries](../specs/repeated-delivery-entries.md) закрыта, абзац в
[unreadable inputs](../specs/unreadable-inputs.md), §12.1 архитектуры, USER_GUIDE.

Срез взят Claude по кандидату из handoff T-115 на «продолжи доработку» владельца — не synthetic
Astra-выбор. Отвергнуто: искать в report `contract.invalid`, чей subject равен `--product`, —
issue не знает kind своего файла (metric contract, объявивший id продукта, назвался бы
продуктом), а subject файла без id — имя файла, и `--product weekly_margin.yaml` назвал бы его.

Прогоны: red на HEAD `b347050` — 10 failed, 5 passed (проходят неизменные отказы: контракт
другого kind, непрочитанный файл и файл чужого kind, два прочитанных продукта, продукт этого id
рядом с отвергнутым, версии); на дереве 15 passed, с тестом границы причины (`\e` в ключе
проходит как есть) — 16, после правок по ревью — 17; тест T-115 обновлён под новый текст отказа.
Восемнадцать мутантов (`.tmp/t116/mutants.py`, `mutants_post_review.log`) пойманы: без продолжения; поиск по
report с `--product` и без; с `--product` все отвергнутые; предел 5 и 2; непрочитанные пути
раньше; причина — многострочный message report; id файла без id — имя файла; без продолжения в
ветке без `--product`; id без `repr`; путь без `display_path`; продолжение в ветке двух
продуктов; порядок по id; отказы metric contract тоже; счёт `(+k more)` и общий счёт; предел до
фильтра по id (M18, из ревью). Black, Ruff, mypy (default, strict, strict Linux, strict win32)
чистые (`.tmp/t116/checks.log`). Полный Windows gate — 6938 passed, 91 skipped, exit 0, покрытие
96.22% (`.tmp/t116/pytest.log`, до правок по ревью); после правок — 10 соседних модулей с
docs-тестами, 540 passed, 33 skipped; Black и Ruff чистые (`.tmp/t116/post_review.log`).

Ревью synthetic Astra xhigh (`.tmp/t116/astra_review.out`; не human acceptance): P1 и P2 нет,
три P3 исправлены. Spec обещал одну строку всей ошибки, а суффикс непрочитанных путей печатает
путь как есть — обещание сужено до того, что называет продукт, суффикс назван границей. Тесты не
держали фильтр по id до предела трёх под `--product` (предел до фильтра их проходил) — тест с
тремя чужими отказами впереди и мутант M18. Тест версий замораживал 114/77 — теперь нижние
границы, отсутствие подъёма в T-116 видно по diff. Повторного ревью нет.

## 2026-10-24 — T-115: повтор записи в доставке продукта отвергает схема

`DeliverySpec` (`auto_bi/decision/spec.py`) брал `delivery.dashboards` и `delivery.brief_formats`
как написаны. Продукт, который дважды перечислял один ref, проходил `decision validate`;
`bi.contract` компилировал каждую запись, и payload дашборда, чей spec компилировался,
запечатывались под одним ID дважды: `EvidenceBundle` отвергал повтор, и `decision verify`
кончался pydantic `ValidationError` traceback с exit 1 без bundle (`.tmp/t115/repro.out`, P1, на
дереве T-114; хеш ID из T-114 тут не помогает — ref один и тот же). Без spec прогон писал bundle,
который называл дашборд дважды — в subjects, detail и `missing` шага `bi.contract` (P2); так же
`invalid` и `refused` при невалидном spec и отклонённом compile (код шага и тест). Повтор у
другого продукта тех же путей прогон первого не задевал (P3), повтор `markdown` в
`brief_formats` принимался (P4). Теперь оба списка — множества, как списки `DecisionScope`:
`DeliverySpec._check_unique` отвергает повтор (`duplicate entries in delivery.<список>:
<записи>`, каждая один раз, в порядке сортировки). Ref сравниваются целым текстом — у ref одно
написание: `@7`/`@8`, `@7`/`@17`, ref одного slug и одного последнего сегмента доставляются
вместе. Отказ схемы — `contract.invalid` (block) в `decision validate` (exit 2, если продукт был
единственным, рядом с валидным — 4); прогон этого продукта — usage error exit 2 `no decision
product …` без записи; прогон другого продукта тех же путей блокирует `contracts.validate`
(`contracts.issues`), как любой issue загрузки файла. Verifier 113 → 114, check 76 → 77, native
source 3 прежний. Прежние bundle читаются: читатели bundle продукт не разбирают (тест
запечатывает bundle с повтором в обход схемы и проверяет его). Spec —
[repeated delivery entries](../specs/repeated-delivery-entries.md); абзацы — §7.2 и §9.2
архитектуры и USER_GUIDE; граница в [evidence ref ids](../specs/evidence-ref-ids.md) закрыта.

Срез выбрал владелец — кандидат, который выбор T-114 отложил как отдельное правило `DeliverySpec`;
не synthetic Astra. Отвергнуто: issue валидации с дедупом записей в гейте — повтор оставался бы в
digest, чужие продукты путей не блокировались бы, и это расходится с прецедентом `DecisionScope`.
Следующий кандидат: `decision verify --product X` для X, отвергнутого схемой, говорит «нет такого
продукта», а не почему (закрыт T-116).

Прогоны: red на HEAD `951153b` — 15 failed, 7 passed (проходят разные ref и чтение прежних
bundle — то, что не должно меняться); на дереве 22 passed. Тринадцать мутантов
(`.tmp/t115/proto.py`, `.tmp/t115/mutants22.log`) пойманы: без правила; правило только для одного
из двух списков (×2); сравнение без версии, по slug, по последнему сегменту; тихий дедуп до
проверки; сообщение без записей; повторы без сортировки и единственности; дедуп в гейте вместо
правила; правило в field validator продукта, а не в `DeliverySpec`; verifier version и check
version без подъёма (по одной). Black, Ruff, mypy (default, strict, strict Linux, strict win32) и
63 соседних модуля — 2466 passed, 84 skipped (`.tmp/t115/checks.log`, до правок по ревью).
Полный Windows gate после правок по ревью — 6922 passed, 91 skipped, exit 0,
покрытие 96% (`.tmp/t115/pytest.log`).

Ревью synthetic Astra xhigh (`.tmp/t115/astra_review.out`; не human acceptance): P1 и P2 нет,
три P3 исправлены. Тесты не привязывали правило к `DeliverySpec`: перенос в field validator
продукта их проходил (мутант M11 выжил) — добавлен прямой тест `DeliverySpec`, M11 пойман.
Docstring `_check_unique` обещал двойную компиляцию без условия «если spec компилируется». Журнал
называл M10 «версиями без подъёма», хотя он откатывал только verifier version, — добавлен M10b
для check version. По ответам ревью в spec добавлены границы: правило держится для
валидированного продукта (собранный в обход валидации и переданный гейту напрямую грейдится как
есть), а тест прежнего bundle запечатывает его сегодняшним writer. Повторного ревью нет.

## 2026-10-24 — T-114: ID claim и BI payload по хешу ref

`_slugify` (`auto_bi/verification/release_gate.py`) давал ID claim эквивалентного результата
(`claim-<slug asset id>`) и скомпилированного BI payload (`bi-<slug dashboard ref>-<payload id>`)
одним slug: нижний регистр, каждый символ, кроме букв и цифр, — `-`. Эквивалентные assets
`asset://sales/weekly_margin_by_region` и `asset://sales/weekly-margin-by-region` (или
`asset://sales/weekly/margin_by-region`), скомпилированные дашборды `dashboard://weekly_margin@7` и
`dashboard://weekly-margin@7` получали один ID; `EvidenceBundle` отвергал повтор, и `decision
verify` после оценки всех шагов кончался pydantic `ValidationError` traceback с exit 1 — без
bundle, без строки `error:` и без вердикта (`.tmp/t114/audit/f4_slug_collision.out`,
воспроизведено на HEAD `0caec3b`). Теперь ID — `claim-<slug>-<hex>` с полным SHA-256 canonical
JSON `[asset id]` и `bi-<slug>-<payload id>-<hex>` с хешем `[dashboard ref, payload id]`
(`_ref_id`); slug остался впереди для чтения — claim иначе не называет свой asset в `decision
evidence`. Хеш хранит сегменты и разделители, пин и границу между ref и payload id и не зависит
от payload, соседних ref и их порядка; одинаковые payload двух дашбордов делят content address.
Id asset уникален в репозитории (`duplicate verified asset id`), поэтому уникален и ID его claim.
Verifier 112 → 113, check 76 и `NATIVE_SOURCE_VERSION` 3 прежние: ни одна проверка не оценивает
иначе; run id сдвигается у каждого прогона вместе с verifier version. Прежние bundle читаются со
своими slug-ID (pilot `2026-08-25-ch-control` — тест). Граница: повтор одного ref в
`delivery.dashboards` проходит `decision validate` и, если его spec компилируется, кончает
`decision verify` тем же traceback (`.tmp/t114/audit/f4b_repeated_delivery.out`) — это правило
`DeliverySpec`, не ID. Spec — [evidence ref ids](../specs/evidence-ref-ids.md); абзац — §9.2
архитектуры и USER_GUIDE.

Срез выбрала synthetic Astra xhigh (ADR 0006, `.tmp/t114/astra_select.out`) из кандидатов
аудита T-111, перепроверенных на HEAD (`.tmp/t114/audit/`); не human acceptance. Отклонены:
повтор delivery-ref (отдельное правило уникальности `DeliverySpec`), пересечение validated paths
(ложный block, нужна идентичность файлов), нестрогий `Timestamp` (контракт времени и
совместимость старого evidence), порядок gaps и узлов (нестабильный run id при сохранённом
block), тесты `decision runs` из worktree B (дефекта нет, часть fixtures закрепляет нестрогий
`Timestamp`). Astra предлагала голый хеш (`claim-<hex>`, как `result-<hex>` investigation);
Claude Opus 5.5 оставил slug впереди: в `decision evidence` ID claim — единственное место, где
виден его asset.

Windows, Python 3.13.7: `tests/test_evidence_ref_ids.py` на коде HEAD `0caec3b` — 12 failed,
1 passed (`.tmp/t114/red_head.log`; проходит чтение pilot-bundle со старыми ID — оно и не должно
меняться), после правки — 13 passed. Десять мутантов ловятся (`.tmp/t114/mutants.py`,
`.tmp/t114/mutants.log`; каждый валит тест среза): slug вместо хеша у claim и у BI payload
(обязательная пара по выбору), хеш payload вместо ref, хеш slug, хеш одного ref без payload id,
хеш склеенного текста, хеш, усечённый до 16 знаков, позиция артефакта в хеше, pinned ref asset
вместо id, читаемая часть без payload id; файл восстановлен. Точные ID в `assert_digests`
(`tests/test_bi_contract_validation.py`) пересчитаны под новый формат, digests payload прежние.
Black, Ruff, mypy (default, strict, strict Linux, strict win32) чистые; 18 соседних модулей —
714 passed, 51 skipped, 1 failed (`.tmp/t114/checks.log`): `test_internal_markdown_links_resolve`
— новая spec ещё не была в индексе; после `git add` обоих новых файлов docs-тесты на итоговых
доках — 32 passed (`.tmp/t114/docs.log`). Полный Windows offline gate
(`.tmp/t114/gate.py`, `uv run` с pytest-cov и duckdb) — 6899 passed, 91 skipped, exit 0, покрытие
96% (`.tmp/t114/pytest.log`), на дереве до правок по ревью. Прогоны шли медленно: одни и те же
пять модулей прошли за 31 с, а следующим прогоном — за 11 мин; вероятная причина — Defender
(`MsMpEng`), не проверена.

Ревью synthetic Astra xhigh (`.tmp/t114/REVIEW_BRIEF.md` → `.tmp/t114/astra_review.out`; не
human acceptance) шло одновременно с gate. P1: spec обещала, что ни один прежний bundle не держит
двух ref одного slug, но неэквивалентный asset не даёт claim, а пропущенный, невалидный или
отклонённый дашборд — payload, и такие ref остаются в checks: отказ был только при двух записанных
ID. Уточнены spec, архитектура, CURRENT_STATE, журнал, CLAUDE.md, комментарий `#: 113` и
сообщение коммита. P2: замедление прогонов приписывалось Defender без доказательства — теперь это
гипотеза. P3: хеш ref со сжатыми повторами `-` проходил тесты — добавлена пара `weekly-margin@7`
/ `weekly--margin@7`, мутант `hash-of-collapsed-separators` ловится (`.tmp/t114/mutant_sep.py`,
`.tmp/t114/mutant_sep.log`). P3: USER_GUIDE называет хешируемые массивы canonical JSON. Slug
перед хешем ревью сочло оправданным, повтор delivery-ref — верно оставленным `DeliverySpec`.
После правок: 28 модулей (срез и читающие правленые доки) — 1061 passed, 51 skipped,
black и ruff чистые; на дереве T-114 повтор delivery-ref по-прежнему кончается тем же traceback
(`.tmp/t114/audit/f4b_repeated_delivery.t114.out`). Повторного ревью нет.

## 2026-10-24 — T-113: spec дашборда по полному пути ref

`_dashboard_file` (`auto_bi/decision/use_cases.py`) называл файл spec последним сегментом
ref и пином: `dashboard://sales/weekly_margin@7` и `dashboard://ops/weekly_margin@7` читали
один `weekly_margin@7.yaml`. Без spec ops-дашборда verifier 111 компилировал под его ref
вёрстку другого дашборда, запечатывал её payloads и читал её плитки в chart impact;
`bi.contract` давал `bi.compiled` вместо review `bi.spec_missing`, и вердикт pilot-раскладки
`needs_review` становился `pass_with_advisory` (`.tmp/t113/audit/f1_dashboard_segment.out`,
воспроизведено на HEAD `9076ed0`). Продукт, доставляющий только
`dashboard://ops/weekly_margin@7`, так же читал вёрстку `dashboard://weekly_margin@7`.
Теперь каждый сегмент ref, кроме последнего, — каталог: spec читается из
`<dashboards>/sales/weekly_margin@7.yaml` (`.yaml`, `.yml`, `.json` — в этом порядке), отката на
файл последнего сегмента нет. Файла нет — `bi.spec_missing` и `chart.dashboard_spec_missing`,
сегмент-файл вместо каталога — тоже; отказ взгляда или чтения и невалидный `DashboardSpec`
кончают прогон, как прежде, с путём файла, и соседний суффикс его не заменяет. Сегмент ref —
`[a-z0-9][a-z0-9_-]*`, поэтому текст пути не выходит из каталога и два ref не дают один путь;
symlink, junction и hard link под каталогом читаются насквозь — граница в spec. Ref из одного
сегмента (все tracked pilot-ref) читают прежний файл. Verifier 111 → 112, check 75 → 76,
`NATIVE_SOURCE_VERSION` 3 прежний; гейт, compile и chart impact берут specs по ref и не
менялись, run id сдвигается у каждого прогона — verifier version в ключе. Spec — [dashboard spec paths](../specs/dashboard-spec-paths.md); абзац —
§12.1 архитектуры (форматы входов) и USER_GUIDE.

Срез выбрала synthetic Astra xhigh (ADR 0006, `.tmp/t113/astra_select.out`) из кандидатов
аудита T-111, перепроверенных на HEAD (`.tmp/t113/audit/`); не human acceptance. Отклонены:
slug и повтор delivery-ref (явный отказ, отдельный контракт идентичности), пересечение
validated paths (ложный block), нестрогий `Timestamp` (нужен контракт времени и
совместимости старого evidence), порядок gaps и узлов (нестабильный run id, вердикт закрыт
T-112), тесты `decision runs` из worktree B (дефекта нет, часть fixtures закрепляет
нестрогий `Timestamp`). Claude Opus 5.5 проверил выбор независимо: грамматика сегмента
исключает `..`, точки и верхний регистр; `ENOTDIR` и путь через файл на Windows — «нет
файла» (`filesystem._NOT_THERE`).

Windows, Python 3.13.7: `tests/test_dashboard_spec_paths.py` на коде HEAD `9076ed0` — 14
failed (`.tmp/t113/red_head.log`), после правки — 14 passed. Девять мутантов ловятся
(`.tmp/t113/mutants.py`, каждый валит тест среза): откат на файл последнего сегмента, только
последний сегмент, только первый или только последний каталог, сегменты в одном имени файла,
имя без пина, откат на старшую версию, обратный порядок суффиксов, взгляд мимо `_looked_at`;
файл восстановлен. Black, Ruff, mypy (default, strict, strict Linux, strict win32) чистые; 17
соседних модулей до ревью — 942 passed, 84 skipped (POSIX-режимы файлов на Windows не
исполняются). Полный Windows offline gate на итоговом дереве: 6886 passed, 91 skipped, 38
deselected, coverage 96.18%, exit 0. Ревью synthetic Astra xhigh
(`.tmp/t113/astra_review.out`), не human acceptance: P1 — гарантия «путь не выходит из
каталога, два ref не делят файл» верна только для текста пути; docstring, spec, §12.1 и журнал
сужены до неё, symlink, junction и hard link — граница spec. P3 — алгоритм run identity
прежний, но run id сдвигается у каждого прогона вместе с verifier version; сказано явно. P3 —
у тестов отказа взгляда, отказа чтения и невалидного spec теперь валидный `.yml` рядом; два
новых мутанта — отказ взгляда, отказ чтения или разбора передают ход следующему суффиксу —
ловятся, всего одиннадцать; на коде HEAD тесты среза — снова 14 failed
(`.tmp/t113/red_head2.log`). P2 нет; повторного ревью нет.

## 2026-10-24 — T-112: два контракта одного metric id — blocking gap snapshot'а

Граф native snapshot держит узел на каждый загруженный контракт, и `SemanticGraph` допускает
два узла одного metric id. `diff_snapshots` оставлял последний узел id,
`SemanticSnapshot.metric` — первый, а узлы одного id стоят в порядке путей файлов. Старая
версия запинненной метрики (v2, другая формула) рядом с текущей v3 в base: verifier 110 из
`metrics/` давал `block` (diff сравнивал v2), из `metrics/archive/` — `pass_with_advisory`
(`semantic.unchanged`); воспроизведено на HEAD `269285c` (`.tmp/t112/head_layouts.py`).
Теперь `_build_graph` после узлов метрик добавляет на каждый id с двумя и более контрактами
blocking `gap.metric_duplicate` по адресу `metrics.<id>`: `metric is defined by <n> contracts
(versions …): which of them the ref means is not known`, версии по возрастанию с повторами;
гэпы стоят после остальных и в порядке id. Узлы остаются, поэтому
`metric_ref.candidate_ambiguous` прежний. `UNTRUSTWORTHY_GAPS` включает новый гэп:
`semantic.diff`, `impact.products`, `assets.invalidated`, `results.reconcile`,
`results.provenance` и `sql.compile` — `check.not_run` с причиной о дубле, при обоих гэпах —
обе причины через `; `. `not_compared` для pin, которого candidate не держит, остаётся только
при `gap.contract_invalid` (`UNLOADED_CONTRACT_GAPS`). Verifier 110 → 111, check 74 → 75,
`NATIVE_SOURCE_VERSION` 3 прежний. Spec — [two contracts of one metric
id](../specs/repeated-metric-contracts.md); абзацы — §7.4 и §8.2 архитектуры и USER_GUIDE;
поправлены границы трёх смежных spec.

Срез выбрала synthetic Astra xhigh (ADR 0006) из кандидатов аудита T-111; не human
acceptance. Отклонены: namespace dashboard spec (нужен контракт имени файла), slug и повтор
delivery-ref, пересечение validated paths, `Timestamp`. Claude Opus 5.5 проверил выбор
независимо. Модель 2026-10-04 держала такой гэп вне пропуска шагов, чтобы правила pin
оставались достижимы, но тогда bundle называл `semantic.unchanged` при двух определениях.
Принят сегодняшний вариант; его цена — `asset.metric_ref_ambiguous` гейт больше не выносит
(правило осталось в `VerifiedAssetRepository.resolve_metric_pins`, граница записана).
Повторов metric id в десяти pilot-ref нет (`.tmp/t112/pilot_duplicates.py`).

Windows, Python 3.13.7: новые и изменённые тесты на коде HEAD `269285c` — 17 failed. На
итоговом дереве до тестов по ревью `pytest tests/test_repeated_metric_contracts.py
tests/test_semantic_gateway.py tests/test_verified_asset_metric_pins.py
tests/test_product_metric_pins.py tests/test_decision_verify.py tests/test_run_identity.py
tests/test_repeated_declarations.py tests/test_unlisted_paths.py tests/test_docs_as_code.py
tests/test_docs_defaults.py` — 604 passed, 33 skipped. На дереве с тестами по ревью
одиннадцать мутантов ловятся (`.tmp/t112/mutants.py`, каждый валит тест среза): гэпы не в
порядке id, версии без повторов, версии по сортировке текста, гэп только при разных версиях,
severity review, число разных версий вместо числа контрактов, дубль не пропускает шаги,
дубль подавляет pin, из двух причин только последняя, только первая, в обратном порядке;
файлы восстановлены. Ruff, Black и mypy (default, strict, strict Linux, strict win32)
чистые. Полный Windows offline gate на итоговом дереве: 6872 passed, 91 skipped, 38
deselected, coverage 96.18%, exit 0. Ревью synthetic Astra xhigh
(`.tmp/t112/astra_review.out`): P1 нет. P2 — не описана граница DWH: шаг 7 спрашивает о
запинненной метрике по id, то есть о первом из двух её контрактов, и планы `dwh.cost`
следуют порядку файлов; граница записана в spec и §8.2 и закреплена offline-тестом. P3 —
«digest меняется» неверно для побайтовых копий: сужено до различающихся контрактов. P3 —
однозначные версии не ловили сортировку как текста: добавлены тест 2, 10, 2 и мутант.
Повторного ревью нет.

## 2026-10-24 — T-111: source digest snapshot адресует каждый прочитанный файл

`NativeModelSource.snapshot` хранил digest контракта под `path.name`, а `_contract_files`
обходит `metrics/` рекурсивно. Из двух одноимённых контрактов разных подкаталогов
(`metrics/a/gross_margin_pct.yaml`, `metrics/b/gross_margin_pct.yaml`) в `source_digest`
оставался последний по пути. Комментарий, добавленный в первый, не менял ни graph, ни
canonical и source digest: два прогона с разными байтами входа получали один run id —
вопреки требованию [run identity](../specs/run-identity.md) запечатывать snapshot с digest
байтов, из которых он прочитан. Так же через API файл контрактов с именем `model` вытеснял
digest модели. Теперь `_source_digest` хранит под именем одного файла его digest, как
прежде, а под именем нескольких — их отсортированный список с повторами. Каталог под
`metrics/` в digest не входит (требование [unreadable inputs](../specs/unreadable-inputs.md)
о вложенных каталогах сохранено), две байтовые копии — два файла. Ref без повторяющихся
имён сохраняет digest verifier 109: у всех десяти pilot-ref он равен запечатанному в их
bundles. Verifier 109 → 110; check 74 и `NATIVE_SOURCE_VERSION` прежние. Сценарий — в run
identity, абзац — в §7.4 архитектуры.

Срез выбрала synthetic Astra xhigh (ADR 0006) из кандидатов предыдущей сессии и того же
дефекта, найденного Claude Opus 5.5 независимо; не human acceptance. Реализовал субагент
Claude Opus в отдельном git worktree (новые тесты на HEAD: 5 failed), интегрировал и
проверил Claude Opus 5.5: docstring и комментарий версии сужены до того, что проверено.
Windows, Python 3.13.7: `pytest tests/test_semantic_gateway.py tests/test_run_identity.py
tests/test_unlisted_paths.py tests/test_decision_verify.py tests/test_docs_as_code.py
tests/test_docs_defaults.py` — 324 passed, 33 skipped. Мутанты last-wins, dedupe, без
сортировки, всегда список, «сводка вместо списка из трёх» и «контракт `model` вытесняет
модель» ловятся (6/1/2/2/1/1 failed),
файл восстановлен. Ruff, Black и mypy (default, strict, strict Linux, strict win32) чистые.
Полный Windows offline gate на итоговом дереве: 6855 passed, 91 skipped, 38 deselected,
coverage 96.18%, exit 0. Ревью synthetic Astra xhigh: P1 — тексты обещали неизменный
snapshot при перестановке файлов, но gaps идут в порядке файлов, — сужены до source
digest; P3 — не было теста на три разных одноимённых файла (мутант «сводка min/max/count»
проходил), тест добавлен. Остальное — без замечаний; повторного ревью нет.

Параллельно read-only аудит (субагент Claude Opus) нашёл дефекты того же семейства.
Snapshot зависит от раскладки при двух контрактах одного metric id: перенос старой версии
запиненной метрики в `metrics/archive/` меняет вердикт `block` на `pass_with_advisory`
(воспроизведено и на дереве T-111); при контрактах с gaps — их порядок.
Dashboard spec называется только последним сегментом ref: `sales/x@7` и `ops/x@7` — один
файл. Slug id claim и артефактов теряет различия, и повтор delivery-ref обрывает прогон
traceback'ом. Субагент Claude Sonnet написал тесты `decision runs` на потоках без UTF-8 и
нашёл, что evidence `Timestamp` пропускает текст между микросекундами и `Z`
(`datetime.fromisoformat` в 3.13 принимает `…123456abc+00:00`). Его тесты строк держатся
на этой нестрогости и не влиты. Скрипты — `.tmp/t111/`.

## 2026-10-24 — T-110: полный offline-прогон на Mac и в Linux после T-109

Последний полный прогон вне Windows был на дереве T-97; T-98…T-109 проверялись на Windows,
где пропускаются 86 проверок POSIX modes и отказов доступа и 1 named pipe (всего 91
skipped). На Mac
(iMac Intel, macOS 13.7.8) дерево `5cb2c08` прошло под Python 3.13.13, 3.12.13 и 3.14.6
с DuckDB: каждый прогон — 6919 passed, 20 skipped, 38 deselected, exit 0. Black, Ruff и
mypy (Linux, strict Linux, strict win32) чистые. 20 skips — Windows junction/ACL/read-only,
`Illegal byte sequence` macOS, Playwright и отсутствующий Anthropic SDK.

Рабочий каталог `/tmp/autobi_t47` пришлось пересобрать: ежедневная очистка `/tmp` в macOS
удалила файлы прежних venv, первый запуск завершился за 44 s без тестов (отказ среды).
Затем Linux: изолированный Colima `autobi-linux` на том же Mac, контейнер
`python:3.12-slim` по digest, CPython 3.12.15, дерево `636772f` (тот же код плюс запись
Mac-проверки). От непривилегированного пользователя: 6921 passed, 18 skipped,
38 deselected, exit 0, coverage 96.00%; Ruff, Black и три режима mypy чистые. Linux
исполняет два теста имён, которые macOS отвергает (6921 + 18 = 6919 + 20). Контейнер
удалён, профиль остановлен.
Исполнитель — Claude Opus 5.5, без модельного ревью: изменена только документация.
Код и версии 109/74 прежние; Linux 3.13/3.14 и live DWH не запускались. Отчёты с
командами и средой: [Mac](../DECISIONOPS_MAC_CHECKS_2026-10-24.md),
[Linux](../DECISIONOPS_LINUX_CHECKS_2026-10-24.md).

## 2026-10-23 — T-109: JSON-контракты при обходе validated каталогов

Архитектура §11.1 называет decision products и metric contracts Git-tracked YAML/JSON, и
явно переданный JSON-файл загружался, но обход каталога брал только `.yaml`/`.yml`.
JSON-контракт в каталоге молча выпадал из `decision validate` и `contracts.validate`:
битый, неизвестного kind или дублирующий JSON не блокировал, а pin продукта на метрику,
описанную только в JSON, не разрешался. Теперь `_iter_contract_files` берёт `.yaml`,
`.yml` и `.json`, в том числе во вложенных каталогах; loader и правила те же, что у явно
переданных файлов. Посторонний JSON в переданном каталоге блокирует как
`contract.unknown_kind` — это сказано в USER_GUIDE и §11.1. Каталог с contract suffix
(`archive.json/`, а теперь и `old.yaml/`, который раньше блокировал как нечитаемый
контракт) обходится, а не читается (`_names_a_contract`); путь, на который файловая система
не даёт посмотреть, по-прежнему читается. Каталог `metrics/` semantic ref не затронут:
snapshot читает там только
YAML, и JSON рядом не меняет graph и digests — это закреплено тестом. Справка CLI путей
контрактов — «YAML or JSON». Verifier 108 → 109, check 73 → 74; historical bundles прежние.

Срез начат интерактивной сессией Codex (код, версии, тесты validate/verify, USER_GUIDE,
§11.1); владелец закрыл её до коммита. Доделал Claude Opus 5.5: граница snapshot и её тест,
справка CLI, записи состояния и журнала.
Windows: `uv run pytest tests/test_decision_validate.py tests/test_decision_verify.py
tests/test_semantic_gateway.py tests/test_decision_cli.py tests/test_docs_as_code.py
tests/test_docs_defaults.py tests/test_unlisted_paths.py tests/test_validation_issue_scope.py
tests/test_repeated_declarations.py tests/test_run_identity.py tests/test_surrogate_escapes.py
tests/test_unreadable_run_inputs.py -q` — 928 passed, 33 skipped, exit 0. Мутанты до ревью: обход
только YAML — 7 failed из 26 выбранных тестов; snapshot, читающий JSON, — новый тест падает.
Ruff check, Black --check и mypy (Linux, strict Linux, strict win32) чистые.
Полный Windows offline gate (`uv run --with pytest-cov --with duckdb pytest -q -rs
--tb=short --cov=auto_bi --cov-report=term --cov-fail-under=90`, Python 3.13.7): 6843
passed, 91 skipped, 38 deselected, 1 failed, coverage 96.20%. Упал статический
`test_no_decision_command_prints_past_the_one_function`: его перечень команд не знал
`_decision_runs` из T-106, и полный набор был красным с `3253b3b`. Исправлено отдельным
коммитом `4877671`, модуль — 119 passed; весь набор повторно не прогонялся.
Synthetic Astra xhigh review (cw, read-only, по ADR 0006): 2 замечания. P1 — обход
отдавал и каталоги, и `archive.json/` читался как контракт, блокируя проверку; исправлено
`_names_a_contract` и тестами на каталоги `.json`/`.yaml`/`.yml` и на отказ посмотреть на
путь. P2 — USER_GUIDE и §11.1 не говорили, что посторонний JSON блокирует; дописано.
Тест границы snapshot по гипотезе ревью держит и валидный JSON-контракт рядом. После
правок: `uv run pytest tests/test_decision_validate.py tests/test_semantic_gateway.py
tests/test_decision_verify.py tests/test_unlisted_paths.py tests/test_unreadable_run_inputs.py
tests/test_validation_issue_scope.py -q` — 463 passed, 33 skipped; пять мутантов (обход
только YAML, каталог читается, отказ посмотреть отбрасывает путь, snapshot читает JSON,
snapshot читает только валидный JSON) обнаружены; Ruff/Black чистые. Повторного ревью не
было; это не human acceptance.
Итоговое дерево: тот же полный Windows offline gate — 6848 passed, 91 skipped,
38 deselected, exit 0, coverage 96.18%; Ruff check, Black --check и mypy (Linux, strict
Linux, strict win32) чистые. Mac/Linux runtime и live DWH не запускались.

## 2026-10-23 — T-108: операторский путь от discovery к проверке evidence

По synthetic owner/design selection Astra xhigh (ADR 0006) закрыт пробел T-106
в USER_GUIDE: команда `decision runs` добавлена в таблицу и walkthrough.
Поля каталога объяснены; адресующий `run_id` и `manifest_digest` одной строки
передаются в inspection. Отдельный пример читает signed `report-summary-md`
с тем же pin и независимым trust key. Текст различает discovery, полную inspection
и чтение одного payload; pin фиксирует manifest, но не удостоверяет производителя
и не создаёт атомарный snapshot каталога. Обычная inspection при stale pin
возвращает отчёт в stdout и exit 4; `--artifact` — пустой stdout, stderr и exit 4.

Изменена только документация. Исполнитель — Codex (`gpt-5.6-luna` недоступна).
Production-код, тесты и версии evidence 108/73 прежние.
Windows: `uv run pytest tests/test_decision_runs.py tests/test_evidence_inspect.py
tests/test_evidence_signing.py tests/test_docs_as_code.py tests/test_docs_defaults.py
-q --tb=short` — 92 passed, 1 skipped, exit 0. После добавления примера artifact:
`uv run pytest tests/test_decision_cli.py tests/test_docs_as_code.py
tests/test_docs_defaults.py -q --tb=short` — 66 passed, exit 0.
Финальный synthetic Astra xhigh review: существенных замечаний нет; уточнение,
что пустой каталог даёт `[]` только при `--json`, внесено. Это не human acceptance.
Полный offline gate, Mac/Linux runtime и live DWH не запускались.

## 2026-10-23 — T-107: отказы discovery до чтения manifest

По synthetic owner/design selection Astra xhigh (ADR 0006) закреплён существующий
контракт архитектуры §11.2. Четыре адресных OS-отказа: `os.listdir(root)`,
`os.stat(run)`, `os.stat(manifest)` и `os.lstat(manifest)` после отсутствующего stat.
Отдельно проверен реальный каталог вместо `manifest.json`. Все пять случаев
проверяются в text/JSON: exit 2, пустой stdout, одна строка stderr с расположением
и классом ошибки; приватный текст PermissionError не выводится. Учёт чтений
подтверждает обработку первого корректного run до отказа второго.

Реализация — Codex: указанная в правилах `gpt-5.6-luna` недоступна в списке моделей.
Production-код и evidence versions 108/73 не менялись. Новых runtime нет.
Windows: `uv run pytest tests/test_decision_runs.py tests/test_refused_evidence.py
tests/test_evidence_run_root.py tests/test_unreadable_run_inputs.py -q --tb=short`
— 338 passed, 74 skipped, exit 0. Профильный модуль — 22 passed, 1 skipped.
`uv run ruff check tests/test_decision_runs.py` и
`uv run black --check tests/test_decision_runs.py` — exit 0.
Финальный synthetic Astra xhigh review: существенных замечаний нет;
это не human outcome или production acceptance. Полный offline gate,
Mac/Linux runtime и live DWH в этом срезе не запускались.

## 2026-10-23 — T-106: локальный каталог evidence-прогонов

По synthetic owner/design-partner selection Astra xhigh (ADR 0006) реализован
первый read-only `RunRepository.list_runs()`, архитектура §11.2.
`uv run python -m auto_bi.cli decision runs <evidence-root> --json` возвращает
список по имени run-каталога: адресующий и manifest run ID, product/time и digest
однократно прочитанных байтов manifest. `integrity_checked=false`; подпись и
payload hashes не проверяются. Выбранный bundle проверяет `decision evidence`.
Нет индекса, записи, рекурсии, новых runtime или изменений evidence versions 108/73.

Пустой root успешен, незапечатанные каталоги пропускаются. Невалидный/нечитаемый
manifest и недоступный root дают exit 2 без частичного stdout; store сохраняет
защиту от symlink/junction. Слишком глубокий JSON также обрабатывается штатно:
дефект найден при Astra-review, подтверждён failing test, исправлен.
Тесты используют реальные bundles, включая signed bundle без trust с испорченным
payload; проверяют единственное чтение manifest и отсутствие store writes.

Windows: `uv run pytest tests/test_decision_runs.py tests/test_decision_cli.py
tests/test_evidence_bundle.py tests/test_evidence_store.py tests/test_evidence_inspect.py
tests/test_evidence_signing.py tests/test_evidence_run_root.py tests/test_docs_as_code.py
tests/test_docs_defaults.py -q` — 178 passed, 14 skipped, exit 0.
Ruff и Black по трём изменённым Python-файлам чистые; strict mypy Linux/win32
по `auto_bi/decision/runs.py auto_bi/cli.py` — без ошибок.
Полный offline gate не повторялся; Mac/Linux runtime и live DWH не запускались.
Финальный synthetic Astra xhigh review: существенных замечаний нет;
это не human outcome или production acceptance.
В current state исправлен устаревший procedural human gate на ADR 0006.

## 2026-10-23 — T-105: полный Windows offline gate после T-104

После профильных проверок T-98–T-104 выбран полный нативный Windows offline gate.
HEAD 0d17bf3, Python 3.13.7 с DuckDB: 6786 passed, 118 skipped, 38 deselected,
coverage 96.15%, exit 0, 931.48 s. Один запуск; дополнительных исправлений и повторов
не потребовалось. Ruff/Black и mypy Linux/strict Linux/strict win32 — exit 0.
Код, тесты и версии evidence 108/73 прежние. Изменена только документация.

118 skips обусловлены Windows symlink/POSIX доступом, named pipe, отсутствующим
Playwright и проверками отсутствующего SDK; 38 deselected — integration/e2e.
Это не полный CI и не проверка runtime Mac/Linux или live DWH.
[Отчёт с командами и средой](../DECISIONOPS_WINDOWS_CHECKS_2026-10-23.md).
Synthetic Astra xhigh selection/review: 0 actionable findings, не human acceptance.
Docs-as-code после добавления отчёта в индекс: 25 passed; первоначальный отказ
касался ссылок на ещё untracked отчёт. Исполнение — Codex.

## 2026-10-23 — T-104: буквальные пути в invalid-key диагностике

Четыре сообщения invalid key в Ed25519 loaders вставляли raw source: читаемый файл
с U+2028 в имени разбивал диагностический текст на строки при отказе PEM parser или
при ключе другого алгоритма. Все четыре сообщения теперь используют display_path;
обычные Unicode-имена сохраняют прежний текст. Коды signing_key_invalid/trust_key_invalid,
исходные причины, политика доверия и успешные payload не менялись.

Восемь тестов test_evidence_invalid_key_paths.py используют реальные временные файлы
и parsers: private/public × malformed PEM/EC key × Unicode/U+2028. Проверяются точные
loader detail/code/cause, CLI exit 2, пустой stdout и одна строка stderr, отсутствие
verify/locate_evidence/output, неизменность файла ключа. До исправления — 4 failed,
4 passed; после — 8 passed. Нативные LF/ESC-имена и Mac/Linux не проверялись.

Windows: все test_evidence*.py через rg --files плюс test_decision_cli.py —
211 passed, 13 skipped, exit 0. Ruff и Black --check двух затронутых Python-файлов,
uv run mypy --platform win32 --strict auto_bi/evidence/signing.py — exit 0.
Выбор и финальное synthetic review — Astra xhigh, actionable findings нет;
не human/production acceptance. Реализация — Codex, gpt-5.6-luna недоступна.
Версии evidence 108/73 прежние. Полный offline gate не запускался.

## 2026-10-23 — T-103: штатный отказ чтения некодируемого пути ключа

Загрузчики Ed25519 private/trust keys перехватывали только OSError; UnicodeEncodeError
из Path.read_bytes выходил наружу через decision verify/evidence. Теперь он становится
существующим EvidenceSignatureError signing_key_unreadable/trust_key_unreadable с
исходной __cause__. Обе unreadable-диагностики используют filesystem.display_path:
непечатаемые символы пути видны как escapes, сообщение исключения не выводится.
CLI возвращает 2 с пустым stdout и одной строкой stderr до запуска verify/locate_evidence;
следующий trust key не читается. Invalid PEM diagnostics и неожиданные ошибки прежние.

Новый tests/test_evidence_key_paths.py: private/trust × loader/CLI × Unicode/непечатный
путь × UnicodeEncodeError/OSError; дополнительно RuntimeError пробрасывается и реальные
ключи с Unicode-именами подписывают и проверяют payload. После исправления двух ошибок
тестового harness (повторный --trust-key и обязательный signed_at), до правки продукта:
12 failed, 7 passed; после правки — 19 passed. Encoding refusal смоделирован на Windows,
нативное поведение файловой системы POSIX здесь не проверялось.

Windows: uv run pytest по всем test_evidence*.py (rg --files) и test_decision_cli.py:
203 passed, 13 skipped, exit 0. Ruff/Black --check двух затронутых Python-файлов и
uv run mypy --platform win32 --strict auto_bi/evidence/signing.py — exit 0.
Выбор и финальное synthetic review — Astra xhigh, actionable findings нет;
не human/production acceptance. Реализация — Codex, gpt-5.6-luna недоступна.
Evidence и версии 108/73 прежние. Полный offline gate и Mac/Linux не запускались.

## 2026-10-23 — T-102: бюджет объединения групп двух периодов

Tests-only срез закрепляет max_groups_per_step для contribution и concentration:
лимит считается по объединению категорий comparison/current. Проверены одинаковые
{A,B}/{A,B}, пересекающиеся {A,B}/{B,C} и непересекающиеся {A,B}/{C,D} наборы,
каждый с бюджетом union−1 и union. Skill возвращает точный budget.groups_exceeded
при превышении; реальный CLI даёт exit 2 и пустой stdout до создания output parent.
На точной границе CLI публикует четыре artifacts с исходным typed output и plan digest.

Прежняя проверка одинаковых категорий пропускала in-memory мутант подсчёта максимума
по одному периоду. Новый модуль обнаруживает его (4 failed, 8 passed), подсчёт суммой
периодов (4 failed, 8 passed) и >= вместо > (6 failed, 6 passed); каждый mutant pytest
завершился ожидаемым exit 1. Файлы продуктового кода для мутаций не менялись.

Выбор и финальное synthetic review — Astra xhigh: actionable findings нет,
не human/production acceptance. Реализация — Codex; gpt-5.6-luna недоступна.
Windows: uv run pytest tests/test_investigation_group_budget.py -q — 12 passed;
все tests/test_investigation*.py, выбранные через rg --files, — 433 passed, exit 0.
Ruff и Black --check нового модуля — exit 0. Продуктовый код, evidence и версии
108/73 прежние. Полный offline gate и Mac/Linux после тестов не запускались.

## 2026-10-23 — T-101: отказ очистки после появления чужого output

Tests-only срез закрепляет существующий контракт публикации investigation: другой
писатель создаёт output при записи последнего staging-артефакта, затем rmtree
отказывает с PermissionError. Application сохраняет исходную причину коллизии и
ошибку очистки, с cleanup error в прямом __cause__; CLI возвращает 2, пустой stdout
и точную диагностику обеих причин. Rename не вызывается. Чужой output с бинарным
sentinel неизменен; staging содержит четыре полностью записанных реальных artifacts.
Teardown снимает подмены и проверяет расположение staging перед его удалением.
Общая безопасность гонки после последней проверки существования не заявляется.

Продуктовый код, evidence и версии 108/73 прежние. Выбор и финальное synthetic review —
Astra xhigh: замечаний нет, не human/production acceptance. Реализация — Codex;
gpt-5.6-luna недоступна. Windows: новый модуль
`tests/test_investigation_cleanup_collision.py` — 2 passed; весь набор
`tests/test_investigation*.py` через `rg --files` и pytest — 421 passed, exit 0.
Ruff и Black --check нового модуля — exit 0. Полный offline gate и Mac/Linux
после добавления тестов не запускались.

## 2026-10-23 — T-100: проверки runtime budget investigation

Tests-only срез закрепляет существующий контракт `max_runtime_ms`: ровно лимит разрешён,
превышение на 1 нс даёт exit 2 до создания output. Проверены границы до первого skill,
после первого и последнего skill, после настоящего compiler; отказ не запускает
следующие этапы. На успешном пути сохранены четыре artifacts, plan digest и семь claims.
Часы детерминированы, начало ненулевое; нет sleep и фиксации числа обращений к часам.
Обёртки исполняют настоящий CLI/application/skills/compiler, подставных outputs нет.

Runtime budget — проверки между этапами, не прерывание вычислений и не ограничение
полного wall time с загрузкой YAML, сериализацией и записью. Продуктовый код, evidence
и версии skill/verifier/check прежние. Оснований дублировать stop-GAP policy в compiler
или вводить раннюю остановку группировки найдено не было; эти изменения не выполнялись.

Выбор и финальное synthetic review — Astra xhigh по ADR 0006: замечаний нет;
не human/production acceptance. Реализация — Codex; gpt-5.6-luna недоступна.
Windows, `.venv\Scripts\python.exe -m pytest tests/test_investigation_runtime_budget.py -q`:
12 passed. Весь `tests/test_investigation*.py` через список `rg --files` и pytest:
419 passed, exit 0. Ruff и Black `--check` нового модуля — exit 0.
Четыре локальных in-memory мутанта в отдельных pytest-процессах обнаружены assertions:
удаление проверки до skill, после skill, после compiler — по 1 failed;
замена `>` на `>=` — 4 failed. Исходные продуктовые файлы при этом не менялись.
Полный offline gate и Mac/Linux после добавления тестов не запускались.

## 2026-10-23 — T-99: параметры output соответствуют объявленному шагу

`compile_brief` принимал настоящий результат другого валидного плана с теми же
step id/metric/skill/version/kind, но с другим dimension или top_n, и связывал factual
claims с digest целевого плана. Теперь contribution/concentration проверяют точное
совпадение dimension с измерением шага, concentration дополнительно проверяет top_n.
Несовпадение — `BriefCompilationError` до построения brief. GAP, соответствующие outputs,
успешные bytes/digests и версии skill/verifier/check прежние. Штатный application сам
запускает объявленный шаг; дефект обычного CLI этим воспроизведением не заявляется.
Арифметическая достоверность произвольного output остаётся вне этого среза.

Выбор и финальное synthetic review — Astra xhigh по ADR 0006: замечаний нет;
не human/production acceptance. Реализация — Codex; gpt-5.6-luna недоступна.
Нативный Windows Python: regression-модуль до правки — 4 failed, 6 passed;
после — 10 passed, с двумя дополнительными GAP-контролями — 12 passed, exit 0.
Финальный набор `tests/test_investigation*.py` (14 модулей через `rg --files` и массив
аргументов PowerShell) — 407 passed, exit 0; команда `.venv\Scripts\python.exe -m pytest
@tests -q --tb=short`. Предыдущий прогон до двух GAP-контролей — 405 passed.
Тесты используют настоящие producer plans и run_skill: обе схемы dimension,
меньший/больший top_n, положительный top_n сверх числа групп, сохранение bytes/digest.
`python -m ruff check auto_bi/investigation/claims.py tests/test_investigation_output_parameters.py`
и `python -m black --check` с теми же путями — exit 0.
`uv run --with mypy --with types-PyYAML mypy --platform win32 --strict auto_bi/investigation/claims.py`
— exit 0, один модуль. Полный offline gate и Mac/Linux после правки не запускались.

## 2026-10-23 — T-98: однострочная диагностика отказов investigation

Допустимое имя отсутствующего dimension с LF добавляло вторую строку stderr при
`scope.unsupported_dimension`. CLI теперь показывает непечатаемые символы всех
`OfflineInvestigationError` видимыми escapes; обычный Unicode и пробелы сохраняются.
Граница — обработчик отказов `decision investigate`; общий `_say`, raw domain error,
GAP detail, policy, evidence и версии 108/73 прежние. До создания output отказ остаётся
exit 2; существующие правила очистки при ошибках записи не менялись.

Выбор и финальное synthetic review — Astra xhigh по ADR 0006. Функциональных замечаний
нет; замечание о CRLF в добавленных строках исправлено. Не human/production acceptance.
Реализация — Codex: предписанная gpt-5.6-luna отсутствует среди доступных моделей.

Windows, нативная `.venv\Scripts\python.exe`: новый regression-модуль до исправления —
7 failed, 2 passed; после — 9 passed. Полный набор `tests/test_investigation_*.py` —
395 passed, exit 0 (13 модулей переданы явно). Новые кейсы используют настоящий YAML
и CLI: LF, CRLF, CR, TAB, ESC, Unicode separators/bidi, обычный ASCII и кириллица;
проверены raw error, точный stderr, пустой stdout и отсутствие output parent.
`python -m ruff check auto_bi/cli.py tests/test_investigation_policy_diagnostics.py`
и `python -m black --check` с теми же путями — exit 0.
Полный offline gate и Mac/Linux после этой правки не запускались.

## 2026-10-23 — T-97: буквальный step id в заголовке GAP investigation Markdown

Компилятор называет блок GAP `Gap: {step_id}`, а грамматика step id допускает `_` между
`-` или `.` и буквой: `## Gap: null-_dimension_-check` CommonMark показывал курсивом без
подчёркиваний. Renderer экранирует step id, когда блок ссылается на один GAP и title
точно совпадает с шаблоном; custom title и несколько evidence references выводятся как
сохранены. Raw title, JSON/digests, causal policy и версии 108/73 прежние; байты
заголовка меняются у step id с `_`, `-` или `.`. Mac: новый модуль на прежнем
`claims.py` — 5 failed, 4 passed; пять планов — JSON-payload побайтно прежние, в
`brief.md` отличаются только строки `## Gap:`; полный прогон 3.12/3.13/3.14 — каждый
6809 passed, 19 skipped, exit 0; Black, Ruff и mypy как в CI чистые. Реализация —
Claude Opus 5.5; финальное synthetic Astra xhigh: 1 P2 — тесты не закрепляли байты
заголовка для `-` и `.`, мутант с экранированием одного `_` проходил. Исправлено тестом
точных строк заголовка, мутант ловится; итоговые код и тесты: gate 3.13 — 6811 passed, 19 skipped, exit 0.
Не human/production acceptance.
[Проверки и границы](../DECISIONOPS_INVESTIGATION_GAP_TITLES_CHECKS_2026-10-23.md).

## 2026-10-23 — T-96: тесты T-74, T-75 и T-82 на macOS

Полный прогон T-95 на Mac давал 6 failed, и те же шесть падали до T-95: тесты
запускались только на Windows. Вложенность 3000 превышает лимит JSON-декодера только
там (macOS разбирает до 9997–37286 уровней), поэтому три теста T-82 — reader, CLI и
приоритет digest pin — строят manifest в 100 000 уровней с предусловием `RecursionError`.
Мутант reader без перехвата `RecursionError` ловят все шесть depth-кейсов, мутант с
разбором до проверки pin — тест pin; прежние CLI-тесты и тест pin на macOS их пропускали.
Суррогатные кейсы имён пропускаются только при `EILSEQ` (APFS). Mac 3.12/3.13/3.14 до
правки по ревью: каждый 6800 passed, 19 skipped, exit 0; Black, Ruff и mypy как в CI
чистые; после правки модули — 94 passed, 2 skipped; Windows, пять модулей: 96 passed.
Продуктовый код и версии 108/73 прежние. Реализация — Claude Opus 5.5; финальное
synthetic Astra xhigh: 1 P2 (тест pin тоже брал 3000) исправлено, не human/production
acceptance.
[Проверки и границы](../DECISIONOPS_TEST_PORTABILITY_CHECKS_2026-10-23.md).

## 2026-10-23 — полный прогон T-95 на Mac

Дерево `db54a05` на imac-julia, с DuckDB: Python 3.13.13, 3.12 и 3.14.6 — каждый
**6 failed, 6796 passed, 17 skipped, 38 deselected**; Black, Ruff и mypy как в CI
(Linux, strict Linux, strict win32) чистые. Те же шесть тестов падают в своих модулях и
на `7363962`, до T-95: это не регрессия, а тесты T-74, T-75 и T-82, которые запускались
только на Windows. APFS не принимает имя с байтом вне UTF-8, а вложенность 3000
превышает C-лимит JSON-декодера только на Windows; продуктовые ответы на macOS
управляемые. Код и тесты не менялись. Live DWH не запускался.
[Проверки и границы](../DECISIONOPS_SUMMARY_REFS_CHECKS_2026-10-23.md).

## 2026-10-23 — T-95: буквальные refs продукта, метрик и дашбордов в summary

Грамматика ref допускает `_` и `-`, и валидный ref с `-_x_-` в заголовке summary и
строках metrics/dashboards читался как курсив: подчёркивания пропадали, курсив
склеивал соседние refs. Все эти refs теперь проходят `_literal`; raw refs, пропуск
metrics, равной subject, и grading сохранены. Verifier 107 → 108, check 73; новые
run IDs меняются. Red: 5 failed, 1 passed. Отдельный коммит чинит 13 тестов в шести
модулях, которые с T-93 искали в summary сырой detail: полный набор на T-93/T-94
был красным, смежные наборы их не включали. Полный прогон: 9 failed, 6703 passed —
все 9 подпроцессы `0xC0000142` и wall-clock бюджет при нехватке памяти, их модули
отдельно 170 passed. Ruff/Black и strict mypy Linux/Windows чистые. Реализация —
Claude Opus 5.5; финальное synthetic Astra xhigh: 1 P2 (описание test helper)
исправлено формулировкой, не human/production acceptance.
[Проверки и границы](../DECISIONOPS_SUMMARY_REFS_CHECKS_2026-10-23.md).

## 2026-10-22 — T-94: буквальные subjects semantic impact в summary

Изменение типа governed-поля переносило свободное имя в ProductImpact.subject,
добавляя ложные Verdict/Owner action при BLOCK. Весь subject теперь проходит
`_literal`; raw subject, привязки impact и grading сохранены. Остальные поля вне среза.
Verifier 106 → 107, check 73; новые run IDs меняются. Подтверждённый red: 4 failed,
2 passed; смежный green: 354 passed. Ruff/Black и strict mypy Linux/Windows чистые;
docs/new tests: 31 passed. Реализация Codex при недоступной `gpt-5.6-luna`;
выбор и финальное ревью — synthetic Astra xhigh: 0 actionable findings,
не human/production acceptance.
[Проверки и границы](../DECISIONOPS_SUMMARY_IMPACT_SUBJECTS_CHECKS_2026-10-22.md).

## 2026-10-22 — T-93: буквальные пояснения всех проверок в summary

Role/target/evidence из environment facts попадали в raw CheckResult.detail и
добавляли ложные Verdict/Owner action. Все details теперь проходят `_literal`
в списках findings/undone, таблице и Product scope intro, включая PASS details.
Raw findings и grading сохранены. Verifier 105 → 106, check 73; новые run IDs меняются.
Red: 24 failed, 10 passed; смежный green: 348 passed. Ruff/Black и strict mypy
Linux/Windows чистые; docs/new tests: 59 passed. Реализация Codex при недоступной
`gpt-5.6-luna`; выбор и финальное ревью — synthetic Astra xhigh:
0 actionable findings, не human/production acceptance.
[Проверки и границы](../DECISIONOPS_SUMMARY_CHECK_DETAILS_CHECKS_2026-10-22.md).

## 2026-10-22 — T-92: буквальные пояснения chart impact в summary

Свободный Measure.label попадал через measure_alias в ChartImpact.detail и добавлял
ложные Verdict/Owner action при BLOCK. Весь detail теперь проходит `_literal`
для обеих relations; raw payload и grading сохранены. Другие свободные поля вне среза.
Verifier 104 → 105, check 73; новые run IDs меняются. Red: 7 failed, 4 passed;
смежный green: 314 passed; strict mypy Linux/Windows чистый.
Ruff/Black чистые; финальные docs/new tests: 36 passed.
Реализация Codex при недоступной `gpt-5.6-luna`; выбор и финальное ревью — synthetic
Astra xhigh: 0 actionable findings, не human/production acceptance.
[Проверки и границы](../DECISIONOPS_SUMMARY_CHART_DETAILS_CHECKS_2026-10-22.md).

## 2026-10-22 — T-91: буквальные пояснения semantic impact в summary

Timezone/calendar метрики попадали через semantic diff в ProductImpact.detail
и добавляли ложные Verdict/Owner action при BLOCK. Весь detail теперь проходит
`_literal`; исходный payload и grading сохранены. Другие свободные поля вне среза.
Verifier 103 → 104, check 73; новые run IDs меняются. Red: 5 failed, 2 passed;
смежный green: 303 passed; strict mypy Linux/Windows чистый.
Ruff/Black чистые; финальные docs/new tests: 32 passed.
Реализация Codex при недоступной `gpt-5.6-luna`; выбор и финальное ревью — synthetic
Astra xhigh: 0 actionable findings, не human/production acceptance.
[Проверки и границы](../DECISIONOPS_SUMMARY_IMPACT_DETAILS_CHECKS_2026-10-22.md).

## 2026-10-22 — T-90: буквальные пояснения пробелов анализа в summary

Quoted SQL-имя попадало через reads.unattributed в ChartGap.detail и добавляло
ложные Verdict/Owner action при BLOCK. Весь detail теперь проходит `_literal`
для всех типов гэпа. Raw detail, gap codes, SQL-анализ и grading сохранены;
другие impact/check details вне среза. Verifier 102 → 103, check 73;
новые run IDs меняются. Red: 10 failed, 6 passed; смежный green: 296 passed.
Ruff/Black и strict mypy Linux/Windows чистые; docs-as-code: 25 passed.
Выбор и финальное ревью — synthetic Astra xhigh: 0 actionable findings,
не human/production acceptance. Реализовал Codex при недоступной `gpt-5.6-luna`.
[Проверки и границы](../DECISIONOPS_SUMMARY_GAP_DETAILS_CHECKS_2026-10-22.md).

## 2026-10-22 — T-89: буквальные ссылки на чарты в summary

Допустимый ChartSpec.id добавлял ложные Verdict/Owner action через ChartImpact.ref
и ChartGap.subject при BLOCK. Теперь обе позиции используют `_literal`, без
внешних backticks у chart ref. Все gap subjects охвачены; details и остальные
поля вне среза. Raw refs/subjects, relations, gap codes и grading сохранены.
Verifier 101 → 102, check 73; новые run IDs меняются. Red: 7 failed;
смежный green: 280 passed; strict mypy Linux/Windows чистый.
Финальные Ruff/Black чистые; после форматирования docs/new tests: 32 passed.
Выбор среза и финальное ревью — synthetic Astra xhigh: 0 actionable findings,
не human/production acceptance. Реализовал Codex при недоступной `gpt-5.6-luna`.
[Проверки и границы](../DECISIONOPS_SUMMARY_CHART_REFS_CHECKS_2026-10-22.md).

## 2026-10-22 — T-88: буквальное отображение владельцев в summary

Допустимый metadata.owner добавлял ложные Verdict/Owner action через список
ProductImpact.owners при реальном BLOCK. Каждый owner теперь проходит `_literal`
перед соединением списка; порядок, разделители, raw evidence и grading сохранены.
Остальные поля impact вне среза. Verifier 100 → 101, check 73; новые run IDs меняются.
Red: 4 failed, 2 passed; смежный green: 211 passed. Ruff/Black и strict mypy
Linux/Windows чистые. Выбор среза и финальное ревью кода/тестов — synthetic Astra xhigh:
0 actionable findings, не human/production acceptance.
Реализовал Codex при недоступной `gpt-5.6-luna`.
[Проверки и границы](../DECISIONOPS_SUMMARY_OWNERS_CHECKS_2026-10-22.md).

## 2026-10-22 — T-87: буквальное отображение среды в summary

Допустимое имя среды с Markdown и переводами строк добавляло ложный Verdict/Owner
action в заголовке и через detail проверки provenance. Теперь заголовок Environment
и весь detail `results.provenance` используют существующий literal-renderer;
таблица не экранирует pipe повторно. Исходные findings/claims, JSON payload и grading
прежние. Остальные свободные поля summary вне среза. Verifier 99 → 100, check 73;
обычная пунктуация этих полей также экранируется, новые run IDs меняются.
Red заголовка: 5 failed, 2 passed; отдельный red provenance: 3 failed.
Смежный green: 342 passed; strict mypy Linux/Windows чистый.
Выбор среза и финальное ревью — synthetic Astra xhigh: 0 actionable findings,
не human/production acceptance. После форматирования/документации: 35 passed;
Ruff/Black и diff check чистые.
Реализовал Codex при недоступной `gpt-5.6-luna`.
[Проверки и границы](../DECISIONOPS_SUMMARY_ENVIRONMENT_CHECKS_2026-10-22.md).

## 2026-10-22 — T-86: запрет перенаправленного каталога evidence run

Store проверяет сам run-root через lstat до resolve/I/O и отвергает symlink/junction,
включая ссылку на соседний run. Configured root остаётся доверенным, OSError сохраняется.
Версии 99/73 прежние; bundles через обычный root и configured-root junction побайтно
совпадают. Red: 12 failed, 1 passed, 9 skipped. Профиль: 194 passed, 76 skipped.
Ruff/Black и strict mypy Linux/Windows чистые. Synthetic Astra xhigh: 0 P1/P2,
не human/production acceptance. Codex реализовал при недоступной `gpt-5.6-luna`.
Дополнительная проверка T-86 на `7453e3f` (2026-10-22), выбранная synthetic Astra xhigh:
полный offline Windows/Python 3.13.7 без DuckDB — 6582 passed, 134 skipped,
38 deselected in 561.71s, exit 0; отдельный профиль с DuckDB — 15 passed, exit 0.
Результаты не суммируются. Код не менялся. Native POSIX и live DWH/Mac не проверялись;
Windows junction проверены, девять новых symlink-сценариев пропущены из-за
привилегии Windows. Модельный выбор не является human/production acceptance.
[Проверки и границы](../DECISIONOPS_RUN_ROOT_CHECKS_2026-10-22.md).

## 2026-10-22 — T-85: проблемы других продуктов в сводке

Условный раздел summary показывает source/code/reported severity/subject/message
из прежнего other_product_issues, с буквальным отображением Markdown/control text.
Grading, payload, claims и owner action прежние; пустой список не создаёт раздел.
Verifier 99, check 73; historical bundles сохранены. Red: 7 failed; профиль: 134 passed.
Контроль старого/нового renderer: четыре verdict-профиля без чужих issues побайтно
совпали. Synthetic Astra xhigh по коду/тестам: 0 P1/P2, не human acceptance.
Codex реализовал при недоступной `gpt-5.6-luna`.
Полный offline на финальном коде: 6584 passed, 109 skipped, 38 deselected in 538.85s,
exit 0. Ruff/Black, strict mypy Linux/Windows чистые. Review документации: 0 P1/P2.
[Проверки и границы](../DECISIONOPS_OTHER_PRODUCT_SUMMARY_2026-10-22.md).

## 2026-10-22 — T-84: проверка ключей recorded results

Неизвестный ключ результата больше не игнорируется: exit 2 до probe/evidence.
Ключ точно сопоставляется с полным единожды прочитанным asset repository;
известные out-of-scope assets допустимы. Приоритет отказа схем значений,
bare/attested формы и отсутствие ответа сохраняют прежнее поведение.
Контракт выбран synthetic Astra xhigh по ADR 0006, не human acceptance.
Codex реализовал при недоступной `gpt-5.6-luna`; версии 98/73 прежние.
Red: 12 failed, 7 passed. После исправления P2 однострочной диагностики:
422 passed, 1 skipped; Ruff/Black и strict mypy Linux/Windows чистые.
Второе Astra review: 0 открытых P1/P2; полный offline/live gate не повторялся.
[Проверки и границы](../DECISIONOPS_RESULT_KEYS_CHECKS_2026-10-22.md).

## 2026-10-22 — полный offline-baseline после T-83

На `b75edef`, Windows/Python 3.13.7 с duckdb: 6551 passed, 109 skipped,
38 deselected in 527.35s, exit 0. Один полный прогон; код, тесты и версии 98/73
не менялись. Synthetic Astra xhigh подтвердила выбор задачи; это не
human/production acceptance. Live DWH/Mac и отдельные lint/type-check gates
не запускались. [Команда и границы](../DECISIONOPS_OFFLINE_T83_VERIFICATION_2026-10-22.md).

## 2026-10-22 — T-83: Unicode-суррогаты в тексте evidence claim

`Claim.unit/caveats` проверяются на допустимость Unicode до canonicalization.
JSON-escape одиночного суррогата больше не вызывает traceback полной inspection
и не допускается к artifact reader: `evidence.manifest_invalid`/exit 4 либо
`EvidenceReadError`/exit 2 без payload. Digest pin имеет прежний приоритет.
Допустимые Unicode, пробелы, canonical bytes и подписи сохранены; версии 98/73.
Red: 14 failed, 5 passed; evidence: 232 passed, 67 skipped; investigation и
регрессии: 53 passed; финальные Unicode-тесты: 21 passed. Ruff/Black чистые;
четыре файла обычного bundle сохранили SHA-256. Полного/live gate не было.
Выбор и review production diff с исходными 19 cases — synthetic Astra xhigh:
0 открытых P1/P2, не human/production acceptance. Codex реализовал срез при
недоступной `gpt-5.6-luna`; два положительных roundtrip добавлены после review.
[Проверки и границы](../DECISIONOPS_CLAIM_UNICODE_CHECKS_2026-10-22.md).

## 2026-10-22 — T-82: управляемый отказ глубоко вложенного JSON manifest

`decision evidence --artifact` переводит `RecursionError` при разборе manifest
в существующий `EvidenceReadError`: exit 2, одна строка stderr, пустой stdout,
без содержимого manifest и записи файлов. Неверный digest pin имеет прежний
приоритет; полная inspection сохраняет `evidence.manifest_invalid` и exit 4.
Writer, подписи, grading и версии 98/73 не менялись.
Red: 6 failed, 1 passed; профиль: 190 passed, 67 skipped; после уточнения тестов
семь регрессий прошли. Ruff/Black чистые. Выбор — synthetic Astra xhigh;
реализация — Codex при недоступной `gpt-5.6-luna`. Synthetic Astra xhigh review:
0 открытых P1/P2, не human/production acceptance.
Полный offline suite финального кода и live DWH/Mac не запускались.
[Проверки и границы](../DECISIONOPS_MANIFEST_DEPTH_CHECKS_2026-10-22.md).

## 2026-10-22 — полная offline-проверка после T-81

Код `e05703ca44e267d589a4c5ce1ddd972c77f4ff33`, нативный Windows/Python 3.13.7:
`uv run --frozen --with duckdb pytest -q --tb=short` — **6523 passed, 109 skipped,
38 deselected in 460.52s**, exit 0. Закрыт пробел полного прогона после T-79–T-81;
production-код и тесты не менялись, нового функционального среза нет.
Ограниченное synthetic Astra xhigh review investigation не выявило подтверждённого
следующего дефекта и подтвердило выбор verification-only этапа.
Live DWH/Mac не запускались; это не human/production acceptance.
[Подробности и границы](../DECISIONOPS_OFFLINE_VERIFICATION_2026-10-22.md).

## 2026-10-22 — T-81: неотрицательность исходных строк концентрации

`concentration_shift` больше не принимает отрицательную supplied row, скрытую
положительной суммой категории. Существующий `method.negative_value` GAP заменяет
ошибочный concentration result; brief не выпускает concentration claims. Приоритет
прежних input/additivity/group diagnostics сохранён. Variance/contribution допускают
отрицательные значения как прежде. Skill version 2, verifier/check 98/73 прежние;
новые outputs ошибочно допускавшихся входов меняются, сохранённые brief читаются.

Выбор и review — synthetic Astra xhigh; реализация — Codex при недоступной
`gpt-5.6-luna`. Red: 14 failed, 7 passed; green: 21 passed; после добавления
проверок diagnostic precedence профиль: 27 passed, весь investigation: 375 passed.
Ruff/Black и strict mypy Linux чистые. Четыре payload обычного fixture побайтно
совпали с исходной функцией. Astra независимо повторила профиль и сравнение:
0 открытых P1/P2. Полный offline suite и live DWH не запускались;
не human/production acceptance.
Проверки и границы — [T-81](../DECISIONOPS_CONCENTRATION_DOMAIN_CHECKS_2026-10-22.md).

## 2026-10-22 — T-80: управляемый отказ environment SQL guard

CLI переводит `SQLGuardError` только внутри вызова `collect` в `VerifyError`:
exit 2, пустой stdout, фиксированная строка без текста SQL/ANSI, без evidence.
Для обычного CLI logging `NullHandler` убирает sqlglot warnings через `lastResort`;
явно настроенные handlers, levels и propagation сохранены, их вывод вне гарантии.
Guard, прямые collector/runtime вызовы, catalog factory и посторонние исключения
сохраняют поведение. Каталоги и runtime connection могут предшествовать отказу;
запрещённый runtime SQL не исполняется. Версии 98/73 прежние. Воспроизведение
подставляет `PlanQuestion`; обычный compiler input, порождающий этот SQL, не найден.

Реализация — Codex при недоступной `gpt-5.6-luna`; выбор и review — synthetic Astra xhigh.
Red: 12 failed, 18 passed; green: 30 passed; финальный смежный набор: 448 passed.
Первый review нашёл P2 в parser warning; red: 4 failed, после правки: 16 passed.
Заключительный review: P2 закрыт, 0 открытых P1/P2. Docs-as-code/defaults: 32 passed.
Ruff/Black и strict mypy Linux/Windows чистые. Четыре completed runs с fake DWH
сохранили по 20 файлов bundle побайтно относительно исходного HEAD.
Полный offline suite и live DWH не запускались; не human/production acceptance.
Проверки и финальное review — [T-80](../DECISIONOPS_ENVIRONMENT_SQL_GUARD_CHECKS_2026-10-22.md).

## 2026-10-22 — T-79: буквальный detail GAP в Markdown investigation

Имя dimension с Markdown/HTML давало разметку в описании `input.null_dimension`,
достижимого через contribution/concentration. Теперь renderer экранирует detail
`InvestigationGap`, когда блок имеет одну evidence reference и его body точно
совпадает с шаблоном компилятора. Код GAP остаётся code span. Raw body, output,
JSON/digests и causal policy сохранены; версии 98/73 прежние. Экранирование всей
пунктуации detail меняет также Markdown обычных GAP. Custom prose и неоднозначная
связь сохраняют отображение; legacy brief защищён при повторном рендеринге.

Реализация — Codex при недоступной `gpt-5.6-luna`; выбор и review — synthetic Astra
xhigh, один финальный проход: 0 открытых P1/P2, не human/production acceptance.
Red: 8 failed, 10 passed; Markdown-модуль: 43 passed; investigation: 348 passed.
Ruff/Black и package-wide strict mypy Linux чистые. Четыре payload обычного fixture
побайтно сохранены; brief JSON/digest пяти GAP-входов совпали с исходным HEAD.
Полный offline-набор на T-79 не повторялся; последний полный green — T-78.
Подробные проверки — [T-79](../DECISIONOPS_INVESTIGATION_GAP_MARKDOWN_CHECKS_2026-10-22.md).

## 2026-10-22 — T-78: буквальные category/unit в Markdown investigation

Допустимые category/unit больше не внедряют заголовки, ссылки и HTML в Markdown
variance/contribution-блоков компилятора. Рендерер экранирует значения только при
единственном связанном output и точном совпадении body с исходным шаблоном.
Raw JSON, Claim, evidence и causal policy прежние; custom prose и неоднозначная
связь сохраняют поведение. Прежние compiler-produced brief защищаются при новом
рендеринге; уже записанный Markdown не переписывается. Версии 98/73 сохранены.

Реализация — Codex при недоступной `gpt-5.6-luna`; выбор и review — synthetic Astra
xhigh. Первый review подтвердил P2 промежуточного экранирования в JSON; обработка
перенесена в renderer, второй review: 0 открытых P1/P2, не human acceptance.
Red: 18 failed, 2 passed; investigation: 327 passed до трёх дополнительных
compatibility cases. Четыре обычных payload и шесть brief JSON специальных входов
побайтно сохранены. Ruff/Black и package-wide strict mypy Linux чистые. Полный
offline-набор с duckdb на финальном Python-коде: 6436 passed, 109 skipped,
38 deselected in 1282.17s, exit 0; все 25 новых cases прошли. SQLite budget прошёл;
два медленных теста после faulthandler-диагностики также завершились успешно.
Подробные проверки — [T-78](../DECISIONOPS_INVESTIGATION_MARKDOWN_CHECKS_2026-10-22.md).

## 2026-10-22 — T-77: управляемый отказ causal-wording policy investigation

Подставленные category/unit, нарушающие существующий запрет причинных формулировок,
больше не завершают offline investigation необработанной `ValidationError`.
Компилятор различает эту policy-ошибку по собственному типу и переводит только её
в `BriefCompilationError`; application и CLI возвращают штатный отказ, exit 2.
Диагностика фиксированная, без входных значений; output и отсутствующий parent
не создаются. Прочие исключения, сам запрет и версии verifier 98 / check 73 прежние.

Реализация — Codex при недоступной `gpt-5.6-luna`; выбор и review — synthetic Astra
xhigh, 0 открытых P1/P2, не human/production acceptance. Red: 6 failed, 2 passed;
investigation и смежные проверки: 381 passed (305 + 76). Успешные четыре payload
сохранены побайтно; Ruff, Black и package-wide strict mypy Linux чистые.
Полный baseline, начатый до правки: 6401 passed, 109 skipped, 38 deselected,
1 failed (SQLite write budget, 25.66s при лимите 5s). Отдельная диагностика —
1 passed, call 0.84s; полного green gate финального кода нет.
Подробные проверки — [T-77](../DECISIONOPS_INVESTIGATION_POLICY_CHECKS_2026-10-22.md).

## 2026-10-22 — T-76: различимая идентичность evidence investigation

Устранены коллизии между сегментами metric ref и между границами step/metric при
построении evidence ID. Новый ID содержит полный SHA-256 canonical пары
`[step_id, metric_ref]`; claims и blocks адресуют правильный output. Markdown явно
называет шаг и метрику. Новые brief bytes/digest меняются; прежний JSON читается,
plan/results и skill-output digests сохранены. Версии verifier 98 / check 73 прежние.

Реализация — Codex при недоступной `gpt-5.6-luna`. Synthetic Astra xhigh выбрала срез
и выполнила два прохода review: P2 о читаемости Markdown исправлен; 0 открытых P1/P2.
Это не human/production acceptance. Финальный investigation-набор: 296 passed,
Ruff и Black чистые. Red/green, совместимость и границы —
[в отчёте T-76](../DECISIONOPS_INVESTIGATION_IDENTITY_CHECKS_2026-10-22.md).

## 2026-10-22 — T-75: отказ кодирования output-пути investigation

`decision investigate` переводит `UnicodeEncodeError` при подготовке, записи или
публикации output в отказ с exit 2 и очищает staging по существующему правилу.
Диагностика называет output и класс без текста исходного исключения; непечатаемые
символы пути экранируются. Существующий output сохраняется, читаемые Unicode-пути
и U+DCFF сохраняют байты четырёх артефактов. Версии verifier 98 / check 73 прежние.

Реализация — Codex при недоступной `gpt-5.6-luna`. Synthetic Astra xhigh выбрала срез
и провела два прохода review: P2 о LF/CR в пути устранён, открытых P1/P2 нет.
Это не human/production acceptance. Полный offline-прогон начат до последней правки
диагностики и остановлен примерно на 9%; полного результата на финальном коде нет.
Профильные проверки, red/green и команды —
[в отчёте T-75](../DECISIONOPS_INVESTIGATION_OUTPUT_PATH_CHECKS_2026-10-22.md).

## 2026-10-22 — T-74: некодируемое имя входа investigation

`decision investigate` переводит `UnicodeEncodeError` от загрузки plan/result в
однострочный отказ `cannot read <path>: UnicodeEncodeError`, exit 2, пустой stdout.
Исходная причина сохранена, её текст не печатается. Более поздние входы не читаются;
выходной каталог и отсутствующий parent не создаются. Читаемые Unicode-имена и
U+DCFF дают прежние байты артефактов; неожиданные исключения сохраняют свои границы.
Output paths и другие команды не менялись; версии evidence 98/73 прежние.
Red: 6 failed, 5 passed; финальный профильный набор: 258 passed, exit 0.
Ruff, Black и package-wide strict mypy Linux/Windows чистые.
Реализовал Codex при недоступной `gpt-5.6-luna`; выбор и review кода, тестов,
спецификаций и архитектуры — synthetic Astra xhigh: 0 P1/P2.
Записи статуса и отчёт проверены Codex; это не human/production acceptance.
Полный baseline на исходном HEAD намеренно прерван до правки, итогового результата нет.
Mac-проба подтверждает только отказ stdlib на Python 3.9.6, не запуск проекта.
[Проверки и границы](../DECISIONOPS_INVESTIGATION_PATH_CHECKS_2026-10-22.md).

## 2026-10-22 — T-73: согласованы границы ошибок capture

Устаревшие абзацы Known boundaries в [stated-run-facts](../specs/stated-run-facts.md) и
[unreadable-inputs](../specs/unreadable-inputs.md) согласованы с реализованным T-67:
`ClickHouseError`, `StreamFailureError` и `psycopg.Error` дают класс и нормализованный
текст в одной строке, exit 2, без публикации результата и повторного запроса.
Остальные исключения и допустимые значения environment сохраняют прежние границы.
Изменена только документация; код, тесты и версии evidence 98/73 прежние.
Реализовал Codex при недоступной `gpt-5.6-luna`; выбор и review двух спецификаций —
synthetic Astra xhigh: 0 P1/P2. Записи статуса проверены Codex.
Независимая проверка Astra: 37 passed. Проверка Codex: `python -m pytest -q
tests/test_capture_driver_failures.py tests/test_docs_as_code.py --tb=short` —
66 passed, exit 0 (Windows/Python 3.13.7). Полный offline suite и живые стенды не запускались.
Не human/production acceptance.

## 2026-10-22 — T-72: ошибки аргументов на строгом stderr

Закрыта документированная граница T-71: ошибочная Unicode-команда или option больше
не теряет диагностику из-за `UnicodeEncodeError` в strict stderr. Подкласс parser
добавляет encoding fallback в `_print_message`, сохраняя выбор потока и подавление
`AttributeError`/`OSError` стандартного argparse. Контракт `print_help` отдельный и
сохранён; обработчики команд, grading и версии evidence 98/73 прежние.
Red: 27 failed, 69 passed; green: 96 passed; смежный набор: 282 passed.
Ruff, Black и strict mypy для Linux/Windows чистые. Реализовал Codex при недоступной
`gpt-5.6-luna`; выбор и итоговое review — synthetic Astra xhigh: 0 P1/P2.
Ревьюер независимо проверил пять сценариев повторной записи и выбора потока;
документационные проверки: 32 passed.
Не human/production acceptance.
[Проверки и границы](../DECISIONOPS_PARSER_DIAGNOSTICS_CHECKS_2026-10-22.md).

## 2026-10-22 — T-71: справка CLI на потоке с ограниченной кодировкой

Закрыта документированная граница T-60: root `--help` и `eval --help` больше не
падают на тире под ASCII/Latin-1. Подкласс parser меняет только `print_help`: текст
предлагается без изменения, после `UnicodeEncodeError` неподдерживаемые символы
получают backslash-escape. Форматирование и выбранный поток сохраняются; глобальные
потоки не перенастраиваются, остальные сообщения parser и handlers прежние.
Другие ошибки записи справки пробрасываются. Версии evidence 98/73 не менялись.
Red нового модуля: 9 failed, 47 passed; первый green: 56 passed. После review добавлены
два теста явного потока вывода; финальный смежный набор: 244 passed.
Его первый прогон выявил старый AST-тест T-60, не учитывавший `_decision_settings`
из T-68; тот же отказ воспроизведён на HEAD. Helper добавлен в проверяемое множество,
assertion о запрете прямой печати сохранён. Ruff, Black, strict mypy Linux/Windows
чистые. Десять вариантов вывода и exit codes совпали с HEAD на Unicode-потоке;
документационные проверки: 32 passed. Реализовал Codex при недоступной `gpt-5.6-luna`;
выбор и review — synthetic Astra xhigh: 0 P1/P2, дополнительный acceptance явного
потока закрыт тестами. Это не human/production acceptance.
[Проверки и границы](../DECISIONOPS_HELP_ENCODING_CHECKS_2026-10-22.md).

## 2026-10-22 — T-70: отказ runtime-настройки не доказывает превышение бюджета

Точный локальный `ProgrammingError` для настройки, переданной ClickHouse runtime command,
даёт факт без counters, checked bounds и exceeded bound. `dwh.cost` называет каждый
объявленный runtime-потолок `cost.not_measured` (review); каталог сохраняется, запрос
без лимитов не повторяется. Превышение времени требует server code 159, байтов чтения —
307 с соответствующим detail; посторонние ошибки сохраняют прежнюю границу отказа.
Версии 98/73. На одноразовом Mac ClickHouse 24.8.14.39 воспроизведено старое ложное
превышение для readonly=1; исправление, успешный readonly=2 и настоящие byte/timeout
превышения проверены. Первичный red: 30 failed, 2 passed; реальные ответы дополнительно
дали 3 failed, 1 passed до уточнения классификатора. Смежный набор: 368 passed;
Ruff, Black, strict mypy Linux/Windows чистые. Реализовал Codex при недоступной
`gpt-5.6-luna`; synthetic Astra xhigh выбрала срез и проверила итог: 0 P1/P2,
не human/production acceptance.
[Проверки и границы](../DECISIONOPS_RUNTIME_SETTINGS_CHECKS_2026-10-22.md).

## 2026-10-22 — T-69: однострочный отказ quoting environment probe

`decision verify --dwh` переводит только локальный `UnsafeIdentifierError` из
`collect` в `VerifyError`: exit 2, пустой stdout, одна строка
`cannot build environment query: unsafe identifier: …`, без evidence. Новый тип
наследует `ValueError`; допустимые имена и `repr` отказа сохранены. Ошибка отделена
от `EnvironmentProbeError`; соединение, каталог и ранние факты отсутствия/нечитаемости
сохраняют порядок. Неожиданные исключения не маскируются. Версии 97/72 сохранены.
Red: 20 failed, 8 passed; green: 42 passed; смежные проверки: 406 passed.
Synthetic Astra xhigh выбрала срез и проверила код, тесты и контракт: 0 P1/P2,
не human/production acceptance. Codex реализовал срез при недоступной `gpt-5.6-luna`.
[Проверки и границы](../DECISIONOPS_ENVIRONMENT_IDENTIFIER_CHECKS_2026-10-22.md).

## 2026-10-22 — T-68: безопасные ошибки загрузки настроек

`decision verify --dwh` и `decision capture` переводят `ValidationError`, `OSError` и
`UnicodeError` только от загрузчика Settings в однострочный usage error, exit 2, без
stdout, DWH-фабрик и выходных файлов. Валидация называет до трёх известных полей и
кодов ошибок, остальные считает; значения, сообщения валидатора и context не
выводятся. I/O и Unicode дают только класс исключения. Capture строит target внутри
существующего `VerifyError` handler. Offline verify, общий загрузчик, v1 и версии
97/72 сохранены. Red: 32 failed, 3 passed; green: 35 passed; смежные проверки:
506 passed, 1 skipped. Synthetic Astra xhigh выбрала срез и проверила код, тесты и
контракт: 0 P1/P2, не human/production acceptance. Codex реализовал срез при
недоступной `gpt-5.6-luna`. [Проверки и границы](../DECISIONOPS_SETTINGS_CHECKS_2026-10-22.md).

## 2026-10-22 — T-67: однострочная ошибка драйвера при capture

`decision capture` преобразует ошибки ClickHouse и PostgreSQL-семейства в строку с
классом исключения, exit 2, без stdout и файла результата. ClickHouse-перехват включает
`StreamFailureError`, metadata fallback и ленивую материализацию `result_rows`; обрыв
после первого блока не публикует частичный результат и не повторяет запрос. Сообщения
драйверов нормализованы, пустое сообщение представлено классом. Успешный путь и версии
verifier 97 / check 72 сохранены. Новые regression-тесты дали 30 red до правки; код,
тесты и контракт прошли synthetic Astra xhigh review: 0 P1/P2, не human/production
acceptance. Реализовал Codex: указанная `gpt-5.6-luna` недоступна. Свежие команды,
результаты, уточнение сетевых тестов для `ConnectionTimeout` и границы —
[проверки T-67](../DECISIONOPS_CAPTURE_DRIVER_CHECKS_2026-10-22.md).

- 2026-10-19 — T-66: Windows-only код за проверкой, которую читает mypy. CI проверяет типы на
  Linux (`mypy auto_bi` и `mypy --strict auto_bi` в джобах `quality` и `quality-py-latest`
  на `ubuntu-latest`); mypy пропускает ветку по `sys.platform`, а `os.name` не читает. Срез
  T-63 достигал Windows-API (`ctypes.WinDLL`, `ctypes.get_last_error`,
  `msvcrt.open_osfhandle`, `os.O_BINARY`) за `existing_mode is not None and os.name == "nt"`:
  с T-63 по T-65 mypy, запущенный как в CI, находил 7 ошибок без `--strict` и 9 со `--strict`
  в `auto_bi/evidence/store.py` на Linux и macOS, 0 на Windows (T-63 и T-64 записали strict
  mypy чистым, T-64 — на Windows). У репозитория нет remote, CI на этих коммитах не
  запускался. Теперь `put` ветвится по `sys.platform == "win32"` левым операндом `and`, а
  `_open_windows_staged` определена под `if sys.platform == "win32":`; тело изменилось только
  отступом и переносом одного вызова, поведение — нет. На рабочем дереве mypy чист на Linux,
  macOS и Windows, со `--strict` и без. Джоб `quality` получил шаг `Mypy strict (Windows
  branches)` — `mypy --platform win32 --strict auto_bi`, иначе Windows-ветку не проверял бы ни
  один шаг CI; он же находит POSIX-only API вне проверки (проба: `os.geteuid()`,
  `fcntl.LOCK_EX`). `tests/test_platform_guards.py` читает исходники `auto_bi` без mypy:
  Windows-only модули (5) и имена `ctypes`, `os`, `subprocess`, `signal` (28), сверенные с
  typeshed через mypy (33 из 33 на Linux и macOS, 0 на Windows), и собственные определения за
  проверкой вне Windows-ветки в понимании mypy (`==`, `startswith`, `!=` и `not`, левый
  операнд `and`/`or`) — находка, как и любое `os.name`. Red на HEAD с новым модулем: 1 failed
  (проверка пакета: 8 находок в `store.py` — `os.name` и Windows-API на строках ошибок mypy),
  49 passed; 16 ручных мутантов `store.py` и чтения проверок убиты на первом прогоне.
  CONTRIBUTING («The gate») и SLO называют платформу CI и `--platform linux` для локального
  прогона на Windows и macOS; `tests/test_docs_as_code.py` держит это рядом с CI. Контроль на
  10 pilot-прогонах: вердикты, run ID и байты bundle совпали с HEAD. Полный offline gate с
  duckdb: 6211 passed, 7 skipped, 38 deselected, exit 0 на 3.13, 3.12 и 3.14; Black и Ruff
  чистые. Версии 97/72 не менялись. Выполнил Claude Opus 5.5 напрямую, без MyFlow и других
  моделей; модельного ревью (Astra xhigh, ADR 0006) не было — только проверки автора, не
  human/production acceptance. Границы: Windows не прогонялся; тест — эвристика над
  перечисленным (не алиасы импорта, не `import *`, не определения глубже верхнего уровня),
  гейт остаётся за mypy.
  [Проверки и границы](../DECISIONOPS_PLATFORM_GUARDS_CHECKS_2026-10-19.md).
- 2026-10-19 — T-65: движок, до которого `decision verify --dwh` не дотянулся. На HEAD проба
  движка соединялась при построении, до вызова use case, а то, что поднимал драйвер, уходило
  наверх как есть. Процессами над закрытым портом loopback и над слушателем, который сразу
  закрывает соединение: 8 прогонов `verify --dwh` из 8 — exit 1 и traceback (154 и 139 строк
  у ClickHouse, первая — предупреждение драйвера через `logging.lastResort`; 23 и 24 у
  PostgreSQL); прогоны с моментом, который не момент, и с файлом results, которого нет,
  открывали сессию и кончались так же. `capture --dwh clickhouse` отвечал двумя строками.
  Теперь клиент создаётся, когда use case спрашивает, — последним, после заявленного и всех
  входов. То, что поднял драйвер (`ClickHouseError`, `StreamFailureError`, `psycopg.Error`),
  проба поднимает как `EnvironmentProbeError` порта, use case — как `VerifyError`: `error:
  cannot ask <engine> at <host>:<port>/<database>: <класс>: <текст в одну строку>`, exit 2,
  без evidence. Отказ движка — факт, как прежде; поднятое не драйвером — как есть. `verify` и
  `capture` с ClickHouse ставят один `NullHandler` на логгер `clickhouse_connect` до импорта
  драйвера. На рабочем дереве из 12 прогонов пробы traceback драйвера — 0: `verify` — одна
  строка в 8 (в 4 — отказ момента или файла без сессии), `capture --dwh clickhouse` — одна;
  остаются две строки libpq у `capture --dwh postgres` и traceback порта, который не число.
  Версии 97/72 не менялись. `tests/test_unanswered_engine.py`: 45 тестов, red на HEAD
  37 failed / 8 passed; 16 ручных мутантов — 15 убиты на первом прогоне, выживший («у psycopg
  только `OperationalError`») убит тестом с `DataCorrupted`. Контроль на 10 pilot-прогонах:
  вердикты, run ID и байты bundle совпали с HEAD. Полный offline gate с duckdb: 6186 passed,
  7 skipped, 38 deselected, exit 0 на 3.13, 3.12 и 3.14.
  Black/Ruff чистые; strict mypy на macOS — 9 ошибок Windows-API `store.py` среза T-63, те же
  на HEAD; с `--platform win32` чисто. Выполнил Claude Opus 5.5 напрямую, без MyFlow и других
  моделей; модельного ревью (Astra xhigh, ADR 0006) не было — только проверки автора, не
  human/production acceptance. Границы: сбой самой пробы и нечитаемые настройки — traceback;
  ClickHouse `readonly=1` — usage error (по коду драйвера); `capture` печатает отказ psycopg
  двумя строками и не ловит `StreamFailureError`; живой движок и Windows не прогонялись.
  [Проверки и границы](../DECISIONOPS_UNANSWERED_ENGINE_CHECKS_2026-10-19.md).
- 2026-10-08 — T-64: `NativeModelSource.snapshot` больше не заменяет ложное YAML-значение
  на `{}`. До исправления base принимал пустой `model.yaml` как пустую модель, candidate
  получал отказ при повторном чтении, а capture доходил до query factory. Теперь документ
  без mapping отвергается до gate, probe/query и output одинаково во всех трёх путях:
  exit 2 и одна строка `<path> is not a valid semantic model: <root>: …`. Явный `{}` допустим.
  Synthetic Astra xhigh выбрала срез по ADR 0006; Luna xhigh выполнила однострочную правку
  и тесты. Red: 40 failed, 1 passed; green после финального уточнения тестов: 41 passed.
  Codex независимо проверил восемь связанных модулей на Windows/Python 3.13.7:
  553 passed, 1 skipped, exit 0. Ruff, Black и strict mypy (122 source files) чистые.
  Контроль до/после: обычная mapping, `{}` в base и `{}` в candidate; snapshots, verdict
  и все 44 файла трёх bundles идентичны. Версии verifier 97, check 72 и native source 3
  сохранены. Финальное synthetic Astra xhigh review: 0 P1/P2; не human/production acceptance.
  Полный offline suite и живые движки не запускались. v1 `SemanticModel.load` и asset loader
  остаются за границей среза. [Контракт](../specs/unreadable-inputs.md),
  [план и команды проверки](../../empty-model-consistency.md).

- 2026-10-07 — T-63: файловый `ArtifactStore` прежде открывал target напрямую, поэтому
  отказ во время записи manifest оставлял частичный файл и блокировал повтор. Теперь каждый
  payload пишется в уникальный соседний staging и публикуется одним `os.replace`. Отказ записи
  или публикации сохраняет прежние байты, а отсутствующий target не появляется; staging
  удаляется при успешном cleanup, затем возможен повтор. Mode bits существующего
  файла переносятся до публикации; на Windows staging для существующего target создаётся
  через `CreateFileW(CREATE_NEW)` с owner/DACL target в `SECURITY_ATTRIBUTES`. Read-only target
  получает отказ без изменения байтов и без read-only staging. `ArtifactRefusalError.refusal`
  несёт исходный `OSError`.
  Red-тесты на Mac воспроизвели частичный новый и существующий manifest и отказ публикации;
  отдельные тесты закрепили mode `0600` при записи и сохранение `0600`/`0640`. Synthetic
  Astra xhigh review выявило Windows-окно между созданием staging и переносом DACL: ранний
  read-handle сохранил доступ к новому payload. Windows red-тест воспроизвёл утечку, green-тест
  подтвердил отказ раннему reader; финальное synthetic review не нашло P1/P2. Связанные
  модули: 238 passed, 3 skipped под Python 3.12, 3.13 и 3.14 на Mac; Windows-модуль:
  25 passed, 4 skipped. Полный offline gate 3.13 с DuckDB:
  6101 passed, 7 skipped, 38 deselected, exit 0; Black, Ruff, package-wide strict mypy чистые.
  `VERIFIER_VERSION=97` и `CHECK_VERSION=72` не менялись. Гарантия относится к одному файлу,
  не к bundle; parent dirs, гонки, прерывание, отказ cleanup, прочие metadata и power-loss
  durability остаются вне неё.
  Synthetic review не является human/production acceptance.
  [Контракт](../specs/unreadable-inputs.md).

- 2026-10-07 — T-62: `decision investigate` писал четыре файла прямо в новый output.
  Отказ третьей записи оставлял каталог с `plan.json` и `results.json`; повторный запуск
  получал `output directory already exists`. Теперь payload вычисляется до filesystem
  mutation, четыре файла пишутся в уникальный соседний временный каталог и публикуются
  одним `os.rename`. При обычном отказе записи или публикации временный каталог удаляется,
  существующий output не очищается; наличие output, включая dangling symlink, проверяется
  до staging и перед публикацией. CLI отвечает кодом 2 без success-строк. Существующие
  байты артефактов, digest и успешный вывод сохранены. Версии evidence 97/72 не менялись.
  Тесты сначала дали red для отказов каждого из четырёх файлов после частичной записи,
  ошибки rename, преждевременной видимости и output, созданного при последней записи.
  Synthetic Astra xhigh review нашло P2: prefix с полным именем output делал staging
  длиннее `NAME_MAX`. Регрессия с допустимым именем в 255 байт дала red на Mac; короткий
  постоянный prefix вернул green. Иных конкретных P1/P2 замечаний к коду review не дало.
  Целевые модули: 127 passed под Python 3.12, 3.13 и 3.14. Полный offline gate с duckdb
  под 3.13: 6094 passed, 4 skipped, 38 deselected, exit 0; Black, Ruff, strict mypy чистые.
  Проба на Mac подтвердила, что POSIX rename заменяет пустой destination. Это остаётся
  гонкой после последней проверки; конкурентное изменение родителей также не закрыто.
  Каталог теперь получает права `0700` с учётом umask; отказ самой очистки, прерывание
  процесса и power-loss durability — отдельные границы. Synthetic review не является
  human/design-partner outcome или production acceptance.
  [Контракт](../specs/unreadable-inputs.md).

- 2026-10-05 — то, что файловая система не показала читателю evidence, читалось как то, чего
  нет: у store был один класс ошибки на отказ и на отсутствие, а взгляды store и `decision
  evidence` шли через `pathlib`. Командами как процессами на HEAD над одним bundle из 15
  артефактов в 19 состояниях (каталоги bundle и над ним с mode 000, 0o300, 0o400; manifest и
  проверка с mode 000; каталог рядом с прогоном; каталог на месте проверки; проверки нет),
  шестью способами чтения — 114 прогонов `decision evidence` на интерпретатор: под 3.12 и
  3.13 traceback `PermissionError` в 42; под 3.14 — в 8, а 31 ответ говорит о лежащем на
  месте bundle, что его нет (`evidence path not found`, `no evidence bundle under …`, `run …
  has no manifest.json`). Под всеми тремя каталог проверок без прав — `evidence.artifact_missing`
  на каждую проверку, `block`, exit 4; `decision verify` под umask, отнимающим чтение у
  записанного, сообщал `evidence.invalid` и exit 4 о bundle, записанном целиком.
  Теперь отказ — не отсутствие. Взгляды store и `decision evidence` — взгляды системы
  (`auto_bi/filesystem.py`, взгляд на symlink в их числе). Store отличает отказ классом:
  `ArtifactRefusalError` держит поднятый системой `OSError` (`refusal`), а собственный ответ
  store — артефакта нет, symlink, путь вне прогона — остаётся `ArtifactStoreError`; артефакт —
  обычный файл, и путь с каталогом или каналом не открывается. `verify_bundle` оценивает
  только отсутствие, отказ поднимает. `decision evidence` на отказ отвечает `error: cannot
  read <путь>: <класс>`, exit 2, без вердикта — текстом, с `--json` и с `--artifact`; каталог
  под evidence-путём, в который нельзя заглянуть, не пропускается (`…; name the run to read
  with --run`). Гейт, которому отказано в повторном чтении записанного bundle, поднимает
  `EvidenceReadBackError`: `error: cannot read back the evidence bundle under <каталог>: …`,
  exit 2, bundle остаётся записанным. Путь с LF/CR печатается через `repr`, так что отказ
  остаётся одной строкой. На рабочем дереве из 114 прогонов 36 читают bundle, 70 — usage
  error в одну строку, 8 — `block` за то, чего нет, traceback — 0, одинаково под тремя
  интерпретаторами. Версии evidence не менялись (97/72):
  manifest пробного прогона на обоих деревьях — один sha256.
  Не закрыто: отказ кончает чтение целиком — частичной инспекции нет, назван первый путь;
  bundle, который гейт не смог перечитать, был `block` и кодом 4, а стал кодом 2 при целом
  bundle на диске; каталог, положенный на место артефакта между взглядом и чтением, и
  evidence-путь, исчезнувший перед перечислением, отвечают классом системы и не
  воспроизводились; store другого вида оценивается по тому, что поднимает; `decision
  investigate` спрашивает `pathlib` о выходном каталоге; root ни в чём не отказано; на
  Windows ничего не прогонялось.
  Выбор — оркестратор (граница среза T-52, Astra-выбора по ADR 0006 нет). Основной срез
  выполнил Claude Opus 5.5; synthetic Astra xhigh review нашло P2: LF/CR в пути делал отказ
  многострочным. Codex добавил 8 вариантов и исправил вывод; 8 failed → 8 passed. Других
  конкретных ошибок ревью не нашло; это не human/production acceptance. Тесты шли на Mac.
  Red на HEAD до восьми LF/CR вариантов с двумя тестовыми модулями рабочего дерева:
  112 failed / 217 passed под 3.13 и под 3.14; новый модуль: 156 passed. Мутанты до
  правки LF/CR под 3.13 — 88, все выявлены; восемь возвратов к взгляду `pathlib` под 3.14
  без теста, читающего исходники, — все выявлены. Контроль на tracked pilot-пакетах:
  10 прогонов; вердикты, проверки, run ID и каждый байт bundle совпали с HEAD.
  Полный offline gate с duckdb: 6085 passed, 4 skipped, 38 deselected, exit 0 на 3.13,
  те же числа и exit 0 на 3.12 и 3.14; Black/Ruff/strict mypy чистые.
  [Проверки и границы](../DECISIONOPS_REFUSED_EVIDENCE_CHECKS_2026-10-05.md).
- 2026-10-05 — decision-команда печатала прямо в поток, и символ ответа, которого кодировка
  потока не знает, кончал её `UnicodeEncodeError`: traceback и exit 1 — после вынесенного
  вердикта, записанного bundle или записанных файлов. Командами как процессами на HEAD, с
  потоками ASCII, Latin-1, cp1252, cp1251 и UTF-8 (`PYTHONIOENCODING`) над фикстурой в
  каталоге с кириллическим именем — 30 прогонов на кодировку, 150 всего: 36 исключений (14
  под ASCII, 12 под Latin-1, 10 под cp1252). `decision validate` падал, как только issue
  называла файл, и его `--json` не печатал документа; `decision verify` под ASCII падал на
  detail со знаком параграфа, уже записав bundle; `decision investigate` и `decision capture`
  — на строке `wrote <путь>`, уже записав файлы; `decision evidence --artifact` — в 20 из 45
  прогонов; `auto_bi --help` — в двух. Ещё 8 прогонов в процессе на строгом UTF-8 с одиночным
  суррогатом в пути: 5 исключений, 4 из них — печать отчёта `decision validate`.
  Теперь всё, что пять команд печатают, идёт через одну функцию. Строка предлагается потоку
  как есть, а при отказе печатается с backslash-escape на месте символа, которого поток не
  кодирует (так интерпретатор пишет в свой stderr); JSON-ответ при отказе печатается целиком
  в ASCII с escape самого JSON — читатель JSON получает тот же документ; payload артефакта
  печатается как есть или не печатается вовсе: exit 2 и строка `error: <artifact id> holds
  text the output stream cannot encode (<encoding>); it is stored at <path>`. Exit code и
  записанное командой — те же, что на потоке, который кодирует всё. На рабочем дереве из 150
  прогонов 128 отвечают и кончаются вердиктом или usage error, 20 — отказ печатать payload,
  2 — `auto_bi --help`; из 8 с суррогатом исключение осталось одно. Версии evidence не
  менялись (97/72): ничто запечатанное не изменилось.
  Не закрыто: argparse печатает сам, и `auto_bi --help` с тире в help-тексте кончается
  traceback под ASCII и Latin-1; v1-команды печатают как прежде; escape строки не отличим от
  тех же символов в самой строке; поток, отказавший не из-за символа (закрытый pipe, полный
  диск), не покрыт; `decision investigate` над планом с U+D800 в пути кончается
  `UnicodeEncodeError` там, где план открывается (в процессе: в командной строке POSIX такой
  путь не передать); на Windows ничего не прогонялось.
  Выбор — оркестратор (граница срезов T-50, T-53 и T-55, Astra-выбора по ADR 0006 нет). По
  решению владельца срез выполнен напрямую Claude Opus 5.5, без MyFlow-исполнителей; модельное
  ревью по ADR 0006 (Astra xhigh) не проводилось — проверки сделал сам автор, это не
  независимое ревью. Тесты прогонялись на Mac. Red на HEAD с тестовым модулем рабочего
  дерева: 81 failed / 38 passed; новый модуль: 119 passed на 3.12, 3.13 и 3.14. Ручные
  мутанты — 55, убиты все; 13 из них убиты и без теста, читающего исходник пяти команд.
  Контроль на tracked pilot-пакетах: 10 прогонов, каждый файл каждого bundle байт в байт как
  на HEAD. Полный offline gate с duckdb: 5929 passed, 4 skipped, 38 deselected, exit 0 на
  3.13, столько же passed на 3.12 и 3.14; Black/Ruff/strict mypy чистые.
  [Проверки и границы](../DECISIONOPS_UNENCODABLE_OUTPUT_CHECKS_2026-10-05.md).
- 2026-10-05 — момент и окружение, которые заявляет командная строка, прежде не проверялись
  до использования. `decision verify --created-at`, который manifest не берёт, кончал прогон
  исключением, когда все входы уже загружены и все проверки оценены; `decision capture
  --captured-at`, который не берёт attestation, отвергался четырьмя строками pydantic и
  только после соединения с движком и исполнения reviewed-запроса; окружение с одиночным
  суррогатом кончало `decision verify` `CanonicalizationError`, а `decision capture`
  аттестовал его в файл, который `decision verify` не читает. Вызовом команд в процессе на
  HEAD (16 моментов и 8 окружений у capture и у verify над прогоном с recorded result и без
  assets, три прогона capture с настроенными окружением и пользователем — 75 прогонов):
  32 исключения `decision verify`, которые никто не ловит (`ValueError`, `TypeError`,
  `ValidationError`, `UnicodeEncodeError`, `CanonicalizationError`), 14 отказов `decision
  capture` в четыре строки после запроса и 4 записанных файла с суррогатом. Теперь use case
  проверяет заявленное первым — до взгляда на путь, чтения, соединения и записи: момент —
  `Timestamp` manifest и attestation, иначе `--created-at must be an RFC 3339 UTC timestamp
  ending in Z` (у capture — `--captured-at …`); окружение и настроенный пользователь capture
  — текст без одиночного суррогата, иначе `… must be text without a lone surrogate`. Отказ
  не цитирует значение, назван один: момент раньше окружения, окружение раньше пользователя.
  Exit 2, одна строка на stderr, ничего не прочитано, не спрошено и не записано.
  `result_probe.capture` строит attestation до запроса. Любой другой текст окружения
  (пробелы, перевод строки, NUL) берётся как передан; флаг, переданный пустым, — флаг,
  который не передан. На рабочем дереве те же 75 прогонов: 50 — exit 2 с одной строкой,
  25 — как на HEAD; исключений нет. Версии evidence не менялись (97/72): ни один запрос,
  который команды принимали, не получает другого ответа.
  Не закрыто: `Timestamp` шире RFC 3339 — момент без секунд, без разделителей, неделей и
  днём, с запятой перед долями берётся и запечатывается как передан (проба: 10 прогонов, как
  на HEAD); `decision verify --dwh` соединяется с движком до use case, и движок на закрытом
  порту кончает команду traceback в 154 строки (ClickHouse) или 23 (PostgreSQL) и exit 1 —
  проба: 8 прогонов на каждом дереве; `decision capture` об отказе соединения отвечает двумя
  строками; схема `ResultAttestation` суррогат в окружении по-прежнему берёт.
  Выбор — оркестратор (граница срезов T-54 и T-55, Astra-выбора по ADR 0006 нет). По решению
  владельца срез выполнен напрямую Claude Opus 5.5, без MyFlow-исполнителей; модельное
  ревью по ADR 0006 (Astra xhigh) не проводилось — проверки сделал сам автор, это не
  независимое ревью. Тесты прогонялись на Mac. Red на HEAD с тестовым модулем рабочего
  дерева: 106 failed / 31 passed; новый модуль: 137 passed на 3.12, 3.13 и 3.14. Ручные
  мутанты — 40, убиты все (на первом прогоне один выжил и не был эквивалентен — добавлен
  тест). Контроль на tracked pilot-пакетах: 10 прогонов, каждый файл каждого bundle байт в
  байт как на HEAD. Полный offline gate с duckdb: 5810 passed, 4 skipped, 38 deselected,
  exit 0 на 3.13, столько же passed на 3.12 и 3.14; Black/Ruff/strict mypy чистые.
  [Проверки и границы](../DECISIONOPS_STATED_RUN_FACTS_CHECKS_2026-10-05.md).
- 2026-10-05 — таблица, колонка или join, которые `model.yaml` объявляет больше одного раза,
  прежде давали canonical graph по узлу на объявление: схема модели (v1) берёт имя таблицы,
  имя колонки и концы join'а сколько угодно раз. Читатели такого графа отвечали врозь:
  semantic diff индексирует узлы по id и берёт последний, гейт и v1-модель читают первый,
  digest считается по всем. Вызовом команды в процессе на HEAD (14 видов повтора — таблица,
  колонка, join, слово в слово или иначе; новое объявление первым или последним; в
  candidate, в base, в обоих — 84 прогона): повтор и расхождение не называла ни одна
  проверка (`semantic.gaps` — `semantic.representable` в 78 прогонах). Повтор слово в слово
  в одном из двух ref (24 прогона) менял canonical digest, а `semantic.diff` отвечал
  `semantic.unchanged`. Из 48 прогонов с расходящимися объявлениями 32 — exit 0
  (`pass_with_advisory`), 4 — exit 3, 12 — exit 4: десять блокировал `bi.contract` (колонка
  витрины дважды или первое объявление без колонки), два — `impact.products` по последнему
  объявлению. В 18 из 32 прогонов, где расходится один ref, diff отвечал
  `semantic.unchanged`.
  Теперь граф держит один узел на id таблицы, поля и join'а, и `SemanticGraph` с двумя
  узлами одного id не строится. Объявления, которые дают графу одно и то же, — одно
  объявление: узел один, canonical digest — digest модели, объявляющей вещь один раз,
  snapshot несёт advisory `gap.table_repeated`, `gap.field_repeated` или
  `gap.join_repeated`. Объявления, которые расходятся, узла не дают: `gap.table_conflict`,
  `gap.field_conflict` или `gap.join_conflict` (block) по адресу `tables.<имя>`,
  `fields.<таблица>.<колонка>`, `joins.<left>-><right>`. Таблица одинакова, когда одинаковы
  её узел, сам факт physical facts и узлы её колонок с их числом; порядок колонок, synonyms
  и статистика объявлений не различают. Таблица в конфликте дальше не читается, и digest —
  digest модели без неё; поле в конфликте — не объявленное поле для join'ов и метрик;
  dangling join, объявленный дважды одинаково, — один `gap.dangling_join`. `semantic.gaps`
  — advisory на повторе и block на конфликте, snapshots сравниваются в обоих случаях.
  На рабочем дереве те же 84 прогона: 48 с расходящимися объявлениями — exit 4, блокирует
  `semantic.gaps`; 36 с повтором слово в слово — exit code как на HEAD (24 — exit 0, 12 —
  exit 4 с `bi.compile_refused`), `semantic.gaps` называет повтор advisory, digest ref'а с
  повтором равен digest ref'а без него; исключений нет ни на одном дереве.
  `VERIFIER_VERSION` 96 → 97, `CHECK_VERSION` 71 → 72: прогон над ref'ом с повторным
  объявлением давал bundle и даёт другой; historical bundles не переписаны.
  Не закрыто: два metric-контракта одного id в ref — по-прежнему два узла метрики (diff
  берёт последний, `snapshot.metric` — первый); raw-SQL метрика, объявленная дважды, — два
  gap по одному адресу; что говорят расходящиеся объявления, gap не хранит, и два ref с
  одним конфликтом «по-разному» в нём не различаются; колонка витрины, объявленная дважды
  слово в слово, — advisory в snapshot и по-прежнему `bi.compile_refused` на Superset;
  `decision capture` gaps не грейдит (проба: 28 прогонов из 28 пишут файл, как на HEAD).
  Выбор — оркестратор (граница среза T-56, Astra-выбора по ADR 0006 нет). По решению
  владельца срез выполнен напрямую Claude Opus 5.5, без MyFlow-исполнителей; модельное
  ревью по ADR 0006 (Astra xhigh) не проводилось — проверки сделал сам автор, это не
  независимое ревью. Тесты прогонялись на Mac. Red на HEAD с тестовым модулем рабочего
  дерева: 220 failed / 3 passed; новый модуль: 223 passed на 3.12, 3.13 и 3.14.
  Ручные мутанты — 74, убиты все (на первом прогоне из 73 один выжил и не был эквивалентен
  — добавлены тест и парный мутант). Контроль на tracked pilot-пакетах: 10 прогонов,
  вердикт и каждая проверка как на HEAD. Полный offline gate с duckdb: 5673 passed,
  4 skipped, 38 deselected, exit 0 на 3.13, столько же passed на 3.12 и 3.14;
  Black/Ruff/strict mypy чистые.
  [Проверки и границы](../DECISIONOPS_REPEATED_DECLARATIONS_CHECKS_2026-10-05.md).

- 2026-10-05 — поставляемый дашборд, чей spec прошёл валидатор, а компиляция отвергла,
  прежде кончал `decision verify` traceback: `bi.contract` компилирует теми же builders, что
  build path, а они говорят «не строю» исключением, которое гейт не ловил. Вызовом команды в
  процессе на HEAD (184 прогона: отказы планировщика, значения фильтров на трёх видах
  чартов, пустые имена, движки под DataLens, обход видов чартов на обеих BI): 32 исключения,
  которые никто не ловит, — 15 `CanonicalizationError` (значение фильтра `±inf`, `nan`,
  целое `10**400`), 5 `SourceAliasCollisionError` (колонка витрины дважды; колонка
  `stores_id` рядом с joined label `dm.stores.id`; два joined label одного алиаса),
  6 `ValidationError` (пустой title дашборда или id чарта), 6 `ValueError` (список там, где
  оператор берёт одно значение; DataLens над движком без source type); отчёта и bundle нет.
  Теперь `ValueError` компиляции — отказ: дашборд записан в evidence `bi.contract` в список
  `refused` с ref и одной строкой (класс и слова компиляции, пробельные серии сжаты; у
  отказа схемы — имя модели и первые три отказа без значений), payload'ов у него нет,
  проверка — block с `bi.compile_refused` (`bi.spec_invalid`, если рядом невалидный spec),
  detail называет refs после невалидных и перед отсутствующими. Список `refused` запечатан
  всегда, пустой тоже. Остальные дашборды, включая стоящие после отвергнутого, компилируются
  как прежде; исключение, которое не `ValueError`, не ловится. На рабочем дереве те же
  184 прогона: 32 — exit 4 с `bi.compile_refused`, остальные 152 — как на HEAD;
  исключений нет. Валидатор, builders и схема `DashboardSpec` не тронуты.
  `VERIFIER_VERSION` 95 → 96, `CHECK_VERSION` 70 → 71: evidence `bi.contract` каждого
  прогона несёт новый ключ; historical bundles не переписаны.
  Не закрыто: исключение компиляции, которое не `ValueError`, кончает прогон как прежде;
  строка отказа builder'а не обрезается и может цитировать значение фильтра; detail и
  `summary.md` называют только refs, причина — в `checks/check-bi.contract.json`; build path
  на таком spec'е кончается как прежде (v1 не тронут); колонка, объявленная моделью дважды,
  блокирует Superset-дашборд, а сам повтор snapshot не называет.
  Выбор — оркестратор (исключение найдено пробой автора, Astra-выбора по ADR 0006 нет). По
  решению владельца срез выполнен напрямую Claude Opus 5.5, без MyFlow-исполнителей;
  модельное ревью по ADR 0006 (Astra xhigh) не проводилось — проверки сделал сам автор, это
  не независимое ревью. Тесты прогонялись на Mac. Red на HEAD с двумя тестовыми модулями
  рабочего дерева: 80 failed / 3 passed; на копии HEAD с одним ключом
  `refused`, всегда пустым: 51 failed / 32 passed; два модуля: 83
  passed на 3.12, 3.13 и 3.14. Ручные мутанты — 32, убиты все. Контроль на
  tracked pilot-пакетах: 10 прогонов, вердикт и каждая проверка как на HEAD. Полный offline
  gate с duckdb: 5450 passed, 4 skipped, 38 deselected, exit 0 на 3.13, столько же
  passed на 3.12 и 3.14; Black/Ruff/strict mypy чистые.
  [Проверки и границы](../DECISIONOPS_BI_COMPILE_REFUSAL_CHECKS_2026-10-05.md).

- 2026-10-05 — таблица, которую `model.yaml` объявляет с пустым именем, прежде входила в
  canonical graph как таблица с пустым id и полями `.<колонка>`: схема модели (v1) берёт
  для имени любой текст. Два ref, которые держали её одинаково, оценивались как с таблицей;
  два ref, которые в ней расходились, кончали `decision verify` `ValidationError` при
  сравнении snapshots: изменение называется id того, что изменилось, а имени у него нет.
  Вызовом команды в процессе на HEAD (первая или вторая таблица фикстуры без имени в base,
  в candidate, в обоих одинаково, в обоих с разным описанием; ещё одна таблица без имени в
  base, в candidate, в обоих — 11 прогонов): 8 исключений, которые никто не ловит, 2 прогона
  с exit 4 и один с exit 0 (`pass_with_advisory`). Теперь такая таблица — gap
  `gap.table_unnamed` (block) по адресу `tables.<индекс>.name`, где индекс — место таблицы в
  `tables` модели; ни её, ни её колонок в графе нет, и canonical digest — digest той же
  модели без неё. Остальное, что она объявляет, не читается (нет и `gap.missing_physical`);
  join к её колонке — `gap.dangling_join` по прежнему правилу; `TableNode.id` не бывает
  пустым текстом. `semantic.gaps` блокирует, когда gap несёт base или candidate, snapshots
  по-прежнему сравниваются: gap одного из них — `gap.appeared` (block) или `gap.resolved`
  (advisory). На рабочем дереве те же 11 прогонов: exit 4, `block`, исключений нет.
  `VERIFIER_VERSION` 94 → 95, `CHECK_VERSION` 69 → 70: прогон над двумя ref с такой таблицей
  давал bundle и даёт другой; historical bundles не переписаны.
  Не закрыто: имя из одних пробелов — имя; колонка с пустым именем остаётся полем
  `<таблица>.` (проба: 3 прогона оценены, как на HEAD); таблица, колонка или join,
  объявленные дважды, не названы; `decision capture` gaps не грейдит и над candidate с такой
  таблицей исполняет запрос (проба: 3 прогона из 3 пишут файл); схема v1 по-прежнему
  принимает таблицу без имени.
  Выбор — оркестратор (граница среза T-55, Astra-выбора по ADR 0006 нет). По решению
  владельца срез выполнен напрямую Claude Opus 5.5, без MyFlow-исполнителей; модельное
  ревью по ADR 0006 (Astra xhigh) не проводилось — проверки сделал сам автор, это не
  независимое ревью. Тесты прогонялись на Mac. Red на HEAD с тестовым модулем рабочего
  дерева: 31 failed / 4 passed; новый модуль: 35 passed на 3.12,
  3.13 и 3.14. Ручные мутанты — 21, убиты все. Контроль на tracked pilot-пакетах:
  10 прогонов, вердикт и каждая проверка как на HEAD. Полный offline gate с duckdb:
  5387 passed, 4 skipped, 38 deselected, exit 0 на 3.13, столько же passed на 3.12
  и 3.14; Black/Ruff/strict mypy чистые.
  [Проверки и границы](../DECISIONOPS_UNNAMED_TABLE_CHECKS_2026-10-05.md).

- 2026-10-05 — YAML-файл, чей double-quoted scalar называет escape-последовательностью
  суррогат (U+D800–U+DFFF), прежде читался: сканер PyYAML строит из такого escape строку, у
  которой нет ни UTF-8-формы, ни формы в canonical JSON, и дальше всё решал следующий
  читатель. Вызовом команд в процессе на HEAD, escape на месте каждой строки и каждого ключа
  файлов фикстуры (666 мест, 1332 документа, 1934 прогона): 50 исключений, которые никто не
  ловит (`decision validate` — 12 `UnicodeEncodeError` при печати отчёта; `decision verify`
  — 18 `CanonicalizationError` и 20 `ValidationError`), 4 прогона `decision validate` с
  exit 0 (metric contract с escape на месте ключа grain прошёл), 464 оценённых прогона,
  124 прогона `decision capture` до движка, 1292 отказа с exit 2 без слова об escape. Теперь
  общий загрузчик decision-команд (`auto_bi/yaml_document.py`) отвергает такой документ до
  построения: `SurrogateEscapeError` (`yaml.MarkedYAMLError`) с позицией начала скаляра.
  Отвергается каждый код суррогата, названный escape в 16 или 32 бита, в значении, ключе,
  элементе и скаляре-документе; пара, которую JSON читает как один символ, — тоже (загрузчик
  ничего не склеивает); назван первый по порядку документа; узел под alias просмотрен один
  раз. Текст, который PyYAML отвергает сам, отвергается как прежде; буквы escape без замены
  (одинарные кавычки, plain и block scalar, комментарий) и символ вне BMP читаются. Читатели
  отвечают как на любой текст, отвергнутый PyYAML: `contract.unreadable` (`cannot read
  contract: SurrogateEscapeError`) под validated paths, gap `gap.contract_invalid` в ref,
  usage error `cannot read <файл>: SurrogateEscapeError at line <n>, column <m>` (exit 2, до
  грейда, движка и записи) для `model.yaml`, spec, asset, results и входов `decision
  investigate`. На рабочем дереве те же 1934 прогона: 1252 — exit 2 с одной строкой об
  отказе, 82 — `decision validate` печатает отчёт с ним и не находит продукта, 480 — exit 4
  с файлом, названным нечитаемым, 120 — `decision capture` идёт к движку с непрочитанным
  metric contract candidate ref; исключений нет. `VERIFIER_VERSION` 93 → 94, `CHECK_VERSION`
  68 → 69: прогон над таким файлом давал bundle и даёт другой или никакого; historical
  bundles не переписаны.
  Не закрыто: JSON-читатели и v1-загрузчики строят такую строку по-прежнему (`decision
  evidence` над manifest с escape отвечает `evidence.manifest_invalid`, exit 4 — проба);
  `decision capture` берёт `--environment` с суррогатом как передан и пишет файл, который
  `decision verify` теперь отвергает одной строкой; таблица модели с пустым именем кончает
  `decision verify` `ValidationError` при сравнении snapshots (4 прогона побочной пробы из
  1904); recorded result под ключом, который не id ни одного asset, читается и не
  спрашивается; печать в поток, который не кодирует символ, кончает команду traceback.
  Выбор — оркестратор (граница срезов T-50, T-53 и T-54, Astra-выбора по ADR 0006 нет). По
  решению владельца срез выполнен напрямую Claude Opus 5.5, без MyFlow-исполнителей;
  модельное ревью по ADR 0006 (Astra xhigh) не проводилось — проверки сделал сам автор, это
  не независимое ревью. Тесты прогонялись на Mac. Red на копии HEAD с шестью тестовыми
  модулями рабочего дерева и `yaml_document.py`, где новые имена есть, а загрузчик прежний:
  76 failed / 576 passed; шесть модулей: 652 passed на 3.12, 3.13 и 3.14. Ручные мутанты —
  21, убиты все. Контроль на tracked pilot-пакетах: 10 прогонов, вердикт и каждая проверка
  как на HEAD. Полный offline gate с duckdb: 5352 passed, 4 skipped, 38 deselected, exit 0
  на 3.13, столько же passed на 3.12 и 3.14; Black/Ruff/strict mypy чистые.
  [Проверки и границы](../DECISIONOPS_SURROGATE_ESCAPES_CHECKS_2026-10-05.md).

- 2026-10-05 — вход `decision verify` и `decision capture`, который прочитан и не принят
  своей схемой, прежде назывался, а дальше печатался перечнем pydantic: строка счёта и на
  каждую ошибку — место, причина со значением и ссылка. Процессные прогоны на HEAD (прогон,
  который проходит, и одна порча одного файла): из 70 — 61 с exit 2 и 3–22 строками на
  stderr (42 из 50 у `verify`, 19 из 20 у `capture`); results file не говорил, какой из его
  результатов отвергнут. Теперь после прежних слов о файле (`<файл> is not a valid semantic
  model: `, `… DashboardSpec: `, `… verified asset: `) идёт одна строка `schema_reason`:
  первые три отказа схемы как `<место>: <причина>` без значений и ссылок, затем
  `(+<n> more)`. Recorded result назван ключом файла — `<файл> holds an invalid recorded
  result for <ключ>: …`, ключ литералом Python, первый отвергнутый результат кончает чтение.
  Отказ схемы, под которую не написан файл ref, сказан о ref с её именем: `cannot load
  semantic ref <ref>: not a valid <схема>: …`. `ValueError`, который не отказ схемы, сказан
  своим текстом в одну строку или классом, когда текста нет. `VerifiedAssetFileError` несёт
  эту строку, `ValidationError` схемы — его причина. Exit 2, одна строка на stderr, ничего
  не записано и не спрошено. Версии evidence не менялись (93/68): меняется только текст
  usage error.
  Не закрыто: одиночный суррогат как значение spec, asset или results там, где схема его
  принимает, кончает `decision verify` исключением (проба мест: 22 прогона из 1200, как на
  HEAD — `CanonicalizationError` и `ValidationError` для `ResultDifference`); `decision
  capture` печатает отказ `--captured-at` четырьмя строками pydantic и только после запроса
  к движку, а `--environment` с одиночным суррогатом аттестует как передан; ключ recorded
  result при чтении не проверяется.
  Выбор — оркестратор (граница среза T-53, Astra-выбора по ADR 0006 нет). По решению
  владельца срез выполнен напрямую Claude Opus 5.5, без MyFlow-исполнителей; модельное
  ревью по ADR 0006 (Astra xhigh) не проводилось — проверки сделал сам автор, это не
  независимое ревью. Тесты прогонялись на Mac. Red на HEAD с двумя тестовыми модулями
  рабочего дерева: 87 failed / 169 passed; на рабочем дереве 256 passed на 3.12, 3.13 и
  3.14. Ручные мутанты — 27, убиты все. Процессная проба на рабочем дереве: 69 прогонов из
  70 — exit 2 с одной строкой, один — exit 4 (пустой `model.yaml` base оценён, как прежде).
  Контроль на tracked pilot-пакетах: 10 прогонов, каждый файл каждого bundle байт в байт как
  на HEAD. Полный offline gate с duckdb: 5258 passed, 4 skipped, 38 deselected, exit 0 на
  3.13, столько же passed на 3.12 и 3.14; Black/Ruff/strict mypy чистые.
  [Проверки и границы](../DECISIONOPS_RUN_INPUT_SCHEMA_REFUSALS_CHECKS_2026-10-05.md).

- 2026-10-05 — вход `decision investigate`, который команда не может прочитать или принять,
  прежде кончал её traceback или отказом в несколько строк без имени файла: оба вида входа
  читались под одним обработчиком с ответом `input contract is unreadable: ` и текстом того,
  что брошено. Процессные прогоны на HEAD (план и результат из фикстур, одна порча за
  прогон): из 54 — 22 traceback (документ, который PyYAML не может построить, и байты не в
  UTF-8 — по 9 на план и результат; значение в 5000 цифр без кавычек; `unit` с одиночным
  суррогатом — `CanonicalizationError`; имя dimension строки с ним — `UnicodeEncodeError`
  внутри схемы) и 18 с exit 2 и 3–25 строками pydantic или PyYAML, где файл не назван, — с
  двумя результатами неизвестно, о каком речь. Теперь каждый вход отвечает своим путём, план
  читается первым, результаты — в порядке передачи. Файл, который не открывается, не UTF-8,
  отвергнут PyYAML или не построен, — `cannot read <путь>: <причина>` (причина как у входов
  прогона гейта). Файл, который прочитан и не принят схемой, — `<путь> is not a valid
  InvestigationPlan|CanonicalMetricResult: <место>: <причина>; …`: первые три отказа схемы
  без значений и ссылок, затем `(+<n> more)`; ключ из пустого текста — `""`, корень —
  `<root>`, пробельные серии сжаты, строка одна. Exit 2, stdout пуст, каталог вывода не
  создан. `from_yaml` обеих моделей строит документ общим загрузчиком. Схема результата
  отвергает одиночный суррогат в `unit` и в имени dimension строки, не цитируя значение;
  любой другой `unit` принимается как есть. Версии evidence, skills и плана не менялись
  (93/68): принятые входы дают те же байты — 44 файла 11 вариантов байт в байт как на HEAD.
  Не закрыто: `decision verify` и `decision capture` печатают отказ схемы модели, spec,
  asset и results перечнем pydantic (проба: 33 прогона из 42 — от 3 до 22 строк); каталог
  вывода `investigate` пишется файл за файлом (отказ третьей записи оставляет два файла, и
  повтор отвергается); путь вывода, который stdout не кодирует, кончает команду traceback
  после записи.
  Выбор — оркестратор (граница срезов T-50 и T-51, Astra-выбора по ADR 0006 нет). По решению
  владельца срез выполнен напрямую Claude Opus 5.5, без MyFlow-исполнителей; модельное
  ревью по ADR 0006 (Astra xhigh) не проводилось — проверки сделал сам автор, это не
  независимое ревью. Тесты прогонялись на Mac. Red на копии HEAD с новым
  `yaml_document.py`, который там никто не вызывает: 98 failed / 16 passed; новый модуль:
  114 passed на 3.12, 3.13 и 3.14. Ручные мутанты — 45, убиты все (на первом прогоне из 43
  один выжил и не был эквивалентен — добавлен тест). Контроль на tracked pilot-пакетах:
  10 прогонов, каждый файл каждого bundle байт в байт как на HEAD. Полный offline gate с
  duckdb: 5183 passed, 4 skipped, 38 deselected, exit 0 на 3.13, столько же passed на 3.12
  и 3.14; Black/Ruff/strict mypy чистые.
  [Проверки и границы](../DECISIONOPS_INVESTIGATION_INPUTS_CHECKS_2026-10-05.md).

- 2026-10-05 — каталог, который команда не может перечислить, прежде читался как пустой, а
  путь, на который ей нельзя посмотреть, кончал её traceback или читался как отсутствующий.
  Процессные прогоны на macOS с каталогом без части прав (HEAD, Python 3.13.13): каталог
  assets или каталог под ним с asset продукта — `decision verify` exit 0 и
  `pass_with_advisory`, ничто не говорит, что asset не удалось выбрать; каталог metrics base
  ref — exit 3, candidate ref — exit 4 на snapshot без метрик, а `decision capture` шёл к
  движку; validated-каталог — exit 2 без имени каталога; путь assets, каталог dashboards,
  spec, results, validated-файл и output capture под каталогом, в который нельзя войти, —
  9 traceback. На Python 3.14.6 `Path.exists/is_file/is_dir` отвечают `False` на любой отказ:
  те же пути «не найдены», spec — `bi.spec_missing`, а каталог, который перечисляется, но
  в который нельзя войти, тоже читался как пустой (assets — exit 0). Теперь взгляд и обход
  спрашивают операционную систему сами (`auto_bi/filesystem.py`), одинаково на 3.12–3.14:
  «нет» — только ответ системы о пути, которого нет. Под validated paths каталог, который не
  перечисляется, и путь, на который нельзя посмотреть, — `contract.unreadable`, block
  (`cannot list directory: <класс>`, `cannot look at path: <класс>`); всё перечисленное
  загружается. Без продукта `decision verify` называет до трёх непрочитанных путей. В ref и
  под assets такой каталог и вход прогона, на который нельзя посмотреть, — usage error
  `cannot read <путь>: <класс>`, exit 2, до грейда, движка и записи. Пути, которых нет,
  вложенные контракты и assets, пустой каталог читаются как прежде. `VERIFIER_VERSION`
  92 → 93, `CHECK_VERSION` 67 → 68: прогон над validated paths с таким каталогом давал
  bundle и даёт другой; historical bundles не переписаны.
  Не закрыто: `decision evidence` и store смотрят через `pathlib` (проба: 6 traceback из 7
  на 3.13, 3 на 3.14); права Windows не проверялись; каталог, который не перечисляется,
  блокирует, даже если пуст.
  Выбор — оркестратор (границы среза T-51, Astra-выбора по ADR 0006 нет). По решению
  владельца срез выполнен напрямую Claude Opus 5.5, без MyFlow-исполнителей; модельное
  ревью по ADR 0006 (Astra xhigh) не проводилось — проверки сделал сам автор, это не
  независимое ревью. Тесты прогонялись на Mac. Red на копии HEAD с новым модулем, который
  никто не вызывает: 65 failed / 239 passed на 3.13 и 77 failed / 227 passed на 3.14; на
  рабочем дереве 304 passed на 3.12, 3.13 и 3.14. Ручные мутанты — 78: под 3.14 убиты все
  (один выжил на первом прогоне и не был эквивалентен — добавлен тест), под 3.13 — 66,
  остальные 12 возвращают взгляд в `pathlib` и на 3.13 эквивалентны.
  Контроль на tracked pilot-пакетах: 10 прогонов, вердикт и каждая проверка как на HEAD.
  Полный offline gate с duckdb: 5069 passed, 4 skipped, 38 deselected, exit 0 на 3.13,
  столько же passed на 3.12 и 3.14; Black/Ruff/strict mypy чистые.
  [Проверки и границы](../DECISIONOPS_UNLISTED_PATHS_CHECKS_2026-10-05.md).

- 2026-10-04 — вход прогона, который команда не может прочитать, прежде обрывал
  `decision verify` и `decision capture` traceback с exit 1: `model.yaml` base или candidate
  ref, dashboard spec, verified asset или recorded results со строкой, которую PyYAML не
  строит, с незакрытой последовательностью, не-UTF-8, не-mapping или без права чтения —
  44 из 75 процессных прогонов `decision verify` и 6 из 30 `decision capture`; путь
  `--evidence`, который оказался файлом или не принимает запись, — 3 из 3. Теперь такой вход
  — usage error до грейда, до вопросов движку и до записи: `error: cannot read <файл>:
  <причина>`, exit 2, одна строка в stderr. Причина — класс, позиция PyYAML (`at line <n>,
  column <m>`) или брошенное при построении (`ConstructorError (ValueError: …)`); строка
  печатается и не запечатывается, поэтому может цитировать файл. Прочитанный файл, который
  не то, чем должен быть, назван сам: `<файл> is not a valid semantic model: …`, `<файл> is
  not a valid verified asset: …`; ref со значением, с которым его граф не адресуется, —
  `cannot load semantic ref <ref>: …`. Store отвечает на отказ файловой системы своей
  `ArtifactStoreError`, `decision verify` — `cannot write the evidence bundle under
  <каталог>: …` и exit 2; записанное до отказа — без manifest и не bundle, повтор пишет тот
  же bundle. Версии evidence не менялись (92/67): ни один вход, дававший bundle или captured
  result, не даёт другого. Четыре прежних usage error читаются иначе: позиция у
  синтаксической ошибки, файл вместо пути assets, `ValueError`-строки в spec как `cannot
  read`, слова `decision verify` у `decision capture`.
  Не закрыто: взгляд на validated-файл, путь assets, dashboard spec, results и output capture
  под каталогом, в который нельзя войти, — traceback; каталог assets, который нельзя
  перечислить, читается как пустой (exit 0, `pass_with_advisory`); запись manifest не атомарна.
  Выбор — оркестратор (граница среза T-50, Astra-выбора по ADR 0006 нет). По решению
  владельца срез выполнен напрямую Claude Opus 5.5, без MyFlow-исполнителей; модельное
  ревью по ADR 0006 (Astra xhigh) не проводилось — проверки сделал сам автор, это не
  независимое ревью. Тесты прогонялись на Mac. Red на копии HEAD с двумя новыми именами,
  которые никто не вызывает: 148 failed / 33 passed; новый модуль: 181 passed. 42 ручных
  мутанта убиты. Контроль на tracked pilot-пакетах: 10 прогонов, каждый файл каждого
  bundle байт в байт как на HEAD. Первый полный gate упал на одном существующем тесте
  (2 failed): он ждал от репозитория assets `ValidationError` схемы, которая стала cause
  `VerifiedAssetFileError`; тест обновлён. Полный offline gate с duckdb: 4946 passed,
  4 skipped, 38 deselected, exit 0; Black/Ruff/strict mypy чистые.
  [Проверки и границы](../DECISIONOPS_UNREADABLE_RUN_INPUTS_CHECKS_2026-10-04.md).

- 2026-10-04 — файл контракта, который PyYAML разбирает, но не может построить, прежде
  обрывал `decision validate` и `decision verify` traceback с exit 1: `as_of: 2026-02-30`,
  смещение зоны больше суток, целое в 5000 цифр, `!!int`/`!!float` с текстом
  (`ValueError`), `!!bool perhaps` (`KeyError`), `!!timestamp lately` (`AttributeError`),
  вложенность 3000 (`RecursionError`) — в validated paths и среди контрактов base и
  candidate refs. Так же падал контракт, чья `metadata` не mapping. Отчёта и bundle не
  было. Теперь всё, что брошено при загрузке YAML-документа контракта, — отказ прочитать
  файл: `contract.unreadable` в validated paths, `gap.contract_invalid` в semantic ref, оба
  block с классом `ConstructorError`; сообщение не цитирует файл, остальные файлы
  загружаются, exit code — вердикт (4). Отказы самого PyYAML сохраняют свой класс,
  `MemoryError` и прерывание не превращаются. Контракт с `metadata` не-mapping —
  `contract.invalid` под именем файла. Общий загрузчик — `auto_bi/yaml_document.py`.
  Версии evidence не менялись (92/67): ни один вход, дававший bundle, не даёт другого.
  `model.yaml`, assets, dashboard specs, recorded results и входы `decision investigate`
  читают другие загрузчики и не тронуты.
  Выбор — оркестратор (кандидат из handoff, Astra-выбора по ADR 0006 нет). По решению
  владельца срез выполнен напрямую Claude Opus 5.5, без MyFlow-исполнителей; модельное
  ревью по ADR 0006 (Astra xhigh) не проводилось — проверки сделал сам автор, это не
  независимое ревью. Тесты прогонялись на Mac. Red на копии HEAD с одним загрузчиком,
  который никто не вызывает: 64 failed / 33 passed; новый модуль: 97 passed. 16 ручных
  мутантов убиты. Контроль на tracked pilot-пакетах: 10 прогонов, каждый файл каждого
  bundle байт в байт как на HEAD. Полный offline gate с duckdb: 4765 passed, 4 skipped,
  38 deselected, exit 0; Black/Ruff/strict mypy чистые.
  [Проверки и границы](../DECISIONOPS_UNBUILT_CONTRACT_YAML_CHECKS_2026-10-04.md).

- 2026-10-04 — `test_cte_gate_walk_on_a_long_chain_stays_linear` больше не меряет время:
  работа обхода CTE-гейтов считается в строках собственного кода пакета (`sys.settrace`,
  только кадры `auto_bi`). Потолок малой цепочки — 1 000 000 строк (обход берёт 391 748),
  цепочка в 4 раза длиннее должна уложиться в 8× малой (берёт 3,96×). Счёт один и тот же
  на нагруженной и свободной машине и при трёх `PYTHONHASHSEED`, и цикл без вызовов его
  двигает; прежние пороги 2 с и 8× по времени падали под нагрузкой (первая попытка полного
  гейта T-45). Три новых теста закрепляют саму меру. Production-код и версии evidence не
  менялись. Ручные мутанты обхода: два квадратичных убиты; третий — пересборка набора
  открытых гейтов на каждую ссылку — выжил: стоимость внутри одного builtin-вызова счёт
  строк не видит, граница записана в docstring меры. Wall-clock бюджеты
  `tests/test_perf_baseline.py` (v1, SQLite I/O) не тронуты.
  Выбор — оркестратор (кандидат из handoff, Astra-выбора по ADR 0006 нет). По решению
  владельца срез выполнен напрямую Claude Opus 5.5, без MyFlow-исполнителей; модельное
  ревью по ADR 0006 (Astra xhigh) не проводилось — проверки сделал сам автор, это не
  независимое ревью. Тесты прогонялись на Mac. Модуль теста: 140 passed. Полный offline
  gate с duckdb: 4668 passed, 4 skipped, 38 deselected, exit 0; Black/Ruff/strict mypy
  чистые.

- 2026-10-04 — `contracts.validate` прежде грейдил каждый issue validation report, а
  отчёт — один на все validated paths: `decision verify` валидирует каждый продукт этих
  путей и верифицирует один. Pin другого продукта на ушедшую версию или на метрику без
  контракта, dimension, которую его метрика не объявляет, и его retired-метрика
  блокировали релиз продукта, о котором правила ничего не сказали; чужие
  `policy.mismatch` и draft-метрика отправляли его в review. Теперь issue, чей `source`
  — другой продукт из `products` отчёта, не грейдится и запечатан отдельно в
  `other_product_issues` (ключ есть всегда); detail считает отложенные issues и
  продукты. Issues загрузки файла (`contract.unreadable`, `contract.unknown_kind`,
  `contract.invalid`, `contract.duplicate_id`) грейдятся на прогоне каждого продукта,
  как прежде; отчёт, не называющий верифицируемый продукт, грейдится целиком, issue с
  `source` вне `products` — тоже. Validation report, `decision validate` (отвечает за
  пути целиком), pin и rule issues, остальные checks и состав run id прежние: чужой
  issue по-прежнему двигает `run_id`. `VERIFIER_VERSION` 91 → 92, `CHECK_VERSION`
  66 → 67; historical bundles не переписаны.
  Выбор — оркестратор: срез закрывает границу спеки T-47, Astra-выбора по ADR 0006 для
  него нет. По решению владельца срез выполнен напрямую Claude Opus 5.5, без
  MyFlow-исполнителей; модельное ревью по ADR 0006 (Astra xhigh) не проводилось —
  проверки сделал сам автор, это не независимое ревью. Тесты прогонялись на Mac.
  Red на HEAD: 30 failed / 0 passed (payload HEAD не несёт нового ключа); на копии HEAD
  с одним этим ключом, всегда пустым: 19 failed / 11 passed; новый модуль: 30 passed.
  19 ручных мутантов правила убиты. Контроль на tracked pilot-пакетах: 10 прогонов,
  вердикт и каждая проверка как на HEAD, отложенных issues нет. Полный offline gate с
  duckdb: 4665 passed, 4 skipped, 38 deselected, exit 0; Black/Ruff/strict mypy чистые.
  [Проверки и границы](../DECISIONOPS_VALIDATION_ISSUE_SCOPE_CHECKS_2026-10-04.md).

- 2026-10-04 — `run_id` прежде не различал прогоны, отличавшиеся статическим входом
  гейта вне продукта и canonical digests двух snapshots: validated-контракт `active`
  против `draft` давал один `run_id` при вердиктах `pass_with_advisory` и `block`, и так
  же — другие expected rows verified asset, другой dashboard spec, непрочитанный
  контракт в candidate ref, комментарий в `model.yaml`. Повтор `decision verify` в тот
  же каталог evidence после исправления такого входа отклонялся с exit 2 как конфликт
  с запечатанным bundle. Теперь idempotency key несёт content address каждого
  статического входа целиком: validation report, validated-контракты, оба snapshot с
  gaps и digest источника, candidate model, dashboard specs, verified assets; порядок
  передачи ключ не двигает. Изменённый вход — другой `run_id`, и повтор пишется рядом
  с первым bundle. Наблюдения момента — записанные результаты, показания движка,
  `created_at` — остаются вне ключа по решению §7.6: такой повтор делит `run_id` и в
  одном каталоге отклоняется, как прежде. Вывод ключа не добавляет точки отказа:
  точный текст входа принимает float, non-finite, целое любой длины и одиночный
  суррогат и различает `1` и `1.0`, 2^53 и 2^53+1, символ вне BMP и пару суррогатов.
  Проверки, findings, вердикты и артефакты меняются только через `run_id`, который
  несут. `VERIFIER_VERSION` 90 → 91, `CHECK_VERSION` остаётся 66; historical bundles
  не переписаны.
  Выбор — оркестратор: срез закрывает границу спек T-45 и T-46, Astra-выбора по
  ADR 0006 для него нет. По решению владельца срез выполнен напрямую Claude Opus 5.5,
  без MyFlow-исполнителей; модельное ревью по ADR 0006 (Astra xhigh) не проводилось —
  проверки сделал сам автор, это не независимое ревью. Тесты прогонялись на Mac.
  Red на HEAD: 74 failed / 17 passed; новый модуль: 91 passed. Контроль на tracked
  pilot-пакетах: 10 прогонов, исходы всех проверок как на HEAD, повторы
  детерминированы, `run_id` на Mac и Windows совпадают. Полный offline gate с duckdb:
  4635 passed, 4 skipped, 38 deselected, exit 0; Black/Ruff/strict mypy чистые.
  [Проверки и границы](../DECISIONOPS_RUN_IDENTITY_CHECKS_2026-10-04.md).

- 2026-10-04 — `contracts.validate` прежде пропускал с advisory запинненную метрику,
  которую candidate вернул в `draft` или отдал под governing policy, под которой
  продукт не верифицируется: diff классифицирует оба изменения как advisory, verdict
  был `pass_with_advisory`, хотя `decision validate` по контракту candidate отвечает
  review. Теперь на три validation rules, читающие контракт метрики, — dimensions,
  status, governing policies — гейт отвечает и на определении candidate каждого
  сравниваемого pin (ровно один узел на запинненной версии и validated-контракт этой
  версии). Ответ, которого правила не дают на validated definition, — rule issue:
  `dimension.candidate_not_allowed` и `metric.candidate_retired` — block,
  `metric.candidate_draft` и `policy.candidate_mismatch` — review,
  `metric.candidate_deprecated` — advisory; держит ли base validated definition,
  неважно. Rule issues стоят после pin issues, detail называет их коды по каждому
  pin. `candidate_pins`, число совпавших pins, validation report, `decision validate`,
  остальные checks и состав run id прежние. `VERIFIER_VERSION` 89 → 90,
  `CHECK_VERSION` 65 → 66; historical bundles не переписаны.
  Выбор — оркестратор: срез закрывает первую границу T-45, Astra-выбора по ADR 0006
  для него нет. По решению владельца срез выполнен напрямую Claude Opus 5.5, без
  MyFlow-исполнителей; модельное ревью по ADR 0006 (Astra xhigh) не проводилось —
  проверки сделал сам автор, это не независимое ревью. Red на HEAD: 17 failed /
  29 passed; новый модуль: 46 passed. Контроль ложных отказов: пять pilot-пакетов,
  9 продуктов, 0 rule issues. Полный offline gate с duckdb: 4545 passed, 4 skipped,
  38 deselected, exit 0; Black/Ruff/strict mypy чистые.
  [Проверки и границы](../DECISIONOPS_PIN_RULES_CHECKS_2026-10-04.md).

- 2026-10-04 — `contracts.validate` прежде пропускал как `contracts.clean`
  validated-контракт active с dimensions `region`, `category` и numerator
  `gross_margin`, когда оба refs держали ту же версию со status `deprecated`,
  dimensions `region` и numerator `revenue`: diff отвечал `semantic.unchanged`,
  SQL компилировался по candidate, verdict был `pass_with_advisory`. Теперь
  validated-контракт запинненной метрики сравнивается с candidate definition
  по изменениям, которые называет semantic diff. Если base держит validated
  definition, изменение под review остаётся у `semantic.diff` и `impact.products`.
  Иначе `metric_ref.candidate_content_mismatch` — block при review или block
  хотя бы одного отличия, advisory при одних advisory; запись pin несёт
  `differences` в порядке diff. Четыре исхода правила версий (`version_mismatch`,
  `ambiguous`, `absent`, `not_compared`), `decision validate`, validation report,
  остальные checks и состав run id прежние. `VERIFIER_VERSION` 88 → 89,
  `CHECK_VERSION` 64 → 65; historical bundles не переписаны.
  Выбор — оркестратор: пробел назван альтернативой synthetic Astra xhigh выбора, давшего
  T-43, и границей спеки T-40; консультацию после срыва Codex-попытки дал
  `claude-fable-5-1` xhigh — не Astra-выбор по ADR 0006, его правило «любая разница
  блокирует» отклонено по owner-пакетам. Реализовано `gpt-6.1-sol` medium через MyFlow
  `sol-high`: GLM-5.3 был в недельном лимите z.ai. Red на HEAD: 38 failed / 15 passed;
  новый модуль: 53 passed. Synthetic Astra xhigh review: 0 findings. Полный offline gate
  с duckdb: 4502 passed, 4 skipped, 38 deselected, exit 0; Black/Ruff/strict mypy
  чистые.
  [Проверки и границы](../DECISIONOPS_PIN_CONTENTS_REVIEW_2026-10-04.md).

- 2026-10-04 — числовые investigation skills (`period_variance`,
  `contribution_decomposition`, `concentration_shift`) прежде теряли цифры после
  28-го знака: группа `10000000000000000000000000000` →
  `10000000000000000000000000001` отвечала дельтой `0`, и brief нёс этот `0` как
  claim. Ответ зависел от порядка строк и decimal context вызывающего потока;
  значение или окно freshness длиннее лимита преобразования integer/text обрывало
  `decision investigate` с unhandled error, короткая caller precision давала ложный
  `pass` freshness. Теперь суммы и разности supplied values вычисляются точно при
  любой длине, ranking идёт по точным значениям, отношения сохраняют шесть знаков,
  rounding — `HALF_EVEN`. Output и digest зависят только от шага и canonical result:
  не от caller decimal context (skill его не читает и не меняет, флаги тоже), порядка
  строк или длины значения. `data_health` берёт возраст точно до микросекунды,
  сравнивает его с окном в целых и читает окно общим `duration_seconds`.
  Числовые skills: `skill_version` 1 → 2; план объявляет версию каждого skill, другая
  версия делает его невалидным, brief отказывается от output другой версии.
  `data_health` остаётся 1, его digest при default context прежний.
  `VERIFIER_VERSION` 88 и `CHECK_VERSION` 64 не изменены; сохранённые артефакты
  не переписаны. Выбор — synthetic Astra xhigh; точный возраст и общий парсер окна
  в `data_health` добавил оркестратор по воспроизведённым отказам.
  Реализовано `gpt-6.1-sol` medium через MyFlow `sol-high`: GLM-5.3 был в недельном
  лимите z.ai. Red на HEAD: 89 failed / 0 passed (HEAD не принимает шаг версии 2);
  на копии HEAD с одними поднятыми версиями skill: 45 failed / 44 passed; новый
  модуль: 89 passed. Synthetic Astra xhigh review: 0 findings. Полный offline gate
  с duckdb: 4449 passed, 4 skipped, 38 deselected, exit 0; Black/Ruff/strict mypy чистые.
  [Проверки и границы](../DECISIONOPS_INVESTIGATION_EXACT_ARITHMETIC_REVIEW_2026-10-04.md).

- 2026-10-04 — `assets.invalidated` прежде пропускал metric pin verified asset
  на другой версии, несколько контрактов или отсутствие метрики как `assets.valid`.
  Теперь каждый pin каждого выбранного asset резолвится в candidate snapshot:
  `asset.metric_ref_version_mismatch`, `asset.metric_ref_ambiguous`,
  `asset.metric_ref_absent` — block. Findings семантического изменения, pins и
  reviewed-запроса идут в этом порядке, независимо друг от друга; base не сравнивается.
  Payload несёт `metric_pins`; clean detail продолжен
  `; <t> of <t> metric pin(s) match the candidate snapshot`, если pins есть.
  Выбор assets, `contracts.validate`, `results.reconcile`, `results.provenance`,
  `decision validate` и not-run при непрочитанном контракте прежние.
  Verifier 87 → 88, check 63 → 64; historical bundles прежние.
  Выбор — synthetic Astra xhigh. Реализовано `gpt-6.1-sol` medium через MyFlow
  `sol-high`: GLM-5.3 был в недельном лимите z.ai. Red на HEAD: 23 failed / 1 passed;
  новый модуль: 24 passed. Synthetic Astra xhigh review: 0 findings.
  Полный offline gate с duckdb: 4360 passed, 4 skipped, 38 deselected, exit 0;
  Black/Ruff/strict mypy чистые.
  [Проверки и границы](../DECISIONOPS_ASSET_METRIC_PINS_REVIEW_2026-10-04.md).

- 2026-10-04 — ClickHouse own-grant строка без `access_type` больше не читается
  как «не write grant» и «не SELECT»: прежде роль становилась read-only по grants,
  exact runtime SQL исполнялся, колонка называлась unreadable. Missing key, NULL
  или пустое значение теперь означает неполный ответ, независимо от partial revoke.
  Сначала `readonly=1/2`, затем отказ grants-вопроса, доказанный write grant и
  неполные enabled roles сохраняют evidence; иначе read-only не установлен,
  `dwh.access` блокируется, reach-вопросы и exact runtime SQL не отправляются.
  Column reach неизвестен при любом `readonly`: `column_reads=null`,
  `security.not_observed`, без ложных physical absence и unenforced classification.
  Typed answers, `readonly=1/2`, отказ grants-вопроса, неполные enabled roles,
  row-policy facts, вопросы и их порядок, PostgreSQL прежние.
  Версии evidence 86 → 87 / 62 → 63; historical bundles прежние.
  Выбор — альтернатива №1 synthetic Astra xhigh выбора, давшего T-40. Движок такую
  строку вернуть не может (колонка — не-nullable `Enum16`, ClickHouse 24.8.14.39,
  вживую): срез закрывает неполный ответ слоя между движком и коллектором.
  Реализовано `gpt-6.1-sol` medium через MyFlow `sol-high`: GLM-5.3 был в недельном
  лимите z.ai. Red на HEAD: 15 failed / 8 passed; новый модуль: 23 passed.
  Synthetic Astra xhigh review: 0 findings. Полный offline gate с duckdb:
  4336 passed, 4 skipped, 38 deselected, exit 0; Black/Ruff/strict mypy чистые.
  [Проверки и границы](../DECISIONOPS_UNTYPED_GRANTS_REVIEW_2026-10-04.md).

- 2026-10-03 — гейт больше не падает на identity-вопросе ядра 9.4 из-за
  отсутствующего `pg_roles.rolbypassrls`. Только после undefined column (`42703`)
  collector спрашивает `server_version_num`; целое неотрицательное значение ниже
  `90500` разрешает identity без `BYPASSRLS` и грейд роли по прежним правилам.
  RLS-каталоги витрин не читаются: `bypasses_row_security=null`, `row_policies=[]`,
  `row_security_enabled=null`; версия записана в facts, clean `dwh.security`
  с покрытием называет ядро, без читаемых витрин его текст прежний. Ядро с
  row-level security, ClickHouse и каждый другой undefined-column случай
  сохраняют прежние правила. Версии evidence 85 → 86 /
  61 → 62; historical bundles прежние. Выбор — оркестратор по v2-скоупу
  Greenplum/Greengage; живые факты сняты на ядре PostgreSQL 9.4.26 и PostgreSQL 16.15
  на Mac (одноразовые кластеры, удалены). Реализовано `gpt-6.1-sol` medium через
  MyFlow `sol-high`: GLM-5.3 был в недельном лимите z.ai. Red на HEAD: 151 failed /
  12 passed; новый модуль: 163 passed. Synthetic Astra xhigh review: 0 findings.
  Полный offline gate с duckdb: 4313 passed, 4 skipped, 38 deselected, exit 0;
  Black/Ruff/strict mypy чистые.
  [Проверки и границы](../DECISIONOPS_KERNEL_ROW_SECURITY_REVIEW_2026-10-03.md).

- 2026-10-03 — `contracts.validate` резолвит каждый metric pin продукта в
  candidate snapshot: ровно один контракт на запинненной версии. Другая версия,
  несколько контрактов или отсутствие блокируют шаг с
  `metric_ref.candidate_version_mismatch`, `metric_ref.candidate_ambiguous`,
  `metric_ref.candidate_absent`. Base и метрики вне pins продукта не сравниваются;
  отсутствующий узел при `gap.contract_invalid` назван `not_compared`, найденный
  грейдится как обычно. Validation issues сохранены, payload добавляет
  `candidate_pins`, detail считает совпавшие pins; validation report не меняется.
  Содержимое контрактов одной версии и pins verified assets не сравниваются;
  `decision validate` прежний. `VERIFIER_VERSION` 84 → 85, `CHECK_VERSION` 60 → 61;
  исторические bundles не переписаны. Выбор — synthetic Astra xhigh; правило
  «ровно один контракт» добавил оркестратор по воспроизведённому расхождению
  diff и компилятора reference SQL при двух контрактах одного id. Реализовано
  `gpt-6.1-sol` medium через MyFlow `sol-high`: GLM-5.3 был в недельном лимите
  z.ai. Red на HEAD: 19 failed / 0 passed; новый модуль: 19 passed. Synthetic
  Astra xhigh review: 0 findings. Полный offline gate с duckdb: 4150 passed,
  4 skipped, 38 deselected, exit 0; Black/Ruff/strict mypy чистые.
  [Проверки и границы](../DECISIONOPS_METRIC_PINS_REVIEW_2026-10-03.md).

- 2026-10-03 — PostgreSQL-family identity-вопрос читает superuser-флаг текущей
  роли из `pg_roles.rolsuper`: у current role без LOGIN нет строки в `pg_user`,
  поэтому прежний вопрос отвечал NULL и блокировал select-only роль как
  unreported. NOLOGIN роль грейдится по своим флагам и write-привилегиям,
  включая роли, которые session user достигает через `SET ROLE`: select-only
  роль сохраняет read-only и eligible exact runtime SQL, NOLOGIN superuser —
  прежнее superuser evidence и block без privilege-вопросов и runtime SQL.
  BYPASSRLS, login-роли и правила отказа, пустого, частичного и NULL
  identity-ответа прежние. `VERIFIER_VERSION` 83 → 84, `CHECK_VERSION` 59 → 60;
  исторические bundles не переписаны. Выбор — альтернатива №2 synthetic Astra
  xhigh выбора, давшего T-37. Живые факты сняты на PostgreSQL 16.15 на Mac
  (одноразовый кластер, удалён). Реализовано `gpt-6.1-sol` medium через MyFlow
  `sol-high`: GLM-5.3 был в недельном лимите z.ai. Red на HEAD: 24 failed /
  20 passed; новый модуль: 44 passed. Synthetic Astra xhigh review: 0 findings.
  Полный offline gate с duckdb: 4131 passed, 4 skipped, 38 deselected, exit 0;
  Black/Ruff/strict mypy чистые.
  [Проверки и границы](../DECISIONOPS_NOLOGIN_IDENTITY_REVIEW_2026-10-03.md).

- 2026-10-03 — ClickHouse role probe спрашивает роли, которые сессия может включить:
  выданная, но не включённая роль (`SET DEFAULT ROLE NONE`) отсутствует в
  `system.enabled_roles`, её write grant невидим собственному grants-вопросу, а
  `SET ROLE` (или HTTP-параметр `role`) пишет. Один вопрос к `system.role_grants`
  называет выданные пользователю роли (строка с `user_name`) и роли, выданные
  ролям (строка с `role_name`), включаемые вместе с ними на любую глубину; уже
  включённые роли из охвата исключены. Достижимые роли опрашиваются одним
  вопросом к `system.grants`: write access type — `read_only=false` с evidence
  `SET ROLE reaches …`, `dwh.access` — block, exact runtime SQL не исполняется;
  отказ или неполный ответ — block без утверждения о чужих правах, доказанный
  write grant сохраняет приоритет. `readonly=1/2`, отказ `system.grants`,
  неполные enabled roles и собственный write grant прежние и новых вопросов не
  шлют; `readonly` переключением роли не снимается, имя роли в литерале — с
  удвоенными обратной косой чертой и кавычкой. Достижимые роли не становятся
  principals: column reach и policy applicability остаются сессионными.
  Пользователю гейта, read-only по grants, нужен `SELECT` на `system.role_grants`
  помимо `system.grants` — иначе `dwh.access` блокируется.
  `VERIFIER_VERSION` 82 → 83, `CHECK_VERSION` 58 → 59; исторические bundles
  не переписаны. Выбор — альтернатива №1 synthetic Astra xhigh выбора, давшего
  T-37. Живые факты сняты на ClickHouse 24.8.14.39 на Mac (одноразовый стенд,
  удалён). Реализация — GLM-5.3 xhigh через MyFlow `glm-5.3`; после лимита z.ai
  закончил `gpt-6.1-sol` medium через `sol-high`. Red на HEAD: 25 failed /
  10 passed; новый модуль: 35 passed. Synthetic Astra xhigh review: 0 findings.
  Полный offline gate с duckdb: 4087 passed, 4 skipped, 38 deselected, exit 0;
  Black/Ruff/strict mypy чистые.
  [Проверки и границы](../DECISIONOPS_CLICKHOUSE_SET_ROLE_REVIEW_2026-10-03.md).

- 2026-10-02 — canonical metric node несёт объявленные `join_paths`, отсортированные
  без одинаковых объявлений; висячий endpoint — `gap.metric_join_dangling`, несколько
  cardinality одного пути — `gap.metric_join_conflict`, оба block. `semantic.diff` именует
  удаление как `metric.join_path_removed` (block), смену cardinality как
  `metric.join_path_changed` (review), добавление как `metric.join_path_added` (advisory).
  Удаление или смена cardinality достигает verified assets как `asset.metric_changed`;
  модельные joins и field-level impact сохраняют правила. Native projection version 2 → 3
  перемещает canonical digest каждого native snapshot с метриками; snapshot без метрик
  сохраняет digest. Прежние digest-привязки требуют повторной верификации.
  `VERIFIER_VERSION` 81 → 82, `CHECK_VERSION` 57 → 58; исторические bundles не переписаны.
  Выбор — synthetic Astra xhigh. Реализовано `gpt-6.1-sol` medium через MyFlow `sol-high`.
  Red на HEAD: 21 failed / 8 passed; профильный набор: 392 passed. Synthetic Astra
  xhigh review: 0 findings. Полный offline gate с duckdb: 4052 passed, 4 skipped,
  38 deselected, exit 0; Black/Ruff/strict mypy чистые.
  [Проверки и границы](../DECISIONOPS_METRIC_JOIN_PATHS_REVIEW_2026-10-02.md).

- 2026-10-02 — metric node canonical snapshot несёт объявленную `freshness`:
  `max_age` как объявлен, quality preconditions отсортированы без повторов.
  `semantic.diff` именует ослабление как `metric.freshness_relaxed` (review),
  ужесточение как `metric.freshness_tightened`, равное окно в другой записи как
  `metric.freshness_restated` (advisory). Native projection version 1 → 2 перемещает
  canonical digest каждого native snapshot с метриками; snapshot без метрик сохраняет
  digest. Прежние digest-привязки требуют повторной верификации.
  Ослабление достигает verified assets как `asset.metric_changed`; `dwh.freshness`
  сохраняет окно продукта. Окна сравниваются в точных целых секундах: очень длинное
  или записанное тысячами цифр объявленное окно больше не роняет gate в diff,
  freshness витрин и provenance stale-capture. `VERIFIER_VERSION` 80 → 81,
  `CHECK_VERSION` 56 → 57; исторические bundles не переписаны. Выбор — альтернатива
  №2 synthetic Astra xhigh выбора, давшего T-35. Реализовано `gpt-6.1-sol` medium
  через MyFlow `sol-high`. Red на HEAD: 13 failed / 2 passed. Synthetic Astra xhigh
  review: F-T-36-1 (окно длиннее предела `timedelta`) и F-T-36-3 (компонент длиннее
  предела одного `int()`) воспроизведены и исправлены, третий проход — 0 новых
  findings. Полный offline gate с duckdb: 4023 passed, 4 skipped, 38 deselected,
  exit 0; Black/Ruff/strict mypy чистые.
  [Проверки и границы](../DECISIONOPS_METRIC_FRESHNESS_REVIEW_2026-10-02.md).

- 2026-10-02 — `bi.contract` перед компиляцией валидирует поставляемый spec
  против candidate-модели тем же `validate_spec`, что и build path перед
  вызовом BI; операторский `raw_sql` разрешён. Spec с ошибками не компилируется
  и payload'ов не получает: block с `bi.spec_invalid`, evidence называет
  дашборд и ошибки валидатора в порядке поставки. Валидные дашборды сохраняют
  findings и payload digests, отсутствующий spec — review с `bi.spec_missing`;
  `impact.products` читает specs как прежде. Версии evidence 79 → 80 / 55 → 56;
  исторические bundles не переписаны. Выбор — synthetic Astra xhigh. Реализовано
  `gpt-6.1-sol` medium через MyFlow `sol-high`, тексты ошибок в тестах закрепил
  оркестратор. Red на HEAD: 20 failed; профильный набор: 310 passed.
  Synthetic Astra xhigh review: 0 findings. Полный offline gate с duckdb:
  3975 passed, 4 skipped, 38 deselected, exit 0; Black/Ruff/strict mypy чистые.
  [Проверки и границы](../DECISIONOPS_BI_SPEC_VALIDATION_REVIEW_2026-10-02.md).

- 2026-10-02 — PostgreSQL-family role probe одним вопросом находит роли, в которые
  session user может переключиться через `SET ROLE` (PostgreSQL 16 — опция `SET`,
  раньше и в Greenplum/Greengage — членство), включая самого session user за role
  default; superuser-роли остаются в вопросе и тогда, когда текущая роль их
  наследует, — членство не передаёт атрибут `SUPERUSER`. Одним вопросом на витрину
  спрашиваются их табличные и колоночные write-права. Достижимая пишущая роль,
  достижимый superuser, имя без значения или неотвеченный вопрос не подтверждают
  read-only: `dwh.access` — block с `access.role_writable`, exact runtime SQL
  не исполняется; доказанные grants сохраняют приоритет. Чистое read-only evidence
  называет проверенные достижимые роли или сообщает, что `SET ROLE` других ролей
  не достигает. Identity-вопрос с его короткими путями, `RoleFacts`
  и ClickHouse-коллекция без изменений.
  Версии evidence 78 → 79 / 54 → 55; исторические bundles не переписаны.
  Выбор — оркестратор по границе `SET ROLE` из review T-33. Реализация — GLM-5.3
  xhigh через MyFlow `glm-5.3`; ремонт после лимита z.ai закончил `gpt-6.1-sol`
  medium через `sol-high`. Synthetic Astra xhigh review: F-T-34-1 (наследуемая
  superuser-группа выпадала из вопроса) воспроизведена на PostgreSQL 16.15
  и исправлена, второй проход — 0 новых findings. Полный offline gate
  с duckdb: 3955 passed, 4 skipped, 38 deselected, exit 0; Black/Ruff/strict
  mypy чистые. [Проверки и границы](../DECISIONOPS_SET_ROLE_REVIEW_2026-10-02.md).

- 2026-10-02 — отсутствующая readable-витрина не считается допустимой для
  датировки: без допустимых вопросов чистый код `dwh.freshness` —
  `freshness.not_run`, проверка появляется в `What was not done`.
  Частичное ненулевое покрытие, findings, status и verdict прежние.
  Verifier 78, check 54. Выбор — synthetic Astra xhigh, реализация Codex.
  Профильный набор: 218 passed; Black/Ruff/strict mypy чистые.
  Итоговое synthetic Astra xhigh review: 0 substantive findings.
  Полный offline gate: 3911 passed, 5 skipped, 38 deselected, exit 0.
  [Проверки и границы](../DECISIONOPS_FRESHNESS_OUTCOME_REVIEW_2026-10-02.md).

- 2026-10-02 — при нуле фактических security-наблюдений чистый код —
  `security.not_run`, поэтому проверка появляется в `What was not done`.
  Частичное ненулевое покрытие сохраняет `security.unfiltered`; findings,
  status, verdict и detail прежние. Verifier 77, check 53.
  Выбор — synthetic Astra xhigh, реализация Codex. Профильный набор: 224 passed;
  Black/Ruff/strict mypy чистые. Итоговое synthetic Astra xhigh review:
  0 substantive findings. Полный offline gate: 3899 passed, 5 skipped,
  38 deselected, exit 0.
  [Проверки и границы](../DECISIONOPS_SECURITY_OUTCOME_REVIEW_2026-10-02.md).

- 2026-10-02 — при нуле фактических физических сравнений clean code —
  `physical.not_run`, поэтому проверка появляется в `What was not done`.
  Частичное ненулевое покрытие сохраняет `physical.as_declared`; findings,
  status, verdict и detail прежние. Verifier 76, check 52.
  Выбор — synthetic Astra xhigh, реализация Codex. Профильный набор: 247 passed;
  Black/Ruff/strict mypy чистые. Итоговое synthetic Astra xhigh review:
  0 substantive findings. Полный offline gate: 3887 passed, 5 skipped,
  38 deselected, exit 0.
  [Проверки и границы](../DECISIONOPS_PHYSICAL_OUTCOME_REVIEW_2026-10-02.md).

- 2026-10-02 — при нулевом числе допустимых cost-сравнений clean code —
  `cost.not_run`, поэтому проверка появляется в `What was not done`.
  Частичное ненулевое покрытие сохраняет `cost.within_budget`; findings,
  status, verdict и detail прежние. Verifier 75, check 51.
  Выбор — synthetic Astra xhigh, реализация Codex.
  Полный offline gate: 3871 passed, 5 skipped, 38 deselected, exit 0;
  Black/Ruff/strict mypy чистые. Итоговое synthetic Astra xhigh review:
  0 substantive findings.
  [Проверки и границы](../DECISIONOPS_COST_OUTCOME_REVIEW_2026-10-02.md).

- 2026-10-02 — чистая сводка `dwh.cost` сообщает фактическое покрытие объявленных
  потолков: строки из планов, байты/время из exact-query runtime evidence.
  Неполное и нулевое покрытие названы явно; findings/codes/verdict прежние.
  Verifier 74, check 50. Выбор — synthetic Astra xhigh, реализация Codex.
  Полный offline gate: 3821 passed, 5 skipped, 38 deselected, exit 0;
  docs-as-code: 24 passed. Black/Ruff/strict mypy чистые. Итоговое synthetic
  Astra xhigh review: 0 substantive findings.
  [Проверки и границы](../DECISIONOPS_COST_COVERAGE_REVIEW_2026-10-02.md).

- 2026-10-02 — чистая сводка `dwh.security` считает подтверждённые права чтения
  запрошенных колонок и ответы о row policies по вопросам (витрина, колонка времени).
  Частичное и нулевое покрытие названы явно, findings/codes/verdict прежние.
  Verifier 73, check 50. Выбор — synthetic Astra xhigh, реализация Codex.
  Профильный набор: 306 passed; полный offline gate: 3790 passed, 5 skipped,
  38 deselected, exit 0; docs-as-code: 24 passed. Black/Ruff/strict mypy чистые.
  Итоговое synthetic Astra xhigh review: 0 substantive findings.
  [Проверки и границы](../DECISIONOPS_SECURITY_COVERAGE_REVIEW_2026-10-02.md).

- 2026-10-02 — чистая сводка `dwh.freshness` считает сравнения по вопросам
  (витрина, колонка времени) и явно называет неполное покрытие. Положительный вывод
  ограничен сравнёнными возрастами; findings, codes и verdict прежние. Verifier 72,
  check 50. Red: 15 failed / 6 passed; связанный набор: 225 passed.
  Полный offline gate: 3763 passed, 5 skipped, 38 deselected, exit 0;
  docs-as-code: 24 passed; Black/Ruff/strict mypy чистые. Выбор и итоговое review —
  synthetic Astra xhigh (0 substantive findings), реализация Codex.
  [Проверки и границы](../DECISIONOPS_FRESHNESS_COVERAGE_REVIEW_2026-10-02.md).

- 2026-10-02 — `dwh.physical` считает фактически сравненные типы колонок и заявленные
  свойства layout по каждому вопросу (витрина, колонка времени). Неполное и нулевое
  покрытие названы явно; недоступные и неизвестные факты не подтверждают соответствие.
  Findings, summary codes и verdict прежние; verifier 71, check 50.
  Выбор — synthetic Astra xhigh; реализация Codex, не human/production acceptance.
  Профильный набор 204 passed, docs-as-code 24 passed; Black/Ruff/strict mypy чистые.
  Итог Astra: 0 незакрытых substantive findings. Полный offline gate: 3741 passed,
  1 failed (SQLite timing budget); изолированная диагностика: 1 passed за 1,25 с.
  [Проверки и границы](../DECISIONOPS_PHYSICAL_COVERAGE_REVIEW_2026-10-02.md).

- 2026-10-02 — составные canonical ClickHouse write grants (`ALTER ...`,
  `CREATE ...`, `DROP ...`) и `ALL` блокируют grant-based read-only и runtime SQL.
  Evidence сохраняет точные имена; профили readonly=1/2 и partial revokes прежние.
  Источник canonical имён — pinned ClickHouse 24.8.14.39. Версии evidence 69 → 70 /
  49 → 50. Выбор — synthetic Astra xhigh, реализация Codex.
  Red: 26 failed / 15 passed, отдельно ALL — 2 failed. Полный offline pytest:
  3723 passed, 5 skipped, 38 deselected; Ruff, Black и strict mypy чистые.
  Итоговое synthetic Astra review: 0 substantive findings.
  [Проверки и границы](../DECISIONOPS_WRITE_GRANTS_REVIEW_2026-10-02.md).

- 2026-10-02 — неполный/неотвеченный список ClickHouse enabled roles не подтверждает
  grant-based read-only и effective column/policy reach. Профиль `readonly=1/2`
  и доказанные write grants сохраняют приоритет; иначе доступ и runtime блокируются.
  Unknown reach даёт security REVIEW, без ложных physical absence/classification claims.
  Версии evidence 68 → 69 / 48 → 49. Выбор — synthetic Astra xhigh, реализация Codex.
  Red: 23 failed / 11 passed. Human/production acceptance не заявляется.
  Полный offline pytest с duckdb: 3679 passed, 5 skipped, 38 deselected, exit 0;
  Ruff, Black и strict mypy чистые. Итоговое synthetic Astra review: 0 substantive findings.
  [Проверки и границы](../DECISIONOPS_ENABLED_ROLES_REVIEW_2026-10-02.md).

- 2026-10-02 — missing/NULL поля PostgreSQL-family write-privilege ответов не
  подтверждают read-only. `dwh.access` — BLOCK, exact runtime SQL не выполняется;
  evidence называет права и витрину, доказанные grants сохраняют приоритет.
  Версии evidence 67 → 68 / 47 → 48. Выбор и synthetic review — Astra xhigh,
  реализация Codex. Red: 74 failed / 4 passed; связанная группа — 451 passed.
  Ruff, Black и strict mypy чистые; итоговое review — 0 substantive findings.
  Полный offline pytest с duckdb: 3645 passed, 5 skipped, 38 deselected, exit 0.
  [Проверки и границы](../DECISIONOPS_PARTIAL_PRIVILEGES_REVIEW_2026-10-02.md).

- 2026-10-02 — частичный PostgreSQL-family identity-ответ, NULL в поле или пустое имя
  не подтверждают read-only. Известные имя и положительные superuser/BYPASSRLS
  сохраняются; false bypass требует двух известных отрицательных флагов, иначе null.
  Неполный identity блокирует `dwh.access` и runtime SQL; write-privilege вопросы
  не выполняются, evidence называет неотвеченные поля. Доказанный superuser сохраняет
  прежнее writable evidence даже при неполной строке. SQL-вопросы и normalizer прежние.
  Точные непустые имя и subject refs сохраняют пробелы quoted-идентификаторов;
  исправлены только два локальных поля, общий `NonBlankText` прежний.
  Версии evidence 66 → 67 / 46 → 47. Выбор — Astra xhigh, тесты — Luna xhigh,
  реализация и независимая проверка — Codex. Red: 40 failed / 40 passed;
  связанная группа до quoted-name поправки — 353 passed; quoted-name red — 20 failed.
  Итоговый полный offline pytest с duckdb: 3567 passed, 5 skipped, 38 deselected;
  Ruff, Black и strict mypy чистые. Synthetic Astra xhigh review — 0 substantive findings:
  [review и границы](../DECISIONOPS_PARTIAL_IDENTITY_REVIEW_2026-10-02.md).

- 2026-10-02 — отказ и пустой ответ PostgreSQL-family identity-вопроса:
  роль `unknown`, `read_only=false`, `bypasses_row_security=null`; write-privilege
  вопросы и exact runtime SQL не выполняются. `dwh.access` — BLOCK через прежний
  `access.role_writable`, detail описывает неподтверждённый read-only без утверждения
  о способности неизвестной роли писать. Ответившие роли и ClickHouse сохраняют
  правила; ошибки соединения пробрасываются. Версии evidence 65 → 66 / 45 → 46.
  Выбор — synthetic Astra xhigh по ADR 0006, реализация Codex. Red:
  16 failed / 16 passed; связанная группа — 305 passed. Полный offline pytest
  с duckdb: 3499 passed, 5 skipped, 38 deselected; Ruff, Black и strict mypy чистые.
  [Итоговое synthetic Astra review](../DECISIONOPS_ROLE_IDENTITY_REVIEW_2026-10-02.md):
  0 substantive findings; human/production acceptance не заявляется.

- 2026-10-02 — отказ или пустой ответ PostgreSQL-family табличного write-privilege
  вопроса не подтверждает read-only при отрицательных колоночных ответах.
  `dwh.access` — BLOCK, exact runtime SQL не выполняется; evidence называет
  неотвеченные табличные INSERT/UPDATE/DELETE/TRUNCATE и витрину. Колоночный вопрос
  продолжается, доказанные grants сохраняют своё evidence. API, набор SQL,
  superuser и ClickHouse без изменений. Версии evidence 64 → 65 / 44 → 45.
  Выбор среза — synthetic Astra xhigh по ADR 0006, реализация Codex.
  Red: 2 failed / 4 passed; связанная группа — 188 passed. Полный offline pytest
  с duckdb — 3467 passed, 5 skipped, 38 deselected; Ruff, Black и package-wide
  strict mypy чистые. [Synthetic Astra xhigh review](../DECISIONOPS_TABLE_PRIVILEGES_REVIEW_2026-10-02.md):
  0 substantive findings; не human/production acceptance.

- 2026-10-02 — PostgreSQL-family role probe спрашивает колоночные INSERT/UPDATE
  через `has_any_column_privilege` по всей витрине скоупа. Только колоночный
  write-grant теперь делает роль writable: `dwh.access` — BLOCK с
  `access.role_writable`, exact runtime SQL не исполняется. Табличный grant
  сохраняет одно evidence; read-only evidence называет табличные и колоночные
  права. Отказ или пустой ответ колоночного privilege-вопроса не подтверждает
  read-only и также блокирует доступ и exact runtime SQL. Superuser и ClickHouse
  сохраняют свои правила.
  Версии evidence: `VERIFIER_VERSION` 63 → 64, `CHECK_VERSION` 43 → 44;
  исторические bundles не переписаны. Реализовано `gpt-6.1-sol` high через
  MyFlow `sol-high` (T-33), завершено и независимо проверено Codex.
  Новые refused/empty регрессии: red 3 failed / 1 passed; полный offline pytest
  с duckdb — 3461 passed, 5 skipped, 38 deselected. Ruff, Black и package-wide
  strict mypy чистые. [Synthetic Astra xhigh review](../DECISIONOPS_COLUMN_PRIVILEGES_REVIEW_2026-10-02.md):
  P1 исправлен, итог 0 substantive findings; не human/production acceptance.

- 2026-10-01 — `results.reconcile` сравнивает нечисловое значение по виду:
  бесконечности одного знака равны при разных записях, включая float за пределом
  binary64, без float-допуска. NaN кандидата против числа или бесконечности —
  `value.changed`; ожидаемый NaN, включая decimal `sNaN`, не решает ячейку:
  `value.expected_nan`, `insufficient` / REVIEW, если больше ничего не доказано.
  Доказанное различие в membership, другом value, порядке или candidate aggregate
  сохраняет `different`; ожидаемые NaN перечисляются после value/order differences,
  перед candidate aggregate issues. NULL сохраняет свои коды; ячейка любого business
  key решается сопоставлением строки, float key `nan` больше не сравнивается сам с собой.
  Версии evidence: `VERIFIER_VERSION` 62 → 63, `CHECK_VERSION` 42 → 43;
  исторические bundles не переписаны.
  Реализовано `gpt-6.1-sol` high через MyFlow `sol-high`.
  Red на HEAD: 58 новых случаев падали, 77 контролей проходили; после правки все 135
  и связанный focused gate прошли: 403 passed. Gate-level тест проверяет REVIEW
  с `result.not_equivalent`, `insufficient` / `value.expected_nan` в payload и отсутствие
  sealed `result.equivalent` claim. Полный offline pytest: 3424 passed, 20 skipped,
  38 deselected; strict mypy — 0 ошибок в 120 модулях, Ruff и Black чистые.
  Срез — альтернатива №2 синтетического Astra xhigh выбора, давшего T-29; политику
  определил оркестратор по делегированию владельца. Итоговое ревью Astra xhigh:
  0 findings. Живые факты сняты на Mac одноразовыми ClickHouse 24.8.14.39 и
  PostgreSQL 16.15 (стенды удалены); гейт не следует SQL-семантике ни одного движка.
  Human/production acceptance не заявляется.

- 2026-10-01 — `results.reconcile` сравнивает точную разность конечных binary64-значений
  с точным объявленным decimal-допуском: absolute напрямую, relative через умножение
  на max(|a|, |b|), без приближения разности, отношения или допуска. Превышение,
  скрытое погрешностью binary64, теперь даёт `value.changed`; точное попадание
  в relative-допуск, отвергавшееся приближённым отношением, теперь даёт `equivalent`.
  Перенос float-колонки
  сохраняет её допуск; signed zero и разные записи одного binary64-значения равны.
  Absolute-допуск за пределом binary64 больше не делает бесконечность равной числу
  или бесконечности другого знака (прежде он сам становился бесконечностью).
  Версии evidence: `VERIFIER_VERSION` 61 → 62, `CHECK_VERSION` 41 → 42;
  исторические bundles не переписаны.
  Реализовано `gpt-6.1-sol` high через MyFlow `sol-high`.
  Red на HEAD: 9 новых случаев падали, 17 контролей проходили; после правки 26 новых
  случаев и связанный focused gate прошли: 268 passed. Gate-level воспроизводитель
  переводит `results.reconcile` из PASS в BLOCK, валидный контроль остаётся PASS.
  Полный offline pytest: 3289 passed, 20 skipped, 38 deselected; strict mypy — 0 ошибок
  в 120 модулях, Ruff и Black чистые. Срез — альтернатива №1 синтетического
  Astra xhigh выбора, давшего T-29. Итоговое ревью Astra xhigh: проход 1 —
  F-T-31-1 (допуск за пределом binary64) отклонена воспроизведением и описана в доках,
  проход 2 подтвердил отклонение: 0 actionable P1/P2.
  Живая БД не запускалась; human/production acceptance не заявляется.

- 2026-10-01 — environment-чтения записывают `event_time_column` своего вопроса
  (витрина, колонка времени). Общий resolver атрибутирует чтение для access,
  freshness, physical, security, cost и допуска runtime probe; порядок не скрывает
  протухшие часы / denied column и не сообщает свежие часы протухшими.
  Неоднозначное чтение — `access.reading_unattributed` (review); legacy без колонки
  сохраняет grading только для единственных часов и единственного чтения витрины.
  `freshness.stale` называет часы; evidence facts несут `event_time_column`.
  Версии evidence: `VERIFIER_VERSION` 60 → 61, `CHECK_VERSION` 40 → 41.
  Реализовано `gpt-6.1-sol` high через MyFlow `sol-high`.
  Red на HEAD: 37 новых случаев падали, 7 контролей проходили; после правки
  44 новых случая и связанный focused gate прошли: 365 passed. Новые offline-тесты
  покрывают обе семьи движков, неизменность SELECT replay, gate-level REVIEW/BLOCK
  и bundle facts. Полный offline pytest: 3263 passed, 20 skipped, 38 deselected;
  strict mypy — 0 ошибок в 120 модулях, Ruff и Black чистые.
  Выбор среза и итоговое ревью — синтетические Astra xhigh: 0 actionable P1/P2.
  Живая БД не запускалась; human/production acceptance не заявляется.

- 2026-10-01 — `results.reconcile` отождествляет integer/decimal business key
  и `group_by` проверок `share_sum` / `partition_conservation` по числу.
  Другой scale находит строку; две записи одного числа дают `insufficient` /
  `key.not_unique`; группы на разных scales объединяются. Non-finite,
  нечисловые значения, NULL и ключи других типов сохраняют записанную форму.
  Детали используют ключ своего result; чтение по имени и неизменность входов сохранены.
  Версии evidence: `VERIFIER_VERSION` 59 → 60, `CHECK_VERSION` 39 → 40.
  Реализовано `gpt-6.1-sol` high через MyFlow `sol-high`.
  Red: 24 новых случая падали, 11 контролей проходили.
  После правки 35 новых случаев и связанный focused gate прошли: 242 passed.
  Полный offline pytest: 3219 passed, 20 skipped, 38 deselected; strict mypy —
  0 ошибок в 120 модулях, Ruff и Black чистые.
  Срез — альтернатива №2 того же синтетического Astra xhigh выбора; итоговое
  ревью Astra xhigh: 0 actionable P1/P2. Живая БД не запускалась;
  human/production acceptance не заявляется.

- 2026-10-01 — `results.reconcile` после schema step читает supplied columns
  по имени: values, business key, контрактный порядок строк и ключ строки
  failed aggregate check. Permuted named values больше не дают ложный pass
  или block; NULL и float tolerance следуют своей колонке, ResultSet не меняются.
  Native capture сохраняет порядок колонок asset; исторические bundles не переписаны.
  Версии evidence: `VERIFIER_VERSION` 58 → 59, `CHECK_VERSION` 38 → 39.
  Реализовано `gpt-6.1-sol` high через MyFlow `sol-high`. Red-green: на comparator
  из HEAD 22 новых теста падали, включая два gate-level; после правки 29 новых
  регрессий прошли, связанный gate: 269 passed.
  Полный offline pytest: 3184 passed, 20 skipped, 38 deselected; strict mypy —
  0 ошибок в 120 модулях, Ruff и Black чистые.
  Выбор среза и итоговое ревью — синтетические Astra xhigh: 0 actionable P1/P2.
  Живая БД не запускалась; human/production acceptance не заявляется.

- 2026-10-01 — table extraction и SQL guard исключают bare-ссылку на CTE,
  только если query-владелец объявления входит в ancestors этой ссылки.
  Вложенная или соседняя CTE больше не скрывает внешнюю физическую таблицу
  от governance/raw-SQL RBAC и не даёт исключение для denylisted bare name.
  Наследование внешнего WITH, root WITH над UNION, whole-AST extraction,
  правила сравнения имён и консервативные same-WITH/recursive/scalar правила сохранены.
  Версии evidence: 57 → 58 / 37 → 38. Red: 19 падений, 33 passed;
  после Luna xhigh fix: 52 passed, включая 34 новые регрессии.
  Независимый полный offline pytest: 3154 passed, 21 skipped, 38 deselected;
  strict mypy — 0 ошибок в 120 модулях, Ruff и Black чистые.
  Выбор среза и итоговое ревью — синтетические Astra xhigh: 0 actionable P1/P2.
  Живая БД не запускалась; human/production acceptance не заявляется.
- 2026-09-30 — ClickHouse table extraction и SQL guard исключают bare-ссылку
  на CTE только по точному регистру. `WITH A … FROM a` больше не скрывает
  физическую таблицу от governance и raw-SQL RBAC; mismatch не обходит denylist.
  Точное совпадение, whole-AST область, lexical resolution и PostgreSQL сохраняют
  правила. Версии evidence: 56 → 57 / 36 → 37.
  Luna xhigh: red-green 9 падений, затем 59 passed; 18 новых регрессий,
  соседний ClickHouse/raw-SQL gate: 144 passed.
  Независимый полный offline pytest: 3120 passed, 21 skipped, 38 deselected;
  strict mypy — 0 ошибок в 120 модулях, Ruff и Black чистые.
  Выбор среза и итоговое ревью — синтетические Astra xhigh: 0 actionable P1/P2.
  Живая БД не запускалась; human/production acceptance не заявляется.
- 2026-09-30 — governance ClickHouse учитывает точный набор достижимых ключей
  `JOIN … USING`: непрочитанные named CTE и scalar `WITH` больше не добавляют
  эту причину partial-field advisory; при соседнем живом JOIN остаются его ключи.
  Используется общий с chart impact обход гейтов CTE; общий extractor сохраняет
  whole-AST область. Версии evidence: 55 → 56 / 35 → 36.
  Luna xhigh: red-green 3 падения, затем 20 новых регрессий; связанный gate: 262 passed.
  Независимо: 20 passed, strict mypy — 0 ошибок в 120 модулях, Ruff и Black чистые.
  Полный offline pytest: 3102 passed, 21 skipped, 38 deselected.
  Итоговое синтетическое Astra xhigh review: 0 actionable P1/P2.
  Живой ClickHouse не запускался; human/production acceptance не заявляется.
- 2026-09-30 — governance ClickHouse учитывает `COLUMNS(...)` только в достижимой
  части SQL: непрочитанные named CTE и scalar `WITH` больше не оставляют ложную
  причину partial-field advisory, живой селектор её сохраняет. Общий extractor
  остаётся whole-AST; используется прежний общий с chart impact обход гейтов CTE.
  Regex не раскрывается по snapshot-полям; именованные чтения, неизвестные
  таблицы/поля, звёзды, `JOIN … USING`, PostgreSQL и chart matcher сохраняют правила.
  Версии evidence: 54 → 55 / 34 → 35. Реализация Luna xhigh: red-green 2 падения,
  затем 17 новых регрессий. Независимый целевой gate: 242 passed; полный offline
  pytest: 3082 passed, 21 skipped, 38 deselected; strict mypy: 0 ошибок в 120 модулях,
  Ruff и Black чистые. Выбор среза — синтетическое Astra xhigh review; итоговое
  синтетическое Astra xhigh review: 0 actionable P1/P2. Живой ClickHouse не запускался;
  human/production acceptance не заявляется.
- 2026-09-30 — общий обход достижимости ClickHouse связывает named CTE и scalar
  `WITH` с точным регистром: внутреннее `a` не скрывает внешнее `A`, а ссылка
  на `a` не открывает непрочитанное соседнее `A`. Звёзды, `COLUMNS`, `JOIN … USING`,
  dictionary- и Join-table-вызовы используют одно правило, включая named CTE в `IN`.
  Точное затенение, self-reference, дубликаты, квалификаторы и `SETTINGS` сохранены.
  Версии evidence: 53 → 54 / 33 → 34. Red-green: 15 новых падений и одно в прежнем
  граничном тесте; 28 новых регрессий прошли, связанный gate: 610 passed.
  Полный offline pytest: 3065 passed, 21 skipped, 38 deselected; strict mypy:
  0 ошибок в 120 модулях, Ruff и Black чистые.
  Синтетическое Astra xhigh review: 0 actionable P1/P2. ClickHouse 24.8.14.39 на Mac
  подтвердил различие регистра и точное затенение в `FROM`/`IN`; стенд удалён.
  Human/production acceptance не заявляется.
- 2026-09-30 — governance ClickHouse сообщает `it selects *` только для звезды,
  читающей колонки в достижимой части SQL: непрочитанные named CTE и scalar
  `WITH` больше не создают эту причину advisory. Общий с chart impact обход
  гейтов CTE перенесён в SQL guard без изменения алгоритма; общий extractor
  сохраняет whole-AST `ColumnReads.starred` и не добавляет обход в chart matcher.
  Именованные чтения, неизвестные таблицы и поля, `COLUMNS`, `JOIN … USING` и
  PostgreSQL сохраняют правила. Версии evidence: 52 → 53 / 32 → 33.
  Red-green: 4 падения, затем 17 passed; расширенные регрессии: 23 passed,
  связанный ClickHouse/chart gate: 498 passed. Полный offline pytest:
  3037 passed, 21 skipped, 38 deselected; strict mypy: 0 ошибок в 120 модулях,
  Ruff и Black чистые. Синтетическое Astra xhigh review: 0 actionable P1/P2;
  живой ClickHouse не запускался, human/production acceptance не заявляется.
- 2026-09-30 — PostgreSQL `CAST(expr AS type)` и `expr::type` сохраняют
  известное сильное имя выхода колонки, пяти агрегатов или оконного
  `ROW_NUMBER` для целого `ORDER BY`/`DISTINCT ON` и левой ветки set operation.
  Аргументы и оконные поля остаются чтениями; `AS`, унарный `+`,
  `GROUP BY` и ClickHouse сохраняют границы. Нормализованные sqlglot
  функции `Cast` не наследуют имя аргумента. Версии evidence: 51 → 52 / 31 → 32.
  Red-green подтвердил исходные ложные чтения и найденный Astra пропуск;
  целевой gate: 539 passed, включая 40 CAST-регрессий. Полный offline pytest:
  3014 passed, 21 skipped, 38 deselected; strict mypy: 0 ошибок в 120 модулях,
  Ruff и Black чистые. PostgreSQL 16.15 на Mac подтвердил контракт casts,
  унарный `+` и границу пользовательских функций; временные кластеры удалены.
  Итоговое синтетическое Astra xhigh review: 0 actionable P1/P2;
  human/production acceptance не заявляется.
- 2026-09-29 — PostgreSQL `ROW_NUMBER() OVER (…)` без явного alias даёт
  неявный выход `row_number` для целого `ORDER BY`/`DISTINCT ON` и
  сортировки set operation по левой ветке; поля `PARTITION BY` и внутреннего
  `ORDER BY` остаются чтениями. `VERIFIER_VERSION` 50 → 51,
  `CHECK_VERSION` 30 → 31: прежний reviewed-запрос мог получить ложный
  governance finding. Red-green: исходный тест упал на ложном
  `dm.sales_daily.row_number`, затем 15 граничных тестов прошли; целевой
  gate: 497 passed, полный offline pytest: 2974 passed, 21 skipped,
  38 deselected. Strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые.
  Итоговое синтетическое Astra xhigh review: 0 actionable P1/P2; живой
  PostgreSQL не запускался, human/production acceptance не заявляется.
- 2026-09-29 — PostgreSQL `COUNT`/`SUM`/`AVG`/`MIN`/`MAX` с `OVER`,
  включая `FILTER (WHERE …)` внутри оконного вызова, сохраняют неявное имя
  выхода для целого `ORDER BY`/`DISTINCT ON` и сортировки set operation по
  левой ветке. Аргументы, предикат `FILTER`, `PARTITION BY` и внутренний
  `ORDER BY` остаются чтениями. `VERIFIER_VERSION` 49 → 50,
  `CHECK_VERSION` 29 → 30: прежний reviewed-запрос мог получить ложный
  governance finding. Red-green: исходный тест упал на ложном `dm.sales_daily.sum`;
  целевой gate: 482 passed, полный offline pytest: 2959 passed, 21 skipped,
  38 deselected. Strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые.
  Итоговое синтетическое Astra xhigh review: 0 actionable P1/P2; живой
  PostgreSQL не запускался, human/production acceptance не заявляется.
- 2026-09-29 — пустой ClickHouse `map()` в operator SQL больше не считается
  шаблоном Superset `{{ … }}`: ложный `chart.sql_partial` уходит, а настоящий
  шаблон остаётся частично прочитанным и рядом с `map()`. Явные чтения колонок
  сохраняются. `VERIFIER_VERSION` 48 → 49, `CHECK_VERSION` остаётся 29;
  меняется chart evidence, но не грейдинг проверок. Red-green подтвердил ложные
  `chart.sql_partial` и `chart.metric_unread`; целевой gate: 161 passed. Полный
  offline pytest: 2941 passed, 21 skipped, 38 deselected; strict mypy: 0 ошибок
  в 120 модулях, Ruff и Black чистые. Синтетическое Astra xhigh review:
  0 actionable P1/P2. Живой ClickHouse не запускался; это не human/production
  acceptance.
- 2026-09-29 — PostgreSQL `COUNT`/`SUM`/`AVG`/`MIN`/`MAX` с `FILTER`
  сохраняют неявное имя выхода в `ORDER BY`/`DISTINCT ON` и сортировке set
  operation; аргументы и предикат остаются чтениями. `VERIFIER_VERSION`
  47 → 48, `CHECK_VERSION` 28 → 29. Red-green: ложное чтение
  `dm.sales_daily.sum` воспроизведено, затем 181 тест PostgreSQL order-by
  прошёл. Полный offline pytest: 2937 passed, 21 skipped, 38 deselected;
  strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые. Срез выбран
  синтетическим Astra xhigh review и реализован Luna xhigh; итоговое review:
  0 actionable P1/P2. Живой PostgreSQL не запускался; это не human/production
  acceptance.
- 2026-09-29 — прямые PostgreSQL `AVG`, `MIN` и `MAX` без alias дают неявные
  выходные имена для `ORDER BY`/`DISTINCT ON` и левой ветки set operation;
  чтения аргументов сохраняются. `VERIFIER_VERSION` 46 → 47,
  `CHECK_VERSION` 27 → 28. Red-green: 18 падений, затем 137 passed;
  полный offline pytest: 2893 passed, 21 skipped, 38 deselected;
  strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые.
  Синтетическое Astra xhigh review: 0 actionable P1/P2;
  live PostgreSQL не запускался, не human/production acceptance.
- 2026-09-29 — PostgreSQL-алиас таблицы со списком выходных имён оставляет
  `asset.query_fields_unchecked` для ссылок на эти имена: snapshot не доказывает
  физический ordinal lineage. Покрыты именованная таблица, скобочный `JOIN`,
  голое и квалифицированное имя, корреляция; соседние физические поля проверяются.
  `ORDER BY` сохраняет приоритет выхода SELECT, `GROUP BY` внутри LATERAL —
  локального выхода. `VERIFIER_VERSION` 45 → 46, `CHECK_VERSION` 26 → 27.
  Синтетические Astra xhigh review выявили четыре P2, закрытые red-green
  тестами; итоговое review: 0 actionable P1/P2. Полный offline pytest:
  2868 passed, 21 skipped, 38 deselected; strict mypy: 0 ошибок в 120 модулях,
  Ruff и Black чистые. Это не human/production acceptance.
- 2026-09-29 — PostgreSQL-семейство оставляет `asset.query_fields_unchecked` для
  `NATURAL JOIN`: ключи берутся из совпадающих выходных имён источников,
  которые проверка не разворачивает и которые могут зависеть от физической схемы.
  Явные соседние поля грейдятся отдельно, вложенные join учитываются.
  `VERIFIER_VERSION` 44 → 45, `CHECK_VERSION` 25 → 26. Red-green тесты закрыли
  пропуск и неточную причину для производных источников. Полный offline pytest:
  2847 passed, 21 skipped, 38 deselected; strict mypy: 0 ошибок в 120 модулях.
  Синтетическое Astra xhigh review: 0 actionable P1/P2 после исправления P2;
  не human/production acceptance.
- 2026-09-29 — PostgreSQL `SUM(...)` без alias даёт неявное имя `sum` для
  целого `ORDER BY`/`DISTINCT ON` и левой ветки set operation. Ложные field
  review/advisory уходят; аргументы сохраняют чтения. `AS`, квотированный
  регистр и унарный `+` сохраняют границы; `GROUP BY` не использует выход.
  `VERIFIER_VERSION` 43 → 44, `CHECK_VERSION` 24 → 25. PostgreSQL 16
  проверен на Mac. Синтетическое Astra xhigh review: 0 actionable P1/P2.
  Полный offline pytest: 2839 passed, 21 skipped, 38 deselected; strict mypy,
  Ruff и Black чистые. Не human/production acceptance.
- 2026-09-29 — PostgreSQL `ORDER BY`/`DISTINCT ON` распознаёт неявное имя
  `count` у прямого `COUNT(...)` без alias, также в левой ветке set operation.
  Ложные field review/advisory уходят, чтения аргументов сохраняются; `AS` и
  унарный `+` сохраняют границы; `"Count"(...)` сохраняет регистр имени.
  `VERIFIER_VERSION` 42 → 43,
  `CHECK_VERSION` 23 → 24. PostgreSQL 16.15 проверен на Mac. Срез выбран по
  синтетическому Astra xhigh review; повторное ревью: 0 actionable P1/P2.
  Полный offline pytest: 2830 passed, 21 skipped, 38 deselected; strict mypy,
  Ruff и Black чистые. Это не human/production acceptance.
- 2026-09-28 — корневые read-only `INTERSECT`/`EXCEPT` проходят SQL guard на
  PostgreSQL и ClickHouse; обе ветки грейдятся для governance. Скобочный корень
  PostgreSQL с внешней сортировкой тоже принимается. `SELECT INTO` отклоняется;
  scalar `LIMIT`/`OFFSET` на set operation сохраняет чтения подзапроса.
  Синтаксис обеих операций проверен на Mac в PostgreSQL 16.15 и ClickHouse 24.8.14.39.
  `VERIFIER_VERSION` 41 → 42 из-за перехода operator SQL от `chart.opaque_sql` к
  `reads_field`; `CHECK_VERSION` 22 → 23 из-за изменённой оценки прежних запросов.
  Синтетическое Astra xhigh review: P1/P2 исправлены, не human/production acceptance.
  Полный offline pytest: 2807 passed, 21 skipped, 38 deselected; strict mypy,
  Ruff и Black чистые.
- 2026-09-28 — корневой PostgreSQL `(SELECT …)` с внешними `ORDER BY`, `LIMIT`,
  `OFFSET` проходит guard и сохраняет чтения для governance; неподдержанные формы
  остаются отказом. Evidence versions 41/22 без изменений: ранее вход не принимался.
  Реализовано Luna xhigh; синтетическое Astra xhigh review: 0 actionable P1/P2,
  не human/production acceptance. Полный offline pytest: 2774 passed, 21 skipped,
  38 deselected; strict mypy, Ruff и Black чистые.
- 2026-09-28 — скобочный элемент ClickHouse `ARRAY JOIN (tags AS t)` читает `tags`,
  а `t` — алиас. Внешний `AS a` заменяет внутренний алиас; настоящий скобочный
  SELECT сохраняет свой scope. Поиск таблиц и chart impact при `IN <name>` используют
  ту же нормализацию. Ложные field review и chart reads уходят, валидный запрос
  больше не обрывает governance. `VERIFIER_VERSION` 40 → 41,
  `CHECK_VERSION` 21 → 22.
  Реализовано как Codex companion Luna xhigh; синтетическое Astra xhigh review:
  0 actionable P1/P2, не human/production acceptance. Целевой прогон T-25:
  303 passed; полный offline pytest: 2751 passed, 21 skipped, 38 deselected.
  Strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые.
- 2026-09-28 — SQL guard переводит `RecursionError` sqlglot на запросе глубже parser depth в
  обычный отказ `SQLGuardError`: `guard_sql`, `ReferenceQuery` и verified-asset registry
  больше не выпускают аварию интерпретатора, а читаемые запросы и chart impact сохраняют
  прежнее поведение. `VERIFIER_VERSION=40`, `CHECK_VERSION=21` без изменений. Реализовано как
  Codex companion Luna xhigh. Целевой прогон T-26: 190 passed, включая docs-as-code;
  полный offline pytest: 2745 passed, 21 skipped, 38 deselected. Strict mypy:
  0 ошибок в 120 модулях, Ruff и Black чистые.
- 2026-09-27 — PostgreSQL `ORDER BY` и `DISTINCT ON` разрешают целое голое имя
  через выход SELECT с учётом регистра, а имя внутри выражения читают как input-колонку.
  `ORDER BY` set operation называет выход левой ветки; корневые `INTERSECT` и
  `EXCEPT` guard по-прежнему отклоняет. Оконные, `WITHIN GROUP` и агрегатные
  сортировки, остальные формы `GROUP BY`, ClickHouse и chart impact не менялись.
  `VERIFIER_VERSION` 39 → 40, `CHECK_VERSION` 20 → 21. Реализовано `gpt-6-sol` high
  через MyFlow-кандидат `sol-high`; унарный `+` читает input и не даёт имени
  неалиасированной проекции, сортировка после скобочного запроса принадлежит его телу,
  а `(column)` сохраняет имя выхода. Полный offline pytest: 2737 passed, 20 skipped, 38
  deselected; strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые. Правила сверены
  вживую на PostgreSQL 16.15 (одноразовый кластер на Mac); ClickHouse 24.8.14.39 форму
  `(SELECT …) ORDER BY …` отвергает синтаксически. Синтетическое ревью `gpt-6-astra` xhigh
  через MyFlow: проход 1 — три находки (унарный `+`, сортировка после скобочного запроса,
  `(column)`) исправлены, проход 2 — F-T-24-7 (`+dm.stores.id`) исправлена, проход 3 — 0
  findings; не human/production acceptance.
- 2026-09-27 — PostgreSQL `GROUP BY` разрешает целое голое имя сначала через локальные
  FROM-колонки, затем через выходной столбец SELECT: при отсутствии объявления в
  governed-источниках ложный review заменён advisory; без источников выходное имя не
  оставляет остатка. Выражения, агрегатные и оконные выходы и квалифицированные имена
  сохраняют input-чтение; ClickHouse и chart impact не менялись. `VERIFIER_VERSION`
  38 → 39, `CHECK_VERSION` 19 → 20. Реализовано `gpt-6-sol` high через MyFlow-кандидат
  `sol-high`; полный offline pytest: 2684 passed, 20 skipped, 38 deselected; strict mypy:
  0 ошибок в 120 модулях, Ruff и Black чистые. Правило сверено вживую на PostgreSQL 16.15
  (одноразовый кластер на Mac). Синтетическое ревью `gpt-6-astra` xhigh через MyFlow:
  F-T-23-1 (имя `GROUP BY` в LATERAL без собственного FROM) исправлена, второй проход
  — 0 findings; не human/production acceptance.
- 2026-09-27 — governance reviewed SQL теперь называет ключи `JOIN … USING` как
  advisory `asset.query_fields_unchecked` на ClickHouse и PostgreSQL-семействе:
  общий ключ нельзя приписать одной таблице, а явные соседние поля грейдятся отдельно.
  `VERIFIER_VERSION` 37 → 38, `CHECK_VERSION` 18 → 19. Реализовано `gpt-5.6-luna`
  xhigh; финальное синтетическое `gpt-6-astra` xhigh review: 0 actionable P1/P2,
  не human/production acceptance. Полный offline pytest: 2655 passed, 21 skipped,
  38 deselected; docs-as-code: 24 passed; strict mypy: 0 ошибок в 120 модулях,
  Ruff и Black чистые.
- 2026-09-26 — F-T-11-13: обычная `dm.sales_daily.revenue` с точным единственным
  неалиасированным именованным источником своего SELECT больше не открывает scalar
  `WITH dm`. Ложные chart reads и `chart.sql_partial` исчезают; несовпадение,
  дубликаты, конфликтующие имена и catalog-источник остаются conservative.
  `VERIFIER_VERSION` 36 → 37, `CHECK_VERSION` остаётся 18. Синтетическое review —
  `gpt-6-astra` xhigh: 3 P2 закрыты red-green тестами, не human/production acceptance.
  Полный offline pytest: 2647 passed, 21 skipped, 38 deselected; docs-as-code: 24 passed;
  strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые.
- 2026-09-26 — F-T-11-7: `t.region` при однозначном явном `FROM`/`JOIN … AS t` своего
  SELECT больше не открывает одноимённый scalar `WITH t` в операторском SQL. Ложные
  chart reads и `chart.sql_partial` исчезают; реальный table read, tuple `t.1` и
  отдельное чтение scalar alias сохраняются. `VERIFIER_VERSION` 35 → 36,
  `CHECK_VERSION` остаётся 18. Итоговое синтетическое `gpt-6-astra` xhigh review:
  0 actionable findings после закрытия P2; не human/production acceptance.
  Полный offline pytest: 2641 passed, 21 skipped,
  38 deselected; chart-impact suite: 260 passed; docs-as-code: 24 passed;
  strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые.
- 2026-09-26 — однозначная ссылка на named CTE в операторском SQL теперь связывается с
  ближайшим объявлением: внутренняя CTE не открывает одноимённую внешнюю и не добавляет
  ложные chart reads или `chart.sql_partial`. `FROM` и bare `IN <name>` следуют одному
  правилу; отдельная внешняя ссылка сохраняет реальный гэп. Self-reference и одноуровневые
  дубликаты остаются conservative. `VERIFIER_VERSION` 34 → 35, `CHECK_VERSION` остаётся 18.
  Полный offline pytest до трёх дополнительных граничных тестов: 2628 passed, 21 skipped,
  38 deselected; после них chart-impact suite: 179 passed. Strict mypy: 0 ошибок в 120
  модулях. Синтетическое ревью `gpt-6-astra` xhigh: дефектов кода нет, один пробел покрытия
  закрыт тестами; не human/production acceptance.
- 2026-09-26 — chart impact учитывает `JOIN … USING` только в исполняемой части SQL:
  непрочитанная named CTE или scalar `WITH` не создаёт ложный `chart.sql_partial`,
  достижимый JOIN сохраняет гэп. `VERIFIER_VERSION` 33 → 34, `CHECK_VERSION` остаётся 18.
  Полный offline pytest: 2623 passed, 21 skipped, 38 deselected; strict mypy: 0 ошибок
  в 120 модулях, Ruff и Black чистые. Синтетическое ревью `gpt-6-astra` xhigh:
  0 actionable findings, не human/production acceptance.
- 2026-09-26 — chart impact учитывает ClickHouse `COLUMNS('regex')` только в исполняемой
  части операторского SQL: непрочитанные named CTE и scalar `WITH` больше не дают ложный
  `chart.sql_partial`, а открытая ссылка сохраняет гэп. Governance reviewed asset SQL
  ищет этот селектор во всём AST; `VERIFIER_VERSION` 32 → 33, `CHECK_VERSION` остаётся 18. Полный
  offline pytest: 2618 passed, 21 skipped, 38 deselected; strict mypy: 0 ошибок в 120
  модулях, Ruff и Black чистые. Синтетическое ревью `gpt-6-astra` xhigh:
  0 actionable findings, не human/production acceptance.
- 2026-09-26 — ClickHouse `COLUMNS('regex')` больше не даёт false-clean governance:
  `asset.query_fields_unchecked` называет физические колонки, не перечисленные SQL;
  соседние именованные поля продолжают грейдиться. PostgreSQL-семейство и chart impact
  без изменений. `VERIFIER_VERSION` 31 → 32, `CHECK_VERSION` 17 → 18. Выбор среза —
  синтетическая оценка `gpt-6-astra` xhigh: 0 actionable findings, не human/production
  acceptance. Полный offline pytest: 2612 passed, 21 skipped, 38 deselected; strict mypy:
  0 ошибок в 120 модулях, Ruff и Black чистые.
- 2026-09-26 — восстановлен package-wide strict mypy для SQL AST: типы `Expr`/`Selectable`,
  nullable cache/scope и коллекций согласованы с sqlglot без изменения SQL-поведения.
  Версии evidence остаются 31/17. Strict mypy: 0 ошибок в 120 модулях; полный offline pytest:
  2603 passed, 21 skipped, 38 deselected. Синтетическое ревью `gpt-6-astra` xhigh:
  0 actionable findings, не human/production acceptance.
- 2026-09-26 — однозначная ссылка внутри вложенного scalar `WITH` теперь связывается с
  ближайшим объявлением: внутренний алиас не открывает внешние `dictGet`, `joinGet` или `*`.
  Self-reference, дубликаты на одном уровне и shadowing именованных CTE остаются conservative;
  `VERIFIER_VERSION` 30 → 31, `CHECK_VERSION` остаётся 17. Итоговое synthetic/modelled review
  `gpt-6-astra` xhigh — 0 actionable findings; полный offline pytest — 2603 passed, 21 skipped,
  38 deselected. Это не human evidence и не production/design-partner acceptance.
- 2026-09-26 — девять offline-тестов ClickHouse alias read-through больше не зависят от
  порога `elapsed < 2.0`: бюджет Python-вызовов прерывает реальную работу парсера и reader'а,
  семантические assertions сохранены. Production-код и версии evidence не менялись.
  Моделированное ревью `gpt-6-astra` xhigh: 0 actionable findings, не human/production
  acceptance. Полный offline pytest: 2596 passed, 21 skipped, 38 deselected; Ruff и Black
  чистые.
- 2026-09-25 — один `COUNT(*)` считает строки, а `COUNT(DISTINCT *)` зависит от всех
  колонок источника. Governance снимает advisory `asset.query_fields_unchecked`, когда его
  единственной причиной была звезда `COUNT(*)`; chart impact добавляет `chart.sql_partial` для
  плитки с `COUNT(DISTINCT *)`, которая раньше была «не этой плиткой» либо была названа только
  по другим чтениям. VERIFIER_VERSION 29 → 30, CHECK_VERSION 16 → 17. Реализовано
  `gpt-5.6-terra` xhigh (Codex companion, MyFlow-кандидат `terra-xhigh`); ревью —
  `gpt-6-astra` xhigh через MyFlow: моделированное ревью, не human evidence и не
  production/design-partner acceptance. Итог ревью — один проход `gpt-6-astra` xhigh через
  MyFlow (2026-09-25): 0 findings.
- 2026-09-25 — ClickHouse `SETTINGS` не читает имя или значение настройки как поле на любом
  query level. Уходят phantom review `asset.query_ungoverned_field`, advisory
  `asset.query_fields_unchecked`, chart `reads_field` и `chart.sql_partial` на имени
  настройки; `additional_table_filters` вместо phantom review даёт advisory
  `asset.query_fields_unchecked` и `chart.sql_partial`, потому что его row-filter SQL не
  читается. VERIFIER_VERSION 28 → 29, CHECK_VERSION 15 → 16. Реализовано
  `gpt-5.6-terra` xhigh (Codex companion, MyFlow-кандидат `terra-xhigh`); ревью —
  `gpt-6-astra` xhigh через MyFlow: моделированное ревью, не human evidence и не
  production/design-partner acceptance. Итог ревью — один проход `gpt-6-astra` xhigh через
  MyFlow (2026-09-25): 0 findings.
- 2026-09-25 — голые input-колонки `HAVING` и `QUALIFY` читаются как из любой другой
  клаузы: PostgreSQL не видит output alias в `HAVING`, а ClickHouse сначала применяет
  видимый alias/read-through; `PARTITION BY` и `ORDER BY` окна в `QUALIFY` тоже читаются.
  VERIFIER_VERSION 27 → 28, CHECK_VERSION 14 → 15: направление одно — добавляются review
  `asset.query_ungoverned_field`, advisory `asset.query_fields_unchecked` и chart
  `reads_field`, ни одна находка не снимается. Реализовано `gpt-5.6-terra` xhigh (Codex
  companion, MyFlow-кандидат `terra-xhigh`); ревью — `gpt-6-astra` xhigh через MyFlow:
  моделированное ревью, не human evidence и не production/design-partner acceptance. Итог
  ревью — один проход `gpt-6-astra` xhigh через MyFlow (2026-09-25): одна находка (голое
  имя над единственным подзапросом или CTE) отклонена воспроизведением — её запрос
  отвергают оба движка, а имя читается так же, как в любой другой клаузе.
- 2026-09-22 — `chart.metric_unread` спрашивается по дашборду: гэп на плитке raw-SQL
  возникает, только когда продукт в scope, доставляющий именно этот точный pinned ref
  дашборда, декларирует одну из изменённых метрик — объединением деклараций всех
  доставляющих продуктов, с независимой фильтрацией смешанного изменения по каждому
  дашборду, без слияния версий дашборда и независимо от порядка продуктов. Ложный
  `chart.metric_unread` на дашборде, который доставляет только продукт, не читающий
  изменённую метрику, исчез; чтения полей, IR `computes_metric` и структурные гэпы
  остались всем изменением. VERIFIER_VERSION 25 → 26, CHECK_VERSION остаётся 13.
  Реализовано GLM 5.3 xhigh (изолированный профиль, попытка продолжена после rate
  limit); ревью — synthetic Astra xhigh read-only по ворк-ордеру, направившее правку
  (восстановлен `reaches_product` на `_match_raw`, фильтрация `computed` — в
  `charts_for`): моделированное ревью, не human evidence и не production/design-partner
  acceptance. Финальный post-implementation review — один проход `gpt-6-astra` xhigh через
  MyFlow (2026-09-24), 0 findings.
- 2026-09-24 — ClickHouse inline- и ARRAY JOIN-алиасы читаются как алиасы, не как поля
  таблицы: `ARRAY JOIN tags AS a` читает `tags`, но не phantom `dm.sales_daily.a`; inline
  alias виден на своём query level, включая ссылку до текстового объявления, и не виден в
  подзапросе. Верхнеуровневый SELECT-алиас сохраняет приоритет, а alias справа от `IN` не
  становится таблицей. VERIFIER_VERSION 26 → 27, CHECK_VERSION 13 → 14: направления оба —
  уходят ложные findings на именах алиасов, но inline-определение в HAVING или QUALIFY,
  которое прежняя атрибуция пропускала, теперь грейдится на месте и может назвать реальное
  поле (`internal_cost_raw` в `(internal_cost_raw AS y)`). Реализовано `gpt-5.6-terra`
  xhigh (Codex companion, MyFlow-кандидат `terra-xhigh`); ревью — `gpt-6-astra` xhigh
  через MyFlow: моделированное ревью, не human evidence и не production/design-partner
  acceptance. Итог ревью — шесть проходов `gpt-6-astra` xhigh через MyFlow (2026-09-24):
  девять находок, восемь исправлены, одна (порог по wall-clock) отклонена с доказательством,
  последний проход — 0 findings.
- 2026-09-18 — на ClickHouse `x IN <table>` читается ровно как `x IN (SELECT * FROM
  <table>)` — в governance-проверке и в достижимости chart impact: ложной находки поля для
  `IN rev` больше нет, а неуправляемая таблица справа от `IN` теперь даёт review
  `asset.query_ungoverned` там, где был advisory (`IN dm.rev`) или ничего (`IN rev`).
  Остатки F-T-14-3 и F-T-14-1 названы в доках. VERIFIER_VERSION 24 → 25, CHECK_VERSION
  12 → 13. Начал grok xhigh (`grokw`), после лимитов обоих grok-аккаунтов закончил
  opus-high (`claude -p`, claude-opus-5, effort high), ранний выход добавил оркестратор;
  ревью — один проход Fable (claude-fable-5-1, effort medium), записанный через MyFlow
  как внешний; Opus-ревьюеры пропущены (тот же движок, что у автора), GLM-5.3 и Codex в
  rate limit: моделированное ревью, не Astra по ADR 0006 и не human evidence.
- 2026-09-18 — ClickHouse-алиас читается насквозь, а не как колонка: колонки
  `WITH <expr> AS a` читаются там, где `a` используется, а ссылка на алиас — не колонка
  таблицы плитки. Имя, объявленное и WITH-, и SELECT-алиасом на одном уровне, читается
  как колонка (F-T-13-17, риск принят). VERIFIER_VERSION 23 → 24, CHECK_VERSION 11 → 12.
  Реализовано grok xhigh (`grokw`), ревью — Opus через MyFlow (`opus-sub`), три прохода,
  без второго голоса; GLM-5.3 и Codex недоступны: моделированное ревью, не Astra по
  ADR 0006 и не human evidence.
- 2026-09-18 — какая часть operator SQL исполняется, решает один обход гейтов CTE,
  общий для словаря, Join-таблицы и звезды: именованная CTE открывается исполняемой
  неквалифицированной ссылкой, scalar `WITH` — исполняемым чтением алиаса (голое имя
  или кортеж); вложенный запрос в живой части исполняется, мёртвый читатель ничего не
  открывает. Shadowing, table alias и database qualifier с тем же именем — известные
  пере-сообщения. Оба пункта, отложенные в T-11, закрыты. VERIFIER_VERSION 22 → 23,
  CHECK_VERSION остаётся 11. Реализовано grok xhigh (`grokw`), ревью — Opus через
  MyFlow (`opus-sub`), без второго голоса; GLM-5.3 и Codex недоступны: моделированное
  ревью, не Astra по ADR 0006 и не human evidence.
- 2026-09-18 — `joinGet` / `joinGetOrNull` с литеральными Join-таблицей и одной колонкой
  называют чтение `<таблица>.<колонка>`; любой другой `joinget…` оставляет плитку
  прочитанной частично (`chart.sql_partial`) с клаузой «whose Join table read this run
  could not name». VERIFIER_VERSION 21 → 22, CHECK_VERSION остаётся 11. Реализовано
  grok xhigh (`grokw`), ревью — Opus через MyFlow (`opus-sub`), без второго голоса;
  GLM-5.3 и Codex недоступны: моделированное ревью, не Astra по ADR 0006 и не human
  evidence.
- 2026-09-18 — словарное правило доведено: закрытый набор типизированных геттеров
  (`dictGet<Type>` по целым 8…256, float, decimal, date/datetime, uuid, ip, string, bool)
  называет чтение, scalar `WITH (SELECT …) AS alias` считается, когда алиас прочитан, клауза
  непрочитанного вызова правдива для каждого случая. Пере-сообщение через алиас из
  неисполняемой CTE и асимметрия правила звезды отложены в T-11. VERIFIER_VERSION 20 → 21,
  CHECK_VERSION остаётся 11. Реализовано grok xhigh (`grokw`), ревью — Opus через MyFlow
  (`opus-sub`, второй голос `fable`; GLM-5.3 и Codex недоступны): моделированное ревью, не
  Astra по ADR 0006 и не human evidence.
- 2026-09-18 — metric-derived гэпы (`chart.metric_unread`, `chart.metric_unknown`,
  `chart.formula_untyped`) записываются только для изменения, которое достигает продукта в
  scope; per-product scoping гэпа остаётся открытым. VERIFIER_VERSION 19 → 20, CHECK_VERSION
  остаётся 11. Реализовано grok xhigh (`grokw`), ревью — Opus через MyFlow (`opus-sub`, второй
  голос `fable`; GLM-5.3 и Codex недоступны): моделированное ревью, не Astra по ADR 0006 и не
  human evidence.
- 2026-09-18 — словарный вызов ClickHouse в исполняемом scope либо называет чтение
  `<словарь>.<атрибут>` (литеральный геттер, кортеж — по чтению на атрибут), либо оставляет
  плитку прочитанной частично (`chart.sql_partial`), `dictHas`/`dictIsIn` включительно.
  VERIFIER_VERSION 18 → 19, CHECK_VERSION остаётся 11. Реализовано grok xhigh (`grokw`),
  ревью — Opus через MyFlow (`opus-cli`, на части прогонов молча отдавший haiku, затем
  `opus-sub`; второй голос `fable`; GLM-5.3 и Codex недоступны): моделированное ревью, не
  Astra по ADR 0006 и не human evidence.
- 2026-09-17 — операторский `raw_sql` читается из SQL: `reads_field` там, где statement сам
  атрибутирует чтение, никогда `computes_metric`; `chart.opaque_sql` — неразобранный SQL,
  `chart.sql_partial` — частичное чтение, `chart.metric_unread` — невыведенная связь с
  метрикой; `impact.products` раскладывает плитки по коду гэпа. VERIFIER_VERSION 17 → 18,
  CHECK_VERSION остаётся 11. Реализовано grok xhigh (`grokw`), ревью — Opus (`opus-cli`)
  через MyFlow: моделированное ревью, не Astra по ADR 0006 и не human evidence.
- 2026-09-17 — durable spec и комментарии версий записывают, что имя внутри тела LATERAL
  может сдвинуть грейдинг в любую сторону, `needs_review` включительно; три сценария
  фиксируют направления, у которых не было покрытия. Поведение не менялось. Реализовано
  grok xhigh (`grokw`), ревью — Opus (`opus-cli`) через MyFlow: моделированное ревью, не
  Astra по ADR 0006 и не human evidence.
- 2026-09-17 — предшествующие FROM-элементы LATERAL доходят до каждого уровня тела до стены
  derived table/CTE; кандидаты с охватывающих уровней рядом с ближайшими, не упорядочены
  по уровню. VERIFIER_VERSION 16 → 17, CHECK_VERSION 10 → 11. Реализовано grok xhigh
  (`grokw`), ревью — Opus (`opus-cli`) через MyFlow: моделированное ревью, не Astra по
  ADR 0006 и не human evidence.
- 2026-09-17 — голое имя разрешается только по источникам, которые scope читает; имя в
  коррелированном подзапросе, которого локальный источник не объявляет, остаётся
  `asset.query_fields_unchecked`. VERIFIER_VERSION 15 → 16, CHECK_VERSION 9 → 10.
  Реализовано grok xhigh (`grokw`), ревью — Opus (`opus-cli`) через MyFlow: моделированное
  ревью, не Astra по ADR 0006 и не human evidence.
- 2026-09-12 — полная evidence inspection использует один manifest snapshot для
  digest, подписи и возвращённой модели; замена файла между проверками больше не
  смешивает версии. Формат evidence и writer не изменены.
- 2026-09-12 — исправлен обход `--expect-digest` в `decision evidence --artifact`:
  неверный manifest pin блокирует выдачу payload с exit `4`; проверки подписи и
  classification сохранены. Срез реализован Luna xhigh и одобрен Astra xhigh.
- 2026-09-12 — по synthetic Astra xhigh review R3/R4 owner-summary объясняет границы
  reconciliation, attestation, compiled-reference cost и подписи; verifier 15, check 9.
  Исторические signed bundles не изменены; human/production acceptance не заявляется.
- 2026-09-12 — техническая часть R3 закрыта tracked live owner package: Mac, нативный
  ClickHouse 24.8.14.39 на изолированных портах, attested capture, все пять DWH checks и
  signed bundle `run-a7e2e9f914a502de`; pinned-key inspection проходит. Предыдущий S7 снят
  для этого среза без Docker/Colima; human/design-partner outcome остаётся внешним gate.
- 2026-09-12 — решение 3 пересмотрено: canonical evidence manifest поддерживает opt-in
  Ed25519 signature; trust root — pinned public SPKI PEM, signing identity входит в
  `run_id`. Pilot-only private key создан вне checkout на Mac с mode `0600`; production key
  и инфраструктура на Windows не создавались; ADR 0005.
- 2026-09-12 — PostgreSQL `max_scanned_bytes`: native API PostgreSQL 14–18 не
  предоставляет ни exact-query scanned-byte counter, ни byte ceiling; buffer/cache/I/O
  counters измеряют другой контракт. До named trusted provider/extension остаётся честный
  `cost.not_measured`.
- 2026-09-12 — `dwh.cost`: runtime-границы exact reference-запроса проверяются под
  engine limits — ClickHouse для bytes и timeout, PostgreSQL для timeout; недоступные в
  PostgreSQL scanned bytes остаются явно неизмеренными.
- 2026-09-11 — `decision capture`: capture-путь реализован; live-контракты проверены на PostgreSQL-семействе и ClickHouse 24.8.14.39, включая UTC RFC 3339 и metadata пустого результата.
