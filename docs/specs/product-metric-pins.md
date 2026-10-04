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

Until the first requirement nothing compared the pin with the candidate snapshot (reproduced
through the inputs of `decision verify` at HEAD `40e7a80`, 2026-10-03):

- the product and the validated contract at `metric://sales/gross_margin_pct@3`, the candidate
  ref holding version 4 of that metric: `contracts.validate` passed as `contracts.clean`, the
  reference SQL was compiled for `metric://sales/gross_margin_pct@4` under subject refs naming
  `@3`, and the verdict was `pass_with_advisory`;
- neither semantic ref holding the metric at all: the same clean `contracts.validate`,
  `sql.compile` reporting `sql.not_compiled` as an advisory, verdict `pass_with_advisory`;
- the candidate ref holding two contracts of the metric id, versions 4 and 3: no gap,
  `semantic.diff` reporting `semantic.unchanged`, verdict `pass_with_advisory` — the diff reads
  the last contract of an id and the reference-SQL compiler the first.

The first requirement compares the version a pin names. It left the contents of that version
open: the validation rules that read a contract — dimensions, status, policy — grade the copy
found in the validated paths, while the gate diffs, compiles and reconciles the copy the
candidate ref holds. Reproduced through the inputs of `decision verify` at HEAD `d8f6761`,
2026-10-04, with the validated contract `metric://sales/gross_margin_pct@3` active, declaring
the dimensions `region` and `category` and the numerator `gross_margin`:

- base and candidate refs both holding version 3 as a deprecated contract with the dimension
  `region` alone and the numerator `revenue`: `contracts.validate` passed as `contracts.clean`,
  `semantic.diff` reported `semantic.unchanged`, the reference SQL was compiled from the
  candidate's formula under the product's pin, and the verdict was `pass_with_advisory` — while
  `decision validate` over the product and the candidate's own contract blocks with
  `dimension.not_allowed`;
- both refs holding version 3 with another numerator alone, or with another description alone:
  the same clean run.

The owner-review packages keep the validated contract equal to the base ref while the candidate
changes the metric's timezone at the same version
(`docs/pilot/2026-09-12-owner-review-live-r3`). That difference is the change under review:
`semantic.diff` names it and `impact.products` grades it. The second requirement therefore
reports a difference only when the base snapshot does not hold the validated definition either.

The second requirement weighs a difference by the semantic diff's classification, and that
classification is not the validation rules'. Reproduced through the inputs of `decision verify`
at HEAD `7f35c65`, 2026-10-04:

- the base ref holding the validated contract and the candidate ref holding version 3 with the
  status `draft`: `contracts.validate` passed as `contracts.clean`, `semantic.diff` listed
  `metric.status_changed` as an advisory, and the verdict was `pass_with_advisory`;
- a validated contract declaring no governing policy, base and candidate refs both holding
  version 3 governed by `policy://finance/close@2`: `contracts.validate` was an advisory
  (`metric_ref.candidate_content_mismatch` naming `metric.policy_added`), and the verdict was
  `pass_with_advisory`.

`decision validate` over the product and the candidate's own contract answers `metric.draft` in
the first case and `policy.mismatch` in the second, each a review. The third requirement
therefore answers the rules that read a metric contract on the candidate's definition as well.

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

A pin of the first row is graded further by the second requirement, which reports a node that
is another definition than the validated contract as `content_mismatch` in place of `match`.

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
names above, or the second requirement's `content_mismatch`).

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

The base snapshot SHALL NOT be graded: a historical base may hold an older version of the
metric, several contracts of it, or none, and none of that is an issue (the second requirement
reads the base for one question only — whether it holds the validated definition). Metric nodes
whose id no pin of this product names SHALL NOT produce an issue. The validation report handed to
the gate SHALL NOT be modified. Because the same inputs can now yield `block` where the verifier
before it passed, the verifier version and the check version SHALL be bumped; historical bundles
are not rewritten.

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
  the status is block; and with a candidate holding that same draft at version 3 the payload's
  `issues` are `metric.draft` alone and the status is review, as before this capability

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

## Requirement: `contracts.validate` compares the validated contract of a pinned metric with the candidate's definition

