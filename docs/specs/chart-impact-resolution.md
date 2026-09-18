# Capability: Resolving a semantic change to a chart

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

Product impact answers "which governed decision moved". Chart impact answers the question an
owner actually asks next: *which tile on my dashboard*. It is evidence, not grading — resolving
a tile never changes a verdict — and what it cannot resolve leaves a named `chart_gaps` entry
rather than an empty list, because a hole and a clean answer must not look alike.

## Requirement: a tile that reproduces a metric's terms is named as computing it

The system SHALL relate a chart to a changed metric as `computes_metric` when a measure of the
chart's query reproduces the metric's typed formula terms, and SHALL prefer that relation over
`reads_field` for the same tile.

### Scenario: a tile whose measure is the metric's formula

- **GIVEN** a dashboard whose tile sums `gross_margin` on `dm.sales_daily` and a change to a
  metric whose typed terms are that same table, column and aggregation
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries one chart impact for that tile with relation `computes_metric`

## Requirement: a tile that reads a changed field is named as reading it

The system SHALL relate a chart to a change as `reads_field` when the chart's query names a
field the change touches, qualifying a bare column with the query's own base table.

### Scenario: a tile filtering on a changed field

- **GIVEN** a change touching `dm.sales_daily.region` and a tile on `dm.sales_daily` filtering
  on `region`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field`

## Requirement: an operator SQL tile is read from its SQL, not skipped

The system SHALL resolve the field reads of a tile carrying operator `raw_sql` from that SQL,
attributing a column only where the statement itself leaves no choice, and SHALL report a
chart gap naming the tile when its SQL cannot be parsed or when part of what it reads could not
be attributed — so a tile resolved in part is not read as one resolved in whole. A star that
reads no column (`COUNT(*)`, `COUNT(DISTINCT *)`) and a star written in a scope the statement
never reads (`*` inside a CTE the FROM never names) are not unread parts. A column whose source
is a table function the statement does not name is an unread part, reported as such rather than
as an empty table name.

An expression the system cannot expand into column reads SHALL leave the tile read in part,
never resolved as "this tile does not read the change": a template placeholder, ClickHouse's
`COLUMNS('re')` selector and a `JOIN … USING` key each read columns while leaving neither a star
nor an unattributed name behind, so silence about them would read as an answer. The rule is the
contract; a construct found later is named the same way rather than reopening the question.
When the change carries typed metric terms, the system SHALL name that tile with
`chart.metric_unread` unless its SQL was read in whole and names none of those terms' columns.

### Scenario: an operator SQL tile reading a changed field

- **GIVEN** a change touching `dm.sales_daily.revenue` and a tile whose `raw_sql` is
  `SELECT sum(revenue) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field`

### Scenario: an operator SQL tile whose reads the statement does not decide

- **GIVEN** a tile whose `raw_sql` selects `*`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the report carries a chart gap naming that tile and saying which part of its SQL was
  not resolved

### Scenario: a metric change names a parseable operator tile

- **GIVEN** a change to a metric whose typed terms are on `dm.sales_daily` and a tile whose
  `raw_sql` is `SELECT region, sum(gross_margin)/sum(revenue) FROM dm.sales_daily GROUP BY region`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the report carries `chart.metric_unread` naming that tile

### Scenario: a fully-read operator tile that names none of a metric's columns

- **GIVEN** a change to a metric whose typed terms are `gross_margin` and `revenue` and a tile
  whose `raw_sql` is `SELECT sum(store_id) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the report names neither a chart impact nor a chart gap for that tile

### Scenario: count-star beside an attributed read is not a hole

- **GIVEN** a change touching `dm.sales_daily.revenue` and a tile whose `raw_sql` is
  `SELECT count(*), sum(revenue) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field` and
  the report carries no `chart.sql_partial` gap for it

### Scenario: count-distinct-star beside an attributed read is not a hole

- **GIVEN** a change touching `dm.sales_daily.revenue` and a tile whose `raw_sql` is
  `SELECT count(DISTINCT *), sum(revenue) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field` and
  the report carries no `chart.sql_partial` gap for it

### Scenario: a star in a CTE the query does not read is not a hole

- **GIVEN** a change touching `dm.sales_daily.revenue` and a tile whose `raw_sql` is
  `WITH x AS (SELECT * FROM dm.stores) SELECT sum(revenue) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field` and
  the report carries no `chart.sql_partial` gap for it

### Scenario: a table-function source is named as unread, not as an empty table

- **GIVEN** a tile whose `raw_sql` is `SELECT sum(revenue) FROM numbers(10)`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the report carries `chart.sql_partial` naming that tile and saying a column is
  read from a table function the statement does not name

### Scenario: a template placeholder leaves the tile read in part

- **GIVEN** a change touching `dm.sales_daily.revenue` and a tile whose `raw_sql` is
  `SELECT sum(revenue) FROM dm.sales_daily WHERE {{ filter }}`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field` and the
  report carries `chart.sql_partial` naming the placeholder as the unread part

### Scenario: a placeholder inside a string literal is a value, not a hidden read

- **GIVEN** a change touching `dm.sales_daily.revenue` and a tile whose `raw_sql` is
  `SELECT sum(revenue) FROM dm.sales_daily WHERE dt > '{{ from_dttm }}'`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field` and the
  report carries no chart gap for it

### Scenario: a COLUMNS selector is read as ClickHouse's form of `*`

- **GIVEN** a tile whose `raw_sql` is `SELECT COLUMNS('rev.*') FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the report carries `chart.sql_partial` naming that tile and the selector

### Scenario: a JOIN USING key is an unread part

- **GIVEN** a change touching `dm.sales_daily.revenue` and a tile whose `raw_sql` is
  `SELECT a.revenue FROM dm.sales_daily AS a JOIN dm.stores AS b USING (store_id)`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field` and the
  report carries `chart.sql_partial` naming the USING key as the unread part

### Scenario: a bare table name that is the tile's own table is qualified through it

- **GIVEN** a change touching `dm.sales_daily.revenue` and a tile whose `query.table` is
  `dm.sales_daily` and whose `raw_sql` is `SELECT sum(revenue) FROM sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field`

### Scenario: a bare table name that is not the tile's own table is named as unread

- **GIVEN** a tile whose `query.table` is `dm.sales_daily` and whose `raw_sql` is
  `SELECT sum(revenue) FROM other_table`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the report carries `chart.sql_partial` naming that tile and the name that could
  not be reconciled

### Scenario: a catalog-prefixed table name that is the tile's own table is qualified through it

- **GIVEN** a change touching `dm.sales_daily.revenue` and a tile whose `query.table` is
  `dm.sales_daily` and whose `raw_sql` is `SELECT sum(revenue) FROM cat.dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field`

### Scenario: a catalog-prefixed table name that is not the tile's own table is named as unread

- **GIVEN** a tile whose `query.table` is `dm.sales_daily` and whose `raw_sql` is
  `SELECT sum(revenue) FROM cat.other.table`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the report carries `chart.sql_partial` naming that tile and the name that could
  not be reconciled

## Requirement: an unresolvable input is named rather than silently dropped

The system SHALL record a chart gap for every input the resolver could not read — a dashboard
spec the run did not load, a metric whose definition was not supplied, and a metric whose
formula carries no typed terms — keyed so one hole is reported once.

### Scenario: a metric with an external formula

- **GIVEN** a change to a metric whose formula carries no typed terms
- **WHEN** impact is resolved with a dashboard supplied
- **THEN** the report carries `chart.formula_untyped` naming that metric
