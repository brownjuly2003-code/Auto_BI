# Capability: An answer on a stream that encodes less

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

A decision command prints what it found: the path of a file, an id, the detail a check wrote, a
caveat, a section sign of the architecture. The stream it prints to encodes what its locale or
its caller says — UTF-8 on a terminal, cp1252 or cp1251 where the output of a Windows runner is
redirected, ASCII under `PYTHONIOENCODING=ascii` — and refuses a character it has no form for
(`UnicodeEncodeError`). No encoding has a form for a lone surrogate, which is what a byte of a
command line that is no UTF-8 is decoded to: a stream of UTF-8 refuses a path that holds one.

The commands printed with nothing between them and the stream, so a character of the answer
could end them: after the verdict was reached, after the bundle was sealed or the files were
written, with a traceback and exit code 1 — the code the architecture keeps for an exception
nothing handles, because a crash must not read as a verdict (`DECISIONOPS_ARCHITECTURE.md`
§12.1).

Reproduced at HEAD `1e744a4`, 2026-10-05, Python 3.13.13 on macOS. The commands ran as
processes whose streams encode ASCII, Latin-1, cp1252, cp1251 or UTF-8 (`PYTHONIOENCODING`),
over a fixture under a directory named in Cyrillic; `decision capture`, which needs an engine,
ran in the process on strict streams of the same encodings. 30 runs for each encoding, 150 in
all, and 36 of them ended in `UnicodeEncodeError`: 14 under ASCII, 12 under Latin-1, 10 under
cp1252, none under cp1251 or UTF-8.

- `decision validate` printed a clean report and ended in the exception once an issue named a
  file (3 runs), and so did its `--json` (3): no document was printed.
- `decision verify` of a run that passes ended in it under ASCII while its checks were printed,
  at a detail that holds a section sign, and so did its `--json` (2 runs). The bundle was
  written by then.
- `decision investigate` ended in it after its four artifacts were written (3 runs), `decision
  capture` after the result file was written (3 runs): the line that failed is `wrote <path>`.
- `decision evidence --artifact` ended in it for 20 of the 45 runs of those three encodings: 7
  of the 15 artifacts of the bundle hold a character ASCII and Latin-1 have no form for — the
  two snapshots, the three artifacts of the dashboard, `check-bi.contract` and the summary —
  and 6 of them one cp1252 has none for.
- `auto_bi --help` ended in it under ASCII and Latin-1 (2 runs): a help text of the program
  holds a dash. That printing is argparse's own (see the boundaries).

Eight more runs were made in the process on streams of strict UTF-8, with a path that holds a
lone surrogate — U+DCFF, which a byte of a command line is decoded to, or U+D800. `decision
validate` and its `--json` ended in `UnicodeEncodeError` where the report is printed, in all 4.

## Requirement: what a decision command prints does not end it

What `decision validate`, `decision verify`, `decision capture`, `decision evidence` and
`decision investigate` print, to standard output and to standard error, SHALL be printed on any
stream: a character the stream has no form for SHALL NOT end the command. The exit code SHALL
be the one the command ends with on a stream that encodes every character, and what the command
wrote — a bundle, a result file, the artifacts of an investigation — SHALL be the same.

A line SHALL be offered to the stream as it is. When the stream refuses it
(`UnicodeEncodeError`), the line SHALL be printed with each character the stream cannot encode
as its backslash escape and every other character as it is: `\xNN` for a character up to
U+00FF, `\uNNNN` up to U+FFFF and `\UNNNNNNNN` beyond, in lowercase digits — the spelling
the interpreter uses on its own error stream. The encoding is the one the stream names; a
stream that refuses and names none is printed to in ASCII. A stream that takes the line gets it
as it is, whatever it names. A line goes to the stream it is meant for — standard output, or
the stream that is named — and is printed once, ending in one line break.

