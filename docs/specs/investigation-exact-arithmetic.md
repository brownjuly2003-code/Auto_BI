# Capability: Exact arithmetic of the offline investigation skills

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

DECISIONOPS_ARCHITECTURE §3 (invariant 9) has one input give one content hash, and §12.1 has
`decision investigate` run four version-pinned pure skills over supplied canonical results. A
canonical result carries its values as plain decimal strings of any length
(`auto_bi/scalars.py`), and the skills computed with them through the decimal arithmetic of
the calling thread: 28 significant digits unless a caller set otherwise. So a sum of the supplied
values was not always the sum of the supplied values, and what a skill observed depended on
things that are not its input.

Reproduced at commit `e97eafc` (2026-10-04) with the plan
`tests/fixtures/investigation/weekly_margin.yaml` and a result of one dimension, `category`:

- one group holding `10000000000000000000000000000` and then `10000000000000000000000000001`
  (29 digits): `period_variance` answered `current_value` `10000000000000000000000000000` and
  `absolute_delta` `0`, and the brief carried the claim `investigation.period_variance` with the
  value `0`;
- a group moving from `1` to `3` beside a group holding `10000000000000000000000000000` in both
  periods: both totals came out as `10000000000000000000000000000`, `total_delta` as `0`, the
  contribution of the small group as `2`, `reconciled` as false and every share as undefined;
- three groups — `10000000000000000000000000000`, `-10000000000000000000000000000` and one moving
  from `5` to `7` — answered `5` → `7` with the rows written A, B, C and `0` → `10` with the same
  rows written A, C, B;
- with the caller's decimal precision at 6, group values came out shortened (`0.123457`); with
  the caller trapping inexact results, `decimal.Inexact` came out of every numeric skill for the
  values `100`, `0.5` → `70`, `1.25`; a numeric skill left the flags `Inexact` and `Rounded` raised
  in the caller's context;
- one value of 5000 digits in a row, or a freshness window written `PT` + 5000 zeros + `6H`,
  ended `decision investigate` with an unhandled `ValueError` (the interpreter converts at most
  4300 digits between an integer and text by default) and no output directory;
- an age of `7200.000001` seconds against the window `PT2H`, with the caller's precision at 6,
  was reported by `data_health` as `pass` with the age `7200`.

A value quoted in a scenario below may be wrapped across lines of this file; each is one string,
with nothing where it wraps here.

## Requirement: an investigation skill answers from the supplied values alone, exactly

**Sums and differences.** `period_variance`, `contribution_decomposition` and
`concentration_shift` SHALL compute every sum and every difference of supplied values exactly:
the result is the mathematical sum or difference of the decimal strings supplied, whatever the
number of digits they or the result carry. This covers `comparison_value`, `current_value` and
`absolute_delta` of `period_variance`; `comparison_value`, `current_value`, `total_delta` and
every contribution's `comparison_value`, `current_value` and `delta` of
`contribution_decomposition`; `total` and every leader's `value` of `concentration_shift`.

**Order.** Contributions SHALL stay ordered by the absolute value of their `delta`, largest
first, then by `dimension_value`; leaders by their `value`, largest first, then by
`dimension_value`. Both orders SHALL be decided by the exact values.

**Ratios.** `relative_delta`, `share_of_total_delta`, `share`, `hhi`, `top_n_share`, `hhi_delta`
and `top_n_share_delta` SHALL be the exact rational value computed from those exact sums and
written with at most six fractional digits, a tie going to the even neighbour — the scale and the
rule they had before. `reconciled` SHALL say whether the contributions add up to `total_delta`
exactly.

**Nothing but the input.** An output and its digest SHALL depend on the step and the canonical
result alone:

- not on the decimal context of the caller — its precision, its rounding mode, its traps — which
  a skill SHALL neither read nor change, its flags included;
- not on the order in which the rows of the result are written;
- and a value of any length SHALL be answered: no limit of the interpreter on converting an
  integer to or from text may end a skill.

**Freshness.** `data_health` SHALL take the age of a result as the exact difference between
`observed_at` and `source_max_event_time` in seconds, to the microsecond, and SHALL compare it
with the declared window in exact integers. The window SHALL be read by the parser the release
gate reads its windows with (`auto_bi.scalars.duration_seconds`): a fixed-length duration is its
seconds however long it is written, and a duration with a year or a month has no fixed length and
leaves `freshness` as `unknown` with no age, as before.

