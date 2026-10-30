# Capability: The validation report is one report on every system

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

`decision validate` and `decision verify` read every contract file under the paths they are
given (`load_contracts`, `auto_bi/decision/validate.py`). An issue of a file names the file:
as the `subject` and the `source` of a path that is not there, of one that may not be
looked at, of a directory that cannot be listed and of a file that cannot be read; as the
`source` of a file of an unknown kind, of one its schema refuses and of one that defines an id
again. A decision product its schema refused carries the same name, printed by a run that
cannot find its product ([schema-refused products](schema-refused-products.md)). The report is
sealed whole as an input of a run and is part of its idempotency key
([run identity](run-identity.md)), and of an id two files define, the run is handed the contract
of the file read first.

The path was written by `str`, which joins names by the separator of the system, and what a
validated directory holds was read in the order `sorted` gives paths, which on Windows folds
case. The same files, read by the same relative paths on Windows and on POSIX, gave issues in
other words and in another order, and of a metric id two files define, the run was handed the
contract of the other file: another report, other validated contracts, another run id.

Reproduced at HEAD `9ef1811`, 2026-10-28, the same files read as `contracts` and `dup` relative
to the working directory. `contracts/sub/B.yaml` and `contracts/sub/a.yaml` of an unknown kind
gave on Windows the issues of `contracts\sub\a.yaml`, then of `contracts\sub\B.yaml`, and on
macOS of `contracts/sub/B.yaml`, then of `contracts/sub/a.yaml`. Of
`metric://sales/gross_margin_pct`, defined in `dup/B.yaml` with the owner `team-b` and in
`dup/a.yaml` with `team-a`, Windows kept the contract of `a.yaml` and named `dup\B.yaml` in
`contract.duplicate_id`; macOS kept `B.yaml` and named `dup/a.yaml`. One `decision verify` over
one set of files, the same bytes on both, was `run-42d88e9a70ec9b55` on Windows and
`run-32f45c3e4019b6ef` on macOS.

## Requirement: a file is named with `/` between its names

The validation report SHALL name a file — in the `source` of an issue of the file, in the
`subject` of a `contract.unreadable`, and in the `source` of a decision product its schema
refused — with `/` between the names, on every system. The `subject` of a `contract.invalid`
stays what it was: the id the file declares, or the file's name alone where it declares none.
Since verifier 121 the name is the place of the validated path that reached the file, `$1` for
the first, then the names below that path ([validated path names](validated-path-names.md));
verifiers 118 to 120 wrote the path the file was reached by, as `pathlib` writes it — relative where that
was relative, absolute where it was absolute, a drive written `C:/`. Each name SHALL be kept as it
is listed: a `\` a name holds, where a name may hold one, is part of the name and not turned into
`/`. `decision validate` prints these texts, and a run that cannot find its product names the file
of a refused product and the paths that could not be read by them
([one-line errors](one-line-errors.md)).

### Scenario: an issue of a file

- **GIVEN** `contracts/sub/x.yaml` relative to the working directory, holding text that is not
  YAML; a list; a mapping of an unknown `kind`; a `MetricContract` its schema refuses
- **WHEN** it is validated as `contracts`, given as a path and as text, and as the file itself
- **THEN** the report holds one issue, `contract.unreadable`, `contract.unreadable`,
  `contract.unknown_kind` and `contract.invalid` in turn, with the `source` `$1/sub/x.yaml`, and
  `$1` where the file itself is validated, on every system

### Scenario: a file that defines an id again

- **GIVEN** `contracts/sub/a.yaml` and `contracts/sub/b.yaml` holding one metric contract
- **WHEN** `contracts` is validated
- **THEN** the one issue is `contract.duplicate_id` of `metric://sales/gross_margin_pct` with
  the `source` `$1/sub/b.yaml`

### Scenario: paths that cannot be gone through

- **GIVEN** `contracts/sub/gone.yaml`, which is not there, given as a path and as text; the file
  `contracts/sub/x.yaml`, a look at which is refused with `PermissionError`, given itself; and
  `contracts/sub/deep`, a directory the process may not list, under the validated `contracts`
- **WHEN** each is validated
- **THEN** each report holds one `contract.unreadable` whose `subject` and `source` are `$1`
  (`path does not exist`), `$1` (`cannot look at path: PermissionError`) and `$1/sub/deep`
  (`cannot list directory: PermissionError`)

### Scenario: a file under an absolute path

