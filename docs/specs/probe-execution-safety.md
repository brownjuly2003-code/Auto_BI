# Capability: Read-only execution with finite probe limits

## Requirement: confirm read-only execution before reviewed text

Native capture and runtime executors SHALL require a read-only session before sending the
reviewed SELECT. PostgreSQL SHALL use an explicit transaction, issue `SET TRANSACTION READ
ONLY`, set the local statement timeout, and require exactly `on` from `SHOW
transaction_read_only` on that connection. Before any per-query settings, ClickHouse SHALL
ask for `readonly`, `max_execution_time` and all eleven overflow modes below, with no per-query
settings. Mode 0 SHALL issue `SET readonly = 2`; modes 0 and 2 SHALL then require `2` from
`SELECT getSetting('readonly')` through the same retained client. Mode 1 SHALL send neither
SET nor per-query settings. Capture SHALL accept mode 1 only with a positive, finite session
`max_execution_time` no greater than `capture_timeout_ms / 1000` and
every overflow mode equal to `throw`. A time refusal SHALL name the configured limit and
session values. Each other failing overflow mode SHALL be named with its value.
Mode-1 capture/catalog SHALL also ask for `name, value, changed` of `limit` and `offset`
from `system.settings`, without per-query settings. Both rows SHALL have `value='0'` and
`changed=0`; missing, repeated or malformed rows SHALL refuse. `getSetting` and `value`
report zero even when the user's own settings set these two names, so `changed=1` SHALL
refuse with the setting name and explain that readonly=1 cannot lift it. Modes 0 and 2
SHALL omit this extra question.
Other modes and failed confirmation SHALL prevent execution.
Capture SHALL report the refusal as `ResultCaptureError`.

### Scenario: the setting is not confirmed

- **WHEN** PostgreSQL reports `off`, `ON` or an empty value, or ClickHouse reports an invalid
  mode or does not confirm mode 2 after SET
- **THEN** neither executor sends the reviewed text, and the error names the required setting

### Scenario: a session owns its time limit

- **WHEN** ClickHouse reports mode 1, `max_execution_time=0.04` and `throw` with a configured
  capture or catalog limit of 75 ms
- **THEN** capture/catalog sends the question without per-query settings; zero, 0.1 seconds
  or `break` instead causes refusal before the text
- **AND** a capture timeout names both `capture_timeout_ms=75` and
  `session max_execution_time=0.04 s`
- **AND** runtime executes nothing in every mode-1 case, returning no checked bound and
  evidence naming the session and ending `no declared runtime bound was measured`

## Requirement: finite statement time for environment questions and capture

`Settings` SHALL define positive `environment_query_timeout_ms` and `capture_timeout_ms`,
each defaulting to 30000. The CLI environment probe SHALL pass the former to its native
catalog callable. That callable SHALL set PostgreSQL `statement_timeout` before every
question or pass ClickHouse `max_execution_time` and `timeout_overflow_mode='throw'` on
every question in modes 0 and 2. Before its first question, a bounded ClickHouse catalog
callable SHALL read the session settings once per client, without changing the mode.
Mode 1 SHALL meet the same session-limit rule against `environment_query_timeout_ms`.
Refusal SHALL be `OperationalError`, not a missing-privilege fact. Without
`timeout_ms`, the catalog callable SHALL retain its earlier behavior.
This includes COUNT/MAX statistics, catalog reads and plan questions.
Capture SHALL use its own timeout, PostgreSQL transaction-local and ClickHouse per query
or through the accepted mode-1 session limit,
including its empty-result metadata question. A recognised timeout SHALL name the setting
and value. Capture timeouts SHALL raise `ResultCaptureError`; environment timeouts SHALL
retain the driver-error boundary, without producing access or freshness facts.

In modes 0 and 2, both capture questions, every bounded catalog question and runtime SHALL
send `max_execution_time`, `limit=0`, `offset=0` and `throw` for each of:
`distinct_overflow_mode`, `group_by_overflow_mode`, `join_overflow_mode`,
`read_overflow_mode`, `read_overflow_mode_leaf`, `result_overflow_mode`,
`set_overflow_mode`, `sort_overflow_mode`, `timeout_overflow_mode`,
`timeout_overflow_mode_leaf`, `transfer_overflow_mode`. Runtime SHALL retain its byte limit.

A catalog timeout under mode 1 SHALL say
`ClickHouse session max_execution_time=<s> s exceeded; configured environment_query_timeout_ms=<n>`.

## Requirement: preparation errors are separate from query outcomes

