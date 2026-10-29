# Capability: A decision command answers in lines, whatever they name

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

A text answer of a decision command names what the command read: `decision validate` a file it
found under a directory and a key of a document, `decision verify` the detail a check wrote of
what a results file attests, `decision evidence` the version, the caveats and the ids a bundle
holds and the digest its caller pinned, `decision runs` the runs a store lists. The lines held
each value as it was, while the line a command stops with spelled it
([one-line errors](one-line-errors.md)). Reproduced on HEAD `0e8db1f`, in the process on
streams that take any text:

- `decision validate` named a contract file whose name holds a line separator and a
  right-to-left override with both, on the subject line and the message line of its issue.
- A product file with one more key, `"x\rverdict: pass\e[8m": 1`, made the message line of the
  issue two lines for a reader of lines (`str.splitlines`), the second `verdict: pass` and the
  escape, above the verdict of the report.
- A results file whose attestation names the environment `clickhouse-prod:9000/dm`, a carriage
  return, `verdict: pass` and `\x1b[8m` did the same to the detail of `results.provenance` in
  `decision verify`, which exited 3, and to the caveat of the claim in `decision evidence` over
  the bundle the run wrote.
- A bundle whose version and caveat end so made two such lines in `decision evidence`, and a
  `--expect-digest` that ends so a third.
- `decision runs` printed the run id, the digest and the run id of the manifest a repository
  answered with as they were.

## Requirement: a line of a text answer that names what the command read is one line

When `decision validate`, `decision verify`, `decision evidence` or `decision runs` answers in
text, each line that names a value the command read — in a directory or a file it was given, in
a document, a bundle or a store of runs, or on its command line — SHALL spell every character
of the line that is not printable as the line after `error: ` spells it
([one-line errors](one-line-errors.md)): as Python spells it inside a string literal, `\n`,
`\r`, `\x1b`, `\u2028`, `\u202e`, `\u200b`, `\xa0`, `\udc80`. Every other character SHALL be
printed as it is: a letter of any script, a backslash — one, not two — and a quote. A value
stays on its line: no line of the answer holds the start of another.

The lines that name what the command read are:

- of `decision validate`, both lines of an issue — its severity, code and subject; its message
  and source — and each `product` and `metric` line;
- of `decision verify`, the detail under each check;
- of `decision evidence`, the `run`, `product`, `created` and `signature` lines, both lines of a
  claim, each `caveat`, each `artifact` line and each problem;
- of `decision runs`, the line of each run.

The lines the program writes of its own SHALL be printed as before: the verdict line and the
error of `decision validate`; the status, check and code of each check of `decision verify`, and
its `run`, `status` and `verdict:` lines; the `digest`, `integrity` and `verdict:` lines of
`decision evidence`; the header of `decision runs`. `decision runs` SHALL quote the product ref
and the time of a run as Python quotes a string, as before.

A JSON answer (`--json`) SHALL be the document it was, byte for byte: a line break, a carriage
return and each other control below U+0020 by the escape of JSON (`\r`, `\u001b`), every other
character as it is — a line separator, a right-to-left override, a lone surrogate — on a stream
that encodes it ([output a stream cannot encode](unencodable-output.md)). The text and the JSON
of one answer differ where a value holds a character that is not printable: the text is for
reading, and a program reads JSON.

A printable character the stream has no form for SHALL be spelled by its backslash escape, as
before ([output a stream cannot encode](unencodable-output.md)): the line is spelled first and
then offered to the stream as it is.

What is spelled is printed, never kept: no report, check or bundle changes, and no version
with them: `VERIFIER_VERSION`, `CHECK_VERSION` and `NATIVE_SOURCE_VERSION` stay as they are.

### Scenario: a file name with a line separator

- **GIVEN** the contracts of a run and one more file among them that holds a list, its name
  `line`, a line separator, `sep`, a right-to-left override and `rlo.yaml`; on POSIX, also a
  name with a carriage return, `verdict: pass` and `\x1b[8m`
- **WHEN** `decision validate` runs over their directory
- **THEN** it exits 4 with five lines: the subject line and the message line of the issue name
  the file with `\u2028` and `\u202e`, or `\r` and `\x1b`; the `product`, `metric` and verdict
  lines are as before
