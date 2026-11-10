# Capability: A decision command says why it stopped in one line

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

A decision command that cannot answer prints why on standard error, after `error: `, and exits 2
(4 for a bundle whose manifest digest is not the one pinned). The line quotes what it refuses: a
path found under a directory the command was given, a key of a document, what a validator, an
engine or a store said. Until now `decision investigate` alone spelled a character that is not
printable by its escape. `decision verify`, `decision capture` and `decision evidence` printed
the message as it was. A line break in a quoted name split the error into lines; a pipeline
reading the line after `error: ` read the first half, and the rest came as a second message. A
control a terminal acts on reached the terminal: an escape that clears the screen, a carriage
return that writes over the line. Reproduced on HEAD `880e452`: a validated path with a line
break that is not there ended `decision verify` in two lines; a product file or a `model.yaml`
with a key spelled `"bad\ekey"` printed the escape character itself.

## Requirement: the line after `error: ` holds every character it quotes, printable

When `decision verify`, `decision capture`, `decision evidence` or `decision investigate` stops
with a usage error, it SHALL print `error: `, the message and one line break on standard error,
and nothing on standard output, with the exit code it has always had. Every character of the
message that is not printable — as Python's `str.isprintable` says, which is false for a
character of Unicode's categories Other and Separator other than the ASCII space — SHALL be
spelled as Python spells it inside a string literal: `\n`, `\t`, `\x1b`, `\xa0`, `\u2028`,
`\u200b`, `\udc80`. Every other character SHALL be printed as it is: a letter of any script, a
backslash — one, not two — and a quote. This holds for every message these commands stop with:
a refusal of the command itself (`VerifyError`), what a key or a signature refused
(`EvidenceSignatureError`), what the store refused (`EvidenceReadError`), a manifest digest that
is not the one pinned (`EvidenceDigestError`, exit 4), an artifact that is not text and one the
output stream cannot encode, and a refusal of an offline investigation, which already spelled it
so.

`decision runs` SHALL say why it cannot list the runs as before — the message with no `error: `
ahead of it — and spell its characters the same way.

A printable character the stream has no form for SHALL be spelled by its backslash escape, as
before ([output a stream cannot encode](unencodable-output.md)): `мера` on a stream that encodes
ASCII is `\u043c\u0435\u0440\u0430`.

The message the command is refused with is unchanged but for the paths of the next
requirement: an application calling `verify`, `capture_result` or `locate_evidence` gets the
text the refusal was built of, and a key of a document keeps its escape character there. What
the line spells is what is printed, never kept: no report, check or bundle changes, so
`VERIFIER_VERSION` stays 114, `CHECK_VERSION` 77 and `NATIVE_SOURCE_VERSION` 3.

### Scenario: a key with an escape character

- **GIVEN** the product file of the run, or the `model.yaml` of its candidate ref, with one more
  key `"bad\ekey": 1`
- **WHEN** `decision verify` runs
- **THEN** it exits 2 with nothing on standard output, and standard error is one line:
  `error: no decision product contract found in the given paths; 1 decision product(s) failed
  the schema: '<id>' in <the file> (bad\x1bkey: Extra inputs are not permitted)`, or
  `error: <model.yaml> is not a valid semantic model: bad\x1bkey: Extra inputs are not
  permitted`, with `\x1b` as its four characters
- **AND** the refusal `verify` raises keeps the escape character itself
- **AND** a verified asset with that key ends `decision capture` the same way:
  `error: <the file> is not a valid verified asset: bad\x1bkey: …`, exit 2, no output written

### Scenario: every line a decision command stops with

- **GIVEN** a message that holds a line break, an escape that clears the screen, a line
  separator, a zero-width space, a lone surrogate, a backslash, a Cyrillic word, a tab and a
  no-break space
- **WHEN** `decision verify` is refused with it, or a key or a signature is; `decision capture`
  is; `decision evidence` is refused with it by the run it locates, by the store, by a signature
  or by the digest pinned; or its artifact, named so, is not text or cannot be printed
- **THEN** standard error is one line, `error: ` and the message with each of those characters
  but the backslash and the letters spelled by its escape; the exit code is 2, and 4 for the
  digest
- **AND** `decision runs` says it the same way, without `error: `, exit 2
- **AND** `decision investigate` says it as it did

## Requirement: the paths that could not be read are spelled as the products refused

When `decision verify` finds no product to run, the paths it could not read follow the products
the schema refused ([files a command cannot read](unreadable-inputs.md)): `; <n> path(s) could
not be read: <path> (<message>)`. Each `<path>` SHALL be spelled as a diagnostic line spells a
path (`filesystem.display_path`): as it is when every character of it is printable, quoted as
Python quotes a string otherwise — the way the file of a product its schema refused is spelled
([schema-refused products](schema-refused-products.md)). The message, the count, the order, the
first three and ` (+<k> more)` SHALL stay as they were for the issues in the report. Since
verifier 121, the report names paths by their places and names below, and a path reached again
has one issue ([validated path names](validated-path-names.md)); before verifier 121, an
unreadable path could be counted once per validated path that reached it.

