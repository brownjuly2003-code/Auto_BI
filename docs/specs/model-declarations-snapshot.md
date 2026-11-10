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

## Requirement: what a model declares more than once is one node of the graph, or none

`model.yaml` takes the name of a table, the name of a column in a table and the two ends of a
join any number of times. Each declaration used to become a node, so a graph held several nodes
under one id — and its readers do not read such a graph alike: a semantic diff indexes the
nodes by id and keeps the last, the gate and the v1 model read the first. Two refs that both
declared a table twice and differed in the first declaration alone were compared as unchanged
though their canonical digests differed, and a ref that declared a table once and a ref that
declared it twice, word for word, had two digests and no change between them.

The canonical graph SHALL hold at most one table node to a table id, one field node to a field
id and one join node to a join id; a `SemanticGraph` with two such nodes of one id SHALL be
refused when it is built. For every id the model declares more than once the snapshot SHALL
carry a gap, and which one depends on whether the declarations are alike — whether the graph
could tell them apart:

- declarations of a field (one `<table>.<column>`) or of a join (one `<left>-><right>`) are
  alike when they give the same node;
- declarations of a table (one name) are alike when they give the same table node, agree on
  whether physical facts are declared at all, and give the same field nodes, each the same
  number of times. The order of the columns, and what the graph does not hold of a table or a
  column (synonyms, sample values, the statistics among the physical facts), do not tell
  declarations apart.

Declarations that are alike are one declaration: the graph holds the node once, the canonical
digest is the digest of the model that declares the thing once, and the snapshot carries
`gap.table_repeated`, `gap.field_repeated` or `gap.join_repeated` (advisory) with the reason
`<kind> is declared <n> times, alike: the graph holds it once`. Declarations that are not alike
give no node, for there is no telling which of them the model means: the snapshot carries
`gap.table_conflict`, `gap.field_conflict` or `gap.join_conflict` (block) with the reason
`<kind> is declared <n> times and not alike: it is not in the graph` — for a table, `it and its
columns are not in the graph`. `<kind>` is `table`, `field` or `join` and `<n>` the number of
declarations; the path is `tables.<name>`, `fields.<table>.<column>` or `joins.<left>-><right>`;
none of these gaps carries a `source_value_ref`.

A table in conflict is not read further: neither it nor a column of any of its declarations is
in the graph, it gets no `gap.missing_physical`, and a column one of its declarations declares
twice gets no gap of its own. The canonical digest SHALL be the digest of the model without the
table. Field ids are compared across the tables that are in the graph: columns of two tables
that make one id (`dm` with a column `stores.id`, `dm.stores` with a column `id`) are
declarations of one field, and never alike. A field in conflict is no declared field for what
follows: a join to it is a `gap.dangling_join` and a metric that reads it a
`gap.metric_field_missing`, by the existing rules. A join whose declarations are alike and whose
end is no declared field is one `gap.dangling_join`, not one for each declaration, and gets no
`gap.join_repeated`.

The gaps stand in this order: for each table name, where the model first declares it, its
`gap.table_repeated` or `gap.table_conflict` and then its `gap.missing_physical` (a
`gap.table_unnamed` stands at the place of its own table among them); then the field gaps in the
order the field ids first stand; then the join gaps in the order the join ids first stand; then
the gaps of raw-SQL metrics and of metric contracts, as before; last, the gaps of metric ids that
more than one contract defines
([two contracts of one metric id](repeated-metric-contracts.md)).

