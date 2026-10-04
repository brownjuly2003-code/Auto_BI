# Capability: A verified asset's metric pins in the candidate snapshot

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

DECISIONOPS_ARCHITECTURE §7.1 makes a ref the pin of the exact domain version a run was verified
against, and §7.5 has a verified asset carry the metric refs it was reviewed for. Ids and refs are
separate types so that a verified claim cannot quietly re-point at a metric that moved after the
evidence was written (`auto_bi/refs.py`), and `semantic.diff` reports a moved version as
`metric.version_changed` with the words "pinned refs must be re-pointed". `contracts.validate`
resolves the product's own pins in the candidate snapshot
([product metric pins](product-metric-pins.md)). The pins of a verified asset were resolved
nowhere: the assets are narrowed to the product by metric id without the version,
`assets.invalidated` reads an asset's metrics by id as well, and an advisory change — a moved
version among them — invalidates nothing.

Reproduced through an in-memory gate run at HEAD `e97eafc` (2026-10-04), the asset's result
attested and equal to its expectation in every case:

- base, candidate, product and validated contract at version 3 of
  `metric://sales/gross_margin_pct`, the asset pinning `@99`: `assets.invalidated` passed as
  `assets.valid` with `1 verified asset(s) still stand as evidence`, `results.reconcile` sealed
  `result.equivalent`, and the verdict was `pass_with_advisory`;
- the base at version 3, the candidate, the product and the validated contract at version 4, the
  asset still pinning `@3`: `semantic.diff` was advisory, `assets.invalidated` passed as
  `assets.valid`, verdict `pass_with_advisory`;
- the asset also pinning a metric no snapshot holds: the same clean `assets.invalidated`.

Which assets a run is given is not changed by this capability, and every other check reports its
own facts as before: a pin that does not resolve does not skip the comparison of the asset's
result or the grading of where that result came from.

A detail quoted in a scenario below may be wrapped across lines of this file; each is one line of
text, with a single space where it wraps here.

## Requirement: `assets.invalidated` resolves every metric pin of every verified asset in the candidate snapshot

For every verified asset the gate was given, in the order of their ids, and for every ref in the
asset's `metric_refs`, in the order the asset declares them, the gate SHALL take the metric nodes
of the candidate snapshot whose id is the ref without its version and grade the pin by them. A
ref the asset writes more than once SHALL be graded once, at its first position.

| the candidate snapshot holds | outcome | finding |
|---|---|---|
| exactly one such node, at the pinned version | `match` | none |
| exactly one such node, at another version | `version_mismatch` | `asset.metric_ref_version_mismatch`, block |
| more than one such node, whatever their versions | `ambiguous` | `asset.metric_ref_ambiguous`, block |
| no such node | `absent` | `asset.metric_ref_absent`, block |

Each finding SHALL name the asset by the asset's own ref and SHALL carry this detail, where `<id>`
is the metric id without the version, `<p>` the pinned version, and the versions held are written
in ascending order, joined by `, `:

- `asset.metric_ref_version_mismatch`:
  `the candidate snapshot holds version <v> of <id>, pinned at version <p>`
- `asset.metric_ref_ambiguous`:
  `the candidate snapshot holds <n> contracts of <id> (versions <v1>, <v2>), pinned at version <p>`
- `asset.metric_ref_absent`:
  `the candidate snapshot holds no <id>, pinned at version <p>`

The findings of the check SHALL be, in this order: the findings of the semantic change as before
(`asset.stale_snapshot`, `asset.metric_removed`, `asset.metric_changed`), then the pin findings in
the order of the first paragraph, then the findings about the reviewed query as before. A pin
finding is independent of the others: an asset verified against another snapshot keeps
`asset.stale_snapshot` and has its pins graded all the same, and an asset whose metric the diff
reports as removed gets both `asset.metric_removed` and `asset.metric_ref_absent`. The status SHALL
be graded from all the findings. The summary code SHALL remain the code of the worst finding, the
first of them in that order among equals, and the detail of a check with findings SHALL remain
`<asset>: <detail>` per finding, joined by `; `.