### Scenario: a validated path with a line break

- **GIVEN** the run, without its product file, and one more validated path, a file name with a
  line break that is not there
- **WHEN** `decision verify` runs, with or without `--product`
- **THEN** the refusal ends `; 1 path(s) could not be read: $2 (path does not exist)`: the path is
  named by its place, and none of its text is on the line
  ([validated path names](validated-path-names.md), verifier 121; under verifiers 118 to 120 the path
  quoted, its line break the two characters `\n`, its names joined by `/`
  ([validation report paths](validation-report-paths.md)); before verifier 118, on Windows, each backslash
  of the path doubled, as Python spells the path)
- **AND** the command exits 2 with that one line after `error: ` and no evidence directory
- **AND** a file below a validated path whose name holds a line separator is named by its place
  and that name, quoted, the separator spelled by its escape
  ([validated path names](validated-path-names.md))

### Scenario: printable names read as before

- **GIVEN** four issues of paths that could not be read, named `$1/plain.yaml`,
  `$1/line<line break>break.yaml`, `$1/tab<tab>dir` and `$1/<escape>[2J.yaml`
- **WHEN** the refusal names them
- **THEN** the first is as it is, the next two are quoted, and the fourth is counted in
  ` (+1 more)`

## Requirement: argparse's own refusal of a decision command is one line too

argparse quoted what it refused as it was. Reproduced on HEAD `0e8db1f`: `decision runs <root>`
with one more argument, `x`, a line break and `y`, ended in `auto_bi: error: unrecognized
arguments: x` and a second line `y`; `decision verify <root> --d=x`, a line break and `y`, in
`ambiguous option: --d=x` and a second line `y could match --dashboards, --dwh`. A right-to-left
override and a lone surrogate were printed as they were too. On CPython 3.12.0 and 3.12.6
`decision runs <root> --=x`, a line break and `y`, ended in `auto_bi: error: ambiguous option:
--=x` and a second line `y could match --help, --version`: those interpreters refuse the option
in the parser of `auto_bi`, ahead of the parser of `runs`, which 3.12.7 and later leave it to.

When argparse refuses the command line of `decision` or of one of its commands, the line after
`error: ` SHALL spell each character that is not printable as the first requirement spells it:
an argument the command does not take, written after the command, between `decision` and the
command or ahead of `decision`, quoted after `unrecognized arguments: `, and an option argparse
cannot tell, quoted after `ambiguous option: `. Everything else SHALL be as argparse prints it:
the usage lines above the line — those of `auto_bi` for an argument no parser takes and for an
option the parser of `auto_bi` cannot tell, those of the command for an option it cannot tell —,
the prefix `auto_bi: error: ` or `auto_bi decision <command>: error: `,
exit 2, nothing on standard output, and every printable character, a backslash and a letter of
any script among them. A choice argparse refuses — a command `decision` does not have, a
`--dwh` it does not know — SHALL stay quoted as Python quotes a string, its backslash doubled,
as before.
A printable character the stream has no form for SHALL be spelled by its backslash escape, as
before ([output a stream cannot encode](unencodable-output.md), T-72).

The parser of `decision` and the parser of each of its commands spell so, and so does the parser
of `auto_bi` on a command line whose first argument that does not begin with `-` is `decision`:
argparse hands no other command line to `decision`, for the options of `auto_bi` take no value.
On any other command line the parser of `auto_bi`, which the v1 runtime shares, quotes what it
refuses as it is. Nothing is kept: no report, check or bundle changes, and no version with them.

### Scenario: an argument a decision command does not take

- **GIVEN** `decision runs <root>` and one more argument that holds a line break, an escape that
  clears the screen, a line separator, a zero-width space, a right-to-left override, a lone
  surrogate, a backslash, a Cyrillic word, a tab and a no-break space; or that argument, with no
  space in it, as an option after `runs`, between `decision` and `runs` or ahead of `decision`
- **WHEN** argparse refuses it
- **THEN** standard error is the usage lines of `auto_bi` and one line, `auto_bi: error:
  unrecognized arguments: ` and the argument, each of those characters but the backslash and the
  letters spelled by its escape
- **AND** the exit code is 2 and standard output is empty

### Scenario: an option a decision command cannot tell

- **GIVEN** `decision verify --d=<that text>`, or `decision capture --c=<that text>`, with no
  space in the text
- **WHEN** argparse refuses the option, which could be `--dashboards` or `--dwh`, or
  `--candidate` or `--captured-at`
