# Capability: Files a command cannot read

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

Decision products and metric contracts are YAML files people write and keep in Git.
`decision validate` reads every contract under the validated paths; `decision verify` reads them
too, and the metric contracts of the base and of the candidate semantic ref. A contract file the
command cannot read is a finding about that file: under the validated paths one issue
`contract.unreadable`, in a semantic ref one gap `gap.contract_invalid`, both block
(`auto_bi/decision/validate.py`, `auto_bi/semantic/gateway.py`). The exit code of the command is
then its verdict. Exit code `1` is the interpreter's, for an exception nothing handled, and is
deliberately no verdict (DECISIONOPS_ARCHITECTURE §12.1): a command that ends that way has
said nothing about the release.

PyYAML answers a text it cannot scan, parse or compose with a `yaml.YAMLError`, and both loaders
caught that class. Building the values of a composed document is plain Python, and what that
raises left `yaml.safe_load` as it was. Reproduced at HEAD `71c486e`, 2026-10-04, PyYAML 6.0.3
on Python 3.13.13, with the fixture's metric contract and one more line in the file:

- `as_of: 2026-02-30`, a date that does not exist; `as_of: 2026-01-01T00:00:00+99:99`, a zone
  offset past a day; `sample_size:` followed by an integer of 5000 digits, more than the
  interpreter converts; `sample_size: !!int many`; `share: !!float most` — `ValueError`;
- `certified: !!bool perhaps` — `KeyError`;
- `as_of: !!timestamp lately` — `AttributeError`;
- `tags:` followed by a flow sequence nested 3000 deep — `RecursionError`.

With such a file under the validated paths `decision validate` and `decision verify` ended in a
traceback of that class and exit code 1, whichever contract the file was; with such a file among
the contracts of the base or of the candidate ref `decision verify` ended the same way. No
report and no bundle was written. A file with a syntax error in their place was graded, as
`contract.unreadable`.

One more file ended both commands in a traceback, though it is a YAML mapping of a known kind: a
contract whose `metadata` is not a mapping — a number, a text, a list or nothing. The schema
refuses it, and naming the refused contract by the id it declares raised `AttributeError`.

A message or reason quoted in a scenario below may be wrapped across lines of this file; each is
one line of text, with a single space where it wraps here.

## Requirement: a contract file whose YAML cannot be built is an unreadable contract

Whatever is raised while the YAML document of a contract file is loaded SHALL be a failure to
read that file, as a `yaml.YAMLError` is. What PyYAML refuses itself SHALL keep the class it is
refused with. Anything else SHALL be reported as a `ConstructorError` — the class PyYAML gives a
value it cannot build where it checks, such as `!!binary` that is not base64 — which keeps what
was raised as its cause and whose text is `<class> while the document was built`: the class that
was raised and nothing of its message, which may quote the file. Running out of memory, an
interrupt and an exit say nothing about the file and SHALL NOT be converted.

Under the validated paths such a file SHALL give one issue: `contract.unreadable`, block, with
the path of the file as its subject and its source and the message `cannot read contract:
ConstructorError`. Among the contracts of a semantic ref it SHALL give one gap:
`gap.contract_invalid`, block, with the path `contracts.<file name>`, the reason `metric contract
cannot be read: ConstructorError` and the digest of the file's bytes as `source_value_ref`. The
file stays a part of what the snapshot was read from and is no part of the graph. Every other
file SHALL be loaded and every product validated as before, and a file PyYAML builds SHALL be
read exactly as before — but for the file the seventh requirement refuses.

A contract that is a YAML mapping of a known kind and fails its schema SHALL be named, in its
`contract.invalid` issue, by the `id` its `metadata` declares when `metadata` is a mapping that
has one, and by the name of its file otherwise — also when `metadata` is not a mapping.

The gate grades these findings as it grades any other `contract.unreadable`, `contract.invalid`
and `gap.contract_invalid`: `contracts.validate` or `semantic.gaps` is block and so is the
verdict; the bundle is written; `decision validate` and `decision verify` exit 4. No input that
gave a report or a bundle before gives another one now, so the verifier version and the check
version SHALL NOT be bumped.

In the scenarios below *a line that cannot be built* is any of the eight lines listed above,
appended to the fixture's metric contract `metric://sales/gross_margin_pct@3`; the product
`decision://sales/weekly_margin@1` pins that metric.

### Scenario: a document that cannot be built is a YAML error

- **GIVEN** a line that cannot be built
- **WHEN** it is loaded as a YAML document
- **THEN** `yaml.safe_load` raises the class listed for it, which is no `yaml.YAMLError`; the
  loader raises `ConstructorError`, whose cause is of that class and whose text is `<class>
  while the document was built`

### Scenario: what PyYAML refuses itself is raised unchanged

- **GIVEN** a sequence that is not closed, a tab that indents, two documents in one file, an
  alias nothing anchors, a tag safe loading does not build, a mapping key that cannot be one, or
  `!!binary` that is not base64
- **WHEN** the text is loaded as a YAML document
- **THEN** the loader raises what `yaml.safe_load` raises — the same class (`ParserError`,
  `ScannerError`, `ComposerError`, `ComposerError`, `ConstructorError`, `ConstructorError`,
  `ConstructorError`) with the same text — and the error has no cause

### Scenario: a document PyYAML builds is returned as it builds it

- **GIVEN** an empty text, a comment alone, a mapping, a sequence of an integer, a text, a
  float, a boolean and a null, a date and a timestamp that exist, an integer of 4300 digits, a
  sexagesimal integer, a sequence nested 40 deep, `!!bool yes` or `!!float 1`
- **WHEN** the text is loaded as a YAML document
- **THEN** the loader returns what `yaml.safe_load` returns, of the same types

### Scenario: what says nothing about the document is not converted

- **GIVEN** a load that raises `MemoryError`, `KeyboardInterrupt` or `SystemExit`
- **WHEN** a YAML document is loaded
- **THEN** that same exception leaves the loader

### Scenario: a validated file that cannot be built is an unreadable contract

- **GIVEN** the product and its metric contract, and beside them a file `revenue.yaml` holding
  the metric contract and a line that cannot be built
- **WHEN** the paths are validated
- **THEN** the report has one issue: `contract.unreadable`, block, subject and source the path
  of `revenue.yaml`, message `cannot read contract: ConstructorError`; it lists the product and
  the metric `metric://sales/gross_margin_pct@3`; its verdict is `block`

### Scenario: a pinned metric whose contract cannot be built is unknown to its product

- **GIVEN** the product, and a date that does not exist in the file of its pinned metric
- **WHEN** the paths are validated
- **THEN** the report has two issues: `contract.unreadable` for the file, then
  `metric_ref.unknown` for the pin with the product as its source; it lists no metric

### Scenario: `decision validate` names the file and exits by the verdict

- **GIVEN** the paths of the scenario before the last
- **WHEN** `decision validate` runs over them, and again with `--json`
- **THEN** both exit 4; the first prints the issue in two lines — the severity, the code and the
  path, then the message and the path in brackets — and ends with `verdict: block (1 product(s),
  1 metric contract(s), 1 issue(s))`; the JSON's `issues` is that one issue

### Scenario: a contract whose `metadata` is not a mapping is invalid under its file name

- **GIVEN** the product and its metric contract, and beside them a file `extra.yaml` holding a
  metric contract or a product contract whose `metadata` is `5`, `2.5`, `true`, `sales`, a list
  or null
- **WHEN** the paths are validated
- **THEN** the report has one issue: `contract.invalid`, block, subject `extra.yaml`, source the
  path of the file, and a message that begins with `metadata: `; it lists the product and its
  metric; `decision validate` exits 4

### Scenario: an invalid contract is named by the id it declares or by its file

- **GIVEN** beside the product and its metric contract a file `extra.yaml` holding a metric
  contract whose `metadata` is the mapping `id: metric://sales/revenue` alone, a mapping without
  an `id`, an empty mapping, or is absent
- **WHEN** the paths are validated
- **THEN** the one issue is `contract.invalid` with the path of the file as its source; its
  subject is `metric://sales/revenue` in the first case and `extra.yaml` in the other three

### Scenario: a snapshot names a contract that cannot be built as a gap

- **GIVEN** a semantic ref holding the metric contract, and beside it a file `revenue.yaml`
  holding the metric contract and a line that cannot be built
- **WHEN** the ref is frozen into a snapshot
- **THEN** the snapshot has one gap: `gap.contract_invalid`, block, path
  `contracts.revenue.yaml`, reason `metric contract cannot be read: ConstructorError`,
  `source_value_ref` the digest of the file's bytes; its graph holds the one metric and has the
  canonical digest of the ref without the file, and its source digest is another

### Scenario: the gate blocks on a validated contract that cannot be built

- **GIVEN** a date that does not exist in the validated contract of the pinned metric
- **WHEN** the gate runs
- **THEN** the verdict is `block`; `contracts.validate` is block with `contracts.issues`, and
  the `issues` of its payload are `contract.unreadable` for the file, then `metric_ref.unknown`

### Scenario: the gate blocks on a snapshot contract that cannot be built

- **GIVEN** a date that does not exist in the metric contract of the base ref, or of the
  candidate ref
- **WHEN** the gate runs
- **THEN** the verdict is `block`; `semantic.gaps` is block with `semantic.gaps_present`; its
  payload holds the gap under that ref and nothing under the other

### Scenario: `decision verify` exits by the verdict and writes the bundle

- **GIVEN** a line that cannot be built in the metric contract under the validated paths, in the
  base ref's or in the candidate ref's
- **WHEN** `decision verify` runs
- **THEN** it exits 4 and the evidence directory holds one run with a manifest

## Requirement: a run input a command cannot read ends the run before it starts

`decision verify` is handed more than contracts: the `model.yaml` of the base and of the
candidate semantic ref, the delivered dashboard specs, the verified assets and the recorded
results. `decision capture` reads the verified assets and the candidate's `model.yaml`. A
contract that cannot be read leaves the rest of the run to grade. Without one of these files
there is no snapshot to diff, no spec to compile, no asset to select or no result to reconcile,
so such a file is no finding: the command says which file and why, and exits 2. That was already
the answer for a dashboard spec or a results file with a syntax error.

Reproduced at HEAD `43869e8`, 2026-10-04, PyYAML 6.0.3 on Python 3.13.13, with the command as a
process, a run that passes when nothing is damaged, and one damage to one file at a time: each
of the eight lines above appended; a sequence that is never closed appended; bytes that are not
UTF-8 appended; the file replaced by a list, by a text, by nothing, by a mapping its schema
refuses; the file made unreadable to its owner. Of the 75 runs of `decision verify` 44 ended in
a traceback and exit code 1:

- the base's `model.yaml` — 14 of 15: everything but the empty file, which is a model of nothing
  and is graded;
- the candidate's `model.yaml` — 14 of 15: the empty file is refused as `must contain a mapping`;
- the dashboard spec — 3: the lines that raise `KeyError`, `AttributeError` and
  `RecursionError`. The five that raise `ValueError` were refused as `… is not a valid
  DashboardSpec: day is out of range for month` — the interpreter's refusal of the YAML, reported
  as the schema's;
- the verified asset — 5: the same three lines, the sequence that is never closed (a
  `yaml.YAMLError` is no `ValueError`) and the file that may not be opened. Every other damage
  was refused under the path the assets were loaded from, not under the file;
- the recorded results — 8: every line that cannot be built.

Of the 30 runs of `decision capture` over the asset and the candidate's `model.yaml` 6 ended the
same way: the lines that raise `KeyError`, `AttributeError` and `RecursionError`, in either file.

A run input SHALL be read through the loader of the requirement above. When a run input cannot
be opened, is not UTF-8 text, is a text PyYAML refuses or a document that cannot be built,
`decision verify` and `decision capture` SHALL end before anything is graded, asked of an engine
or written, with the usage error `cannot read <path of the file>: <reason>`: exit code 2, that
one line after `error: ` on the standard error stream and nothing on standard output.
`decision verify` creates no evidence directory; `decision capture` opens no query and writes no
output file.

The reason SHALL be one line: the class of what was raised;

- followed by ` at line <n>, column <m>`, both counted from 1, when PyYAML refuses the text at a
  position;
- followed by ` (<class>: <message>)` — the class and the message of what was raised while the
  document was built — when the document could not be built. Every run of whitespace in the
  message is one space, and the brackets hold the class alone when the message is empty;
- and nothing more in every other case.

The line is printed and never kept, so it MAY quote the file — which a finding, sealed in a
report or a bundle, does not: the position is all PyYAML gives to look by, and the message is the
only pointer to a value that could not be built.

A metric contract of a semantic ref that the filesystem refuses to open SHALL end the run the
same way, under its own path. A contract that is opened and cannot be read stays a gap that
carries the digest of the file's bytes; a file that was never opened has no bytes to address,
and a refusal of the filesystem says something of the machine the run is on, not of the ref. A
refusal of the filesystem that names no file SHALL be said of the `model.yaml` of the ref.

The same SHALL hold for a path the command may not look at — the `model.yaml` of a ref or its
metrics directory, or a path under the assets path while its files are listed — under a
directory that may not be searched: `cannot read <that path>: PermissionError`. At HEAD
`decision verify` ended in a traceback there, and `decision capture` answered `cannot load
candidate semantic ref <ref>: [Errno 13] Permission denied: '<path>'` for the ref and `<assets
path> holds an invalid verified asset: [Errno 13] Permission denied: '<path>'` for the assets.

A semantic ref whose files are read and accepted by their schemas, and that holds a value its
graph cannot be built with or addressed by, SHALL be a usage error said of the ref: `cannot load
semantic ref <ref>: ` and what was raised. Which of the files of the ref held the value, the
refusal does not know, so no file is named — and the `model.yaml` SHALL be named as invalid only
for what the schema of the model itself refuses. Reproduced at HEAD with a lone surrogate, which
a YAML file holds as the escape `"\ud800"` and PyYAML builds: in the description of a table of
the model and in the description of a metric contract of the ref `decision verify` raised
`CanonicalizationError` (the digest of the graph has no form for it), in a dimension or a grain
key of a metric contract — the `ValidationError` of the schema of a gap, which cannot quote it;
neither is caught by the command. `decision capture` answered `cannot load candidate semantic ref
<ref>: ` and the same refusal.

Since the seventh requirement no YAML file hands that string in: a file that spells it is not
read. The rule stands for a value that reaches the graph another way, and its two scenarios
below hand the string on past the loader.

A run input that is read and is not what the file must hold SHALL be a usage error that names
the file:

- a `model.yaml` whose document is not a mapping, or is a mapping the schema refuses — of either
  ref, for both commands — `<file> is not a valid semantic model: ` and what the schema refuses,
  in the one line of the sixth requirement;
- a file under the assets path that holds no verified asset — a list, a text, nothing or a
  mapping the schema refuses — `<file> is not a valid verified asset: ` and what the schema
  refuses, in that one line, for both commands. Two assets of one id are the fault of no one
  file and stay `<assets path> holds an invalid verified asset: duplicate verified asset id:
  <id>`;
- a dashboard spec or a results file that holds no mapping, `<file> must contain a mapping`,
  as before. Since T-64, an empty `model.yaml` is refused by the model's schema in both refs
  and both commands; an explicit `{}` is still a valid empty model.

`VerifiedAssetRepository.load` SHALL raise `VerifiedAssetFileError` for a file it cannot read or
that holds no asset: a `ValueError`, which an asset the schema refuses has always been to whoever
loads a repository, with the file in its text and what was raised as its cause. The schema's
`ValidationError` is that cause and is no longer the error raised: a caller that caught it by
its own class has to catch the repository's.

A run that read every input reads it exactly as before, and no input that gave a bundle or a
captured result before gives another one: the verifier version and the check version SHALL NOT
be bumped. Four refusals that were usage errors already read differently:

- a dashboard spec or a results file PyYAML refuses gains the position: `cannot read <file>:
  ParserError` is now `cannot read <file>: ParserError at line <n>, column <m>`;
- a dashboard spec whose document raises `ValueError` is refused as a file that cannot be read,
  `cannot read <file>: ConstructorError (ValueError: day is out of range for month)`, and no
  longer as an invalid `DashboardSpec`;
