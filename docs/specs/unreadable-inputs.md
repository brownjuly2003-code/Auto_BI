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
read exactly as before.

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

A run input that is read and is not what the file must hold SHALL be a usage error that names
the file:

- a `model.yaml` whose document is a list, a text or a mapping the schema refuses — of either
  ref, for both commands — `<file> is not a valid semantic model: ` and what the schema says;
- a file under the assets path that holds no verified asset — a list, a text, nothing or a
  mapping the schema refuses — `<file> is not a valid verified asset: ` and what the schema says,
  for both commands. Two assets of one id are the fault of no one file and stay `<assets path>
  holds an invalid verified asset: duplicate verified asset id: <id>`;
- a dashboard spec or a results file that holds no mapping, and an empty `model.yaml` of the
  candidate, `<file> must contain a mapping`, as before. An empty `model.yaml` of the base is a
  model of nothing and is graded, as before.

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
- **THEN** it is refused with a message that begins `<that file> is not a valid semantic model:
  ` and goes on with what the schema says of a `SemanticModel`; no evidence directory exists

### Scenario: a value the graph of a ref has no digest for is said of the ref

- **GIVEN** the run, and a lone surrogate — the escape `"\ud800"` in a double-quoted scalar — as
  the description of the first table of the `model.yaml`, or as the description of the metric
  contract, of the base ref or of the candidate ref
- **WHEN** `decision verify` runs, and — for the candidate ref — `decision capture`
- **THEN** each is refused with `cannot load semantic ref <that ref>: $.tables[0].description:
  string contains a lone surrogate` for the model, and with `cannot load semantic ref <that
  ref>: $.metrics[0].description: string contains a lone surrogate` for the contract; no evidence
  directory exists, no query is opened and no output is written

### Scenario: a refusal of another schema is said of the ref, not of its model

- **GIVEN** the run, and a lone surrogate as the first dimension of the metric contract of the
  base ref or of the candidate ref
- **WHEN** `decision verify` runs, and — for the candidate ref — `decision capture`
- **THEN** each is refused with a message that begins `cannot load semantic ref <that ref>: `,
  names the schema of a `SemanticGap` and does not say `is not a valid semantic model`; no
  evidence directory exists

### Scenario: an empty model is read as it was

- **GIVEN** the run with an empty `model.yaml` in the base ref; and the run with an empty
  `model.yaml` in the candidate ref
- **WHEN** `decision verify` runs
- **THEN** the first is graded and its verdict is `block`; the second is refused with `<that
  file> must contain a mapping` and no evidence directory exists

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
- **THEN** each is refused with a message that begins `<that file> is not a valid verified
  asset: ` and goes on with what the schema says of a `VerifiedAsset`; no evidence directory
  exists, no query is opened and no output is written

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
id>': <class>` for a write, `cannot read '<path>' of run '<run id>'` for a read, and `cannot
look for '<path>' of run '<run id>': <class>` when whether an artifact is there could not be
established — which is not "no". A path the store refuses itself — a symlink, a path that
leaves the run — is the error it was.

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
  refuses every look at a path with `PermissionError`
- **WHEN** an artifact is put into the first; and the artifact of the second is looked for, read,
  and another one put beside it
- **THEN** the first raises `ArtifactStoreError`, `cannot write 'checks/one.json' of run
  'run-1': ` and the class of the refusal, with the refusal as its cause, and the file holds what
  it held; the second raises `cannot look for 'checks/one.json' of run 'run-1':
  PermissionError` with the refusal as its cause, `cannot read 'checks/one.json' of run
  'run-1'`, and `cannot write 'checks/two.json' of run 'run-1': PermissionError`

### Scenario: a refused look into the evidence directory is a usage error

- **GIVEN** the run, where the filesystem refuses every look at a path under its evidence
  directory with `PermissionError`
- **WHEN** `decision verify` runs
- **THEN** it is refused with a message that begins `cannot write the evidence bundle under <the
  evidence directory>: cannot ` and ends `: PermissionError`; the evidence directory does not
  exist

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
- The first requirement covers the contract files, the second the run inputs of `decision
  verify` and `decision capture`. The inputs of `decision investigate` and the loaders of the v1
  runtime read YAML by themselves and are not changed.
- The second requirement covers reading. Looking is covered inside a semantic ref, among the
  files under the assets path and inside the evidence directory only. Under a directory that may
  not be searched a validated contract file still ends `decision verify` and `decision validate`
  in a traceback, the assets path itself `decision verify` and `decision capture`, a dashboard
  spec or the recorded results `decision verify`, and the output path `decision capture`.
  Reproduced with the command as a process and a directory of mode 000, on Python 3.12.13 and
  3.13.13: both raise `PermissionError` from a look at such a path.
- A directory that cannot be listed is read as an empty one. Reproduced the same way: with the
  assets directory `decision verify` exits 0 with `pass_with_advisory` — no asset is selected and
  nothing says that one could not be; with the metrics directory of the base ref it exits 3, of
  the candidate ref 4, each on a snapshot without the metrics; with the validated directory it
  exits 2 with `no decision product contract found in the given paths`, and so does `decision
  validate`; `decision capture` with the assets directory exits 2 with `no verified asset under
  <path>`.
- A contract the filesystem refuses to open is a finding under the validated paths
  (`contract.unreadable`) and ends the run in a semantic ref: a validation report answers for
  its paths whether or not a run follows, and a snapshot says what bytes it was read from.
- Every file under the assets path is loaded, the assets of other products among them: one file
  there that cannot be read ends the run of every product, as one the schema refuses always did.
- `cannot load semantic ref <ref>: ` is followed by the text of whatever refused: the place of a
  value in the graph, or the message of a schema no file is written against. The value is not
  graded as a gap of the file that held it. The same value under the validated paths is not
  covered at all: reproduced, a lone surrogate as a dimension of a validated metric contract
  ends the use case of `decision verify` in the `ValidationError` of the schema of a gap.
- The manifest is one write and the last one, so a refused write leaves none; nothing makes that
  one write atomic. Reproduced with a manifest cut in half: the repeated run exits 2 with `run
  '<id>' already holds bundle <digest>; refusing to overwrite it with <digest>` and `decision
  evidence` exits 4 with `verdict: block (1 problem(s))`.
- The line of a usage error may quote the file: the message of a value that could not be built,
  the schema's own text for a model or an asset it refuses. It is printed to the standard error
  stream and is never sealed.
- A contract whose issue is printed with a character the output stream cannot encode still ends
  `decision validate` in a traceback while the report is printed: reproduced with a lone
  surrogate, written as an escape in a quoted `id`, on a UTF-8 stream.
- A value PyYAML builds and the schema refuses is `contract.invalid` (validated paths) or
  `gap.contract_invalid` with `metric contract does not validate` (semantic ref), as before; an
  unquoted date where the schema wants text is such a value.