- **THEN** standard error is the usage lines of the command and one line, `auto_bi decision
  verify: error: ambiguous option: --d=<the text, spelled> could match --dashboards, --dwh`,
  exit 2

### Scenario: an option `auto_bi` may refuse ahead of the command

- **GIVEN** `decision runs <root> --=<that text>`, with no space in the text
- **WHEN** argparse refuses the option, which could be `--help` or `--version` of `auto_bi`, and
  `--help` or `--json` of `runs`
- **THEN** standard error is the usage lines of `auto_bi` and one line, `auto_bi: error:
  ambiguous option: --=<the text, spelled> could match --help, --version`, where the interpreter
  refuses it in the parser of `auto_bi`, as CPython 3.12.0 and 3.12.6 do; or the usage lines of
  `runs` and `auto_bi decision runs: error: ambiguous option: --=<the text, spelled> could match
  --help, --json`, where it refuses it in the parser of `runs`, as 3.12.7, 3.12.13, 3.13.13 and
  3.14.6 do; exit 2

### Scenario: a printable argument is refused as before

- **GIVEN** `мера` after `decision runs <root>`, `--мера` between `decision` and `runs` or ahead
  of `decision`, or `decision verify --d=мера`
- **WHEN** argparse refuses it
- **THEN** standard error is what argparse printed before: the same usage lines, and the line
  with `мера` as it is

### Scenario: a command line of the v1 runtime

- **GIVEN** `gaps` and the argument of the first scenario with no space in it, as an option after
  `gaps` or ahead of it, or `decision` and that option after `gaps`
- **WHEN** argparse refuses it
- **THEN** standard error is the usage lines of `auto_bi` and `auto_bi: error: unrecognized
  arguments: ` with the option, or `decision` and the option, as they are, as before

### Scenario: a choice argparse refuses

- **GIVEN** `decision <that text>`
- **WHEN** argparse refuses the command it names
- **THEN** the line is `auto_bi decision: error: argument decision_command: invalid choice: `
  and the text as Python quotes a string, in quotes and its backslash doubled, as before

### Scenario: a stream that encodes ASCII

- **GIVEN** a standard error that encodes ASCII and refuses a character it cannot encode
- **WHEN** argparse refuses the argument of the first scenario
- **THEN** the line holds what is spelled as it is, and the Cyrillic word by the backslash
  escapes of its letters, `\u043c\u0435\u0301\u0440\u0430`

### Scenario: a byte a POSIX command line cannot decode

- **GIVEN** on POSIX the argument `--x`, the byte 0x80 and `y` after `decision runs <root>`,
  which Python reads with the lone surrogate U+DC80
- **WHEN** the command runs in a process
- **THEN** standard error ends `auto_bi: error: unrecognized arguments: --x\udc80y`, exit 2:
  the line the interpreter's error stream wrote before, its `backslashreplace` spelling the
  surrogate. What changes is the line a stream of an application's own takes in the process.

## Known boundaries

- One line is the line of a refusal. What a command prints when it answers — the report of
  `decision validate`, the checks of `decision verify`, the inspection of `decision evidence`,
  the catalogue of `decision runs` — is spelled the same way where it names what the command
  read, and its `--json` is the document it was ([one-line answers](one-line-answers.md)).
- A backslash is printed as it is, so `\n` on the line is either a line break that was spelled or
  a backslash and an `n` that were there: a Windows path `C:\new` reads as it is where a message
  writes a path as the system does, and by its place, `$1`, where it names a file of the
  validation report ([validated path names](validated-path-names.md)). The parts of a
  message keep the spelling they had: the paths that could not be read and the file of a refused
  product are quoted as Python quotes a string when they hold a character that is not printable,
  as is any other path or value a message already spells with `display_path` or `repr`; the rest
  is not quoted. A quote is a character like another, and a name may hold its own: the line is
  for reading, and the text it was built of cannot always be told back from it.
- `str.isprintable` is the interpreter's, of the Unicode version it was built with. A character
  it holds printable is printed as it is, whatever a terminal makes of it: a letter of any
  script, a combining mark, a letter that looks like another. A mark that turns the direction of
  the text, such as U+202E, is a format character and is spelled `\u202e`.
- The commands outside `decision` — `build`, `introspect`, `gaps` and the rest of the v1 runtime
  — are unchanged, argparse's refusals of them with them. The parser of `auto_bi` tells a command
  line of `decision` by its first argument that does not begin with `-`; an argument ahead of it
  that argparse takes for a positional though it begins with `-` — one that holds a space and
  names no option of `auto_bi`, in full or in part; a negative number; `-` itself — is refused as
  a command `auto_bi` does not have, quoted as Python quotes a string, whichever way the line is
  read.
- A refusal by argparse is one line below its usage lines, not one line of standard error: the
  usage lines are argparse's, as many as the width of the terminal makes them. A program that
  reads standard error for the refusal reads its last line.