For every pin whose candidate snapshot holds exactly one node of the metric, at the pinned
version, the gate SHALL compare that node with the *validated definition*: the canonical metric
node of the contract the validated set holds under the metric's id — the node a snapshot carries
for that contract. Of two contracts of one id in the validated paths the set holds the one the
validation rules graded, and the report blocks with `contract.duplicate_id`. There is nothing to
compare, and the pin keeps the outcome `match`, when the validated set holds no contract of that
id or holds it at another version than the pin (`decision validate` blocks both itself), or when
the gate was given no validated contracts.

The *differences* between two definitions of a metric SHALL be the changes the semantic diff
names for one metric with the first definition on its base side and the second on its candidate
side, each with the severity the diff classifies it with (DECISIONOPS_ARCHITECTURE §8.3), in the
order the diff lists them. Two definitions with no such change are one definition: dimensions or
governing policies written in another order, a join path or a quality precondition declared
twice, or the keys of the file in another order do not separate them.

| the candidate node, against the validated definition | the base snapshot | outcome | issue |
|---|---|---|---|
| no differences | not read | `match` | none |
| differences | holds exactly one node of the metric id, without differences from the validated definition | `match` | none |
| differences | anything else: no such node, several, or one with differences from the validated definition | `content_mismatch` | `metric_ref.candidate_content_mismatch` |

A candidate that changes the validated definition the base holds is the change under review:
`semantic.diff` names it and `impact.products` grades it, and this check SHALL NOT report it a
second time. A difference that the base does not account for is not named by any other check,
and the validation report describes a contract the release does not hold.

The issue's severity SHALL be `block` when the diff classifies any of the differences from the
validated definition to the candidate node as `review` or `block`, and `advisory` when it
classifies every one of them as `advisory`. This is the weight `impact.products` gives the same
change of a metric that a governed product depends on, and the pin is that dependency: a
difference is graded the same whether the base holds the validated definition or not, and only
the check that reports it differs. The issue's `subject` SHALL be the pin as the product writes
it, its `source` the product's own ref, and its `message`:

`the candidate snapshot holds another definition of <id> at version <p> than the validated contract (<codes>), and the base snapshot does not hold the validated definition`

where `<id>` is the metric id without the version, `<p>` the pinned version and `<codes>` the
codes of those differences, in the diff's order, joined by `, `.

The pin's `candidate_pins` entry SHALL carry the outcome `content_mismatch` and one more key,
`differences`: those codes in that order. An entry with any other outcome SHALL have exactly the
three keys of the first requirement. A pin with the outcome `content_mismatch` does not count as
matching in the detail, whatever its severity, and its clause, placed among the other clauses in
the product's declared order, SHALL be:

`it holds another definition of <id> at version <p> than the validated contract (<codes>)`

The issue takes its place among the pin issues in the product's declared order, after the
validation report's own issues. The rows of the first requirement for another version, several
contracts, an absent metric and a contract that did not load SHALL be graded first and keep their
issues, messages, clauses and entries. The validation report handed to the gate SHALL NOT be
modified, `decision validate` SHALL NOT change, and every other check SHALL run as before.
Because the same inputs can now yield `block` or an advisory where the verifier before it passed,
the verifier version and the check version SHALL be bumped; historical bundles are not rewritten.

In the scenarios below the product pins `metric://sales/gross_margin_pct@3` and, unless a
scenario says otherwise, the validated contract is the active one with the dimensions `region`
and `category`, the numerator `gross_margin` over the denominator `revenue`, the timezone
`Europe/Moscow`, the freshness window `PT6H` and the governing policy the product verifies under.

### Scenario: a validated contract that neither snapshot holds blocks the release

- **GIVEN** base and candidate refs both holding version 3 as a deprecated contract with the
  dimension `region` alone and the numerator `revenue`