Session reads, readonly SET/confirmation, the limit/offset question, PostgreSQL preparation
and transaction/savepoint entry and exit SHALL be outside the query-outcome handler.
Driver errors raised there SHALL remain driver errors for catalog/runtime. Capture SHALL
report `<engine> session preparation failed: <driver class>: <reason>`.
An existing `check_session` refusal SHALL keep its connection-error wrapper. These failures
SHALL establish no checked or exceeded runtime bound and no safety-timeout outcome.
Runtime SHALL retain its unmeasured outcome for a client-side
`Setting <name> is unknown or readonly` refusal raised by the reviewed question itself.

ClickHouse server codes SHALL come from the driver's `code` when present; otherwise from
`Code: N.` at the start of the message, after a line break or after `server response: `.
Only code 159 SHALL mean a timeout. A table name containing `TIMEOUT_EXCEEDED` SHALL not
change the meaning of another code.

## Requirement: PostgreSQL settings do not outlive the reviewed text

Capture and runtime SHALL use `connection.transaction(force_rollback=True)` for the
reviewed question. Capture SHALL send `SAVEPOINT capture_query` and
`ROLLBACK TO SAVEPOINT capture_query` as plain SQL on its cursor, outside `query_errors()`:
savepoint, text, rows and description, session check, savepoint rollback. psycopg counts a
nested transaction before sending its SAVEPOINT and keeps that count when the command fails.
A failure of either savepoint command SHALL answer
`PostgreSQL session preparation failed: <class>: <reason>` through `ResultCaptureError`,
with the outer transaction still attempting ROLLBACK. A text error, session refusal or
capture limit failure SHALL send no ROLLBACK TO SAVEPOINT and SHALL keep its error;
a session refusal that closes the connection SHALL keep its connection-error wrapper.
If metadata is
absent, capture SHALL check the session and ask for metadata after the savepoint rollback,
without a new SET. The outer transaction SHALL end with ROLLBACK. Both local and session
settings changed by the text SHALL thereby be restored before the next capture/runtime
question. Capture's session checks SHALL remain before and inside the transaction.
After savepoint rollback, capture SHALL require transaction status INTRANS; after outer exit,
capture/runtime SHALL require IDLE. A different state SHALL close the connection and raise
an OperationalError through the preparation boundary, including when psycopg suppressed
a rollback error. These checks establish the reported transaction state, not every command
sent inside the driver.

## Requirement: reviewed SETTINGS cannot replace execution limits

Before any session question, ClickHouse capture SHALL refuse reviewed SETTINGS naming
`max_execution_time`, `timeout_overflow_mode`, `max_execution_time_leaf` or
`timeout_overflow_mode_leaf`, all eleven overflow modes and `profile`.
Runtime SHALL also refuse `max_bytes_to_read`,
`read_overflow_mode`, `max_bytes_to_read_leaf` and `read_overflow_mode_leaf`.
Names SHALL be compared case-insensitively across every query branch, using the guard's
parse and SETTINGS traversal. The guard's acceptance rules SHALL remain unchanged.
Capture SHALL raise `ResultCaptureError` naming the setting. Runtime SHALL execute nothing
and return no checked bound, with evidence naming the setting and ending
`no declared runtime bound was measured`.
Text SETTINGS naming `limit` or `offset` SHALL remain accepted: like SQL LIMIT/OFFSET,
they are part of the reviewed question's meaning. A named profile can replace limits and
overflow modes, so it SHALL be refused before session preparation.

### Scenario: settings inside another query branch

- **WHEN** a protected setting occurs in an outer SELECT, subquery or UNION branch, including
  an upper-case spelling
- **THEN** capture/runtime refuses before its first session question

### Scenario: statistics exceed their time allowance

- **WHEN** a statistics question raises PostgreSQL `QueryCanceled` naming statement timeout,
  or ClickHouse reports server code 159
- **THEN** its error names `environment_query_timeout_ms` and the configured value

## Requirement: capture returns a complete result within positive size limits

`Settings` SHALL define positive `capture_max_rows=10000` and
`capture_max_bytes=16777216`. PostgreSQL capture SHALL consume `cursor.stream(size=1)`;
ClickHouse capture SHALL consume `query_rows_stream`. Before retaining each row, capture
SHALL check the row count and the accumulated sum of UTF-8 bytes of `str(value)` for
non-null values. Equality SHALL be accepted. The first excess SHALL raise
`ResultCaptureError` naming the limit and value, close the stream, and return no partial
`QueryResult`. The capture use case SHALL create neither a directory nor a result file
after such a refusal. A result within both limits SHALL retain its rows and column metadata.

### Scenario: the third row exceeds a two-row allowance

- **WHEN** the stream offers four rows and `capture_max_rows=2`
- **THEN** the third row causes a refusal, the fourth is not consumed, and no result is written

