# Capability: A moment evidence carries has one RFC 3339 spelling

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

A `Timestamp` (`auto_bi/scalars.py`) is the moment of every record that evidence carries:
- the manifest of a bundle and its signature;
- the attestation of a recorded result;
- the review of a metric formula and of a verified asset;
- the report of a gate;
- the data of an offline investigation;
- what `--created-at` and `--captured-at` state.

The scalar promised RFC 3339 UTC. It took any text that ended in `Z`, held a `T`, and that
`datetime.fromisoformat` read with `+00:00` in place of the `Z`. That parser reads forms of
ISO 8601 that RFC 3339 has not, so a moment could have no seconds (`2026-08-25T09:00Z`), no
separators (`20260825T090000Z`), a week (`2026-W35-2T09:00:00Z`) or a comma before the
fraction. When an offset follows, CPython's parser stops reading a fraction at its sixth digit.
Whatever stood between that digit and the `Z` was taken: a word, a control, a line break followed
by a line that reads like a key of the document.

Python 3.14 reads `24:00:00` and 3.13 does not. So whether a bundle held a moment depended on the
interpreter that read it.

Reproduced on HEAD `3e75954` (Python 3.13 and 3.14):
- a recorded result attested at `2026-08-25T08:00:00.123456`, a line break and `verdict: passZ`
  was taken by `decision verify`, and its attestation was sealed with that second line;
- a verified asset or a metric contract reviewed at `2026-08-20T10:00Z` passed;
- `--created-at 2026-08-25T09:00:00.123456abcZ` was a moment of the manifest.

## Requirement: a moment is text of one spelling

A `Timestamp` SHALL be text of this form and no other:
- four digits of the year, `-`, two of the month, `-`, two of the day;
- `T`;
- two digits of the hour, `:`, two of the minute, `:`, two of the second;
- optionally, `.` and one or more digits of a fraction of the second;
- `Z`.

The digits SHALL be the ASCII digits `0` to `9`, and the `T` and the `Z` SHALL be upper case.
The date SHALL be a day of the Gregorian calendar from `0001-01-01` to `9999-12-31`. The hour
SHALL be `00` to `23`, and the minute and the second `00` to `59`. This is RFC 3339's
`date-time` with the offset `Z`; what RFC 3339 allows beyond it is listed under known
boundaries.

Text of that form SHALL be taken as it is written. It is not normalized, and a fraction of any
length is kept as written.

Whether a text is a `Timestamp` SHALL be decided by the form and the calendar, never by what
`datetime.fromisoformat` reads. So the answer is the same on every interpreter the project runs
on.

Any other value SHALL be refused with `must be an RFC 3339 UTC timestamp such as
2026-08-25T09:00:00Z`. Some values are a date, or a date and time, and not text: this is what YAML
builds of an unquoted `2026-08-25T09:00:00Z` or `2026-08-25`. Such a value SHALL be refused with
`must be an RFC 3339 UTC timestamp as text, such as '2026-08-25T09:00:00Z' in quotes: YAML reads
an unquoted one as a date`.

### Scenario: what was taken after the sixth digit

- **GIVEN** `2026-08-25T09:00:00.123456abcZ`, the same with a line break and a line before the
  `Z`, with a control after a seventh digit, or with a word, a blank or a digit of another script
  after the sixth
- **WHEN** it is validated as a `Timestamp`
- **THEN** it is refused with `must be an RFC 3339 UTC timestamp such as 2026-08-25T09:00:00Z`

### Scenario: what ISO 8601 has and RFC 3339 has not

- **GIVEN** a moment with no seconds, the hour alone, the basic form of the time or of the date and
  time, a week and a day, a comma for the point, a point with no digit, or a blank for the `T`
  with a `T` before the `Z`
- **WHEN** it is validated
- **THEN** it is refused with the same words

### Scenario: one answer on every interpreter

- **GIVEN** `2026-08-25T24:00:00Z`, which Python 3.14's `datetime.fromisoformat` reads and 3.13's
  does not
- **WHEN** it is validated on either
- **THEN** it is refused, as are the minute 60, the second 60, a day the calendar does not have
  (`2025-02-29`, `1900-02-29`), the month 13, the year 0000, a year of five digits or with a sign,
  digits of another script (Arabic-Indic, fullwidth), an offset for the `Z`, a small `t` or `z`,
  two `Z`, and a blank or a line break around the text

### Scenario: RFC 3339 UTC moments are taken as written

- **GIVEN** a moment to the second; with a fraction of one digit, of nought, with zeros behind it
  (`.500`), to the microsecond, to the nanosecond, or of 400 digits; on a leap day
  (`2024-02-29`, `2000-02-29`); or the first second and the last moment there are
- **WHEN** it is validated
- **THEN** it is taken, and the text it is taken as is the text given

### Scenario: an unquoted moment in YAML

- **GIVEN** a YAML document with `at: 2026-08-25T09:00:00Z`, `at: 2026-08-25T09:00:00.5Z`,
  `at: 2026-08-25 09:00:00` or `at: 2026-08-25`, unquoted
- **WHEN** the value YAML builds of it, a date, is validated as a `Timestamp`
- **THEN** it is refused with `must be an RFC 3339 UTC timestamp as text, such as
  '2026-08-25T09:00:00Z' in quotes: YAML reads an unquoted one as a date`
- **AND** `at: '2026-08-25T09:00:00Z'` is taken

## Requirement: each record refuses another spelling as it refuses any value of its schema

A record that holds a moment of another spelling SHALL be refused where it was refused for any
value its schema does not take. Nothing else changes about those refusals.

