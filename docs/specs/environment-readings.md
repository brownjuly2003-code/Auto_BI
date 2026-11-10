# Capability: Attribution of environment readings to the questions they answer

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

Step 7 (DECISIONOPS_ARCHITECTURE §8.2) asks the engine one question per pair of a mart and the
event-time column the in-scope metrics date it by (`questions_for`): two metrics that date one
mart by different columns are two questions, each with its own clock, columns and metrics. The
collector answers each question with its own reading — the newest value of that clock, which
asked columns the role may select, whether the mart exists and can be read for that clock. A
reading answers the question it was made for and no other: grading one question with another
question's reading turns a stale clock into a fresh one, a denied column into an unasked one,
and makes the verdict depend on the order of the readings.

## Requirement: catalog values cannot supply SQL syntax

Every value inserted as a ClickHouse catalog string literal SHALL pass through
`_ch_literal`, which doubles every backslash and every single quote. PostgreSQL-family
catalog values containing a backslash SHALL use `E'…'` with backslashes and quotes doubled;
values without a backslash SHALL retain their ordinary single-quoted form with quotes
doubled. In the gate's checked PostgreSQL session (`client_encoding=UTF8`,
`standard_conforming_strings=on`), reading each representable NUL-free literal SHALL
recover the supplied value and SHALL NOT add a SQL condition. The same value and structure
requirements apply to ClickHouse catalog literals. T-126 literal escaping SHALL NOT change
the SQL bytes of questions for names consisting of letters, digits and underscores. Since
verifier 127, PostgreSQL relation arguments quote both name parts, so ordinary names retain
their answers but their catalog SQL bytes have changed, pinned by
`test_ordinary_catalog_sql_bytes` in `tests/test_catalog_literals.py`. ClickHouse catalog SQL
bytes for ordinary names remain unchanged; see [environment access](environment-access.md).

`make_run_query_pg`, `make_postgres_runtime_probe` and `make_postgres_result_query` SHALL
request UTF8 through the driver and standard strings on through its connection options.
On connection, before each explicit `execute` or `stream`, and before the start of each
transaction, they SHALL check both server-reported parameters through
`connection.info.parameter_status`. Result capture SHALL use an explicit transaction and
check again after reading its statement's result, while that transaction is still open;
it SHALL return the result only after the transaction ends. Transaction exit sends its
fixed COMMIT or ROLLBACK without a separate session check; if a refusal has closed the
connection, exit sends neither command and preserves the refusal. A value other than
exactly `UTF8` or `on`, respectively,
including a missing parameter, SHALL close the connection and raise a driver error;
the pending statement SHALL NOT be sent, and a captured result awaiting its final check
SHALL NOT be returned. Result capture SHALL retain its `ResultCaptureError` connection-error
wrapper. These checks SHALL issue no SQL.

### Scenario: the connection reports different settings

- **WHEN** the driver returns a connection reporting `client_encoding=EUC_JP`, `SJIS`,
  `LATIN1` or `SQL_ASCII`, `standard_conforming_strings=off` or `ON`, or a missing value
  for either parameter
- **THEN** the factory refuses that connection before asking catalog, runtime or result
  questions, and its error names the setting, required value and reported value

### Scenario: a statement changes the session settings

- **WHEN** a statement changes `client_encoding` to `EUC_JP` or
  `standard_conforming_strings` to `off` on a retained connection
- **THEN** the next explicit `execute` or `stream` is refused before it is sent, and a
  runtime or capture call is refused before its transaction starts; the error names the
  setting, required value and reported value
- **AND** if a change during result capture is still in effect after reading the result,
  capture raises the same connection-error wrapper instead of returning the result.
  This includes transaction-local changes from `set_config('client_encoding', 'LATIN1', true)`
  or `set_config('standard_conforming_strings', 'off', true)`

### Scenario: capture cannot complete its transaction

- **WHEN** the outer ROLLBACK raises a driver error after capture has read its result
- **THEN** capture raises `ResultCaptureError` with `PostgreSQL session preparation failed:`, the driver
  error type and its text, without returning the result
- **WHEN** the statement raises a driver error and its ROLLBACK succeeds or also fails
- **THEN** capture raises the same query-error wrapper for the statement's error, without
  returning a result; a failed ROLLBACK does not replace that error