**Versions.** `period_variance`, `contribution_decomposition` and `concentration_shift` SHALL run
at `skill_version` 2, and `data_health` at `skill_version` 1: under the default decimal context
`data_health` answers every input it answered before with the same output and digest.

- A step of a plan SHALL declare the version its skill runs at. Another version SHALL make the
  plan invalid with `skill_version for <skill> must be <n>`, where `<n>` is the version the skill
  runs at.
- An output SHALL carry the version of its skill, and a gap the version of the step it answers.
- `compile_brief` SHALL refuse an output whose `skill_version` differs from its step's, with
  `output for step <step_id> has skill_version <a>, expected <b>`.
- `VERIFIER_VERSION` and `CHECK_VERSION` of the release gate SHALL stay as they are: an
  investigation output is not part of a release evidence bundle.

Typed gaps, budgets, the six-place scale, the `exploratory` trust of a brief and the prohibition
of causal claims are unchanged.

### Scenario: a difference of one between 29-digit values is observed

- **GIVEN** group A holding `10000000000000000000000000000` in the comparison period and
  `10000000000000000000000000001` in the current one
- **WHEN** `period_variance` and `contribution_decomposition` run
- **THEN** `period_variance` answers `comparison_value` `10000000000000000000000000000`,
  `current_value` `10000000000000000000000000001`, `absolute_delta` `1` and `relative_delta` `0`
- **AND** `contribution_decomposition` answers `total_delta` `1` and one contribution with
  `delta` `1` and `share_of_total_delta` `1`, `reconciled` true
- **AND** the brief built from the fixture plan carries the claim
  `investigation.period_variance` with the value `1`, the block body
  `Наблюдаемое значение: 10000000000000000000000000000 RUB -> 10000000000000000000000000001 RUB;
  delta 1 RUB; relative 0.` (one line, a single space where it wraps here) and, for the
  contribution, the two lines `Общая наблюдаемая дельта: 1 RUB; reconciliation=pass.` and
  `- A: 1 RUB; share 1`

### Scenario: a small group beside a large one keeps its change

- **GIVEN** group A holding `10000000000000000000000000000` in both periods and group B moving
  from `1` to `3`
- **WHEN** the three numeric skills run
- **THEN** `period_variance` answers `10000000000000000000000000001` →
  `10000000000000000000000000003`, `absolute_delta` `2`, `relative_delta` `0`
- **AND** `contribution_decomposition` answers the same two totals, `total_delta` `2`, the
  contributions B (`1`, `3`, `delta` `2`, share `1`) and then A (`delta` `0`, share `0`),
  `reconciled` true
- **AND** `concentration_shift` answers the totals `10000000000000000000000000001` and
  `10000000000000000000000000003`, the leaders A (share `1`) and B (value `1`, then `3`; share
  `0`), `hhi` `1` and `top_n_share` `1` in both periods and both deltas `0`

### Scenario: fractional digits past the 28th significant digit are kept

- **GIVEN** group A moving from `0.1234567890123456789012345678` to
  `0.1234567890123456789012345679` and group B holding `1000000` in both periods
- **WHEN** `period_variance` and `contribution_decomposition` run
- **THEN** `period_variance` answers `1000000.1234567890123456789012345678` →
  `1000000.1234567890123456789012345679`, `absolute_delta` `0.0000000000000000000000000001`,
  `relative_delta` `0`
- **AND** `contribution_decomposition` answers `total_delta` `0.0000000000000000000000000001`,
  the contributions A (that `delta`, share `1`) and then B (`delta` `0`, share `0`), `reconciled`
  true

### Scenario: the order of rows does not change an observation

- **GIVEN** group A holding `10000000000000000000000000000` and group B holding
  `-10000000000000000000000000000` in both periods, group C moving from `5` to `7`, once with the
  rows of each period written A, B, C and once written A, C, B
- **WHEN** `period_variance` and `contribution_decomposition` run over each result
- **THEN** both answer `5` → `7`, a delta of `2`, `relative_delta` `0.4`, the contributions C
  (`delta` `2`, share `1`), A and B (`delta` `0`, share `0`), `reconciled` true
- **AND** the outputs for the two results have equal digests

### Scenario: contributions are ranked by their exact change

- **GIVEN** group A moving from `0` to `10000000000000000000000000001` and group B from `0` to
  `10000000000000000000000000002`
