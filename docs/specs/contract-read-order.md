# Capability: A snapshot freezes its contracts in an order of what they hold

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

A native snapshot reads `model.yaml` and every `.yaml`/`.yml` contract file below the contracts
path of a semantic ref, its sub-directories included (`NativeModelSource`,
`auto_bi/semantic/gateway.py`). Its source digest keeps each contract's digest under its file
name, not under the directory that holds it, so a contract moved between those directories
leaves the digest as it was ([run identity](run-identity.md), verifier 110). The rest of the
snapshot followed the order the files were listed in — `sorted` paths, which compare the
directory before the name and, on Windows, fold case. That order put the gaps of two contracts
in place, and the nodes of a metric id that two contracts define. The snapshot is sealed whole
and is part of the idempotency key, so the same files gave another snapshot and another run id
when one of them moved to another directory, or when the ref was read on a system that sorts
case another way: with the source digest and the canonical digest unchanged where only gaps
moved, and with another canonical digest where the nodes of an id did.

Reproduced at HEAD `17f59f8`, 2026-10-27, on Windows: two refs holding the same model and two
contracts of reviewed SQL, `metric://sales/x` and `metric://sales/y`, as `x.yaml` and `y.yaml`
in one and as `z/x.yaml` and `y.yaml` in the other, had one source digest and one canonical
digest, and their two `gap.opaque_metric_formula` stood in the other order; `decision verify`
over the two as candidates gave two idempotency keys and two run ids. `x.yaml` and `Y.yaml` sort
`x.yaml` first on Windows and last where case is kept.

## Requirement: the files and contracts of a snapshot are taken in an order of their own

A snapshot SHALL answer the contract files of its ref in an order that neither the directory of
a file nor the order of a listing is part of. A file that does not load as a metric contract
SHALL be answered — its `gap.contract_invalid` — in the order of file names, compared by code
point, and of names alike in the order of the digests of their bytes. A contract that loads
SHALL stand in the graph, and its gaps SHALL be answered, in the order of metric ids, then of
versions as numbers, then of the digests of the bytes of their files. The gaps of one contract
keep their order among themselves, and the gaps of files that do not load, of the model and of
contracts keep their order among each other: files first, then the model, then contracts, then
`gap.metric_duplicate` in the order of ids, as before. The source digest and the canonical
digest are computed as before.

A ref is frozen into the snapshot it was before when its listing already gave the files that
do not load in the order of their names and bytes, and the contracts that load in the order of
their ids, versions and bytes. Every semantic ref tracked in this repository, the pilot refs and
`semantic/`, is frozen into the snapshot the gateway of `17f59f8` froze it into.

### Scenario: a contract moved to another directory

- **GIVEN** two refs with the same model and two contracts of reviewed SQL, `metric://sales/x`
  and `metric://sales/y`: as `x.yaml` and `y.yaml` in one, as `z/x.yaml` and `y.yaml` in the
  other
- **WHEN** both are frozen
- **THEN** the snapshots are equal, their two `gap.opaque_metric_formula` the gap of `x` first

### Scenario: file names that do not sort as their ids

- **GIVEN** a ref with the contract of `metric://sales/y` in `a.yaml` and of `metric://sales/x` in
  `b.yaml`, both of reviewed SQL; or in `Y.yaml` and `x.yaml`; or in `y.yaml` and `X.yaml`
- **WHEN** it is frozen
- **THEN** in each case the gap of `x` stands before the gap of `y`, on every system

### Scenario: the gaps of one contract keep their order

- **GIVEN** a ref with `a.yaml` holding `metric://sales/y` and `b.yaml` holding `metric://sales/x`
  of reviewed SQL whose grain names `zz_key` and `aa_key`, which the model does not declare
- **WHEN** it is frozen
- **THEN** the gaps are the formula gap of `x`, its missing `aa_key` and `zz_key`, then the
  formula gap of `y`

### Scenario: two contracts of one metric id

- **GIVEN** two refs with version 4 of a metric in `a/m.yaml` and version 3 in `b/m.yaml`, and
  the other way round; and a ref with version 10 in `a.yaml` and version 9 in `b.yaml`
- **WHEN** they are frozen
- **THEN** the first two are one snapshot, one graph and one canonical digest, with the nodes of
  the id at versions 3 and 4 and a lookup of the id finding version 3; the third holds versions
  9 and 10 in that order

### Scenario: two contracts of one id and one version

- **GIVEN** two contracts of one id at version 3 that name different owners, as `a.yaml` and
  `b.yaml` in one ref, swapped in another, and as `z/a.yaml` and `b.yaml` in a third