Capture ends its transaction with ROLLBACK, including after a successful read, as required
by [probe execution safety](probe-execution-safety.md). These outcomes are pinned by
`test_capture_wraps_failed_transaction_exit` and
`test_capture_keeps_statement_error_after_rollback` in `tests/test_postgres_sessions.py`,
and the successful command order by `test_capture_sql_savepoint_order` in
`tests/test_postgres_savepoint.py`.

### Scenario: the same capture callable is used after a session refusal

- **GIVEN** capture refused a transaction-local setting change and closed its connection
- **WHEN** that same callable is called again
- **THEN** it raises `ResultCaptureError` with
  `PostgreSQL connection failed: OperationalError: the connection is closed` before starting
  a transaction or sending the next statement, and opens no second connection

### Scenario: a name contains an escape or a quote

- **WHEN** a database, table, column or role name used by a catalog question contains a
  backslash (including literal `\n` or `\x27`), a quote, two adjacent quotes, a line feed,
  a NUL-free control character, a non-ASCII letter or `¥`
- **THEN** the question retains its text outside literals and each literal representable
  in the database encoding reads as the original value under the session settings above

### Scenario: the mart is absent or unreadable

- **WHEN** the earlier catalog questions succeed, the mart is absent or PostgreSQL reports
  it unreadable, and the event-time identifier is outside the collector's quoting rule
- **THEN** the collector still returns the absence or unreadability fact before quoting
  the event-time identifier

### Scenario: a present mart needs an unsupported identifier

- **WHEN** collection reaches COUNT/MAX and its table or event-time identifier is outside
  the existing identifier rule
- **THEN** the collector raises `UnsafeIdentifierError`; `decision verify` writes no bundle

### Known boundaries of catalog literals

- Session checks detect a change still in effect at a checked point. A change made and
  restored inside one statement is not detected, including by capture's check after reading
  the result. When a live server reports a changed parameter is established only for the
  recorded kernels below.
- Literal escaping does not expand the identifier rule. Tests of later catalog questions
  substitute only the COUNT/MAX table identifier to reach those questions with hostile names;
  separate tests keep the real rule and its refusal order.
- PostgreSQL privilege functions and `to_regclass` parse their relation-name argument after
  reading the literal (`cstring` for `to_regclass` on 9.4). Since verifier 127 the collector
  quotes the exact schema and table inside that literal; case is preserved, as required by
  [environment access](environment-access.md). COUNT/MAX retains its identifier rule.
  On the recorded verifier 122 PostgreSQL 16.15 and 9.4.26 runs, before that quoting,
  the two privilege functions raised
  `InvalidName` (SQLSTATE 42602) for `public.mart\' OR 1=1 --`; `to_regclass` returned NULL
  on 16.15 and raised `InvalidName` on 9.4.26. The collector still does not treat 42602 as an
  access refusal (`test_relation_name_error_precedes_absence` in
  `tests/test_catalog_literals.py` injects that driver error). Superuser sessions skipped
  those questions; for the present mart the recorded collector reached readability and raised
  `InvalidName` on PostgreSQL, and raised `UnsafeIdentifierError` on ClickHouse.
- Recorded macOS runs before the connection changes used ClickHouse 24.8.14.39,
  PostgreSQL 16.15 and the PostgreSQL 9.4.26 kernel, with PostgreSQL standard strings
  both on and off. With the literal changes, every server-read literal equalled its
  supplied name and `missing\' OR 1=1 --` was absent: zero literal or presence mismatches.
  At `40052f9` there were 26 mismatches on ClickHouse plus PostgreSQL 16 and 15 on 9.4;
  the absent name was found on ClickHouse and on both PostgreSQL kernels with standard
  strings off.
- Further recorded PostgreSQL 16.15 and 9.4.26 runs used superuser sessions in EUC_JP and
  UTF8 databases, both with database-default standard strings off. At `40052f9`, the
  EUC_JP connection reported EUC_JP/off: the absent `missing¥' OR 1=1 --` was found and
  collection raised `SyntaxError` (42601). The absent `missing\¥' OR 1=1 --` was reported
  absent, but its literals lost `¥` and read as `missing\' OR 1=1 --`. With only the first
  literal changes, both absent names were found in EUC_JP and collection raised 42601.
  In UTF8 both names were absent in both trees; at `40052f9`, the second name's literals
  lost the backslash.
