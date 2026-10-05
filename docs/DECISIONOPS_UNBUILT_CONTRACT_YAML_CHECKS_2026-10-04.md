# Unbuilt contract YAML: a contract file PyYAML cannot build is a finding — проверки автора

**Дата:** 2026-10-04
**Срез:** T-50; verifier 92 / check 67 (без изменений)
**Автор:** Claude Opus 5.5 (`claude-opus-5-5`), напрямую, без MyFlow-исполнителей
**Модельное ревью:** не проводилось

Владелец в этой сессии выбрал прямое исполнение: спеку, код, тесты и доки написал Claude
Opus 5.5, MyFlow не запускался, synthetic Astra xhigh review не было. По
[ADR 0006](adr/0006-modelled-review-development-gate.md) модельное ревью не выполнено, пока
нет фактического результата Astra xhigh, — у этого среза его нет. Всё перечисленное ниже —
проверки автора над собственной работой: не независимое ревью, не human/design-partner
acceptance и не production acceptance. Тесты и пробы прогонялись на Mac (Python 3.13.13,
PyYAML 6.0.3). Контракт: [files a command cannot read](specs/unreadable-inputs.md).

Контракты — YAML, который пишут люди. `decision validate` читает каждый файл validated paths,
`decision verify` — их же и контракты метрик base и candidate refs. Файл, который не читается,
обе команды грейдят: `contract.unreadable` в validated paths, `gap.contract_invalid` в
semantic ref, оба block. Exit code `1` §12.1 архитектуры оставляет необработанному
исключению: падение не должно выглядеть как вердикт.

Оба загрузчика ловили `yaml.YAMLError` — так PyYAML отвечает на текст, который не может
отсканировать, разобрать или скомпоновать. Значения скомпонованного документа строит обычный
Python, и то, что он бросает, выходило из `yaml.safe_load` как есть. На HEAD `71c486e`
воспроизведено настоящим CLI как процессом — фикстурный контракт метрики и ещё одна строка:

- `as_of: 2026-02-30` (даты нет), `as_of: 2026-01-01T00:00:00+99:99` (смещение зоны больше
  суток), `sample_size:` с целым в 5000 цифр, `sample_size: !!int many`,
  `share: !!float most` — `ValueError`;
- `certified: !!bool perhaps` — `KeyError`;
- `as_of: !!timestamp lately` — `AttributeError`;
- `tags:` с flow-последовательностью вложенности 3000 — `RecursionError`.

С таким файлом в validated paths `decision validate` кончался traceback и exit 1 во всех
8 случаях, `decision verify` — во всех 24 (8 строк в трёх местах: validated paths, контракты
base ref, контракты candidate ref). Контракт метрики или продукта, чья `metadata` — `5`,
текст, список или null, давал `AttributeError` и exit 1 у обеих команд: 16 прогонов из 16.
Ни отчёта, ни bundle не было. Контроль — синтаксическая ошибка в том же файле — и на HEAD
грейдился: exit 4 в каждом из четырёх прогонов.

Теперь всё, что брошено при загрузке YAML-документа контракта, — отказ прочитать файл.
Отказы самого PyYAML сохраняют свой класс; остальное общий загрузчик
`auto_bi/yaml_document.py` превращает в `ConstructorError` — класс, которым PyYAML сам
отвечает на значение, которое не может построить, там, где проверяет (`!!binary` не в
base64). Ошибка хранит брошенное как cause, а её текст — `<class> while the document was
built`: имя класса и ничего из сообщения, которое может цитировать файл. `MemoryError`,
прерывание и выход не превращаются. В validated paths такой файл — один issue
`contract.unreadable` с сообщением `cannot read contract: ConstructorError`, в semantic ref —
один gap `gap.contract_invalid` с причиной `metric contract cannot be read:
ConstructorError` и digest байтов файла. Остальные файлы загружаются, продукты валидируются.
Контракт, чья `metadata` не mapping, — `contract.invalid` под именем файла, как контракт
без `id`. Гейт грейдит эти findings как любые другие того же кода: `contracts.validate` или
`semantic.gaps` — block, bundle записан, exit 4.

Версии evidence не менялись: ни один вход, дававший отчёт или bundle, не даёт другого — вход,
прежде кончавшийся traceback, теперь даёт bundle. Прецедент — срез 2026-09-28 (скобочный
корень PostgreSQL): «ранее вход не принимался».

Выбор. Кандидат из handoff («голое YAML-целое длиннее 4300 цифр роняет `decision validate`»)
после проб оказался одним из восьми случаев одного класса; срез выбрал оркестратор,
Astra-выбора по ADR 0006 для него нет. Решения внутри среза — тоже автора:

- Один общий загрузчик вместо списка классов в каждом `except`: какие классы PyYAML
  выпускает, — деталь его реализации, и список в двух местах разошёлся бы.
