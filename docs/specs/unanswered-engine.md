# Capability: An engine a run was to ask and that did not answer

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

`decision verify --dwh <engine>` asks a running engine the questions of §8.2 step 7: who the
session is and what it may write, which marts the product reads and how fresh they are, what a
metric's reference query is planned to read and, where a metric declares a runtime ceiling, what
its exact query costs. The questions go through a probe the command builds from the configured
settings and hands to the use case (`EnvironmentProbe`). What the engine refuses — "not for
you", "no such object" — is an answer: a fact of the environment, which the gate grades.

What the engine did not answer was not handled. The probe connected when the command built it,
before the use case was called, and a connection refused, a session the engine closed or an
error the engine answered a question with went up as the driver raised it: exit code 1 and the
driver's traceback — 23 or 24 lines of it from psycopg, 139 to 154 from clickhouse_connect,
whose own warning `Unexpected Http Driver Exception` was printed above it. A run that was to be
refused for its `--created-at` or for a results file that is not there was not refused: the
session was opened first, and the run ended the same way. `decision capture`, which connects
only when it runs the reviewed query, answered a ClickHouse engine it did not reach in two
lines, the first of them that warning.

## Requirement: building the probe connects to nothing

The probe the command hands to the use case SHALL open no session when it is built. Its first
session SHALL be opened when the use case asks its questions, and the use case SHALL ask them
last: after the run's stated facts were checked and after every input the run reads — the
contracts, both semantic refs, the dashboards, the assets and the results — was loaded. A run
refused before the questions SHALL have opened no session with the engine.

A run the engine answers SHALL open one session for the questions of the catalog. The session
of the exact queries a runtime ceiling is measured by is opened as before: when the first of
them is run, and only for a metric that declares such a ceiling.

### Scenario: building the probe opens no session

- **GIVEN** settings that name a port nobody listens on, for each engine `--dwh` takes
- **WHEN** the probe is built
- **THEN** no session is opened
- **AND** when it is asked, one session is tried and its refusal is raised as
  `EnvironmentProbeError`

### Scenario: a run refused before its questions opens no session

- **GIVEN** `decision verify --dwh <engine>` with a `--created-at` that is no moment, or with a
  `--results` file that is not there
- **WHEN** the command runs
- **THEN** it exits 2 with the one line of that refusal
- **AND** no driver was asked to open a session and no evidence directory was created

### Scenario: a run the engine answers opens one session

- **GIVEN** an engine that answers the questions of the catalog
- **WHEN** `decision verify --dwh <engine>` runs
- **THEN** the run completes with its verdict, writes its bundle and prints nothing on stderr
- **AND** the session of the catalog was built once

## Requirement: an engine that did not answer ends the run as a usage error of one line

What the driver raises while the probe asks — on opening the session, on a question of the
catalog, on a plan, on an exact query and on opening the session of the exact queries — SHALL
end the run as a usage error (`VerifyError`): exit 2, nothing on stdout, no evidence directory,
and the one line

`error: cannot ask <engine> at <host>:<port>/<database>: <class>: <what it said>`

on stderr. `<engine>` is the value of `--dwh`; the target is the configured one; `<class>` is
the name of the class of what the driver raised and `<what it said>` is its text with each run
of whitespace — a line break, a tab, a line separator — as one space. A driver that said nothing
is named by its class alone: `…: OperationalError`.

What the driver raises is, of clickhouse_connect, a `ClickHouseError` or a `StreamFailureError`
— a result the engine stopped sending midway, which the driver raises outside that hierarchy —
and of psycopg, through which `postgres`, `greenplum` and `greengage` are asked, a
`psycopg.Error`. A refusal SHALL NOT end the run, although the driver raises it as one of these:
the probe answers it as before, as a fact. What is not the driver's SHALL be raised as it is.

The probe SHALL report the failure as `EnvironmentProbeError(engine, target, error)`, the error
of the `EnvironmentProbe` port, and the use case SHALL turn it into the `VerifyError` above. A
probe the command did not build — a caller that holds its own client, a test — ends the run the
same way by raising it.

### Scenario: an engine that is not reached

- **GIVEN** settings that name a port of the loopback address nobody listens on
- **WHEN** `decision verify --dwh <engine>` runs, for each engine `--dwh` takes
- **THEN** it exits 2 with `error: cannot ask <engine> at 127.0.0.1:<port>/dm: OperationalError: `
  and what the driver said, in one line; nothing on stdout; no evidence directory