- a file that holds no verified asset is named itself, where the path the assets were loaded
  from was named;
- `decision capture` refuses the candidate ref in the words of `decision verify` — `cannot read
  <file>: <reason>`, `<file> is not a valid semantic model: …` or `cannot load semantic ref
  <ref>: …` — where it said `cannot load candidate semantic ref <ref>: ` and whatever was raised.

In the scenarios below *the run* is one that passes and reads all five files: the fixture's
product, its dashboard spec `weekly_margin@7.yaml`, one verified asset
`weekly_margin_by_region.yaml` and results recorded for it. *A run file* is one of the base's
`model.yaml`, the candidate's `model.yaml`, the dashboard spec, the verified asset and the
recorded results. *The six texts PyYAML refuses* and the reason of each:

| Text | Reason |
|---|---|
| `tables: [a, b`, a sequence that is never closed | `ParserError at line 2, column 1` |
| `a: 1`, `b:`, then a line indented by a tab | `ScannerError at line 3, column 1` |
| `a: 1`, `---`, `b: 2`: two documents | `ComposerError at line 2, column 1` |
| `a: 1`, `b: *nobody`: an alias nothing anchors | `ComposerError at line 2, column 4` |
| `a: 1`, `b: !!python/object:os.system x` | `ConstructorError at line 2, column 4` |
| `a: 1`, `? [x, y]`, `: 2`: a key that cannot be one | `ConstructorError at line 2, column 3` |

### Scenario: the reason gives the position PyYAML refuses a text at

- **GIVEN** one of the six texts PyYAML refuses
- **WHEN** it is loaded as a YAML document and the reason of what is raised is taken
- **THEN** the reason is the one the table gives for it

### Scenario: the reason says what was raised while a document was built

- **GIVEN** a line that cannot be built
- **WHEN** it is loaded as a YAML document and the reason of what is raised is taken
- **THEN** the reason is one line, `ConstructorError (<class>: <message>)`, with the class and
  the message `yaml.safe_load` raises for that line — `ConstructorError (ValueError: day is out
  of range for month)` for the date that does not exist. For the document nested past the stack
  the class is `RecursionError` and the message is whatever the interpreter says where the stack
  ran out
- **AND** a message of several lines is given as one, `ValueError: two lines of it`; a
  `ValueError` that says nothing gives `ConstructorError (ValueError)`; a `KeyError` of
  `perhaps` gives `ConstructorError (KeyError: 'perhaps')`

### Scenario: the reason is the class alone where nothing tells more

- **GIVEN** a text with a control character, which PyYAML's reader refuses without a position; a
  `yaml.YAMLError` with neither a position nor a cause; a `PermissionError`; a
  `UnicodeDecodeError`; an `OSError` raised from another exception
- **WHEN** the reason is taken
- **THEN** it is `ReaderError`, `YAMLError`, `PermissionError`, `UnicodeDecodeError` and
  `OSError`: what is not a refusal of the document says nothing more, whatever led to it

### Scenario: a run file that cannot be built is a usage error that names it

- **GIVEN** the run, and a line that cannot be built appended to a run file
- **WHEN** `decision verify` runs
- **THEN** it is refused with the one line `cannot read <that file>: ConstructorError (<class>:
  <message>)` and no evidence directory exists

### Scenario: a run file PyYAML refuses is named with the position

- **GIVEN** the run, and a run file that holds one of the six texts PyYAML refuses
- **WHEN** `decision verify` runs
- **THEN** it is refused with `cannot read <that file>: ` and the reason of that text, and no
  evidence directory exists

### Scenario: a run file that is not text or cannot be opened is named by the class

- **GIVEN** the run, and a run file with a Latin-1 byte appended, with a quoted control character
  appended, or one the filesystem refuses to open
- **WHEN** `decision verify` runs
- **THEN** it is refused with `cannot read <that file>: UnicodeDecodeError`, `cannot read <that
  file>: ReaderError` and `cannot read <that file>: PermissionError`, and no evidence directory
  exists

### Scenario: a metric contract of a ref that cannot be opened is named as that file

- **GIVEN** the run, and the metric contract of the base ref, or of the candidate ref, which the
  filesystem refuses to open
- **WHEN** `decision verify` runs
- **THEN** it is refused with `cannot read <that contract>: PermissionError` and no evidence
  directory exists
- **AND** when freezing a ref raises an `OSError` that names no file, the run is refused with
  `cannot read <the base's model.yaml>: OSError`

### Scenario: a path of a ref that may not be looked at is named as that path

- **GIVEN** the run, and the `model.yaml` or the metrics directory of the base ref, or of the
  candidate ref, which the filesystem refuses to look at
- **WHEN** `decision verify` runs, and — for the candidate ref — `decision capture`
- **THEN** each is refused with `cannot read <that path>: PermissionError`; no evidence directory
  exists, no query is opened and no output is written

### Scenario: a model that is read and is no model is named as that

- **GIVEN** the run, and the `model.yaml` of the base ref or of the candidate ref holding a list,
  a text, or the mapping `unknown_key: 1`
- **WHEN** `decision verify` runs
- **THEN** it is refused with one line that begins `<that file> is not a valid semantic model:
  ` and goes on with what the schema refuses; no evidence directory exists

### Scenario: a value the graph of a ref has no digest for is said of the ref

- **GIVEN** the run, and a lone surrogate — the string PyYAML builds of the escape `"\ud800"`,
  handed on past the loader, which refuses such a file by the seventh requirement — as the
  description of the first table of the `model.yaml`, or as the description of the metric
  contract, of the base ref or of the candidate ref
- **WHEN** `decision verify` runs, and — for the candidate ref — `decision capture`
- **THEN** each is refused with `cannot load semantic ref <that ref>: $.tables[0].description:
  string contains a lone surrogate` for the model, and with `cannot load semantic ref <that
  ref>: $.metrics[0].description: string contains a lone surrogate` for the contract; no evidence
  directory exists, no query is opened and no output is written

### Scenario: a refusal of another schema is said of the ref, not of its model

- **GIVEN** the run, and a lone surrogate, handed on past the loader, as the first dimension of
  the metric contract of the base ref or of the candidate ref
- **WHEN** `decision verify` runs, and — for the candidate ref — `decision capture`
- **THEN** each is refused with one line that begins `cannot load semantic ref <that ref>: not
  a valid SemanticGap: reason: ` and does not say `is not a valid semantic model`; no evidence
  directory exists

### Scenario: a model document without a mapping is refused consistently

- **GIVEN** `model.yaml` is empty, contains only whitespace or comments, or holds a null,
  boolean, number, sequence or string instead of a mapping
- **WHEN** `decision verify` reads either ref, or `decision capture` reads its candidate ref
- **THEN** it exits 2 with one line, `<that file> is not a valid semantic model: <root>: …`,
  no verdict or success output, and no evidence directory or captured result
- **AND** no environment probe or capture query is opened
- **AND** an explicit `{}` remains a valid empty model whose snapshot and gate findings are
  unchanged; a normal mapping produces the same snapshot and evidence as before T-64.

The rule belongs to `NativeModelSource.snapshot`: it validates the loaded document itself,
without substituting an empty mapping for a false value. The v1 `SemanticModel.load` path is
outside this requirement.

### Scenario: a spec or a results file that is no mapping is named as before

- **GIVEN** the run, and the dashboard spec or the recorded results holding a list, a text or
  nothing
- **WHEN** `decision verify` runs
- **THEN** it is refused with `<that file> must contain a mapping` and no evidence directory
  exists

### Scenario: a file that holds no asset is named, not the path it was found under

- **GIVEN** the run, and beside its asset a file `revenue_by_region.yaml` holding a list, a text,
  nothing, or the mapping `unknown_key: 1`
- **WHEN** `decision verify` runs, and `decision capture` over the same assets
- **THEN** each is refused with one line that begins `<that file> is not a valid verified
  asset: ` and goes on with what the schema refuses; no evidence directory exists, no query is
  opened and no output is written

### Scenario: two assets of one id are named under their repository

- **GIVEN** the run, and its asset written a second time as `again.yaml`
- **WHEN** `decision verify` runs, and `decision capture` over the same assets
- **THEN** each is refused with `<assets path> holds an invalid verified asset: duplicate
  verified asset id: <id of the asset>`

### Scenario: a file under the assets path that may not be looked at is named as that file

- **GIVEN** the run, and its verified asset, which the filesystem refuses to look at while the
  files under the assets path are listed
- **WHEN** `decision verify` runs, and `decision capture` over the same assets
- **THEN** each is refused with `cannot read <that file>: PermissionError`; no evidence directory
  exists, no query is opened and no output is written

### Scenario: the repository names the file it cannot load

- **GIVEN** beside an asset a file `revenue_by_region.yaml` holding a date that does not exist;
  or holding the mapping `unknown_key: 1`
- **WHEN** the repository is loaded from the directory
- **THEN** it raises `VerifiedAssetFileError`, which is a `ValueError`: in the first case
  `cannot read <that file>: ConstructorError (ValueError: day is out of range for month)` with a
  `yaml.YAMLError` as its cause; in the second a message that begins `<that file> is not a valid
  verified asset: ` with the schema's `ValidationError` as its cause
- **AND** an asset the filesystem refuses to open raises `VerifiedAssetFileError`, `cannot read
  <that file>: PermissionError`, with the `PermissionError` as its cause
- **AND** a file beside the asset that is not YAML by its name is not read, and the asset loaded
  from its own file is the asset loaded from its directory

### Scenario: `decision capture` names a run file it cannot read as `decision verify` does

- **GIVEN** the verified asset, or the candidate's `model.yaml`, with a line that cannot be
  built appended; holding a sequence that is never closed; with a Latin-1 byte appended; or one
  the filesystem refuses to open
- **WHEN** `decision capture` runs for the asset
- **THEN** it is refused with `cannot read <that file>: ` and `ConstructorError (<class>:
  <message>)`, `ParserError at line 2, column 1`, `UnicodeDecodeError` or `PermissionError`; no
  query is opened and no output file is written
- **AND** a candidate `model.yaml` holding `unknown_key: 1` is refused with a message that begins
  `<that file> is not a valid semantic model: `

### Scenario: the commands exit 2 with one line

- **GIVEN** the run, and a date that does not exist, `certified: !!bool perhaps` or a sequence
  that is never closed appended to a run file — for `decision capture`, to the verified asset or
  to the candidate's `model.yaml`
- **WHEN** `decision verify` runs, or `decision capture`
- **THEN** the command exits 2, prints nothing on standard output and one line on standard
  error, which begins `error: cannot read <that file>: `; `decision verify` leaves no evidence
  directory, `decision capture` opens no query and writes no output file

## Requirement: an evidence directory that cannot take the bundle is a usage error

`decision verify` writes the bundle after the gate has graded the run: every artifact, then the
manifest. Reproduced at HEAD `43869e8` with the command as a process: an `--evidence` path that
is a file, an evidence directory that may not be written to and an evidence path under a
directory that may not be written to each ended the command in a traceback and exit code 1 —
`NotADirectoryError`, `PermissionError`, `PermissionError`.

The filesystem store SHALL answer what the filesystem refuses with its own error,
`ArtifactStoreError`, which keeps the refusal as its cause: `cannot write '<path>' of run '<run
id>': <class>` for a write, `cannot read '<path>' of run '<run id>': <class>` for a read, and
`cannot look for '<path>' of run '<run id>': <class>` when whether an artifact is there could
not be established — which is not "no". A path the store refuses itself — a symlink, a path
that leaves the run — is the error it was. The eighth requirement gives what the filesystem
refuses a class of its own, by which a reader tells it from what is not there.

When the store refuses while a bundle is written, `decision verify` SHALL end with the usage
error `cannot write the evidence bundle under <evidence directory>: ` followed by the store's
message: exit code 2 and that one line after `error: ` on the standard error stream. The
manifest is written last, so what was written before the refusal has no manifest and is no
bundle: `decision evidence` finds none there, and the same run, once the directory takes it,
writes the bundle that a run without the refusal writes.

### Scenario: an evidence path that is a file is a usage error

- **GIVEN** the run, and a file at the path of its evidence directory
- **WHEN** `decision verify` runs
- **THEN** it is refused with a message that begins `cannot write the evidence bundle under <that
  path>: cannot write ` and ends ` of run '<run id>': ` and the class of the refusal; the file
  holds what it held
- **AND** the command exits 2 with that one line on standard error and nothing on standard output

### Scenario: a write the run directory refuses leaves no bundle

- **GIVEN** the run, and in its evidence directory the directory of its run id with a file where
  a directory of the bundle belongs
- **WHEN** `decision verify` runs
- **THEN** it is refused with a message that begins `cannot write the evidence bundle under <the
  evidence directory>: cannot write `, names an artifact of that directory and ends with the
  class of the refusal; the run directory has no manifest and looking for a bundle under the
  evidence directory answers `no evidence bundle`
- **AND** once the file is removed the same run writes a bundle with the run id and the evidence
  digest that the run writes into an evidence directory nothing was in the way of

### Scenario: a symlink inside the run directory is a usage error

- **GIVEN** the run, and in its evidence directory the directory of its run id with a symlink to
  a directory outside, where a directory of the bundle belongs
- **WHEN** `decision verify` runs
- **THEN** it is refused with a message that begins `cannot write the evidence bundle under <the
  evidence directory>: ` and names the symlink; nothing is written to the directory outside and
  the run directory has no manifest

### Scenario: the store answers a refusal of the filesystem with its own error

- **GIVEN** a store whose root is a file; and a store holding one artifact, where the filesystem
  refuses every look at a path under its root with `PermissionError`
- **WHEN** an artifact is put into the first; and the artifact of the second is looked for, read,
  and another one put beside it
- **THEN** the first raises `ArtifactStoreError`, `cannot write 'checks/one.json' of run
  'run-1': ` and the class of the refusal, with the refusal as its cause, and the file holds what
  it held; the second raises `cannot look for 'checks/one.json' of run 'run-1':
  PermissionError` with the refusal as its cause, `cannot read 'checks/one.json' of run
  'run-1': PermissionError`, and `cannot write 'checks/two.json' of run 'run-1':
  PermissionError`

### Scenario: a refused look into the evidence directory is a usage error

- **GIVEN** the run, where the filesystem refuses every look at a path under its evidence
  directory with `PermissionError`
- **WHEN** `decision verify` runs
- **THEN** it is refused with a message that begins `cannot write the evidence bundle under <the
  evidence directory>: cannot ` and ends `: PermissionError`; the evidence directory does not
  exist

## Requirement: what the filesystem refuses to list or to look at is not what is not there

`decision validate`, `decision verify` and `decision capture` go through directories — the
validated paths, the metrics directory of a semantic ref, the assets path — and ask of the paths
they are given whether each is there. Both questions were put to `pathlib`, and two of its
answers drop what the filesystem refuses. `Path.rglob` reads a directory it cannot list as an
empty one: measured on Python 3.12.13, 3.13.13 and 3.14.6. And from Python 3.14 on `Path.exists`,
`Path.is_file` and `Path.is_dir` answer `False` to every `OSError`, so that a path under a
directory that may not be searched reads as a path that is not there; up to 3.13 the same look
raised, and for the paths the second requirement left open nothing caught it.

Reproduced at HEAD `35c18ec`, 2026-10-04, on macOS with the commands as processes over the run
of the scenarios below: 32 runs of `decision verify`, 15 of `decision capture` and 8 of `decision
validate`, each set under Python 3.13.13 and under Python 3.14.6. A directory of mode 000 can be
neither listed nor searched; of mode 0o300 it is searched and not listed, and a file in it opens
by its name; of mode 0o400 it is listed and not searched, so its names are seen and nothing in
it may be looked at.

- The assets directory, or a directory under it that holds the asset of the product, of mode 000
  or 0o300: `decision verify` exits 0 with `pass_with_advisory` — no asset is selected and
  nothing says that one could not be. A second copy of the asset in such a directory is not seen
  either, where two assets of one id are a usage error. `decision capture` answers `no verified
  asset under <path>`.
- The metrics directory of the base ref: exit 3; of the candidate ref: exit 4 — each on a
  snapshot without the metrics. `decision capture` goes on to open its query on that snapshot.
