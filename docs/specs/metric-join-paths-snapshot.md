# Capability: A metric's declared join paths in the canonical snapshot and its diff

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

DECISIONOPS_ARCHITECTURE §7.3 counts "grain, allowed dimensions и join paths" among the minimal
part of a metric contract that must survive into the canonical form, §8.3 grades a removed join
path as block, and principle 6 says semantics the canonical graph cannot carry becomes an explicit
gap. Until now the metric node of the canonical graph left the contract's `join_paths` out without
a gap. A release that removed a metric's declared join path, changed its cardinality or added one
produced the same canonical digest as before; `semantic.diff` passed with `semantic.unchanged` and
"0 semantic change(s) between the snapshots", and the metric's verified assets stayed valid. A
join path whose endpoint the model does not declare, or one path declared with two cardinalities,
passed without a gap.

The model's own `joins` and their rules (`join.added`, `join.removed`, `join.type_changed`,
`gap.dangling_join`) are not changed by this capability: a path declared both in the model and by
a metric is compared in each place on its own. The existing rule that a review- or block-severity
metric change reaches the metric's verified assets (`asset.metric_changed`) applies to the new
changes unchanged. The endpoints of a metric's join paths do not join the fields its value depends
on (`fields`): which metrics a field change reaches stays as it is.

## Requirement: the canonical snapshot carries a metric's declared join paths

The metric node of the native canonical graph SHALL carry the contract's `join_paths`, each as a
join node with the id `<left>-><right>` and the `left`, `right` and `type` the contract declares,
sorted by id, then by `left`, `right` and `type`, an identical declaration once, so that the
order or repetition of paths in the contract does not move the canonical digest. A change of a
path's endpoints or of its cardinality SHALL move the canonical digest. Because the canonical
digest of every native snapshot with metrics moves with this change of the graph shape, the native
projection version (`NATIVE_SOURCE_VERSION`) SHALL be bumped. A native snapshot without metrics
keeps its canonical digest.

A path with an endpoint the model does not declare as a field SHALL still be carried, and SHALL
produce the gap `gap.metric_join_dangling` (block) at `metrics.<metric id>.join_paths.<left>-><right>`
naming each missing endpoint. A path declared with more than one cardinality SHALL carry every
declaration and SHALL produce the gap `gap.metric_join_conflict` (block) at the same path, naming
the cardinalities in sorted order; no declaration silently wins.

### Scenario: a removed join path moves the canonical digest

- **GIVEN** two native snapshots of the same model and metric contract that differ only in that
  the base declares the join path `dm.sales_daily.store_id -> dm.stores.id` (`many_to_one`) and
  the candidate declares none
- **WHEN** both snapshots are built
- **THEN** their canonical digests differ, and the metric node of each carries its own join paths

### Scenario: the order and repetition of paths do not move the digest

- **GIVEN** two contracts whose `join_paths` hold the same declarations in a different order, one
  of them with a declaration repeated
- **WHEN** both snapshots are built
- **THEN** their canonical digests are equal and the node carries the paths sorted, once each

### Scenario: the native projection version is bumped

- **GIVEN** a native model and its metric contracts
- **WHEN** a native snapshot is built
- **THEN** its `source_version` is the bumped native projection version, neither `1` nor `2`

### Scenario: an endpoint the model does not declare is a gap

- **GIVEN** a metric contract declaring the join path `dm.sales_daily.ghost -> dm.stores.id`, where
  the model declares no field `dm.sales_daily.ghost`
- **WHEN** the snapshot is built
- **THEN** the node carries the path, and the snapshot has the gap `gap.metric_join_dangling`
  (block) at that path, naming `dm.sales_daily.ghost`

### Scenario: one path declared with two cardinalities is a gap

- **GIVEN** a metric contract declaring `dm.sales_daily.store_id -> dm.stores.id` both as
  `many_to_one` and as `one_to_one`
- **WHEN** the snapshot is built
- **THEN** the node carries both declarations, and the snapshot has the gap
  `gap.metric_join_conflict` (block) at that path, naming `many_to_one, one_to_one`

## Requirement: a changed metric join path is named in the semantic diff, graded by direction

When the join paths of a metric present in both snapshots differ, `semantic.diff` SHALL compare
them path by path — a path is its `left` and `right` endpoints, named `<left>-><right>`, with the
sorted cardinalities declared for it — and name the change with the metric as its subject and its
impact:

- `metric.join_path_removed`, severity block: a path the base declares and the candidate does not
  (§8.3 "Удалён field/metric/join path").
- `metric.join_path_changed`, severity review: a path both declare with different cardinalities.
- `metric.join_path_added`, severity advisory: a path only the candidate declares.

Each change's detail SHALL name every path it covers with its cardinalities, the old and the new
ones for a changed path. A path written in the opposite direction is a different path. A metric
whose join paths are unchanged SHALL get no join path change. The other metric attribute changes
and the rules for the model's joins SHALL stay as they are. A removed or changed path reaches the
metric's verified assets as `asset.metric_changed`; an added path does not invalidate them.

### Scenario: a removed path is a block

- **GIVEN** base `join_paths` with `dm.sales_daily.store_id -> dm.stores.id` (`many_to_one`) and a
  candidate without it, nothing else changed
- **WHEN** the gate runs
- **THEN** `semantic.diff` lists `metric.join_path_removed` (block) naming the path and its
  cardinality, its status is block, the verdict is block, and the metric's verified asset gets
  `asset.metric_changed`

### Scenario: a changed cardinality is a review

- **GIVEN** the same path declared `many_to_one` in the base and `one_to_one` in the candidate
- **WHEN** the snapshots are compared
- **THEN** `semantic.diff` lists `metric.join_path_changed` (review) naming the path with
  `many_to_one` and `one_to_one`, and the metric's verified asset gets `asset.metric_changed`

### Scenario: an added path is advisory

- **GIVEN** a base without join paths and a candidate declaring
  `dm.sales_daily.store_id -> dm.stores.id` (`many_to_one`)
- **WHEN** the snapshots are compared
- **THEN** `semantic.diff` lists `metric.join_path_added` (advisory) naming the path, no blocking
  or review change appears, and no verified asset is invalidated

### Scenario: unchanged, reordered or repeated paths add nothing

- **GIVEN** base and candidate with the same join paths, in the same or a different order, with or
  without a repeated declaration
- **WHEN** the gate runs
- **THEN** `semantic.diff` has no join path change and, with nothing else changed, stays
  `semantic.unchanged`

### Scenario: the model's joins keep their rules

- **GIVEN** a candidate whose model drops the join `dm.sales_daily.store_id -> dm.stores.id` while
  the metric still declares it as a join path
- **WHEN** the snapshots are compared
- **THEN** `semantic.diff` lists `join.removed` (block) as before and no `metric.join_path_*`
  change
