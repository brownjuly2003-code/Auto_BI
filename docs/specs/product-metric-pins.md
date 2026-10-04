# Capability: A product's metric pins in the candidate snapshot

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

DECISIONOPS_ARCHITECTURE §7.1 makes a ref the pin of the exact domain version a run was verified
against (`metric://sales/gross_margin_pct@3`), and invariant 4 has every run fix the exact
versions of the product spec and of the semantic snapshots it verifies. `decision validate`
resolves each metric pin of a product against the metric contracts found in the validated paths
and blocks a pin that is unknown there or whose version has moved (`metric_ref.unknown`,
`metric_ref.version_mismatch`). The release gate verifies a candidate semantic ref, which carries
its own metric contracts (`<candidate>/metrics/`). Three places look a pinned metric up in a
snapshot by its id without the version — the reference-SQL compiler, product impact and the
narrowing of verified assets — and each leaves "which pin still resolves" to validation, which
never reads a snapshot.

Until now nothing compared the pin with the candidate snapshot (reproduced through the inputs of
`decision verify` at HEAD `40e7a80`, 2026-10-03):

- the product and the validated contract at `metric://sales/gross_margin_pct@3`, the candidate
  ref holding version 4 of that metric: `contracts.validate` passed as `contracts.clean`, the
  reference SQL was compiled for `metric://sales/gross_margin_pct@4` under subject refs naming
  `@3`, and the verdict was `pass_with_advisory`;
- neither semantic ref holding the metric at all: the same clean `contracts.validate`,
  `sql.compile` reporting `sql.not_compiled` as an advisory, verdict `pass_with_advisory`;
- the candidate ref holding two contracts of the metric id, versions 4 and 3: no gap,
  `semantic.diff` reporting `semantic.unchanged`, verdict `pass_with_advisory` — the diff reads
  the last contract of an id and the reference-SQL compiler the first.

`decision validate` has no snapshot and is not changed by this capability; its issue codes keep
their meaning. Every other check of the gate runs as before and reports its own facts: a pin that
does not resolve does not skip the diff, the impact, the reference SQL or the engine questions.

A message or detail quoted in a scenario below may be wrapped across lines of this file; each is
one line of text, with a single space where it wraps here.

## Requirement: `contracts.validate` resolves every metric pin of the product in the candidate snapshot

For every ref in the product's `scope.metrics`, in the order the product declares them, the gate
SHALL take the metric nodes of the candidate snapshot whose id is the ref without its version and
grade the pin by them:

| the candidate snapshot holds | outcome | issue |
|---|---|---|
| exactly one such node, at the pinned version | `match` | none |
| exactly one such node, at another version | `version_mismatch` | `metric_ref.candidate_version_mismatch`, block |
| more than one such node, whatever their versions | `ambiguous` | `metric_ref.candidate_ambiguous`, block |
| no such node, and the snapshot represents its source | `absent` | `metric_ref.candidate_absent`, block |
| no such node, and the snapshot carries the gap `gap.contract_invalid` | `not_compared` | none |

A candidate snapshot whose metric contract did not load does not represent its source: the
missing node may be the contract that failed to parse, so the gate SHALL NOT report the metric as
absent — `semantic.gaps` already blocks that run and names the file. A pin whose metric that
snapshot does hold is graded by the rows above like any other.

Each issue SHALL have the pin exactly as the product writes it as its `subject`, the product's
own ref as its `source`, and this `message`, where `<id>` is the metric id without the version,
`<p>` the pinned version and the versions held are written in ascending order, joined by `, `:

- `metric_ref.candidate_version_mismatch`:
  `the candidate snapshot holds version <v> of <id>; the product pins version <p>`
- `metric_ref.candidate_ambiguous`:
  `the candidate snapshot holds <n> contracts of <id> (versions <v1>, <v2>); the pin to version <p> does not resolve to one of them`
- `metric_ref.candidate_absent`:
  `the candidate snapshot holds no metric <id>; the product pins version <p>`

The sealed payload of the check SHALL keep every key of the validation report it was given. Its
`issues` SHALL hold the validation report's own issues, unchanged and in their order, followed by
the pin issues in the product's declared order; its `verdict` SHALL be the verdict of all those
issues. It SHALL also carry `candidate_pins`: one entry per pin in the product's declared order
with `ref` (the pin as written), `candidate_versions` (the version of every such node, ascending,
a version held twice written twice, empty when there is none) and `outcome` (one of the five
names above).

