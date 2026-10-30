# Capability: A contract file is read once, however many validated paths reach it

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

`decision validate` and `decision verify` take one or more validated paths, each a contract file
or a directory whose contract files are read, its sub-directories included
(`auto_bi/decision/validate.py`). A contract id that two files define is `contract.duplicate_id`,
block: "the same contract id is defined in more than one file" (DECISIONOPS_ARCHITECTURE §7.7).
Until now the files of every path were read as they were found, and a file two of the paths
reached was read twice. Reproduced with `decision validate` at HEAD `c14994d`, 2026-10-26, on the
contracts of the pilot package `docs/pilot/2026-08-25-ch-control` — `pass` alone — each of these
gave `contract.duplicate_id` and the verdict `block`:

- the directory and a file in it (`contracts contracts/gross_margin_pct.yaml`);
- the directory given twice: an issue for each of its four contracts;
- one file spelled two ways: `contracts/revenue.yaml` and `contracts/./revenue.yaml`, relative
  and absolute, or, on Windows, in capitals.

`decision verify` over such paths blocked the same way, on `contracts.validate`. Nothing in the
contracts could be changed to clear the finding: each id was defined by one file.

## Requirement: each file of the validated paths is read once

A file under the validated paths SHALL be read once, under the first of the paths that reached
it, and the files SHALL keep the order in which they were found: the paths in the order given,
the files of a directory in the order they were listed before (since verifier 118 the order of
their paths, one on every system: [validation report paths](validation-report-paths.md)). Two
paths reach one file when the
filesystem gives them one identity — the device and the number it reports for the file a path
leads to, links followed (`filesystem.file_identity`): a directory's path joined with a name it
lists and the file's own path; one directory given twice, or a directory and one below it; one
file spelled with `.` or `..`, relative and absolute, or in another case where the filesystem
folds case; a symbolic link to a file, a directory reached through a link, a hard link. A file is
not told by the text of its path: text that differs can name one file, and text made alike by
folding `..` can name two.

Two paths of one absolute text — the path as `pathlib` writes it, `.` and doubled separators
dropped, with the working directory or the drive's put before it where it is relative, `..` and
case kept (since verifier 121; before it, the text as given:
[validated path names](validated-path-names.md)) — SHALL be one file as well, whether a look gives
either an identity or not; and a path that is one file with another by identity is one file with
every path of its absolute text. A path without an identity — not there, refused a look, on a
filesystem that reports the file number `0`, or a named stream of a file on Windows
(`name:stream`), which the filesystem numbers as its file — SHALL be one file with a path of its
own absolute text only: text in another case, or through `..`, is another path even where the
filesystem folds case or the two lead to one file, so such a file can be read twice, never two
files read once. Two files of equal bytes SHALL stay two files: an id they both define is
`contract.duplicate_id`, with the name of the one read second as its `source`, as before.
This is the file reading order; issues of paths that could not be gone through precede file
issues, and product rules follow them ([validated path names](validated-path-names.md)).

### Scenario: a directory and a file in it

- **GIVEN** a contracts directory with the product `decision://sales/weekly_margin@1` and the
  metric contract `metric://sales/gross_margin_pct@3` it pins
- **WHEN** `decision validate` is given the directory and the metric contract's file in it, in
  either order
- **THEN** the report holds no issue, its verdict is `pass`, and it names the product and the
  metric contract once each

### Scenario: one directory twice, or a directory and one below it

- **GIVEN** the directory of the scenario above
- **WHEN** it is validated given twice; or with the metric contract moved into its sub-directory
  `metrics`, given as `metrics` and then the directory itself
- **THEN** the report holds no issue and its verdict is `pass`

### Scenario: one file spelled two ways

- **GIVEN** the directory of the first scenario with an empty sub-directory `sub`
- **WHEN** it is validated with, after it, the metric contract's file spelled as
  `<directory>/./gross_margin_pct.yaml`, as `<directory>/sub/../gross_margin_pct.yaml`, or
  relative to the working directory; or, where the filesystem folds case, as
  `<directory>/GROSS_MARGIN_PCT.YAML`