- With the connection helper, those runs reported UTF8/on in both databases on both
  kernels. In UTF8 both names were absent and every literal read back exactly. In EUC_JP,
  every question containing `¥` raised `UntranslatableCharacter` (22P05): the UTF8 bytes
  `0xc2 0xa5` have no equivalent in EUC_JP. Collection raised that error without returning
  facts. The gate sets the client encoding; it does not change the database encoding.
- Later recorded runs on both kernels asked the catalog callable, in each database,
  `SELECT set_config('client_encoding', 'EUC_JP', false)` or
  `SELECT set_config('standard_conforming_strings', 'off', false)` and then, on the same
  callable, the presence question for the absent `missing¥' OR 1=1 --`. At `40052f9` and
  with only the literal changes, the presence question was sent: after the encoding change
  in either database and after the standard-strings change in the EUC_JP database it
  returned the `pg_tables` rows of other tables (68 on 16.15, 58 on 9.4.26) instead of
  none; after the standard-strings change in the UTF8 database it returned none. With the
  per-statement check it was not sent: the callable raised the session error naming the
  setting, the required value and the reported value.
- On both kernels and in each database, capture through `make_postgres_result_query` was
  also asked `SELECT set_config('client_encoding', 'LATIN1', true) AS changed, 'Ã©' AS value`
  and `SELECT set_config('standard_conforming_strings', 'off', true) AS changed`. With the
  check inside the capture's transaction, it raised the session error naming the setting for
  both statements in the UTF8 database and for the standard-strings change in the EUC_JP
  database; the EUC_JP database refused LATIN1 itself (`FeatureNotSupported`, 0A000), and
  capture raised its query-error wrapper. No capture returned a result. At `40052f9`, and
  with the final check after an autocommit statement whose own transaction had restored the
  setting, the standard-strings capture returned `off` in both databases, and the encoding
  capture in the UTF8 database returned `LATIN1` and `é` for the literal `Ã©`.
- Escaping a Python string alone cannot preserve it through every client encoding:
  Python `euc_jp` and `shift_jis` encode `¥` as byte `0x5c`. The gate fixes the client
  encoding, not the database encoding. A supplied `RunQuery` is responsible for its own
  session settings; this requirement does not cover other connections. PostgreSQL
  generators and ClickHouse identifier generators are unchanged.
- The test reader covers the emitted single-quoted forms, not arbitrary SQL. NUL-containing
  names, dollar quoting, and SQL supplied as a complete reference query are outside this
  literal requirement. The recorded 9.4.26 run covers the emitted E form; it does not
  establish full Greenplum or Greengage compatibility.

Tests: `tests/test_catalog_literals.py`, `tests/test_postgres_sessions.py`,
`tests/test_environment_identifiers.py`.

## Requirement: a reading answers the question it was collected for

The native collectors SHALL record on every table reading the event-time column of the question
it answers, on every return branch of both engine families. Access, freshness, physical,
security and cost grading SHALL read, for each table question, only the reading recorded for
that mart and that event-time column. The order of readings and of questions SHALL NOT change
any finding.

### Scenario: a stale second clock is reported once, on its own clock

- **GIVEN** one mart `dm.sales_daily` asked about by two questions, clock `booked_at` (columns
  `booked_at`, `booked_amount`) and clock `shipped_at` (columns `shipped_at`, `shipped_amount`),
  a reading for `booked_at` dated `2026-10-01T00:00:00Z` and a reading for `shipped_at` dated
  `2026-09-01T00:00:00Z`, `freshness_max_age` `P1D` and `created_at` `2026-10-01T12:00:00Z`
- **WHEN** freshness is graded
- **THEN** exactly one `freshness.stale` is reported, its detail names `shipped_at`, and the
  `booked_at` question has no freshness finding

### Scenario: the order of readings and questions does not change the findings

- **GIVEN** the questions and readings above, in every order of the readings and of the
  questions
- **WHEN** access, freshness, physical and security are graded
- **THEN** each order yields the same findings with the same codes, subjects and details

### Scenario: a column denied under the second clock blocks

- **GIVEN** the two questions above and a `shipped_at` reading that says the role may not select
  `shipped_amount`, while the `booked_at` reading may select everything it was asked about
- **WHEN** security is graded
- **THEN** `security.column_unreadable` names `shipped_amount`, the check blocks, and no other
  reading's column grants are consulted for it