- **AND** if the host does not refuse the connection before libpq's configured timeout,
  the actual class is `ConnectionTimeout`, a subclass of `psycopg.OperationalError`, with
  the same exit/output contract; tests bound that wait through `PGCONNECT_TIMEOUT=2`
- **AND** psycopg's refusal is one line however libpq words it: `… failed: Connection refused`
  with `Is the server running on that host and accepting TCP/IP connections?` after a line
  break and a tab, or `… failed: could not receive data from server: Connection refused` in
  one line — which of the two depends on when libpq sees the refusal (both seen on macOS over
  the same kind of port)

### Scenario: an engine that falls over midway

- **GIVEN** an engine that answers the first questions of the catalog and then raises a
  `DatabaseError`, a `StreamFailureError`, an `OperationalError` that says nothing, a psycopg
  `OperationalError` of a session lost, a `QueryCanceled`, or a `DataCorrupted` — an error of
  psycopg outside its `OperationalError`
- **WHEN** `decision verify --dwh <engine>` runs
- **THEN** it exits 2 with the one line of that error, named by its class; no evidence
  directory

### Scenario: the session of an exact query is refused

- **GIVEN** a metric that declares a timeout, a catalog that is answered, and settings that name
  a port nobody listens on
- **WHEN** `decision verify --dwh clickhouse` runs
- **THEN** the session of the exact query is tried once, and the run exits 2 with
  `error: cannot ask clickhouse at …: OperationalError: …` in one line

### Scenario: a refusal is a fact

- **GIVEN** an engine that refuses a question of the catalog with the driver's own error —
  ClickHouse `Code: 497 … (ACCESS_DENIED)`, or psycopg `InsufficientPrivilege` (SQLSTATE 42501)
- **WHEN** `decision verify --dwh <engine>` runs
- **THEN** the run completes, writes its bundle and prints nothing on stderr

### Scenario: what is not the driver's is raised as it is

- **GIVEN** an engine whose questions raise a `RuntimeError`
- **WHEN** `decision verify --dwh <engine>` runs
- **THEN** the `RuntimeError` is raised out of the command, and no evidence directory is created

## Requirement: what a driver logs is kept off a command's error stream

`decision verify --dwh clickhouse` and `decision capture --dwh clickhouse` SHALL put a
`logging.NullHandler` on the `clickhouse_connect` logger before the driver is imported — once,
however many times a client is built. No `decision` command configures logging, so a record the
driver logged was printed by `logging.lastResort`, on stderr, above the line the command answers
with: the driver warns of every request it fails before it raises, and of a response it could
not read to the end with the traceback of why. A process that configured logging SHALL still
have the driver's records where its own handlers put them. A command asked through psycopg
leaves the logger as it is.

### Scenario: the commands answer in one line as processes

- **GIVEN** the command as a process of its own, with settings that name a port nobody listens
  on
- **WHEN** `decision verify --dwh clickhouse`, `decision verify --dwh postgres` or `decision
  capture --dwh clickhouse` runs
- **THEN** it exits 2 and its stderr is one line: `error: cannot ask …` for verify, `error:
  cannot capture <asset id>: ClickHouse connection failed: …` for capture

## Requirement: a driver failure ends a capture in one line without a result file

`decision capture` SHALL convert `ClickHouseError` and `StreamFailureError` from
clickhouse_connect, and `psycopg.Error` from the PostgreSQL family, into `ResultCaptureError`
at the adapter boundary. This covers opening the connection, executing the reviewed query,
ClickHouse's empty-result metadata fallback and consuming the result. In particular,
ClickHouse's `result_rows` reads a lazy stream and SHALL remain inside this boundary even
after `client.query` has returned.

The use case SHALL report the error with exit 2, empty stdout and exactly one stderr line:

`error: cannot capture <asset id>: <ClickHouse|PostgreSQL> <connection|query> failed: <class>: <text>`

Each run of whitespace in the driver's text is one space. An empty or whitespace-only text
leaves the class alone, without a trailing colon. `greenplum` and `greengage` use the
PostgreSQL adapter and label. The result file and a missing output parent directory SHALL
not be created, and partial rows SHALL not be published or trigger a retry. Successful
captures retain their exact SQL, metadata, canonical result and attestation behavior.