- **Stated moment.** `decision verify --created-at` and `decision capture --captured-at` stop
  with a usage error, exit 2, before anything is read or written
  ([stated run facts](stated-run-facts.md)). The words are `<flag> must be an RFC 3339 UTC
  timestamp such as 2026-08-25T09:00:00Z`.
- **Attestation.** For an attestation of a recorded result, `decision verify` stops with
  `<file> holds an invalid recorded result for '<asset>': attestation.captured_at: Value error,
  must be …`, exit 2, and writes no evidence.
- **Verified asset.** For the review of a verified asset, `decision verify` and
  `decision capture` stop with `<file> is not a valid verified asset: review.reviewed_at: Value
  error, must be …`, exit 2.
- **Metric contract.** The review of a metric formula (`formula.review.reviewed_at`) gives
  different results depending on where the contract is:
  - for a contract among the validated paths, `decision validate` reports `contract.invalid`
    (block), and the run blocks on `contracts.validate`;
  - for a contract of a semantic ref, the ref carries the blocking gap `gap.contract_invalid`,
    and the run blocks on `semantic.gaps`.
- **Investigation data.** For the canonical result an offline investigation reads
  (`observed_at`, `source_max_event_time`), `CanonicalMetricResult` refuses it.
- **Manifest.** A manifest whose `created_at`, or whose signature's `signed_at`, is of another
  spelling is not a valid manifest (see the known boundaries).

### Scenario: an attestation with a second line in its moment

- **GIVEN** a recorded result attested at `2026-08-25T08:00:00.123456`, a line break,
  `verdict: passZ`
- **WHEN** `decision verify` runs
- **THEN** it exits 2 with `error: <file> holds an invalid recorded result for
  'asset://sales/weekly_margin_by_region': attestation.captured_at: Value error, must be an RFC
  3339 UTC timestamp such as 2026-08-25T09:00:00Z` as the one line on stderr, prints nothing on
  stdout and creates no evidence directory

### Scenario: a result attested to the nanosecond

- **GIVEN** a recorded result attested at `2026-08-25T08:59:59.999999999Z`, a run of
  `2026-08-25T09:00:00Z`
- **WHEN** `decision verify` runs
- **THEN** `results.provenance` passes, and its evidence holds the moment as it was written

### Scenario: reviewed with no seconds

- **GIVEN** a verified asset reviewed at `2026-08-20T10:00Z`
- **WHEN** `decision verify` or `decision capture` runs
- **THEN** each stops with `<file> is not a valid verified asset: review.reviewed_at: Value error,
  must be an RFC 3339 UTC timestamp such as 2026-08-25T09:00:00Z`
- **AND** with `reviewed_at: 2026-08-20T10:00:00Z` unquoted, the words are the ones of a date
  YAML built

### Scenario: a metric formula reviewed with no seconds

- **GIVEN** a `reviewed_sql` metric contract reviewed at `2026-08-21T09:00Z`
- **WHEN** it is among the validated paths
- **THEN** `decision validate` reports `contract.invalid` (block) naming
  `formula.review.reviewed_at`, and `decision verify` blocks on `contracts.validate`
- **AND** when it is the contract of the candidate ref, `semantic.gaps` blocks on
  `gap.contract_invalid` at `contracts.gross_margin_pct.yaml`

## Requirement: the gate that refuses them is a new verifier

A contract that passed now blocks, so the same input can give a different verdict.
`VERIFIER_VERSION` SHALL be 115 and `CHECK_VERSION` 78, because `contracts.validate` and
`semantic.gaps` grade such a contract differently (DECISIONOPS_ARCHITECTURE §7.6).
`NATIVE_SOURCE_VERSION` SHALL stay 3: the graph of a snapshot is not changed.

## Known boundaries

- **What RFC 3339 allows and this type refuses.** RFC 3339 also allows a small `t` and `z`, an
  offset other than `Z`, and the leap second `23:59:60`. These are refused, as before: evidence
  holds one spelling of UTC, and Python's `datetime` has no 61st second to count an age from.
  The year `0000`, which RFC 3339 allows, is refused for the same reason.
- **Two texts for one instant.** A moment is not normalized: `09:00:00Z`, `09:00:00.0Z` and
  `09:00:00.000Z` name one instant and are three texts. Two contracts that differ only so hash
  differently. Decimal strings are normalized (DECISIONOPS_ARCHITECTURE §7.1); moments are kept
  as stated.
- **Ages to the microsecond.** An age is counted to the microsecond. Digits of a fraction after
  the sixth are kept in the text and not counted, so two moments that differ only there are one
  moment for an age.
- **Bundles sealed before T-118.** A bundle whose manifest holds a moment of another spelling —
  its `created_at`, or its signature's `signed_at` — is no longer read:
  - `decision evidence` reports `evidence.manifest_invalid` (block, exit 4);
  - `decision runs` cannot list its store: `cannot discover runs at <its manifest>:
    ValidationError`, exit 2.

  The commands sealed such a moment only when one was stated, because the clock's moment they
  take by default is `YYYY-MM-DDTHH:MM:SSZ`. The pilot bundles of the repository read as before.

  A moment inside an artifact is not read again: `decision evidence` checks an artifact by its
  digest and size. A bundle whose manifest is of the one spelling reads as before, even when
  the payload of `results.provenance` holds a result attested at `2026-08-25T08:00:00,000Z`.
- **Times that are not a `Timestamp`.** Two kinds of time are not read as one, and are read as
  before:
  - the newest event time a DWH reports for `dwh.freshness`, which the engine spells;
  - the `captured_at` of a semantic model's physical statistics.