### Scenario: an unreadable reading for one clock is not covered by another

- **GIVEN** the two questions above, a readable `booked_at` reading and a `shipped_at` reading
  that exists but is not readable
- **WHEN** access is graded
- **THEN** `access.table_unreadable` is reported for the `shipped_at` question and the check
  blocks

### Scenario: both clocks fresh and readable stay clean

- **GIVEN** the two questions above with both readings dated `2026-10-01T00:00:00Z` and every
  asked column selectable
- **WHEN** access, freshness and security are graded
- **THEN** no finding is reported

### Scenario: the collectors record the clock of each reading

- **GIVEN** the two questions above and a ClickHouse or a PostgreSQL-family session replayed
  offline, on every branch a reading can end on (mart absent, mart unreadable, reading refused,
  reading made)
- **WHEN** the collector answers the questions
- **THEN** each reading carries the event-time column of its own question, and collection stays
  read-only

## Requirement: a question without exactly one attributable reading is a named coverage gap

When no reading carries the question's mart, the system SHALL keep today's
`access.table_not_probed` review. When readings of the mart exist but none, or more than one,
answers the question's event-time column, the system SHALL report one review finding
`access.reading_unattributed` naming the mart and the event-time column, and SHALL NOT grade that
question's freshness, physical layout, security or cost from any reading. A reading that records
no event-time column (made before readings recorded one) SHALL answer a question only when the
questions date its mart by exactly one event-time column and it is the only reading of that
mart. Correspondence SHALL NOT be inferred from list order, timestamps or row counts.

### Scenario: an unlabelled reading of a single-clock mart keeps today's grading

- **GIVEN** one question for `dm.sales_daily` with clock `booked_at` and one reading of
  `dm.sales_daily` that records no event-time column
- **WHEN** the environment checks are graded
- **THEN** the reading answers the question and every finding is the one the checks report today

### Scenario: unlabelled readings of a multi-clock mart are not matched by position

- **GIVEN** the two questions above and two readings of `dm.sales_daily` that record no
  event-time column
- **WHEN** access is graded
- **THEN** `access.reading_unattributed` is reported for `booked_at` and for `shipped_at`,
  access reviews, and freshness, physical and security grade neither question

### Scenario: a reading of another clock does not answer the question

- **GIVEN** one question for `dm.sales_daily` with clock `shipped_at` and one reading of
  `dm.sales_daily` recorded for `booked_at`
- **WHEN** access is graded
- **THEN** `access.reading_unattributed` names `dm.sales_daily` and `shipped_at`

### Scenario: two readings for one clock are ambiguous

- **GIVEN** one question for `dm.sales_daily` with clock `booked_at` and two readings recorded for
  `booked_at`
- **WHEN** access is graded
- **THEN** `access.reading_unattributed` names `dm.sales_daily` and `booked_at`, and no reading is
  picked

### Scenario: an exact reading is used and an extra unlabelled reading fills nothing

- **GIVEN** the two questions above, a reading recorded for `booked_at` and a reading that records
  no event-time column
- **WHEN** the environment checks are graded
- **THEN** the `booked_at` question is graded from its reading and `access.reading_unattributed`
  names `shipped_at`

### Scenario: a mart with no reading keeps its finding

- **GIVEN** a question for `dm.sales_daily` and no reading of `dm.sales_daily`
- **WHEN** access is graded
- **THEN** `access.table_not_probed` is reported as today

## Requirement: cost and report wording use only attributed readings

The cost check and the runtime cost probe SHALL take a metric's prerequisites (mart exists, is
readable, rows not emptied by row security) from the reading of the question that lists that
metric. The owner-facing clean wording of the environment checks SHALL count only attributed
readings.

### Scenario: healthy multi-clock marts are still priced

- **GIVEN** the two questions above, both readings readable, and a cost ceiling on a metric of
  each question
- **WHEN** the collector decides which reference queries to execute and cost is graded
- **THEN** both metrics' runtime probes run and both are graded

### Scenario: a metric whose own reading is unusable is not priced

- **GIVEN** the two questions above, a readable `booked_at` reading, an unreadable `shipped_at`
  reading, and a cost ceiling on a metric of the `shipped_at` question
- **WHEN** the collector decides which reference queries to execute
- **THEN** that metric's runtime probe is withheld, and the readable `booked_at` reading does not
  stand in for it
