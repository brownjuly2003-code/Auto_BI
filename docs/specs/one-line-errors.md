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
first three and ` (+<k> more)` SHALL stay as they were.

### Scenario: a validated path with a line break

- **GIVEN** the run, without its product file, and one more validated path, a file name with a
  line break that is not there
- **WHEN** `decision verify` runs, with or without `--product`
- **THEN** the refusal ends `; 1 path(s) could not be read: '<the path>' (path does not exist)`,
  the path quoted, its line break the two characters `\n`, and on Windows each backslash of the
  path doubled, as Python spells the path
- **AND** the command exits 2 with that one line after `error: ` and no evidence directory

### Scenario: printable paths read as before

- **GIVEN** four paths that could not be read: one printable, one with a line break, one with a
  tab, one with an escape
- **WHEN** the refusal names them
- **THEN** the first is as it is, the next two are quoted, and the fourth is counted in
  ` (+1 more)`

## Known boundaries

- One line is the line of a refusal. What a command prints when it answers — the report of
  `decision validate`, the checks of `decision verify`, the inspection of `decision evidence`,
  the catalogue of `decision runs` — names what it read as it is, and so does `decision validate`'s
  report above its own error.
- A backslash is printed as it is, so `\n` on the line is either a line break that was spelled or
  a backslash and an `n` that were there: a Windows path `C:\new` reads as it is. The parts of a
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
  — are unchanged, and so are argparse's own usage errors.