- **WHEN** the gate runs
- **THEN** `contracts.validate` is block with `contracts.issues`; the first issue of its payload
  is `metric_ref.candidate_content_mismatch` (block) whose subject is the pin, whose source is
  the product ref and whose message is `the candidate snapshot holds another definition of
  metric://sales/gross_margin_pct at version 3 than the validated contract
  (metric.formula_changed, metric.dimensions_removed, metric.status_changed), and the base
  snapshot does not hold the validated definition`, followed by the two rule issues of the third
  requirement; `candidate_pins` is one entry with `candidate_versions` `[3]`, the outcome
  `content_mismatch` and `differences` `[metric.formula_changed, metric.dimensions_removed,
  metric.status_changed]`; the payload's `verdict` is `block`; the detail begins `1 metric
  contract(s) resolved, 3 issue(s); 0 of 1 metric pin(s) match the candidate snapshot: it holds
  another definition of metric://sales/gross_margin_pct at version 3 than the validated contract
  (metric.formula_changed, metric.dimensions_removed, metric.status_changed)` and ends with the
  rule clause of the third requirement; the verdict of the run is block and `reports/summary.md`
  names the check with its detail under "What stops the release"; `semantic.diff` is
  `semantic.unchanged` and `sql.compile` is `sql.compiled` as before

### Scenario: a difference that changes meaning or weakens a promise blocks

- **GIVEN** base and candidate refs both holding version 3 with one attribute other than in the
  validated contract: another numerator (`metric.formula_changed`), other grain keys
  (`metric.grain_changed`), another timezone (`metric.time_changed`), another unit precision
  (`metric.unit_changed`), another additivity (`metric.additivity_changed`), a dimension of the
  validated contract missing (`metric.dimensions_removed`), a join path of the validated
  contract missing (`metric.join_path_removed`), that join path at another cardinality
  (`metric.join_path_changed`), its governing policy missing (`metric.policy_weakened`), a lower
  classification (`metric.classification_weakened`), a status further towards retirement
  (`metric.status_changed`), a higher cost ceiling or none where the validated contract declares
  one (`metric.budget_relaxed`), or a longer freshness window (`metric.freshness_relaxed`)
- **WHEN** the gate runs
- **THEN** in each case `contracts.validate` is block with one pin issue
  `metric_ref.candidate_content_mismatch` (block) naming that one code, `differences` is that one
  code, and the verdict of the run is block; it is the only issue, except that the missing
  dimension, which the product splits by, and the status `deprecated` are followed by a rule
  issue of the third requirement (`dimension.candidate_not_allowed`,
  `metric.candidate_deprecated`)

### Scenario: a difference of labels alone is an advisory

- **GIVEN** base and candidate refs both holding version 3 with one attribute other than in the
  validated contract: another description (`metric.description_changed`), another owner
  (`metric.owner_changed`), one dimension more (`metric.dimensions_added`), a higher
  classification (`metric.classification_tightened`), the same window written as `PT360M`
  (`metric.freshness_restated`), a shorter window (`metric.freshness_tightened`), one join path
  more (`metric.join_path_added`), a lower cost ceiling, or one where the validated contract
  declares none (`metric.budget_tightened`), or the policy the product verifies under where the
  validated contract declares no governing policy (`metric.policy_added`)
- **WHEN** the gate runs
- **THEN** in each case the payload lists one issue `metric_ref.candidate_content_mismatch` with
  the severity `advisory` and the message naming that one code; `contracts.validate` is advisory
  with `contracts.issues`; the payload's `verdict` is `pass_with_advisory`; the entry has the
  outcome `content_mismatch` and `differences` with that one code; the detail says `0 of 1 metric
  pin(s) match the candidate snapshot` and carries the clause; the verdict of the run is
  `pass_with_advisory`, as it was

The status `draft` and a governing policy the product does not verify under are differences of
this kind as well — `metric.status_changed` and `metric.policy_added`, each an advisory content
issue — and the third requirement adds the review the product's rules give them.

### Scenario: differences of both kinds block and are all named

- **GIVEN** base and candidate refs both holding version 3 with another numerator and another
  description
- **WHEN** the gate runs
- **THEN** the issue is block and its message, the clause and `differences` name
  `metric.formula_changed, metric.description_changed` in that order

### Scenario: the change under review is not reported a second time

- **GIVEN** a base ref holding the validated contract and a candidate ref holding version 3 with
  another timezone, another numerator, another description or one dimension more
- **WHEN** the gate runs
- **THEN** in each case `contracts.validate` is pass with `contracts.clean` and no issue,
  `candidate_pins` is one entry with exactly the keys `ref`, `candidate_versions` `[3]` and the
  outcome `match`, the detail is `1 metric contract(s) resolved, 0 issue(s); 1 of 1 metric pin(s)
  match the candidate snapshot`, and `semantic.diff` is `semantic.changed` and lists the change
  as before

### Scenario: a validated contract the candidate holds is a match

- **GIVEN** a validated contract and a candidate ref holding one definition with another
  timezone than the base ref holds
- **WHEN** the gate runs
- **THEN** `contracts.validate` has no pin issue and the entry is `match` with exactly the three
  keys; `semantic.diff` lists `metric.time_changed` as before

### Scenario: another writing of one definition is a match

- **GIVEN** a validated contract that writes the definition the refs hold in another way: the
  dimensions in another order, two governing policies in another order, a join path declared
  twice, a quality precondition declared twice, or the keys of the file in another order
- **WHEN** the gate runs
- **THEN** in each case `contracts.validate` is pass with `contracts.clean`, no issue and the
  entry `match` with exactly the three keys

### Scenario: a base that does not hold the validated definition accounts for nothing

- **GIVEN** a candidate ref holding version 3 with another timezone, and a base ref that holds
  the metric with another numerator, holds it at version 2, holds two contracts of the metric
  id, holds no contract of it, or holds one that did not load
- **WHEN** the gate runs
- **THEN** in each case `contracts.validate` is block with one issue
  `metric_ref.candidate_content_mismatch` (block) naming `metric.time_changed`, and `differences`
  is `[metric.time_changed]`

### Scenario: the version and the number of contracts are graded before the contents

- **GIVEN** base and candidate refs holding the metric with another numerator at version 4, or a
  candidate ref holding two contracts of the metric id at version 3 of which one has another
  numerator
- **WHEN** the gate runs
- **THEN** the only pin issue is `metric_ref.candidate_version_mismatch` in the first case and
  `metric_ref.candidate_ambiguous` in the second, with the messages of the first requirement, and
  the entry has exactly the three keys, without `differences`

### Scenario: a pin the validated set does not resolve is not compared

- **GIVEN** base and candidate refs both holding version 3 with another numerator, and a
  validated set that holds no contract of the metric, or holds it at version 4
- **WHEN** the gate runs
- **THEN** the payload's `issues` are the validation report's own alone (`metric_ref.unknown` in
  the first case, `metric_ref.version_mismatch` in the second), and the entry is `match` with
  exactly the three keys

### Scenario: gate inputs without validated contracts are not compared

- **GIVEN** base and candidate snapshots both holding version 3 with another numerator, handed
  to the gate with the validation report of the validated set but without its contracts
- **WHEN** the gate runs
- **THEN** `contracts.validate` is pass with `contracts.clean` and the entry is `match`, as before
  this requirement; and the same inputs with the validated contracts are block with
  `metric_ref.candidate_content_mismatch` naming `metric.formula_changed`

### Scenario: the validation report's own issues come before the content issue

- **GIVEN** a validated contract that is a draft, so the validation report carries
  `metric.draft` (review), and base and candidate refs both holding the active contract at
  version 3
- **WHEN** the gate runs
- **THEN** the payload's `issues` are `metric.draft` with its severity, subject, source and
  message unchanged, then `metric_ref.candidate_content_mismatch` (block) naming
  `metric.status_changed`; the detail is `1 metric contract(s) resolved, 2 issue(s); 0 of 1
  metric pin(s) match the candidate snapshot: it holds another definition of
  metric://sales/gross_margin_pct at version 3 than the validated contract
  (metric.status_changed)`; the status is block

### Scenario: several pins are graded one by one in declared order

- **GIVEN** a product pinning `metric://sales/revenue@2`, `metric://sales/gross_margin_pct@3`
  and `metric://sales/units@1` in that order, validated contracts for all three, and base and
  candidate refs that hold the first as validated, the second with another numerator and no
  contract of the third