- **THEN** the report holds no issue and its verdict is `pass`

### Scenario: a link is the file it leads to

- **GIVEN** the directory of the first scenario
- **WHEN** it is validated with, in it, `linked.yaml`, a hard link of the metric contract's file
  or a symbolic link to it; or given with a symbolic link to the directory, through which the
  metric contract's file is given
- **THEN** the report holds no issue and its verdict is `pass`

### Scenario: two files of equal bytes are two files

- **GIVEN** the directory of the first scenario and a directory `copy` holding a byte-for-byte
  copy of the metric contract's file
- **WHEN** both directories are validated
- **THEN** the report holds one issue, `contract.duplicate_id` with the copy's name,
  `$2/gross_margin_pct.yaml`, as its `source`, and its verdict is `block`

### Scenario: one file number on two devices

- **GIVEN** two files declaring the same metric contract id, `filesystem.file_identity` giving
  `(1, 7)` for the first and `(2, 7)` for the second
- **WHEN** `validate_paths` is given the two files themselves, in that order
- **THEN** both are read, and the report holds one `contract.duplicate_id` with `source` `$2`
- **AND** `test_files_with_one_number_on_two_devices_are_both_read` in
  `tests/test_contract_files_once.py` pins the device in the key used to take files once

### Scenario: a parent through a link is the parent the link leads to

- **GIVEN**, on a system whose kernel resolves `..` after a link, the directory of the first
  scenario holding `link`, a symbolic link to `elsewhere/sub`, and in `elsewhere` a copy of the
  metric contract's file
- **WHEN** the directory's two files are validated with `<directory>/link/../gross_margin_pct.yaml`
- **THEN** the path names the copy, not the file whose path its text folds to, and the report
  holds `contract.duplicate_id` with that path's name, `$3`, as its `source`

### Scenario: the file keeps the first of its paths

- **GIVEN** the directory of the first scenario with an empty sub-directory `sub` and a metric
  contract whose `status` its schema refuses
- **WHEN** it is validated given as `<directory>/sub/../gross_margin_pct.yaml` and then the
  directory; and given as the directory and then that spelling
- **THEN** each report holds one `contract.invalid`, then the product's `metric_ref.unknown`; the
  `source` of `contract.invalid` is `$1`, the spelling given first, in the first run, and
  `$1/gross_margin_pct.yaml`, the name the directory's listing gave, in the second
- **AND** a product file whose schema refuses it, given as the directory and as the file, is one
  `contract.invalid` and one decision product the schema refused

### Scenario: the files keep the order they were found in

- **GIVEN** the directory of the first scenario with two files of an unknown `kind`, `a_first.yaml`
  and `b_second.yaml`, whose identities stand in the other order
- **WHEN** the directory is validated with, after it, `b_second.yaml` and `a_first.yaml`
- **THEN** the report holds `contract.unknown_kind` for `a_first.yaml` and then for
  `b_second.yaml`

### Scenario: a path without an identity

- **GIVEN** the directory of the first scenario with an empty sub-directory `sub`, the filesystem
  giving no path an identity
- **WHEN** it is validated alone; with the metric contract's file joined to its path; with the
  file spelled relative to the working directory; and with it spelled
  `<directory>/sub/../gross_margin_pct.yaml`
- **THEN** the first three reports hold no issue, the relative spelling having the absolute text
  of the file's path; the fourth holds `contract.duplicate_id` of `$2`, `..` kept in the text
- **AND** given with, after it, its metric contract's file spelled in capitals, the report holds
  `contract.duplicate_id` of `$2` — where the filesystem folds case, one file read twice; where it
  does not, a copy in that name
- **AND** `filesystem.file_identity` is the device and the file number `os.stat` reports, of two
  devices with one file number two identities, and `None` for a file number `0`, for a path that
  is not there, one refused a look and one that holds a NUL; on a system other than Windows a
  name that holds a colon has its identity

### Scenario: one path with and without an identity