A JSON answer (`--json` of `decision validate`, `decision verify` and `decision evidence`)
SHALL be the same document on any stream. It is offered to the stream as it is — the
characters outside ASCII as themselves, indented by two — and, when the stream refuses it,
printed whole in ASCII with the escapes of JSON: `\uNNNN`, and a character outside the basic
plane as two of them. A backslash escape of a line is not one JSON reads (`\xNN`,
`\UNNNNNNNN`), so a document SHALL NOT be printed with those.

The payload of an artifact (`decision evidence --artifact`) SHALL be printed as it is or not at
all. A payload that is text and holds a character the output stream cannot encode is refused:
exit code 2, nothing on standard output, and on standard error the one line `error: <artifact
id> holds text the output stream cannot encode (<encoding>); it is stored at <path>`.
`<encoding>` is what the output stream names, `an unnamed encoding` when it names none, and
`<path>` is where the bundle keeps the payload. The payload is the evidence: with an escape in
it, it is other text, and nothing on the stream would say so.

Every line the five commands print SHALL go through the one function (`cli._say`, and
`cli._say_json` for a JSON answer) — the lines that are ASCII in every run too: such a line
names a path or quotes a detail after the next change of it. The one `print` left in the five
is the payload of an artifact.

Nothing that is sealed changes, so `VERIFIER_VERSION` and `CHECK_VERSION` SHALL NOT be bumped:
on a stream that encodes every character of an answer, the answer is printed as before.

### Scenario: a line is printed with an escape for what the stream cannot encode

- **GIVEN** a stream of ASCII, Latin-1, cp1252, cp1251 or UTF-8, and a line that holds a
  section sign, a Cyrillic letter, a dash, an arrow, a character outside the basic plane or a
  lone surrogate
- **WHEN** the line is printed
- **THEN** the stream holds each character it cannot encode as `\xa7`, `\u0434`,
  `\u2014`, `\u2192`, `\U0001f600`, `\ud800` or `\udcff`, every character it
  does encode as it is, and one line break after the line
- **AND** a backslash the line holds itself stays one, and an empty line is a line break

### Scenario: a line goes to the stream it is meant for

- **GIVEN** standard output and standard error that encode ASCII
- **WHEN** a line with a Cyrillic letter is printed with no stream named, and one with a stream
  named
- **THEN** the first is on standard output and the second on the stream that was named, each
  with the escape, and a line the named stream takes as it is goes to that stream too

### Scenario: a stream that takes every character gets the line as it is

- **GIVEN** a stream that names no encoding and takes any text
- **WHEN** a line with a Cyrillic letter, an arrow, a section sign and a lone surrogate is
  printed to it
- **THEN** the stream holds the line as it is: it was asked before anything was escaped

### Scenario: a stream that refuses and names no encoding is printed to in ASCII

- **GIVEN** a stream that takes ASCII alone and has no `encoding`, or one that is `None`
- **WHEN** a line with a Cyrillic letter and a section sign is printed to it
- **THEN** the stream holds the line with `\u0434` and `\xa7` in it

### Scenario: a JSON document is printed as it is where the stream encodes it

- **GIVEN** a document with Cyrillic, an arrow, a section sign and a character outside the
  basic plane
- **WHEN** it is printed on a stream of UTF-8, or the part of it Latin-1 encodes on a stream of
  Latin-1
- **THEN** the stream holds it with those characters as themselves, indented by two

### Scenario: a JSON document is printed in ASCII where the stream refuses it

- **GIVEN** the same document and a stream of ASCII, Latin-1, cp1252 or cp1251
- **WHEN** it is printed
- **THEN** the stream holds it whole in ASCII and in the same lines: every character outside
  ASCII as `\uNNNN` — a letter the stream does encode too — and the character outside the
  basic plane as `\ud83d\ude00`
- **AND** a reader of JSON reads the document that was printed

### Scenario: a JSON document with a lone surrogate is printed on a stream of UTF-8