- **WHEN** the gate runs
- **THEN** the payload's pin issues are `metric_ref.candidate_content_mismatch` for the second
  pin and `metric_ref.candidate_absent` for the third, in that order; `candidate_pins` has three
  entries in declared order with the outcomes `match`, `content_mismatch`, `absent`, of which
  only the second has `differences`; the detail is `3 metric contract(s) resolved, 2 issue(s); 1
  of 3 metric pin(s) match the candidate snapshot: it holds another definition of
  metric://sales/gross_margin_pct at version 3 than the validated contract
  (metric.formula_changed); it holds no metric://sales/units, pinned at version 1`

### Scenario: a snapshot with an unloaded contract still has the metric it holds compared

- **GIVEN** base and candidate refs both holding version 3 with another numerator, the candidate
  beside another contract file that does not parse
- **WHEN** the gate runs
- **THEN** `contracts.validate` is block with `metric_ref.candidate_content_mismatch` naming
  `metric.formula_changed`, and `semantic.gaps` is block as before

### Scenario: the validation report is not modified and `decision validate` is unchanged

- **GIVEN** the inputs of the first scenario of this requirement
- **WHEN** the gate runs, and `decision validate` runs over the same contract paths
- **THEN** the validation report handed to the gate still has no issue and the verdict `pass`,
  and `decision validate` reports the same: `metric_ref.candidate_content_mismatch` does not
  appear in it

