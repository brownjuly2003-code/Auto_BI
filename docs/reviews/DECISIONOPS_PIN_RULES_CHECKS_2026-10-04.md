# Product rules on the candidate's definition of a pinned metric — проверки автора

**Дата:** 2026-10-04
**Срез:** T-46; verifier 90 / check 66
**Автор:** Claude Opus 5.5 (`claude-opus-5-5`), напрямую, без MyFlow-исполнителей
**Модельное ревью:** не проводилось

Владелец в этой сессии выбрал прямое исполнение: спеку, код, тесты и доки написал Claude
Opus 5.5, MyFlow не запускался, synthetic Astra xhigh review не было. По
[ADR 0006](../adr/0006-modelled-review-development-gate.md) модельное ревью не выполнено, пока
нет фактического результата Astra xhigh, — у этого среза его нет. Всё перечисленное ниже —
проверки автора над собственной работой: не независимое ревью, не human/design-partner
acceptance, не измерение времени человека и не production acceptance. Контракт:
[product metric pins](../specs/product-metric-pins.md), требование «`contracts.validate`
answers the product's rules on the candidate's definition of a pinned metric».

[Срез T-45](DECISIONOPS_PIN_CONTENTS_REVIEW_2026-10-04.md) сравнил содержимое запинненной
версии: validated-контракт против определения candidate, по изменениям semantic diff. Вес
различия он взял у классификации diff, и это оставило границу, записанную в его спеке
первой: три правила валидации, читающие контракт метрики, — dimensions, status, governing
policies — по-прежнему отвечали только на копии из validated paths, а классификация diff —
не классификация этих правил. На HEAD `7f35c65` через входы `decision verify` воспроизведено
(validated-контракт `metric://sales/gross_margin_pct@3` — active, dimensions `region` и
`category`; продукт верифицируется под `policy://release/default@1`):

- base держит validated-контракт, candidate держит версию 3 со status `draft`:
  `contracts.validate` — pass с `contracts.clean`, `semantic.diff` называет
  `metric.status_changed` как advisory, вердикт `pass_with_advisory`;
- validated-контракт без governing policy, base и candidate оба держат версию 3 под
  `policy://finance/close@2`: `contracts.validate` — advisory
  (`metric_ref.candidate_content_mismatch` с `metric.policy_added`), вердикт
  `pass_with_advisory`;
- `decision validate` по продукту и собственному контракту candidate отвечает в первом
  случае `metric.draft`, во втором `policy.mismatch` — оба review, вердикт `needs_review`.

Гейт, который грейдит candidate, отвечал слабее, чем валидация того же контракта.

Теперь для каждого пина, содержимое которого сравнивает T-45, — candidate держит ровно один
узел метрики на запинненной версии, validated set держит контракт этой версии — шаг 1 задаёт
три правила второй раз, для этой одной метрики, на определении candidate: dimensions, status
и governing policies берутся из узла candidate. Ответ становится rule issue, если правила не
дают того же ответа — тот же код, subject и message — на validated definition; совпадающий
ответ — собственный ответ validation report и не повторяется. Governing policies для
сравнения и в message названы по одному разу в отсортированном порядке: тот же набор,
записанный в другом порядке или с повтором, — один ответ. Rule issue сохраняет severity,
subject и source правила; код — код правила с `candidate_` после точки, message —
`in the candidate snapshot, ` и текст правила для этой одной метрики:

| rule issue | severity | subject |
|---|---|---|
| `dimension.candidate_not_allowed` | block | dimension |
| `metric.candidate_retired` | block | пин |
| `metric.candidate_draft` | review | пин |
| `metric.candidate_deprecated` | advisory | пин |
| `policy.candidate_mismatch` | review | пин |

Rule issue сообщается независимо от того, держит ли base validated definition: diff
взвешивает изменение по тому, что оно делает с определением, правила — определение, которое
получит выпуск, по тому, на что продукт вправе опираться. Rule issues стоят после pin issues,
в объявленном продуктом порядке пинов и в порядке правил внутри пина: dimensions по алфавиту,
status, policies. `candidate_pins` и число совпавших пинов не меняются; detail считает rule
issues среди issues и после clauses пинов несёт по одному clause на пин — `the product's
rules give <коды> on the candidate's definition of <пин>`, каждый код один раз. Пин, который
T-45 не сравнивает, — другая версия, несколько контрактов, отсутствие, пин, не разрешённый
validated set, входы гейта без validated-контрактов — rule issue не получает. Validation
report, `decision validate`, остальные проверки и run id прежние.

