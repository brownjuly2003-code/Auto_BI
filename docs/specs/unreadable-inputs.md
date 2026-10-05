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
- The requirement covers the contract files alone. The `model.yaml` of a semantic ref, verified
  assets, dashboard specs, recorded results and the inputs of `decision investigate` are read by
  other loaders, which this requirement does not change.
- A contract whose issue is printed with a character the output stream cannot encode still ends
  `decision validate` in a traceback while the report is printed: reproduced with a lone
  surrogate, written as an escape in a quoted `id`, on a UTF-8 stream.
- A value PyYAML builds and the schema refuses is `contract.invalid` (validated paths) or
  `gap.contract_invalid` with `metric contract does not validate` (semantic ref), as before; an
  unquoted date where the schema wants text is such a value.
