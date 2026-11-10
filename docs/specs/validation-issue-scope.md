# Capability: The validation issues a run grades

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

`decision validate` loads every contract under the validated paths and answers its rules for
every decision product it finds there. Its report is one for the whole paths: the refs of the
products and of the metric contracts it loaded, and the issues of all of them
(`auto_bi/decision/validate.py`). `decision verify` verifies one product — the idempotency key
and the bundle both name one — and `--product` names it when the paths hold several. Its first
check, `contracts.validate`, is handed the report of the whole paths. The tracked pilot packages
keep two products in one contracts directory (`docs/pilot/2026-08-25-ch-*/contracts`), so one
report behind several products is the ordinary layout.

Every other check of the gate already answers for the product under verification alone: a change
to a metric another product depends on is `impact.none` for this one, and another product's
verified asset is neither its evidence nor its finding (DECISIONOPS_ARCHITECTURE §8.3).

Until this capability `contracts.validate` graded every issue of the report. Reproduced through
the inputs of `decision verify` at HEAD `25b620c`, 2026-10-04, for the product
`decision://sales/weekly_margin@1` — alone in the paths `contracts.clean` with the verdict
`pass_with_advisory` — beside a second product `decision://sales/monthly_margin@1`:

- the second product pinning `metric://sales/gross_margin_pct@2` while the paths hold version 3:
  `contracts.validate` of the first product's run was block with `contracts.issues`, its one
  issue `metric_ref.version_mismatch` carrying the source `decision://sales/monthly_margin@1`,
  and the verdict of the run was `block`;
- the second product pinning a metric no contract of the paths defines (`metric_ref.unknown`), or
  splitting by a dimension its metric does not declare (`dimension.not_allowed`): the same block;
- the second product verifying under another policy than its metric is governed by
  (`policy.mismatch`): `contracts.validate` was review and the verdict `needs_review`.

In each of those runs the subject refs of the check named the first product and its pin alone,
and nothing in the first product, its metric or either semantic ref could be changed to clear
the finding.

A message or detail quoted in a scenario below may be wrapped across lines of this file; each is
one line of text, with a single space where it wraps here.

## Requirement: `contracts.validate` grades the validation issues of the product it verifies

The validation rules answer for one product at a time and write the ref of that product as the
`source` of every issue they give: `metric_ref.unknown`, `metric_ref.version_mismatch`,
`dimension.not_allowed`, `metric.retired`, `metric.draft`, `metric.deprecated` and
`policy.mismatch`. Nothing the rules say of one product carries the ref of another. An issue of
loading a file — `contract.unreadable`, `contract.unknown_kind`, `contract.invalid`,
`contract.duplicate_id` — carries the name of the file as its `source`: the place of the
validated path that reached it and the names below it
([validated path names](validated-path-names.md)). It is no product's.

An issue of the validation report is *another product's* when the report lists the product under
verification among its `products`, lists the issue's `source` there as well, and the two refs
differ. The gate SHALL NOT grade another product's issue: it changes neither the status nor the
summary code of `contracts.validate`, nor the verdict of the run.

Every other issue of the report SHALL be graded as before — the issues of the product under
verification and the issues of loading a file, whatever that file holds or would have held: a
file that was not read may be a second definition of a contract this product depends on, and
the report cannot tell. A report that does not list the product under verification was not made
for it, so none of its issues is another product's and all of them SHALL be graded. So SHALL an
issue whose `source` is a ref the report does not list among its `products`.

