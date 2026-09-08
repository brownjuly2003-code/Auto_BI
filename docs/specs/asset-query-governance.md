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
ungoverned or is not a table, the system SHALL NOT attribute the name.

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
clean one.

### Scenario: the query selects a star

- **GIVEN** a reviewed query `SELECT * FROM dm.sales_daily`
- **WHEN** the gate grades the asset
- **THEN** the findings carry `asset.query_fields_unchecked` and its detail says the columns read
  depend on the physical table rather than on the SQL
