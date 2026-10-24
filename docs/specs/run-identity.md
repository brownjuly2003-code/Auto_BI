# Capability: Run identity — the idempotency key covers every static gate input

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

DECISIONOPS_ARCHITECTURE §7.6 makes the run id a function of the idempotency key, so that the
same verification writes the same bundle path, and a prior terminal bundle may be reused only
under the same key. Until verifier 91 the key was built from the product spec, the canonical digests
of the two snapshots, the policy, the environment ref, the signing identity and the verifier
version. The gate is handed more than that, and every other input reaches the evidence: the
validation report and the validated metric contracts are graded by `contracts.validate`, the
verified assets by `assets.invalidated`, `results.reconcile` and `results.provenance`, the
dashboard specs and the candidate model by `impact.products` and `bi.contract`, the snapshot
gaps by `semantic.gaps`, and both snapshots are sealed whole, with the digest of the bytes they
were read from.

Reproduced on HEAD `0aa44a8` through `decision verify` on the Mac, two runs that differ in
exactly one of those inputs:

- the validated contract of the pinned metric moved to `draft`: one run id, the verdicts
  `pass_with_advisory` and `block`;
- the expected rows of the verified asset changed: one run id, `results.reconcile` pass with
  `result.equivalent` and block with `result.not_equivalent`;
- a measure of the delivered dashboard pointed at a column the model does not declare: one run
  id, `bi.contract` pass and block with `bi.spec_invalid`;
- an unparsable contract added to the candidate ref (the canonical digest is the same): one run
  id, `semantic.gaps` pass and block, six checks `check.not_run`;
- a comment added to the candidate `model.yaml`, or to the base one: one run id, another
  `snapshot-candidate` (`snapshot-base`) artifact;
- gate inputs with and without the validated contracts: one run id, `contracts.validate`
  review and pass (recorded as a boundary of [product metric pins](product-metric-pins.md)).

The run id therefore did not say which verification a bundle is, and a run repeated into the
same evidence directory after one of those inputs was corrected was refused with "same
idempotency key, different evidence" — the wording of an observation taken at another moment,
for a changed input.

This capability changes what the run id is derived from. It changes no check, no finding, no
verdict and no artifact other than through the run id they carry.

## Requirement: the idempotency key covers every static input the gate is handed

The idempotency key SHALL be derived from every input of `GateInputs` that is not an observation
of a moment, in full: the product spec, the validation report, the validated metric contracts by
metric id, the base snapshot and the candidate snapshot whole (the graph, the gaps, the source
digest and the source description), the candidate model, the dashboard specs by ref and the
verified assets — together with the policy, the environment ref, the evidence-signing identity
and the verifier version, as before. Two runs that differ in any of these SHALL have different
idempotency keys and different run ids, whether or not the difference reaches a check. Two runs
that are handed equal inputs SHALL share the key: the order in which the validated contracts,
the dashboard specs or the verified assets are handed does not move it.

### Scenario: a validated contract that changed is another run

- **GIVEN** two `decision verify` runs over the same refs, dashboards, verified asset and
  recorded result, the validated contract of `metric://sales/gross_margin_pct@3` being `active`
  in the first and `draft` in the second
- **WHEN** both run
- **THEN** the run ids differ; the first verdict is `pass_with_advisory` with
  `contracts.validate` pass and `contracts.clean`, the second is `block` with
  `contracts.validate` block and `contracts.issues`

### Scenario: a validated contract the product does not pin is part of the identity

- **GIVEN** the same two runs, the second with a contract of `metric://sales/revenue` added to
  the validated paths, or with a second decision product added to them
- **WHEN** both run
- **THEN** in each case the run ids differ: the validation report names every contract of the
  validated paths

### Scenario: gate inputs with and without the validated contracts are two runs

- **GIVEN** gate inputs whose candidate holds the pinned version as a draft, handed to the gate
  once with the validation report alone and once with the validated contracts beside it
- **WHEN** the gate runs
- **THEN** the run ids differ; `contracts.validate` is pass with `contracts.clean` in the first
  and review with `metric.candidate_draft` in the second

### Scenario: a verified asset that changed is another run