- **WHEN** `contribution_decomposition` runs
- **THEN** `current_value` and `total_delta` are `20000000000000000000000000003`, and the
  contributions are B (`delta` `10000000000000000000000000002`, share `0.5`) and then A (`delta`
  `10000000000000000000000000001`, share `0.5`), `reconciled` true

### Scenario: leaders are ranked by their exact value

- **GIVEN** in the comparison period A `10000000000000000000000000001`, B
  `10000000000000000000000000002`, C `10000000000000000000000000003`; in the current period A
  `10000000000000000000000000003`, B `10000000000000000000000000002`, C
  `10000000000000000000000000001`; `top_n` 2
- **WHEN** `concentration_shift` runs
- **THEN** both totals are `30000000000000000000000000006`; the comparison leaders are C
  (`10000000000000000000000000003`) and B (`10000000000000000000000000002`), the current leaders
  A (`10000000000000000000000000003`) and B; every leader's share is `0.333333`, `hhi` is
  `0.333333` and `top_n_share` `0.666667` in both periods, both deltas `0`

### Scenario: the caller's decimal context changes nothing and is left as it was

- **GIVEN** the result `tests/fixtures/investigation/revenue_results.yaml` and the result of the
  scenario with the small group beside the large one, and the calling thread's decimal context
  set in turn to: a precision of 2; a precision of 6; a precision of 50 with rounding `ROUND_UP`;
  the default precision with `Inexact` trapped
- **WHEN** each of the four skills runs over each result under each context
- **THEN** every output equals the output under the default context, digest included
- **AND** after a run the caller's context has the precision, rounding mode and traps it had, and
  no flag raised

### Scenario: a trapped inexact result does not end a ratio

- **GIVEN** group A moving from `3` to `4`, the caller trapping `Inexact`
- **WHEN** `period_variance` runs
- **THEN** it answers `absolute_delta` `1` and `relative_delta` `0.333333`

### Scenario: ratios keep six places with a tie going to the even neighbour

- **GIVEN** group A at `2000000` in the comparison period
- **WHEN** `period_variance` runs for the current values `2000001`, `2000003`, `2000005`,
  `1999999` and `1999997`
- **THEN** `relative_delta` is `0`, `0.000002`, `0.000002`, `0` and `-0.000002`
- **AND** `20000000` → `20000011` gives `0.000001`, `100` → `101` gives `0.01`, and `2000000` →
  `2000001` gives `0` under a caller's rounding mode of `ROUND_UP` as well

### Scenario: a value longer than the interpreter converts in one step is answered

- **GIVEN** `N`, the value written as 5000 nines
- **WHEN** `period_variance` runs for group A moving from `7` to `N`
- **THEN** `current_value` is `N`, `absolute_delta` is 4999 nines followed by `2`, and
  `relative_delta` is `142857` written 833 times followed by `13.142857`
- **AND** for group A moving from `N` to `7`, `absolute_delta` is `-` followed by 4999 nines and
  `2`, and `relative_delta` is `-1`
- **AND** for group A holding `N` in both periods and group B moving from `7` to `8`,
  `contribution_decomposition` answers `comparison_value` `1` followed by 4999 zeros and `6`,
  `current_value` `1` followed by 4999 zeros and `7`, `total_delta` `1`, the contributions B
  (`delta` `1`, share `1`) and then A (`delta` `0`, share `0`), `reconciled` true, and
  `concentration_shift` answers the leaders A (share `1`) and B (share `0`) with `hhi` `1`
- **AND** for group A moving from `0.` + 4999 zeros + `1` to `1`, `absolute_delta` is `0.`
  followed by 5000 nines and `relative_delta` is `N`

### Scenario: `decision investigate` writes its artifacts for a long value

- **GIVEN** the fixture plan and the fixture result with the current value of group A written as
  5000 nines
- **WHEN** `decision investigate` runs
- **THEN** it exits `0` and the output directory holds `plan.json`, `results.json`, `brief.json`
  and `brief.md`

### Scenario: the age of a result is exact under any decimal context

- **GIVEN** `source_max_event_time` `2026-09-04T10:00:00Z`, the window `PT2H`, and `observed_at`
  `2026-09-04T12:00:00.000001Z`
- **WHEN** `data_health` runs under the default context, a precision of 2, a precision of 6, a
  precision of 6 with rounding `ROUND_UP`, and with `Inexact` trapped
- **THEN** every run answers `freshness` `stale` and `freshness_age_seconds` `7200.000001`
- **AND** `observed_at` `2026-09-04T11:59:59.999999Z` answers `pass` with `7199.999999`,
  `2026-09-04T12:00:00Z` answers `pass` with `7200`, and a `source_max_event_time` one
  microsecond after `observed_at` answers `future` with `-0.000001`, under each of those contexts