- The validated directory: `no decision product contract found in the given paths` and exit 2
  from both commands. A directory under it that holds the metric contract: the product blocks on
  `metric_ref.unknown`, as if the contract had never been written; one that holds the product:
  the same exit 2; one that holds nothing: exit 0. Nothing names the directory.
- A path the command may not look at, under a directory that may not be searched — the assets
  path, the dashboards directory, a dashboard spec, the recorded results, a validated file, the
  output of a capture: under Python 3.13 a traceback and exit code 1, in 9 of the runs. Under
  Python 3.14 the same paths are "not found" — `verified assets path not found: <path>`,
  `dashboard specs directory not found: <path>`, `recorded results file not found: <path>` — the
  dashboard spec is the finding `bi.spec_missing` and the run exits 3, the validated files give
  `no decision product contract found in the given paths`, and `decision capture` opens its
  query for an output it has not looked at.
- Under Python 3.14 a directory that is listed and not searched reads as an empty one too, where
  3.13 answered `cannot read <file>: PermissionError` as the second requirement has it: the look
  at each file the listing names answers `False`, and the file is passed over. With the assets
  directory `decision verify` exits 0 with `pass_with_advisory`.

A look these three commands take at a path — whether it is there, is a file, is a directory —
SHALL be answered by the operating system, on every supported interpreter. The answer is "no"
for a path that is not there: what the operating system says with `ENOENT`, `ENOTDIR`, `EBADF`
or `ELOOP`, on Windows also with the codes 21, 123 and 1921, and what a name that holds a NUL
is. Any other refusal SHALL be a refusal and no answer.

Going through a directory SHALL give every path below it, as `Path.rglob("*")` gives them where
every directory can be listed: a link to a directory is given and is not followed, a path that
is no directory holds nothing, and neither does a directory that is not there or went away while
the others were listed. A directory the filesystem refuses to list SHALL be a refusal that names
that directory. So SHALL a directory that is listed while the filesystem will not say which of
its entries are directories — once, however many entries it has. The order of the paths is not
promised; each reader sorts what it reads.

Under the validated paths the report answers for every file, so what it could not go through is
a finding of its own. A directory that cannot be listed — a validated path or any directory
below one — SHALL give one issue: `contract.unreadable`, block, with the path of the directory
as its subject and its source, named as the report names the files under that path, and the
message `cannot list directory: <class of the refusal>`. A validated path that may not be looked
at SHALL give one such issue with the message `cannot look at path: <class of the refusal>`. A
path that is not there stays `path does not exist`. Every file that could be listed SHALL be
loaded and every product validated as before. The issues of the paths come first, in the order
the paths were given and, under one path, in the order of the directories' paths; the issues of
the files follow, as before.

The gate grades these issues as it grades every other `contract.unreadable`: `contracts.validate`
blocks with `contracts.issues`, `decision validate` and `decision verify` exit 4, and `decision
verify` writes the bundle. A run over such a directory gave a bundle before and gives another one
now, so the verifier version SHALL be 93 and the check version 68.

When no product is among what was read, `decision verify` SHALL say what was not read. Its usage
errors `no decision product contract found in the given paths` and `no decision product '<id>'
in the given paths (<the products read>)` SHALL go on with `; <n> path(s) could not be read: `
and the first three `contract.unreadable` issues of the report, each as `<source> (<message>)`
and separated by `, `, then ` (+<k> more)` when there are more than three. Where the report
holds no such issue both SHALL read as before. (A product file its schema refused is named
before them, the verifier version unchanged: [schema-refused products](schema-refused-products.md).
Since T-117 `<source>` is spelled as a diagnostic line spells a path, quoted when it holds a
character that is not printable: [one-line errors](one-line-errors.md).)
`decision validate` prints its report above its
own error and names them there.

In a semantic ref and under the assets path a directory that cannot be listed SHALL end
`decision verify`, and `decision capture` for what it reads, in the usage error of the second
requirement — `cannot read <that directory>: <class of the refusal>`, exit code 2, one line —
before anything is graded, asked of the engine or written. So SHALL a path of the run that may
not be looked at: the assets path or a path under it, the dashboards directory, a dashboard spec
under any of the three names it may have, the recorded results, the `model.yaml` of a ref, its
metrics directory, a contract in it. A snapshot says what bytes it was read from, and a
repository answers for every asset under its path; neither can say so of a directory nobody
listed. The output of `decision capture` that may not be looked at SHALL end the command with
`cannot write captured result <output>: ` and the text of the refusal, before the engine is
asked.

`NativeModelSource.snapshot` and `VerifiedAssetRepository.load` SHALL raise the `OSError` of a
refused listing or look, with the refused path as its `filename`.

What can be listed and looked at SHALL be read exactly as before: contracts and assets in
directories below their path; an empty directory; a file that is not YAML by its name; in a ref
and under the assets path, a directory named like a YAML file. A path that is not there keeps
its message — `verified assets path not found: <path>`, `dashboard specs directory not found:
<path>`, `recorded results file not found: <path>`, `semantic ref <ref> has no model.yaml` — and
a dashboard spec that is not there stays the finding of `bi.contract`.

In the scenarios below *a directory that cannot be listed* is one whose listing the filesystem
refuses with `PermissionError`, and *a path that may not be looked at* is one every look at
which it refuses with `PermissionError`. *The tree* is eight directories and nine files:
directories in directories, names that begin with a dot, an empty directory and a directory
named like a YAML file.

### Scenario: a look answers what the path leads to

- **GIVEN** the tree, with a link to a file and a link to a directory of it
- **WHEN** a file, a directory, the directory named like a file and each link are asked whether
  they are there, are a file, are a directory
- **THEN** each answer is that of what the path leads to, links followed, whether the path is
  given as a path or as a text
- **AND** a path nothing is at, a path below a file, a link to nothing, a link to itself and a
  name that holds a NUL are not there, no file and no directory

### Scenario: a refused look is a refusal, and "not there" is told by what was answered

- **GIVEN** a file and a directory of the tree that may not be looked at; and a file for which
  the operating system answers `EIO`
- **WHEN** each is asked whether it is there, is a file, is a directory
- **THEN** every look raises what the filesystem refused with — its `errno`, the path as its
  `filename` — and a path nothing refused is answered as it was
- **AND** a file for which the operating system answers `ENOENT`, `ENOTDIR`, `EBADF` or `ELOOP`,
  or an error that carries the Windows code 21, 123 or 1921, is not there, no file and no
  directory; with the Windows code 5, access denied, every look raises

### Scenario: the walk gives the paths `rglob("*")` gives

- **GIVEN** the tree
- **WHEN** the paths under it are walked
- **THEN** they are the seventeen paths `Path.rglob("*")` gives, whether the tree is given as a
  path or as a text, and no refusal is kept
- **AND** a link to a directory outside the tree, a link to a file and a link to nothing are
  given, as `rglob` gives them, and nothing under the linked directory is
- **AND** a path nothing is at, and a file, hold nothing

### Scenario: a directory the walk cannot list is raised, or kept while the rest is walked

- **GIVEN** the tree, where the tree itself, a directory in it or a directory two levels down
  cannot be listed
- **WHEN** the paths under the tree are walked
- **THEN** the walk raises `PermissionError` with that directory as its `filename`
- **AND** with a list to keep refusals in and two directories that cannot be listed, the list
  holds two errors, each naming its directory, and the walk gives every path that is not below
  either — the two directories themselves among them
- **AND** the tree itself, when it cannot be listed, gives no path and one kept error
- **AND** a listing the filesystem answers with `EIO` is raised, or kept, as that `OSError`

### Scenario: a directory that went away is not a refusal

- **GIVEN** the tree, where the listing of one directory is answered with `ENOENT`, `ENOTDIR`,
  `EBADF` or `ELOOP`
- **WHEN** the paths under the tree are walked, with a list to keep refusals in and without one
- **THEN** nothing is raised and nothing kept, and the paths are those that are not below that
  directory

### Scenario: a directory whose entries are not told apart is refused once

- **GIVEN** the tree, with a directory of two entries that is listed while the filesystem
  refuses, with `PermissionError`, to say of either whether it is a directory
- **WHEN** the paths under the tree are walked
- **THEN** both entries are given and nothing below them is; one `PermissionError` that names
  the directory is kept, or raised when there is no list
- **AND** when the same question is answered with "no such entry", nothing is kept
- **AND** when it is answered so for one entry and refused for the other, the directory is
  refused with the `PermissionError` — whichever of the two is asked first

### Scenario: the walk over directories whose permissions are taken away

- **GIVEN** the tree on a filesystem whose permission bits keep the process out, with one
  directory of mode 000 or 0o300
- **WHEN** the paths under the tree are walked
- **THEN** the walk gives exactly the paths `rglob("*")` gives there and keeps one
  `PermissionError` that names the directory, of which `rglob` says nothing; without a list it
  raises
- **AND** with the directory of mode 0o400 its entries are given, what is below the directory
  among them is not, and one `PermissionError` is kept
- **AND** every look at a path in a directory of mode 000 or 0o400 — a file, a directory, a name
  nothing is at — raises `PermissionError` with that path as its `filename`, while the directory
  itself is a directory

### Scenario: a validated directory that cannot be listed is an unreadable path

- **GIVEN** the fixture's contracts, and under them a directory `archive` that holds a second
  file of the metric's id — which gives `contract.duplicate_id` — and that cannot be listed
- **WHEN** the paths are validated
- **THEN** the report holds one issue: `contract.unreadable`, block, with the path of `archive`
  as its subject and its source and the message `cannot list directory: PermissionError`; the
  product and the metric contract are in the report, and its verdict is `block`
- **AND** when the validated directory itself cannot be listed, the one issue names it and the
  report holds no product and no metric contract

### Scenario: directories that cannot be listed are reported in the order of the paths

- **GIVEN** two validated paths: a directory `other` that cannot be listed; and the fixture's
  contracts with six directories that cannot be listed — `m`, `c`, `x`, `a`, `t`, `f` — and a
  file `a_broken.yaml` holding a sequence that is never closed
- **WHEN** the paths are validated in that order
- **THEN** the issues are that of `other`, those of the six directories in the order of their
  paths, and `contract.unreadable` of `a_broken.yaml` with `cannot read contract: ParserError`
- **AND** given the path `.`, a directory `archive` under it that cannot be listed is named
  `archive`, as a file under it is named
- **AND** a listing the filesystem answers with `EIO` is `cannot list directory: OSError`
- **AND** a directory that is listed while its entries are not told apart gives the one issue of
  that directory

### Scenario: a validated path that may not be looked at is an unreadable path

- **GIVEN** the validated paths given as the metric contract and the product, where the metric
  contract may not be looked at — or the directory it was moved to, given in its place
- **WHEN** the paths are validated
- **THEN** the first issue is `contract.unreadable`, block, with that path as its subject and
  its source and the message `cannot look at path: PermissionError`; the product is loaded and
  validated, its pin gives `metric_ref.unknown`, and the report holds no metric contract
- **AND** a validated path nothing is at gives `path does not exist`, as before

### Scenario: contracts in directories below a validated path are validated as before

- **GIVEN** the fixture's contracts with the metric contract moved two directories down, beside
  a directory `.notes` that holds a text file
- **WHEN** the paths are validated
- **THEN** the report is the report of the contracts side by side: no issue, the metric contract
  in it

### Scenario: `decision validate` prints the directory and exits by the verdict

- **GIVEN** the fixture's contracts, and under them a directory `archive` that cannot be listed
- **WHEN** `decision validate` runs
- **THEN** it exits 4; its first two lines are the issue `block`, `contract.unreadable` and the
  path of `archive`, and `cannot list directory: PermissionError [<that path>]`; its last line
  is `verdict: block (1 product(s), 1 metric contract(s), 1 issue(s))`; with `--json` the issues
  of the report are that one issue
- **AND** when the validated directory itself cannot be listed, the command exits 2 and prints
  the issue, `verdict: block (0 product(s), 0 metric contract(s), 1 issue(s))` and `error: no
  decision product contract found in the given paths`
- **AND** given the metric contract, which may not be looked at, and the product, the command
  exits 4 and its first two lines name the contract and `cannot look at path: PermissionError`

### Scenario: the gate blocks on a validated directory that cannot be listed

- **GIVEN** the run, which does not block, and under its contracts a directory `archive` that
  cannot be listed
- **WHEN** `decision verify` runs
- **THEN** the verdict is `block`; `contracts.validate` blocks with `contracts.issues` and its
  payload holds the one issue of `archive`; the command exits 4 and the bundle is written with
  its manifest
- **AND** the verifier version is at least 93 and the check version at least 68

### Scenario: no product among what was read names what was not read

- **GIVEN** the run, where the validated directory cannot be listed; or where the product file
  holds a sequence that is never closed; or where nothing is at the validated path
- **WHEN** `decision verify` runs
- **THEN** it is refused with `no decision product contract found in the given paths; 1 path(s)
  could not be read: ` and `<the directory> (cannot list directory: PermissionError)`, `<the
  product file> (cannot read contract: ParserError)` or `<the path> (path does not exist)`; no
  evidence directory exists
- **AND** asked for the product `decision://sales/other` with a directory `archive` under the
  contracts that cannot be listed, it is refused with `no decision product
  'decision://sales/other' in the given paths (<the product of the run>); 1 path(s) could not be
  read: <archive> (cannot list directory: PermissionError)`
- **AND** without the product file and with three of five directories under the contracts that
  cannot be listed, the message ends `; 3 path(s) could not be read: ` and the three; with all
  five it ends `; 5 path(s) could not be read: `, the first three and ` (+2 more)`
- **AND** where every path was read — a product id the paths do not hold, a product file that
  holds another kind, no product file — the message is the one it was
- **AND** the command exits 2 with nothing on standard output and that one line after `error: `
  on standard error

### Scenario: a directory of a semantic ref that cannot be listed ends the run

- **GIVEN** the run, where the metrics directory of the base ref or of the candidate ref, or a
  directory `sales` in it, cannot be listed
- **WHEN** `decision verify` runs, and — for the candidate ref — `decision capture`
- **THEN** each is refused with `cannot read <that directory>: PermissionError`; no evidence
  directory exists, no query is opened and no output is written
- **AND** with the metric contract moved into `sales`, where it may not be looked at, each is
  refused with `cannot read <that contract>: PermissionError`
- **AND** `NativeModelSource.snapshot` raises the `PermissionError` whose `filename` is the
  metrics directory that cannot be listed, or the contract or the metrics directory that may not
  be looked at; with nothing refused it gives the snapshot it gave before
- **AND** a metric contract two directories below the metrics, beside an empty directory, a text
  file and a directory named like a contract, gives the snapshot of the contract in the metrics
  directory itself

### Scenario: a directory under the assets path that cannot be listed ends the run

- **GIVEN** the run, where the assets directory, or a directory `archive` in it, cannot be listed
- **WHEN** `decision verify` runs, and `decision capture` over the same assets
- **THEN** each is refused with `cannot read <that directory>: PermissionError`; no evidence
  directory exists, no query is opened and no output is written
- **AND** `VerifiedAssetRepository.load` raises the `PermissionError` whose `filename` is the
  assets directory that cannot be listed, or the asset or the assets directory that may not be
  looked at; with nothing refused it loads the asset
- **AND** an assets directory that holds nothing is an empty one: the run is graded as the run
  without assets is — verdict, and status and summary code of every check — and `decision
  capture` for the asset answers `no verified asset '<id of the asset>' under <assets path>
  (none)`
- **AND** the asset two directories below the assets path, beside an empty directory, a text
  file and a directory named like an asset, is the one asset of the repository, with the digest
  it has side by side

### Scenario: a run input that may not be looked at ends the run

- **GIVEN** the run, where one of these may not be looked at: the assets path; the dashboards
  directory; the dashboard spec under its `.yaml` name, or — with no such file — under the
  `.yml` or the `.json` name looked at next; the recorded results
- **WHEN** `decision verify` runs, and — for the assets path — `decision capture`
- **THEN** each is refused with `cannot read <that path>: PermissionError`; no evidence directory
  exists
- **AND** `decision capture` whose output may not be looked at is refused with `cannot write
  captured result <output>: ` and the text of the refusal; no query is opened and no output is
  written