- **GIVEN** two runs that differ only in the expected rows of the verified asset
- **WHEN** both run
- **THEN** the run ids differ; `results.reconcile` is pass with `result.equivalent` in the first
  and block with `result.not_equivalent` in the second

### Scenario: an input that changes no finding still moves the run id

- **GIVEN** two runs that differ only in the review rationale of the verified asset
- **WHEN** both run
- **THEN** the run ids differ, while every check has the same status and summary code in both,
  and of the sealed artifacts only the owner summary, which names the run id, differs

### Scenario: a dashboard spec that changed is another run

- **GIVEN** two runs that differ only in the spec of the delivered dashboard: another measure
  column the model declares, or a measure column it does not declare
- **WHEN** both run
- **THEN** in each case the run ids differ; with the undeclared column `bi.contract` is block
  with `bi.spec_invalid` where the first run is pass with `bi.compiled`

### Scenario: snapshot gaps are part of the identity

- **GIVEN** two runs whose candidate refs hold the same model and the same contract, the second
  with an unparsable contract file beside it, so that both candidate snapshots have one
  canonical digest
- **WHEN** both run
- **THEN** the run ids differ; `semantic.gaps` is block with `semantic.gaps_present` in the
  second

### Scenario: the bytes a snapshot was read from are part of the identity

- **GIVEN** two runs that differ only in a YAML comment appended to the candidate `model.yaml`;
  or to the base `model.yaml`; or in the statistics of a mart in the candidate `model.yaml`
  (`rows`, `cardinality`, `top_values`), which the canonical graph does not carry
- **WHEN** both run
- **THEN** in each case the canonical digests of the two runs are equal and the run ids differ

### Scenario: contracts that share a file name in two directories are both part of the identity

- **GIVEN** two runs whose base and candidate refs each hold two contracts of different metrics
  under one file name in two directories of the metrics directory,
  `metrics/a/gross_margin_pct.yaml` and `metrics/b/gross_margin_pct.yaml`, and that differ only
  in a YAML comment appended to the first of them in the base ref; or in the candidate ref
- **WHEN** both run
- **THEN** in each case the graphs and the canonical digests of the two snapshots are equal and
  every check has the same status and summary code in both, while the source digests and the
  run ids differ — where verifier 109 kept the digest of the last file read under a name and
  gave both runs one run id; a file name keeps the digest of each file that has it, sorted and
  with a repeat as often as it was read, so the two contracts with their directories swapped
  give the same source digest, a byte-identical copy of a contract in another directory moves the
  source digest, and a ref in which no two contracts share a file name keeps the source digest
  verifier 109 gave it

### Scenario: every field of the gate inputs is either in the key or an observation

- **GIVEN** the fields of `GateInputs`
- **WHEN** one field is replaced by a different value and the key is computed again
- **THEN** for `product`, `validation`, `validated_metrics`, `base`, `candidate`,
  `candidate_model`, `dashboards`, `assets` and `environment_ref` the key differs; for `results`
  and `environment` it is the same; and `GateInputs` has no other field

### Scenario: the order inputs are handed in does not move the key

- **GIVEN** gate inputs with two validated contracts, two dashboard specs and two verified
  assets, and the same inputs with each of the three handed in the opposite order
- **WHEN** the key is computed for both
- **THEN** the keys are equal

## Requirement: observations of a moment stay outside the key

The recorded candidate results, the engine's readings (`environment`) and `created_at` SHALL
stay outside the idempotency key, as DECISIONOPS_ARCHITECTURE §7.6 decided: they are what was
observed when the run was made, and keying on them would make every run a new one. Two runs
handed equal static inputs SHALL share the run id whatever was observed, and with equal
observations and the same `created_at` SHALL write a byte-identical bundle. A different bundle
under a sealed run id SHALL be refused, as before.

### Scenario: other recorded results do not move the run id

- **GIVEN** two runs that differ only in the recorded candidate result, each with its own
  evidence directory
- **WHEN** both run
- **THEN** the run ids are equal and the evidence digests differ

### Scenario: other readings and another `created_at` do not move the run id

- **GIVEN** gate inputs run twice with the same static inputs: with another `max_event_time`
  in the engine's readings, and at another `created_at`
