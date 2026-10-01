# Capability: Reconciliation of a supplied result against its expected result

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

`results.reconcile` decides whether the number a candidate run produced is the number a verified
asset expects (DECISIONOPS_ARCHITECTURE §8.4). A result is a table of named, typed columns:
the schema comparison already reads it by name, so two results with the same columns in another
order have the same schema. Everything after the schema — which row is which, which value is
compared with which, which row a broken aggregate invariant is reported on — has to read the
table the same way. Pairing cells by position instead turns a reordered candidate into a
reconciled number it does not contain, or into a block on a number it does contain. Native
capture is a separate boundary: it refuses a reviewed query whose output columns are not in the
asset's order, and that refusal stays.

## Requirement: a candidate value is compared with the expected value of the same column

Once the schema comparison has found the same column names, logical types, nullability and units
in both results, the system SHALL pair every candidate value with the expected value of the
column of the same name, wherever that column stands in either result. A column permutation alone
SHALL NOT be a difference; a value that is not the expected value of its own column SHALL be a
difference named with that column. The comparison SHALL leave both supplied results unchanged.

### Scenario: reordered columns with the same named values are equivalent

- **GIVEN** an expected result with columns `id`, `revenue`, `cost` and row `east`, `100`, `40`,
  a candidate with columns `id`, `cost`, `revenue` and row `east`, `40`, `100`, and business key
  `id`
- **WHEN** the two results are compared
- **THEN** the outcome is `equivalent` with no differences and one compared row, and
  `results.reconcile` passes with a `result.equivalent` claim

### Scenario: reordered columns with changed named values are different

- **GIVEN** the same expected result and a candidate with columns `id`, `cost`, `revenue` and
  row `east`, `100`, `40` — revenue `40`, cost `100`
- **WHEN** the two results are compared
- **THEN** the outcome is `different` with `value.changed` on `east.revenue` (`'100' -> '40'`)
  and on `east.cost` (`'40' -> '100'`), `results.reconcile` blocks, and the run seals no
  `result.equivalent` claim for the asset

### Scenario: a NULL keeps its column after a permutation

- **GIVEN** an expected row `east`, `NULL`, `40` for columns `id`, `revenue`, `cost` and a
  candidate with columns `id`, `cost`, `revenue`
- **WHEN** the candidate row is `east`, `40`, `NULL`, and then `east`, `NULL`, `40`
- **THEN** the first comparison is `equivalent`; the second names `value.null_became_value` on
  `east.revenue` and `value.value_became_null` on `east.cost`

### Scenario: a moved float column keeps its declared tolerance

- **GIVEN** a float column and a string column whose positions differ between the two results,
  and a declared float tolerance
- **WHEN** the candidate's float value moves within the tolerance
- **THEN** the outcome is `equivalent`; a move outside the tolerance is `value.changed` named on
  the float column

### Scenario: the comparison leaves both supplied results unchanged

- **GIVEN** an expected result and a candidate with permuted columns
- **WHEN** the two results are compared, whatever the outcome
- **THEN** each result serializes to the same JSON before and after the comparison

### Scenario: a captured result still follows the asset's column order

- **GIVEN** a reviewed query whose output names the asset's columns in another order
- **WHEN** `decision capture` checks the query's output columns against the asset
- **THEN** capture is refused with `ResultCaptureError` naming the expected column at its
  position, as before

## Requirement: rows are matched by the declared business key read by name in each result

The system SHALL read the declared business-key columns by name in each result separately, so a
key column standing at another position in the candidate still identifies the same row. Key
uniqueness, missing and added rows, contract row order and the row named by a failed aggregate
check SHALL all use the key read this way.

### Scenario: a moved key column still identifies the row

- **GIVEN** an expected result with columns `id`, `revenue`, `cost` and a candidate with columns
  `revenue`, `id`, `cost` carrying the same named values
- **WHEN** the two results are compared with business key `id`
- **THEN** the outcome is `equivalent`; no `row.missing` or `row.added` is reported

### Scenario: a composite key whose columns stand in another order

- **GIVEN** business key `region`, `day` and a candidate whose `day` column stands before
  `region`
- **WHEN** the two results are compared
- **THEN** each candidate row is matched to the expected row with the same `region` and `day`,
  and `row.missing` / `row.added` name keys in the declared key order

### Scenario: a duplicate key in a permuted candidate is insufficient

- **GIVEN** a candidate with permuted columns in which two rows carry the same business key
- **WHEN** the two results are compared
- **THEN** the outcome is `insufficient` with `key.not_unique` naming that key

### Scenario: an ordered result compares row order by the named key

- **GIVEN** `ordered: true` and a candidate with permuted columns
- **WHEN** the rows are in the expected order, and then in another order
- **THEN** the first comparison is `equivalent`; the second reports `row.order_changed`

### Scenario: a failed aggregate check names the candidate row by its own key

- **GIVEN** a `ratio_reconstruction` or `additive_decomposition` check and a candidate with
  permuted columns whose row breaks the invariant
- **WHEN** the two results are compared
- **THEN** the outcome is `different` and the `aggregate.<kind>.candidate_failed` detail names
  the row by its business-key value, not by the value of another column

## Requirement: an integer or decimal key identifies its row by number

When a declared business-key column, or a `group_by` column of a `partition_conservation` or
`share_sum` check, has logical type `integer` or `decimal`, the system SHALL identify a value of
that column by the number it writes, as the value comparison already does: two written forms of
one number (`0.4120` and `0.412`) SHALL be one key, and different numbers SHALL stay different
keys. A value that is not a finite number, NULL, and a key column of any other logical type
SHALL keep their written form. A detail SHALL name a key as its own result writes it.