- **AND** where nothing is at the assets path, at the dashboards directory, at the results file
  or at the candidate's `model.yaml`, the run is refused with the message it was refused with:
  `verified assets path not found: <path>`, `dashboard specs directory not found: <path>`,
  `recorded results file not found: <path>`, `semantic ref <ref> has no model.yaml`
- **AND** with no dashboard spec there `bi.contract` is under review, as before

### Scenario: the run over directories whose permissions are taken away

- **GIVEN** the run on a filesystem whose permission bits keep the process out
- **WHEN** `decision verify` runs with the assets directory, a directory in it, the metrics
  directory of the base ref or of the candidate ref, or a directory in the candidate's metrics,
  of mode 000 or 0o300
- **THEN** it is refused with `cannot read <that directory>: PermissionError`, and so is
  `decision capture` with the assets directory or the candidate's metrics directory
- **AND** with the assets directory or the metrics directory of either ref of mode 0o400, it is
  refused with `cannot read <the file in that directory>: PermissionError`
- **AND** with the assets directory, the dashboards directory, the results file, the base ref or
  the candidate ref under a directory of mode 000, it is refused with `cannot read <that
  path>: PermissionError` — for a ref, with its `model.yaml` as the path
- **AND** the validated directory, or a directory in it, of mode 000 or 0o300 gives the one issue
  `cannot list directory: PermissionError`; of mode 0o400 it gives `cannot read contract:
  PermissionError` for each of its two files, and no product; and its two files, given as the
  validated paths while the directory has mode 000, give `cannot look at path: PermissionError`
  each, and no product

### Scenario: the commands exit 2 with one line

- **GIVEN** the run, where the assets directory or the metrics directory of either ref cannot
  be listed; or where the assets path, the dashboards directory or the results file may not be
  looked at
- **WHEN** `decision verify` runs
- **THEN** it exits 2, prints nothing on standard output and `error: cannot read <that path>:
  PermissionError` on standard error; no evidence directory exists
- **AND** `decision capture` with the assets directory or the candidate's metrics directory that
  cannot be listed exits 2 with the same one line and writes no output file

## Requirement: an input `decision investigate` cannot read or take is named in one line

`decision investigate` is handed a plan and one canonical result for each metric of the plan,
and writes a directory of four artifacts. It read both kinds of file by itself — `yaml.safe_load`
and then the schema — under one handler for `OSError`, `yaml.YAMLError` and the schema's
`ValidationError`, which answered `input contract is unreadable: ` and the text of whatever was
raised.

Reproduced at HEAD `01d3c8f`, 2026-10-05, PyYAML 6.0.3 and pydantic 2.13.4 on Python 3.13.13,
with the command as a process over the fixture plan and its result and one damage to one input
at a time. Of 54 runs 22 ended in a traceback and exit code 1:

- the plan — 9 of 15, the result — 9 of 15: each of the eight lines that cannot be built, and
  bytes that are not UTF-8, which raise a `UnicodeDecodeError` and no `OSError`;
- a row value written as an unquoted integer of 5000 digits: a document that cannot be built;
- a result whose `unit` holds a lone surrogate. The field is plain text, the schema took it, and
  the digest of the result has no form for it: `CanonicalizationError`;
- a result one row of which names a dimension with a lone surrogate. The names of a row are the
  keys of a mapping, which the schema took as they are; the refusal of the result then quoted
  the name, and pydantic cannot build a refusal that holds a lone surrogate: the schema raised
  `UnicodeEncodeError`;
- an output path the standard output stream cannot encode, after the artifacts were written.
  That one is not covered here: `unencodable-output.md` covers it.

18 more runs exited 2 with a refusal of 3 to 25 lines that named no file: PyYAML's account of
the place `in "<unicode string>"`, or pydantic's listing of each error with the value it refused
and a link to its documentation. With two results on the command line nothing said which was
meant. Asked in the process at every string and every key of the two fixtures, alone and between
two letters, a lone surrogate was refused by the schema at all 202 places of the plan and at 114
of the 128 of the result; at 12 the schema raised `UnicodeEncodeError` and at 2 it took the
value.

Each input SHALL be answered for by its own path. The plan SHALL be read before the results and
the results in the order they are given; the first input that is refused ends the command.

- An input that cannot be opened, is not UTF-8 text, is a text PyYAML refuses or a document
  that cannot be built SHALL be the usage error `cannot read <path>: <reason>`, with the reason
  of the second requirement.
- An input that was read and is not what its schema describes SHALL be the usage error `<path>
  is not a valid <kind>: <refusals>`, the kind being `InvestigationPlan` or
  `CanonicalMetricResult`.

Since T-74, a `UnicodeEncodeError` while loading a plan or result input SHALL use the same
unreadable-input refusal: `cannot read <path>: UnicodeEncodeError`. This includes a filename
the filesystem cannot encode. The exception's text SHALL NOT be printed. The application
SHALL retain the original error as the cause and SHALL NOT read later inputs or create the
output directory or its missing parent. The CLI prints the path through its existing
encoding-safe output function.

This handles an actual load failure, not a character blacklist for paths. Names the
filesystem can read, including POSIX surrogateescape names, remain accepted; a successful
run keeps the same artifact bytes. Other unexpected exceptions, such as `RuntimeError` or
`UnicodeTranslateError`, still propagate. Output paths and other commands are outside T-74.

The refusals SHALL be one line: `<place>: <reason>` for each of the first three errors of the
schema, joined by `; `, and after them `; (+<n> more)` when the schema has `n` more. The place
is the steps to what is refused, joined by `.`: names of fields, indices of lists counted from 0
and keys of mappings. A key that is empty text is `""`, and a place with no step is `<root>`.
The reason is the schema's. The value an error is about SHALL NOT be given, nor a link. A key of
the document is part of a place, and a validator of the schema may quote a value in its reason:
every run of whitespace in a refusal SHALL be one space.

Either way the command SHALL exit 2 with `error: ` and that one line on the standard error
stream, print nothing on standard output and create no output directory.

`InvestigationPlan.from_yaml` and `CanonicalMetricResult.from_yaml` SHALL build the document
with the loader of the first requirement: a text that cannot be built is a `yaml.YAMLError` to
whoever calls them, with what was raised as its cause.

The schema of a canonical result SHALL refuse a lone surrogate where it took one: in the `unit`,
`unit: Value error, must be text without a lone surrogate`, and in a name a row gives a
dimension, `rows.<i>: Value error, row dimension names must be text without a lone surrogate`.
Neither refusal quotes the value. Every other `unit` SHALL be taken as it is — empty, of spaces,
of several lines, in any script, outside the BMP.

Inputs that were read give the artifacts they gave: no byte of `plan.json`, `results.json`,
`brief.json` or `brief.md` changes for inputs the command accepted, and no version of a skill,
of the plan or of the evidence SHALL be bumped. What is read and cannot be used is said as
before: a result no step asks for, two results of one metric, a metric without a result, a
policy stop, an output directory that is there.

In the scenarios below *the inputs* are the fixture plan `weekly_margin.yaml` and its result
`revenue_results.yaml`, each in a file of its own, and *an input* is either of the two.

### Scenario: an input whose YAML cannot be built is named

- **GIVEN** the inputs, and one of the eight lines that cannot be built appended to an input
- **WHEN** the investigation is written
- **THEN** it is refused with `cannot read <that file>: ConstructorError (<class>: <message>)`,
  the class and the message being what the interpreter raises for the line alone — for the line
  nested past the stack, the class `RecursionError` and whatever it says there
- **AND** the output directory does not exist

### Scenario: the filesystem cannot encode an investigation input's name

- **GIVEN** a plan or result input whose load raises `UnicodeEncodeError`
- **WHEN** `decision investigate` reads that input
- **THEN** it exits 2, stdout is empty and stderr names the path and `UnicodeEncodeError`
- **AND** the exception's text is not printed, later inputs are not read and no output
  directory or missing parent is created
- **AND** strict ASCII or UTF-8 stderr escapes a surrogate in the path instead of raising

### Scenario: an input PyYAML refuses is named with the position

- **GIVEN** the inputs, and one of the six texts PyYAML refuses in place of an input
- **WHEN** the investigation is written
- **THEN** it is refused with `cannot read <that file>: ` and the reason of that text

### Scenario: an input that is not text, not there or not to be opened is named by the class

- **GIVEN** the inputs, and an input with a Latin-1 byte appended; removed; replaced by a
  directory; or one the filesystem refuses to open
- **WHEN** the investigation is written
- **THEN** it is refused with `cannot read <that file>: ` and `UnicodeDecodeError`,
  `FileNotFoundError`, what the platform refuses a directory with (`IsADirectoryError`, on
  Windows `PermissionError`) or `PermissionError`

### Scenario: the first input that is refused is the one named

- **GIVEN** the inputs and two more results, the first holding a sequence that is never closed
  and the second one byte that is not UTF-8
- **WHEN** the investigation is written
- **THEN** it is refused with `cannot read <the first of the two>: ParserError at line 2, column
  1`
- **AND** with a plan that is not UTF-8 and a result that holds the unclosed sequence, with
  `cannot read <the plan>: UnicodeDecodeError`
- **AND** with a plan that holds one more key `unknown: 1` and a result that is not UTF-8, with
  `<the plan> is not a valid InvestigationPlan: unknown: Extra inputs are not permitted`
- **AND** with two more results, the first with `complete: perhaps` and the second holding only
  `result_id: 5`, with a message that begins `<the first of the two> is not a valid
  CanonicalMetricResult: complete: `

### Scenario: the models answer a text that cannot be built with a YAML error

- **GIVEN** one of the eight lines that cannot be built
- **WHEN** `InvestigationPlan.from_yaml` or `CanonicalMetricResult.from_yaml` is given it
- **THEN** a `yaml.YAMLError` is raised whose cause is what the interpreter raised

### Scenario: an input that is no mapping is named with its kind

- **GIVEN** the inputs, and an input replaced by a list or by a text
- **WHEN** the investigation is written
- **THEN** it is refused with `<that file> is not a valid <kind>: <root>: Input should be a valid
  dictionary or instance of <kind>`

### Scenario: an input its schema refuses is named with where and why

- **GIVEN** the inputs, and the first step of the plan, a `data_health`, with `skill_version: 2`
- **WHEN** the investigation is written
- **THEN** it is refused with `<the plan> is not a valid InvestigationPlan: steps.0: Value error,
  skill_version for data_health must be 1`
- **AND** the result without its `freshness_max_age` is refused with `<the result> is not a
  valid CanonicalMetricResult: <root>: Value error, source_max_event_time and freshness_max_age
  must be supplied together`

### Scenario: three refusals are spelled out and the rest are counted

- **GIVEN** the inputs, and an input that holds nothing
- **WHEN** the investigation is written
- **THEN** the refusal names three fields as `<field>: Field required` and ends `; (+4 more)`
  for the plan, `; (+2 more)` for the result
- **AND** a plan with one or three keys the schema does not know, `key_00`, `key_01` and so on,
  names each as `<key>: Extra inputs are not permitted` and counts nothing; with four it names
  the first three and ends `; (+1 more)`, with forty `; (+37 more)`

### Scenario: a key of the document is the place of its refusal

- **GIVEN** the inputs, and one more key in the plan holding `5`
- **WHEN** the investigation is written
- **THEN** the refusal is `<the plan> is not a valid InvestigationPlan: <place>: Extra inputs
  are not permitted`, the place being the key for `unknown` and for a key in Cyrillic, `a b` for
  a key of the letter `a`, a line break, two spaces and the letter `b`, and `""` for a key that
  is empty text
- **AND** such an empty key in the third step of the plan is refused at `steps.2.""`
- **AND** a result with the line `1: 2` appended is refused with `<the result> is not a valid
  CanonicalMetricResult: 1: Keys should be strings`

### Scenario: a refusal is one line and gives no value of the document

- **GIVEN** the inputs, and a plan whose scope names twice the dimension `by`, a line break,
  two spaces, `week`
- **WHEN** the investigation is written
- **THEN** it is refused with `<the plan> is not a valid InvestigationPlan: scope: Value error,
  duplicate entries in scope.dimensions: by week`
- **AND** the reason of a plan whose `question` is a list of two texts and whose
  `budget.max_steps` is a text begins `question: Input should be a valid string;
  budget.max_steps: `, holds one `; `, none of the three texts and no link

### Scenario: a unit that holds a lone surrogate is refused by the schema

- **GIVEN** the inputs, and the result with a lone surrogate as its `unit`, handed on past the
  loader, which refuses such a file by the seventh requirement
- **WHEN** the investigation is written
- **THEN** it is refused with `<the result> is not a valid CanonicalMetricResult: unit: Value
  error, must be text without a lone surrogate`

### Scenario: a row that names a dimension with a lone surrogate is refused by the schema

- **GIVEN** the inputs, and the first row of the result naming its dimension with a lone
  surrogate, handed on past the loader
- **WHEN** the investigation is written
- **THEN** it is refused with `<the result> is not a valid CanonicalMetricResult: rows.0: Value
  error, row dimension names must be text without a lone surrogate`
- **AND** a result that declares no dimension, with one row that names such a dimension holding
  nothing, is refused by its schema at that row with the same reason — whether the name is the
  lone surrogate or holds it between two letters
- **AND** so is a row that names three dimensions, the second of them such a name: every name
  of a row is looked at, wherever it stands
- **AND** when what such a name holds is refused first — a number — the refusal is one line
  that begins `<the result> is not a valid CanonicalMetricResult: rows.0.dimensions.` and ends
  `: Input should be a valid string`, and is text any stream can take

### Scenario: no place of an input takes a lone surrogate

- **GIVEN** a fixture document, and a lone surrogate — alone, or between two letters — as one of
  its strings or as one of the keys of its mappings
- **WHEN** the schema of the document validates it
- **THEN** at every such place of the plan and of the result the schema refuses with its
  `ValidationError`, and the reason is one line of text any stream can take

### Scenario: text the artifacts have a form for is taken as it is

- **GIVEN** the result with the `unit` empty, `RUB`, in Cyrillic, a currency sign, a character
  outside the BMP, of spaces around a sign, or of two lines
- **WHEN** the schema validates it
- **THEN** the unit of the result is that text and the result has a digest
- **AND** the inputs with the dimension renamed to a name that holds a character outside the BMP
  are written as four artifacts

### Scenario: the command exits 2 with one line and writes nothing

- **GIVEN** the inputs, and an input with a date that does not exist appended, with a sequence
  that is never closed appended, with bytes that are not UTF-8 appended, removed, holding
  nothing, or with a key the schema does not know
- **WHEN** `decision investigate` runs
- **THEN** it exits 2, prints nothing on standard output and one line on standard error, which
  begins `error: cannot read <that file>: ` for the first four and `error: <that file> is not a
  valid <kind>: ` for the other two; the output directory does not exist
- **AND** the same holds for a result with a lone surrogate, handed on past the loader, as its
  `unit` or as a name of a dimension of its first row: one line that begins `error: <the
  result> is not a valid CanonicalMetricResult: ` and ends `without a lone surrogate`

### Scenario: inputs that are read give the artifacts they gave

- **GIVEN** the inputs
- **WHEN** `decision investigate` runs
- **THEN** it exits 0, prints nothing on standard error, and on standard output four lines: the
  output path after `wrote`, the content address of the plan after `plan`,
  `sha256:cfbedd5368d2edeba94f2a17c19a4eebc4079b5bd048c46c93825ff41b696fcb`, that of the brief
  after `brief`, `sha256:9ecf93c1728a160a88936ba1cbe4fe2e477b17d531f5e257224446a2703e4567`, and
  `trust    exploratory; human acceptance not measured`; the output directory holds the four
  artifacts
- **AND** the models load the fixtures to a plan and a brief of those two content addresses

### Scenario: what is read and cannot be used is said as before

- **GIVEN** the inputs, and one more result of a metric no step of the plan asks for
- **WHEN** the investigation is written
- **THEN** it is refused with `unexpected canonical result outside declared steps:
  metric://sales/profit@1`
- **AND** the result given twice is refused with `duplicate canonical result for metric
  metric://sales/revenue@5`
- **AND** an output directory that is there is refused with `output directory already exists:
  <path>` and stays empty