The check's status SHALL be graded from all those issues, and its summary code SHALL be
`contracts.clean` only when there is none and `contracts.issues` otherwise. Its detail SHALL be
`<n> metric contract(s) resolved, <m> issue(s); <k> of <t> metric pin(s) match the candidate snapshot`,
where `<n>` counts the metric contracts of the validation report as before, `<m>` counts all the
issues, `<t>` the product's pins and `<k>` those with the outcome `match`. When a pin does not
match, the detail SHALL continue with `: ` and one clause per such pin, in declared order, joined
by `; `:

- `version_mismatch`: `it holds version <v> of <id>, pinned at version <p>`
- `ambiguous`: `it holds <n> contracts of <id> (versions <v1>, <v2>), pinned at version <p>`
- `absent`: `it holds no <id>, pinned at version <p>`
- `not_compared`: `<ref> was not compared, because a metric contract of the candidate did not load`

The base snapshot SHALL NOT be compared: a historical base may hold an older version of the
metric, several contracts of it, or none. Metric nodes whose id no pin of this product names
SHALL NOT produce an issue. The validation report handed to the gate SHALL NOT be modified.
Because the same inputs can now yield `block` where the verifier before it passed, the verifier
version and the check version SHALL be bumped; historical bundles are not rewritten.

### Scenario: a candidate at another version blocks the release

- **GIVEN** a product pinning `metric://sales/gross_margin_pct@3`, a validated contract set
  holding version 3, and base and candidate refs both holding version 4 of that metric
- **WHEN** the gate runs
- **THEN** `contracts.validate` is block with `contracts.issues`; its payload lists one issue
  `metric_ref.candidate_version_mismatch` (block) whose subject is the pin, whose source is the
  product ref and whose message is `the candidate snapshot holds version 4 of
  metric://sales/gross_margin_pct; the product pins version 3`; `candidate_pins` is one entry with
  `candidate_versions` `[4]` and the outcome `version_mismatch`; the payload's `verdict` is
  `block`; the detail is `1 metric contract(s) resolved, 1 issue(s); 0 of 1 metric pin(s) match
  the candidate snapshot: it holds version 4 of metric://sales/gross_margin_pct, pinned at
  version 3`; the verdict of the run is block and `reports/summary.md` names the check with that
  detail under "What stops the release"

### Scenario: a candidate without the metric blocks the release

- **GIVEN** the same product and validated contract set, and base and candidate refs that hold
  no metric contract at all
- **WHEN** the gate runs
- **THEN** `contracts.validate` is block with one issue `metric_ref.candidate_absent` whose
  message is `the candidate snapshot holds no metric metric://sales/gross_margin_pct; the product
  pins version 3`; `candidate_pins` has `candidate_versions` `[]` and the outcome `absent`; the
  detail ends with `0 of 1 metric pin(s) match the candidate snapshot: it holds no
  metric://sales/gross_margin_pct, pinned at version 3`; the verdict of the run is block

### Scenario: a matching pin keeps the check clean

- **GIVEN** the product, the validated contract set, the base and the candidate all at version 3
- **WHEN** the gate runs
- **THEN** `contracts.validate` is pass with `contracts.clean`, its payload has no issue and the
  verdict `pass`, `candidate_pins` is one entry with `candidate_versions` `[3]` and the outcome
  `match`, the detail is `1 metric contract(s) resolved, 0 issue(s); 1 of 1 metric pin(s) match
  the candidate snapshot`, and the verdict of the run is what it was before this capability

### Scenario: an older base is not compared

- **GIVEN** a base ref holding version 2 of the metric and a candidate ref holding version 3,
  the product pinning `@3`
- **WHEN** the gate runs
- **THEN** `contracts.validate` is pass with `contracts.clean` and no pin issue;
  `semantic.diff` still lists `metric.version_changed`

### Scenario: a base with several contracts or none is not compared

- **GIVEN** a base ref holding two contracts of the metric id, or none, and a candidate ref
  holding exactly version 3
- **WHEN** the gate runs
- **THEN** `contracts.validate` has no pin issue and its `candidate_pins` outcome is `match`

### Scenario: a product re-pointed together with the candidate passes

- **GIVEN** a product pinning `metric://sales/gross_margin_pct@4`, a validated contract set
  holding version 4, a base ref holding version 3 and a candidate ref holding version 4
- **WHEN** the gate runs
- **THEN** `contracts.validate` is pass with `contracts.clean`, and `candidate_pins` has
  `candidate_versions` `[4]` and the outcome `match`

### Scenario: two contracts of the pinned metric are ambiguous

