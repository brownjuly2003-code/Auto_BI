# Capability: Two contracts of one metric id in a semantic ref

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

DECISIONOPS_ARCHITECTURE principle 6 says semantics the canonical graph cannot carry becomes an
explicit gap. The metrics of a native snapshot are the contracts under the ref's metrics
directory, read until verifier 117 in the order of their paths, and the graph holds a node for each: the graph keeps
one node to an id for tables, fields and joins
([what a model declares that the graph cannot hold](model-declarations-snapshot.md)), not for
metrics. A ref that held two contracts of one metric id was read two ways. The semantic diff kept
the last node of the id, a lookup of the metric by its id returned the first, and which node
stood last was the order of the paths. With an older contract of the pinned metric beside the
current one in the base, verifier 110 blocked the release when the older file stood in `metrics/`
and gave `pass_with_advisory` when the same bytes stood in `metrics/archive/`: `semantic.diff`
compared the older contract in the first layout and the current one in the second.

A reason quoted in a scenario below may be wrapped across lines of this file; each is one line of
text, with a single space where it wraps here.

## Requirement: a metric id that more than one contract defines is a blocking gap

For every metric id that two or more of the ref's metric contracts define, the snapshot SHALL
carry one gap `gap.metric_duplicate` (block) at `metrics.<id>`, with the reason
`metric is defined by <n> contracts (versions <v1>, <v2>): which of them the ref means is not known`,
where `<n>` counts the contracts and the versions are those of every one of them, ascending as
numbers, a version held twice written twice, joined by `, `. The gap has no `source_value_ref`. Contracts
that are alike byte for byte are counted each. A contract that did not load is no contract of the
id: it stays the gap `gap.contract_invalid` it was. The gaps SHALL stand after the other gaps of
the snapshot, one to an id, in the order of the ids, whatever the order of the files.

Every contract SHALL stay a metric node of the graph, so that a pin names the versions it was
given: `contracts.validate` still names a pin of such an id in the candidate `ambiguous`
([product metric pins](product-metric-pins.md)). The graph is the one it was, and the native
projection version is not bumped.

### Scenario: two contracts of one id are a blocking gap and two nodes

- **GIVEN** a ref holding `metrics/gross_margin_pct.yaml` at version 3 and
  `metrics/archive/gross_margin_pct.yaml` at version 4, both of
  `metric://sales/gross_margin_pct`
- **WHEN** the snapshot is built
- **THEN** its only gap is `gap.metric_duplicate` at `metrics.metric://sales/gross_margin_pct`
  with the reason `metric is defined by 2 contracts (versions 3, 4): which of them the ref means
  is not known`, and the graph holds a node at version 3 and one at version 4

### Scenario: copies of a contract are one gap that counts each of them

- **GIVEN** three copies, alike byte for byte, of the contract at version 3, in `metrics/a/`,
  `metrics/b/` and `metrics/c/`
- **WHEN** the snapshot is built
- **THEN** its only gap names `3 contracts (versions 3, 3, 3)`, and the graph holds three nodes

### Scenario: the versions are named in the order of numbers

- **GIVEN** contracts of `metric://sales/gross_margin_pct` at versions 2, 10 and 2, in
  `metrics/a/`, `metrics/b/` and `metrics/c/`
- **WHEN** the snapshot is built
- **THEN** its only gap names `3 contracts (versions 2, 2, 10)`

### Scenario: the gaps of two ids follow the ids, not the files

- **GIVEN** contracts in `metrics/a/` to `metrics/f/`: `metric://sales/other_pct`,
  `metric://sales/gross_margin_pct`, `metric://sales/other_pct` again,
  `metric://sales/gross_margin_pct` again, a file that does not load, and a metric that reads a
  column the model does not declare
- **WHEN** the snapshot is built
- **THEN** its gaps are `gap.contract_invalid`, `gap.metric_field_missing`, then the
  `gap.metric_duplicate` of `metric://sales/gross_margin_pct` and that of
  `metric://sales/other_pct`, each naming the versions `3, 3`

## Requirement: the six checks a contract that did not load leaves not run are not run on such a ref

When the base or the candidate snapshot carries `gap.metric_duplicate`, `semantic.gaps` SHALL
block with `semantic.gaps_present`, its payload listing the gap under the side that carries it.
The checks that a metric contract which did not load leaves not run — `semantic.diff`,
`impact.products`, `assets.invalidated`, `results.reconcile`, `results.provenance` and
`sql.compile` — SHALL NOT be run either: each SHALL be `check.not_run` (review) with the detail
`a semantic ref holds more than one contract of a metric id, so it does not say which of them a diff should compare`.
When a snapshot of the run carries `gap.contract_invalid` as well, the detail SHALL be the reason
given for that gap,
`a metric contract did not load, so the snapshot does not represent its source; a diff would report the parse failure as a removed metric`,
then `; ` and the reason above. No other check SHALL be `check.not_run`, and `evidence.bundle`
re-verifies the bundle as before. The verdict is block.