- **GIVEN** the directory of the first scenario
- **WHEN** it is validated given its metric contract's file and then the directory, one look at
  that path giving an identity and the other none, in either order; or given the directory and
  then `<directory>/sub/../gross_margin_pct.yaml` twice, the first look at that spelling giving the
  file's identity and the second none; or given its metric contract's file, the directory and then
  that spelling, the first look at the file giving no identity and the second the file's
- **THEN** the report holds no issue and its verdict is `pass`

### Scenario: a named stream is not its file

- **GIVEN**, on Windows on NTFS, the metric contract's file of the first scenario and its named
  stream `gross_margin_pct.yaml:other.yaml` holding the same bytes, the two of one file number
- **WHEN** the file and the stream are validated
- **THEN** the stream has no identity, it is read, and the report holds `contract.duplicate_id`
  with the stream's name, `$2`, as its `source`

## Requirement: the run of such paths is the run of the paths without the repeat

`decision verify` given a directory and a file in it SHALL make the run it makes given the
directory: the validation report, the run id and the verdict are the same. The evidence digest
SHALL be the same where `created_at`, the other inputs and the observations are the same
([validated path names](validated-path-names.md)). `decision validate` SHALL print what it
prints for the directory and exit as it does.

### Scenario: a run given a directory and a file in it

- **GIVEN** the contracts, the semantic refs and the dashboard of a gate run that is
  `pass_with_advisory` given the contracts directory
- **AND** one `created_at` (`CREATED_AT` of `tests/test_decision_verify.py`), the same other
  inputs and observations in both runs
- **WHEN** it runs given the directory and the metric contract's file in it, into another
  evidence directory
- **THEN** `contracts.validate` is pass with `contracts.clean`, and the verdict, the run id and the
  evidence digest are those of the run given the directory
- **AND** the CLI's `decision verify` exits and prints as it does for the directory alone, and
  `decision validate` ends with `verdict: pass (1 product(s), 1 metric contract(s), 0 issue(s))`,
  exit `0`

## Requirement: the verifier version and the check version are bumped

`VERIFIER_VERSION` SHALL be 116: the same paths give another validation report, and with it
another run id. `CHECK_VERSION` SHALL be 79: `contracts.validate` grades each file of the paths
once, where rule 78 blocked on its second reading. The native source version stays 3. Historical
bundles SHALL NOT be rewritten.

### Scenario: the versions

- **WHEN** the versions are read
- **THEN** `VERIFIER_VERSION` is at least 116 and `CHECK_VERSION` at least 79

## Known boundaries

- The contracts path of a semantic ref — the `metrics/` a snapshot reads — is not read by
  identity: a hard link of a contract file of the directory is a second file, as the copy a
  checkout writes of it is, and the snapshot carries `gap.metric_duplicate`, block; a symbolic
  link to one is the file of its text, as git commits it, and the snapshot carries what a file
  of that text carries — for a path such as `gross_margin_pct.yaml`, `gap.contract_invalid`,
  block ([metrics links](metrics-links.md)). `decision validate` of the same directory passes.
  The run is blocked either way.
- Under verifiers 118 to 120 a path that cannot be gone through was an issue for each path that named it:
  a missing path given twice was two `contract.unreadable`, and so was a directory that cannot be
  listed, given and reached under another path. Since verifier 121 each is one issue
  ([validated path names](validated-path-names.md)).
- Windows folds `..` in the text of a path before the filesystem sees it, so there
  `<directory>\link\..\x` is `<directory>\x`; a POSIX kernel resolves it after the link. Each is
  the file the system opens, and that is the file read.
- An identity is the filesystem's numbers, taken as reported: that they stay while the paths are
  read and that no two volumes report one device is the filesystem's to keep. A cloned disk
  mounted beside its original may report its serial, and a file of each may then be read as one.
- A file given under its stream's name — `x.yaml::$DATA` beside `x.yaml` — is read twice and is
  `contract.duplicate_id`: a named stream is told by its text.
- The `source` of an issue names the file under the first validated path that reached it, by
  that path's place ([validated path names](validated-path-names.md)). The same contracts given in
  another order can name the file otherwise in a `source`, and the report — with it the run id —
  then differs by that name; under verifiers 118 to 120 another spelling of the paths did as well.