Выбор. Срез закрывает первую границу, записанную в спеке и проверках T-45. Его выбрал
оркестратор при подготовке в конце прошлой сессии; Astra-выбора по ADR 0006 для него нет.
Правило — вариант «(b)»: обе стороны сравнения идут через один носитель (validated-контракт
со `status`, `dimensions` и отсортированным набором `policy_refs` из узла), а «тот же ответ»
означает равенство issue целиком, включая message. Отвергнут вариант «(a)» — сравнение по
паре код и subject без message: он скрыл бы смену набора governing policies, когда policy
продукта нет ни в validated-контракте, ни в candidate.

Проверки автора — на рабочем дереве среза, если не сказано иное:

- Спека написана до кода: третье требование (17 сценариев), правка четырёх сценариев второго
  требования, три новые границы вместо закрытой. Значения сценариев сняты прототипом на копии
  пакета до реализации; приёмочный набор из 37 проверок прошёл на прототипе и лёг в основу
  тестового модуля. После реализации в спеку добавлены абзац о воспроизведении на HEAD и
  утверждение о `reports/summary.md` в первом сценарии.
- Новый модуль `tests/test_product_metric_pin_rules.py` — 46 тестов: по тесту на каждый
  сценарий третьего требования с буквальными ожиданиями (status, summary code, полный detail,
  полные `issues` по порядку, `candidate_pins`, `verdict` payload и вердикт прогона там, где
  сценарий его называет), два контроля ложных отказов и тест версий. Против копии `auto_bi`
  из HEAD `7f35c65`: 17 failed / 29 passed (прошедшие — контроли и сценарии «не сообщается»,
  на которые HEAD отвечает так же); на рабочем дереве — 46 passed.
- Контроль ложных отказов (a): изменения, которых правила не читают, — другой timezone,
  numerator, description, одна dimension больше, снятая policy, более высокая
  classification — при base с validated definition и при base с определением candidate:
  12 случаев, ни одного rule issue. Контроль (b): validated-контракт равен собственному
  определению candidate (active, draft, deprecated) — issues только собственные validation
  report.
- Контроль ложных отказов на tracked pilot-пакетах: пять пакетов, 9 продуктов, с
  переданными validated-контрактами — 0 rule issues. В owner-пакетах candidate меняет
  timezone метрики, которого правила не читают.
- Пробы сверх сценариев спеки: два пина без dimension продукта и в validated set, и в
  candidate — один собственный `dimension.not_allowed` validation report, rule issue нет;
  validated draft и candidate retired — `metric.candidate_retired` рядом с `metric.draft`;
  validated retired и candidate active — rule issue нет; validated-контракт под
  `policy://finance/close@2` и candidate без policies — rule issue нет, candidate под
  `policy://risk/limits@1` — `policy.candidate_mismatch`; продукт без dimensions — правило
  dimensions молчит.
- `reports/summary.md` называет review-исход под «What needs a human», block — под «What
  stops the release», с полным detail.
- Изменённые тесты T-45 (`tests/test_product_metric_pin_contents.py`): два теста на раскладке
  «deprecated, одна dimension, другой numerator» ждут после content issue
  `dimension.candidate_not_allowed` и `metric.candidate_deprecated`; случаи
  `dimensions_removed` и `deprecated` ждут свой rule issue; случай `draft` ушёл из списка
  advisory-различий, `retired` — из списка изменений под review (оба покрыты новым модулем);
  `policy_added` теперь добавляет policy самого продукта; тест версий переехал в новый
  модуль. Остальные существующие тесты не менялись.
- `auto_bi/decision/validate.py`: три правила вынесены в публичную `product_rule_issues` без
  изменения порядка и текстов; `tests/test_decision_validate.py` прошёл без правок.
- Профильный набор (14 модулей: пины, verify, CLI, summary, validate, evidence, gateway,
  diff, docs): 398 passed.
- Ruff, Black и strict mypy (120 модулей) чистые; концы строк изменённых файлов сохранены
  (`validate.py` — CRLF, остальные — LF).
- Полный offline pytest с duckdb: 4545 passed, 4 skipped, 38 deselected, exit 0. После
  записи этого числа в доки повторены docs-тесты.

Границы. Собственные issues validation report описывают validated-копию и остаются:
candidate, который выполняет правило, нарушенное validated-контрактом, issue отчёта не
снимает — лекарство в том, чтобы валидировать контракт, который держит выпуск. Rule issue —
ответ правила для одной запинненной метрики: `dimension.candidate_not_allowed` называет этот
один пин, тогда как `dimension.not_allowed` отчёта называет все метрики validated set без
этой dimension. Второй ответ есть только у трёх правил, читающих контракт метрики; правило,
которое читает один продукт либо наличие и версию контракта в validated set, второго ответа
не имеет. Validated contract set по-прежнему не входит в idempotency key: входы гейта без
validated-контрактов и с ними получают один run id при разном исходе `contracts.validate`
(закреплено тестом). Содержимое пинов verified assets с validated-контрактом не
сравнивается. BI и DWH не запускались: проверка offline. Независимого ревью у среза нет —
ни модельного, ни человеческого.