`contracts.validate` SHALL grade the product's pins as before. A pin of a metric the candidate does
not hold SHALL stay `absent` beside two contracts of another metric: they say nothing of whether
a contract of the pinned metric loaded, and only `gap.contract_invalid` makes such a pin
`not_compared`.

A run over a ref that holds such an id produced a bundle and now produces another, graded
otherwise, so `VERIFIER_VERSION` and `CHECK_VERSION` SHALL be bumped.

### Scenario: an older contract beside the pinned one blocks wherever its file lies

- **GIVEN** a base holding `metrics/gross_margin_pct.yaml` at version 3 and an older contract of
  `metric://sales/gross_margin_pct` at version 2 with another formula, once in `metrics/` and once
  in `metrics/archive/`, and a candidate holding version 3 alone
- **WHEN** the gate runs on each
- **THEN** both runs block: `semantic.gaps` lists `gap.metric_duplicate` with the versions `2, 3`
  for the base and no gap for the candidate, the six checks are `check.not_run` with the reason
  of a duplicate, and `evidence.bundle` passes

### Scenario: either ref, any metric, other versions or copies

- **GIVEN** a base or a candidate holding `metrics/<name>.yaml` and `metrics/archive/<name>.yaml`
  of one metric id — the pinned `metric://sales/gross_margin_pct`, or `metric://sales/revenue`,
  which no pin names — at versions 3 and 4, or alike byte for byte
- **WHEN** the gate runs
- **THEN** the snapshot of that ref carries one `gap.metric_duplicate` with the versions `3, 4` or
  `3, 3`, the other snapshot carries none, the graph of that ref holds a node for each contract,
  the six checks are `check.not_run`, and `contracts.validate` names
  `metric_ref.candidate_ambiguous` only where the candidate holds two contracts of the pinned
  metric

### Scenario: a pin the candidate does not hold stays absent beside another metric's duplicate

- **GIVEN** a candidate holding no contract of the pinned `metric://sales/gross_margin_pct` and two
  contracts of `metric://sales/revenue`
- **WHEN** the gate runs
- **THEN** `contracts.validate` names the pin `absent` with `metric_ref.candidate_absent`, and the
  six checks are `check.not_run`

### Scenario: a contract that did not load and a duplicate are both named

- **GIVEN** a candidate holding `metrics/gross_margin_pct.yaml`, a copy of it in
  `metrics/archive/`, and `metrics/broken.yaml`, which names `metric://sales/gross_margin_pct`
  and does not load
- **WHEN** the gate runs
- **THEN** the candidate's gaps are `gap.contract_invalid`, then `gap.metric_duplicate` with the
  versions `3, 3`, and the detail of each of the six checks is the reason of a contract that did
  not load, `; `, then the reason of a duplicate

### Scenario: the verified assets repository still names the pin; the gate does not ask it

- **GIVEN** a candidate holding two contracts of `metric://sales/gross_margin_pct`, at versions 3
  and 4 in either order of their files or both at version 3, and a verified asset pinning `@3`
- **WHEN** `VerifiedAssetRepository.resolve_metric_pins` is given the candidate snapshot, and the
  gate runs
- **THEN** the repository names the pin `ambiguous` with `asset.metric_ref_ambiguous` and its
  detail ([verified asset metric pins](verified-asset-metric-pins.md)); in the run,
  `semantic.gaps` is block, `assets.invalidated` is `check.not_run`, and no file of the bundle
  holds an `asset.metric_ref_` finding

## Known boundaries

- Until verifier 117 the nodes of an id stood in the graph in the order of their files, so the
  canonical digest of a ref, and with it the run id, differed when two contracts of an id differed
  and the file of one of them moved to where it was read before or after the other. Since
  verifier 117 they stand in the order of versions, then of the digests of their bytes, wherever
  the files lie ([contract read order](contract-read-order.md)). The verdict did not move.
- `contracts.validate`, the environment checks and `bi.contract` are run as before. With an
  engine configured, the environment checks ask about each metric the product pins by its id,
  and of two contracts of it in the candidate about the one a lookup of the id finds — until
  verifier 117 the one whose file was read first, since then the lowest version, then the lowest
  digest of bytes: whether `dwh.cost` asks the engine for the plan of a budget only one of them
  declares, and so what it reports, follows that order. Where the candidate holds two contracts
  of a pinned metric, the run is blocked by both `contracts.validate` and `semantic.gaps`
  whichever of them a check reads.
- `asset.metric_ref_ambiguous` is no longer reached through the gate on a native snapshot: a
  candidate that holds two contracts of a metric carries the gap, and `assets.invalidated` is not
  run. `VerifiedAssetRepository.resolve_metric_pins` still names such a pin for a snapshot handed
  to it.
- `diff_snapshots`, the impact resolver and the reference-SQL compiler are not changed: handed
  such a snapshot directly, they read it as they did.
