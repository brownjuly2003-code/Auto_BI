# Product metric pins in the candidate snapshot — synthetic review

**Дата:** 2026-10-03
**Срез:** T-40; verifier 85 / check 61
**Reviewer:** `gpt-6-astra`, reasoning `xhigh`
**Итог:** первый проход — 0 findings.

Модельная оценка не является human/design-partner acceptance, измерением времени
человека или production acceptance. Контракт:
[product metric pins](specs/product-metric-pins.md).

Ref продукта — пин точной доменной версии (`metric://sales/gross_margin_pct@3`).
`decision validate` резолвит пины по контрактам из validated paths и snapshot не читает.
Гейт же проверяет candidate semantic ref со своим каталогом `metrics/`, а три места,
которые ищут запинненную метрику в snapshot, — компилятор reference SQL, product impact
и сужение verified assets — берут её по id без версии и оставляют вопрос «резолвится ли
пин» валидации. Пин с candidate snapshot не сравнивал никто. На HEAD `40e7a80` через входы
`decision verify` воспроизведено:

- продукт и validated-контракт на версии 3, candidate ref держит версию 4:
  `contracts.validate` — pass с `contracts.clean`, reference SQL скомпилирован для
  `metric://sales/gross_margin_pct@4` под subject refs с `@3`, вердикт `pass_with_advisory`;
- метрики нет ни в base, ни в candidate: тот же чистый `contracts.validate`,
  `sql.compile` — advisory `sql.not_compiled`, вердикт `pass_with_advisory`;
- candidate ref держит два контракта одного id (версии 4 и 3): gap не возникает,
  `semantic.diff` — `semantic.unchanged`, вердикт `pass_with_advisory`; diff читает
  последний контракт id, компилятор reference SQL — первый.

Теперь шаг 1 гейта резолвит каждый metric pin продукта в candidate snapshot: ровно один
контракт метрики на запинненной версии. Другая версия — `metric_ref.candidate_version_mismatch`,
несколько контрактов — `metric_ref.candidate_ambiguous`, отсутствие —
`metric_ref.candidate_absent`; все три — block, subject — пин как записан, source — ref
продукта. Base не читается: его версия метрики — дело `semantic.diff`. Метрики вне пинов
продукта не сравниваются. Если метрики нет в candidate, у которого есть
`gap.contract_invalid`, пин назван `not_compared`, а не отсутствующим: контракт мог не
загрузиться, и `semantic.gaps` уже блокирует прогон; найденный в таком snapshot узел
грейдится как обычно. Sealed payload сохраняет все ключи и issues validation report в их
порядке, добавляет issues пинов в порядке продукта, общий `verdict` и `candidate_pins`
(`ref`, `candidate_versions`, `outcome`); detail считает совпавшие пины и называет каждый
несовпавший. Сам validation report и `decision validate` не меняются.

Срез — выбор synthetic Astra xhigh от 2026-10-03 (P1, false-clean). Оркестратор расширил
правило с «запинненная версия» до «ровно один контракт на запинненной версии»:
расхождение diff и компилятора при двух контрактах одного id воспроизведено на HEAD,
и пин на такой id не резолвится в один контракт.

Проход Astra сверил реализацию с durable spec и требованиями задачи: пять исходов пина,
тексты issues и detail, версии evidence, subject, source и порядок пинов; severity,
candidate gaps, отсутствие чтения base и неизменность validation report; повторяющиеся
версии, оба порядка файлов контрактов, несколько пинов в неалфавитном порядке и метрики
вне пинов продукта. Собственная выборка ревьюера: 18 сценариев через настоящий `run_gate`
с хранилищем в памяти — блокировка, owner summary, совпавший пин и целостность evidence —
и 40 проверок `_check_contracts`; все прошли. Дисковый pytest, полный набор задачи,
strict mypy и red на HEAD ревьюер не повторял. Findings нет.

Реализация — `gpt-6.1-sol` medium через MyFlow `sol-high`: назначенный исполнитель
GLM-5.3 xhigh был в недельном лимите z.ai, и контроллер передал срез следующему кандидату.

Независимая проверка оркестратора:

- Новый модуль `tests/test_product_metric_pins.py` против копии `auto_bi` из HEAD:
  19 failed / 0 passed; на рабочем дереве — 19 passed. Каждый случай разложен на диске
  и проходит через настоящий путь `verify`; один — через `load_contracts` и `run_gate`.
- До реализации правило было собрано прототипом на копии пакета: весь offline-набор
  прошёл без правки существующих тестов, а tracked pilot-пакеты свои пины соблюдают.
- Пробы оркестратора через входы `decision verify` (другая версия, отсутствие метрики,
  два контракта одного id) на рабочем дереве дают block там, где HEAD давал
  `pass_with_advisory`; совпавший пин и более старый base остаются без issue.
- Fast gate MyFlow: Ruff, Black и strict mypy чистые; focused — 790 passed, набор задачи — 542 passed.
- Полный offline pytest с duckdb: 4150 passed, 4 skipped, 38 deselected, exit 0.

Границы: содержимое validated-контракта и candidate-метрики на одной версии не
сравнивается — dimensions, status и policy грейдит копия из validated paths. Пины verified
assets (`metric_refs`) с candidate не сравниваются. Два контракта одного metric id сами по
себе не snapshot gap: для метрики вне пинов продукта diff и компилятор reference SQL
по-прежнему могут читать разные контракты.
