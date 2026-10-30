# Capability: A validated path is named by its place, wherever it lies

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

`decision validate` and `decision verify` read the contract files under the paths they are given
(`load_contracts`, `auto_bi/decision/validate.py`), and an issue of a file names the file: as the
`subject` and the `source` of a path that is not there, of one that may not be looked at, of a
directory that cannot be listed and of a file that cannot be read; as the `source` of a file of an
unknown kind, of one its schema refuses and of one that defines an id again; and as the file of a
decision product its schema refused. The report is sealed whole as an input of a run and is part
of its idempotency key ([run identity](run-identity.md)). Where another capability says such an
issue carries the path of a file, it carries the name this capability gives.

Under verifiers 118 to 120 the name was the path the file was reached by, with `/` between its names
([validation report paths](validation-report-paths.md)): a validated path given as `contracts`
named a file `contracts/notes/x.yaml`, and given absolute it named it by the directory the
checkout lies in. The same files given by another spelling of the path — absolute or through
`..` — or by absolute paths from another checkout directory were another report and another run
id once an issue named a file, and so were the same commit verified in two places or on two
machines. And a path that could not be gone through was one `contract.unreadable` for each
validated path that reached it ([contract files once](contract-files-once.md)).

Reproduced at HEAD `0e8db1f`, 2026-10-28, on Windows, Python 3.13.7: two checkout directories of
one set of files, the run's contracts and `contracts/notes/x.yaml` of an unknown kind beside them.
`decision verify` over `contracts` and over `./contracts/` from the first checkout, and over
`contracts` from the second, was `run-7f20d0c191e269c4`, the issue named
`contracts/notes/x.yaml`; over the absolute path of each checkout's contracts it was
`run-d2ed1ef552005548` and `run-79759f5bdf023881`, the issue named by that path; over
`../checkout-a/contracts` from the first, `run-4e0d18410c1a976a`. Without the file the six runs
were one, `run-cb2975a3abe3210c`. A path that is not there, given twice or relative and absolute,
was two issues, and so was a directory that cannot be listed, given and reached under another
validated path; `decision verify` given the contracts and a path that is not there was
`run-8f5e90ba924d3b01`, given that path twice `run-60a6d13344dcb24f`.

## Requirement: a path is named by the place of the validated path that reached it

The validation report SHALL name a path it reached by the place of the validated path that reached
it — `$1` for the first path given, `$2` for the second — and then the names below that validated
path, joined by `/` on every system: `$1/notes/x.yaml`. A validated path itself SHALL be named by
its place alone: a file given itself, a path that is not there, one that may not be looked at, a
directory that cannot be listed. The place SHALL be counted among every path given, in the order
they are given; a path given again keeps a place of its own. The names below SHALL be the names
the directories listed, each kept as it is listed
([validation report paths](validation-report-paths.md)). Nothing of how the validated path was
spelled — relative or absolute, `.`, `./`, a trailing separator, `..`, a drive, a UNC share, an
extended `\\?\` path, case — and so nothing of where it lies SHALL be in a name. Subject to the
two exceptions below, the same files given by the same paths in the same order, each spelled any
way, from any working directory or checkout directory, SHALL be one report, and one run id where
nothing else the run reads differs. The exceptions, also stated in Known boundaries: the `subject` of
`contract.invalid` without a declared id keeps the file name as given when the file is given
itself; paths without a filesystem identity are taken again when their absolute texts differ.
The `source` and the `subject` of `contract.unreadable` still use the name defined above.

A name begins with `$` and is never the ref of a product, so an issue that names a path is graded
on every product's run, whatever text the path was given as
([the validation issues a run grades](validation-issue-scope.md)). `decision validate` prints these
names, and a run that cannot find its product names by them the file of a refused product and the
paths that could not be read, spelled as a diagnostic line spells a path
([one-line errors](one-line-errors.md)).

### Scenario: the same files wherever they lie

- **GIVEN** two checkout directories of one set of files: the run's contracts and
  `contracts/notes/x.yaml` of an unknown kind beside them
- **AND** one `created_at` (`CREATED_AT` of `tests/test_decision_verify.py`), the same other
  inputs and observations in every run
- **WHEN** `decision verify` runs, from the first checkout, over `contracts`, `./contracts/`,
  `contracts` with a trailing separator, the absolute path of its contracts and
  `../checkout-a/contracts`; and, from the second, over `contracts`, the absolute path of its
  contracts and `../checkout-a/contracts`, the contracts of the first
- **THEN** every run seals the one issue `contract.unknown_kind` of `$1/notes/x.yaml`, and the
  eight are one run id and one evidence digest

### Scenario: the working directory given as `.`

- **GIVEN** a working directory holding `notes/x.yaml` of an unknown kind
- **WHEN** it is validated as `.`, as an empty `Path()`, as `./` and as `notes/..`
- **THEN** each report names `$1/notes/x.yaml`

### Scenario: a drive, an extended path and a share

- **GIVEN**, on Windows, `<directory>/contracts/notes/x.yaml` of an unknown kind, the working
  directory `<directory>`
- **WHEN** the contracts are validated as `<drive>contracts`, relative to the drive's working
  directory; with `/` between the names; as the extended path `\\?\<directory>\contracts`; in
  lower case; and, where the administrative share reaches the directory, as
  `\\localhost\<drive letter>$\...`
- **THEN** each report names `$1/notes/x.yaml`

### Scenario: the names below the validated path alone

- **GIVEN** a validated path and `notes/x.yaml` joined to it by the walk, the path being
  `C:/w/contracts`, the drive `C:` alone, the UNC share `//server/share/`, the extended path
  `//?/C:/w`, the root `/`, two slashes `//`, `.` or `..`