- **WHEN** they are frozen
- **THEN** the nodes stand in the order of the digests of their files' bytes, the first two refs
  have one graph and one canonical digest, and the third is the snapshot of the first

### Scenario: files that do not load

- **GIVEN** a ref with unparsable `z/a.yaml` and `b.yaml`; and a ref with unparsable `a.yaml`
  and `B.yaml`
- **WHEN** they are frozen
- **THEN** the gaps are `contracts.a.yaml` then `contracts.b.yaml`, and `contracts.B.yaml` then
  `contracts.a.yaml`

### Scenario: a name compared as it is listed

- **GIVEN** a ref with unparsable `f.yaml` and a file whose name is `e`, a combining acute accent
  (U+0301) and `.yaml` — a name NFC writes with one code point above `f`; and a ref with
  unparsable `f.yaml` and that name written with the one code point U+00E9
- **WHEN** they are frozen on a filesystem that hands a name back as it was written
- **THEN** the gap of the decomposed name stands before the gap of `f.yaml`, and the gap of
  `f.yaml` before the gap of the composed name

### Scenario: files of one name that do not load

- **GIVEN** two refs with `a/m.yaml` and `b/m.yaml` holding two different texts that do not load,
  and the texts swapped
- **WHEN** both are frozen
- **THEN** the snapshots are equal, and the two gaps stand in the order of the digests they name

### Scenario: files before the model before contracts

- **GIVEN** a ref with unparsable `0/z.yaml`, `a.yaml` holding `metric://sales/y` and `m/b.yaml`
  holding `metric://sales/x`, both of reviewed SQL
- **WHEN** it is frozen
- **THEN** the gaps are `contracts.z.yaml`, then the formula gap of `x`, then of `y`

### Scenario: the listing does not give the order

- **GIVEN** a ref holding contracts that load and files that do not, of one name and of one id
  in several directories
- **WHEN** it is frozen with its files handed in the opposite order of their listing
- **THEN** the snapshot is the one frozen from the listing

### Scenario: a candidate laid out another way is the same run

- **GIVEN** two `decision verify` runs over the same contracts, base, dashboards, verified asset
  and recorded result, the candidate holding the pinned metric's contract and the contracts of
  reviewed SQL of `metric://sales/x` and `metric://sales/y`: as `x.yaml` and `y.yaml` in one,
  as `z/x.yaml` and `y.yaml` in the other
- **WHEN** both run with the same `created_at`
- **THEN** `semantic.gaps` is review, and the idempotency keys, the run ids and the digests of
  every sealed artifact are equal

### Scenario: names that only case tells apart

- **GIVEN**, on a filesystem that keeps case, a ref with version 4 of a metric in `m.yaml` and
  version 3 in `M.yaml`
- **WHEN** it is frozen
- **THEN** both are contracts of the ref, the nodes stand at versions 3 and 4, and the one gap is
  `gap.metric_duplicate`

## Versions

Verifier 116 → 117: the same ref can be frozen into another snapshot, and with it another run
id and other sealed artifacts; the checks a duplicated id leaves not run give another reason,
`a semantic ref holds more than one contract of a metric id, so it does not say which of them a diff should compare`,
no longer speaking of the order of files. Check 79 and native source 3 are unchanged: no check,
gap or digest means anything new.

## Known boundaries

- A name is compared as the text the filesystem lists. Names of one text in two Unicode
  normalization forms are two names, and a system that hands back another form than was written
  orders them as it hands them back.
- `decision validate` reads the validated paths as before
  ([contract files once](contract-files-once.md)). Its report names the products and metrics it
  validated by their refs, an issue of a product by the product's ref, and an issue of a file —
  one that does not load, an id two files define — by the file's path. The run id addresses that
  report and the contracts the run is handed as they are: moving a file that has an issue
  between the validated directories is another report and another run, and of an id two
  validated files define the run is handed the contract read first. A report none of whose
  issues is of a file holds no path.
- The same ref is now one snapshot on every system, and its sealed snapshot artifact one
  artifact. The path an issue of a file names is spelled as the system spells it: where it holds
  a directory, the same contracts read on Windows and on POSIX are still two runs.
- `metrics/` is still read by the snapshot alone: a hard link or symbolic link to a contract is
  a second file there, and its id is `gap.metric_duplicate` ([two contracts of one metric
  id](repeated-metric-contracts.md)).
- A run with two contracts of a pinned metric in its candidate is blocked, as before. With an
  engine configured, the environment checks ask about the contract a lookup of the id finds —
  now the lowest version, then the lowest digest of bytes — wherever its file lies.