## Requirement: what a schema refuses of a run input is said in one line

The second requirement names a run input that was read and is not what the file must hold, and
left the rest of the line to the schema: after `<file> is not a valid semantic model: ` came
what pydantic raised, printed as pydantic lists it — a line that counts the errors, and for
each of them a line for the place, a line for the reason with the value it is about, and a line
for a link to its documentation. The fifth requirement gave `decision investigate` one line for
the same refusal.

Reproduced at HEAD `4720405`, 2026-10-05, PyYAML 6.0.3 and pydantic 2.13.4 on Python 3.13.13,
with the commands as processes over the run of the second requirement and one damage to one
file at a time: the file replaced by a list, by a text, by nothing or by the mapping
`unknown_key: 1`; one more key appended, or four, or a key with a line break in it, a key that
is a number, a key that holds a lone surrogate; a value of the wrong kind written deep inside
the document. Each of the five run files was damaged so for `decision verify`, and the verified
asset and the candidate's `model.yaml` for `decision capture` as well. Of the 70 runs 61 exited
2 with 3 to 22 lines on standard error: 42 of the 50 of `decision verify` and 19 of the 20 of
`decision capture`. A file that held `unknown_key: 1` where a verified asset was looked for
took 22 lines, four keys too many in a file 13. The other nine runs are the ones in which no
schema refuses anything: a dashboard spec or a results file that holds no mapping, and an
empty `model.yaml`.

A results file holds a result for each asset it was recorded for, and the refusal of one of
them did not say which: `<file> holds an invalid recorded result: ` and the listing, the same
under every key of the file.

A run input that was read and that its schema refuses SHALL be a usage error of one line: the
words of the second requirement that name the file — `<file> is not a valid semantic model: `,
`<file> is not a valid DashboardSpec: `, `<file> is not a valid verified asset: ` — and after
them the refusals of the schema as the fifth requirement gives them: `<place>: <reason>` for
each of the first three errors, joined by `; `, then `; (+<n> more)`; no value and no link,
every run of whitespace one space.

A results file is a mapping from the id of an asset to the result recorded for it, bare or
attested, and each result is given to the schema on its own. A result the schema refuses SHALL
be named by the key the file holds it under: `<file> holds an invalid recorded result for
<key>: <refusals>`. The key SHALL be written the way Python writes a literal of it — text in
quotes, a line break or a lone surrogate inside it as an escape; a number, nothing or a truth
value as PyYAML built it — so the line is one line whatever the key is: nothing has required
the key to be the id of an asset by the time the file is read. The places of the refusals are
places inside that result — `result.rows.1`, `attestation.origin`, `<root>` for a value that
is no result at all. The results SHALL be read in the order of the file, and the first one
that is refused ends the reading.

What keeps a semantic ref from being frozen SHALL be said in one line as well. The refusal of
the schema of the model is said of the `model.yaml`, as above. The refusal of any other schema
— raised while the graph of the ref is built out of files their own schemas accepted — SHALL be
said of the ref with the name of the schema that refused: `cannot load semantic ref <ref>: not
a valid <schema>: <refusals>`. The places are then places of what that schema describes, which
is no file of the ref. Anything else that is raised as a `ValueError` there SHALL follow
`cannot load semantic ref <ref>: ` as the text it says, every run of whitespace one space — or
as its class, when it says nothing.

The same SHALL hold for a `ValueError` that is no refusal of a schema, raised while the
candidate's model is read for the compile, while a dashboard spec is taken, while the assets
are loaded or while a recorded result is taken: after the words that name the file — for the
assets `<assets path> holds an invalid verified asset: ` — the text it says with every run of
whitespace as one space, or its class when it says nothing.

`VerifiedAssetRepository.load` SHALL raise its `VerifiedAssetFileError` with that one line as
its text and the schema's `ValidationError` as its cause: whoever loads a repository in a
program still has every refusal, each with its value.

Each of these SHALL end the command as the second requirement says: exit code 2, the one line
after `error: ` on the standard error stream and nothing on standard output; no evidence
directory, no query opened and no output file.

No input that gave a bundle or a captured result gives another one: the verifier version and
the check version SHALL NOT be bumped. What changes is the text of usage errors that were usage
errors already, each of them of several lines: the refusals of a schema are one line; a
recorded result is named by its key, where `holds an invalid recorded result: ` named none;
the refusal of another schema follows `not a valid <schema>: `, where the first line of
pydantic's listing named the schema.

In the scenarios below *the run* and *a run file* are those of the second requirement, and
*the words that name a file* are `<file> is not a valid semantic model: ` for the `model.yaml`
of either ref, `<file> is not a valid DashboardSpec: ` for the dashboard spec and `<file> is
not a valid verified asset: ` for a file under the assets path.

### Scenario: keys a schema does not know are refused in one line

- **GIVEN** the run, and appended to the base's `model.yaml`, to the candidate's, to the
  dashboard spec or to the verified asset: one key `unknown_key`; the four keys `key_a`,
  `key_b`, `key_c` and `key_d`; a key that is the letter `a`, a line break, two spaces and the
  letter `b`; or the key `1`, a number
- **WHEN** `decision verify` runs, and — for the verified asset and the candidate's
  `model.yaml` — `decision capture`
- **THEN** each is refused with the words that name that file and then `unknown_key: Extra
  inputs are not permitted`; `key_a: Extra inputs are not permitted; key_b: Extra inputs are
  not permitted; key_c: Extra inputs are not permitted; (+1 more)`; `a b: Extra inputs are not
  permitted`; `1: Keys should be strings`

### Scenario: a model that is no mapping is refused at the root

- **GIVEN** the run, and the `model.yaml` of the base ref or of the candidate ref holding a
  list or a text
- **WHEN** `decision verify` runs, and — for the candidate ref — `decision capture`
- **THEN** each is refused with `<that file> is not a valid semantic model: <root>: Input
  should be a valid dictionary or instance of SemanticModel`

### Scenario: a value of a model is refused by its place and not shown

- **GIVEN** the run, and the columns of the first table of the `model.yaml` of the base ref or
  of the candidate ref replaced by a text
- **WHEN** `decision verify` runs, and — for the candidate ref — `decision capture`
- **THEN** each is refused with `<that file> is not a valid semantic model: tables.0.columns:
  Input should be a valid list`

### Scenario: a spec that holds none of its fields is told each of three

- **GIVEN** the run, and the dashboard spec holding the mapping `unknown_key: 1` alone
- **WHEN** `decision verify` runs
- **THEN** it is refused with `<that file> is not a valid DashboardSpec: title: Field required;
  charts: Field required; unknown_key: Extra inputs are not permitted`: three refusals are all
  spelled out and nothing is counted

### Scenario: values of a spec are refused by their places and not shown

- **GIVEN** the run, and the measures of the first chart of the dashboard spec replaced by one
  measure whose column is the number 5 and whose aggregate is `most`
- **WHEN** `decision verify` runs
- **THEN** it is refused with `<that file> is not a valid DashboardSpec:
  charts.0.query.measures.0.column: Input should be a valid string;
  charts.0.query.measures.0.agg: Input should be 'sum', 'avg', 'min', 'max', 'count' or
  'count_distinct'`

### Scenario: a file that holds no asset is told what it lacks in one line

- **GIVEN** the run, and beside its asset a file `revenue_by_region.yaml` holding nothing, the
  mapping `unknown_key: 1`, or a list
- **WHEN** `decision verify` runs, and `decision capture` over the same assets
- **THEN** each is refused with `<that file> is not a valid verified asset: ` and then
  `metadata: Field required; question: Field required; intent: Field required; (+3 more)` for
  the file that holds nothing, the same three and `(+4 more)` for the mapping, and `<root>:
  Input should be a valid dictionary or instance of VerifiedAsset` for the list

### Scenario: values of an asset are refused by their places and not shown

- **GIVEN** the run, and its verified asset with the second row of the expected result a text
  where a list is expected, and with a list as its `review.reviewed_at`
- **WHEN** `decision verify` runs, and `decision capture` over the same assets
- **THEN** each is refused with `<that file> is not a valid verified asset:
  expectation.result.rows.1: Input should be a valid list; review.reviewed_at: Value error,
  must be an RFC 3339 UTC timestamp such as 2026-08-25T09:00:00Z` (the words since T-118,
  [UTC timestamps](utc-timestamps.md))

### Scenario: the repository says it in one line and keeps what the schema raised

- **GIVEN** beside an asset a file `revenue_by_region.yaml` holding the mapping `unknown_key: 1`
- **WHEN** the repository is loaded from the directory
- **THEN** it raises `VerifiedAssetFileError` whose text is `<that file> is not a valid verified
  asset: metadata: Field required; question: Field required; intent: Field required; (+4
  more)` and whose cause is the schema's `ValidationError` of seven errors

### Scenario: a recorded result that is none is named by its key

- **GIVEN** the run, and in its results file, under the id of the asset, the type of the first
  column of the result changed to `colour` and the second row of the result to a text
- **WHEN** `decision verify` runs
- **THEN** it is refused with `<that file> holds an invalid recorded result for '<id of the
  asset>': result.columns.0.type: Input should be 'string', 'integer', 'decimal', 'float',
  'boolean', 'date' or 'timestamp'; result.rows.1: Input should be a valid list`
- **AND** when the result is recorded bare, with no attestation, the same two refusals stand
  at `columns.0.type` and `rows.1`
- **AND** an attestation whose `origin` is `guesswork` is refused with `<that file> holds an
  invalid recorded result for '<id of the asset>': attestation.origin: Input should be
  'query_run', 'bi_export' or 'manual'`
- **AND** a number in place of the result of an attested entry is refused with `<that file>
  holds an invalid recorded result for '<id of the asset>': result: Input should be a valid
  dictionary or instance of ResultSet`

### Scenario: the first result that is none ends the reading

- **GIVEN** the run, and after its result in the results file the lines `first: 1` and
  `second: 2`
- **WHEN** `decision verify` runs
- **THEN** it is refused with `<that file> holds an invalid recorded result for 'first': <root>:
  Input should be a valid dictionary or instance of ResultSet`

### Scenario: the key of a result is named as the file gives it

- **GIVEN** the run, and one more line in the results file that holds the number 1 under a key
  that is the number `1`, empty text, nothing (`~`), `true`, the text `it's`, the letter `a`, a
  line break, two spaces and the letter `b`, or — handed on past the loader — a lone surrogate
- **WHEN** `decision verify` runs
- **THEN** it is refused with `<that file> holds an invalid recorded result for <key>: <root>:
  Input should be a valid dictionary or instance of ResultSet`, the key being `1`, `''`, `None`,
  `True`, `"it's"`, `'a\n  b'` with the line break as the two characters of its escape, and the
  surrogate as the escape of its code in quotes
- **AND** each refusal is one line

### Scenario: the refusal of another schema is said of the ref with that schema

- **GIVEN** the run, and freezing a ref raising the `ValidationError` of a schema `_Gap` that
  requires a field `reason`
- **WHEN** `decision verify` runs, and `decision capture`
- **THEN** the first is refused with `cannot load semantic ref <the base ref>: not a valid
  _Gap: reason: Field required` and the second with the same of the candidate ref
- **AND** with a lone surrogate as the first dimension of the metric contract of a ref, as in
  the second requirement, the refusal begins `cannot load semantic ref <that ref>: not a valid
  SemanticGap: reason: ` and is one line

### Scenario: what else is raised for a file that was read is said in one line

- **GIVEN** the run, and one of three errors — a `ValueError` that says `two`, a line break,
  two spaces, `lines`, a tab and `of it`; a `ValueError` that says nothing; a `UnicodeError`
  that says two spaces — raised while a ref is frozen, while the candidate's model is read for
  the compile, while the dashboard spec is taken, while the assets are loaded, or while a
  recorded result, attested or bare, is taken
- **WHEN** `decision verify` runs, and — for the ref and for the assets — `decision capture`
- **THEN** the refusal ends `two lines of it`, `ValueError` and `UnicodeError`, after `cannot
  load semantic ref <ref>: `, `<the candidate's model.yaml> is not a valid semantic model: `,
  `<the spec> is not a valid DashboardSpec: `, `<assets path> holds an invalid verified asset:
  ` and `<the results file> holds an invalid recorded result for '<id of the asset>': `
- **AND** the refusal of another schema raised while the candidate's model is read for the
  compile is `<the candidate's model.yaml> is not a valid semantic model: reason: Field
  required`

### Scenario: the commands exit 2 with one line and write nothing

- **GIVEN** the run, and four more keys, or a key that holds a lone surrogate and is handed on
  past the loader, appended to a run file — for `decision capture`, to the verified asset or to
  the candidate's `model.yaml`
- **WHEN** `decision verify` runs, or `decision capture`
- **THEN** the command exits 2, prints nothing on standard output and one line on standard
  error, which begins `error: <that file> ` and holds neither `input_value` nor a link;
  `decision verify` leaves no evidence directory, `decision capture` opens no query and writes
  no output file

## Requirement: an escape that names a surrogate makes a YAML file one that cannot be read

A double-quoted scalar of YAML may name a character by its code, in an escape of sixteen bits
or of thirty-two. The scanner `yaml.safe_load` runs builds a string from the code of a surrogate
as well, U+D800 to U+DFFF — half of the UTF-16 form of a character, with no UTF-8 form and no
form in canonical JSON — and builds two surrogates of the two escapes JSON reads as one
character outside the basic plane. libyaml refuses each such escape. What became of the string
was up to whoever read the document next, and the requirements above followed it there: a
schema refused it in one field and took it in the next, the digest of a ref or of a run had no
form for it, a report that named a contract by it could not be printed.

Reproduced at HEAD `cbef9e2`, 2026-10-05, PyYAML 6.0.3 and pydantic 2.13.4 on Python 3.13.13,
with the commands called in the process and the escape `"\ud800"` — alone, and between two
letters — as each string and each key the fixture's files hold: the product and the metric
contract under the validated paths, the `model.yaml` and the metric contract of each ref, the
dashboard spec, the verified asset, the results file, the plan and a result of an
investigation. 666 places, 1332 documents, 1934 runs:

- 50 ended in an exception nothing handles. Of `decision validate`, 12: `UnicodeEncodeError`
  while the report was printed to a stream that encodes UTF-8, with the escape as the `kind` or
  the `id` of a contract or as a dimension of the scope of the product. Of `decision verify`,
  38: `CanonicalizationError` in 18 — a dimension of the scope of the product, the `kind` or the
  `id` of the validated metric contract, the title of the dashboard, the id or the title of a
  chart, the environment or the executor of an attestation — and `ValidationError` in 20 — a
  dimension or a key of the grain of the validated metric contract (`SemanticGap`), the unit of
  a column or the text of a cell in the expected and in the recorded result
  (`ResultDifference`);
- 4 runs of `decision validate` exited 0: the metric contract passed with the escape as a key
  of its grain;
- 464 runs were graded, 4 with exit 3 and 460 with exit 4, by what a schema made of the string
  or with the string taken, and in 124 `decision capture` went on to the engine;
- 1292 were refused with exit 2 by whatever met the string first — a schema, the graph of a
  ref, the lack of a product — in words that say nothing of an escape.

The loader every decision command reads a YAML document with (`auto_bi/yaml_document.py`) SHALL
refuse a document one of whose scalars holds a surrogate, before anything is built of the
document. The refusal SHALL be a `SurrogateEscapeError`: a `yaml.MarkedYAMLError`, so that every
reader answers it as it answers a text PyYAML refuses, with the problem `found an escape that
names a surrogate`, no cause, and the mark of the place the scalar begins at — its opening
quote, or the tag or the anchor that stands before it.

Every code from U+D800 to U+DFFF SHALL be refused: named by the escape of sixteen bits or by
the escape of thirty-two, in small or in capital digits, alone or among other characters, in a
value, a key, an item of a sequence or a scalar that is the whole document. Two escapes that
JSON reads as one character SHALL be refused as well; the loader combines nothing. Of several
such scalars the one named SHALL be the first in the order of the document: a key before its
value, an entry of a mapping before the next, an item before the next, and what an entry holds
before the entry after it. A node that aliases repeat SHALL be looked at once and named where
it is anchored, so that a document that holds itself is read, and one whose aliases multiply
its nodes is looked at in as many steps as its text names nodes.