The sealed payload SHALL keep `assets` and `invalidations`, the latter holding the pin findings at
their place. It SHALL also carry `metric_pins`: one entry per graded pin, in the order of the
first paragraph, with `asset` (the asset's own ref), `ref` (the pin as written),
`candidate_versions` (the version of every such node, ascending, a version held twice written
twice, empty when there is none) and `outcome` (one of the four names above).

A check without findings SHALL keep the summary code `assets.valid`. Its detail SHALL be
`<n> verified asset(s) still stand as evidence`, continued with
`; <t> of <t> metric pin(s) match the candidate snapshot` when at least one pin was graded, `<t>`
being the number of graded pins.

The base snapshot SHALL NOT be compared: a historical base may hold an older version of the
metric, several contracts of it, or none. Which assets the gate is given SHALL NOT change: the
narrowing to the product reads metric ids without versions as before. When a metric contract of
either snapshot did not load, `assets.invalidated` stays `check.not_run` as before and no pin is
graded. `contracts.validate`, `results.reconcile` and `results.provenance` SHALL report what they
reported before. Because the same inputs can now yield `block` where the verifier before it
passed, the verifier version and the check version SHALL be bumped; historical bundles are not
rewritten.

### Scenario: a pin to a version the candidate does not hold blocks the release

- **GIVEN** the base, the candidate, the product and the validated contract set at version 3 of
  `metric://sales/gross_margin_pct`, a verified asset `asset://sales/weekly_margin_by_region@2`
  verified against the base and pinning `metric://sales/gross_margin_pct@99`, and an attested
  candidate result equal to the asset's expectation
- **WHEN** the gate runs
- **THEN** `assets.invalidated` is block with `asset.metric_ref_version_mismatch`; its detail is
  `asset://sales/weekly_margin_by_region@2: the candidate snapshot holds version 3 of
  metric://sales/gross_margin_pct, pinned at version 99`; the payload's `invalidations` is that
  one finding with the severity `block`; `metric_pins` is one entry with the asset's ref, the pin
  as written, `candidate_versions` `[3]` and the outcome `version_mismatch`; `results.reconcile`
  is pass with `result.equivalent` and `results.provenance` is pass, as before; the verdict of the
  run is block and `reports/summary.md` names the check with that detail under "What stops the
  release"

### Scenario: a version that moved past the asset blocks the release

- **GIVEN** the base at version 3, the candidate, the product and the validated contract set at
  version 4, and the asset verified against the base and still pinning `@3`
- **WHEN** the gate runs
- **THEN** `contracts.validate` is pass with `contracts.clean`; `semantic.diff` lists
  `metric.version_changed`; `assets.invalidated` is block with `asset.metric_ref_version_mismatch`
  and the detail `asset://sales/weekly_margin_by_region@2: the candidate snapshot holds version 4
  of metric://sales/gross_margin_pct, pinned at version 3`; the verdict of the run is block

### Scenario: an asset re-pointed together with the candidate passes

- **GIVEN** the base at version 3, the candidate, the product and the validated contract set at
  version 4, and the asset verified against the base and pinning `@4`
- **WHEN** the gate runs
- **THEN** `assets.invalidated` is pass with `assets.valid`; its detail is `1 verified asset(s)
  still stand as evidence; 1 of 1 metric pin(s) match the candidate snapshot`; the payload has no
  invalidation and `metric_pins` is one entry with `candidate_versions` `[4]` and the outcome
  `match`

### Scenario: a matching pin keeps the check clean

- **GIVEN** the base, the candidate, the product, the validated contract set and the asset's pin
  all at version 3, the asset verified against the base
- **WHEN** the gate runs
- **THEN** `assets.invalidated` is pass with `assets.valid` and the detail `1 verified asset(s)
  still stand as evidence; 1 of 1 metric pin(s) match the candidate snapshot`; the payload has no
  invalidation and one `metric_pins` entry with `candidate_versions` `[3]` and the outcome
  `match`; the verdict of the run is what it was before this capability

### Scenario: a base without the metric is not compared

- **GIVEN** a base ref holding no metric contract, a candidate ref holding version 3, the product
  and the asset pinning `@3`, the asset verified against that base
- **WHEN** the gate runs
- **THEN** `assets.invalidated` is pass with `assets.valid`, and `metric_pins` has the outcome
  `match`

### Scenario: an asset verified against another snapshot keeps its review and has its pins graded

- **GIVEN** an asset whose `semantic_digest` is neither snapshot's digest
- **WHEN** the gate runs with the asset pinning `@3` beside a candidate at version 3, and again
  with the asset pinning `@99`
- **THEN** the first run reports `asset.stale_snapshot` alone, review, with the detail it had
  before and one `metric_pins` entry with the outcome `match`; the second run's `invalidations`
  are `asset.stale_snapshot` then `asset.metric_ref_version_mismatch`, the status is block, the
  summary code is `asset.metric_ref_version_mismatch`, and the detail is the two findings joined
  by `; ` in that order

### Scenario: several contracts of the pinned metric are ambiguous

- **GIVEN** a candidate ref holding two contracts of `metric://sales/gross_margin_pct`, versions
  3 and 4, in either file order, and the asset pinning `@3`
- **WHEN** the gate runs
- **THEN** `assets.invalidated` is block with `asset.metric_ref_ambiguous`; the finding's detail
  is `the candidate snapshot holds 2 contracts of metric://sales/gross_margin_pct (versions 3,
  4), pinned at version 3`; `metric_pins` has `candidate_versions` `[3, 4]` and the outcome
  `ambiguous` in both file orders; and with both contracts at version 3 the detail names
  `(versions 3, 3)` and `candidate_versions` is `[3, 3]`

### Scenario: every pin of a selected asset is graded, a metric outside the product included

- **GIVEN** the asset pinning `metric://sales/gross_margin_pct@3` and `metric://sales/revenue@3`
  in that order, the product pinning only the first, and a candidate ref holding
  `gross_margin_pct` at version 3
- **WHEN** the gate runs with a candidate that holds no `metric://sales/revenue`, with one that
  holds it at version 3, and with one that holds it at version 9
- **THEN** the first run is block with `asset.metric_ref_absent` and the finding's detail `the
  candidate snapshot holds no metric://sales/revenue, pinned at version 3`, its `metric_pins` two
  entries in declared order with the outcomes `match` and `absent`; the second run is pass with
  the detail `1 verified asset(s) still stand as evidence; 2 of 2 metric pin(s) match the
  candidate snapshot`; the third run is block with `asset.metric_ref_version_mismatch` naming
  version 9 and the pin to version 3; `contracts.validate` is pass with `contracts.clean` in all
  three

### Scenario: a removed metric keeps its finding and its pin is named absent

- **GIVEN** a base ref holding version 3, a candidate ref holding no metric contract, and the
  asset verified against the base and pinning `@3`
- **WHEN** the gate runs
- **THEN** the `invalidations` of `assets.invalidated` are `asset.metric_removed` then
  `asset.metric_ref_absent`, both block; the summary code is `asset.metric_removed` as before; the
  detail is `asset://sales/weekly_margin_by_region@2: metric gone:
  metric://sales/gross_margin_pct; asset://sales/weekly_margin_by_region@2: the candidate snapshot
  holds no metric://sales/gross_margin_pct, pinned at version 3`

### Scenario: a changed metric keeps its review beside the pin finding

- **GIVEN** a candidate whose contract changes the formula of the metric, the asset verified
  against the base and pinning `@3`
- **WHEN** the gate runs with the candidate contract still at version 3, and again with it at
  version 4 (the product and the validated contract set at version 4 too)
- **THEN** the first run reports `asset.metric_changed` alone, review, as before; the second
  run's `invalidations` are `asset.metric_changed` then `asset.metric_ref_version_mismatch`, the
  status is block and the summary code is `asset.metric_ref_version_mismatch`

### Scenario: a finding about the reviewed query follows the pin finding

- **GIVEN** an asset whose reviewed query reads a mart the candidate does not declare, pinning
  `@99`, beside a candidate at version 3
- **WHEN** the gate runs
- **THEN** the `invalidations` are `asset.metric_ref_version_mismatch` then
  `asset.query_ungoverned`, the status is block and the summary code is
  `asset.metric_ref_version_mismatch`

### Scenario: a pin written twice is graded once

- **GIVEN** the asset's `metric_refs` holding `metric://sales/gross_margin_pct@99` twice, beside a
  candidate at version 3
- **WHEN** the gate runs
- **THEN** `invalidations` has one `asset.metric_ref_version_mismatch` and `metric_pins` one entry

### Scenario: two pins of one asset that do not resolve are reported in declared order

- **GIVEN** the asset pinning `metric://sales/revenue@5` and `metric://sales/gross_margin_pct@99`
  in that order, beside a candidate that holds `gross_margin_pct` at version 3 and no
  `metric://sales/revenue`
- **WHEN** the gate runs
- **THEN** the `invalidations` are `asset.metric_ref_absent` then
  `asset.metric_ref_version_mismatch`, both block; the summary code is `asset.metric_ref_absent`,
  the first of the two; the detail is `asset://sales/weekly_margin_by_region@2: the candidate
  snapshot holds no metric://sales/revenue, pinned at version 5;
  asset://sales/weekly_margin_by_region@2: the candidate snapshot holds version 3 of
  metric://sales/gross_margin_pct, pinned at version 99`; `metric_pins` holds the two entries in
  that order

### Scenario: several assets are graded in the order of their ids

- **GIVEN** two verified assets of the product's metric, `asset://sales/a_margin@1` pinning `@99`
  and `asset://sales/weekly_margin_by_region@2` pinning `@98`, both verified against the base,
  beside a candidate at version 3, the asset files written in either order
- **WHEN** the gate runs
- **THEN** `metric_pins` and the two `asset.metric_ref_version_mismatch` findings name
  `asset://sales/a_margin@1` first, and the detail joins the two findings by `; ` in that order

### Scenario: an asset without metric refs has no pin to grade

- **GIVEN** a gate whose only asset is a clarification or a refusal with no `metric_refs`,
  verified against the base
- **WHEN** the gate runs
- **THEN** `assets.invalidated` is pass with `assets.valid`, the detail is `1 verified asset(s)
  still stand as evidence` with nothing after it, and `metric_pins` is empty

### Scenario: an asset of another product's metric stays out of the run

- **GIVEN** an assets directory holding only an asset that pins `metric://sales/revenue@99`, and a
  product that pins `metric://sales/gross_margin_pct@3`
- **WHEN** `decision verify` runs for that product
- **THEN** the payload's `assets` and `metric_pins` are empty, `assets.invalidated` is pass with
  `assets.valid` and the detail `0 verified asset(s) still stand as evidence`

### Scenario: a contract that did not load leaves the check not run

- **GIVEN** a candidate ref whose contract file of the pinned metric does not parse, and the
  asset pinning `@3`
- **WHEN** the gate runs
- **THEN** `semantic.gaps` is block and `assets.invalidated` is `check.not_run` as before; no
  `asset.metric_ref_absent` appears anywhere in the run

## Known boundaries

- A pin is not compared with the snapshot the asset says it was verified against
  (`semantic_digest`): an asset re-pointed to a new version without being re-verified resolves in
  the candidate and is graded by the semantic change alone.
- The contents of the candidate's metric at the pinned version are not compared with what the
  asset was reviewed for; a changed definition reaches the asset through `semantic.diff`
  (`asset.metric_changed`) when the asset was verified against the base, and through
  `asset.stale_snapshot` otherwise.
- A semantic ref holding two contracts of a metric id that neither the product nor a selected
  asset pins is still not a snapshot gap.
