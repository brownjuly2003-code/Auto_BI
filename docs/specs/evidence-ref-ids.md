# Capability: The ids of evidence named after a ref

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

Two kinds of entry in the bundle of `decision verify` are named after a ref: the claim the gate
makes of each equivalent result (`results.reconcile`, DECISIONOPS_ARCHITECTURE §8.4) and each
payload `bi.contract` compiles for a delivered dashboard (§8.5). Verifier 112 named them by the
slug of the ref alone, `claim-<slug of the asset id>` and `bi-<slug of the dashboard ref>-<id of
the payload>`. The slug lowercases the ref and folds every character that is not a letter or a
digit into one `-`, so `asset://sales/weekly_margin_by_region`,
`asset://sales/weekly-margin-by-region` and `asset://sales/weekly/margin_by-region`, or
`dashboard://weekly_margin@7` and `dashboard://weekly-margin@7`, share one. A bundle holds an id
once: when two such assets both had an equivalent result, or two such dashboards both compiled,
the run was graded to the end, and then `EvidenceBundle` refused the second id. The run ended in
a pydantic `ValidationError` traceback with exit 1, no bundle written and no verdict said; the
architecture keeps exit 1 for an unhandled exception.

## Requirement: an id named after a ref ends in the hash of that ref

The claim of an equivalent result SHALL be `claim-<slug>-<hex>`, where `<slug>` is the slug of the
asset id and `<hex>` the full SHA-256 of the canonical JSON array `[<asset id>]`. A compiled BI
payload SHALL be `bi-<slug>-<payload id>-<hex>`, where `<slug>` is the slug of the dashboard ref,
`<payload id>` the id the compiler gave the payload (`dataset-1`, `chart-1`, `dashboard`) and
`<hex>` the full SHA-256 of the canonical JSON array `[<dashboard ref>, <payload id>]`. The asset id
is the one the asset declares, without its version, as before; the dashboard ref is the one the
product delivers, with its pin.

The hash, not the slug, tells two ids apart: it keeps every segment and separator of the ref, its
pin and the boundary between the ref and the payload id. The slug before it is there to be read in
`decision evidence`, where a claim names no asset otherwise, and SHALL NOT be relied on. The id
SHALL depend on nothing else: neither on the payload, nor on another ref of the run, nor on the
order of the assets or the dashboards. A payload that changes keeps its id and moves its digest;
two dashboards whose payloads are alike keep their own ids, and their payloads share one content
address.

A run that writes such an entry seals another id than verifier 112 did, so `VERIFIER_VERSION`
SHALL be bumped. No check changes what it grades, so `CHECK_VERSION` and `NATIVE_SOURCE_VERSION`
are not bumped. Run identity is computed as before; the verifier version is part of its key, so
every run gets another run id, a run of one asset and one dashboard too.

### Scenario: two assets of one slug both seal their claim

- **GIVEN** two assets, `asset://sales/weekly_margin_by_region` and either
  `asset://sales/weekly-margin-by-region` or `asset://sales/weekly/margin_by-region`, each with an
  equivalent recorded result, and a delivered dashboard that compiles
- **WHEN** `decision verify` runs
- **THEN** the verdict is `pass_with_advisory`, `results.reconcile` compares both, the bundle
  re-verifies and holds two `result.equivalent` claims, `claim-asset-sales-weekly-margin-by-region-`
  followed by the hash of each asset id, both on the evidence of `results.reconcile`

### Scenario: two dashboards of one slug both seal their payloads

- **GIVEN** a product delivering `dashboard://weekly_margin@7` and `dashboard://weekly-margin@7`,
  their specs alike or with other titles
- **WHEN** `decision verify` runs
- **THEN** the verdict is `pass_with_advisory`, and the bundle re-verifies and holds the three
  payloads of each dashboard under the id of its ref and payload id, with the digest `bi.contract`
  gives that payload

### Scenario: the command line writes the bundle of two assets of one slug

- **GIVEN** the two assets of the first scenario and no spec for the delivered dashboard
- **WHEN** `decision verify` runs from the command line, then `decision evidence` on its run
- **THEN** it exits 3 with the verdict `needs_review`, and `decision evidence` exits 0 and lists
  both claims

### Scenario: the hash tells apart what the slug folds

- **GIVEN** refs whose slugs and payload ids join to one text: a namespace
  (`dashboard://sales/weekly_margin@7`, `dashboard://sales-weekly_margin@7`), a pin
  (`dashboard://weekly_margin@7`, `dashboard://weekly_margin-7`), a repeated separator
  (`dashboard://weekly-margin@7`, `dashboard://weekly--margin@7`), the boundary between a ref and
  a payload id (`dashboard://weekly_margin@7` with `chart-1`, `dashboard://weekly_margin@7-chart`
  with `1`)
- **WHEN** their ids are made
- **THEN** each pair differs, and each id ends in the hash of its own array

### Scenario: an id depends on its ref alone

- **GIVEN** `dashboard://weekly_margin@7` delivered alone, after `dashboard://weekly-margin@7`, and
  before it; then delivered alone with another title
- **WHEN** `decision verify` runs each time
- **THEN** its payloads keep their ids and digests whatever is delivered beside them, and the
  other title keeps every id and moves the digest of the `dashboard` payload

### Scenario: a bundle sealed under slug ids still reads

- **GIVEN** the tracked pilot bundle of `docs/pilot/2026-08-25-ch-control`, sealed by verifier 2
  with `claim-asset-sales-weekly-margin-by-region` and `bi-dashboard-weekly-margin-7-dataset-1`
- **WHEN** it is read and re-verified
- **THEN** it re-verifies with no problem and keeps those ids

## Known boundaries

- A dashboard ref repeated in `delivery.dashboards` passes `decision validate`; when its spec
  compiles, its payloads still get one id twice, and `decision verify` ends in the same
  traceback. A delivery of one ref twice is a rule of the product contract (`DeliverySpec`), not
  of the id.
- Other ids of the bundle are not named after a ref (`check-<check id>`, `snapshot-base`,
  `report-summary-md`) and are not changed. The evidence ids of an investigation brief are already
  the hash of their step and metric ref (§12.1).
- Bundles written before verifier 113 are not rewritten and read as before, under their slug ids.
  None of them holds the claims of two equivalent assets of one slug or the payloads of two
  compiled dashboards of one slug: such a run wrote no bundle. Their checks can still name two
  refs of one slug, of which only one made a claim or payloads.
- An id is long: the slug, the payload id and 64 hex digits.