What PyYAML refuses itself — a text it cannot scan, parse or compose — SHALL be refused as
PyYAML refuses it, with its class and its position: a document is composed before it is looked
at. Nothing SHALL be built of a document that is refused, so that a value beside the escape that
could not be built — a date that does not exist, `!!binary` that is not base64, a tag the loader
does not know, a key that cannot be one — does not change the answer.

The letters of such an escape where the scanner replaces nothing SHALL be read as the text they
are: in single quotes, in a plain scalar, in a block scalar, after an escaped backslash, in a
comment. A character outside the basic plane SHALL be read as itself and by its own escape of
thirty-two bits. Every other document SHALL be read exactly as before.

Each reader SHALL answer as the requirements above have it answer a text PyYAML refuses:

- a file under the validated paths is one issue `contract.unreadable`, block, with the message
  `cannot read contract: SurrogateEscapeError` — whichever contract the file is and wherever the
  escape stands in it; `decision validate` prints its report, and a product contract with such
  an escape is no product: `decision verify` over paths that hold no other names the file among
  the paths that could not be read;
- a metric contract of a semantic ref is one gap `gap.contract_invalid`, block, with the reason
  `metric contract cannot be read: SurrogateEscapeError` and the digest of the file's bytes;
- the `model.yaml` of a ref, a dashboard spec, a verified asset and a results file are a usage
  error of `decision verify` and of `decision capture`: `cannot read <file>: SurrogateEscapeError
  at line <n>, column <m>`, exit 2, before any grading, any question to an engine and any
  write;
- the plan and a result of `decision investigate` are the same usage error, and the output
  directory is not created.

A finding that is sealed names the class alone. The line of a usage error names the place and
quotes nothing of the file. The text of the refusal itself shows the line of the file, as the
text of an error of PyYAML does, and SHALL be put into no finding and no line.

With the requirement, in the same 1934 runs: 1252 are refused with exit 2 in one line that
names the refusal; in 82 `decision validate` prints the report that names it and says that no
product is left; in 480 — `decision validate` and `decision verify` over the validated metric
contract, `decision verify` over the metric contract of a ref — the command exits 4 with the
file named as one that cannot be read; in 120 `decision capture` goes on to the engine with the
metric contract of the candidate ref unread. None ends in an exception.

A run over such a file gave a bundle where a schema took the string or refused it, and gives
another one now, or none: the verifier version SHALL be 94 and the check version 69. No bundle
that was written is rewritten.

What stood behind the loader keeps its own answer for such a string — the schemas of the fifth
requirement, the graph and the digest of a ref, the key of a recorded result, the identity of a
run. No YAML file hands it to them any more; a reader that is not this loader can, and the
scenarios of those requirements that hold a lone surrogate are run with the string handed on
past the loader.

In the scenarios below *such an escape* is `"\ud800"` in a double-quoted scalar unless another
code is named; *the loader* is `load_yaml_document`; *the run*, *the validated paths* and *the
inputs* are those of the requirements above.

### Scenario: PyYAML builds a string of such an escape

- **GIVEN** the text `a: "\ud800"`, and the text `a: "\ud83d\ude00"`
- **WHEN** `yaml.safe_load` reads each
- **THEN** the first gives a string of one surrogate, which has no UTF-8 form, and the second a
  string of two

### Scenario: such an escape is refused where its scalar begins

- **GIVEN** a text with one such escape: as a value; as a key; as an item, between two letters;
  in a flow mapping inside a flow sequence; in the second line of a scalar of two lines; by the
  escape of thirty-two bits; in capital digits; as the document itself; as the two escapes JSON
  reads as one character; in a scalar with the tag `!!str`; in a scalar with an anchor
- **WHEN** the loader reads it
- **THEN** it is refused with a `SurrogateEscapeError`, which is a `yaml.YAMLError` and has no
  cause, with the problem `found an escape that names a surrogate`
- **AND** its mark is the place the scalar begins at — the opening quote, the tag or the anchor
  — and the reason a reader is given is `SurrogateEscapeError at line <n>, column <m>` of that
  place: line 1, column 8 for `title: "\ud800"`, line 3, column 5 for an item on the third
  line, line 1, column 4 for the scalar after `a: ` whose second line holds the escape

### Scenario: the surrogates are refused and what stands next to them is not

- **GIVEN** the text `a: "x<escape>y"` with the escape of sixteen bits, or of thirty-two, of the
  code D7FF, D800, DBFF, DC00, DFFF or E000
- **WHEN** the loader reads it
- **THEN** D800, DBFF, DC00 and DFFF are refused, and D7FF and E000 give the text of three
  characters

### Scenario: the letters of such an escape are text where nothing is escaped

- **GIVEN** the six letters of the escape in single quotes, in a plain scalar, in a literal
  block, in a folded block, after an escaped backslash in double quotes, or in a comment
- **WHEN** the loader reads the text
- **THEN** it gives what `yaml.safe_load` gives: the letters as text, and for the comment the
  value that stands before it

### Scenario: a character outside the basic plane is read

- **GIVEN** the character U+1F600 written as itself in double quotes, as itself in a plain
  scalar, or by its escape of thirty-two bits
- **WHEN** the loader reads the text
- **THEN** the value is that one character

### Scenario: the first of several is the one refused

- **GIVEN** a text with such an escape in a key and in its value; in two entries; inside the
  first entry and in the entry after it; in the second item of a sequence and in the key after
  the sequence; in two items
- **WHEN** the loader reads it
- **THEN** the place named is the key's, the first entry's, the one inside the first entry, the
  item's, and the first item's

### Scenario: a node that aliases repeat is looked at once

- **GIVEN** the lines `a: ok`, `b: &held ["\ud800"]` and `c: *held`
- **WHEN** the loader reads them
- **THEN** the text is refused at line 2, column 11, where the scalar stands under its anchor
- **AND** the document `&all [*all, "\ud800"]`, which holds itself, is refused at line 1,
  column 13, and `&all [*all, b]` and `a: &held {b: *held, c: [*held]}` are read, each holding
  itself — within a count of calls that a walk without an end would pass, and that ends such a
  walk as a failed test
- **AND** of sixteen sequences, each holding the one before it twice — two scalars repeated
  65536 times — eighteen scalars are looked at: the two and the sixteen keys
- **AND** a scalar under a hundred flow sequences is refused at line 1, column 101

### Scenario: a text PyYAML refuses itself is refused as PyYAML refuses it

- **GIVEN** the line `a: "\ud800"` followed by the line `b: [`
- **WHEN** the loader reads the text
- **THEN** it is refused with the `ParserError` `yaml.safe_load` raises for it, in the same
  words

### Scenario: composing a document and building it convert what they raise alike

- **GIVEN** a text PyYAML reads, and composing its document, or building it, raising
  `MemoryError`, `KeyboardInterrupt`, `SystemExit` or a `LookupError`
- **WHEN** the loader reads the text
- **THEN** the first three are raised as they are, and the fourth is a `ConstructorError` with
  the `LookupError` as its cause and the text `LookupError while the document was built`

### Scenario: nothing is built of a document that holds such an escape

- **GIVEN** a text with such an escape and, beside it, something PyYAML refuses while it builds:
  a date that does not exist on the line before; the tag `!!int` or `!!binary` on the scalar
  itself; a tag the loader does not know on the line after; a sequence, which cannot be a key,
  holding the scalar
- **WHEN** the loader reads it
- **THEN** it is refused with `SurrogateEscapeError` at the place of the scalar

### Scenario: what the refusal says of itself is not what a finding keeps

- **GIVEN** the text `owner: "held by the file \ud800"`
- **WHEN** the loader refuses it
- **THEN** the text of the refusal begins with the problem, names line 1, column 8 and shows the
  line of the file; the reason a reader is given holds nothing of that line

### Scenario: a validated file with such an escape is an unreadable contract

- **GIVEN** the validated paths, and one more metric contract `revenue.yaml` whose description
  is such an escape
- **WHEN** the paths are validated
- **THEN** the one issue is `contract.unreadable`, block, with the path of the file as its
  subject and its source and the message `cannot read contract: SurrogateEscapeError`; the
  product and the metric of the fixture are validated as before, and the verdict is block
- **AND** with a lone surrogate — alone, or between two letters — as any one string or key of
  the product contract or of the metric contract, written to the file as PyYAML writes it, the
  first issue is that issue of that file and the file has no other

### Scenario: decision validate prints the finding

- **GIVEN** the validated paths, and one more metric contract whose `kind`, or whose `id`, is
  such an escape
- **WHEN** `decision validate` runs with a standard output that encodes UTF-8
- **THEN** it exits 4 and prints the issue — `block`, `contract.unreadable` and the path, then
  the message and the path in brackets — and, as its last line, `verdict: block (1 product(s),
  1 metric contract(s), 1 issue(s))`
- **AND** with `--json` the issues of the report are that one issue

### Scenario: a product with such an escape leaves no product to verify

- **GIVEN** the run, and such an escape as the first dimension of the scope of its product
  contract
- **WHEN** `decision verify` runs
- **THEN** it is refused with one line that ends `; 1 path(s) could not be read: <that file>
  (cannot read contract: SurrogateEscapeError)`

### Scenario: a metric contract of a ref with such an escape is a gap

- **GIVEN** the base ref, and a lone surrogate — alone, or between two letters — as any one
  string or key of its metric contract, written to the file as PyYAML writes it
- **WHEN** the ref is frozen
- **THEN** the snapshot has one gap: `gap.contract_invalid`, block, with the path
  `contracts.gross_margin_pct.yaml`, the reason `metric contract cannot be read:
  SurrogateEscapeError` and the digest of the file's bytes as `source_value_ref`; its graph
  holds no metric

### Scenario: the gate blocks on a contract with such an escape

- **GIVEN** the run, and such an escape as the `id`, the first dimension, the first key of the
  grain or the description of the validated metric contract
- **WHEN** the gate runs
- **THEN** the verdict is block; `contracts.validate` blocks with `contracts.issues`, and its
  issues are `contract.unreadable` of the file and `metric_ref.unknown` of the pin of the
  product
- **AND** the verifier version is at least 94 and the check version at least 69
- **AND** with such an escape as the description or the first dimension of the metric contract
  of the base ref, or of the candidate ref, `semantic.gaps` blocks with
  `semantic.gaps_present`; the gaps of that ref are the one gap of the file, and the other ref
  has none

### Scenario: no place of a run file takes such an escape

- **GIVEN** the run, and a lone surrogate — alone, or between two letters — as any one string
  or key of the `model.yaml` of the base ref or of the candidate ref, of the dashboard spec, of
  the verified asset or of the results file, written to the file as PyYAML writes it
- **WHEN** `decision verify` runs, and — for the verified asset and the candidate's model —
  `decision capture`
- **THEN** each is refused with `cannot read <that file>: SurrogateEscapeError at line <n>,
  column <m>`, the place the scalar begins at — where a schema took the string before, where a
  schema refused it and where nothing read it

### Scenario: the commands exit 2 with the file and the place

- **GIVEN** the run, and such an escape as the description of the first table of the model of
  the base ref or of the candidate ref, the title of the dashboard spec, the question of the
  verified asset or the first cell of the recorded result
- **WHEN** `decision verify` runs, and — for the verified asset and the candidate's model —
  `decision capture`
- **THEN** the command exits 2, prints nothing on standard output and the one line `error:
  cannot read <that file>: SurrogateEscapeError at line <n>, column <m>` on standard error;
  `decision verify` leaves no evidence directory, `decision capture` opens no query and writes
  no output file

### Scenario: no place of an input of an investigation takes such an escape

- **GIVEN** the inputs, and a lone surrogate — alone, or between two letters — as any one
  string or key of the plan or of the result, written to the file as PyYAML writes it
- **WHEN** the investigation is written
- **THEN** it is refused with `cannot read <that file>: SurrogateEscapeError at line <n>,
  column <m>`
- **AND** `decision investigate` over a result whose `unit` is such an escape, or over a plan
  with such an escape as a key in place of its `kind`, exits 2 with that one line on standard
  error, nothing on standard output and no output directory

## Requirement: evidence the filesystem refuses to show is not evidence that is not there

`decision evidence` reads a bundle back: it finds the run under the path it is given, reads the
manifest and reads every artifact the manifest names. What is not there has an answer of its
own — an artifact that is missing is the finding `evidence.artifact_missing` and blocks, a run
without a manifest is no bundle. What the filesystem refuses the reader was read as the same
absence: the store raised one class of error for both, and its looks and those of the command
went through `pathlib`, as the boundary of the fourth requirement said.

Reproduced at HEAD `7f2d8ab`, 2026-10-05, on macOS as a user the permission bits keep out, with
the commands as processes. One bundle of 15 artifacts, written by `decision verify`, in 19
states: untouched; the evidence directory, the run directory, the directory above them and the
directory of the checks, each of mode 000, 0o300 and 0o400; `manifest.json` and one check of
mode 000; beside the run a directory that may not be searched, and a directory and a file that
may; a directory where a check was; a check that is gone. Each state was read six ways — by the
evidence path, by that path with `--run`, by the run directory, by the evidence path with
`--json`, and by the evidence path and by the run directory with `--artifact` for that check:
114 runs of `decision evidence` under each of Python 3.12.13, 3.13.13 and 3.14.6.

- Under 3.12 and 3.13, 42 runs end in a traceback of `PermissionError` and exit code 1: every
  run over an evidence directory, a run directory or a directory above them of mode 000 or
  0o400, and, where the run is not named, the runs over an evidence directory that may not be
  listed and over a directory beside the run that may not be searched.
- Under 3.14, 8 runs end so, and 31 say of a bundle that is there that it is not: `evidence path
  not found: <path>`, `no evidence bundle under <path>: no run directory holds manifest.json`,
  `run '<run id>' under <path> has no manifest.json`. The directory beside the run that may not
  be searched is passed over, and the run is read as the only one under the path.
- Under each of the three, a directory of the checks of mode 000 or 0o400 is
  `evidence.artifact_missing` for each of its nine checks, `<artifact id>: <path> is not in the
  store`, with the verdict `block` and exit code 4; one check of mode 000 is that finding once,
  and `--artifact` for it answers `<artifact id>: <path> is not in the store`. A `manifest.json`
  of mode 000 is `run '<run id>' has no manifest.json: this is not an evidence bundle`.
- `decision verify` reads back the bundle it wrote and reports what it found as the check
  `evidence.bundle`. Run into a new evidence directory under a umask that takes the read of what
  is written (0o400), and run again over a bundle one check of which may be written and not
  read (mode 0o200), it reports `evidence.invalid`, the verdict `block` and exit code 4 — of a
  bundle that is whole on disk.

The filesystem store and `decision evidence` SHALL take their looks at a path as the fourth
requirement has the other commands take theirs: answered by the operating system on every
supported interpreter, "no" for a path that is not there by the same codes, and any other
refusal a refusal. Whether a path is a symbolic link is such a look, and it follows no link.

The store SHALL tell what the filesystem refuses it from what it says itself. A look, a read or
a write the filesystem refuses SHALL raise `ArtifactRefusalError`, an `ArtifactStoreError` that
keeps the `OSError` as its cause and as its `refusal`, which names the path refused: `cannot
look for '<path>' of run '<run id>': <class>`, `cannot read '<path>' of run '<run id>':
<class>`, `cannot write '<path>' of run '<run id>': <class>`. What the store says by its own
rule SHALL stay an `ArtifactStoreError` that is no refusal: a run id or a path it does not
take, a symbolic link where the artifact is or on the way to it, whatever the link leads to,
and an artifact that is missing. An artifact is a regular file. Asked to read a path that holds
nothing, a directory or anything else that is no regular file, the store SHALL answer `artifact
'<path>' of run '<run id>' is missing` without opening it, and SHALL answer "no" when asked
whether the artifact is there. An artifact that went away between the look and the read is
missing too.

`verify_bundle` grades a bundle by what the store answers. A manifest or an artifact the store
says is missing SHALL be `evidence.manifest_missing` or `evidence.artifact_missing`, as before.
A refusal SHALL NOT be graded: the `ArtifactRefusalError` of the store is raised as it is,
whether the manifest was read by `verify_bundle` or handed to it.

