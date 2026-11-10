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
the probe answers it as before, as a fact, and tells it by the code the error carries
(ClickHouse since slice S8, below). Neither SHALL an answer that the reference query no
longer fits the mart, nor a refusal of the exact query (ClickHouse since slice S7, PostgreSQL
since S7b; both specified below). Other exceptions SHALL be raised as they are,
apart from the local identifier quoting refusal specified below.

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

## Requirement: a local identifier quoting refusal ends verify in one line

When the collector's existing `_ident` rule rejects an event-time column or a component
of the table name, it SHALL raise `UnsafeIdentifierError`, a subclass of `ValueError`,
with the unchanged message `unsafe identifier: <repr of the rejected component>`.
The supported identifier syntax SHALL remain unchanged.

The CLI-built probe SHALL convert only this type, only while calling `collect`, into
`VerifyError`: exit 2, empty stdout, no evidence directory and exactly one stderr line:

`error: cannot build environment query: unsafe identifier: <repr>`

This is a local query-construction refusal, not `EnvironmentProbeError` or an engine
answer to grade. No failing freshness SELECT SHALL be sent. The connection and catalog
questions may already have happened; their order SHALL remain unchanged. An absent
table or PostgreSQL's earlier unreadable-table answer SHALL still be an environment
fact. Direct collector callers continue to receive a `ValueError` subclass. Offline
verification and other command paths SHALL retain their behavior.

### Scenario: the collector cannot quote an identifier

- **GIVEN** a present, readable table and an unquotable event-time column, schema or
  table component, including whitespace, line separators or an empty dotted component
- **WHEN** `decision verify --dwh` runs with any supported engine alias
- **THEN** the command returns the one-line usage error above, without a bundle
- **AND** the rejected freshness SELECT is never passed to the query callable

### Scenario: an unrelated exception has the same text

- **GIVEN** an ordinary `ValueError` or `RuntimeError` with `unsafe identifier` in its text
- **WHEN** the collector raises it, or the connection factory raises any exception
- **THEN** the identifier handler does not reclassify it; existing driver handling remains

## Runtime settings refused by ClickHouse (T-70)

A local `ProgrammingError` with the exact message `Setting <name> is unknown or readonly`
for a setting passed by this runtime command leaves all declared runtime bounds unmeasured.
The runtime command passes `max_execution_time`, `limit=0`, `offset=0`, and `throw` for
all eleven overflow modes listed in `probe-execution-safety.md` with each query it
executes; only `max_bytes_to_read` is added when a byte ceiling is declared. The fact
carries no counters, checked bounds or exceeded bound.
The existing grader emits `cost.not_measured` (review) for each declared runtime ceiling;
catalog facts remain available. The query is not retried without limits.

An unrelated setting, another error class or another message is not this refusal.
An exceeded timeout requires server code 159. An exceeded scanned-byte limit requires
code 307 and a message identifying an exceeded read-byte limit; a row or result-size
limit does not prove scanned-byte excess. A structured driver code takes precedence;
older drivers can supply the code in a `Code:` response line or `server response:` form.

ClickHouse 24.8.14.39 on a throwaway Mac server reproduced the old false timeout evidence
under `readonly=1`. With the correction it leaves the bounds unmeasured; `readonly=2`
still executes successfully. Actual read-byte and timeout refusals were also checked.
This corrects the earlier specification's statement that the local setting refusal was a usage
error: the former text classifier mistook the setting name for an exceeded ceiling.
Verifier 98 / check 73 distinguish the new evidence; historical bundles are unchanged.

## A reference query the mart no longer fits (ClickHouse, S7)

A column the reference query names that was dropped or renamed, or retyped to a type its
functions do not take, is the drift `dwh.physical` reports (`physical.column_absent` block,
`physical.type_drift` review). ClickHouse 24.8.14.39 answers that query, live, with a server
error rather than a plan: `47 UNKNOWN_IDENTIFIER` for a column gone, `43 ILLEGAL_TYPE_OF_ARGUMENT`
for the stand's `Decimal` `revenue` retyped to `Float64` or to `String`. Verifier 129 let that
error end the run as above — exit 2, no report, no bundle — so the drift was never reported.