### Scenario: a key written at another scale still identifies the row

- **GIVEN** an expected result with columns `rate` (decimal) and `amount` (integer) and row
  `0.4120`, `7`, and a candidate with row `0.412`, `7`
- **WHEN** the two results are compared with business key `rate`
- **THEN** the outcome is `equivalent` with one compared row; no `row.missing` or `row.added`
  is reported

### Scenario: a changed value under a rescaled key is named on its row

- **GIVEN** the same expected result and a candidate row `0.412`, `8`
- **WHEN** the two results are compared
- **THEN** the outcome is `different` with exactly one `value.changed`:
  `0.4120.amount: '7' -> '8'`

### Scenario: two written forms of one key number are a duplicate key

- **GIVEN** a result with decimal key `rate` and rows `0.4120`, `7` and `0.412`, `8`
- **WHEN** it is compared, as the expected result or as the candidate
- **THEN** the outcome is `insufficient` with `key.not_unique`

### Scenario: different numbers stay different keys

- **GIVEN** an expected row with decimal key `0.412` and a candidate row with key `0.4121`
- **WHEN** the two results are compared
- **THEN** `row.missing` names `0.412` and `row.added` names `0.4121`

### Scenario: an ordered result reads its order through the numeric key

- **GIVEN** `ordered: true`, decimal keys `0.4120`, `0.5` in the expected result and `0.412`,
  `0.50` in the candidate
- **WHEN** the candidate rows are in the expected order, and then in another order
- **THEN** the first comparison is `equivalent`; the second reports `row.order_changed` with
  each result's written keys

### Scenario: a group written at two scales is one group

- **GIVEN** a `share_sum` check grouped by decimal column `band` and rows `a`, `0.5`, `0.6` and
  `b`, `0.50`, `0.4`
- **WHEN** the check is evaluated
- **THEN** the two rows form one group that sums to `1`, and the check holds

### Scenario: a non-finite or unparsable key value keeps its written form

- **GIVEN** a decimal key written `NaN` in both results, and then `NaN` in one and `nan` in the
  other
- **WHEN** the two results are compared
- **THEN** the first pair is one key; the second is a `row.missing` / `row.added` pair

### Scenario: a key of another type keeps its written form

- **GIVEN** a string key `code` written `0.4120` in the expected result and `0.412` in the
  candidate
- **WHEN** the two results are compared
- **THEN** `row.missing` names `0.4120` and `row.added` names `0.412`, as before

## Requirement: a float value is within its tolerance only when its exact difference is

A value of a `float` column denotes the binary64 number its written form rounds to, as the engine
holds it. When a float tolerance is declared, the system SHALL compare the exact difference of the
two binary64 values with the tolerance read exactly as the decimal it writes: the absolute bound
holds when |a − b| ≤ `float_absolute`, the relative bound when |a − b| ≤ `float_relative` ×
max(|a|, |b|), and two zeros hold the relative bound only when they are equal. A value within
either declared bound is equal. No rounding of the difference, of the ratio or of the tolerance
SHALL move a value across its bound. This requirement governs finite values, with one
consequence for the others: a tolerance read exactly is finite, so no declared tolerance, even
one beyond the binary64 range, makes an infinity equal to a finite value or to the opposite
infinity. How a value that is not a finite number otherwise compares is not decided here.

### Scenario: a difference just over the absolute tolerance is a change

- **GIVEN** an expected result with columns `id` (string) and `m` (float) and row `east`, `0.1`,
  a candidate with row `east`, `0.3`, business key `id` and `float_absolute` `0.19999999999999997`
- **WHEN** the two results are compared
- **THEN** the outcome is `different` with exactly one `value.changed`: `east.m: '0.1' -> '0.3'`,
  because the exact binary64 difference 0.1999999999999999833… exceeds the tolerance

### Scenario: a difference inside the absolute tolerance stays equivalent

- **GIVEN** the same results and `float_absolute` `0.2`
- **WHEN** the two results are compared
- **THEN** the outcome is `equivalent` with one compared row

### Scenario: a ratio just over the relative tolerance is a change

- **GIVEN** an expected row `east`, `100`, a candidate row `east`, `110` and `float_relative`
  `0.090909090909090906`
- **WHEN** the two results are compared
- **THEN** the outcome is `different` with `value.changed` `east.m: '100' -> '110'`; with
  `float_relative` `0.0909090909090909091` the outcome is `equivalent`

### Scenario: a ratio exactly inside the relative tolerance stays equivalent

- **GIVEN** an expected row `east`, `0.0017108284528077355`, a candidate row `east`,
  `0.009525102111858402` and `float_relative` `0.820387389792092933943225376839`, which the exact
  ratio of the two binary64 values does not exceed
- **WHEN** the two results are compared
- **THEN** the outcome is `equivalent`; with `float_relative`
  `0.820387389792092933943225376838` it is `different` with `value.changed` on `east.m`

### Scenario: two written forms of one binary64 value are equal

- **GIVEN** an expected row `east`, `0.1`, a candidate row `east`,
  `0.1000000000000000055511151231257827` and `float_absolute` `0`
- **WHEN** the two results are compared
- **THEN** the outcome is `equivalent`: both forms denote the same binary64 value

### Scenario: a tolerance beyond the binary64 range does not reach an infinity

- **GIVEN** an expected row `east`, `1`, a candidate row `east`, `inf` and `float_absolute`
  `3` followed by 308 zeros, a decimal that binary64 would hold as an infinity
- **WHEN** the two results are compared
- **THEN** the outcome is `different` with `value.changed` `east.m: '1' -> 'inf'`; an
  expected `-inf` against a candidate `inf` under the same tolerance is `different` too