- `ConstructorError`, а не имя брошенного класса: `KeyError` для `!!bool perhaps` и
  `AttributeError` для `!!timestamp lately` читателю отчёта ничего не говорят, а PyYAML
  именно этим классом отвечает на невалидный `!!binary`. Брошенное остаётся в `__cause__`.
- Текст ошибки не несёт сообщения брошенного: `invalid literal for int() with base 10:
  'many'` цитирует файл, а сообщение issue попадает в отчёт и в bundle.
- `MemoryError` не превращается: это не утверждение о файле.
- Имя для `contract.invalid` при `metadata` не-mapping — имя файла: так уже назывался
  контракт без `metadata` и с `metadata` без `id`.
- `model.yaml`, verified assets, dashboard specs, recorded results и входы
  `decision investigate` читают другие загрузчики; они не тронуты (границы).

Проверки автора — на рабочем дереве среза, на Mac:

- Спека написана до кода: одно требование, 14 сценариев. Факты о HEAD сняты пробой до
  реализации и повторены процессной пробой после неё.
- Новый модуль `tests/test_unbuilt_contract_yaml.py` — 97 тестов в 14 функциях, по функции на
  сценарий. HEAD не держит `auto_bi/yaml_document.py`, а модуль его импортирует, поэтому
  red снят на копии HEAD с одним этим файлом, который там никто не вызывает: 64 failed /
  33 passed. Прошедшие 33 — 29 тестов самого загрузчика и четыре контроля имени
  `contract.invalid` (id, mapping без id, пустая mapping, нет `metadata`). Упавшие 64 —
  каждый тест, где файл читает `decision validate`, snapshot или гейт. На рабочем дереве —
  97 passed.
- 16 ручных мутантов загрузчика и двух его вызовов, каждый — против нового модуля в
  отдельном worktree: убиты все 16. Мутанты: validated paths или snapshot снова зовут
  `yaml.safe_load`; загрузчик превращает только `ValueError`, только `LookupError`, всё кроме
  `RecursionError`, всё кроме `AttributeError`; превращает `MemoryError`; превращает
  `KeyboardInterrupt` и `SystemExit`; превращает отказы самого PyYAML; текст ошибки цитирует
  сообщение брошенного; ошибка не хранит cause; загрузчик бросает голый `YAMLError`;
  `contract.invalid` всегда называется именем файла; `metadata` не-mapping называет
  контракт; mapping без `id` называет его `None`; непрочитанный файл останавливает загрузку
  остальных. Мутанты написал автор: они проверяют то, что автор догадался сломать, и не
  заменяют инструментальное mutation-покрытие.
- Процессная проба (`python -m auto_bi.cli` подпроцессом) на HEAD и на рабочем дереве:
  8 строк в трёх местах и четыре вида `metadata` для двух kinds — 48 прогонов. HEAD: exit 1
  во всех 48. Рабочее дерево: exit 4 во всех 48. Контроль с синтаксической ошибкой — exit 4
  четыре раза на обоих деревьях.
- Контроль на tracked pilot-пакетах: пять пакетов, 9 продуктов и chart-impact overlay —
  10 прогонов на HEAD и на рабочем дереве. Вердикт, статус и каждая из 15 проверок
  совпадают, `run_id` те же, каждый файл каждого bundle совпадает байт в байт (15–16 файлов
  на прогон). Pilot-пакеты не держат ни одного непрочитанного контракта: контроль
  показывает, что читаемые входы читаются как прежде, и само правило не упражняет.
- Ruff, Black и strict mypy (121 модуль) чистые; концы строк новых файлов — LF,
  `auto_bi/decision/validate.py` остался в CRLF, как закоммичен.
- Полный offline pytest с duckdb: 4765 passed, 4 skipped, 38 deselected, exit 0.

Границы. Finding называет файл и класс, а не значение и не строку: так же по одному классу
сообщаются и отказы самого PyYAML. Имена классов — PyYAML и интерпретатора, и что именно не
строится, решают они: предел в 4300 цифр — default интерпретатора, окружение может его
поднять (`PYTHONINTMAXSTRDIGITS`); допустимая вложенность зависит от recursion limit и от
глубины вызова. Файл, прочитанный в одной конфигурации, может не прочитаться в другой.
Документ глубже стека сообщается как `ConstructorError`, хотя стек кончается у composer.
Срез закрывает только файлы контрактов: `model.yaml` semantic ref, verified assets, dashboard
specs, recorded results и входы `decision investigate` по-прежнему могут кончиться traceback
на тех же значениях. Issue, который печатается с символом, не кодируемым потоком вывода,
по-прежнему обрывает `decision validate` при печати отчёта: воспроизведено с одиночным
суррогатом в quoted `id` на UTF-8 потоке. Значение, которое PyYAML строит, а схема отвергает,
— `contract.invalid` или `gap.contract_invalid` с `metric contract does not validate`, как
прежде. BI и DWH не запускались: проверка offline. Независимого ревью у среза нет — ни
модельного, ни человеческого.