The code alone does not say the mart drifted: a query of the gate's own, or a model declaring a
type its aggregate does not take, is answered with the same codes. So the probe takes a code as
a fact about the mart only where the catalog it already read accounts for it: a column the
query names that `dwh.physical` grades as gone (`physical.column_absent`) for 47, or as held
as another type (`physical.type_drift`) for 43. Both read one comparison, `column_drift`,
which leaves out a column ClickHouse hides from the role or whose reach it did not answer.
Since verifier 130, for a server code 43 or 47 (a structured driver code first, else the
`Code:` line, as for T-70):

- `EXPLAIN ESTIMATE` of the reference query SHALL leave the plan unpriced when such a column is
  among those the reference query names (as sqlglot reads it; a query it cannot read names
  none) and the table question that lists the plan's metric declares, with the evidence
  `EXPLAIN ESTIMATE answered the reference query with Code <n> <NAME>, taken to mean the query
  no longer fits the mart as the engine has it (<columns> drifted from the model)` naming those
  columns (since verifier 131; verifier 130 said `…<NAME>: the query no longer fits…`); the
  grader emits `cost.not_estimated` (review) as for any unpriced plan. Otherwise — the query's
  columns as declared, a drift of the other kind, a drift only of columns the query does not
  name or another table question declares — the error SHALL end the run as above;
- the exact SELECT of a plan so answered SHALL NOT be sent: its execution facts measure no
  declared runtime bound, with the evidence `the exact SELECT was not run: EXPLAIN ESTIMATE of
  the same query answered Code <n> <NAME>, and a query the mart no longer fits measures
  nothing; no declared runtime bound was measured`, and the grader emits `cost.not_measured`
  (review) for each declared runtime ceiling. A 43 or 47 from an exact SELECT whose plan was
  answered is the gate's defect and SHALL end the run as above;
- `count()` and `max(<event_time>)` SHALL be asked again as `count()` alone, and the reading
  kept, when the drift of the event-time column accounts for the code (gone: 47; a type
  `max()` does not take: 43): the rows are counted and undated, and `freshness.unknown`
  (review) says `<table> has <n> row(s) on <target> and no newest <column> to date them by — a
  NULL max, or a column the engine no longer has`. Otherwise the error SHALL end the run as
  above, unless the role is refused even `count()`, which is `access.table_unreadable` (block).

A server code 497 `ACCESS_DENIED` from the exact SELECT — a column the reference query names
that the role may not read, which `dwh.security` blocks on — SHALL leave every declared runtime
bound unmeasured, with the evidence `ClickHouse refused the exact SELECT with Code 497
ACCESS_DENIED; no declared runtime bound was measured`.

The evidence names the code and its name alone: what the server said beside them names its
version and the URL it was asked at, which a sealed fact must not depend on. Any other code —
`62 SYNTAX_ERROR` of a query of ours, a network error — and an error with no code still end the
run as above; the query is not retried. Check grading is unchanged (check 83).

`tests/test_decision_live_clickhouse.py` asserts each drift case against a ClickHouse of the
pinned `24.8` series that it creates the mart and the gate role in; slice S7 ran it on the Mac
stand (12 scenarios, ClickHouse 24.8.14.39) and added it to the CI `integration` job, whose
first run is still ahead. The catalog cross-check and the cases a catalog does not explain are
asserted offline (`tests/test_environment_probe.py`, `tests/test_runtime_probe.py`).

## The same drift on PostgreSQL (S7b)

