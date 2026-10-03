# DecisionOps — объявленные join paths метрики в canonical snapshot и semantic diff: проверки и границы

**Дата:** 2026-10-02
**Срез:** T-37, verifier 82 / check 58
**Ревьюер:** `gpt-6-astra` xhigh через MyFlow (synthetic review по ADR 0006)
**Итог:** первый проход — 0 findings.

## Что было

§7.3 [архитектуры](DECISIONOPS_ARCHITECTURE.md) относит «grain, allowed dimensions
и join paths» к минимальной части metric contract, которая должна дожить до canonical
form, §8.3 грейдит удалённый join path как block, а принцип 6 требует явного gap для
семантики, которую canonical graph не несёт. `MetricContract.join_paths` нигде не
использовался: metric node canonical graph оставлял его без gap, а `semantic.diff`
сравнивал только модельные `joins`. На HEAD `bca480a` (offline, gate в памяти,
fixture-продукт с валидным дашбордом и verified asset, привязанным к base digest)
удаление пути `dm.sales_daily.store_id -> dm.stores.id` из контракта метрики давало
прежний canonical digest: `semantic.diff` — `semantic.unchanged` и «0 semantic change(s)
between the snapshots», `assets.invalidated` — `assets.valid`. Так же молча проходили
смена cardinality, добавленный путь, endpoint, которого модель не объявляет, и один путь
с двумя cardinality.

## Что изменилось

Metric node несёт `join_paths`: по узлу на каждое различное объявление (id
`<left>-><right>`, объявленные `left`, `right` и `type`), отсортированные по id,
затем по `left`, `right` и `type`. Одинаковое объявление сохраняется один раз, поэтому
порядок и повторы путей в контракте digest не двигают. Native projection version 2 → 3:
canonical digest каждого native snapshot с метриками сдвинулся (пустой список путей
тоже входит в граф), snapshot без метрик сохраняет digest.

Негодное объявление сохраняется в узле и называется block-гэпом по пути
`metrics.<metric id>.join_paths.<left>-><right>`:

- `gap.metric_join_dangling` — endpoint не объявлен моделью как поле; reason называет
  каждый недостающий endpoint один раз, левый первым;
- `gap.metric_join_conflict` — один путь объявлен с несколькими cardinality; reason
  называет их отсортированными, и ни одно объявление молча не побеждает.

`semantic.diff` сравнивает join paths метрики, которая есть в обоих snapshots, путь
за путём — путь задают `left` и `right`, обратное направление — другой путь — по
отсортированному набору cardinality:

- `metric.join_path_removed` (block) — путь есть только в base;
- `metric.join_path_changed` (review) — набор cardinality изменился;
- `metric.join_path_added` (advisory) — путь есть только в candidate.

Subject и impact — метрика; detail называет каждый путь с cardinality, у изменённого —
старые и новые. Удаление и смена cardinality достигают verified assets метрики как
`asset.metric_changed` прежним правилом, добавление assets не инвалидирует. Модельные
`joins` сохраняют свои правила `join.*` и `gap.dangling_join`: путь, объявленный и моделью,
и метрикой, сравнивается в каждом месте отдельно.

Требования и сценарии — в durable spec
[`docs/specs/metric-join-paths-snapshot.md`](specs/metric-join-paths-snapshot.md).

## Проверки

- Red на HEAD: модуль `tests/test_metric_join_paths_snapshot.py` против копии `auto_bi`
  из HEAD — 21 failed / 8 passed. Проходят контроли: digest snapshot без метрик,
  неизменные, переставленные и повторённые пути, добавленный путь без инвалидации,
  модельные joins и два valid-контроля на уровне gate.
- После правки модуль среза — 29 passed; профильный набор задачи — 392 passed.
  Black, Ruff и strict mypy (120 модулей) чистые.
- Полный offline pytest с duckdb: 4052 passed, 4 skipped, 38 deselected, exit 0.
- Из существующих тестов изменена одна проверка в `tests/test_metric_freshness_snapshot.py`:
  литерал версии native projection `2` заменён на «не `1`» и сравнение
  с `NATIVE_SOURCE_VERSION`, как требует её сценарий в
  [`docs/specs/metric-freshness-snapshot.md`](specs/metric-freshness-snapshot.md).

## Границы

- Verified assets и result attestations, привязанные к digest snapshot с метриками,
  получают `asset.stale_snapshot` / `provenance.other_snapshot` (review) до повторной
  верификации. Исторические bundles не переписаны.
- Endpoints join paths не входят в `fields` метрики, поэтому набор метрик, которых
  достигает изменение поля, прежний. Удалённое поле-endpoint даёт `field.removed` (block)
  и block-гэп `gap.metric_join_dangling`; ни то ни другое не несёт metric impact,
  и verified asset метрики через них не инвалидируется — gate при этом блокирует.
- Join paths сравниваются у метрики, которая есть в обоих snapshots; добавленная или
  удалённая метрика сохраняет свои коды.
- Гейт не проверяет, что путь метрики совпадает с модельным join и что объявленная
  cardinality верна для данных: join не исполняется, lineage не выводится.
- BI и DWH не запускались: проверка offline.
- Реализация — `gpt-6.1-sol` medium через MyFlow `sol-high`. Это моделированное ревью,
  не human evidence; human/production acceptance не заявляется.