### Scenario: UTF-8 bytes exceed the allowance

- **WHEN** two rows each contain `é` and `capture_max_bytes=3`
- **THEN** the second row causes a refusal naming `capture_max_bytes=3`

- **WHEN** rows are `('é', NULL, 'x')`, `(NULL, 'y', NULL)`, `('z', NULL, 'w')`
- **THEN** the total is six bytes: six is accepted, five refuses on the third row, and two
  refuses on the first row, even though each individual column fits

### Scenario: an empty stream

- **WHEN** the result has no rows
- **THEN** the capture retains its column metadata and an empty row tuple
- **AND** the metadata question places the reviewed SQL on its own lines inside
  `SELECT * FROM (\n{statement}\n) AS capture_metadata LIMIT 0` on both engines, so a trailing
  line comment does not consume the closing parenthesis
- **AND** ClickHouse metadata follows the same session-limit rule

## Known boundaries

- These requirements are pinned by substituted-driver tests. One live run on throwaway
  PostgreSQL 16.15, PostgreSQL 9.4.26 (the kernel Greenplum 6 and Greengage 6 are built on)
  and ClickHouse 24.8.14.39 servers, with Python 3.13.13, psycopg 3.3.4 and
  clickhouse-connect 1.6.0, recorded on both PostgreSQL kernels: `nextval` and `setval`
  refused by the read-only transaction, the sequence unchanged; the 50 ms statistics and
  capture limits answering after 56 to 61 ms; the two-row and 64-byte limits refusing; an
  empty answer keeping its column; a text that changes `search_path` through `set_config`
  answering empty with the columns of the table the original `search_path` names, and the
  change not reaching the next capture on the same connection. On ClickHouse
  it recorded readonly 0 moved to 2 and a write refused with code 164; users whose own
  overflow modes or `limit` cut answers short at HEAD answering in full or failing with
  code 158 or 396; readonly=1 sessions refused or accepted by the rule above; texts whose
  SETTINGS name an overflow mode or `profile` refused; a missing table named
  `TIMEOUT_EXCEEDED` answering code 60, not a timeout. In 8000 captures on both kernels
  under a 1 ms statement timeout or random outside cancels, none answered with psycopg's
  transaction nesting error. No Greenplum or Greengage server was run, so their execution
  is not established.
- The byte counter measures retained value text, not wire bytes, YAML size or Python
  object memory. A driver may decode a whole row or ClickHouse block before the counter
  sees it. The row and byte limits do not bound that single allocation.
- A statement timeout is not an overall command deadline or a client network timeout.
  The runtime probe keeps its existing 60000 ms cap and contract-bound reporting.
- ClickHouse applies `max_execution_time` while processing blocks, not as a hard wall-clock
  deadline. In the recorded ClickHouse 24.8.14.39 runs a 50 ms limit fired after 0.10 to
  0.78 s by the server's own count, and the client got its answer as late as 3446 ms. In the live run
  above, `count()` and `max(sleepEachRow(0.1))` over `numbers(20) SETTINGS max_block_size=1`
  answered `environment_query_timeout_ms=50 exceeded (ClickHouse)` after 157 ms (HEAD
  completed it in 2108 ms), and the non-aggregate slow capture answered
  `capture_timeout_ms=50 exceeded (ClickHouse)` after 128 ms (HEAD returned its 20 rows
  after 2103 ms). An accepted readonly=1 session with its own 1 s limit answered after
  1085 ms, a readonly=2 session under a 1500 ms capture limit after 1765 ms.
- PostgreSQL timeout recognition requires `QueryCanceled` and the English text
  `statement timeout`; localized messages without that text remain driver errors.
- Injected query callables retain responsibility for their sessions and resources.
  Catalog sessions are not changed to read-only here: their role facts describe the
  configured role's access. Native capture and runtime use separate sessions.
- Existing PostgreSQL UTF8/on checks still detect changes at their checked points;
  this requirement does not establish a sandbox for arbitrary server-side functions.
- Settings that change the question's meaning, such as `additional_table_filters` and
  `additional_result_filter`, belong to the configured role and are not inspected here.
  Profile constraints fail with code 452 or a client-side driver refusal when a requested
  per-query setting violates them.

Tests: `tests/test_probe_completion.py`, `tests/test_clickhouse_session_limits.py`, `tests/test_probe_safety.py`, `tests/test_postgres_savepoint.py`, `tests/test_postgres_sessions.py`,
`tests/test_result_probe.py`, `tests/test_capture_driver_failures.py`,
`tests/test_runtime_probe.py`, `tests/test_runtime_setting_refusals.py`.