Only the named driver errors are added to this boundary. A `RuntimeError` still propagates;
the use case's existing `ValueError` handling is unchanged.

### Scenario: the ClickHouse stream fails after a first block

- **GIVEN** the driver has returned query metadata and one block of rows
- **WHEN** materializing `result_rows` raises `StreamFailureError`
- **THEN** capture exits 2 with one `ClickHouse query failed: StreamFailureError: …` line
- **AND** no output file or missing parent directory is created and no query is retried

### Scenario: the connection, query or metadata fallback fails

- **GIVEN** a named driver error during connection, query execution, metadata fallback or
  PostgreSQL fetch, including `DataCorrupted` or `QueryCanceled`
- **WHEN** capture runs
- **THEN** its one-line refusal identifies the operation and exception class
- **AND** line breaks, tabs and Unicode line separators in the message become spaces
- **AND** a blank message is represented by the exception class alone

### Scenario: an unavailable engine is used by a capture process

- **GIVEN** a closed loopback port and each of `clickhouse`, `postgres`, `greenplum`, `greengage`
- **WHEN** `decision capture` is run as a process
- **THEN** it exits 2, stdout is empty and stderr is one line naming `OperationalError`
  (or psycopg's `ConnectionTimeout` when the connection times out)
- **AND** no recorded result is written

## Requirement: settings a DWH command cannot load

`decision verify --dwh` and `decision capture` turn `ValidationError`, `OSError` and
`UnicodeError` raised while loading Settings into a usage error: exit 2, empty stdout,
one stderr line starting `error: cannot load settings:`, no DWH factory or session,
no evidence and no captured result. The shared settings loader and other CLI commands
retain their contracts; offline `decision verify` never loads Settings.

Validation reports `ValidationError: <field> (<type code>); ...`: at most three errors,
then `(+N more)`. Only known top-level Settings field names are printed; other locations
become `<settings>`. Values, validation messages, context, URLs and nested locations are
omitted. I/O and Unicode failures report only their exception class, without the source
message or path. Unexpected loader exceptions and failures outside settings loading are
not reclassified by this handler.

### Scenario: a configured port is not an integer

- **GIVEN** a noninteger port in the environment or `.env`, for any supported engine alias
- **WHEN** either DWH command is run
- **THEN** it refuses the settings before building a DWH factory, without artifacts
- **AND** its one error line names `ch_port (int_parsing)` or `gp_port (int_parsing)`
- **AND** the configured value does not appear, including when the command runs as a process

### Scenario: the dotenv file cannot be read

- **GIVEN** a `.env` file whose read raises `PermissionError` or `UnicodeDecodeError`
- **WHEN** either DWH command is run
- **THEN** it returns the same usage-error contract, naming only the exception class

### Scenario: offline verification with invalid settings

- **GIVEN** invalid settings and valid offline inputs
- **WHEN** `decision verify` runs without `--dwh`
- **THEN** it completes normally without reading Settings

## Known boundaries

- Only what the driver raises is a usage error. A fault of the probe itself — an identifier
  step 7 will not quote (`unsafe identifier`), a guard that refuses the SQL of a plan — still
  ends the command in a traceback with exit code 1.
- Every error of the driver ends the run, including one that might be graded as a ceiling that
  went unmeasured. Read from clickhouse_connect 1.6.0, not reproduced on a live engine: a
  session whose settings may not be changed — a profile with `readonly=1` — has the exact query
  of a runtime ceiling refused by the driver before it is sent, `ProgrammingError: Setting
  max_execution_time is unknown or readonly`. Before this slice that was a traceback; now it is
  the one line of a usage error. `readonly=2`, which the R3 stand ran with, allows the setting.
- The target is printed as it is configured: host, port and database, never the user or the
  password. What the driver said is printed as it said it, in one line: libpq names the host
  and the port it tried, and an authentication refusal names the user.
- Settings validation does not check connectivity or add new value restrictions. Other CLI
  commands retain their existing settings-error handling. `SettingsError` from custom or
  complex settings sources is outside this slice; current Settings fields are scalar.
- The engines of this slice are a closed port of the loopback address, a listener that accepts
  a connection and closes it at once, and fake engines that raise the drivers' own errors. No
  live engine was asked.