## Requirement: `contracts.validate` answers the product's rules on the candidate's definition of a pinned metric

Three validation rules read a metric contract: the dimensions it declares
(`dimension.not_allowed`, block), its status (`metric.retired`, block; `metric.draft`, review;
`metric.deprecated`, advisory) and its governing policies (`policy.mismatch`, review). The
validation report answers them on the contract the validated set holds. For every pin whose
contents the second requirement compares — the candidate snapshot holds exactly one node of the
metric, at the pinned version, and the validated set holds the contract at that version — the
gate SHALL answer the three rules a second time, for that one metric, on the *candidate's
definition*: the dimensions, the status and the governing policies of the candidate node.

An answer SHALL be reported as a *rule issue* unless the rules give the same answer — the same
code, subject and message — on the validated definition, the node of the second requirement:
that answer is the validation report's own and is not repeated. For the comparison and in the
message the governing policies of a definition are named once each, in sorted order, so one set
of policies written in another order or with a repeat is one answer.

A rule issue SHALL keep the severity, the subject and the source the rule gives it. Its code
SHALL be the rule's code with `candidate_` after the dot, and its message SHALL be
`in the candidate snapshot, ` followed by the rule's message for that one metric:

| rule issue | severity | subject | the rule's message |
|---|---|---|---|
| `dimension.candidate_not_allowed` | block | the dimension | `dimension '<dimension>' is not declared by <pin>` |
| `metric.candidate_retired` | block | the pin | `metric contract is retired and must not back a decision product` |
| `metric.candidate_draft` | review | the pin | `metric contract is still a draft: its definition is not agreed yet` |
| `metric.candidate_deprecated` | advisory | the pin | `metric contract is deprecated: plan a replacement before it is retired` |
| `policy.candidate_mismatch` | review | the pin | `metric is governed by <policies>, the product verifies under <policy>` |

where `<pin>` is the pin as the product writes it, `<policies>` are the candidate node's
governing policies, once each in sorted order, joined by `, `, and `<policy>` is the policy the
product verifies under.

A rule issue SHALL be reported whether or not the base holds the validated definition. The
semantic diff weighs a change by what it does to the definition, and the rules weigh the
definition the release would hold by what the product may rely on: a metric moved back to
`draft`, or given a governing policy the product does not verify under, is an advisory change
to the diff and, to the rules, a product whose metric is not agreed or is governed elsewhere.
`decision validate` over the product and the candidate's own contract gives the rule's answer;
the gate, grading the candidate, SHALL NOT give a weaker one.

The rule issues SHALL follow the pin issues of the first two requirements, in the product's
declared order of pins and, within one pin, in the order of the rules: the dimensions in sorted
order, the status, the policies. `candidate_pins` SHALL NOT change — an entry keeps the outcome
and the keys of the first two requirements — and neither does the count of matching pins. The
detail counts the rule issues among the issues and SHALL carry, after the pin clauses, one more
clause for every pin with a rule issue, each preceded by `; `:

`the product's rules give <codes> on the candidate's definition of <pin>`

where `<codes>` are the codes of that pin's rule issues, each named once, in the order of the
issues, joined by `, `.

A pin the second requirement does not compare SHALL have no rule issue: a candidate that holds
the metric at another version, holds several contracts of it or none, a pin the validated set
does not resolve, and gate inputs without validated contracts. The validation report handed to
the gate SHALL NOT be modified, `decision validate` SHALL NOT change, and every other check
SHALL run as before. Because the same inputs can now yield `needs_review` or `block` where the
verifier before it passed with an advisory, the verifier version and the check version SHALL be
bumped; historical bundles are not rewritten.

In the scenarios below the product pins `metric://sales/gross_margin_pct@3`, splits by `region`
and `category` and verifies under `policy://release/default@1`; unless a scenario says
otherwise, the validated contract is the one of the second requirement's scenarios, and the base
ref holds it.

### Scenario: a candidate that moves the pinned metric back to draft needs review