PostgreSQL 16.15 answers the same drift, live, in its own states: `42703 undefined_column` for a
column the query names that was dropped or renamed, `42883 undefined_function` for one retyped
to what its functions or operators do not take. `EXPLAIN (FORMAT JSON)` of the reference query
answers so, and so does `max()` of an event-time column dropped (42703) or retyped (42883). A
drift PostgreSQL still plans raises nothing and is graded as before (`physical.type_drift`, and
`freshness.unreadable_event_time` for a `date` that no longer reads as a time). Verifier 130
let the two states end the run as above. `tests/test_decision_live_postgres.py` pins `revenue`
renamed, as `text` (`sum(text)`) and as `double precision` (which `trunc(…, integer)` does not
take), `gross_margin` dropped, `date` dropped and as `jsonb` (`max(jsonb)`), and `revenue` as
`numeric(38,4)`, which still plans. Seen once on 16.15 while the slice was designed, and not
pinned: 42883 for `revenue` as `boolean` or `interval` (no `=` with an integer) and for `date`
as `boolean`; a plan for `revenue` as `bigint` and for `date` as `timestamptz`, `integer` or
`text`.

Since verifier 131 the probe reads them as it reads ClickHouse's codes, by the SQLSTATE the
driver carries (psycopg's `sqlstate`, psycopg2's `pgcode`) and never by the message, which
quotes the query: 42703 is accounted for by a column gone, 42883 by one retyped, against the
same `column_drift`. The PostgreSQL catalog lists every column whatever the role may read, so
no column is left out of it for being hidden.

- `EXPLAIN` of the reference query SHALL leave the plan unpriced where the rule above would on
  ClickHouse, with the evidence `EXPLAIN answered the reference query with SQLSTATE <state>
  <name>, taken to mean the query no longer fits the mart as the engine has it (<columns>
  drifted from the model)`; otherwise the error SHALL end the run as above;
- the exact SELECT of a plan so answered SHALL NOT be sent, with the evidence `the exact SELECT
  was not run: EXPLAIN of the same query answered SQLSTATE <state> <name>, and a query the mart
  no longer fits measures nothing; no declared runtime bound was measured`; a 42703 or 42883
  from an exact SELECT whose plan was answered SHALL end the run as above;
- `count(*)` and `max(<event_time>)` SHALL be asked again as `count(*)` alone, and the reading
  kept, where the drift of the event-time column accounts for the state, as on ClickHouse.

Any `42501 insufficient_privilege` from the exact SELECT SHALL leave every declared runtime
bound unmeasured, with the evidence `PostgreSQL refused the exact SELECT with SQLSTATE 42501
insufficient_privilege; no declared runtime bound was measured`. Live it is a column the
reference query names that the role may not read, which `dwh.security` blocks on; its plan was
refused already, as before (`the engine refused EXPLAIN on the reference query`). The runtime
bounds are set before the SELECT, outside this reading, so a `SET` the role may not make still
ends the run.

Any other state — `42601 syntax_error` of a query of ours, a connection failure — and an error
with no state still end the run as above. `tests/test_decision_live_postgres.py` asserts the
pinned drifts above, the hidden column and the clean, stale, rows-ceiling and writer stands
against a PostgreSQL it creates its own database and roles in; slice S7b ran it on the Mac
(PostgreSQL 16.15) and added it to the CI `integration` job, which starts a throwaway
`postgres:16` container for it; its first run there is still ahead. The cases the catalog does
not explain are asserted offline.

## Refusals read by the code (S8)

Since verifier 132 an answer the probe asks about the environment is a refusal on ClickHouse
by the code the error carries, as on PostgreSQL by its SQLSTATE, and never by the message,
which quotes names from the query:

- `497 ACCESS_DENIED`, `291 DATABASE_ACCESS_DENIED`, `60 UNKNOWN_TABLE` and
  `81 UNKNOWN_DATABASE` SHALL be refusals, whatever the words around the code;
- any other code SHALL NOT be a refusal, even where the message quotes a refusal's words: a
  `47 UNKNOWN_IDENTIFIER` for a column called `access_denied_at` is the drift the rule above
  weighs, or it ends the run;
- the wording (`ACCESS_DENIED`, `Not enough privileges`, `UNKNOWN_TABLE`, …) SHALL be read
  only for an error that carries neither a SQLSTATE nor a ClickHouse code.

