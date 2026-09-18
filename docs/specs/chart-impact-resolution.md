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
Today the resolver recognises those three constructs and the ClickHouse dictionary calls below;
a construct outside that set is a hole in the detector, not a guarantee that the tile was read in
whole.
A ClickHouse dictionary call — any function whose name begins with `dict`, case-insensitive —
SHALL either name the read it performs or leave the tile read in part. An attribute reader
(`dictGet`, `dictGetOrDefault`, `dictGetOrNull` and their typed forms such as `dictGetString`)
whose dictionary and attribute (or tuple of attributes) are string literals is read as
`<dictionary>.<attribute>`, reconciled like a table name, and counts as an attributed read — for
`reads_field` and for the metric rule alike. Every other dictionary call — a dictionary or
attribute that is not a literal, a membership or hierarchy test (`dictHas`, `dictIsIn`,
`dictGetHierarchy`) that reads a key or parent attribute without naming it, a function the
resolver does not know — is an unread part that names the call. Only calls the statement runs
count: a call inside a CTE the statement never names, or inside a scalar `WITH <expr> AS alias`
whose alias nothing reads, is neither a read nor an unread part.
When the change reaches an in-scope product and carries typed metric terms, the system
SHALL name that tile with `chart.metric_unread` unless its SQL was read in whole and names
none of those terms' columns.

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

- **GIVEN** a change to a metric whose typed terms are on `dm.sales_daily`, an in-scope
  product that depends on that metric, and a tile whose `raw_sql` is
  `SELECT region, sum(gross_margin)/sum(revenue) FROM dm.sales_daily GROUP BY region`
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

### Scenario: a literal dictionary call is read as the dictionary's attribute

- **GIVEN** a change touching `dm.rev.revenue` and a tile whose `raw_sql` is
  `SELECT region, dictGet('dm.rev', 'revenue', region) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field` naming
  `dm.rev.revenue`, and the report carries no chart gap for it

### Scenario: a tuple of dictionary attributes is read as each of them

- **GIVEN** a change touching `dm.rev.revenue` and `dm.rev.cost` and a tile whose `raw_sql` is
  `SELECT dictGet('dm.rev', ('revenue', 'cost'), region) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field` naming
  both `dm.rev.cost` and `dm.rev.revenue`

### Scenario: a dictionary call whose dictionary is not a literal leaves the tile read in part

- **GIVEN** a change touching `dm.sales_daily.revenue` and a tile whose `raw_sql` is
  `SELECT sum(revenue), dictGet(dict_name, 'revenue', region) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field` and the
  report carries `chart.sql_partial` naming `dictGet(...)` as the unread part

### Scenario: a dictionary membership test leaves the tile read in part

- **GIVEN** a change touching `dm.sales_daily.revenue` and a tile whose `raw_sql` is
  `SELECT sum(revenue), dictHas('dm.rev', region) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field` and the
  report carries `chart.sql_partial` naming `dictHas(...)` as the unread part

### Scenario: a dictionary call in a CTE the query does not read changes nothing

- **GIVEN** a change touching `dm.rev.revenue` and a tile whose `raw_sql` is
  `WITH x AS (SELECT dictGet('dm.rev', 'revenue', region) AS r FROM dm.sales_daily)
  SELECT sum(store_id) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the report names neither a chart impact nor a chart gap for that tile

### Scenario: a dictionary call in a scalar WITH nobody reads changes nothing

- **GIVEN** a change touching `dm.rev.revenue` and a tile whose `raw_sql` is
  `WITH dictGet('dm.rev', 'revenue', toUInt64(1)) AS r SELECT sum(revenue) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the report names neither a chart impact nor a chart gap for that tile

### Scenario: a dictionary call in a scalar WITH the query reads is read

- **GIVEN** a change touching `dm.rev.revenue` and a tile whose `raw_sql` is
  `WITH dictGet('dm.rev', 'revenue', toUInt64(1)) AS r SELECT sum(revenue) * r FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field` naming
  `dm.rev.revenue`

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
spec the run did not load, and, when the change being resolved reaches an in-scope product, a
metric whose definition was not supplied and a metric whose formula carries no typed terms —
keyed so one hole is reported once.

### Scenario: a metric with an external formula

- **GIVEN** a change to a metric whose formula carries no typed terms and an in-scope product
  that depends on that metric
- **WHEN** impact is resolved with a dashboard supplied
- **THEN** the report carries `chart.formula_untyped` naming that metric

## Requirement: a gap answers a question an in-scope product asked

The system SHALL raise a metric-derived chart gap — `chart.metric_unread`,
`chart.metric_unknown`, `chart.formula_untyped` — only while resolving a change that reaches
at least one in-scope product. A change that reaches no in-scope product asks none of those
questions, and an unanswered question nobody asked is not a hole in this report. Relating a
tile as `computes_metric` is evidence, not a gap: a delivered tile that computes a metric
no in-scope product declares SHALL still be named. Field reads and tile-level gaps
(`chart.opaque_sql`, `chart.sql_partial`, `chart.dashboard_spec_missing`) are not
metric-derived and SHALL still be recorded.

### Scenario: a typed metric change that reaches no in-scope product records no metric_unread

- **GIVEN** a change to a metric whose typed terms are on `dm.sales_daily`, a product in
  scope that does not depend on that metric, and a parseable tile whose `raw_sql` names
  those terms' columns
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the report carries no `chart.metric_unread` for that tile

### Scenario: a tile that computes a metric of a change that reaches nobody is still named

- **GIVEN** a change to a metric whose typed terms a delivered tile reproduces, and a
  product in scope that does not depend on that metric
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `computes_metric`
  and the report carries no metric-derived chart gap

### Scenario: the same change reaching a product still raises metric_unread

- **GIVEN** the same typed metric change and parseable tile, and an in-scope product that
  depends on that metric
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the report carries `chart.metric_unread` naming that tile

### Scenario: a tile whose SQL did not parse is still named opaque for a change that reaches nobody

- **GIVEN** a change to a metric no in-scope product depends on and a tile whose `raw_sql`
  could not be parsed
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the report carries `chart.opaque_sql` naming that tile

### Scenario: a field change that carries no metric still resolves its tile reads

- **GIVEN** a change touching `dm.stores.id`, which no in-scope metric depends on, and a
  tile that reads that field
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field`