- **WHEN** the path below is named, on every system
- **THEN** it is `$1/notes/x.yaml`, and the validated path itself is its place alone

### Scenario: the place among every path given

- **GIVEN** a directory and a path that is not there
- **WHEN** they are validated as the directory, the directory again and the path; as the directory
  and the path; and as the path and the directory
- **THEN** the one issue of the path names it `$3`, `$2` and `$1`

### Scenario: a file several validated paths reach

- **GIVEN** `contracts/notes/x.yaml` of an unknown kind
- **WHEN** it is validated as `contracts` and `contracts/notes`; as `contracts/notes` and
  `contracts`; as the file and `contracts`; and as `contracts` twice
- **THEN** the one issue names `$1/notes/x.yaml`, `$1/x.yaml`, `$1` and `$1/notes/x.yaml`

### Scenario: argument order in a report without path names

- **GIVEN** two products with different ids, each pinning its own metric contract, with both
  metrics active or both draft
- **WHEN** the four files are each given as a path, in descending product and metric id order,
  and then in the reverse order
- **THEN** both reports have equal JSON and name no path: no issues for active, two
  `metric.draft` issues for draft in product id order, each with its product ref as `source`
  and its metric ref as `subject`
- **AND** the reports' product refs and metric refs are sorted
- **AND** `test_argument_order_keeps_a_report_without_path_names` pins both cases

### Scenario: what `decision validate` prints

- **GIVEN** `contracts/notes/x.yaml` of an unknown kind
- **WHEN** `decision validate` runs, and with `--json`, over `contracts`; and over three paths:
  `./contracts/`, the absolute path of the contracts and the file relative to the working directory
- **THEN** both exit 2, no product being found, and print the same: the issue followed by
  `[$1/notes/x.yaml]`, and nothing of the directory the files lie in

### Scenario: a name below a validated path on the line of a refusal

- **GIVEN** the run's contracts without the product's file, and `notes/a<U+2028>b.yaml` holding a
  list
- **WHEN** `decision verify` runs over the contracts
- **THEN** the refusal ends `; 1 path(s) could not be read: '$1/notes/a\u2028b.yaml' (contract file
  must contain a YAML mapping)`: the name the directory listed, quoted, the line separator spelled
  by its escape

## Requirement: a path is gone through once, however many validated paths reach it

