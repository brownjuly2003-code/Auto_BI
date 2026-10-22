# Capability: What a command line states of a run

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

Two things of a run are not read from a file: they are stated by whoever runs the command.
`decision verify` is told the moment the bundle is of (`--created-at`, else the clock) and the
environment the verdict is about (`--environment`). `decision capture` is told the moment of the
capture (`--captured-at`, else the clock) and the environment it ran against (`--environment`,
else the configured `host:port/database`), and attests the configured user of the engine with
them. Both are kept: the moment in the manifest of the bundle and in the attestation of a
recorded result, the environment in the identity of the run and in the attestation.

Neither was looked at before it was used. A `--created-at` the manifest does not take ended
`decision verify` in an unhandled exception — where the age of a recorded result is counted, or
where the manifest is built — after every input had been loaded and every check graded; there
was no report and no bundle. A `--captured-at` an attestation does not take ended `decision
capture` with exit 2 and the four lines pydantic lists a refusal in, and only after the engine
had been connected to and the reviewed query run. An environment that holds a lone surrogate —
what a byte of a command line that is no UTF-8 is decoded to — ended `decision verify` in an
unhandled `CanonicalizationError`, and `decision capture` attested it: the file it wrote spells
the surrogate as an escape, and `decision verify` refuses to read such a file
(`unreadable-inputs.md`, seventh requirement).

## Requirement: a moment or an environment that evidence cannot carry ends the run before it starts

What a request states of the run itself SHALL be checked before anything else is done with the
request: before a path of it is looked at, before a file is read, before an engine is connected
to or asked, and before anything is written. A request that states what evidence cannot carry
SHALL be a usage error (`VerifyError`; the commands exit 2 with `error: <refusal>` as the one
line on stderr and nothing on stdout). The refusal SHALL NOT quote what was stated: it may hold
a line break or a lone surrogate.

A stated moment SHALL be a `Timestamp` — the scalar the manifest of a bundle and the attestation
of a result carry. Any other text, the empty text too, is refused with `<name> must be an RFC
3339 UTC timestamp ending in Z`, where `<name>` is `--created-at` for `decision verify` and
`--captured-at` for `decision capture`. What the request of a run takes for a moment SHALL be
exactly what the manifest takes.

A stated environment, and the user a capture is attested with, SHALL be text without a lone
surrogate. Text with one is refused with `<name> must be text without a lone surrogate`, where
`<name>` is `--environment` for `decision verify`; for `decision capture` it is `--environment,
or the configured host:port/database it defaults to,` for the environment and `the configured
DWH user` for the user. Any other text SHALL be taken as it is given — blanks, a line break, a
tab, a NUL, letters outside ASCII and a character outside the basic plane — and so is the empty
text: an environment that is not stated is not a refusal.

Of several things a request states that are refused, one SHALL be named: the moment ahead of
the environment, the environment ahead of the user.

A result SHALL be attested before its query is run: `result_probe.capture` builds the
attestation first, so a moment it does not take costs no query whoever calls it.

On a command line, a flag given as empty text is a flag that was not given: the command reads
the clock for the moment and the configuration for the environment, as it does without the flag.

No request the commands took is answered another way, so `VERIFIER_VERSION` and `CHECK_VERSION`
SHALL NOT be bumped: a run that wrote a bundle writes the same bundle, and a capture of a moment
and an environment that are taken writes the same file.

### Scenario: a run of a moment that is none is refused by its flag

- **GIVEN** a run that passes, and a `created_at` that is a word, a timestamp with an offset in
  place of `Z`, with no zone, with a blank ahead of it, with a line break behind it, with a
  blank for `T`, of a day or an hour there is none of, with a small `z`, with a lone surrogate,
  of the year 0000, a date alone, blanks or the empty text
- **WHEN** `verify` is called
- **THEN** it raises `VerifyError` with `--created-at must be an RFC 3339 UTC timestamp ending
  in Z`, no engine is asked and no evidence directory is created

### Scenario: a run takes the moments a manifest takes

- **GIVEN** a `created_at` to the second, with a fraction of a second, `0001-01-01T00:00:00Z` or
  `9999-12-31T23:59:59Z`
- **WHEN** `verify` is called
- **THEN** the run completes and the manifest of its bundle carries the moment as it was stated

### Scenario: the moments refused are the ones a manifest refuses

- **GIVEN** each of the moments of the two scenarios above
- **WHEN** it is validated as a `Timestamp` and stated to `verify`
- **THEN** the run takes it exactly when the schema does

### Scenario: a run about an environment that is no text is refused by its flag

- **GIVEN** an `environment_ref` that holds a lone surrogate, the surrogate a byte that is no
  UTF-8 is decoded to, two surrogates side by side, or nothing but a lone surrogate
- **WHEN** `verify` is called
- **THEN** it raises `VerifyError` with `--environment must be text without a lone surrogate`,
  no engine is asked and no evidence directory is created

### Scenario: a run takes an environment that is text as it is given

- **GIVEN** an `environment_ref` of blanks, of two lines, with a blank behind it, with a tab or
  a NUL inside, of letters outside ASCII or with a character outside the basic plane
- **WHEN** `verify` is called
- **THEN** the run completes, and its `run_id` is not the one of the same run about another
  environment: nothing of the text is trimmed or folded
- **AND** a run that states no environment completes

### Scenario: what a run states is refused before anything is read

- **GIVEN** a request whose contract paths, refs, dashboards, assets and results are not there,
  and whose moment — or whose environment — is refused
- **WHEN** `verify` is called
- **THEN** the refusal is the one of the moment — or of the environment — and of no path
- **AND** a run that would pass and is refused loads no contract under its validated paths

