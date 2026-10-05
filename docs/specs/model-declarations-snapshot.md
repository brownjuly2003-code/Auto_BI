# Capability: What a model declares that the canonical graph cannot hold

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

DECISIONOPS_ARCHITECTURE principle 6 says semantics the canonical graph cannot carry becomes an
explicit gap. The canonical graph names a table by the name `model.yaml` gives it: the name is
the table's id, the first part of the id of each of its fields, and the subject a semantic diff
gives a change of either. `model.yaml` is edited by hand, and its schema — the v1 schema, which
this capability does not change — takes any text for a name, the empty one too.

A table declared with `name: ""` was kept in the graph as a table whose id is empty text, with
the fields `.<column>`. Two refs that held it alike were graded as if it were a table. Two refs
that differed in it — one names the table and the other does not, both hold it and describe it
another way, or one holds one more of them — ended `decision verify` in an unhandled
`ValidationError` while the snapshots were compared: a change is named by the id of what
changed, and this one had no name. There was no report and no bundle.

## Requirement: a table declared with no name is a gap, not a table

A table of the model whose `name` is empty text SHALL NOT be in the canonical graph: it gets no
table node, and its columns get no field nodes. For each such table the snapshot SHALL carry the
gap `gap.table_unnamed` (block) at `tables.<index>.name`, where `<index>` is the zero-based place
of the table among the model's `tables`, with the reason `table is declared with no name: it and
its <n> column(s) are not in the graph` and no `source_value_ref`. Nothing else such a table
declares is read: it gets no `gap.missing_physical`, and its description, grain, classification,
columns and physical facts do not move the canonical digest, which SHALL be the digest of the
same model without the table. No table node SHALL have empty text for its id.

A join with an endpoint that is a column of such a table has an endpoint that is no declared
field, and is a `gap.dangling_join` by the existing rule. A name that is not empty text is a
name, one of blanks too, and its table is in the graph.

The gate grades the gap as it grades any blocking gap: `semantic.gaps` blocks with
`semantic.gaps_present` when the base or the candidate snapshot carries it, and the two
snapshots are still compared. A gap only one of them carries is the change `gap.appeared`
(block) or `gap.resolved` (advisory) at the gap's path, by the existing rules. A run over two
refs that held such a table alike produced a bundle and now produces another, so
`VERIFIER_VERSION` and `CHECK_VERSION` SHALL be bumped.

### Scenario: a table with no name is a blocking gap named by its place

- **GIVEN** a model whose second table is declared with `name: ""` and one column
- **WHEN** a native snapshot is built
- **THEN** it carries one `gap.table_unnamed` gap of severity block at `tables.1.name` with the
  reason `table is declared with no name: it and its 1 column(s) are not in the graph`, and the
  graph holds neither a table nor a field of it

### Scenario: the snapshot is the one of the model without that table

- **GIVEN** a model with a table declared with no name, and the same model without that table
- **WHEN** both snapshots are built
- **THEN** their graphs and canonical digests are equal, and the first carries the
  `gap.table_unnamed` gap ahead of the gaps the second carries

### Scenario: each such table is named by its own place

- **GIVEN** a model that declares a table with no name second and another one fourth
- **WHEN** a native snapshot is built
- **THEN** it carries the gap at `tables.1.name` and at `tables.3.name`, each with its own count
  of columns

### Scenario: a candidate that loses the name of a table is compared

- **GIVEN** a base ref whose model names its tables and a candidate ref where the name of one of
  them is empty text
- **WHEN** `decision verify` runs
- **THEN** it ends with no exception and exit code `4`; `semantic.gaps` blocks with
  `semantic.gaps_present`, and `semantic.diff` reports `gap.appeared` at `tables.<index>.name`
  and `table.removed` for the table by the name the base gives it

### Scenario: refs that hold such a table alike are blocked

- **GIVEN** a base and a candidate ref that both declare the same table with no name, alike or
  described another way
- **WHEN** `decision verify` runs
- **THEN** `semantic.gaps` blocks with a `gap.table_unnamed` gap of each snapshot, `semantic.diff`
  reports no change of the table, and the verdict is `block`

### Scenario: a name of blanks is a name

- **GIVEN** a model that declares a table named with one space
- **WHEN** a native snapshot is built
- **THEN** the graph holds a table with that id, and the snapshot carries no `gap.table_unnamed`

## Known boundaries

- Only empty text is no name. A name of blanks, and names that differ only by case or by blanks,
  are names: what the engine makes of them is not asked.
- A column declared with no name stays a field `<table>.` of the graph: its id is not empty text,
  and a change of it is named.
- A table, a column or a join the model declares twice is not covered by this requirement.
- `decision capture` freezes the candidate ref to attest a result and does not grade gaps: a
  candidate with such a table is captured as before, and the gate that reads the result blocks.
- The v1 commands read `model.yaml` by their own schema and are not changed: the schema still
  takes a table with no name.