`inspect_bundle` and `read_artifact` SHALL answer such a refusal with `EvidenceReadError`,
`cannot read <the path refused>: <class of the refusal>`, with the error of the store as its
cause; a refusal that names no path is said as the store says it. `decision evidence` SHALL
then exit 2 with that one line after `error: ` on the standard error stream and nothing on the
standard output stream — as text, with `--json` and with `--artifact`. No verdict is given: a
verdict over what could be read would grade a bundle nobody read. `--artifact` reads the
manifest and the one artifact, as before, so an artifact is printed out of a bundle another
artifact of which may not be read.

While `decision evidence` finds the run, a look the filesystem refuses SHALL be the usage error
`cannot read <path>: <class of the refusal>`: at `<evidence path>/manifest.json`, at the
evidence path itself, at the manifest of the run that `--run` names. An evidence path that
cannot be listed SHALL be `cannot read <evidence path>: <class>` where no run is named. A
directory under the evidence path that may not be looked into SHALL be `cannot read <that
directory>/manifest.json: <class>; name the run to read with --run` where no run is named: it
may hold the run that was meant, or a second one, and which run is read would be a guess. Where
the run is named, the evidence path is not listed and no other directory is looked into. What
is not there SHALL be said as before: `evidence path not found: <path>`, `no evidence bundle
under <path>: no run directory holds manifest.json`, `run '<run id>' under <path> has no
manifest.json`, `<path> holds <n> runs (<their ids>); name the one to read with --run`; a file,
and a directory that holds no manifest, beside the run is passed over.

When the store is refused while `decision verify` reads back the bundle it wrote, the command
SHALL end with the usage error `cannot read back the evidence bundle under <evidence
directory>: ` followed by the message of the store, exit code 2 and that one line. The gate
raises `EvidenceReadBackError`, which keeps the refusal of the store as its cause, and grades
nothing: the bundle is reported neither as verified nor as invalid. It stays as it was written,
and a reader the filesystem lets read it verifies it. A refusal before anything is written —
the manifest of a bundle that is already under the run id may not be read — stays the usage
error of the third requirement, `cannot write the evidence bundle under <evidence directory>:
cannot read 'manifest.json' of run '<run id>': <class>`. An artifact that is not there when the
bundle is read back SHALL be graded as before: `evidence.bundle` blocks with
`evidence.invalid`.

Nothing a run writes changes. A bundle that is read back is byte for byte the bundle it was,
and a run that is refused the reading wrote the same bundle and reported `block` where it now
reports nothing. The verifier version and the check version are therefore not raised.

In the scenarios below *the bundle* is one written under an evidence path: a manifest, the
check `checks/check-freshness.json`, a blob and the report `reports/summary.md`. *A look that
is refused* is one the operating system answers with `PermissionError`, and *a read that is
refused* is the read of a file that can be looked at, answered the same way. *The three
addresses* of the run are its evidence path, that path with `--run`, and the run directory
itself. *The three forms* are the report as text, the report with `--json`, and `--artifact`
for the check.

### Scenario: a look says whether a path is a link, and follows none

- **GIVEN** a file, a directory, a link to each, a link to nothing and a link to itself
- **WHEN** each is asked whether it is a symbolic link
- **THEN** the four links are and the file and the directory are not, asked by a path or by the
  text of one; what a link leads to is told by the looks that follow it
- **AND** a path where nothing is, a path below a file and a name that holds a NUL are no link

### Scenario: a refused look at a link is a refusal, and "not there" is told by what was answered

- **GIVEN** a file the look at which that follows no link the operating system answers with
  `EACCES` or with `EIO`; and a link the looks at which it answers with `ENOENT`, `ENOTDIR`,
  `EBADF` or `ELOOP`, with the Windows code 21, 123 or 1921, and with the Windows code 5
- **WHEN** each is asked whether it is a link
- **THEN** `EACCES` and `EIO` are raised as they were answered, `PermissionError` and `OSError`
  with that code and the path as the `filename`, while the look that follows a link, which was
  not refused, answers; the Windows code 5 is raised as well; the others are "no"
- **AND** where modes are kept, a path under a directory of mode 000 or 0o400 — one that is
  there and one that is not — raises `PermissionError` that names it, and the directory itself
  is looked at through the one above it

### Scenario: the store raises what the filesystem refuses as a refusal

- **GIVEN** the bundle, where the look at the check is refused — the one that follows no link,
  or the one that follows a link
- **WHEN** the check is looked for and read
- **THEN** `ArtifactRefusalError` is raised, `cannot look for 'checks/check-freshness.json' of
  run '<run id>': PermissionError` and `cannot read 'checks/check-freshness.json' of run '<run
  id>': PermissionError`, each with the `PermissionError` as its cause and as its `refusal`,
  which names the check; the manifest beside it is there and the report is read, as before
- **AND** with the look at the directory of the checks refused, the refusal names that
  directory, and writing another check into it is `cannot write 'checks/another.json' of run
  '<run id>': PermissionError` and writes nothing

### Scenario: a read or a write the filesystem refuses is named with its class

- **GIVEN** the bundle, where the read of the check is answered with `EACCES` or with `EIO`;
  and an artifact to be written below the check, a file where a directory would have to be
- **WHEN** the check is looked for and read, and the artifact is written
- **THEN** the check is there, and reading it raises `ArtifactRefusalError`, `cannot read
  'checks/check-freshness.json' of run '<run id>': PermissionError` or `: OSError`, with a
  refusal of that code that names the check; the write raises `ArtifactRefusalError`, `cannot
  write '<path>' of run '<run id>': ` and the class of its refusal, and the check holds what it
  held

### Scenario: a path that holds no regular file holds no artifact

- **GIVEN** the bundle with nothing, with a directory or with a named pipe where the check was;
  and the bundle whose check goes away between the look and the read
- **WHEN** the check is looked for and read
- **THEN** it is not there, and reading it raises an `ArtifactStoreError` that is no refusal,
  `artifact 'checks/check-freshness.json' of run '<run id>' is missing`; nothing is opened to
  find that out. The check that went away is missing by the same message, with the
  `FileNotFoundError` as its cause

### Scenario: what the store refuses by its own rule is no refusal of the filesystem

- **GIVEN** the bundle with a symbolic link where the check was, or where the directory of the
  checks was, that leads to a copy of what was there or to nothing; and an empty store asked
  with a run id or a path it does not take
- **WHEN** the artifact is looked for, read and written
- **THEN** it is not there, and reading and writing raise an `ArtifactStoreError` that is no
  refusal — `artifact path is a symlink: <path>` and `artifact path crosses a symlink: <path>`
  for the links; nothing is written through a link and nothing into the empty store

### Scenario: the store keeps the refusal of a path whose permissions are taken away

- **GIVEN** the bundle, where modes are kept, with the run directory or the directory of the
  checks of mode 000 or 0o400; and with the check of mode 000
- **WHEN** the check is looked for and read
- **THEN** both raise `ArtifactRefusalError` with a `PermissionError` that names the directory
  of the checks under a run directory that may not be searched, and the check under a directory
  of the checks that may not be; a check of mode 000 is there, and reading it raises `cannot
  read 'checks/check-freshness.json' of run '<run id>': PermissionError`

### Scenario: a manifest or an artifact that may not be read is not graded as missing

- **GIVEN** the bundle, where the look at the manifest or its read is refused; and where the
  look at the check or its read is refused, the manifest read by `verify_bundle` or handed to it
- **WHEN** the bundle is re-checked
- **THEN** `ArtifactRefusalError` is raised and names the manifest, or the check; no problem is
  given

### Scenario: a manifest or an artifact that is not there is graded as before

- **GIVEN** the bundle with nothing or a directory where the check was; then with nothing or a
  directory where the manifest was as well
- **WHEN** the bundle is re-checked
- **THEN** the one problem `evidence.artifact_missing`, `check-freshness:
  checks/check-freshness.json is not in the store`; then the one problem
  `evidence.manifest_missing`
- **AND** a store of another kind that raises an `ArtifactStoreError` for the check or for the
  manifest is graded so, and one that raises `ArtifactRefusalError` has it raised as it is
- **AND** the reader inspects the bundle without the check as `block` with that one problem and
  answers the read of the check with `check-freshness: checks/check-freshness.json is not in the
  store`; with the manifest gone as well it answers `run '<run id>' has no manifest.json: this
  is not an evidence bundle`

### Scenario: the reader names the path it may not read

- **GIVEN** the bundle, where the look at the manifest or at the check, or its read, is refused
- **WHEN** the bundle is inspected, and the check is read out of it
- **THEN** each is refused with `EvidenceReadError`, `cannot read <that path>:
  PermissionError`, with the refusal of the store as its cause
- **AND** the report is read out of the bundle whose check may not be looked at or read
- **AND** a refusal of a store that names no path is said as that store says it — by an
  inspection and by the read of an artifact, for the manifest and for the check

### Scenario: an evidence path the filesystem refuses to show is not one without a bundle

- **GIVEN** the bundle, where the looks at `<evidence path>/manifest.json` and at the manifest
  of the run are refused; where the look at the evidence path is refused; where the listing of
  the evidence path is answered with `EACCES` or with `EIO`
- **WHEN** the run is looked for by its three addresses
- **THEN** in the first it is refused with `cannot read <evidence path>/manifest.json:
  PermissionError` by the evidence path, with and without the run, and with `cannot read <the
  manifest of the run>: PermissionError` by the run directory
- **AND** in the second with `cannot read <evidence path>: PermissionError` by the evidence
  path, with and without the run, while the run directory finds the run
- **AND** in the third with `cannot read <evidence path>: PermissionError` or `: OSError` by
  the evidence path, while the run is found where it is named and by its directory

### Scenario: a directory that may not be looked into is not passed over

- **GIVEN** the bundle and beside the run a directory, listed before the run or after it, the
  look into which is refused; and the bundle where the look at the manifest of the run is
  refused
- **WHEN** the run is looked for by its three addresses
- **THEN** in the first, by the evidence path: `cannot read <that directory>/manifest.json:
  PermissionError; name the run to read with --run`; with the run named, and by the run
  directory, the run is found
- **AND** in the second, by the evidence path: `cannot read <the manifest of the run>:
  PermissionError; name the run to read with --run`, and the same line without what follows
  the class where the run is named or its directory given

### Scenario: what is not there is said of an evidence path as before

- **GIVEN** a path where nothing is; the manifest of the bundle given as the evidence path; an
  empty directory; the bundle asked for the run `run-other`, by the evidence path and by the
  run directory; beside the run a file, an empty directory, and a directory the look at whose
  manifest is answered with `ENOENT`, `ENOTDIR`, `EBADF` or `ELOOP`; then a copy of the run as
  `run-other` beside it
- **WHEN** the run is looked for
- **THEN** `evidence path not found: <path>` for the first two; `no evidence bundle under
  <path>: no run directory holds manifest.json`; `run 'run-other' under <evidence path> has no
  manifest.json`; `<run directory> is run '<run id>', not 'run-other'`; the run is found beside
  what holds no manifest
- **AND** with the copy: `<evidence path> holds 2 runs (<run id>, run-other); name the one to
  read with --run`, and `run-other` is found where it is named

### Scenario: `decision evidence` exits 2 with one line

- **GIVEN** the bundle, where the look at the manifest or at the check, or its read, is refused
- **WHEN** `decision evidence` runs by each of the three addresses in each of the three forms
- **THEN** it exits 2, prints nothing on standard output and the one line `error: cannot read
  <that path>: PermissionError` on standard error — followed by `; name the run to read with
  --run` where the manifest may not be looked at and the run is asked for by the evidence path
  alone
- **AND** with the listing of the evidence path refused it exits 2 by the evidence path, with
  the one line `error: cannot read <evidence path>: PermissionError`, and where the run is
  named, and by the run directory, the bundle is read: exit 0, nothing on standard error, and
  `verdict: pass (0 problem(s))` as text, the verdict `pass` in the JSON, or the text of the
  check
- **AND** with a directory beside the run that may not be looked into it exits 2 by the
  evidence path, with the line that ends `; name the run to read with --run`, and reads the
  bundle by the two other addresses
- **AND** `--artifact` for the report prints the report and exits 0 by each address, where the
  check may not be looked at or read

### Scenario: `decision evidence` grades an artifact that is not there as before

- **GIVEN** the bundle with nothing or a directory where the check was
- **WHEN** `decision evidence` runs by each of the three addresses
- **THEN** as text it exits 4 with the problem `evidence.artifact_missing`, `check-freshness:
  checks/check-freshness.json is not in the store`, and `verdict: block (1 problem(s))`; with
  `--json` it exits 4 with that one problem; with `--artifact` for the check it exits 2 with
  `error: check-freshness: checks/check-freshness.json is not in the store`

### Scenario: `decision evidence` over a bundle whose permissions are taken away

- **GIVEN** the bundle, where modes are kept, with the permissions of one path taken away: the
  directory above the evidence path, the evidence path, the run directory or the directory of
  the checks, each of mode 000, 0o400 or 0o300; the manifest of mode 000; the check of mode 000
- **WHEN** `decision evidence` runs by each of the three addresses in each of the three forms
- **THEN** a refusal is exit 2 with the one line `error: cannot read <path>: PermissionError`,
  and what is not refused is the bundle read
- **AND** the directory above, or the evidence path, of mode 000 or 0o400: the path named is
  `<evidence path>/manifest.json` by the evidence path, with and without the run, and the
  manifest of the run by the run directory
- **AND** the evidence path of mode 0o300: the path named is the evidence path, where the run
  is not named; the bundle is read where it is, and by the run directory
- **AND** the run directory of mode 000 or 0o400: the manifest of the run, followed by `; name
  the run to read with --run` where the run is not named
- **AND** the directory of the checks of mode 000 or 0o400, and the check of mode 000: the
  check; the manifest of mode 000: the manifest
- **AND** the directory above, the run directory or the directory of the checks of mode 0o300:
  the bundle is read by every address

### Scenario: a bundle the gate may not read back ends the run

- **GIVEN** the run, where the read of the manifest, or of every check, that it writes under
  its evidence directory is refused
- **WHEN** `decision verify` runs
- **THEN** it exits 2, prints nothing on standard output and the one line `error: cannot read
  back the evidence bundle under <evidence directory>: cannot read 'manifest.json' of run '<run
  id>': PermissionError` on standard error — or `cannot read 'checks/<a check>.json' of run
  '<run id>': PermissionError` after the colon; the bundle under that directory, inspected by a
  reader that may read it, passes
- **AND** the gate raises `EvidenceReadBackError`, which is no `ArtifactStoreError`, with the
  message of the store and the refusal of the store as its cause, which names the manifest
- **AND** where modes are kept, the command run as a process under a umask of 0o400 answers
  the line of the manifest and exit code 2, and the bundle, once it may be read, passes

### Scenario: a bundle that is read back is reported as before

- **GIVEN** the run; and the run one check of whose bundle is gone once the manifest is written
- **WHEN** the gate runs
- **THEN** in the first `evidence.bundle` passes with `evidence.verified` and a detail that
  ends ` artifact(s), 0 problem(s)`; in the second it blocks with `evidence.invalid` and a
  detail that ends ` artifact(s), 1 problem(s)`, and the verdict is `block`

### Scenario: a run over a manifest it may not read names the refusal

- **GIVEN** the run and the bundle it wrote, where the read of the manifest is refused
- **WHEN** `decision verify` runs again
- **THEN** it exits 2, prints nothing on standard output and the one line `error: cannot write
  the evidence bundle under <evidence directory>: cannot read 'manifest.json' of run '<run
  id>': PermissionError` on standard error; the manifest holds what it held

### Scenario: the readers of evidence look through the functions that keep a refusal

- **GIVEN** the source of the filesystem store and of the use cases of the decision commands
- **WHEN** it is read for the looks and listings of `pathlib` and `os.path` that drop a refusal
  on some interpreter — `exists`, `is_file`, `is_dir`, `is_symlink`, `is_mount`, `is_junction`,
  `iterdir`, `glob`, `rglob`, `walk`, `isfile`, `isdir`, `islink`, `lexists`
