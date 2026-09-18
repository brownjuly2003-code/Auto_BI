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
never reads — `*` inside a named CTE the FROM never names, or inside ClickHouse's scalar
`WITH` (the expression form `WITH <expr> AS alias` or the subquery form
`WITH (SELECT …) AS alias`) whose alias no running part of the statement reads — are not
unread parts. Either of those scopes read (FROM/JOIN names the CTE, or the alias is read
by a part of the statement that runs) makes the star an unread part. A star inside
ClickHouse's scalar `WITH` follows its alias the same way a dictionary call does. A query
nested inside the body of a scalar `WITH` is part of that `WITH`: it runs when the `WITH`
is live, and the named CTEs it names and the `WITH`s it declares follow the same rules.
Shadowing is not modelled: a name opens every CTE it could name, compared
case-insensitively, which can only over-report. A tuple access at any depth (`t.1`,
`t.name`, `t.a.b`) reads the scalar alias through its leftmost qualifier; a table alias
or a database qualifier with the same name opens it too, which is an over-report. A
column whose source is a table function
the statement does not name is an unread part, reported as such rather than as an empty
table name.

An expression the system cannot expand into column reads SHALL leave the tile read in part,
never resolved as "this tile does not read the change": a template placeholder, ClickHouse's
`COLUMNS('re')` selector and a `JOIN … USING` key each read columns while leaving neither a star
nor an unattributed name behind, so silence about them would read as an answer. The rule is the
contract; a construct found later is named the same way rather than reopening the question.
Today the resolver recognises those three constructs, the ClickHouse dictionary calls below,
and the Join-table calls below; a construct outside that set is a hole in the detector, not
a guarantee that the tile was read in whole. One known example is a ClickHouse table name
on the right of `IN` (`WHERE region IN rev`): ClickHouse reads `rev` as
`IN (SELECT * FROM rev)`, while sqlglot parses the identifier as a column and the guard
attributes it to the tile's own table, so the tile looks read in whole.
A ClickHouse dictionary call — any function whose name begins with `dict`, case-insensitive —
SHALL either name the read it performs or leave the tile read in part. The resolver names a
closed set of attribute readers: `dictGet`, `dictGetOrDefault`, `dictGetOrNull`, `dictGetAll`,
and the typed forms `dictGet<Type>`, `dictGet<Type>OrDefault` and `dictGet<Type>OrNull` where
`<Type>` is an unsigned or signed integer (8/16/32/64/128/256), float32/64,
decimal32/64/128/256, date, date32, datetime, datetime64, uuid, ipv4, ipv6, string or bool.
A call in that set whose dictionary and attribute (or tuple of attributes) are string
literals is read as `<dictionary>.<attribute>`, reconciled like a table name, and counts as
an attributed read — for `reads_field` and for the metric rule alike. Every other dictionary
call — a dictionary or attribute that is not a literal, a membership or hierarchy test
(`dictHas`, `dictIsIn`, `dictGetHierarchy`, `dictGetChildren`, `dictGetDescendants`), a
function outside that set — is an unread part that names the call as one whose dictionary
read this run could not name. Only calls the statement runs count: a call inside a named CTE
the FROM/JOIN never names, inside a scalar `WITH <expr> AS alias` whose alias no running
part reads, or inside `WITH (SELECT …) AS alias` whose alias no running part reads, is
neither a read nor an unread part. A dictionary call inside ClickHouse's scalar `WITH` —
both the expression form and the subquery form — counts when the alias is read by a part
of the statement that runs; a named CTE counts when FROM/JOIN names it. A scalar alias
read only inside a CTE the statement never runs does not make that `WITH` live. A query
nested inside the body of a scalar `WITH` is part of that `WITH`: it runs when the `WITH`
is live, and the named CTEs it names and the `WITH`s it declares follow the same rules.
Shadowing is not modelled: a name opens every CTE it could name, compared
case-insensitively, which can only over-report. A tuple access at any depth (`t.1`,
`t.name`, `t.a.b`) reads the scalar alias through its leftmost qualifier; a table alias
or a database qualifier with the same name opens it too, which is an over-report.
A ClickHouse Join-table call — any function whose name begins with `joinget`,
case-insensitive — SHALL either name the read it performs or leave the tile read in part,
through the same reachability rule as a dictionary call. The resolver names a closed set of
readers: `joinGet` and `joinGetOrNull`. A call in that set whose Join table and column are
each one string literal is read as `<table>.<column>`, reconciled like a table name, and
counts as an attributed read — for `reads_field` and for the metric rule alike. Every other
Join-table call — a table or column that is not a literal, a tuple of columns, a function
outside that set — is an unread part that names the call as one whose Join table read this
run could not name.
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

### Scenario: a star in an unread expression `WITH` is not a hole

