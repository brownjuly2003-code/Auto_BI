# Capability: Governance of a reviewed reference query

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

A verified asset anchors a candidate number on the SQL a reviewer signed off. That anchor is
worth exactly as much as its attachment to the governed model: a reviewed query reading a mart
nobody governs, or an undeclared column of a governed mart, produces an attested and reconciled
number computed partly from data the model says nothing about. `assets.invalidated` grades that
attachment. It never guesses — but "the SQL does not decide it" is not the same as "nothing
decides it", and where the governed model decides it, the check reads that too.

## Requirement: a reviewed query reading an ungoverned mart is named

The system SHALL report `asset.query_ungoverned` (severity review) for every table a reviewed
`reference_query` reads that the candidate snapshot does not govern, naming all of them rather
than the first.

### Scenario: the query reads a mart outside the model

- **GIVEN** a snapshot governing `dm.sales_daily` and a verified asset whose reviewed query
  reads `dm.sales_daily_v2`
- **WHEN** the gate grades the asset against that snapshot
- **THEN** the findings carry `asset.query_ungoverned` and its detail names `dm.sales_daily_v2`

## Requirement: identifiers are matched the way the asset's engine matches them

The system SHALL compare a name from the reviewed query to a declared name under the folding
rule of the query's engine — ClickHouse compares as written, the PostgreSQL family folds an
*unquoted* identifier to lower case — and SHALL compare a quoted reference exactly on every
engine.

### Scenario: an unquoted PostgreSQL identifier folds, a quoted one does not

- **GIVEN** a snapshot governing `dm.sales_daily` and two assets whose reviewed queries are on
  the PostgreSQL family, one reading `DM.SALES_DAILY` and one reading `"DM"."SALES_DAILY"`
- **WHEN** the gate grades both
- **THEN** the unquoted one produces no finding and the quoted one produces
  `asset.query_ungoverned`

## Requirement: a column is attributed only where SQL or the model leaves no choice

The system SHALL attribute a column of a reviewed query to a governed table when the statement
leaves no choice — qualified, or unqualified with a single source in scope — and, for an
unqualified name the statement alone cannot pin, SHALL attribute it to the one source in its
scope the snapshot declares it on, but only when every source in that scope is a table the
snapshot governs. Where two such sources declare the name, or any source in scope is
ungoverned or is not a table, the system SHALL NOT attribute the name. Sources are counted
as the statement lists them, not as distinct tables: a table joined to itself is two sources.
A source is identified by the FROM/JOIN item the statement names, not by its unqualified
table name: two marts whose short names collide (`dm.sales_daily JOIN stg.sales_daily`) are
two distinct candidates.

The sources of a scope are the ones it **reads** — the tables, derived tables, laterals and
CTEs its own FROM/JOIN clauses name. A CTE its FROM does not name SHALL NOT count as a source
of that scope, whether its WITH clause declared it or an enclosing query did: a query nobody
ran is not a place the engine could have read the column from, and counting it would leave a
name undecidable that the same statement without the WITH clause decides.

An unqualified name written in a scope that can read its enclosing query — a scalar, `IN` or
`EXISTS` subquery, a lateral written inside a scope that can itself read outward, or a branch
of a set operation written in one of them — SHALL NOT be attributed on the strength of a
single local source. A lateral's own readable outer sources are the FROM items written before
it, which are already its local candidates, so it reads the query around the level containing
it only where that level does. A plain non-lateral derived table and a CTE body SHALL NOT be
treated as able to read the query around them, wherever they sit: they introduce a new range
that SQL does not look out of. SQL resolves a correlated name at the innermost level whose
sources have the column and only then outward, so the local sources of a correlated scope are
candidates rather than the whole set. Where exactly one local governed source declares the
name it is attributed there, because that innermost level is where the engine reads it; where
none of them declares it the system SHALL leave the name unchecked rather than report it as an
undeclared column of the local table, since it may be a column of the query around it. The
rule is structural and the same on every engine: the gate does not know which server version
is behind the asset, and grading one engine's tolerance of correlated references onto another
would reject valid SQL.

The sources walked in from enclosing lateral levels are candidates beside the nearest ones,
not ordered by level, so a name several levels declare stays unchecked instead of being
attributed to the nearest — deliberately, because attributing it would mean specifying
innermost-first resolution for every correlated scope. The same flattening also leaves a
name unchecked when the levels name one and the same governed table.

### Scenario: a table joined to itself offers two candidates

- **GIVEN** a snapshot declaring `category` on `dm.sales_daily`, and a reviewed query
  `SELECT category FROM dm.sales_daily AS a JOIN dm.sales_daily AS b ON a.date = b.date`