- **GIVEN** a document one value of which holds U+D800 or U+DCFF
- **WHEN** it is printed on a stream of strict UTF-8
- **THEN** the stream holds the surrogate as `\ud800` or `\udcff`, and a reader of JSON
  reads the document that was printed

### Scenario: each command answers on a stream that encodes less

- **GIVEN** the fixture under a directory named in Cyrillic and an arrow, and one of: `decision
  validate` with a path that is not there; `decision verify` of a run that passes; `decision
  verify` whose base is not there; `decision capture`; `decision capture` whose candidate is
  not there; `decision investigate`; `decision investigate` whose plan is not there; `decision
  evidence` over a bundle with a caveat in Cyrillic; `decision evidence` over a path that is
  not there
- **WHEN** the command runs on streams of ASCII, Latin-1, cp1252, cp1251 or UTF-8
- **THEN** it exits as it does on streams that take every character — 4, 0, 2, 0, 2, 0, 2, 0
  and 2 — and each stream holds what such a stream holds, with each character it cannot encode
  as its escape
- **AND** the run leaves the same files: the bundle, the result file, the four artifacts, or
  none
- **AND** each of the nine answers holds a character ASCII has no form for: the directory, the
  caveat, or the section sign of a detail

### Scenario: a JSON answer is the same document on a stream that encodes less

- **GIVEN** the same fixture and `decision validate --json` with a path that is not there,
  `decision verify --json` of a run that passes or `decision evidence --json` over the bundle
  with the caveat
- **WHEN** the command runs on streams of ASCII, Latin-1, cp1252, cp1251 or UTF-8
- **THEN** it exits 4, 0 and 0, prints nothing on standard error, and standard output holds
  the document as it is where the stream encodes all of it, and whole in ASCII where it does
  not; either is the same document to a reader of JSON
- **AND** the run leaves the same files

### Scenario: the directory is spelled as each stream can

- **GIVEN** `decision capture` with an output path under the directory `данные→v2`
- **WHEN** it runs on streams of ASCII and of cp1251
- **THEN** the first line starts with `wrote` and names the directory as
  `\u0434\u0430\u043d\u043d\u044b\u0435\u2192v2` under ASCII and as
  `данные\u2192v2` under cp1251; both runs exit 0 and write the same result file

### Scenario: a path with a lone surrogate is named on a stream of UTF-8

- **GIVEN** `decision validate`, with or without `--json`, over the contracts and a path that
  holds U+DCFF or U+D800
- **WHEN** it runs on streams of strict UTF-8
- **THEN** it exits 4 with nothing on standard error; the lines name the path with
  `\udcff` or `\ud800` for the surrogate, and the JSON answer is ASCII and the same
  document

### Scenario: an artifact the stream encodes is printed as it is

- **GIVEN** a bundle with three text artifacts: one in ASCII, one with Cyrillic, one with an
  arrow
- **WHEN** `decision evidence --artifact` prints the first on any of the five streams, the
  second on cp1251 or UTF-8, or the third on UTF-8
- **THEN** it exits 0, standard output holds the payload and a line break, and standard error
  is empty

### Scenario: an artifact the stream cannot encode is not printed

- **GIVEN** the same bundle
- **WHEN** `decision evidence --artifact` is asked for the second artifact on ASCII, Latin-1 or
  cp1252, or for the third on any of the four that are not UTF-8
- **THEN** it exits 2 with nothing on standard output and the one line `error: <artifact id>
  holds text the output stream cannot encode (<encoding>); it is stored at
  blobs/sha256/<digest>` on standard error, and the file at that path holds the payload

### Scenario: an artifact is refused by a stream that names no encoding

- **GIVEN** a standard output that takes ASCII alone and names no encoding, and a standard
  error of UTF-8
- **WHEN** `decision evidence --artifact` is asked for the artifact with the arrow
- **THEN** it exits 2, and the line says `(an unnamed encoding)`: the encoding named is the one
  of the stream that refused

### Scenario: no decision command prints past the one function