A path SHALL be taken once, under the first validated path that reached it, whatever it is: a file
is read once, as before ([contract files once](contract-files-once.md)), and a path that is not
there, one that may not be looked at and a directory that cannot be listed SHALL each be one
`contract.unreadable`. Two paths are one when the filesystem gives them one identity
(`filesystem.file_identity`) or when their absolute texts are one: the text of the path with the
working directory, or the drive's, put before it where it is relative — `.` and doubled
separators dropped as `pathlib` drops them, `..` and case kept. Where the working directory
cannot be told, a relative path SHALL keep its own text. A directory reached again may be listed
again; its files are not read twice and its unlisted directories are not reported twice.

The issues SHALL keep three phases: first, paths that could not be gone through, for all
validated paths in the order given and, under one path, in the order of their paths; then issues
from reading files, in the order the files were taken (unknown kind, invalid contract, duplicate
id, unreadable or unparsable file); then the rules of each product, the products by id. A run
given a path again, after every other path it is given, SHALL keep the report, the run id and the
verdict of it given once. Its evidence digest SHALL be the same where `created_at`, the other
inputs and the observations are the same. The CLI takes `created_at` from the clock unless
`--created-at` is given.

### Scenario: a missing path after a file that does not parse

- **GIVEN** `bad.yaml` holding `tables: [a, b` and `missing.yaml`, which is not there
- **WHEN** `validate_paths([bad, missing])` validates the file and then the missing path
- **THEN** the report holds `contract.unreadable` of `$2`, `path does not exist`, then
  `contract.unreadable` of `$1`, `cannot read contract: ParserError`
- **AND** `test_a_missing_path_is_reported_before_an_earlier_files_parse_error` in
  `tests/test_validated_path_names.py` pins this order

### Scenario: a path that is not there, given again

- **GIVEN** `<directory>/contracts` and `<directory>/contracts/gone.yaml`, which is not there
- **WHEN** they are validated as the directory, the path, and the path again — spelled the same,
  relative to the working directory `<directory>`, or with `./` in it
- **THEN** each report holds one `contract.unreadable`, `$2`, `path does not exist`

### Scenario: a directory that cannot be listed

- **GIVEN** `contracts/archive`, a directory the process may not list
- **WHEN** it is validated as `contracts` and `contracts/archive`; as `contracts/archive` and
  `contracts`; and as `contracts/archive` twice
- **THEN** each report holds one `contract.unreadable` with `cannot list directory:
  PermissionError`, named `$1/archive`, `$1` and `$1`

### Scenario: a path refused a look

- **GIVEN** a file a look at which is refused with `PermissionError`
- **WHEN** it is validated twice
- **THEN** the report holds one `contract.unreadable`, `$1`, `cannot look at path:
  PermissionError`

### Scenario: a run given a path twice

- **GIVEN** the gate run of the tests of `decision verify` and a path that is not there
- **AND** one `created_at` (`CREATED_AT` of `tests/test_decision_verify.py`), the same other
  inputs and observations in both runs
- **WHEN** it runs given the contracts and the path, and given the contracts and the path twice
- **THEN** both seal the one issue `contract.unreadable` of `$2`, and are one run id and one
  evidence digest

### Scenario: the working directory cannot be told

- **GIVEN** `contracts/notes/x.yaml` of an unknown kind, and a working directory that cannot be
  told: `os.getcwd` raises `FileNotFoundError`
- **WHEN** `contracts` is validated twice, and `contracts/gone.yaml`, which is not there, twice
- **THEN** the reports hold the one issue of `$1/notes/x.yaml`, and the one issue of `$1`, `path
  does not exist`
- **AND** on a POSIX system that no longer tells a working directory removed under the process,
  `gone.yaml` given twice is one issue of `$1`

## Versions

