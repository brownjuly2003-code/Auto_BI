# Verified asset metric pins in the candidate snapshot — synthetic review

**Дата:** 2026-10-04
**Срез:** T-43; verifier 88 / check 64
**Reviewer:** `gpt-6-astra`, reasoning `xhigh`
**Итог:** первый проход — 0 findings.

Модельная оценка не является human/design-partner acceptance, измерением времени
человека или production acceptance. Контракт:
[verified asset metric pins](../specs/verified-asset-metric-pins.md), требование
«`assets.invalidated` resolves every metric pin of every verified asset in the candidate
snapshot».

Verified asset несёт `metric_refs` — версии метрик, для которых он был отревьюен (§7.5).
Их никто не резолвил: `decision verify` сужает assets до продукта по id метрики без версии,
`VerifiedAssetRepository.invalidate` читает метрики asset тоже по id, а advisory-изменение —
в том числе `metric.version_changed` — ничего не инвалидирует. `contracts.validate` уже
резолвит pins самого продукта
([product metric pins](../specs/product-metric-pins.md)); pins assets оставались половиной,
которую не читал никто. Воспроизведено прогоном гейта в памяти на HEAD `e97eafc`, результат
asset аттестован и равен ожиданию в каждом случае:

- base, candidate, продукт и провалидированный контракт на версии 3
  `metric://sales/gross_margin_pct`, asset пинит `@99`: `assets.invalidated` проходит как
  `assets.valid` с `1 verified asset(s) still stand as evidence`, `results.reconcile`
  запечатывает `result.equivalent`, verdict — `pass_with_advisory`;
- base на версии 3, candidate, продукт и контракт на версии 4, asset по-прежнему пинит
  `@3`: `semantic.diff` — advisory, `assets.invalidated` — `assets.valid`, verdict тот же;
- asset дополнительно пинит метрику, которой нет ни в одном snapshot: тот же чистый
  `assets.invalidated`.

Теперь шаг 5 резолвит каждую запись `metric_refs` каждого выбранного asset в candidate
snapshot: ровно один контракт метрики на запинненной версии — совпадение. Другая версия —
`asset.metric_ref_version_mismatch`, несколько контрактов одного id (при любых версиях,
включая запинненную) — `asset.metric_ref_ambiguous`, ни одного —
`asset.metric_ref_absent`; все три — block, asset в finding назван своим ref. Findings идут
в порядке: семантическое изменение, pins, reviewed-запрос; assets — по id, pins — в
объявленном порядке, повторённый pin грейдится один раз. Причины независимы: устаревший
asset сохраняет `asset.stale_snapshot` и получает оценку pins, удалённая метрика даёт
`asset.metric_removed` и `asset.metric_ref_absent`. Base не сравнивается: исторический base
может держать более старую версию, несколько контрактов или ни одного. Payload шага несёт
`metric_pins` (asset, ref, версии в candidate, исход); чистый detail продолжен
`; <t> of <t> metric pin(s) match the candidate snapshot`, когда pins есть. Выбор assets,
`contracts.validate`, `results.reconcile`, `results.provenance`, `decision validate` и
not-run при `gap.contract_invalid` прежние: pin, который не резолвится, не отменяет
сравнение результата asset и оценку его происхождения. Версия, сдвинутая без изменения
определения (`metric.version_changed`, advisory в diff), теперь блокирует выпуск до
перепривязки asset; asset, перепривязанный вместе с candidate, проходит.

Срез выбран synthetic Astra xhigh (2026-10-04).

Проход Astra (8 проверок, 21 прочитанный файл) сопоставил реализацию и новый тестовый
модуль со всеми сценариями требования — содержательных расхождений нет. Собственная выборка
ревьюера: 120 комбинаций разрешения pins с точными payload и findings, порядком,
дедупликацией и сохранением повторяющихся версий; 14 сценариев `run_gate`, исполненных в
памяти, — совпадение, несовпадение, неоднозначность, отсутствие метрики, перепривязка asset
и исторические base. Проверены порядок findings, приоритет summary code, блокирующий
verdict, `results.reconcile`, `results.provenance`, целостность bundle и
`reports/summary.md`; `gap.contract_invalid` в любом snapshot сохраняет `check.not_run`, и
ни один артефакт такого прогона не содержит `asset.metric_ref_`. Существующие проверки
`VerifiedAsset` без файловых fixtures — 9 passed. Версии 88/64 и документация согласованы с
реализацией. Red на HEAD, полный набор и результаты внешнего гейта ревьюер не повторял.
Findings нет.

Реализация — `gpt-6.1-sol` medium через MyFlow `sol-high`: назначенный исполнитель
GLM-5.3 xhigh был в недельном лимите z.ai, и контроллер передал срез следующему кандидату.
Требование в durable spec написал оркестратор до запуска исполнителя; после исполнителя
оркестратор дописал в четыре теста буквальные утверждения о полном тексте detail.

Независимая проверка оркестратора:

- Новый модуль `tests/test_verified_asset_metric_pins.py` против копии `auto_bi` из HEAD:
  23 failed / 1 passed (проходит контроль not-run при контракте, который не прочитан); на
  рабочем дереве — 24 passed.
- До реализации правило было собрано прототипом на копии пакета: каждый текст detail в
  спеке получен его прогоном через гейт, полный offline-набор прошёл без правки
  существующих тестов.
- Fast gate MyFlow: Ruff, Black и strict mypy чистые; focused — 998 passed, набор задачи —
  334 passed.
- Полный offline pytest с duckdb: 4360 passed, 4 skipped, 38 deselected, exit 0.

Границы: pin не сравнивается со snapshot, против которого asset заявлен верифицированным
(`semantic_digest`), — asset, перепривязанный на новую версию без повторной верификации,
резолвится в candidate и грейдится только семантическим изменением; содержимое метрики
candidate на запинненной версии не сравнивается с тем, для чего asset ревьюился, — изменённое
определение доходит до asset через `semantic.diff` (`asset.metric_changed`) либо через
`asset.stale_snapshot`; semantic ref с двумя контрактами метрики, которую не пинит ни
продукт, ни выбранный asset, по-прежнему не считается пробелом snapshot. Какие assets
получает прогон, срез не менял: asset, не называющий ни одной метрики продукта, в прогон не
попадает и его pins не грейдятся. BI и DWH не запускались: проверка offline.