- **GIVEN** the source of the five commands
- **WHEN** the calls of `print` in them are listed
- **THEN** there is one, in `decision evidence`: the payload of an artifact

### Scenario: a process whose streams encode ASCII exits by the verdict

- **GIVEN** `decision validate` over the contracts and a path that is not there, as a process
  with `PYTHONIOENCODING=ascii`
- **WHEN** it ends
- **THEN** its exit code is 4, standard error is empty and standard output holds the lines of
  the report with the escapes

### Scenario: a process whose streams encode ASCII prints its JSON document

- **GIVEN** `decision verify --json` of a run that passes, as a process with
  `PYTHONIOENCODING=ascii`
- **WHEN** it ends
- **THEN** its exit code is 0, standard error is empty and standard output is ASCII that reads
  as the document a stream of UTF-8 holds, whose details hold a section sign

## Requirement: CLI help survives an unencodable character (T-71)

The root and nested CLI parsers SHALL print help to the requested stream (stdout by
default), preserving the formatted text and its existing line breaks. The text SHALL
first be offered unchanged. On `UnicodeEncodeError`, characters the stream cannot
encode SHALL use `backslashreplace` in its declared encoding, or ASCII if none is named.
Normal help completes with exit 0 and empty stderr. A stream accepting the text receives
the original text, including under UTF-8 and cp1252.

The parser SHALL NOT reconfigure or replace process streams. This behavior is confined
to `print_help`; argument errors, usage, version, command handlers, JSON and artifact
payloads retain their existing behavior. Non-encoding help write errors propagate;
unlike the default argparse help printer, this printer does not suppress `OSError` or
`BrokenPipeError`. Evidence versions remain unchanged.

### Scenario: root or nested help on a narrow output stream

- **GIVEN** strict ASCII or Latin-1 stdout
- **WHEN** `auto_bi --help` or `auto_bi eval --help` prints a dash
- **THEN** the dash is `\u2014`, help is printed once with no extra line breaks,
  stderr is empty and exit is 0
- **AND** ASCII-only `decision verify --help` prints its original text

### Scenario: a caller owns the output stream

- **GIVEN** a UTF-8, cp1252, StringIO or unnamed ASCII-only stream
- **WHEN** the parser prints help
- **THEN** supported characters stay unchanged, unsupported characters are escaped,
  and the stream identity and encoding/error settings remain unchanged
- **AND** a non-encoding write failure remains an exception, not a successful help exit

## Known boundaries

- Argument errors, usage and version retain argparse's own printing. Normal interpreter
  stderr escapes unsupported characters; a caller-supplied strict stderr may still refuse
  them. Help uses the T-71 boundary above.
- The v1 commands print as before.
- An escape is not told from the same characters typed. A line that holds a backslash is
  printed with it as it is, so a printed line cannot be read back into what was meant; a JSON
  answer can.
- On a stream that refuses a document, the whole of it is in ASCII: the letters that stream
  does encode too.
- The text of an artifact is printed as text, with a line break after it, as before: where the
  stream encodes all of it, the bytes on the stream are those of its encoding, not those the
  bundle keeps. The bytes are in the file the refusal names.
- A stream that fails for another reason than a character is not covered: a reader that has
  closed the pipe, a disk that is full. Reproduced only this far: a reader that takes one line
  of `decision verify` and closes — the 33 lines of the answer are in the pipe by then, and the
  command exits 0 and says nothing.
- A path whose name holds a lone surrogate outside U+DC80 to U+DCFF cannot be handed in on a
  POSIX command line. In the process, `decision investigate` over a plan path that holds U+D800
  ends in `UnicodeEncodeError` where the plan is opened, at HEAD and with this requirement in
  place: the filesystem has no form for the name, and that is no `OSError`. `decision validate`
  reads such a path as one that is not there.
- T-60's original probes ran on macOS. T-71 tests help on Windows/Python 3.13.7 with
  explicitly encoded strict streams and real ASCII/Latin-1 subprocesses; automatic
  selection of the Windows console code page was not tested.
