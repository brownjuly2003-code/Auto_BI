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
be attributed — so a tile resolved in part is not read as one resolved in whole.

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

## Requirement: an unresolvable input is named rather than silently dropped

The system SHALL record a chart gap for every input the resolver could not read — a dashboard
spec the run did not load, a metric whose definition was not supplied, and a metric whose
formula carries no typed terms — keyed so one hole is reported once.

### Scenario: a metric with an external formula

- **GIVEN** a change to a metric whose formula carries no typed terms
- **WHEN** impact is resolved with a dashboard supplied
- **THEN** the report carries `chart.formula_untyped` naming that metric