- **WHEN** the gate grades the asset
- **THEN** the findings carry `asset.query_fields_unchecked` naming `category`, and no field is
  attributed on its behalf

### Scenario: two marts whose short names collide are two candidates

- **GIVEN** a snapshot governing `dm.sales_daily` and `stg.sales_daily`, declaring `discount`
  on only `stg.sales_daily`, and a reviewed query
  `SELECT discount FROM dm.sales_daily JOIN stg.sales_daily ON 1 = 1`
- **WHEN** the gate grades the asset
- **THEN** there is no finding — the two FROM items are two sources even though they share a
  short name, and the model leaves one candidate

### Scenario: the model leaves one candidate for a bare column

- **GIVEN** a snapshot declaring `category` on `dm.sales_daily` and not on `dm.stores`, and a
  reviewed query `SELECT a.region, category FROM dm.sales_daily AS a JOIN dm.stores AS b ON
  a.store_id = b.id`
- **WHEN** the gate grades the asset
- **THEN** `category` is graded as a column of `dm.sales_daily` — no finding — and it is not
  reported as unattributed

### Scenario: the model leaves two candidates for a bare column

- **GIVEN** a snapshot declaring `region` on both `dm.sales_daily` and `dm.stores`, and a
  reviewed query joining the two and reading a bare `region`
- **WHEN** the gate grades the asset
- **THEN** the findings carry `asset.query_fields_unchecked` naming `region`, and no field is
  attributed on its behalf

### Scenario: a CTE nobody reads offers no candidate

- **GIVEN** a snapshot declaring `category` on `dm.sales_daily`, and a reviewed query
  `WITH x AS (SELECT 1 AS one) SELECT category FROM dm.sales_daily AS a`
- **WHEN** the gate grades the asset
- **THEN** there is no finding — `category` is graded as a column of `dm.sales_daily`, exactly
  as it is in the same statement without the WITH clause

### Scenario: a subquery's bare name its own source declares is graded there

- **GIVEN** a snapshot declaring `id` on `dm.stores`, and a reviewed query
  `SELECT a.region FROM dm.sales_daily AS a WHERE a.store_id IN (SELECT id FROM dm.stores)`
- **WHEN** the gate grades the asset
- **THEN** there is no finding — the innermost level holding the column is where the engine
  reads it

### Scenario: a derived table nested in a subquery is not enclosed

- **GIVEN** a snapshot governing `dm.sales_daily` and not declaring `discount` on it, and a
  reviewed query `SELECT a.region FROM dm.sales_daily AS a WHERE a.store_id IN (SELECT x FROM
  (SELECT discount AS x FROM dm.sales_daily) AS t)`
- **WHEN** the gate grades the asset
- **THEN** the findings carry `asset.query_ungoverned_field` naming `dm.sales_daily.discount`:
  the FROM-subquery cannot read the query around it, so its one source decides as it does at
  the root

### Scenario: a lateral body written as a set operation still reads the sibling mart

- **GIVEN** a snapshot declaring `category` on `dm.sales_daily` and not on `dm.stores`, and a
  reviewed query `SELECT a.region, t.x FROM dm.sales_daily AS a, LATERAL (SELECT category AS x
  FROM dm.stores AS b UNION ALL SELECT category AS x FROM dm.stores AS c) AS t`
- **WHEN** the gate grades the asset
- **THEN** there is no finding — each branch of the set operation still reads the sibling
  `dm.sales_daily`, so `category` is graded as its column

### Scenario: a subquery written inside a lateral body still reads the sibling mart

- **GIVEN** a snapshot declaring `category` on `dm.sales_daily` and not on `dm.stores`, and a
  reviewed query `SELECT a.region, t.x FROM dm.sales_daily AS a, LATERAL (SELECT 1 AS x FROM
  dm.stores AS b WHERE b.id IN (SELECT category FROM dm.stores AS c)) AS t`
- **WHEN** the gate grades the asset
- **THEN** there is no finding — the subquery inside the lateral body still reads the sibling
  `dm.sales_daily`, so `category` is graded as its column rather than left unchecked

### Scenario: a derived table inside a lateral body does not read the lateral's siblings

- **GIVEN** a snapshot declaring `category` on `dm.sales_daily` and not on `dm.stores`, and a
  reviewed query `SELECT a.region, t.x FROM dm.sales_daily AS a, LATERAL (SELECT y AS x FROM
  (SELECT category AS y FROM dm.stores AS b) AS inner_t) AS t`