- **GIVEN** a change touching `dm.sales_daily.revenue` and a tile whose `raw_sql` is
  `WITH tuple(*) AS t SELECT sum(revenue) FROM dm.sales_daily`, whose alias `t` no
  running part of the statement reads
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field`
  and the report carries no `chart.sql_partial` gap for it

### Scenario: a star in a read expression `WITH` is an unread part

- **GIVEN** a change touching `dm.sales_daily.revenue` and a tile whose `raw_sql` is
  `WITH tuple(*) AS t SELECT t, sum(revenue) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field`
  and the report carries a `chart.sql_partial` gap naming that tile

### Scenario: a star reached through a subquery inside a live expression WITH is an unread part

- **GIVEN** a change touching `dm.sales_daily.revenue` and a tile whose `raw_sql` is
  `WITH tuple(*) AS t, x AS (SELECT t AS z FROM dm.other),
  1 + (SELECT z FROM x) AS s SELECT s, sum(revenue) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field`
  and the report carries a `chart.sql_partial` gap naming that tile and saying it selects *

### Scenario: a star in a nested WITH inside a live expression-form body is an unread part

- **GIVEN** a change touching `dm.sales_daily.revenue` and a tile whose `raw_sql` is
  `WITH 1 + (WITH tuple(*) AS q SELECT q) AS s SELECT s, sum(revenue) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field`
  and the report carries a `chart.sql_partial` gap naming that tile and saying it selects *

### Scenario: a dictionary call whose alias only a dead CTE reads changes nothing

- **GIVEN** a change touching `dm.rev.revenue` and a tile whose `raw_sql` is
  `WITH (SELECT dictGet('dm.rev', 'revenue', toUInt64(1))) AS r,
  x AS (SELECT r AS z FROM dm.other) SELECT sum(revenue) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the report names neither a chart impact nor a chart gap for that tile

### Scenario: a star whose alias only a dead CTE reads is not a hole

- **GIVEN** a change touching `dm.sales_daily.revenue` and a tile whose `raw_sql` is
  `WITH (SELECT * FROM dm.other) AS r, x AS (SELECT r AS z FROM dm.other)
  SELECT sum(revenue) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field`
  and the report carries no `chart.sql_partial` gap for it

### Scenario: a dictionary call whose alias is read only by an unread alias changes nothing

- **GIVEN** a change touching `dm.rev.revenue` and a tile whose `raw_sql` is
  `WITH dictGet('dm.rev', 'revenue', toUInt64(1)) AS a, a + 1 AS b
  SELECT sum(revenue) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the report names neither a chart impact nor a chart gap for that tile

### Scenario: a dictionary call whose alias is read through a live alias chain is read

- **GIVEN** a change touching `dm.rev.revenue` and a tile whose `raw_sql` is
  `WITH dictGet('dm.rev', 'revenue', toUInt64(1)) AS a, a + 1 AS b
  SELECT b, sum(revenue) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field`
  naming `dm.rev.revenue`

### Scenario: a dictionary call reached through a subquery inside a live expression WITH is read

- **GIVEN** a change touching `dm.rev.revenue` and a tile whose `raw_sql` is
  `WITH dictGet('dm.rev', 'revenue', toUInt64(1)) AS a, x AS (SELECT a AS z FROM dm.other),
  1 + (SELECT z FROM x) AS s SELECT s, sum(revenue) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field`
  naming `dm.rev.revenue`

### Scenario: a dictionary call in a nested WITH inside a live expression-form body is read

- **GIVEN** a change touching `dm.rev.revenue` and a tile whose `raw_sql` is
  `WITH 1 + (WITH dictGet('dm.rev', 'revenue', toUInt64(1)) AS q SELECT q) AS s
  SELECT s, sum(revenue) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field`
  naming `dm.rev.revenue`

### Scenario: a named CTE named only from a subquery inside a live expression WITH is read

- **GIVEN** a change touching `dm.rev.revenue` and a tile whose `raw_sql` is
  `WITH x AS (SELECT dictGet('dm.rev', 'revenue', toUInt64(1)) AS v FROM dm.other),
  1 + (SELECT v FROM x) AS r SELECT r, sum(revenue) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field`
  naming `dm.rev.revenue`

### Scenario: a named CTE declared inside a live expression-form body and never named changes nothing

- **GIVEN** a change touching `dm.rev.revenue` and a tile whose `raw_sql` is
  `WITH 1 + (WITH x AS (SELECT dictGet('dm.rev', 'revenue', toUInt64(1)) AS v FROM dm.other)
  SELECT 1) AS s SELECT s, sum(revenue) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the report names neither a chart impact nor a chart gap for that tile

### Scenario: a named CTE declared inside a live expression-form body and named there is read

- **GIVEN** a change touching `dm.rev.revenue` and a tile whose `raw_sql` is
  `WITH 1 + (WITH x AS (SELECT dictGet('dm.rev', 'revenue', toUInt64(1)) AS v FROM dm.other)
  SELECT v FROM x) AS s SELECT s, sum(revenue) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field`
  naming `dm.rev.revenue`

### Scenario: a shadowed scalar alias is a known over-report