- **WHEN** the gate runs
- **THEN** the run id is the same in each case; in one evidence store the second run is refused
  with `EvidenceConflictError` naming "same idempotency key, different evidence", and the first
  bundle still re-verifies

### Scenario: equal inputs and equal observations write one bundle

- **GIVEN** the same contracts, refs, dashboards, verified asset and recorded result laid out in
  two directories
- **WHEN** `decision verify` runs in each with the same `created_at`
- **THEN** the idempotency keys, the run ids and the evidence digests are equal

## Requirement: a run repeated after a static input changed is a new run

`decision verify` repeated into the same evidence directory after a static input changed SHALL
complete under another run id and leave the first bundle as it was. Both bundles SHALL
re-verify.

### Scenario: the validated contract is corrected and the gate is run again

- **GIVEN** a run whose validated contract of the pinned metric is a draft, verdict `block`, and
  the same evidence directory after the validated contract was made `active`
- **WHEN** `decision verify` runs again with the same `created_at`
- **THEN** it completes with the verdict `pass_with_advisory` under another run id — where
  verifier 90 refused it with "refusing to overwrite" and exit 2 — and both bundles re-verify
  with no problem

### Scenario: every changed static input is written beside the first bundle

- **GIVEN** a completed run, and the same evidence directory after one of: the expected rows of
  the verified asset, the review rationale of the verified asset, a measure column of the
  dashboard spec, a comment in the candidate `model.yaml`, a comment in the base `model.yaml`
- **WHEN** `decision verify` runs again
- **THEN** in each case it completes under another run id and both bundles re-verify

## Requirement: the key is total over what the gate can be handed and exact

Deriving the key SHALL NOT stop a run that the gate could otherwise complete. An input may hold
a value the canonical contract form refuses — a binary float or an integer past the IEEE-754
safe range in a dashboard filter, an integer of any size in a mart's statistics, a non-finite
float, a lone surrogate in a text the gate does not seal — and the run SHALL complete as it did
under verifier 90. Two inputs that differ only in such a value SHALL have different keys: the
value is written exactly, an integer never as the double it rounds to, a float never as the
integer it equals, a character outside the BMP never as the two surrogates that would spell
it. What is written for an input SHALL have an order the input fixes: no static input holds a
set, bytes or an untyped value, and every mapping it holds is keyed by text.

### Scenario: an invalid dashboard spec with a non-finite filter value is still graded

- **GIVEN** two runs whose dashboard spec names a filter column the model does not declare, with
  the filter value `.nan` in the first and `.inf` in the second
- **WHEN** both run
- **THEN** each completes with `bi.contract` block and `bi.spec_invalid`, as under verifier 90,
  and the run ids differ

### Scenario: integers past the safe range are told apart

- **GIVEN** two runs whose valid dashboard spec differs only in a filter value:
  `9007199254740992` and `9007199254740993`
- **WHEN** both run
- **THEN** both complete with `bi.compiled` and the run ids differ, although the compiled
  payloads, which hold the value as a JSON number, are equal

### Scenario: an integer and the float it equals are told apart

- **GIVEN** two runs whose valid dashboard spec differs only in a filter value: `1` and `1.0`
- **WHEN** both run
- **THEN** both complete and the run ids differ

### Scenario: statistics of any size are part of the identity

- **GIVEN** two runs whose candidate `model.yaml` declares the row count of a mart as 10^400 and
  as 10^400 + 1
- **WHEN** both run
- **THEN** both complete with the verdict of the run without statistics, and the run ids differ

### Scenario: a lone surrogate outside the sealed evidence does not stop the run

- **GIVEN** a candidate `model.yaml` with the synonym `"\ud800"` on a column; and a validated
  metric contract with the description `"\ud800"`
- **WHEN** `decision verify` runs for each
- **THEN** each completes, as under verifier 90, and its run id differs from the run with
  `"\ud801"` in the same place

### Scenario: a character outside the BMP and the surrogates that would spell it are two runs

- **GIVEN** two runs whose candidate `model.yaml` holds, as the synonym of a column, the
  character U+1F600 written as itself, and the two escapes `"\ud83d\ude00"`, which YAML
  reads as two code points
