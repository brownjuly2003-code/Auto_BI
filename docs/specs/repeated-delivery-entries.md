# Capability: An entry repeated in the delivery of a decision product

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

A decision product delivers the dashboards it lists in `delivery.dashboards` and the brief formats
in `delivery.brief_formats` (DECISIONOPS_ARCHITECTURE §7.2). Verifier 113 took both lists as they
were written. A product that delivered one dashboard ref twice passed `decision validate`, and the
gate's `bi.contract` (§8.5) went through each entry: a spec that compiled was compiled twice, its
payloads were sealed under one id twice ([the ids of evidence named after a ref](evidence-ref-ids.md)),
`EvidenceBundle` refused the second, and the run ended in a pydantic `ValidationError` traceback
with exit 1, no bundle written and no verdict said. With no spec for the dashboard, an invalid one
or one the compile refused no payload was sealed, and the run wrote a bundle that named the
dashboard twice — in the subjects and the detail of `bi.contract` and in the list of its payload
that held it (`missing`, `invalid` or `refused`). A brief format listed twice was taken and moved
the digest of the product.

Both lists are sets by meaning, as the lists of `DecisionScope` are: an entry written twice changes
the digest of the product without changing what it delivers.

## Requirement: the schema refuses an entry repeated in either list of the delivery

`DeliverySpec` SHALL refuse a dashboard ref that `dashboards` lists more than once with
`duplicate entries in delivery.dashboards: <refs>`, and a format that `brief_formats` lists more
than once with `duplicate entries in delivery.brief_formats: <formats>`, where each repeated entry
is named once, in sorted order, joined by `, ` — the rule and the words of `DecisionScope`. A ref is
compared as the whole text the ref field keeps: `auto_bi.refs` gives one spelling to a ref, so two
texts are two refs. Two versions of one dashboard (`dashboard://weekly_margin@7`,
`dashboard://weekly_margin@8`), refs of one slug (`dashboard://weekly_margin@7`,
`dashboard://weekly-margin@7`) and refs of one last segment (`dashboard://sales/weekly_margin@7`,
`dashboard://ops/weekly_margin@7`) SHALL stay deliverable together.

A product the schema refuses is a contract that fails its schema: `decision validate` SHALL report
it as `contract.invalid` (block) of the id it declares, with the location `delivery` and the words
above, as it reports a repeat in `scope.metrics`. The run of such a product SHALL be refused as the
run of any product that is not among what was read: `no decision product contract found in the
given paths`, or `no decision product '<id>' in the given paths (<the products read>)`, exit 2,
nothing written. The run of another product of the same paths SHALL block in `contracts.validate`
on that issue, as on every issue of loading a file ([validation issue scope](validation-issue-scope.md)).

A run that verifier 113 ended in a traceback or wrote a bundle for is refused, and the run of
another product of its paths may block where it did not, so `VERIFIER_VERSION` SHALL be 114 and
`CHECK_VERSION` 77. `NATIVE_SOURCE_VERSION` is not bumped: no snapshot changes.

### Scenario: a dashboard delivered twice fails the schema

- **GIVEN** a product whose `delivery.dashboards` lists `dashboard://weekly_margin@7` twice
- **WHEN** it is validated
- **THEN** the schema refuses it at `delivery` with `duplicate entries in delivery.dashboards:
  dashboard://weekly_margin@7`

### Scenario: each repeated ref is named once, in sorted order

- **GIVEN** `dashboard://weekly_margin@7`, `dashboard://ops/weekly_margin@7`,
  `dashboard://weekly_margin@7`, `dashboard://weekly_revenue@1`, `dashboard://ops/weekly_margin@7`,
  `dashboard://weekly_margin@7`
- **WHEN** it is validated
- **THEN** the refusal names `dashboard://ops/weekly_margin@7, dashboard://weekly_margin@7`

### Scenario: a brief format listed twice fails the schema

- **GIVEN** `brief_formats: [markdown, markdown]`
- **WHEN** it is validated
- **THEN** the schema refuses it with `duplicate entries in delivery.brief_formats: markdown`

### Scenario: the rule is the delivery's own

- **GIVEN** a `delivery` validated on its own, not as a part of a product, that lists one dashboard
  ref twice, or one brief format twice
- **WHEN** it is validated
- **THEN** it is refused with the same words, not only the product built of it

### Scenario: refs that differ stay deliverable together

- **GIVEN** two versions of one dashboard (`@7` and `@8`, or `@7` and `@17`), two refs of one slug,
  or two refs of one last segment
- **WHEN** they are validated
- **THEN** the product is valid and delivers both, in the order written

### Scenario: decision validate names the repeat

- **GIVEN** the product of the first scenario in a validated directory
- **WHEN** `decision validate` runs
- **THEN** the report holds one issue `contract.invalid`, block, of `decision://sales/weekly_margin`,
  from the product's file, with `delivery: Value error, duplicate entries in delivery.dashboards:
  dashboard://weekly_margin@7`; the command exits 2 when that was the only product of the paths,
  and 4 beside a product that validates

### Scenario: the run of the product is refused whatever its spec

- **GIVEN** the product of the first scenario, and for its dashboard a spec that compiles, no spec,
  a spec that does not validate, or one the compile refuses
- **WHEN** `decision verify` runs, with or without `--product`
- **THEN** it is refused with the usage error of a product that was not read, exit 2, one line, no
  traceback, and no evidence directory is created

### Scenario: a repeat in another product blocks the run of this one

- **GIVEN** a product that validates, and beside it in the validated paths another product that
  delivers one dashboard twice
- **WHEN** `decision verify --product` runs for the first
- **THEN** `contracts.validate` blocks with `contracts.issues` on the issue of the other product's
  file, the verdict is `block`, and the bundle re-verifies

### Scenario: a bundle that names a dashboard twice still reads

- **GIVEN** a bundle sealed for a product that delivers one dashboard twice, with no spec for it, a
  spec that does not validate, or one the compile refuses
- **WHEN** it is read and re-verified, and `decision evidence` lists it
- **THEN** it re-verifies with no problem, and `bi.contract` still names the dashboard twice in
  `missing`, `invalid` or `refused`

## Known boundaries

- The usage error of a refused run says that the product is not among what was read, not why: the
  schema's refusal is named by `decision validate`, not by `decision verify`. That is so for every
  product its schema refuses, a repeat in `scope.metrics` too.
- Bundles written before verifier 114 are not rewritten. A run of a product with a repeated
  dashboard wrote one when no payload of that dashboard was sealed — no spec, an invalid one, or one
  the compile refused. No reader of a bundle builds the product (the run key holds its digest
  only), so they read and re-verify as before. The test of it seals such a bundle with today's
  writer; no bundle of verifier 113 is kept.
- The rule holds for a product that is validated — by `decision validate`, `decision verify` or
  `DecisionProductSpec.load`. A product built past validation (`model_construct`,
  `model_copy(update=…)`, a list changed after it) and handed to the gate directly is graded as it
  is: that is how the test of an old bundle seals one with a repeat.
- A repeat is told by the text of the ref. A symlink, a junction or a hard link that makes two refs
  one file stays the boundary of [the paths of dashboard specs](dashboard-spec-paths.md).
- A product with `brief_formats: [markdown, markdown]`, taken before, is refused now. `markdown`
  is the only brief format, so the list holds nothing else to repeat.