The gate grades these gaps as it grades any gap. `semantic.gaps` is advisory when a repeat is
the worst gap of the two snapshots and blocks when either holds a conflict; the snapshots are
compared either way, and a gap only one of them carries is the change `gap.appeared` (of the
gap's severity) or `gap.resolved` (advisory). A ref that repeats a declaration alike has the
canonical digest of the ref that does not, so a result attested against the one is attested
against the other. A run over a ref that declares a table, a column or a join more than once
produced a bundle and now produces another, so `VERIFIER_VERSION` and `CHECK_VERSION` SHALL be
bumped.

### Scenario: a table declared twice, word for word, is one table and a named repeat

- **GIVEN** a model that declares `dm.stores` twice, word for word
- **WHEN** a native snapshot is built
- **THEN** the graph and the canonical digest are those of the model that declares it once, and
  the snapshot carries one `gap.table_repeated` gap of severity advisory at `tables.dm.stores`
  with the reason `table is declared 2 times, alike: the graph holds it once`

### Scenario: declarations that differ only in what the graph does not hold are alike

- **GIVEN** a model whose second declaration of a table lists the columns in another order, with
  other synonyms and other statistics among the physical facts
- **WHEN** a native snapshot is built
- **THEN** the snapshot carries `gap.table_repeated` and no `gap.table_conflict`

### Scenario: a table declared two ways is a blocking gap and no table

- **GIVEN** a model that declares `dm.sales_daily` twice, the second time without one column
- **WHEN** a native snapshot is built
- **THEN** it carries `gap.table_conflict` (block) at `tables.dm.sales_daily` with the reason
  `table is declared 2 times and not alike: it and its columns are not in the graph`; the graph
  and the canonical digest are those of the model without the table, and the gaps that follow
  are the gaps of that model

### Scenario: no physical facts and physical facts declared empty are not alike

- **GIVEN** a model that declares a table once with `physical: {engine: ""}` and once with no
  `physical`
- **WHEN** a native snapshot is built
- **THEN** it carries `gap.table_conflict` for the table and no `gap.missing_physical`

### Scenario: a column declared twice in one table

- **GIVEN** a model whose table `dm.sales_daily` declares the column `category` twice
- **WHEN** a native snapshot is built
- **THEN** with declarations that give the same field node the graph holds the field once and
  the snapshot carries `gap.field_repeated` at `fields.dm.sales_daily.category`; with
  declarations of another type, role, classification, description or foreign key the graph
  holds no such field and the snapshot carries `gap.field_conflict` (block) at that path

### Scenario: columns of two tables that make one field id

- **GIVEN** a model with a table `dm` that declares the column `stores.id` and a table
  `dm.stores` that declares the column `id`
- **WHEN** a native snapshot is built
- **THEN** it carries `gap.field_conflict` at `fields.dm.stores.id`, the graph holds both tables
  and no field `dm.stores.id`, and the join to that field is a `gap.dangling_join`

### Scenario: a join declared twice

- **GIVEN** a model that declares the join `dm.sales_daily.store_id->dm.stores.id` twice
- **WHEN** a native snapshot is built
- **THEN** with one cardinality the graph holds the join once and the snapshot carries
  `gap.join_repeated`; with two cardinalities the graph holds no such join and the snapshot
  carries `gap.join_conflict` (block) at `joins.dm.sales_daily.store_id->dm.stores.id`

### Scenario: a dangling join declared twice dangles once

- **GIVEN** a model that declares twice, alike, a join whose right end is no declared field
- **WHEN** a native snapshot is built
- **THEN** the snapshot carries one `gap.dangling_join` for it and no `gap.join_repeated`

### Scenario: refs that differ in one declaration of a table are not compared as unchanged

- **GIVEN** a base ref that declares a table twice, word for word, and a candidate ref whose
  first declaration of that table describes it another way
- **WHEN** the two snapshots are compared
- **THEN** the candidate carries `gap.table_conflict`, and the diff reports `table.removed` for
  the table, `gap.appeared` (block) for the conflict and `gap.resolved` for the repeat

### Scenario: a candidate that declares a table two ways is blocked

- **GIVEN** a base ref that declares its tables once and a candidate ref that declares one of
  them once more, ahead of the first, described another way
- **WHEN** `decision verify` runs
- **THEN** `semantic.gaps` blocks with `semantic.gaps_present` and the `gap.table_conflict` gap
  of the candidate snapshot in its evidence, the verdict is `block` and the exit code is `4`

### Scenario: a candidate that repeats a declaration alike is named and not blocked

- **GIVEN** a base ref and a candidate ref that differ only in that the candidate declares a
  table and a join once more, word for word
- **WHEN** `decision verify` runs
- **THEN** the two canonical digests are equal, `semantic.gaps` is advisory with
  `semantic.gaps_present` and the two repeats in its evidence, `semantic.diff` reports a
  `gap.appeared` (advisory) for each, and no check blocks

### Scenario: no graph holds two nodes of one id

- **GIVEN** two table nodes, two field nodes or two join nodes of one id, alike or not
- **WHEN** a `SemanticGraph` is built of them
- **THEN** it is refused with `more than one <kind> node of the graph has the id '<id>'`

## Known boundaries

- Only empty text is no name. A name of blanks, and names that differ only by case or by blanks,
  are names: what the engine makes of them is not asked.
- A column declared with no name stays a field `<table>.` of the graph: its id is not empty text,
  and a change of it is named.
- Declarations are compared by what the graph holds of them, and what the declarations of a
  table, a field or a join in conflict say is not kept: the gap counts them and does not tell
  them apart. Two refs that hold one conflict, each its own way, carry the same gap and show
  no change of it.
- Names are compared as text: `dm.Sales` and `dm.sales` are two tables, whatever the engine
  makes of them.
- A raw-SQL metric a model declares twice is two `gap.raw_sql_metric` gaps at one path, as
  before. Two metric contracts of one id in a ref are a metric node each and the blocking gap
  `gap.metric_duplicate` of the id, on which the gate does not run the checks a contract that did
  not load leaves not run ([two contracts of one metric id](repeated-metric-contracts.md)).
- The v1 readers of `model.yaml` — the spec validator, the BI compile, the SQL generator —
  read the first declaration of a name and are not changed: a mart column declared twice,
  alike, is an advisory of the snapshot and still a dashboard `bi.contract` refuses to
  compile.
- `decision capture` freezes the candidate ref to attest a result and does not grade gaps: a
  candidate with a table of no name, or with declarations in conflict, is captured as before,
  and the gate that reads the result blocks.
- The v1 commands read `model.yaml` by their own schema and are not changed: the schema still
  takes a table with no name.