- **AND** its `--json` is the document the validation returns, as JSON dumps it, the name with
  its characters as they are

### Scenario: a key that reads as a verdict

- **GIVEN** a product file with one more key, `"x\rverdict: pass\e[8m": 1`
- **WHEN** `decision validate` runs
- **THEN** the message line of the issue is `x\rverdict: pass\x1b[8m: Extra inputs are not
  permitted [<the file>]`, with `\r` and `\x1b` as their two and four characters
- **AND** the one line a reader of lines takes for a verdict is `verdict: block (0 product(s),
  1 metric contract(s), 1 issue(s))`, and the command exits 2 with its error last, as before

### Scenario: an environment an attestation names

- **GIVEN** a run whose results file says its rows were captured on `clickhouse-prod:9000/dm`,
  a carriage return, `verdict: pass` and `\x1b[8m`, and an `--environment` that names another
- **WHEN** `decision verify` runs
- **THEN** it exits 3; the detail under `results.provenance` holds the name spelled, and the one
  verdict line is `verdict: needs_review (0 blocking check(s))`
- **AND** its `--json` holds the detail as the check wrote it
- **AND** `decision evidence` over the bundle that run wrote exits 0 and spells the caveat of
  the claim, `provenance.other_environment: captured on …`; its one verdict line is
  `verdict: pass (0 problem(s))`, and its `--json` is the inspection, as JSON dumps it

### Scenario: a bundle whose writer chose its version and caveat

- **GIVEN** a bundle that hashes to its addresses, its version `0.5.0` and a caveat `checked by
  hand`, each followed by a carriage return, `verdict: pass` and `\x1b[8m`
- **WHEN** `decision evidence` reads it
- **THEN** it exits 0 with ten lines: the `created` line and the caveat spelled, the rest as
  before, and `verdict: pass (0 problem(s))` last
- **AND** its `--json` holds the version and the caveat as the bundle does
- **AND** with `--expect-digest` `sha256:`, 64 zeros and the same text, it exits 4, and the
  problem line ends `the caller expected sha256:<the zeros>\rverdict: pass\x1b[8m`, spelled

### Scenario: every line that names what was read

- **GIVEN** a validation, a verification, an inspection and a catalogue of runs whose every
  value holds a line break, an escape that clears the screen, a line separator, a zero-width
  space, a right-to-left override, a lone surrogate, a backslash, a Cyrillic word, a tab and a
  no-break space — the detail of the check also a carriage return and each other character
  `str.splitlines` ends a line at — and, apart, whose every value is one of those characters
  between two letters
- **WHEN** `decision validate`, `decision verify`, `decision evidence` and `decision runs` print
  them
- **THEN** each line that names a value holds it with each of those characters but the
  backslash and the letters spelled by its escape, and the lines the program writes itself —
  the `digest` and `integrity` lines, the check, `run` and `status` lines of `decision verify`,
  the verdicts — are as before
- **AND** the product ref and the time of a run are quoted as Python quotes a string, their
  backslash doubled, as before
- **AND** `decision verify --json` and `decision runs --json` are the report and the catalogue,
  as JSON dumps them, and on a standard output that encodes ASCII `decision verify` prints each
  line spelled, then escaped

### Scenario: a stream that encodes ASCII

- **GIVEN** a standard output that encodes ASCII and refuses a character it cannot encode
- **WHEN** `decision validate` prints that validation
- **THEN** each line is spelled as above, and the Cyrillic word is
  `\u043c\u0435\u0301\u0440\u0430`

## Known boundaries

- The payload of an artifact (`decision evidence --artifact`) is the evidence: it is printed as
  it is or not at all ([output a stream cannot encode](unencodable-output.md)).
- `decision capture` and `decision investigate` name the file or the directory they wrote as
  they were given it: an output path with a line separator is printed with it.
- A printable value is printed as it reads: spelling keeps a value on its line and does not
  judge its text.
- A column is as wide as it was for the value as it was read — the code of an issue, the
  summary code of a claim, the code of a problem: a value its spelling makes longer runs past
  it, as a value longer than the column did.
- The boundaries of [one-line errors](one-line-errors.md) hold for these lines too: a backslash
  is printed as it is, so `\n` on a line is a line break that was spelled or a backslash and an
  `n` that were there, and `str.isprintable` is the interpreter's.