- **GIVEN** a candidate ref holding version 3 with the status `draft`
- **WHEN** the gate runs
- **THEN** `contracts.validate` is review with `contracts.issues`; its payload lists one issue
  `metric.candidate_draft` (review) whose subject is the pin, whose source is the product ref
  and whose message is `in the candidate snapshot, metric contract is still a draft: its
  definition is not agreed yet`; the payload's `verdict` is `needs_review`; `candidate_pins` is
  one entry with exactly the keys `ref`, `candidate_versions` `[3]` and the outcome `match`; the
  detail is `1 metric contract(s) resolved, 1 issue(s); 1 of 1 metric pin(s) match the candidate
  snapshot; the product's rules give metric.candidate_draft on the candidate's definition of
  metric://sales/gross_margin_pct@3`; `semantic.diff` lists `metric.status_changed` as before;
  the verdict of the run is `needs_review`, where it was `pass_with_advisory`, and
  `reports/summary.md` names the check with that detail under "What needs a human"

### Scenario: a draft that the base holds too needs review

- **GIVEN** base and candidate refs both holding version 3 with the status `draft`
- **WHEN** the gate runs
- **THEN** the payload's `issues` are `metric_ref.candidate_content_mismatch` (advisory) naming
  `metric.status_changed`, then `metric.candidate_draft` (review); `contracts.validate` is
  review; the entry has the outcome `content_mismatch` and `differences`
  `[metric.status_changed]`; the detail is `1 metric contract(s) resolved, 2 issue(s); 0 of 1
  metric pin(s) match the candidate snapshot: it holds another definition of
  metric://sales/gross_margin_pct at version 3 than the validated contract
  (metric.status_changed); the product's rules give metric.candidate_draft on the candidate's
  definition of metric://sales/gross_margin_pct@3`; the verdict of the run is `needs_review`,
  where it was `pass_with_advisory`

### Scenario: a governing policy the product does not verify under needs review

- **GIVEN** a validated contract that declares no governing policy, and a candidate ref holding
  version 3 governed by `policy://finance/close@2`, with a base ref that holds the validated
  contract, or holds the candidate's
- **WHEN** the gate runs
- **THEN** in both cases the payload lists `policy.candidate_mismatch` (review) whose subject is
  the pin and whose message is `in the candidate snapshot, metric is governed by
  policy://finance/close@2, the product verifies under policy://release/default@1`, and
  `contracts.validate` is review; in the first case it is the only issue and the entry is
  `match`; in the second it follows `metric_ref.candidate_content_mismatch` (advisory) naming
  `metric.policy_added`; the verdict of the run is `needs_review`, where it was
  `pass_with_advisory`

### Scenario: a deprecated candidate is an advisory of the rules

- **GIVEN** a candidate ref holding version 3 with the status `deprecated`
- **WHEN** the gate runs
- **THEN** the payload lists one issue `metric.candidate_deprecated` (advisory) with the message
  `in the candidate snapshot, metric contract is deprecated: plan a replacement before it is
  retired`; `contracts.validate` is advisory with `contracts.issues` and the payload's `verdict`
  is `pass_with_advisory`; the entry is `match`; the verdict of the run is block through
  `impact.products`, as before

### Scenario: a retired candidate blocks

- **GIVEN** a candidate ref holding version 3 with the status `retired`
- **WHEN** the gate runs
- **THEN** the payload lists one issue `metric.candidate_retired` (block) with the message `in
  the candidate snapshot, metric contract is retired and must not back a decision product`;
  `contracts.validate` is block; the entry is `match`; the detail is `1 metric contract(s)
  resolved, 1 issue(s); 1 of 1 metric pin(s) match the candidate snapshot; the product's rules
  give metric.candidate_retired on the candidate's definition of
  metric://sales/gross_margin_pct@3`

### Scenario: a dimension the product splits by is missing in the candidate

- **GIVEN** a candidate ref holding version 3 with the dimension `region` alone, or with the
  dimension `store_id` alone
- **WHEN** the gate runs
- **THEN** in the first case the payload lists one issue `dimension.candidate_not_allowed`
  (block) whose subject is `category` and whose message is `in the candidate snapshot, dimension
  'category' is not declared by metric://sales/gross_margin_pct@3`; in the second it lists two,
  for `category` and then `region`, and the detail names the code once: `1 metric contract(s)
  resolved, 2 issue(s); 1 of 1 metric pin(s) match the candidate snapshot; the product's rules
  give dimension.candidate_not_allowed on the candidate's definition of
  metric://sales/gross_margin_pct@3`; `contracts.validate` is block in both

### Scenario: a dimension the product does not split by is not the rules' concern

- **GIVEN** a validated contract and a base ref that declare `region`, `category` and
  `store_id`, and a candidate ref holding version 3 without `store_id`