The code is the one the S7 rule reads: the driver's `code` attribute, else the `Code: <n>.` that
opens the message or one of its lines, or follows `server response:`. Live, ClickHouse
24.8.14.39 answers `497` for a column the role may not read, for its `EXPLAIN ESTIMATE` and for
a table not granted; `60` for a table that is not there; `81` for a database that is not
there. `291` was not seen live and stays in the set: the wording verifier 131 read matched it
(`ACCESS_DENIED` inside `DATABASE_ACCESS_DENIED`), and what is not seen is not taken away.
Verifier 131 took the `47` above for a refused `EXPLAIN ESTIMATE` and a refused `max()` of the
event-time column: the plan was unpriced as refused, and the table taken for unreadable.
`tests/test_decision_live_clickhouse.py` pins `497`, `60` and `81` against the live engine, and
`47` for `max(access_denied_at)` as not a refusal; `tests/test_environment_probe.py` pins the
reading offline, with the code as an attribute and in the message.

## A catalog the role may not read (F1)

The column list of a mart is one question of the catalog: `pg_attribute` with
`format_type(atttypid, atttypmod)` on the Postgres family, `system.columns` on ClickHouse. A
role can be refused it — `REVOKE SELECT ON pg_catalog.pg_attribute FROM PUBLIC` answers
`42501`, live on PostgreSQL 16.15 — and verifier 132 read that refusal as an empty catalog:
each declared column whose reach was not denied (on ClickHouse, only with the grants answered)
gone, `physical.column_absent` (block) for each, over a mart that holds them all; and that
"drift" accounted for a `42703`/`47` of the reference query or of `max()` of the event-time
column, so the run reported a plan unpriced or rows undated for a reason nothing the engine
answered had given. Since verifier 133 (ADR 0010, F1):

- a refused column catalog SHALL leave the mart's columns unreported (`columns` is `None`), as
  for a probe that did not ask: `dwh.physical` compares no column of that mart and finds no
  column absent; the mart adds none of its declared column types to the compared count of the
  check's detail, which then says coverage is incomplete, and the code is `physical.not_run`
  when no mart in scope had a column type or a declared layout field compared;
- no drift SHALL then account for a `42703`/`47`: such an error from EXPLAIN of the reference
  query or from `max()` of the event-time column ends the run as above;
- an answered catalog with no rows SHALL be read as before — the engine's answer, which
  `column_drift` weighs as any other (ClickHouse leaves out what the role may not read).

`dwh.physical` raises no review of its own over a refused catalog: it passes with coverage
incomplete, as for a probe that did not ask, and where nothing was compared the owner report
lists `physical.not_run` as not done. The refusal of `pg_attribute` refuses `has_column_privilege` asked over it too, so on
PostgreSQL the review comes from `dwh.security`: `security.not_observed` for which columns the
role may read. A role refused only the type question (column grants on `pg_attribute`), or a
ClickHouse role refused `system.columns` alone, has its verdict decided by the other checks; a
review of `dwh.physical`'s own is in the [backlog](../DECISIONOPS_BACKLOG.md).

The presence question of a PostgreSQL mart reads `pg_tables`, `pg_views` and, since verifier
133, `pg_matviews`: a mart served as a materialized view (relkind `m`) is in neither of the first
two, and verifier 132 graded it `access.table_absent` (block). Verifier 132 read the refusal of
the first two (`REVOKE SELECT ON pg_catalog.pg_tables, pg_catalog.pg_views FROM PUBLIC`,
`42501`) as "no row": `access.table_absent` (block) over a mart the role reads. Since verifier
133 a refused presence question SHALL be followed by `to_regclass` of the mart's name, which
reads none of the three views: a relation by that name is present, and the probe goes on as for an
answered one. `NULL`, or that question refused too, SHALL leave the mart absent, as before. An
answered "no row" SHALL stay absent without the second question, which would resolve any
relation of that name, an index or a sequence too.

