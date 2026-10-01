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