- **GIVEN** a candidate ref holding two contracts of `metric://sales/gross_margin_pct`, versions
  4 and 3, in either file order, the product pinning `@3`
- **WHEN** the gate runs
- **THEN** `contracts.validate` is block with one issue `metric_ref.candidate_ambiguous` whose
  message is `the candidate snapshot holds 2 contracts of metric://sales/gross_margin_pct
  (versions 3, 4); the pin to version 3 does not resolve to one of them`; `candidate_pins` has
  `candidate_versions` `[3, 4]` and the outcome `ambiguous`; the detail ends with `it holds 2
  contracts of metric://sales/gross_margin_pct (versions 3, 4), pinned at version 3`

### Scenario: two contracts at the pinned version are ambiguous too

- **GIVEN** a candidate ref holding two contracts of the metric id, both at version 3
- **WHEN** the gate runs
- **THEN** `contracts.validate` is block with `metric_ref.candidate_ambiguous`, the message
  naming `2 contracts` and `(versions 3, 3)`, and `candidate_versions` is `[3, 3]`

### Scenario: a metric the product does not pin is not compared

- **GIVEN** a candidate ref that also holds another metric, `metric://sales/revenue`, at a
  version no contract set knows, or twice, while the product pins only
  `metric://sales/gross_margin_pct@3` and the candidate holds exactly that
- **WHEN** the gate runs for that product
- **THEN** `contracts.validate` is pass with `contracts.clean` and `candidate_pins` has one
  entry, the product's own pin

### Scenario: several pins are graded one by one in declared order

- **GIVEN** a product pinning three metrics, validated contracts for all three, and a candidate
  ref that holds the first at its pinned version, the second at another version and no contract
  of the third
- **WHEN** the gate runs
- **THEN** the payload's pin issues are `metric_ref.candidate_version_mismatch` for the second
  pin and `metric_ref.candidate_absent` for the third, in that order; `candidate_pins` has three
  entries in declared order with the outcomes `match`, `version_mismatch`, `absent`; the detail
  says `1 of 3 metric pin(s) match the candidate snapshot` followed by the two clauses in that
  order joined by `; `

### Scenario: the validation report's own issues are kept

- **GIVEN** a validated contract set in which the pinned metric is a draft, so the validation
  report carries `metric.draft` (review), and a candidate ref holding version 4
- **WHEN** the gate runs
- **THEN** the payload's `issues` are `metric.draft` with its severity, subject, source and
  message unchanged, then `metric_ref.candidate_version_mismatch`; the detail counts `2 issue(s)`;
  the status is block; and with a candidate at version 3 the payload's `issues` are `metric.draft`
  alone and the status is review, as before this capability

### Scenario: a candidate contract that did not load is not reported as an absent metric

- **GIVEN** a candidate ref whose only contract file of the pinned metric does not parse
- **WHEN** the gate runs
- **THEN** `semantic.gaps` is block and `semantic.diff` is `check.not_run` as before;
  `contracts.validate` is pass with `contracts.clean` and no issue; `candidate_pins` has
  `candidate_versions` `[]` and the outcome `not_compared`; the detail is `1 metric contract(s)
  resolved, 0 issue(s); 0 of 1 metric pin(s) match the candidate snapshot:
  metric://sales/gross_margin_pct@3 was not compared, because a metric contract of the candidate
  did not load`

### Scenario: a snapshot with an unloaded contract still names a version it does hold

- **GIVEN** a candidate ref holding version 4 of the pinned metric beside another contract file
  that does not parse
- **WHEN** the gate runs
- **THEN** `contracts.validate` is block with `metric_ref.candidate_version_mismatch`, and
  `semantic.gaps` is block as before

### Scenario: the validation report is not modified and `decision validate` is unchanged

- **GIVEN** the inputs of the first scenario
- **WHEN** the gate runs, and `decision validate` runs over the same contract paths
- **THEN** the validation report handed to the gate still has no issue and the verdict `pass`,
  and `decision validate` reports the same: none of the three `metric_ref.candidate_*` codes
  appears in it

## Known boundaries

- The contents of the validated contract and of the candidate's metric at the same version are
  not compared: the validation rules that read a contract — dimensions, status, policy — grade the
  copy found in the validated paths.
- The metric pins of verified assets (`metric_refs`) are not compared with the candidate
  snapshot.
- A semantic ref holding two contracts of one metric id is not a snapshot gap: for a metric no
  product in the run pins, the diff and the reference-SQL compiler may still read different
  contracts of it.
