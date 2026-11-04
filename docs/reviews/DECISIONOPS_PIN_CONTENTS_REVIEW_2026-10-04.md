# Product metric pin contents — synthetic review

**Дата:** 2026-10-04
**Срез:** T-45; verifier 89 / check 65
**Reviewer:** `gpt-6-astra`, reasoning `xhigh`
**Итог:** первый проход — 0 findings.

Модельная оценка не является human/design-partner acceptance, измерением времени
человека или production acceptance. Контракт:
[product metric pins](../specs/product-metric-pins.md), требование «`contracts.validate`
compares the validated contract of a pinned metric with the candidate's definition».

[Срез T-40](DECISIONOPS_METRIC_PINS_REVIEW_2026-10-03.md) сравнил версию, которую называет
пин продукта, с версией в candidate snapshot. Содержимое этой версии осталось открытым:
правила валидации, читающие контракт, — dimensions, status, policy — грейдят копию из
validated paths, а гейт сравнивает, компилирует и сверяет копию из candidate ref. Эти две
копии не сравнивал никто. На HEAD `d8f6761` через входы `decision verify` воспроизведено
(validated-контракт `metric://sales/gross_margin_pct@3` — active, dimensions `region` и
`category`, numerator `gross_margin`):

- base и candidate оба держат версию 3 как deprecated-контракт с одной dimension `region`
  и numerator `revenue`: `contracts.validate` — pass с `contracts.clean`, `semantic.diff` —
  `semantic.unchanged`, reference SQL скомпилирован по формуле candidate под пином
  продукта, вердикт `pass_with_advisory`; `decision validate` по продукту и собственному
  контракту candidate при этом блокирует с `dimension.not_allowed`;
- оба ref держат версию 3 с другим numerator или с другим description: тот же чистый
  прогон.

Теперь для пина, который резолвится по версии, шаг 1 сравнивает validated definition —
canonical node контракта из validated set — с узлом candidate теми изменениями, которые
называет semantic diff. Нет изменений — одно определение: другой порядок dimensions или
policy refs, дважды объявленный join path или precondition и порядок ключей файла
определения не различают. Изменения есть, и base держит validated definition — это
изменение под ревью: его называет `semantic.diff` и грейдит `impact.products`, здесь оно
второй раз не сообщается. Иначе пин получает исход `content_mismatch` и issue
`metric_ref.candidate_content_mismatch`: block, если diff классифицирует хотя бы одно
различие как review или block, advisory — если каждое различие advisory. Это вес, который
`impact.products` даёт тому же изменению метрики, от которой зависит governed-продукт
(review становится block, advisory не эскалирует), а пин и есть эта зависимость: различие
весит одинаково, держит base validated definition или нет, — отличается только проверка,
которая о нём сообщает. Message и detail называют коды различий в порядке diff, запись
пина в `candidate_pins` несёт их в `differences`. Сравнивать нечего, и пин остаётся
`match`, когда validated set не держит контракт этого id или держит его на другой версии
(это блокирует сама валидация) и когда гейту не передали validated-контракты. Исходы
T-40 — другая версия, несколько контрактов, отсутствие, `not_compared` — грейдятся раньше
и не изменились. Validation report, `decision validate`, остальные проверки и run id
прежние.

Почему читается base. В owner-пакетах (`docs/pilot/2026-09-12-owner-review-live-r3`,
`2026-08-25-ch-tz-margin`, `2026-08-25-ch-tz-revenue`) validated-контракт равен base, а
candidate меняет timezone метрики на той же версии: это и есть проверяемое изменение, и
`contracts.validate` в этих пакетах записан прошедшим. Правило «любая разница validated и
candidate блокирует» перевело бы их в block на шаге 1 и сообщило бы одно изменение
дважды.

Выбор. Пробел назван альтернативой synthetic Astra xhigh выбора от 2026-10-04, давшего
T-43 («validated contract versus same-version candidate contents»: credible follow-up,
требующий определить, чья копия авторитетна), и записан границей в спеке T-40.
Консультацию о следующем срезе после T-44 оркестратор запросил у Astra; Codex-попытка
сорвалась, и ответил запасной кандидат цепочки — `claude-fable-5-1`, effort `xhigh`: это
не Astra-выбор по ADR 0006. Ответ предложил этот срез с правилом «на одной запинненной
версии обе копии обязаны быть одним определением, иначе block». Срез оркестратор принял,
правило — нет: оно противоречит owner-пакетам. Правило этого среза — сравнение
именованными изменениями diff, base как объяснение и severity по классификации diff —
определил оркестратор; каждое значение сценариев спеки получено прототипом на копии
пакета, весь offline-набор прошёл на прототипе с правкой одного теста T-40.

