# Capability: A metric's declared freshness in the canonical snapshot and its diff

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

DECISIONOPS_ARCHITECTURE §7.3 counts "freshness и data-quality preconditions" among the minimal
part of a metric contract that must survive into the canonical form, and principle 6 says
semantics the canonical graph cannot carry becomes an explicit gap. Until now the metric node of
the canonical graph left the contract's `freshness` out without a gap. A release that relaxed a
metric's declared freshness from `max_age: PT6H` to `P7D`, or dropped one of its data-quality
preconditions, produced the same canonical digest as before; `semantic.diff` passed with
`semantic.unchanged` and "0 semantic change(s) between the snapshots". The edit that lets the
metric's consumers be served week-old numbers under a six-hour promise was invisible to the gate.

`dwh.freshness` grades the marts against the product's `freshness_max_age`; this capability does
not change that check and does not grade data against a metric's window. The existing rule that
a review-severity metric change reaches the metric's verified assets (`asset.metric_changed`)
applies to the new changes unchanged, as it does to a relaxed cost ceiling.

## Requirement: the canonical snapshot carries a metric's declared freshness

The metric node of the native canonical graph SHALL carry the contract's `freshness`: `max_age`
as the contract declares it and `quality_preconditions` as a sorted list without duplicates, so
that the order or repetition of preconditions in the contract does not move the canonical digest.
A change of `max_age` or of the set of preconditions SHALL move the canonical digest. Because
the canonical digest of every native snapshot with metrics moves with this change of the graph
shape, the native projection version (`NATIVE_SOURCE_VERSION`) SHALL be bumped. A native
snapshot without metrics keeps its canonical digest.

### Scenario: a changed freshness window moves the canonical digest

- **GIVEN** two native snapshots of the same model and metric contract that differ only in
  `freshness.max_age` (`PT6H` and `P7D`)
- **WHEN** both snapshots are built
- **THEN** their canonical digests differ, and the metric node of each carries its own `max_age`

### Scenario: the order of preconditions does not move the digest

- **GIVEN** two contracts whose `quality_preconditions` hold the same names in a different order,
  one of them with a name repeated
- **WHEN** both snapshots are built
- **THEN** their canonical digests are equal and the node carries the names sorted, once each

### Scenario: the native projection version is bumped

- **GIVEN** a native model and its metric contracts
- **WHEN** a native snapshot is built
- **THEN** its `source_version` is the bumped native projection version, not `1`

## Requirement: a changed metric freshness is named in the semantic diff, graded by direction

When the freshness of a metric present in both snapshots differs, `semantic.diff` SHALL name the
change with the metric as its impact, split by direction, as it does for a moved cost ceiling:

- `metric.freshness_relaxed`, severity review: a longer `max_age` (both windows of fixed length,
  compared in exact integer seconds with `auto_bi.scalars.duration_seconds`); a different `max_age`
  that cannot be ordered because either window counts years or months; or a dropped quality
  precondition. A
  relaxed promise lets the metric's consumers be served older or less checked numbers than before,
  and only a person can say that was intended.
- `metric.freshness_tightened`, severity advisory: a shorter `max_age`, or an added quality
  precondition.
- `metric.freshness_restated`, severity advisory: `max_age` written differently with the same
  fixed length (`PT6H` and `PT360M`), so the digest move still has a name.

One edit SHALL be able to produce both a relaxed and a tightened change for one metric. Each
change's detail SHALL name every move it covers, with the old and the new value. A metric whose
freshness is unchanged SHALL get no freshness change. The other metric attribute changes SHALL
stay as they are.

### Scenario: a longer window is a review

- **GIVEN** base `max_age: PT6H` and candidate `max_age: P7D`, nothing else changed
- **WHEN** the gate runs
- **THEN** `semantic.diff` lists `metric.freshness_relaxed` (review) naming `max_age PT6H -> P7D`,
  its status is review and the verdict is not a pass

### Scenario: a shorter window is advisory

- **GIVEN** base `max_age: P7D` and candidate `max_age: PT6H`
- **WHEN** the gate runs
- **THEN** `semantic.diff` lists `metric.freshness_tightened` (advisory) and no relaxed change

### Scenario: a window too long for timedelta is ordered exactly

- **GIVEN** base `max_age: PT6H` and candidate `max_age: P1000000000D`
- **WHEN** the gate runs
- **THEN** `semantic.diff` lists `metric.freshness_relaxed` (review), the reverse edit lists
  `metric.freshness_tightened` (advisory), and neither raises

### Scenario: a window written with more digits than one integer conversion takes is ordered exactly

- **GIVEN** base `max_age: PT6H` and a candidate written `PT`, 4300 zeros and `6H`
- **WHEN** the gate runs
- **THEN** `semantic.diff` lists `metric.freshness_restated` (advisory), and a candidate
  written `P`, 4301 nines and `D` lists `metric.freshness_relaxed` (review), and nothing raises

### Scenario: the same window written differently is named

- **GIVEN** base `max_age: PT6H` and candidate `max_age: PT360M`
- **WHEN** the gate runs
- **THEN** `semantic.diff` lists `metric.freshness_restated` (advisory) and neither a relaxed nor a
  tightened change

### Scenario: a window that cannot be ordered is a review

- **GIVEN** base `max_age: P1M` and candidate `max_age: P30D`
- **WHEN** the gate runs
- **THEN** `semantic.diff` lists `metric.freshness_relaxed` (review), whose detail says the windows
  cannot be ordered

### Scenario: a dropped precondition is a review, an added one advisory

- **GIVEN** a candidate that drops the quality precondition `dq.orders_loaded`, and separately a
  candidate that adds it
- **WHEN** the gate runs
- **THEN** the drop gives `metric.freshness_relaxed` (review) naming the precondition, and the
  addition gives `metric.freshness_tightened` (advisory) naming it

### Scenario: a mixed edit names both directions

- **GIVEN** a candidate whose `max_age` is shorter and which drops a quality precondition
- **WHEN** the gate runs
- **THEN** `semantic.diff` lists both `metric.freshness_relaxed` (the dropped precondition) and
  `metric.freshness_tightened` (the shorter window)

### Scenario: an unchanged freshness adds nothing

- **GIVEN** base and candidate with the same freshness
- **WHEN** the gate runs
- **THEN** `semantic.diff` has no freshness change and, with nothing else changed, stays
  `semantic.unchanged`
