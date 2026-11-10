# Capability: The newest event time a DWH reports is read in one of three forms

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

`dwh.freshness` (DECISIONOPS_ARCHITECTURE §8.2, step 7) asks the engine for the newest value of
each question's event-time column, `max(<column>)` beside the count of rows, and ages it against
the run's `created_at`. The probe seals the text of that value in the facts of every `dwh.*`
check. The text is not a `Timestamp` ([UTC timestamps](utc-timestamps.md)): a mart dated by day
has no moment, and a PostgreSQL `timestamp` has no zone.

The drivers the project locks (clickhouse-connect 1.6.0, psycopg 3.3.4) hand the value back as
follows, decoded offline from the bytes a server sends and asked live of ClickHouse and
PostgreSQL (Known boundaries):
- ClickHouse `Date` is a `date`. A `DateTime` is a count of seconds since the epoch, so its
  instant is known. clickhouse-connect hands it back naive when it reads it in UTC: that is
  `tz_mode="naive_utc"`, its default, which `make_run_query` keeps. Otherwise the value comes
  back aware, in the zone the driver read it in. That zone is the column's
  (`DateTime('Europe/Moscow')`), else one the response names (`X-ClickHouse-Timezone`) that is
  not the server's, else the server's. Under the default `tz_source="auto"` the driver applies a
  server zone that is UTC, a fixed offset or a zone in the gate host's
  `zoneinfo.available_timezones()`, and reads in UTC one it cannot resolve. The gate host's own
  zone is used only when the server's is not applied, as with `tz_source="local"`, which
  `make_run_query` does not set.
- PostgreSQL and Greengage `date` is a `date`. `timestamptz` is aware, in the session's
  `TimeZone`. `timestamp` is naive: a wall clock whose zone the engine never had.