- **WHEN** the gate runs
- **THEN** `contracts.validate` is pass with `contracts.clean` and no issue; `semantic.diff`
  lists `metric.dimensions_removed` as before

### Scenario: the rules are answered in their order on one pin

- **GIVEN** a candidate ref holding version 3 with the status `deprecated`, the dimension
  `region` alone and the governing policy `policy://finance/close@2` alone
- **WHEN** the gate runs
- **THEN** the payload's `issues` are `dimension.candidate_not_allowed` (block, subject
  `category`), `metric.candidate_deprecated` (advisory) and `policy.candidate_mismatch` (review),
  in that order; the detail is `1 metric contract(s) resolved, 3 issue(s); 1 of 1 metric pin(s)
  match the candidate snapshot; the product's rules give dimension.candidate_not_allowed,
  metric.candidate_deprecated, policy.candidate_mismatch on the candidate's definition of
  metric://sales/gross_margin_pct@3`; `contracts.validate` is block

### Scenario: the rule issues follow the pin issues

- **GIVEN** base and candidate refs both holding version 3 as a deprecated contract with the
  dimension `region` alone and the numerator `revenue` — the first scenario of the second
  requirement
- **WHEN** the gate runs
- **THEN** the payload's `issues` are `metric_ref.candidate_content_mismatch` (block), then
  `dimension.candidate_not_allowed` (block, subject `category`), then
  `metric.candidate_deprecated` (advisory); the detail is `1 metric contract(s) resolved, 3
  issue(s); 0 of 1 metric pin(s) match the candidate snapshot: it holds another definition of
  metric://sales/gross_margin_pct at version 3 than the validated contract
  (metric.formula_changed, metric.dimensions_removed, metric.status_changed); the product's
  rules give dimension.candidate_not_allowed, metric.candidate_deprecated on the candidate's
  definition of metric://sales/gross_margin_pct@3`

### Scenario: an answer the validated contract gives too is not repeated

- **GIVEN** a validated contract, a base ref and a candidate ref that all hold version 3 with
  the status `draft`; or all three with the dimension `region` alone; or all three governed by
  `policy://finance/close@2` alone
- **WHEN** the gate runs
- **THEN** in each case the payload's `issues` are the validation report's own alone —
  `metric.draft`, `dimension.not_allowed` for `category`, `policy.mismatch` — with their codes
  and messages unchanged; the entry is `match` and the detail has no rule clause

### Scenario: another answer of the same rule is reported beside the report's own

- **GIVEN** a validated contract and a base ref with the status `draft` and a candidate ref with
  the status `deprecated`; or the two with the dimension `region` alone and a candidate ref with
  the dimension `store_id` alone; or the two governed by `policy://finance/close@2` and a
  candidate ref governed by `policy://risk/limits@1` and `policy://finance/close@2`, written in
  that order
- **WHEN** the gate runs
- **THEN** the payload's `issues` are, in the first case, the report's `metric.draft` and then
  `metric.candidate_deprecated` (advisory); in the second, the report's `dimension.not_allowed`
  for `category` and then `dimension.candidate_not_allowed` for `region` alone; in the third,
  the report's `policy.mismatch` and then `policy.candidate_mismatch` (review) with the message
  `in the candidate snapshot, metric is governed by policy://finance/close@2,
  policy://risk/limits@1, the product verifies under policy://release/default@1`

### Scenario: one set of governing policies written in another way is one answer

- **GIVEN** a validated contract governed by `policy://risk/limits@1` and
  `policy://finance/close@2`, written in that order, and base and candidate refs that write the
  two in the other order; or a validated contract and a base ref governed by
  `policy://finance/close@2` and a candidate ref that declares that policy twice
- **WHEN** the gate runs
- **THEN** in both cases the payload's `issues` are the report's `policy.mismatch` alone, with
  the policies in the order the validated contract writes them, and the detail has no rule
  clause

### Scenario: a candidate that satisfies a rule the validated contract fails adds nothing

- **GIVEN** a validated contract with the status `draft` and base and candidate refs both
  holding the active contract; or a validated contract and a base ref governed by
  `policy://finance/close@2` and a candidate ref governed by the policy the product verifies
  under; or a candidate ref governed by that policy and `policy://finance/close@2` beside it
- **WHEN** the gate runs
- **THEN** no rule issue is reported in any case: the payload's `issues` are the report's
  `metric.draft` and the content issue of the second requirement in the first case, the report's
  `policy.mismatch` alone in the second, and none in the third