- **WHEN** the gate grades the asset
- **THEN** the findings carry `asset.query_ungoverned_field` naming `dm.stores.category`: the
  derived table cannot read the lateral's siblings, so its one source decides as it does at
  the root

### Scenario: a name declared at two lateral levels stays unchecked

- **GIVEN** a snapshot declaring `revenue` on `dm.sales_daily` and not on `dm.stores`, and a
  reviewed query `SELECT t.x FROM dm.sales_daily AS a, LATERAL (SELECT 1 AS x FROM
  dm.sales_daily AS b, LATERAL (SELECT sum(revenue) AS y FROM dm.stores AS d) AS u) AS t`
- **WHEN** the gate grades the asset
- **THEN** the findings carry `asset.query_fields_unchecked` naming `revenue`: the outer
  sibling and the nearer `b` both declare it, and the sources walked in from enclosing
  lateral levels sit beside the nearest ones rather than being ordered by level

### Scenario: an ungoverned mart at an outer lateral level leaves an inner name unchecked

- **GIVEN** a snapshot governing `dm.sales_daily` and `dm.stores` and not `dm.other`,
  declaring `revenue` on `dm.sales_daily`, and a reviewed query `SELECT t.x FROM dm.other
  AS a, LATERAL (SELECT 1 AS x FROM dm.stores AS b, LATERAL (SELECT sum(revenue) AS y FROM
  dm.sales_daily AS d) AS u) AS t`
- **WHEN** the gate grades the asset
- **THEN** the findings carry `asset.query_ungoverned` naming `dm.other` and
  `asset.query_fields_unchecked` naming `revenue`: the ungoverned mart is written at an
  outer lateral level and is a candidate of the inner body too, so `d` declaring the name
  does not settle it

## Requirement: an undeclared column of a governed mart is named

The system SHALL report `asset.query_ungoverned_field` (severity review) for a column attributed
to a governed table the snapshot does not declare it on, and SHALL NOT repeat, as a field
finding, a column of a table already reported as ungoverned.

### Scenario: a bare column no governed source in scope declares

- **GIVEN** a snapshot governing `dm.sales_daily` and `dm.stores` and declaring `discount` on
  neither, and a reviewed query joining the two and reading a bare `discount`
- **WHEN** the gate grades the asset
- **THEN** the findings carry `asset.query_ungoverned_field` and its detail names `discount`
  together with the sources that were in scope

## Requirement: a partial field check says so

The system SHALL report `asset.query_fields_unchecked` (severity advisory) whenever part of the
reviewed query could not be graded at field level — a `*`, or a name neither the statement nor
the model attributes — and SHALL state which part and why, so a partial check never reads as a
clean one. The remainder is counted per *reference*, not per name: one reference the model
resolves SHALL NOT cover another reference of the same name that nothing resolves. The reason
SHALL distinguish a name several governed sources declare, a scope holding a source the
snapshot cannot see into, a qualifier no enclosing scope declares, a scope with no source
of its own, and a name that may have been read from the enclosing query — a wrong reason sends
the reader after a fix the query does not need.

### Scenario: the query selects a star

- **GIVEN** a reviewed query `SELECT * FROM dm.sales_daily`
- **WHEN** the gate grades the asset
- **THEN** the findings carry `asset.query_fields_unchecked` and its detail says the columns read
  depend on the physical table rather than on the SQL

### Scenario: a resolved name does not cover an unresolvable one beside it

- **GIVEN** a snapshot declaring `category` on `dm.sales_daily` and not on `dm.stores`, and a
  reviewed query `SELECT a.region, category, z.category FROM dm.sales_daily AS a JOIN dm.stores
  AS b ON a.store_id = b.id`
- **WHEN** the gate grades the asset
- **THEN** the findings carry `asset.query_fields_unchecked` naming `z.category`, and its detail
  says that no enclosing scope declares that qualifier rather than that a source was invisible

### Scenario: a subquery's bare name no local source declares is unchecked, not ungoverned

- **GIVEN** a snapshot governing `dm.sales_daily` and `dm.stores`, declaring `store_id` on the
  first and only `id` on the second, and a reviewed query `SELECT a.region FROM dm.sales_daily
  AS a WHERE EXISTS (SELECT 1 FROM dm.stores AS b WHERE b.id = store_id)`
- **WHEN** the gate grades the asset on any engine
- **THEN** the findings carry `asset.query_fields_unchecked` naming `store_id` and saying it may
  be read from the enclosing query, and carry no `asset.query_ungoverned_field`: the valid
  reviewed query is not rejected over a column the outer query declares