- **GIVEN** a change touching `dm.rev.revenue` and a tile whose `raw_sql` is
  `WITH dictGet('dm.rev', 'revenue', toUInt64(1)) AS a
  SELECT sum(revenue) FROM (WITH 1 AS a SELECT revenue, a FROM dm.sales_daily)`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field`
  naming `dm.rev.revenue` — the inner `a` is the inner WITH's literal, but shadowing is
  not modelled, so the name also opens the outer WITH; that over-reports and hides nothing

### Scenario: a tuple-element access reads a scalar WITH alias

- **GIVEN** a change touching `dm.rev.revenue` and a tile whose `raw_sql` is
  `WITH (SELECT dictGet('dm.rev', 'revenue', toUInt64(1)), 2) AS t
  SELECT t.1, sum(revenue) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field`
  naming `dm.rev.revenue`

### Scenario: a two-level tuple access reads a scalar WITH alias

- **GIVEN** a change touching `dm.rev.revenue` and a tile whose `raw_sql` is
  `WITH (SELECT dictGet('dm.rev', 'revenue', toUInt64(1)), 2) AS t
  SELECT t.a.1, sum(revenue) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field`
  naming `dm.rev.revenue`

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
  report carries `chart.sql_partial` naming that tile and saying it calls `dictGet(...)`,
  whose dictionary read this run could not name

### Scenario: a dictionary membership test leaves the tile read in part

- **GIVEN** a change touching `dm.sales_daily.revenue` and a tile whose `raw_sql` is
  `SELECT sum(revenue), dictHas('dm.rev', region) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field` and the
  report carries `chart.sql_partial` naming that tile and saying it calls `dictHas(...)`,
  whose dictionary read this run could not name

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

### Scenario: a dictionary call in a scalar subquery WITH the query reads is read

- **GIVEN** a change touching `dm.rev.revenue` and a tile whose `raw_sql` is
  `WITH (SELECT dictGet('dm.rev', 'revenue', toUInt64(1))) AS r
  SELECT sum(revenue) * r FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field` naming
  `dm.rev.revenue`

### Scenario: a dictionary call in a scalar subquery WITH nobody reads changes nothing

- **GIVEN** a change touching `dm.rev.revenue` and a tile whose `raw_sql` is
  `WITH (SELECT dictGet('dm.rev', 'revenue', toUInt64(1))) AS r
  SELECT sum(revenue) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the report names neither a chart impact nor a chart gap for that tile

### Scenario: a star in a scalar subquery WITH the query reads is a hole

- **GIVEN** a change touching `dm.sales_daily.revenue` and a tile whose `raw_sql` is
  `WITH (SELECT * FROM dm.stores) AS m SELECT sum(revenue) * m FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field` and the
  report carries `chart.sql_partial` naming that tile and saying it selects *

### Scenario: a star in a named CTE whose alias is a coincidental column is not a hole

- **GIVEN** a change touching `dm.sales_daily.revenue` and a tile whose `raw_sql` is
  `WITH x AS (SELECT * FROM dm.other) SELECT x FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the report carries no `chart.sql_partial` gap for it

### Scenario: a dictionary call in a named CTE whose alias is a coincidental column changes nothing

- **GIVEN** a change touching `dm.rev.revenue` and a tile whose `raw_sql` is
  `WITH x AS (SELECT dictGet('dm.rev', 'revenue', region) AS a FROM dm.other)
  SELECT x FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the report names neither a chart impact nor a chart gap for that tile

### Scenario: a literal joinGet call is read as the Join table's column

- **GIVEN** a change touching `dm.rev.revenue` and a tile whose `raw_sql` is
  `SELECT joinGet('dm.rev', 'revenue', toUInt64(1)) AS r, sum(revenue) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the change carries a chart impact for that tile with relation `reads_field` naming
  `dm.rev.revenue`, and the report carries no chart gap for it

### Scenario: a joinGet call whose table or column is not a literal leaves the tile read in part

- **GIVEN** a change touching `dm.rev.revenue` and a tile whose `raw_sql` is
  `SELECT joinGet(concat('dm.', 'rev'), 'revenue', toUInt64(1)) AS r, sum(revenue)
  FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the report carries `chart.sql_partial` naming that tile and saying it calls
  `joinGet(...)`, whose Join table read this run could not name

### Scenario: a joinGet call in a CTE the query does not read changes nothing

- **GIVEN** a change touching `dm.rev.revenue` and a tile whose `raw_sql` is
  `WITH x AS (SELECT joinGet('dm.rev', 'revenue', toUInt64(1)) AS r FROM dm.sales_daily)
  SELECT sum(store_id) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the report names neither a chart impact nor a chart gap for that tile

### Scenario: a joinGet call in a scalar subquery WITH nobody reads changes nothing

- **GIVEN** a change touching `dm.rev.revenue` and a tile whose `raw_sql` is
  `WITH (SELECT joinGet('dm.rev', 'revenue', toUInt64(1))) AS r
  SELECT sum(revenue) FROM dm.sales_daily`
- **WHEN** impact is resolved with that dashboard supplied
- **THEN** the report names neither a chart impact nor a chart gap for that tile

### Scenario: a joinGet call in a scalar subquery WITH the query reads is read

- **GIVEN** a change touching `dm.rev.revenue` and a tile whose `raw_sql` is
  `WITH (SELECT joinGet('dm.rev', 'revenue', toUInt64(1))) AS r
  SELECT sum(revenue) * r FROM dm.sales_daily`
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