### Scenario: a pin that is not compared has no rule issue

- **GIVEN** a draft in the candidate ref at version 4; or beside a second contract of the metric
  id at version 3; or a candidate ref without the metric; or a draft in the candidate ref at
  version 3 with a validated set that holds no contract of the metric, or holds it at version 4
- **WHEN** the gate runs
- **THEN** in each case the payload's `issues` and the detail are those of the first two
  requirements — `metric_ref.candidate_version_mismatch`, `metric_ref.candidate_ambiguous`,
  `metric_ref.candidate_absent`, the report's `metric_ref.unknown`, the report's
  `metric_ref.version_mismatch` — and no code with `candidate_` after a rule's family appears

### Scenario: gate inputs without validated contracts have no rule issue

- **GIVEN** a candidate snapshot holding version 3 with the status `draft`, handed to the gate
  with the validation report of the validated set but without its contracts
- **WHEN** the gate runs
- **THEN** `contracts.validate` is pass with `contracts.clean`, as before this requirement; the
  same inputs with the validated contracts are review with `metric.candidate_draft`; and the two
  runs share one run id

### Scenario: several pins are answered one by one in declared order

- **GIVEN** a product pinning `metric://sales/revenue@2`, `metric://sales/gross_margin_pct@3`
  and `metric://sales/units@1` in that order, validated contracts and a base ref holding all
  three as active, and a candidate ref holding the first as a draft, the second retired with the
  dimension `region` alone, and the third unchanged
- **WHEN** the gate runs
- **THEN** the payload's `issues` are `metric.candidate_draft` (review) whose subject is
  `metric://sales/revenue@2`, then `dimension.candidate_not_allowed` (block, subject `category`,
  the message naming `metric://sales/gross_margin_pct@3` alone), then `metric.candidate_retired`
  (block) whose subject is `metric://sales/gross_margin_pct@3`; `candidate_pins` has three
  entries with the outcome `match`; the detail is `3 metric contract(s) resolved, 3 issue(s); 3
  of 3 metric pin(s) match the candidate snapshot; the product's rules give
  metric.candidate_draft on the candidate's definition of metric://sales/revenue@2; the
  product's rules give dimension.candidate_not_allowed, metric.candidate_retired on the
  candidate's definition of metric://sales/gross_margin_pct@3`

### Scenario: the validation report is not modified and `decision validate` is unchanged

- **GIVEN** the inputs of the first scenario of this requirement
- **WHEN** the gate runs, and `decision validate` runs over the same contract paths
- **THEN** the validation report handed to the gate still has no issue and the verdict `pass`,
  and `decision validate` reports the same: `metric.candidate_draft` does not appear in it

## Known boundaries

- The validation report's own issues describe the validated copy and stay as they are: a
  candidate that satisfies a rule the validated contract fails does not remove the report's
  issue, and the remedy is to validate the contract the release holds.
- A rule issue is the rule's answer for one pinned metric. `dimension.candidate_not_allowed`
  names that one pin, where the report's `dimension.not_allowed` names every metric of the
  validated set that lacks the dimension.
- The rules answered on the candidate's definition are the three that read a metric contract. A
  rule that reads the product alone, or the presence and the version of a contract in the
  validated set, has no second answer.
- A content difference is named by the codes of the semantic diff; the differing values stay in
  the two contracts and are not copied into the evidence.
- What the semantic diff names as a change is a difference here too, and nothing else is: the
  grain keys written in another order are `metric.grain_changed`, while a contract's
  `lineage_refs` and `verified_asset_refs`, which a snapshot does not carry, are not compared.
- The validated contract set is not part of the run's idempotency key (DECISIONOPS_ARCHITECTURE
  §7.6). A run repeated into the same evidence directory after the validated copy alone was
  corrected shares the run id of the first and is refused as a conflict with the sealed bundle,
  as it already was for a validated contract whose status alone changed.
- The metric pins of verified assets (`metric_refs`) are not resolved by this check:
  `assets.invalidated` resolves them in the candidate snapshot
  ([verified asset metric pins](verified-asset-metric-pins.md)), by version; their contents are
  not compared with the validated contract.
- A semantic ref holding two contracts of one metric id is not a snapshot gap: for a metric that
  neither the product nor a verified asset given to the run pins, the diff and the reference-SQL
  compiler may still read different contracts of it.