Ревью — один проход `gpt-6-astra` xhigh через MyFlow (Codex companion, read-only): 0
findings. Ревьюер сопоставил сценарии второго требования с новым тестовым модулем и
прогнал 59 собственных проверок через `run_gate` с evidence в памяти (запись файлов
запрещал audit hook): block и advisory, смешанные различия, порядок кодов, полные
`issues`, `candidate_pins`, detail, verdict и `reports/summary.md`; правило для base,
приоритет исходов `version_mismatch`, `ambiguous`, `absent` и `not_compared`, входы без
validated-контрактов, неизменность validation report и run id. Пять записей одного
определения сохранили `match`. Контроль ложных отказов ревьюера: пять проверок продуктов
из owner-review-live-r3 и обоих ch-tz пилотов сохранили `contracts.clean`. Эквивалентность
`metric_node` и `metric_changes` существующей канонизации и diff проверена исполнением.
Документация согласована с изменением; версии 89/65 и LF изменённых файлов подтверждены.
Полный набор, файловые pytest-сценарии и red на HEAD ревьюер не повторял. Findings нет.

Реализация — `gpt-6.1-sol` medium через MyFlow `sol-high`: назначенный исполнитель GLM-5.3
xhigh был в недельном лимите z.ai, и контроллер передал срез следующему кандидату. Второе
требование в durable spec написал оркестратор до запуска исполнителя. После исполнителя
оркестратор перенёс импорт `deepcopy` в шапку изменённого теста и написал docstrings трёх
новых функций; strict mypy, не уложившийся в предел времени песочницы исполнителя,
прогнали оркестратор и fast gate.

Независимая проверка оркестратора:

- Новый модуль `tests/test_product_metric_pin_contents.py` против копии `auto_bi` из HEAD:
  38 failed / 15 passed (прошедшие — контроли, на которые HEAD отвечает так же); на рабочем
  дереве — 53 passed.
- Приёмочный набор оркестратора, написанный до реализации и независимо от тестов
  исполнителя: 63 passed на рабочем дереве. Он проходит каждый сценарий второго
  требования через настоящий путь `verify` (и `run_gate` — для входов без
  validated-контрактов), включая пять видов base, который не держит validated definition,
  и пять записей одного определения.
- Один существующий тест изменён: `test_the_validation_reports_own_issues_are_kept` в
  `tests/test_product_metric_pins.py` — candidate теперь держит тот же draft, что и
  validated set; прежде он держал active-контракт, и при сравнении содержимого это
  различие (`metric.status_changed`). Сценарий спеки T-40 уточнён так же.
- Контроль ложных отказов на рабочем дереве: пять tracked pilot-пакетов (9 продуктов) с
  переданными validated-контрактами грейдятся как прежде — каждый пин `match`, issues нет.
  В приёмочном наборе раскладка owner-пакета (validated равен base, candidate меняет
  timezone) остаётся `contracts.clean`, раскладка воспроизведения даёт block с
  `metric.formula_changed, metric.dimensions_removed, metric.status_changed`.
- Fast gate MyFlow: Ruff, Black и strict mypy чистые; focused — 789 passed, набор задачи —
  355 passed.
- Полный offline pytest с duckdb: 4502 passed, 4 skipped, 38 deselected, exit 0.

Границы. Правила валидации, читающие контракт, по-прежнему грейдят копию из validated
paths, а различие с определением candidate весит по классификации diff: метрика,
возвращённая в `draft`, или получившая governing policy, которой нет у продукта, остаётся
advisory — и в `semantic.diff`, когда base держит validated definition, и в этой проверке,
когда не держит, — тогда как `decision validate` по продукту и контракту candidate
отвечает `metric.draft` и `policy.mismatch` как review (воспроизведено на HEAD `d8f6761`:
вердикт `pass_with_advisory` в обоих случаях). Различием считается то, что называет diff:
grain keys в другом порядке — `metric.grain_changed`, а `lineage_refs` и
`verified_asset_refs` контракта, которых snapshot не несёт, не сравниваются. Evidence
называет коды различий; сами различающиеся значения остаются в двух контрактах.
Validated contract set не входит в idempotency key: повтор в тот же каталог evidence
после правки одной validated-копии получает прежний run id и отклоняется как конфликт с
запечатанным bundle — так было и до среза, когда у validated-контракта менялся один
status. Пины verified assets резолвятся по версии (`assets.invalidated`), их содержимое с
validated-контрактом не сравнивается. BI и DWH не запускались: проверка offline.