- **GIVEN** a file of an unknown kind at `<directory>/contracts/sub/x.yaml`
- **WHEN** `<directory>/contracts` is validated, an absolute path
- **THEN** the `source` is `$1/sub/x.yaml`, nothing of the absolute path in it

### Scenario: a backslash a name holds

- **GIVEN**, where a name may hold `\`, a file of an unknown kind named `a\b.yaml` in `contracts`
- **WHEN** `contracts` is validated
- **THEN** the `source` is `$1/a\b.yaml`

### Scenario: a product its schema refused

- **GIVEN** `contracts/sub/weekly_margin.yaml` holding the decision product with one dashboard
  delivered twice
- **WHEN** `contracts` is validated
- **THEN** the product the schema refused has the `source` `$1/sub/weekly_margin.yaml`, as has
  its `contract.invalid`
- **AND** a `decision verify` of that product over `contracts` is refused with a usage error that
  ends `'decision://sales/weekly_margin' in $1/sub/weekly_margin.yaml (<what the schema
  refused>)`

### Scenario: what `decision validate` prints

- **GIVEN** `contracts/sub/x.yaml` of an unknown kind
- **WHEN** `decision validate contracts` runs, and with `--json`
- **THEN** it exits 2, no product being found; its report prints the issue's message followed by
  `[$1/sub/x.yaml]`, and the JSON issue has that `source`

## Requirement: what a directory holds is read in the order of its paths

The files a validated directory holds SHALL be read, and the directories under it that cannot be
listed SHALL be named, in the order of their paths: name by name from the directory down, each
name compared by code point with its case, on every system. `B.yaml` stands before `a.yaml`, and
what `sub/` holds before `sub-x.yaml` and `sub.yaml`. The validated paths SHALL still be gone
through in the order they are given, and a file two of them reach read once, under the first of
them ([contract files once](contract-files-once.md)). Of an id two files define, the contract of
the first of them in this order SHALL be the one kept and handed to a run, and the other file is
`contract.duplicate_id`. The decision products the schema refused SHALL be named in this order.
In the report, issues of paths that could not be gone through come first, across all validated
paths; file reading issues follow in the order the files were taken; product rules come last,
the products by id ([validated path names](validated-path-names.md)).

On POSIX this is the order `sorted` gave, and a file was already named with `/`: at verifier 118
the report of a set of files read there was the report it was.

### Scenario: the files of a directory

- **GIVEN** `contracts` holding `sub.yaml`, `a.yaml`, `sub-x.yaml`, `B.yaml`, `sub/x.yaml` and
  `sub/Y.yaml`, each of an unknown kind
- **WHEN** `contracts` is validated
- **THEN** the issues name `$1/B.yaml`, `$1/a.yaml`, `$1/sub/Y.yaml`, `$1/sub/x.yaml`,
  `$1/sub-x.yaml` and `$1/sub.yaml`, in that order

### Scenario: the paths given keep their order

- **GIVEN** `contracts/z/a.yaml`, `contracts/z/B.yaml` and `contracts/a/x.yaml`, each of an
  unknown kind
- **WHEN** `contracts/z` and then `contracts/a` are validated
- **THEN** the issues name `$1/B.yaml`, `$1/a.yaml` and `$2/x.yaml`, in that order

### Scenario: an id two files define

- **GIVEN** one metric contract with the owner `team-kept` in `contracts/B.yaml` and with
  `team-again` in `contracts/a.yaml`; or, in another tree, with `team-kept` in
  `contracts/sub/m.yaml` and with `team-again` in `contracts/sub.yaml`
- **WHEN** `contracts` is validated, in each tree
- **THEN** in each the contract kept is the one of `team-kept`, and the one issue is
  `contract.duplicate_id` with the `source` `$1/a.yaml` in the first tree and `$1/sub.yaml` in
  the other

### Scenario: a name compared as it is listed

- **GIVEN** `contracts` holding `f.yaml` and a file whose name is `e`, a combining acute accent
  (U+0301) and `.yaml` — a name NFC writes with one code point above `f`; or `f.yaml` and that
  name written with the one code point U+00E9; each of an unknown kind
- **WHEN** `contracts` is validated on a filesystem that hands a name back as it was written
- **THEN** the issue of the decomposed name stands before the issue of `$1/f.yaml`, and the issue
  of `$1/f.yaml` before the issue of the composed name

### Scenario: directories that cannot be listed

- **GIVEN** `contracts/sub-x`, `contracts/a`, `contracts/sub/deep` and `contracts/B`, directories
  the process may not list
- **WHEN** `contracts` is validated
- **THEN** the issues name `$1/B`, `$1/a`, `$1/sub/deep` and `$1/sub-x`, in that order

### Scenario: products the schema refused

- **GIVEN** `contracts/a.yaml` and `contracts/B.yaml`, each holding the decision product with one
  dashboard delivered twice
- **WHEN** `contracts` is validated
- **THEN** the products the schema refused are named by `$1/B.yaml`, then `$1/a.yaml`

### Scenario: the run seals the report

- **GIVEN** the run's contracts with `notes/a.yaml` and `notes/B.yaml` of an unknown kind beside
  them, read as `contracts` relative to the working directory
- **WHEN** `decision verify` runs
- **THEN** `contracts.validate` blocks, and its sealed issues are `contract.unknown_kind` of
  `$1/notes/B.yaml`, then of `$1/notes/a.yaml`

### Scenario: one set of files on two systems

- **GIVEN** one set of files, the same bytes on Windows and on macOS: the run's product, its
  metric contract in `contracts/m/B.yaml` (owner `team-b`) and in `contracts/m/a.yaml`
  (`team-a`), and files of an unknown kind in `contracts/notes/`, `B.yaml`, `a.yaml`,
  `sub/x.yaml` and `sub-x.yaml`
- **WHEN** `decision verify` runs over `contracts`, relative, with one `created_at`, on each
- **THEN** the sealed issues, the contract the run is handed (`team-b`), the checks and the run
  id are one on both: `run-e5aebfe5ee3db71f`, at verifier 118 (checked by a script run on both
  systems, not by the suite). At verifier 121 a script compared relative and absolute contracts
  on Windows and macOS: all four gave `run-d2f5a2a0e526b0e6` and the same issues; the files
  and limits are stated in [validated path names](validated-path-names.md).

## Versions

Verifier 117 → 118. On Windows the same contracts give another validation report — names with
`/`, files in another order where case sorted them otherwise — and of an id two files define, the
other contract where case put the other file first; with them another run id and other sealed
artifacts. On POSIX the report is the one it was, and only the version moves the run id. Check
79 and native source 3 are unchanged: no check, issue or digest means anything new. Historical
bundles SHALL NOT be rewritten.

### Scenario: the versions

- **WHEN** the versions are read
- **THEN** `VERIFIER_VERSION` is at least 118 and `CHECK_VERSION` at least 79

## Known boundaries

- Under verifiers 118 to 120 a path was named as it was given or reached: where an issue named a file,
  the same contracts given by absolute paths that differ — on two machines, in two directories —
  or by two paths `pathlib` writes otherwise to one directory (`contracts` and
  `../repo/contracts`) were two reports and two runs. Since verifier 121 a path is named by the
  place of the validated path that reached it, and they are one subject to the exceptions for an
  invalid file's subject and paths without an identity in
  [validated path names](validated-path-names.md); a report none of whose issues names a file
  was one report wherever its files lie ([run identity](run-identity.md)).
- A snapshot's source digest is of the bytes of its files: the same contracts checked out with
  CRLF on one system and LF on another are two snapshots and two runs. The scenario of two
  systems wrote every file with LF on both; written by each system's text mode, the runs
  differed by that alone.
- A name is compared as the text the filesystem lists. Names of one text in two Unicode
  normalization forms are two names, and a system that hands back another form than was written
  names and orders them as it hands them back.
- Where the filesystem folds case, `M.yaml` and `m.yaml` are one file; where it does not, two,
  and `M.yaml` stands first (tested only on such a filesystem: Windows and macOS fold case by
  default).
- The message of an issue holds the class of the refusal (`PermissionError`, `ParserError`) the
  system raised; a system that refuses with another class gives another message.
- What a command prints of a path outside the validated paths — the evidence, the dashboards, the
  assets, the recorded results — is written as the system writes a path, as before
  ([one-line errors](one-line-errors.md)).
- Under verifiers 118 to 120 a path that cannot be gone through was an issue for each path that named
  it; since verifier 121 it is one issue, however many validated paths reach it
  ([validated path names](validated-path-names.md)).
- The contracts of a semantic ref are frozen in an order of their own and named by their file
  names alone ([contract read order](contract-read-order.md)); this capability does not change
  them.
