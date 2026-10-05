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
holds no such issue both SHALL read as before. `decision validate` prints its report above its
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
  That one is not covered here (see the boundaries).

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

- **GIVEN** the inputs, and the result with a lone surrogate as its `unit`
- **WHEN** the investigation is written
- **THEN** it is refused with `<the result> is not a valid CanonicalMetricResult: unit: Value
  error, must be text without a lone surrogate`

### Scenario: a row that names a dimension with a lone surrogate is refused by the schema

- **GIVEN** the inputs, and the first row of the result naming its dimension with a lone
  surrogate
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
- **AND** the same holds for a result with a lone surrogate as its `unit` or as a name of a
  dimension of its first row: one line that begins `error: <the result> is not a valid
  CanonicalMetricResult: ` and ends `without a lone surrogate`

### Scenario: inputs that are read give the artifacts they gave

- **GIVEN** the inputs
- **WHEN** `decision investigate` runs
- **THEN** it exits 0, prints nothing on standard error, and on standard output four lines: the
  output path after `wrote`, the content address of the plan after `plan`,
  `sha256:cfbedd5368d2edeba94f2a17c19a4eebc4079b5bd048c46c93825ff41b696fcb`, that of the brief
  after `brief`, `sha256:e07680ef35e98144a7ca1ffcea738fdfb3552e911ddeaa166a1914e79908f4b4`, and
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
  verify` and `decision capture`, the fifth the inputs of `decision investigate`. The loaders of
  the v1 runtime read YAML by themselves and are not changed.
- The fifth requirement gives the refusal of a schema in one line for `decision investigate`
  alone. `decision verify` and `decision capture` still print a model, a dashboard spec, a
  verified asset or recorded results their schema refuses as pydantic lists it, with the values
  and the links. Reproduced with the commands as processes, 42 runs over those five kinds of
  file: 33 print 3 to 22 lines on standard error.
- The reasons of the fifth requirement are pydantic's sentences, of the version installed —
  2.13.4 where the scenarios were run; another version may word them differently. A place that
  is a key holding a lone surrogate is given by pydantic in replacement characters.
- One line means no line break: every run of whitespace in a refusal is one space. Any other
  character of a key, or of a value a validator quotes, is printed as it is — an escape
  character among them.
- `decision investigate` writes its output directory file by file. Reproduced with the third
  write refused, `No space left on device`: the run answers `cannot write output directory
  <path>: ` and what the system said, the directory is left holding `plan.json` and
  `results.json`, and the run after it is refused with `output directory already exists: <path>`.
- What `decision investigate` prints after it wrote goes to the standard output stream, which
  refuses a character it cannot encode. Reproduced with an output path in Cyrillic and
  `PYTHONIOENCODING=ascii`: the command ends in a traceback and exit code 1 after the four
  artifacts are written.
- The fourth requirement covers `decision validate`, `decision verify` and `decision capture`.
  `decision evidence` and the filesystem store still look through `pathlib`. Reproduced with the
  command as a process over a written bundle, 7 runs under each interpreter, with the evidence
  directory, the run directory or a directory above them of mode 000, 0o300 or 0o400: `decision
  evidence` ends in a traceback in 6 of them under Python 3.13.13 and in 3 under 3.14.6, where 3
  more answer `no evidence bundle under <path>: no run directory holds manifest.json` or
  `evidence path not found: <path>` for a bundle that is there. In the seventh a directory of
  the bundle that cannot be read is reported as `evidence.artifact_missing`, `… is not in the
  store`.
- A refusal is told from "not there" by what the operating system answers. The answers of
  Windows and a directory whose entries are not told apart are given to the code by the tests:
  no run was made on Windows or on a filesystem that withholds the kind of an entry. Directories
  whose permission bits are taken away were run on macOS, as a user the bits keep out; a process
  they do not keep out — root — is refused nothing.
- A directory that cannot be listed stops the report or the run whatever it holds: the command
  cannot know that it holds no contract and no asset. A validated path with a directory in it
  that its reader may not list blocks until the directory is listed or left out of the paths.
- Under a validated path a file is chosen by its name alone, as before, so a directory named
  like a YAML file is a contract that cannot be read. In a ref and under the assets path such a
  directory is passed over, as before.
- The order in which a walk gives paths is the filesystem's. Every reader sorts what it reads,
  and the refusals under one validated path are sorted by their paths.
- The dashboards directory is never listed: a spec is looked for by its name. A dashboards
  directory that is searched and not listed therefore reads as any other.
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