`tests/test_decision_live_postgres.py` pins four cases live on PostgreSQL 16 — the clean mart
behind the revoked column catalog (`physical.not_run`, `security.not_observed`,
`needs_review`), a column dropped behind it (exit 2, `UndefinedColumn`, no bundle), the clean
mart behind the revoked table catalog, and the same rows served as a materialized view (both the
clean stand's `pass`). The ClickHouse refusal
of `system.columns` is pinned offline only; the live ClickHouse test does not refuse it. The
ClickHouse presence question over `system.tables` is unchanged: a refusal there still reads as
absent, the one silence the probe already shares with a mart out of reach (both block).
Check grading is unchanged (check 83); ADR 0010 named `CHECK_VERSION` for this item, but the
rules of `dwh.physical` over unreported columns and of `dwh.access` over a present mart are the
ones they had — what changed is the fact the probe reports, and that is the verifier's (as S7,
S7b and S8).

## One type, any of its PostgreSQL names (F2)

`dwh.physical` compares the type the model declares for a column with the one the catalog holds,
and on the Postgres family the catalog's is the name `format_type(atttypid, atttypmod)` prints:
one name per type. A model may write any name the engine takes for it — `float8`, `int4`,
`varchar(20)`, `timestamp` — and verifier 133 compared the names as written, spacing and case
aside: a model declaring `float8` for a `double precision` column had that column graded
`physical.type_drift` (review), and the "retype" accounted for a `42883` of the reference query
or of `max()` of the event-time column, so a function missing altogether was reported as the
mart's drift. Since verifier 134 and check 84 (ADR 0010, F2), on an engine of the Postgres family
(`postgres`, `greenplum`, `greengage`):

- a declared type and the catalog's SHALL compare equal when both name one type. The names read are
  those of the manual's alias table, the serial types aside — `int` and `int4` for `integer`, `int2`
  for `smallint`, `int8` for `bigint`, `float4` for `real`, `float8` for `double precision`, `bool`
  for `boolean`, `decimal` and `dec` for `numeric`, `char` for `character`, `varchar` and `char
  varying` for `character varying`, `varbit` for `bit varying`, `timestamptz` and `timetz` for the
  time types `with time zone` — and the rules of its grammar: `timestamp` and `time` with no zone
  are `without time zone`, `float` is `double precision`, `float(p)` is `real` for `p` from 1 to 24
  and `double precision` from 25 to 53, `numeric(p)` is `numeric(p,0)`, `character` with no length
  is `character(1)`, `bpchar(n)` is `character(n)`. A modifier is kept, and so is an array as the
  one pair of brackets `format_type` prints for any of its dimensions (`varchar(10)[3]` and
  `varchar(10)[][]` are `character varying(10)[]`);
- a spelling the rule does not read — a quoted or schema-qualified name (`"char"`,
  `pg_catalog.int4`), `integer array`, `national character varying`, `serial`, `bpchar` with
  no length, `float(54)`, a zone after a name that carries one (`timestamptz without time
  zone`), an array bound other than one number — SHALL be compared as written, spacing and
  case aside, as before;
- a pair the rule reads as one type SHALL be no drift: `dwh.physical` does not grade it, and it
  accounts for no `42883`, which then ends the run as any state the catalog does not account for;