- **THEN** none is named — called, handed on as a function or imported — but as a function of
  the module of the fourth requirement, `auto_bi.filesystem`: found on whichever interpreter
  reads the source, where a run of such a look answers one way up to Python 3.13 and another
  from 3.14 on

## Known boundaries

- The finding names the file and the class, not the value or its line: PyYAML's own errors are
  reported by class alone as well, and the message of what was raised may quote the file.
- The class names are PyYAML's and the interpreter's. Which texts cannot be built is theirs too:
  the limit of 4300 digits is the interpreter's default and an environment can raise it
  (`PYTHONINTMAXSTRDIGITS`); the nesting a document may have depends on the interpreter's
  recursion limit and on how deep the call already is. A file read on one configuration may be
  unreadable on another.
- A document nested past the stack is reported as `ConstructorError` like a value that cannot be
  built, although it is the composer that runs out of stack.
- The first requirement covers the contract files, the second and the sixth the run inputs of
  `decision verify` and `decision capture`, the fifth the inputs of `decision investigate`, the
  seventh every YAML file those read. The loaders of the v1 runtime read YAML by themselves and
  are not changed: `SemanticModel.load`, which the v1 commands read a model with,
  `auto_bi/auth.py`, and the `load` of `MetricContract` and of `DecisionProductSpec`, which
  nothing in the package calls, still build a string of an escape that names a surrogate.
- The sixth requirement covers the files a run is handed. What a command line states of the
  run itself — its moment and its environment — is settled before the run starts
  (`stated-run-facts.md`): a `--captured-at` that is no timestamp, which was refused in four
  lines of pydantic's listing and only after the query was run, and an `--environment` with a
  lone surrogate, which was attested as it was given, are usage errors of one line that cost no
  query. Since T-67, `decision capture` reports `ClickHouseError`, `StreamFailureError` and
  `psycopg.Error` with their class and whitespace-normalized text in one line, exit 2, without
  publishing a result or retrying the query ([an engine that did not answer](unanswered-engine.md)).
  Other exceptions retain their existing handling. An `--environment` of blanks or of two
  lines is still attested as it is given.
- A lone surrogate reaches no reader from a YAML file (seventh requirement). What stands behind
  the loader — a schema, the graph and the digest of a ref, the identity of a run — is held to
  its own answer for one only by tests that hand the string on past the loader. It still comes
  in other ways. JSON spells it by the same escape and `json.loads` builds it: reproduced over
  a written bundle with the escape written into seven text fields of its manifest in turn —
  `decision evidence` answers `evidence.manifest_invalid` and exits 4, in text and with `--json`
  — and into two of its artifacts — `evidence.digest_mismatch`; the plan of a query an engine
  hands back as text and the manifest of a dbt project the v1 import reads were not asked. And
  an argument of the command line was taken as it was given: `decision capture` with an
  `--environment` that holds a lone surrogate ran the query, exited 0 and wrote the escape
  into a file `decision verify` refuses. The moment and the environment a command line states
  are refused for one now (`stated-run-facts.md`); a path is still taken as it is given.
- Two escapes that JSON reads as one character are refused, not combined: PyYAML builds two
  surrogates of them, and libyaml refuses each (`found invalid Unicode character escape code`,
  PyYAML 6.0.3). A file some writer exported that way is unreadable until the character is
  written as itself or by its escape of thirty-two bits.
- The place named is where the scalar begins — its opening quote, or the tag or the anchor
  before it — not where the escape stands: in a scalar of several lines the escape may be lines
  below. Of several such scalars only the first is named.
- A contract that spelled such an escape where its schema took the string was graded as the
  contract PyYAML built. Asked at every string and key of the fixture's metric contract under
  the validated paths, that was 4 runs of 120 of `decision validate`, which passed with the
  escape as a key of the grain and block now.
- `decision capture` goes on to the engine with a metric contract of the candidate ref that
  spells such an escape — 120 runs of 120 — as it did at HEAD wherever the schema of the
  contract refused the string, 112 of them: the gap is the snapshot's, and a capture grades
  nothing.
- Every document is composed, looked at and then built, where `yaml.safe_load` did it in one
  call. Timed on the fixture's `model.yaml` and on a model of 4039 lines, the best of seven:
  2.66 ms against 2.72 ms of `yaml.safe_load`, and 210.5 ms against 209.8 ms — no difference
  the measure can tell.
- Before T-64, an empty `model.yaml` was read three ways: of the base ref it was a model of
  nothing and the run was graded; of the candidate ref `decision verify` refused it with
  `<file> must contain a mapping`, and `decision capture` went on to the engine. T-64 supersedes
  this observation: both commands refuse a nonmapping document before the run or engine query.
- Before T-84, the key of a recorded result was not looked at when the file was read.
  [Recorded result keys](recorded-result-keys.md) supersedes this observation: after value
  validation, an unknown key is refused against the full supplied asset repository, before
  probe or evidence. Previously reproduced: a result that
  is one, under a key that is the id of no asset — a word, a number — beside the result of the
  run, is read and never asked for, and the run is graded as without it; the result of the run
  under such a key alone leaves the asset with none — `results.reconcile` is review with
  `result.not_equivalent`, `results.provenance` advisory with `provenance.no_results`.
- The reasons of the fifth and of the sixth requirement are pydantic's sentences, of the version
  installed — 2.13.4 where the scenarios were run; another version may word them differently. A
  place that is a key holding a lone surrogate is given by pydantic in replacement characters;
  a key of a run file that holds one is refused at no place, `<root>: Input should be a valid
  string, unable to parse raw data as a unicode string`. No YAML file hands such a key in any
  more (seventh requirement).
- One line means no line break: every run of whitespace in a refusal is one space. Any other
  character of a key, or of a value a validator quotes, stays in the refusal as it is — an
  escape character among them. Since T-117 the line a decision command prints spells each
  character that is not printable by its escape ([one-line errors](one-line-errors.md)).
- Before T-62, `decision investigate` wrote its output directory file by file. Reproduced with
  the third write refused, `No space left on device`: the run answered `cannot write output
  directory <path>: ` and what the system said, left `plan.json` and `results.json` under the
  target, then refused a retry with `output directory already exists: <path>`. T-62 publishes
  the complete directory after staging; the requirement below supersedes this observation.
- What `decision investigate` prints after it wrote went to a standard output stream that
  refuses a character it cannot encode: with an output path in Cyrillic and
  `PYTHONIOENCODING=ascii` the command ended in a traceback and exit code 1 after the four
  artifacts were written. A decision command now answers on such a stream
  (`unencodable-output.md`).
- The fourth requirement covers `decision validate`, `decision verify` and `decision capture`,
  the eighth `decision evidence`, the filesystem store and the gate's second reading of the
  bundle it wrote. Since T-62, `decision investigate` asks `auto_bi.filesystem` whether its
  output path or a symlink is there; the v1 runtime looks as it did.
- A refusal ends the reading of a bundle (eighth requirement): `decision evidence` gives no
  verdict over what it could read and names the first path it was refused, not every one.
  `--artifact` reads the manifest and one artifact and answers for those alone.
- A bundle the gate wrote and may not read back was the verdict `block` and exit code 4 and is
  a usage error and exit code 2. A caller that reads exit code 2 as "nothing was run" finds a
  whole bundle under the evidence directory. What the gate graded is in that bundle — every
  check and the summary — and whoever may read it reads it there; the run itself prints none
  of it and gives no verdict.
- The store tells an artifact from what is none by a look and reads it after the look. What
  changes between the two is said by what the read was answered: an artifact that went away is
  missing, and anything else — a directory laid where the artifact was — is a refusal of the
  class the system gave. An evidence path that goes away between the look at it and its
  listing is `cannot read <evidence path>: FileNotFoundError`, not `evidence path not found`.
  Neither of the last two was run.
- The eighth requirement tells a refusal from an absence for the filesystem store. A store of
  another kind is graded by what it raises: a refusal it reports as a plain
  `ArtifactStoreError` is read as an absence, as before. A symbolic link where an artifact was
  is the store's own refusal and is graded `evidence.artifact_missing`, as before.
- A refusal is told from "not there" by what the operating system answers. The answers of
  Windows and a directory whose entries are not told apart are given to the code by the tests:
  no run was made on Windows or on a filesystem that withholds the kind of an entry. Directories
  whose permission bits are taken away were run on macOS, as a user the bits keep out; a process
  they do not keep out — root — is refused nothing. The same holds for the files and the umask
  of the eighth requirement.
- A directory that cannot be listed stops the report or the run whatever it holds: the command
  cannot know that it holds no contract and no asset. A validated path with a directory in it
  that its reader may not list blocks until the directory is listed or left out of the paths.
- Under a validated path a file is chosen by its name alone, as before, so a directory named
  like a YAML file is a contract that cannot be read. In a ref and under the assets path such a
  directory is passed over, as before.
- The order in which a walk gives paths is the filesystem's. Every reader sorts what it reads,
  and the refusals under one validated path are sorted by their paths — since verifier 118 name
  by name, by code point with case, on every system
  ([validation report paths](validation-report-paths.md)).
- The dashboards directory is never listed: a spec is looked for by its name. A dashboards
  directory that is searched and not listed therefore reads as any other.
- A contract the filesystem refuses to open is a finding under the validated paths
  (`contract.unreadable`) and ends the run in a semantic ref: a validation report answers for
  its paths whether or not a run follows, and a snapshot says what bytes it was read from.
- Every file under the assets path is loaded, the assets of other products among them: one file
  there that cannot be read ends the run of every product, as one the schema refuses always did.
- `cannot load semantic ref <ref>: ` is followed by what refused, in one line: the place of a
  value in the graph, or `not a valid <schema>: ` and the refusals of a schema no file is
  written against. The value is not graded as a gap of the file that held it. The one value
  known to reach that answer was a lone surrogate, which no file hands in any more. Asked in
  the process with 14 other values — an integer past the safe range and its negative, a float,
  not-a-number, infinity, a truth value, nothing, a list, a mapping, empty text, a line break,
  a control character, a character outside the basic plane, a text of 70000 characters — at
  every scalar place of the `model.yaml` and of the metric contract of both refs, 1904 runs of
  `decision verify`: none is answered so, and 4 end in an exception nothing handles. A table
  of either model named with empty text is taken by the schema of the model and refused where
  the two snapshots are compared — reproduced on the first table, the `ValidationError` of a
  `SemanticChange`.
- Before T-63, `FilesystemArtifactStore.put` wrote the manifest directly to its target. A
  refused partial write could therefore leave a truncated manifest; the repeated run exited 2
  with `run '<id>' already holds bundle <digest>; refusing to overwrite it with <digest>` and
  `decision evidence` exited 4 with `verdict: block (1 problem(s))`. This is a historical
  observation, superseded by the one-file staging contract below.
- The line of a usage error may quote the file: the message of a value that could not be built,
  a key of the document as the place of a refusal, the key of a recorded result, a value the
  validator of a schema puts into its reason. It is printed to the standard error stream and is
  never sealed.
- What a command prints went to a stream that refuses a character it cannot encode. With
  streams that encode ASCII and the fixture under a directory named in Cyrillic, `decision
  validate` printed a clean report and ended in `UnicodeEncodeError` once an issue named a file,
  and `decision verify` ended in it while its checks were printed, at a detail that holds a
  section sign — the use case had returned its report by then. The five decision commands now
  answer on any stream: a character the stream has no form for is printed as its backslash
  escape, a JSON answer in ASCII, and the exit code stays the verdict
  (`unencodable-output.md`).
- A value PyYAML builds and the schema refuses is `contract.invalid` (validated paths) or
  `gap.contract_invalid` with `metric contract does not validate` (semantic ref), as before; an
  unquoted date where the schema wants text is such a value.

## Requirement: an offline investigation publishes a complete output directory

`decision investigate` SHALL build its four artifact payloads before writing to the output
path. If that path exists, including a symbolic link whose destination does not exist, it
SHALL answer `output directory already exists: <path>` and leave the path untouched. After
writing `plan.json`, `results.json`, `brief.json` and `brief.md` into a unique directory beside
the output, it SHALL check the output path again and publish the complete directory with one
rename. The successful artifact bytes, digests and CLI answer SHALL stay the same.

For an ordinary write or rename failure before publication, the command SHALL answer with
exit 2 and no success output, create no output path, and remove its temporary directory. It
SHALL never remove an existing output path. If removing its temporary directory also fails,
the error SHALL name both the original failure and the cleanup failure; a temporary directory
may remain. Parent directories created for the output may also remain.

Since T-75, a `UnicodeEncodeError` during output preparation, writing or publication SHALL
follow that same refusal and cleanup path. The diagnostic SHALL be `cannot write output
directory <path>: UnicodeEncodeError`, without the exception's text. Paths containing
nonprintable characters SHALL be quoted and escaped with `filesystem.display_path` in this
diagnostic, including the temporary path if cleanup fails. When cleanup succeeds or is not
needed, the original error SHALL remain the direct cause; when cleanup fails, its error
SHALL be the direct cause, as before. The CLI SHALL use its existing encoding-safe error
output. Readable
Unicode paths SHALL keep their behavior and artifact bytes; names SHALL NOT be rejected
by character inspection. Other exception classes, including `ValueError` and
`UnicodeTranslateError`, SHALL keep their existing boundaries.

### Scenario: the filesystem cannot encode the output path

- **WHEN** creating the output parent raises `UnicodeEncodeError`
- **THEN** the command exits 2 without success output or an output directory.
- **WHEN** publication raises `UnicodeEncodeError` after all four staged files were written
- **THEN** the command exits 2, removes its temporary directory and leaves no output path.
- **AND** after successful cleanup stderr names the output path and error class in one
  encoding-safe line (also for a name with LF or CR), without
  the original exception's text; an existing output remains untouched.

### Scenario: a write fails after leaving a partial artifact

- **WHEN** any of the four artifact writes leaves a prefix and raises `OSError`
- **THEN** the output path is absent and the owned temporary directory is removed
- **AND** a subsequent invocation with the same inputs and output can publish all four files.

### Scenario: publication fails or the output appears during staging

- **WHEN** the final rename raises `OSError` without a concurrent output creator
- **THEN** the output path is absent and the owned temporary directory is removed.
- **WHEN** another directory appears at the output path during the final artifact write
- **THEN** the command refuses publication, preserves that directory and removes its own
  temporary directory.

This is an atomic visibility guarantee for a completed ordinary run, not a durability or
multi-writer coordination guarantee. On POSIX, an empty output directory created after the
last existence check may be replaced by `os.rename`; changing a parent path concurrently is
also outside the guarantee. Process interruption or termination may leave staging behind.
No filesystem sync is performed for power-loss durability. On POSIX the published directory
inherits `mkdtemp` permissions (`0700` subject to umask), rather than the previous `mkdir`
permissions (`0777` subject to umask).

## Requirement: one artifact write is staged and replaced

`FilesystemArtifactStore.put` SHALL write one payload to a unique staging file beside its
target and publish it with one `os.replace`. On POSIX and for an absent Windows target, staging
SHALL be opened with `os.open(..., 0o600)`. When replacing an existing Windows target, staging
SHALL be created with `CreateFileW(CREATE_NEW)` using the target's owner and DACL in
`SECURITY_ATTRIBUTES`, so the inherited parent DACL never grants an interim read of the new
payload. If the payload write raises `OSError` after writing a prefix, the target SHALL remain
byte-for-byte unchanged
when it existed and SHALL remain absent otherwise. If `os.replace` raises `OSError`, the same
before/after rule SHALL hold. In either case the staging file SHALL be removed when cleanup
succeeds, and a later `put` MAY retry; `ArtifactRefusalError.refusal` SHALL be the original
`OSError`.

When replacing an existing target, its mode SHALL be applied to the staged file before
publication; on POSIX, a new target retains the staging mode subject to umask. On Windows, an
existing target's owner and DACL SHALL also be preserved, and a read-only target SHALL be
refused without changing its bytes or leaving a read-only staging file behind. An absent
Windows target receives the parent directory's DACL.

This is a guarantee for one file only: it does not make a bundle atomic. Parent directories may
remain, writers are not coordinated and concurrent target or parent-path races are outside the
guarantee, interruption or cleanup failure may leave staging behind, and no `fsync` or power-loss
durability is provided.
