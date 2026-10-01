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