- ClickHouse SHALL compare as before, spacing and case aside: its aliases are its own (its `INT8`
  is `Int8`, PostgreSQL's `int8` is `bigint`), and `system.columns` prints the canonical name.

`tests/test_decision_live_postgres.py` creates a column in each spelling of the rule on
PostgreSQL 16 and reads it back through `format_type`, as the probe does: the names the rule maps
to are the engine's, not the manual's. The rule is pinned offline both ways and on each engine
name of the family; a spelling of thousands of characters is read at once (each repeated part
of the pattern matches its text one way only, so a failed match does not retry every split),
and so is a `float(p)` of thousands of digits, past what `int()` converts.

## Runtime SQL guard refusal at the CLI boundary (T-80)

If `collect` raises `SQLGuardError` while `decision verify --dwh` asks about the
environment, the CLI SHALL return exit 2, empty stdout, and exactly one diagnostic:

`error: cannot execute environment query: SQLGuardError`

The diagnostic SHALL omit the exception text, which can contain SQL, line breaks
and terminal controls from the parser. No evidence directory SHALL be created.
The refusal SHALL NOT become `cost.not_measured`, a completed check or a retry.

For ordinary CLI logging, a `NullHandler` on `sqlglot` SHALL prevent `logging.lastResort`
from printing parser warnings (including SQL) before that diagnostic. It is installed
after the catalog connection factory, before `collect`, and is not duplicated on later
calls. Explicitly configured application handlers, levels and propagation are preserved:
they can still receive parser records, so their output is outside the one-line guarantee.

The handler covers only `collect`. A `SQLGuardError` raised by the catalog connection
factory remains unchanged. Direct collector/runtime calls retain their exception;
unrelated exceptions, including `TokenError`, remain outside this handler. Guard policy,
connection/catalog order, successful artifacts and verifier 98 / check 73 are unchanged.
Catalog queries and runtime connection creation may precede the guard refusal;
the refused runtime statement is not executed.

Verification supplies rejected SQL through the `PlanQuestion` seam, uses fake DWH
clients, and exercises the real runtime guard for all four engine aliases. It does
not establish that the current typed compiler emits that SQL from an ordinary input.

## Known boundaries

- The answers of a query that no longer fits the mart (S7, S7b) were observed live on
  ClickHouse 24.8.14.39 and PostgreSQL 16.15 only. Greenplum and Greengage take the PostgreSQL
  path and are expected to answer with the same states, but no stand of either has been run.
  A retype answered with another state — `42804 datatype_mismatch`, which no drift produced on
  16.15 — still ends the run as above.
- A code or a state is taken to mean a drift by its kind alone — a column gone for `47` and
  `42703`, one retyped for `43` and `42883` — and neither the message nor where in the query the
  engine failed is read. A fault of the gate's own query that meets a drift of that kind of a
  column the query names is reported as that drift: the plan unpriced, its evidence «taken to
  mean», `physical.*` grading the drift; it ends the run once the drift is resolved. `42883`
  also answers a function or an operator missing altogether (an older kernel, an extension
  outside `search_path`), so a model that declares a type other than the one the catalog holds
  holds that column as retyped, and a `42883` over it as explained. Another name of the same
  type no longer does (F2, above), save a spelling F2 does not read (`pg_catalog.int4`,
  `serial`), which is compared as written.
- Still ending the run, as before S7b: a drift that breaks the reference query of a mart the role
  may not read (PostgreSQL resolves the query's columns and functions before it checks
  privileges, and the catalog of an unreadable mart is not read, so no drift accounts for the
  state); and a table the reference query joins, not the mart, dropped: its `EXPLAIN` answers
  `42P01`, a refusal, so the exact SELECT is still sent, and its `42P01` ends the run.
- On ClickHouse a column absent from `system.columns` while the engine did not answer what the
  role may read is not compared: it may be hidden rather than gone. A `43`/`47` over such a
  column, even one really dropped, still ends the run, as before S7; `dwh.physical` does not
  grade that column either.
- An error ClickHouse relays from another engine — a table of the `PostgreSQL` or `MySQL` engine
  whose server refuses the read — is read by the ClickHouse code it carries, not by the refusal
  its message quotes (S8): under any code outside the refusal set it ends the run.
- Other faults of the probe, including tokenizer failures that are not `SQLGuardError`,
  remain outside the named handlers and may end in a traceback with exit 1.
- Other driver failures end the run through the existing handler. Server-side settings
  refusals, connection failures and unknown errors are not reclassified as measurements.
- The target is named as it is configured: host, port and database, never the user or the
  password. What the driver said is kept as it said it, in one line: libpq names the host and
  the port it tried, and an authentication refusal names the user. The message keeps both as
  they are; since T-117 the line the command prints spells each character of it that is not
  printable by its escape ([one-line errors](one-line-errors.md)).
- Settings validation does not check connectivity or add new value restrictions. Other CLI
  commands retain their existing settings-error handling. `SettingsError` from custom or
  complex settings sources is outside this slice; current Settings fields are scalar.
- The engines of this slice are a closed port of the loopback address, a listener that accepts
  a connection and closes it at once, and fake engines that raise the drivers' own errors. No
  live engine was asked.