The probe sealed `isoformat()` of that value, with `+00:00` written `Z`. The check read the text
with `datetime.fromisoformat`, and a day or a clock with no zone as UTC. Reproduced on HEAD
`0e8db1f` (Python 3.13):
- one instant, `2026-08-25T06:30:00Z`, was sealed as `2026-08-25T06:30:00Z`,
  `2026-08-25T09:30:00+03:00` or `2026-08-25T06:30:00`, whichever zone the driver read it in
  (and as `2026-08-25T02:30:00-04:00` by a client reading in this gate host's zone). A second
  run of the same inputs into the same evidence directory was refused with
  `same idempotency key, different evidence`;
- a PostgreSQL `timestamp` of `2026-08-25T04:00:00`, a Moscow clock eight hours old at
  `2026-08-25T09:00:00Z`, was read as UTC and passed a six-hour window with nothing said;
- `2026-W35-2`, `20260824`, `2026-08-25 04:00:00` and `2026-08-25T06:30:00.123456abcZ` were read
  as moments, and the week was said to be read as `2026-W35-2T00:00:00Z`;
- a `timestamptz` past 9999-12-31 in UTC was a moment in the future and passed. psycopg hands
  such a value back at a fixed offset, as it does one before 0001-01-01 in UTC.

## Requirement: the probe spells an instant once, in UTC

The probe SHALL seal a value that names an instant in the `Timestamp` form: in UTC, to the
second, with `Z`. When the driver gave microseconds, they SHALL be six digits behind a point.

An aware `datetime` names an instant, whatever its zone. A naive `datetime` names one only where
the driver reads it in UTC, which is on ClickHouse. On PostgreSQL and Greengage, a naive
`datetime` SHALL be sealed as a clock with no zone: `YYYY-MM-DDTHH:MM:SS`, with the microseconds
as above. A `date` SHALL be sealed as the day `YYYY-MM-DD` on either engine.

An aware value whose instant UTC cannot spell, before `0001-01-01T00:00:00Z` or past
`9999-12-31T23:59:59.999999Z`, SHALL be sealed with the offset it came with. Text SHALL be
sealed as the engine gave it, without the blanks around it.

### Scenario: one instant through any zone the driver chose

- **GIVEN** `2026-08-25T06:30:00Z` handed back by clickhouse-connect naive, aware in UTC, aware
  at `+03:00` or aware at `-04:00`, or by psycopg in a UTC or a Moscow session
- **WHEN** the probe reads it as the newest event time
- **THEN** it seals `2026-08-25T06:30:00Z`
- **AND** the same instant with 120000 microseconds, aware or naive on ClickHouse, is sealed as
  `2026-08-25T06:30:00.120000Z`

### Scenario: a clock with no zone and a day keep their form

- **GIVEN** a PostgreSQL `timestamp` of `2026-08-25T04:00:00`, or of the same with 500000
  microseconds, and a `date` of `2026-08-24` on either engine
- **WHEN** the probe reads it
- **THEN** it seals `2026-08-25T04:00:00`, `2026-08-25T04:00:00.500000` and `2026-08-24`

### Scenario: an instant UTC cannot spell

- **GIVEN** a `timestamptz` handed back as `9999-12-31T23:00:00-03:00` or as
  `0001-01-01T01:00:00+03:00`
- **WHEN** the probe reads it and `dwh.freshness` grades it
- **THEN** the probe seals that text, and the check reports `freshness.unreadable_event_time`

## Requirement: the check reads three forms, by the form and the calendar

`dwh.freshness` SHALL read a newest event time in one of three forms and no other:
- a day: four digits of the year, `-`, two of the month, `-`, two of the day;
- a clock with no zone: a day, `T`, two digits each of the hour, the minute and the second
  between `:`, and optionally `.` and the digits of a fraction;
- a UTC moment: a clock followed by `Z`, which is the `Timestamp` form.

The digits SHALL be the ASCII digits `0` to `9`. The day SHALL be a day of the Gregorian
calendar. The hour SHALL be `00` to `23`, and the minute and the second `00` to `59`. Whether a
text is of one of the forms SHALL be decided by the form and the calendar, never by
`datetime.fromisoformat`. Digits of a fraction after the sixth SHALL NOT be counted. A UTC moment
SHALL be read as the instant it names, whatever timezones the metrics declare. The run's
`created_at` SHALL be a `Timestamp`; any other text is a caller's error, raised before a reading
is compared with it.

Any other text SHALL be graded `freshness.unreadable_event_time` (review). Its detail SHALL name
the mart, the target, the text as a Python literal, the event-time column and the three forms.

### Scenario: the three forms are read

- **GIVEN** a newest event time `2026-08-24`, `2024-02-29`, `2026-08-25T04:00:00`,
  `2026-08-25T04:00:00.5`, `2026-08-25T06:30:00Z` or `2026-08-25T06:30:00.123456789Z`
- **WHEN** the check reads it
- **THEN** the first two are days, the next two are clocks of 04:00:00 and of 04:00:00.5 with no
  zone, and the last two are moments of 06:30:00 and 06:30:00.123456 in UTC

### Scenario: what `fromisoformat` read and the check does not

- **GIVEN** a newest event time `2026-W35-2`, `20260824`, `2026-08-25 04:00:00`,
  `2026-08-25T09:30:00+03:00`, `2026-08-25T06:30:00.123456abcZ`, `2026-08-25T06:30Z`,
  `2026-08-25T24:00:00`, `2025-02-29`, a day in Arabic-Indic digits, a day with a blank ahead or
  a line break behind, or `not a time`
- **WHEN** `dwh.freshness` grades it on `127.0.0.1:8124/dm`
- **THEN** it reports one `freshness.unreadable_event_time` (review), whose detail reads
  `dm.sales_daily on 127.0.0.1:8124/dm reports '<text>' as its newest date, which is none of
  the forms this check reads: a day YYYY-MM-DD, a clock YYYY-MM-DDTHH:MM:SS with no zone, or a
  UTC moment such as 2026-08-25T09:00:00Z`

### Scenario: the UTC form is the `Timestamp` form

- **GIVEN** every moment the tests of [UTC timestamps](utc-timestamps.md) take, and every text
  they refuse but the date alone
- **WHEN** each is read as a newest event time
- **THEN** each moment is read as the instant it names, and no refused text is read

### Scenario: one answer on every interpreter

- **GIVEN** a `datetime.fromisoformat` that reads every text, or one that reads none
- **WHEN** `2026-08-25T24:00:00`, `2026-08-25T24:00:00Z`, `2026-W35-2`, `2026-08-25T06:30:00Z`
  and `2026-08-25T01:00:00Z` are graded at `2026-08-25T09:00:00Z` against `PT6H`
- **THEN** the first three are unreadable, the fourth is fresh and the fifth is stale

### Scenario: the run's moment is a `Timestamp`

- **GIVEN** `created_at` `2026-08-25`, `2026-08-25T09:00:00` or `2026-08-25T12:00:00+03:00`
- **WHEN** a day or a UTC moment is graded against it
- **THEN** a `ValueError` says `created_at must be a Timestamp, not '<text>'`

## Requirement: a day or a clock with no zone is read in the one zone the metrics declare

A day and a clock with no zone SHALL be read in the timezone that the metrics reading the column
declare (`TableQuestion.timezones`) when they declare exactly one; a name declared by several
metrics SHALL count once. The name SHALL be one the gate host's zone database lists
(`zoneinfo.available_timezones()`), as it is written there. A clock SHALL be read as that wall
clock in the zone, and a day as 00:00 of that day in the zone: of the instants the day stands
for, the one that makes it oldest.

Where the zone skips or repeats the wall clock, it SHALL be read with `fold=0`, the offset the
zone had before its clocks moved: a repeated clock is its first instant, the older of the two,
and a skipped clock, which no instant of the zone has, takes the offset it had before the skip.
The same rule reads a day whose 00:00 the zone skips or repeats as the instant the day began,
but where a skip spans its 00:00 (Known boundaries).

When the metrics declare no timezone, several different ones, or one the zone database does not
list, a day or a clock SHALL be graded `freshness.event_time_zone_unknown` (review), and its age
SHALL NOT be graded: there is no silent UTC. The detail SHALL name the mart, the target, the text
as a Python literal, the event-time column, the form, and the declared zones as Python literals.

A day or a clock whose instant in the zone falls outside the years 0001 to 9999 of UTC SHALL be
graded `freshness.unreadable_event_time` (review), with a detail that names the zone.

When a day or a clock is stale, the detail of `freshness.stale` SHALL say what it was read as:
`read in <zone> as <moment>` for a clock, `read as 00:00 of that day in <zone>, <moment>` for a
day, the moment in the `Timestamp` form. A stale UTC moment SHALL name no reading.

### Scenario: a Moscow clock and a Moscow day

- **GIVEN** a metric that declares `Europe/Moscow`, the window `PT6H` and `created_at`
  `2026-08-25T09:00:00Z`
- **WHEN** `dwh.freshness` grades the reading `2026-08-25T04:00:00`
- **THEN** it reports `freshness.stale`, `8.0h before 2026-08-25T09:00:00Z, read in
  Europe/Moscow as 2026-08-25T01:00:00Z`
- **AND** `2026-08-25T10:00:00`, which read in UTC would be after the run, is two hours old and
  passes
- **AND** the day `2026-08-24` is stale, `36.0h before 2026-08-25T09:00:00Z, read as 00:00 of that
  day in Europe/Moscow, 2026-08-23T21:00:00Z`, and the day `2026-08-25` is stale against `PT10H`
  and fresh against `PT12H`
- **AND** `2026-08-25T01:00:00Z` is stale, with no reading named, whether the metrics declare
  no zone, `Europe/Moscow` and `UTC`, `Mars/Olympus` or `Europe/Moscow`
- **AND** `Europe/Moscow` declared twice reads as `Europe/Moscow` declared once

### Scenario: no one zone to read it in

- **GIVEN** a reading `2026-08-24` or `2026-08-25T04:00:00`, and metrics that declare no zone;
  `Europe/Moscow` and `UTC`; `Europe/Moscow` and `W-SU`; `Mars/Olympus`; `utc`;
  `europe/moscow`; `Europe/Moscow ` with a blank behind; or `Europe\Moscow`
- **WHEN** `dwh.freshness` grades it
- **THEN** it reports one `freshness.event_time_zone_unknown` (review), such as `dm.sales_daily
  on 127.0.0.1:8124/dm reports '2026-08-24' as its newest date, a day with no zone; the metrics
  that read it declare 2 timezones, 'Europe/Moscow', 'UTC', so the instant it names is not known
  and its age is not graded`, or, for a name the database does not list, `the metrics that read
  it declare 'utc', which the zone database this gate reads does not hold`
- **AND** `ZoneInfo` on Windows opens `utc`, `Europe/Moscow ` and `Europe\Moscow` all the same

### Scenario: a host with no zone database

- **GIVEN** a gate host whose zone database lists no name, and metrics that declare `UTC` or
  `Europe/Moscow`
- **WHEN** `dwh.freshness` grades `2026-08-24` or `2026-08-25T04:00:00`
- **THEN** it reports one `freshness.event_time_zone_unknown` (review) that names the declared
  zone, such as `the metrics that read it declare 'UTC', which the zone database this gate reads
  does not hold`
- **AND** `2026-08-25T01:00:00Z` is stale, with no reading named
- **AND** with metrics that declare `Europe/Moscow` and the clock `2026-08-25T04:00:00`, the same
  inputs run on it and on a host whose database lists `Europe/Moscow` have one run id and two
  evidence digests (Known boundaries)

### Scenario: a clock the zone skips or repeats

- **GIVEN** `Europe/Berlin`, which skips 02:30 on 2026-03-29 and repeats it on 2026-10-25, and
  `America/Havana`, which skips 00:00 on 2026-03-08 and repeats it on 2026-11-01
- **WHEN** `2026-03-29T02:30:00`, `2026-10-25T02:30:00`, `2026-03-08` and `2026-11-01` are graded
  at 12:00Z of their day against `PT6H`
- **THEN** each is stale, read as `2026-03-29T01:30:00Z` (the offset before the skip),
  `2026-10-25T00:30:00Z` (the first of the two), `2026-03-08T05:00:00Z` and
  `2026-11-01T04:00:00Z`, the instants those two days began

### Scenario: an instant UTC cannot spell

- **GIVEN** `0001-01-01` or `0001-01-01T01:00:00` in `Europe/Moscow`, whose offset then was
  +02:30:17, or `9999-12-31T23:00:00` in `America/Sao_Paulo`
- **WHEN** `dwh.freshness` grades it
- **THEN** it reports `freshness.unreadable_event_time`: `… a day that read in Europe/Moscow falls
  outside the years 0001 to 9999 of UTC, where no instant is read`

## Requirement: a reading after the run is review

A reading whose instant is after the run's `created_at` SHALL be graded
`freshness.event_time_ahead` (review), and its age SHALL NOT be graded: a source cannot be dated
after the run that reads it. Its detail SHALL name the text, the moment a day or a clock was read
as, and `created_at`. There SHALL be no tolerance. The code has no notion of clock skew to borrow
one from, and `provenance.capture_after_run` grades a capture after `created_at` with none
either. A reading at the instant of `created_at` is not after it.

### Scenario: after the run by a moment, a clock and a day

- **GIVEN** `created_at` `2026-08-25T09:00:00Z` and a reading `2026-08-25T10:00:00Z`;
  `2026-08-25T09:00:00.000001Z`; `2026-08-25T13:00:00` in `Europe/Moscow`; `2026-08-26` in
  `Pacific/Kiritimati`, which is UTC+14; or `9999-12-31` in `America/Sao_Paulo`
- **WHEN** `dwh.freshness` grades it
- **THEN** it reports one `freshness.event_time_ahead` (review), such as `dm.sales_daily on
  127.0.0.1:8124/dm reports '2026-08-26' as its newest date, read as 00:00 of that day in
  Pacific/Kiritimati, 2026-08-25T10:00:00Z, after the run's created_at 2026-08-25T09:00:00Z: a
  source cannot be dated after the run that reads it, so its age is not graded`
- **AND** `2026-08-25T09:00:00Z`, `2026-08-25T09:00:00.0000009Z` and `2026-08-25T12:00:00` in
  `Europe/Moscow` are no finding

## Requirement: one instant is one bundle

Two runs of the same inputs whose engines hand back one instant through two zones SHALL seal the
same evidence. The second run into the same evidence directory therefore finds the first one's
bundle and is not refused.

### Scenario: three driver values of one instant

- **GIVEN** the inputs of a run and a ClickHouse reading of `2026-08-25T06:30:00Z` handed back
  aware in UTC, aware at `+03:00`, and naive
- **WHEN** the gate runs with each into one evidence directory
- **THEN** the three reports have one run id and one evidence digest

## Requirement: the gate that reads them so is a new verifier

The probe seals another text for the same instant in the facts of every `dwh.*` check, and
`dwh.freshness` grades days and clocks with no zone in another zone, and readings after the run,
under new codes. So `CHECK_VERSION` went from 79 to 80, and with it `VERIFIER_VERSION` from 119
to 120, as DECISIONOPS_ARCHITECTURE §7.6 has it; a later gate SHALL hold at least these.
`NATIVE_SOURCE_VERSION` SHALL stay 3: no snapshot changes.

## Known boundaries

- **The zone database is the gate host's.** On Windows it is the `tzdata` package the lock pins
  (2026.2, IANA 2026b), which comes in only through clickhouse-connect and psycopg, and only on
  Windows; on macOS and Linux it is the system's (`zoneinfo.TZPATH`). A host with no zone
  database at all, neither a system one nor the `tzdata` package, as a slim container image can
  be, lists no name: every day and every clock grades `freshness.event_time_zone_unknown`, a
  declared `UTC` included, and a UTC moment is read as anywhere. Two hosts whose databases
  differ can read one clock as two instants, where a zone's rules changed between their versions,
  or one of them can lack a zone and grade `freshness.event_time_zone_unknown`. The detail names
  the instant read; the database and its version are not sealed and do not move the run id, so
  where the difference changes the evidence such hosts seal two bundles under one run id, and
  one evidence store keeps the bundle it took first and refuses the other (`same idempotency
  key, different evidence`).
- **Names as the database writes them.** `W-SU` and `Europe/Moscow` are one zone in the
  database, and declared together they are two names, so no one zone.
- **A day whose 00:00 a skip spans.** In tzdata 2026b, from 1900 to 2100 and looking at the
  days around each change of offset seen a week apart, `fold=0` reads 00:00 as the first instant
  of its day on 798 days whose 00:00 a zone repeats and on 4223 whose 00:00 it skips, and not on
  6: 1919-03-31 in `America/Toronto` and in the five names that share its history there. Its
  clocks moved from 23:30 to 00:30 on the evening before, so the day began at 04:30Z, and its
  00:00 is read as 05:00Z, half an hour younger. Two changes within one week that come back to
  the same offset were not looked at.
- **PostgreSQL reads a repeated clock as its later instant.** Asked live (PostgreSQL 16.15 on
  macOS), `'2026-10-25 02:30:00'::timestamp AT TIME ZONE 'Europe/Berlin'` is
  `2026-10-25T01:30:00Z`, and the check reads that clock as `00:30Z`, an hour older. In such an
  hour the check can call a mart stale that the engine's instant keeps in its window, and it
  calls a clock after the run only where the older instant is. A skipped clock the two read
  alike: `2026-03-29 02:30:00` is `01:30Z` to both. The check keeps one rule for both.
- **A window in years or months is named first.** With `P1M`, `dwh.freshness` reports
  `freshness.window_not_comparable` and reads no reading: an unreadable text, a reading with no
  one zone or one after the run is not reported then.
- **Text in one of the forms is read in that form.** The check reads the sealed text, not the
  column's type: a text column that holds `2026-08-25T04:00:00` is read as that clock in the
  declared zone, and one that holds `2026-08-25 04:00:00` is unreadable. The probe does not
  reinterpret the engine's text.
- **Microseconds.** A ClickHouse `DateTime64(9)` reaches the probe to the microsecond:
  clickhouse-connect drops the digits after the sixth. A reading less than a microsecond after
  `created_at` is not after it.
- **Bundles sealed before.** `decision evidence` checks an artifact by its digest and size, and
  reads no moment in it and grades nothing again. A bundle whose `dwh.*` facts hold
  `2026-08-25T09:30:00+03:00`, or a ClickHouse clock with no `Z`, reads as before; so does the
  pilot bundle of `docs/pilot/2026-08-25-ch-control`, which read the day `2026-08-24` as
  `2026-08-24T00:00:00Z` and which today's check reads as `2026-08-23T21:00:00Z` in
  `Europe/Moscow`. With the raised `VERIFIER_VERSION`, a run of the same inputs has another run
  id, so it never meets such a bundle in one evidence directory.
- **What the engines were asked.** Live on macOS, through clients opened as `make_run_query` and
  `make_run_query_pg` open them: ClickHouse 24.8.14.39 with its server zone UTC, then
  `Europe/Moscow`, hands `max()` of a `DateTime` back naive, then aware in the server's zone, and
  the probe seals `2026-08-25T06:30:00Z` both times; a `DateTime64(3, 'Europe/Moscow')` comes
  back aware and is sealed `2026-08-25T06:30:00.120000Z`, a `Date` as a date. PostgreSQL 16.15,
  in a session in UTC and then in `Europe/Moscow`, hands a `timestamp` back naive, sealed as the
  clock `2026-08-25T04:00:00`, a `timestamptz` aware in the session's zone, sealed
  `2026-08-25T01:00:00Z` both times, and a `date` as a date. Greengage was not asked.