### Scenario: the moment of a run is refused ahead of its environment

- **GIVEN** a request whose moment and whose environment are both refused
- **WHEN** `verify` is called
- **THEN** the refusal is the one of `--created-at`

### Scenario: a capture of a moment that is none is refused by its flag

- **GIVEN** a capture that would be written, and a `captured_at` of the first scenario
- **WHEN** `capture_result` is called
- **THEN** it raises `VerifyError` with `--captured-at must be an RFC 3339 UTC timestamp ending
  in Z`; the query factory is not called, no query is run and no file is written

### Scenario: a capture takes the moments an attestation takes

- **GIVEN** a `captured_at` to the second, with a fraction of a second, `0001-01-01T00:00:00Z`
  or `9999-12-31T23:59:59Z`
- **WHEN** `capture_result` is called
- **THEN** the query is run once and the file written attests the moment as it was stated

### Scenario: a capture for an environment or by a user that is no text is refused

- **GIVEN** an `environment_ref` — or an `executed_by` — that holds a lone surrogate
- **WHEN** `capture_result` is called
- **THEN** it raises `VerifyError` with `--environment, or the configured host:port/database it
  defaults to, must be text without a lone surrogate` — or `the configured DWH user must be text
  without a lone surrogate`; the query factory is not called and no file is written

### Scenario: a capture attests an environment and a user that are text as they are given

- **GIVEN** an `environment_ref` and an `executed_by` of blanks, of two lines, with a blank
  behind, with a tab or a NUL inside, of letters outside ASCII, with a character outside the
  basic plane, or the empty text
- **WHEN** `capture_result` is called
- **THEN** the file written attests both as they were given

### Scenario: what a capture states is refused before anything is looked at

- **GIVEN** a request whose output file is taken, whose assets and candidate ref are not there
  and whose engine is none, and whose moment — or environment, or user — is refused
- **WHEN** `capture_result` is called
- **THEN** the refusal is the one of what was stated; the query factory is not called and the
  output file is as it was

### Scenario: what a capture states is refused in one order

- **GIVEN** a request whose moment, environment and user are all refused — or whose environment
  and user are
- **WHEN** `capture_result` is called
- **THEN** the refusal is the one of `--captured-at` — or the one of the environment

### Scenario: a result is attested before its query is run

- **GIVEN** an asset with a reviewed query and a `captured_at` that is a word
- **WHEN** `result_probe.capture` is called
- **THEN** it raises the `ValidationError` of a `ResultAttestation` and the query is not run

### Scenario: the commands exit 2 with one line

- **GIVEN** a `--created-at` that is no moment, or an `--environment` that is no text
- **WHEN** `decision verify` runs
- **THEN** it exits 2 with `error: --created-at must be an RFC 3339 UTC timestamp ending in Z` —
  or `error: --environment must be text without a lone surrogate` — as the one line on stderr,
  prints nothing on stdout and creates no evidence directory
- **AND** `decision capture` with such a `--captured-at` or `--environment` exits 2 with the
  line of its refusal, opens no engine, runs no query and writes no file

### Scenario: what is configured is refused as what is stated

- **GIVEN** a configured environment — or a configured user — that holds a lone surrogate, and
  no `--environment`
- **WHEN** `decision capture` runs
- **THEN** it exits 2 with the line of the environment — or of the user — and writes no file
- **AND** with an `--environment` that is text the capture is written and attests the flag

### Scenario: a flag given as empty text is a flag not given

- **GIVEN** `--captured-at ""`, `--created-at ""` or, for a capture, `--environment ""`
- **WHEN** the command runs
- **THEN** it is not refused: the moment attested and the moment of the manifest are the clock's,
  to the second, and the environment attested is the configured one

## Known boundaries

- A moment is whatever a `Timestamp` is, and the schema of that scalar is not changed: it ends
  in `Z`, holds a `T`, and the rest is what `datetime.fromisoformat` reads with `+00:00` in
  place of the `Z`. That is wider than RFC 3339. Reproduced on the command line of both
  commands: a moment with no seconds (`2026-08-25T09:00Z`), with no separators
  (`20260825T090000Z`), of a week and a day (`2026-W35-2T09:00:00Z`) and with a comma for the
  fraction is taken, attested and sealed as it is stated.
- An environment is compared as text. One with a blank behind it or a line break inside is not
  the environment a result was captured on, which `results.provenance` says as before
  (`provenance.other_environment`, review), and `summary.md` shows it as it was stated.
- Only the lone surrogate is refused of an environment and a user. The schema of a
  `ResultAttestation` is not changed: a recorded result built in the process may still carry
  one, and `result_probe.capture` called by itself attests it.
- The use case settles what its request states. Before it is called, `decision verify` has read
  the signing key it was given. With `--dwh` it has also built the probe of the engine, which
  connected when it was built — a run of a moment that is none opened its session and ended in
  the traceback of the refused connection, exit code 1. Since T-65 the probe connects to nothing
  until the use case asks it, last, and such a run is refused in its one line without a session
  ([an engine that did not answer](unanswered-engine.md)).
- Since T-67, `decision capture` reports `ClickHouseError`, `StreamFailureError` and
  `psycopg.Error` in one line, naming the exception class and folding each run of whitespace
  in its text to one space. It exits 2 without publishing a result or retrying the query
  ([an engine that did not answer](unanswered-engine.md)). This covers connection, query,
  metadata fallback and result consumption; `RuntimeError` still propagates and the existing
  `ValueError` handling is unchanged. Since T-65, the warning clickhouse_connect logs for an
  unavailable engine is kept off stderr. With a moment that is none the capture answers with
  the one line of the moment and opens nothing.