- **WHEN** `decision verify` runs for each
- **THEN** each completes and the run ids differ, although a JSON text spells both strings as
  `"\ud83d\ude00"`

### Scenario: the exact text of an input

- **GIVEN** the value `{"b": [1, -1, 9007199254740993, 1.5, -0.0, inf, nan, null, true,
  false], "a": "ж\ud800\"", "": {}}`
- **WHEN** its identity text is written
- **THEN** it is `{'':{},'a':'\u0436\ud800"','b':[0x1,-0x1,0x20000000000001,
  0x1.8000000000000p+0,-0x0.0p+0,inf,nan,null,true,false]}` on one line: objects by sorted
  key, integers and floats in hexadecimal, strings as ASCII literals that read back as the
  string; an integer of 5000 decimal digits has a text, and a value that is not a dumped JSON
  value — an object key that is not a string, a tuple — raises `TypeError`

### Scenario: the text of a string reads back as that string

- **GIVEN** strings of every kind: empty, with either quote and with both, a backslash, a line
  feed, a control character, Latin-1 and Cyrillic letters, a lone surrogate, a character outside
  the BMP, two surrogates in a row
- **WHEN** the identity text of each is written
- **THEN** it is ASCII and reads back, as a string literal, as the same sequence of code points;
  a mapping writes its keys the same way

### Scenario: no static input is dumped in an order the process chooses

- **GIVEN** the models of the static inputs: the validation report, a metric contract, a
  snapshot, the semantic model, a dashboard spec, a verified asset
- **WHEN** every type their fields can hold is listed, a type named by a `type` statement read
  through to the type it names
- **THEN** none is a set, bytes or an untyped value, and every mapping is keyed by text; the
  aggregate checks of a verified asset's tolerance, declared through such a statement, are
  among the types listed

## Requirement: the verifier version is bumped and the check version is not

`VERIFIER_VERSION` SHALL be 91: the same inputs produce another run id, so a bundle of
verifier 90 is not reused under today's key. `CHECK_VERSION` SHALL stay 66: no check is graded
by another rule. Historical bundles SHALL NOT be rewritten; they keep the run ids their
verifiers derived.

### Scenario: the versions

- **GIVEN** the release gate
- **WHEN** its versions are read
- **THEN** `VERIFIER_VERSION` is `91` and `CHECK_VERSION` is `66`

## Known boundaries

- Observations of a moment are outside the key by decision (§7.6). A second run with other
  recorded results, other readings or another `created_at` shares the run id of the first and is
  refused in the same evidence directory; each run keeps its own directory. `decision verify`
  without `--created-at` takes the clock, so repeating it into one directory is refused even
  with nothing changed.
- The key's components are not recorded in the bundle. The manifest names the run id; which
  inputs it was derived from can be recomputed from the inputs, not read from the evidence.
- The key covers inputs in full, not the part of them a check reads. An input that changes no
  finding — the review rationale of a verified asset, a comment in `model.yaml` — is another
  run with the same checks.
- The validation report names every product and metric contract of the validated paths and
  carries their issues, so a contract of another product in those paths moves this product's
  run id. Under verifier 91 it moved this product's `contracts.validate` as well: an issue of
  another product was graded on this product's run. Since verifier 92 the check seals such an
  issue apart and does not grade it
  ([the validation issues a run grades](validation-issue-scope.md)); the run id still moves.
- A file-level issue of the validation report names its file by the path it was read from. The
  same contracts read from another directory are then another report, another
  `contracts.validate` artifact — as before — and another run id.
- `decision verify` hands the gate the dashboard specs the product pins and the verified assets
  that name one of its metrics. A file outside that selection does not reach the gate and does
  not move the run id.
- A valid dashboard spec whose compiled payload cannot be canonicalized — a non-finite filter
  value, an integer beyond the range of a double, a lone surrogate in a title — still stops the
  run in `bi.contract` with `CanonicalizationError`, as under verifier 90. Deriving the key
  adds no stop of its own; whether such a spec is graded instead is outside this capability.
- The identity text is private to the key. Evidence stays RFC 8785; no artifact is written in
  the identity text and no digest of it appears in the bundle.