### Scenario: a freshness window of any written length is read

- **GIVEN** `observed_at` `2026-09-04T12:00:00Z` and `source_max_event_time`
  `2026-09-04T10:00:00Z`
- **WHEN** `data_health` runs for the windows `PT` + 5000 zeros + `6H`, `PT` + 5000 zeros + `2H`,
  `PT` + 5000 zeros + `1H`, `PT` + 5000 nines + `S` and `P1W2DT3H4M5S`
- **THEN** it answers `pass`, `pass`, `stale`, `pass` and `pass`, each with the age `7200`
- **AND** the windows `P1M`, `P1Y2M` and `P0Y1D` answer `unknown` with no age
- **AND** `decision investigate` over the fixture result with the window `PT` + 5000 zeros +
  `6H` exits `0` and writes its four artifacts

### Scenario: ordinary values answer as before, at the new version

- **GIVEN** the fixture plan and `tests/fixtures/investigation/revenue_results.yaml`
- **WHEN** the four skills run
- **THEN** `period_variance` answers `200` → `180`, `absolute_delta` `-20`, `relative_delta`
  `-0.1`; `contribution_decomposition` answers the contributions A (`-30`, share `1.5`), B (`10`,
  share `-0.5`) and C (`0`, share `0`), `reconciled` true; `concentration_shift` answers `hhi`
  `0.375` → `0.339506`, `top_n_share` `0.75` → `0.722222`, `hhi_delta` `-0.035494` and
  `top_n_share_delta` `-0.027778` — each with `skill_version` 2
- **AND** `data_health` answers with `skill_version` 1 and the digest
  `sha256:80a0370edd1278f29b5a2aa34e07134bcaf067cdb07deb16d9778da1887f2bf5`, the digest it had
  before this capability
- **AND** equal values (`5` → `5`) answer `absolute_delta` `0`, `relative_delta` `0` and an
  undefined share

### Scenario: a plan declares the version each skill runs at

- **GIVEN** the fixture plan, which declares `data_health` at version 1 and the three numeric
  skills at version 2
- **WHEN** a step is changed to another version and the plan is validated
- **THEN** `period_variance`, `contribution_decomposition` or `concentration_shift` at version 1
  is refused with `skill_version for <skill> must be 2`, and `data_health` at version 2 with
  `skill_version for data_health must be 1`
- **AND** the unchanged fixture plan is valid

### Scenario: an output carries the version of its skill and a gap the version of its step

- **GIVEN** the fixture plan
- **WHEN** the four skills run over the fixture result, and over the same result marked incomplete
- **THEN** the outputs carry `skill_version` 1 for `data_health` and 2 for the numeric skills
- **AND** the three gaps `input.incomplete` of the numeric skills carry `skill_version` 2

### Scenario: a brief refuses an output of another skill version

- **GIVEN** the fixture plan and its four outputs, the output of the step `variance` replaced by
  a gap carrying `skill_version` 1
- **WHEN** `compile_brief` runs
- **THEN** it raises `BriefCompilationError` with
  `output for step variance has skill_version 1, expected 2`

### Scenario: `decision investigate` refuses a plan pinning a numeric skill at version 1

- **GIVEN** the fixture plan with every step at `skill_version` 1
- **WHEN** `decision investigate` runs
- **THEN** it exits `2`, writes no output directory, and its error names
  `skill_version for period_variance must be 2`

## Known boundaries

- A ratio is still written with six fractional digits: two different ratios may read the same,
  and one below half a millionth reads `0`.
- A timestamp is read to the microsecond; finer fractions of a second are not part of an age.
- A value has to be supplied as a decimal string. A number written in YAML without quotes is
  refused as before (`must be a decimal string`); a bare YAML integer longer than the interpreter
  converts in one step ends the YAML reader itself with an error `decision investigate` does not
  handle. The reader is the one every command of the release gate loads its inputs with, and it
  is not part of this capability.
- The time a skill takes is not bounded by the length of a value: `max_runtime_ms` is checked
  between steps and does not interrupt one.
- A `results.json` or `brief.json` written before this capability keeps its numeric outputs at
  `skill_version` 1; nothing reads a stored artifact back, and none is rewritten.
- `results.reconcile` of the release gate has its own exact comparison and is not touched here.