The sealed payload of the check SHALL keep every key of the validation report it was given, with
`products` and `metrics` as the report holds them. Its `issues` SHALL hold the report's issues
that are graded, unchanged and in the report's order, followed by the pin issues and the rule
issues of the [product's metric pins](product-metric-pins.md). It SHALL carry one more key,
`other_product_issues`: another product's issues, unchanged and in the report's order — an empty
list when there is none. Its `verdict` SHALL be the verdict of `issues`.

The check's status SHALL be graded from `issues`, and its summary code SHALL be
`contracts.clean` only when `issues` is empty. In its detail `<m> issue(s)` SHALL count
`issues`, and when there is another product's issue the detail SHALL continue, before the `;`
that precedes the count of matching pins, with

`, <x> issue(s) of <y> other product(s) not graded`

where `<x>` counts `other_product_issues` and `<y>` the different products they name. With no
such issue the detail SHALL be what it was.

The validation report handed to the gate SHALL NOT be modified, and `decision validate` SHALL
NOT change: over the same paths it reports the issues of every product, and its verdict and exit
code follow all of them. The report stays a part of the [run identity](run-identity.md) whole,
so another product's issue moves the run id of this product's run although no check grades it.
Because the same inputs can now pass where the verifier before it blocked or asked for review,
the verifier version and the check version SHALL be bumped; historical bundles are not
rewritten.

## Requirement: The summary names the other products' issues without grading them

As of verifier 99, `reports/summary.md` SHALL show a conditional section
`Validation issues for other products`, after `What was not done` and before
`How to read this evidence`. It SHALL restate exactly the `other_product_issues`
selected by the existing product partition, in their original order, including duplicates.
Each entry names source, code, reported severity, subject and message. Free text
is literal Markdown text with escaped punctuation and visible control characters.

The section SHALL explain that these issues belong to other products in the validated
paths, are not graded for this product and do not change this run's verdict. It SHALL
be absent for an empty list. It changes no check payload, status, owner action or claim;
the check version remains 73. Verifier 99 changes new run identity and summary evidence;
historical bundles are not rewritten. With identical summary facts and no other-product
issues, the rendered text remains byte-identical to verifier 98's renderer.

## Scenarios

In the scenarios below the *verified product* is `decision://sales/weekly_margin@1`: it pins
`metric://sales/gross_margin_pct@3`, splits by `region` and `category` and verifies under
`policy://release/default@1`. The validated contract of that metric is active, declares those
two dimensions and that governing policy, and the base and the candidate ref hold it. The
*other product* is `decision://sales/monthly_margin@1`, a contract in the same paths; unless a
scenario says otherwise it is the verified product's contract under that id. The run verifies
the verified product unless a scenario says otherwise.

### Scenario: another product's moved pin does not block this product's release

- **GIVEN** the other product pinning `metric://sales/gross_margin_pct@2`
- **WHEN** the gate runs
- **THEN** `contracts.validate` is pass with `contracts.clean`; the payload's `issues` is empty
  and its `verdict` is `pass`; `other_product_issues` is one issue,
  `metric_ref.version_mismatch` (block), whose subject is `metric://sales/gross_margin_pct@2`,
  whose source is `decision://sales/monthly_margin@1` and whose message is `the repository holds
  version 3 of metric://sales/gross_margin_pct; the pinned version is gone`; the payload's
  `products` names both products; the detail is `1 metric contract(s) resolved, 0 issue(s), 1
  issue(s) of 1 other product(s) not graded; 1 of 1 metric pin(s) match the candidate snapshot`;
  the subject refs of the check are the verified product and its pin; the verdict of the run is
  `pass_with_advisory`, where it was `block`; `reports/summary.md` has neither "What stops the
  release" nor "What needs a human", and the row of the check in its table carries that detail

### Scenario: no rule issue of another product is graded

- **GIVEN** the other product pinning a metric no contract of the paths defines
  (`metric_ref.unknown`); pinning `metric://sales/gross_margin_pct@2`
  (`metric_ref.version_mismatch`); splitting by `store`, which its metric does not declare
  (`dimension.not_allowed`); verifying under `policy://release/strict@1` (`policy.mismatch`);
  or pinning a metric of its own, `metric://sales/revenue@3`, whose validated contract is
  retired (`metric.retired`), a draft (`metric.draft`) or deprecated (`metric.deprecated`)
- **WHEN** the gate runs
- **THEN** in each case `contracts.validate` is pass with `contracts.clean`, the payload's
  `issues` is empty, `other_product_issues` is that one issue with the other product's ref as its
  source, and the verdict of the run is what it is without the other product in the paths; under
  verifier 91 the check was block in the first three cases and for the retired metric, review for
  the policy and the draft, and an advisory for the deprecated metric

### Scenario: the verified product's own issue is graded beside another product's

- **GIVEN** the verified product splitting by `store` as well, which its metric does not
  declare, and the other product pinning `metric://sales/gross_margin_pct@2`
- **WHEN** the gate runs
- **THEN** `contracts.validate` is block with `contracts.issues`; the payload's `issues` is
  `dimension.not_allowed` for `store` with the verified product's ref as its source, and its
  `verdict` is `block`; `other_product_issues` is the other product's
  `metric_ref.version_mismatch`; the detail is `1 metric contract(s) resolved, 1 issue(s), 1
  issue(s) of 1 other product(s) not graded; 1 of 1 metric pin(s) match the candidate snapshot`

### Scenario: a rule answered for both products is graded once

- **GIVEN** a validated contract that is a draft, held by the base and the candidate ref as
  well, and both products pinning it, so the report carries `metric.draft` twice — once with
  each product's ref as the source
- **WHEN** the gate runs
- **THEN** the payload's `issues` is the `metric.draft` whose source is the verified product,
  and `other_product_issues` the `metric.draft` whose source is the other product;
  `contracts.validate` is review; the detail is `1 metric contract(s) resolved, 1 issue(s), 1
  issue(s) of 1 other product(s) not graded; 1 of 1 metric pin(s) match the candidate snapshot`,
  where verifier 91 counted `2 issue(s)`

### Scenario: verifying the other product grades its issues and not this one's

- **GIVEN** the verified product splitting by `store` as well and the other product pinning
  `metric://sales/gross_margin_pct@2`
- **WHEN** the gate runs for the other product
- **THEN** the payload's `issues` begins with the other product's `metric_ref.version_mismatch`,
  `other_product_issues` is the `dimension.not_allowed` whose source is
  `decision://sales/weekly_margin@1`, and `contracts.validate` is block

### Scenario: an issue of loading a file is graded whatever the file holds

- **GIVEN** beside the two products, both clean: a file that is not a YAML mapping
  (`contract.unreadable`); a mapping of a kind the loader does not know
  (`contract.unknown_kind`); a third product's contract that fails its schema
  (`contract.invalid`); a contract of a metric no product pins that fails its schema
  (`contract.invalid`); a second file defining the other product (`contract.duplicate_id`); or a
  second file defining a metric no product pins (`contract.duplicate_id`)
- **WHEN** the gate runs
- **THEN** in each case `contracts.validate` is block with `contracts.issues`, the payload's
  `issues` is that one issue with the name of the file as its source, `other_product_issues` is
  empty and the detail has no clause about other products

### Scenario: a path that does not exist is graded, however it is spelled

- **GIVEN** the two products, both clean, and one more validated path that does not exist and is
  spelled `decision://sales/monthly_margin@1`
- **WHEN** the gate runs
- **THEN** `contracts.validate` is block with one issue `contract.unreadable`, in `issues`: its
  source is `$2`, the place of the path among the paths given, which is never the ref of a
  product (under verifiers 118 to 120, the path as the loader wrote it)

### Scenario: the issues keep the report's order on both sides

- **GIVEN** three products in the paths — `decision://sales/daily_margin@1` pinning
  `metric://sales/gross_margin_pct@2`, the verified product splitting by `store` as well, and
  `decision://sales/yearly_margin@1` verifying under `policy://release/strict@1` — beside a file
  that is not a YAML mapping
- **WHEN** the gate runs
- **THEN** the payload's `issues` are `contract.unreadable`, then the verified product's
  `dimension.not_allowed`; `other_product_issues` are `metric_ref.version_mismatch` of
  `decision://sales/daily_margin@1`, then `policy.mismatch` of
  `decision://sales/yearly_margin@1`; the detail begins `1 metric contract(s) resolved, 2
  issue(s), 2 issue(s) of 2 other product(s) not graded; `; `contracts.validate` is block

### Scenario: several issues of one other product are counted by issue and by product

- **GIVEN** the other product pinning a metric no contract of the paths defines and then
  `metric://sales/gross_margin_pct@3`, splitting by `store` and `warehouse` as well, neither
  declared by that metric, and verifying under `policy://release/strict@1`; and a third product
  in the paths, clean
- **WHEN** the gate runs
- **THEN** `other_product_issues` are `metric_ref.unknown`, the two `dimension.not_allowed` and
  the `policy.mismatch`, in the report's order, and the detail is `1 metric contract(s)
  resolved, 0 issue(s), 4 issue(s) of 1 other product(s) not graded; 1 of 1 metric pin(s) match
  the candidate snapshot`: a product the issues do not name is not counted

### Scenario: a report stated in another order keeps it on both sides

- **GIVEN** gate inputs whose validation report lists the verified product, the other product
  and a third one, and states six issues in an order of its own: the third product's, the
  verified product's, the other product's, one of loading a file, the verified product's
  again, the other product's again
- **WHEN** the gate runs
- **THEN** the payload's `issues` are the second, the fourth and the fifth of them and
  `other_product_issues` the first, the third and the sixth, each in the order stated; the
  detail counts `3 issue(s), 3 issue(s) of 2 other product(s) not graded`

### Scenario: the pin and rule issues of the verified product follow its own report issues

- **GIVEN** a validated contract that is a draft and both products pinning it, a base ref
  holding that draft and a candidate ref holding the metric at version 4
- **WHEN** the gate runs
- **THEN** the payload's `issues` are the verified product's `metric.draft`, then
  `metric_ref.candidate_version_mismatch`; `other_product_issues` is the other product's
  `metric.draft`; the detail is `1 metric contract(s) resolved, 2 issue(s), 1 issue(s) of 1
  other product(s) not graded; 0 of 1 metric pin(s) match the candidate snapshot: it holds
  version 4 of metric://sales/gross_margin_pct, pinned at version 3`; `contracts.validate` is
  block

### Scenario: one product in the paths has nothing set aside

- **GIVEN** the verified product alone in the paths, clean or with an issue of its own
- **WHEN** the gate runs
- **THEN** `other_product_issues` is an empty list, the payload's `issues` are the report's own
  and the detail is what it was: `1 metric contract(s) resolved, 0 issue(s); 1 of 1 metric
  pin(s) match the candidate snapshot` for the clean product

### Scenario: a report that does not list the verified product is graded whole

- **GIVEN** gate inputs whose product is the verified product and whose validation report is
  the report of paths that hold the other product alone, pinning
  `metric://sales/gross_margin_pct@2`
- **WHEN** the gate runs
- **THEN** `contracts.validate` is block with `contracts.issues`, the payload's `issues` is that
  `metric_ref.version_mismatch` and `other_product_issues` is empty, as before this capability

### Scenario: an issue whose source the report does not list is graded

- **GIVEN** gate inputs whose validation report lists the two products and carries one issue
  with the source `decision://sales/quarterly_margin@1`, a ref the report does not list
- **WHEN** the gate runs
- **THEN** the issue is in the payload's `issues` and graded by its severity, and
  `other_product_issues` is empty

### Scenario: another product's issue moves the run id and nothing else

- **GIVEN** the other product clean in one run and pinning
  `metric://sales/gross_margin_pct@2` in another, every other input equal
- **WHEN** the gate runs both
- **THEN** the two runs have the same verdict and the same status, summary code and subject refs
  of every check; their run ids differ, the validation report being part of the run identity

### Scenario: the validation report is not modified and `decision validate` is unchanged

- **GIVEN** the inputs of the first scenario
- **WHEN** the gate runs, and `decision validate` runs over the same contract paths
- **THEN** the validation report handed to the gate still carries the other product's
  `metric_ref.version_mismatch` and the verdict `block`, and `decision validate` reports the
  same

### Scenario: `decision verify` exits by its product and `decision validate` by the paths

- **GIVEN** the inputs of the first scenario
- **WHEN** `decision validate` runs over the contract paths, and `decision verify` runs once
  with `--product` naming the verified product and once naming the other product
- **THEN** `decision validate` exits 4; `decision verify` exits 0 for the verified product and 4
  for the other product, whose own pin the report blocks

## Known boundaries

- An issue of loading a file is graded on every product's run: a file of the validated paths
  that does not parse, is of an unknown kind, fails its schema or repeats an id blocks the
  release of every product verified against those paths, also when the file is another
  product's contract or a metric this product does not pin. Telling whose file it is needs the
  id a broken file declares to be trusted, which this capability does not decide.
- Another product's issue is told by its `source` alone, as the validation rules write it; the
  code of the issue is not read. A report built by other means than `decision validate` is taken
  at its word.
- `<n> metric contract(s) resolved` counts every metric contract of the validated paths, as
  before, whichever product pins it.
- The run id moves with another product's issue, the validation report being keyed whole
  ([run identity](run-identity.md)): two runs of one product that differ in another product's
  contract alone have different run ids and one outcome.
- `decision validate` remains the answer for the paths as a whole: a repository that wants no
  product released while any product's contracts are in conflict gates on its exit code.