Verifier 121. An issue that names a path — every `contract.unreadable`,
`contract.unknown_kind`, `contract.invalid` and `contract.duplicate_id` — and the file of a
decision product its schema refused name it otherwise: `$1/notes/x.yaml` where verifiers 118 to 120 wrote
`contracts/notes/x.yaml`, a relative POSIX path no exception; and a path that could not be gone
through, reached again, is no second issue. Their reports, and with them the run ids and the
sealed `contracts.validate` artifacts, are other ones. A report that names no path — of clean
contracts, or of contracts whose issues are all the rules' of a product, whose `source` is its ref
— is the report it was, byte for byte, and only the version moves its run id (compared at
verifiers 120 and 121 by a script, not by the suite). Check 80 is unchanged:
`contracts.validate` grades what it graded, and a path that could not be gone through blocks it as
it did, once instead of again. Native source 3 is unchanged: a snapshot is read from a semantic
ref, never through the validated paths. Historical bundles SHALL NOT be rewritten: a bundle
sealed by verifier 120 names its paths as they were given, and verifies and reads as it did.

### Scenario: a bundle sealed by verifier 120

- **GIVEN** a bundle sealed at verifier 120 by the code at `70eeab5`, its issue naming
  `contracts/notes/x.yaml`
- **WHEN** it is verified with this capability's code, and `decision evidence --expect-digest`
  reads it
- **THEN** no bundle problem is found, the command exits 0, and no file of the bundle changes; the
  same inputs run again are another run id, the issue named `$1/notes/x.yaml` (checked by a
  script, not by the suite)

### Scenario: the versions

- **WHEN** the versions are read
- **THEN** `VERIFIER_VERSION` is at least 121, `CHECK_VERSION` at least 80 and
  `NATIVE_SOURCE_VERSION` is `"3"`

## Known boundaries

- A name holds the place of a validated path, not its text, and neither the report nor the bundle
  keeps the text: a reader of a sealed report learns that `$2/notes/x.yaml` is `notes/x.yaml`
  below the second path given, and which path that was only from the command that ran. The
  bundle never recorded the validated paths ([run identity](run-identity.md)).
- A name holds the place of a path among the paths given: the same files given in another order,
  with a path given again before another, or with one path more before them, can change the names
  or order of issues, and then the report and run id. Reordering the same paths leaves a report
  with no path-naming issues unchanged: product and metric refs are sorted, and product rules
  follow product id order. A file two validated paths reach is named under the first of
  them: `contracts` and `contracts/notes` name it `$1/notes/x.yaml`, the two in the other order
  `$1/x.yaml`.
- The `subject` of a `contract.invalid` stays the id the file declares or, where it declares none,
  the name of the file: as listed for a file found under a directory, as given for a file given
  itself. On a filesystem that folds case, `extra.yaml` given itself as `EXTRA.YAML` is the subject
  `EXTRA.YAML`, and `extra.yaml` when its directory is validated: two reports.
- Two spellings of a path the filesystem gives no identity — a path that is not there, one refused
  a look, a named stream — are one only where their absolute texts are: `..` and case are kept in
  that text, so such a path spelled through `sub/..`, or in another case where the filesystem folds
  case, is reached again ([contract files once](contract-files-once.md)).
- The names below a validated path are those its directories list: a file moved to another
  directory, or a directory renamed, is another name, as before.
- Compared by a script run on both systems, not by the suite, on Windows 11 / Python 3.13.7 and
  macOS / Python 3.13.13: the run's contracts from `tests/test_decision_verify.py`, the metric
  defined in `m/B.yaml` and `m/a.yaml`, and unknown kinds in `notes/B.yaml`, `notes/a.yaml`,
  `notes/sub/x.yaml` and `notes/sub-x.yaml`; every file with LF, one fixed `created_at` and the
  same other inputs and observations. At `70eeab5`, relative contracts gave
  `run-e2d87cfb7a13de7c` on both systems; absolute contracts gave `run-a79297a40c69c8b1` on
  Windows and `run-a904f849aa986c60` on macOS, the issues named by those paths. At verifier 121
  all four runs are `run-d2f5a2a0e526b0e6`: `contract.duplicate_id` of `$1/m/a.yaml`, then
  `contract.unknown_kind` of `$1/notes/B.yaml`, `$1/notes/a.yaml`, `$1/notes/sub/x.yaml` and
  `$1/notes/sub-x.yaml`, then the product's `metric_ref.candidate_content_mismatch`.
  This compares this set of files;
  different file bytes, names returned by the filesystem or refusal classes can still give
  different reports or run ids ([validation report paths](validation-report-paths.md)).
