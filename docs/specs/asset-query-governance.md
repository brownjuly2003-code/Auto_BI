# Capability: Governance of a reviewed reference query

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

A verified asset anchors a candidate number on the SQL a reviewer signed off. That anchor is
worth exactly as much as its attachment to the governed model: a reviewed query reading a mart
nobody governs, or an undeclared column of a governed mart, produces an attested and reconciled
number computed partly from data the model says nothing about. `assets.invalidated` grades that
attachment. It never guesses — but "the SQL does not decide it" is not the same as "nothing
decides it", and where the governed model decides it, the check reads that too.

## Requirement: a query the parser cannot read is refused at the contract boundary

The system SHALL refuse a reviewed `reference_query` at the contract boundary when the SQL
parser cannot read it because its nesting exceeds the parser's depth, using the same validation
failure path as any other unreadable or non-plain-SELECT statement. It SHALL report a guard
rejection rather than leak a parser `RecursionError`.

### Scenario: parser-depth overflow is a guard rejection

- **GIVEN** a verified asset whose reviewed query is a SELECT nested deeper than the SQL parser
  can read
- **WHEN** the asset is loaded at the contract boundary
- **THEN** validation rejects the asset as a non-plain SELECT and the error names the nesting
  depth; the loader does not expose a bare `RecursionError`

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

For ClickHouse, excluding a bare table reference as a CTE alias SHALL require the same exact
spelling, whether either identifier is quoted. A differently cased CTE SHALL NOT suppress that
physical table from extraction, governance or schema-RBAC, or exempt it from the existing
denylisted bare-table guard. PostgreSQL CTE name comparison and the whole-AST extraction boundary
SHALL retain their existing rules.

### Scenario: a differently cased ClickHouse CTE does not hide a physical table

- **GIVEN** a snapshot governing only `dm.sales_daily` and a ClickHouse reviewed query
  `WITH A AS (SELECT 1) SELECT count(*) FROM a`
- **WHEN** the gate grades the asset against that snapshot
- **THEN** table extraction includes `a` and the findings carry `asset.query_ungoverned`
  (severity review), naming `a`; the ordinary `COUNT(*)` does not add a field remainder
- **AND** replacing the declaration with `WITH a AS (SELECT 1)` makes `FROM a` an exact-case
  CTE reference and leaves no physical-table finding

### Scenario: only an exact-case ClickHouse CTE exempts a denylisted bare name

- **GIVEN** a ClickHouse query declaring `WITH MeRgE AS (SELECT 1 AS x)`
- **WHEN** the SQL guard checks `SELECT x FROM merge`
- **THEN** the existing denylisted bare-table rule rejects `merge`
- **AND** `SELECT x FROM MeRgE` retains the CTE exemption

### Scenario: an unquoted PostgreSQL identifier folds, a quoted one does not

- **GIVEN** a snapshot governing `dm.sales_daily` and two assets whose reviewed queries are on
  the PostgreSQL family, one reading `DM.SALES_DAILY` and one reading `"DM"."SALES_DAILY"`
- **WHEN** the gate grades both
- **THEN** the unquoted one produces no finding and the quoted one produces
  `asset.query_ungoverned`

## Requirement: a CTE exemption stays within its declaring query

After applying the existing CTE name-comparison rule, the system SHALL exclude a bare table
reference as a CTE only when that CTE's declaring query is an ancestor of the reference.
A declaration in a nested query or sibling branch SHALL NOT hide a physical table from
extraction, governance or schema-RBAC, or exempt a denylisted bare name from the SQL guard.
An enclosing WITH SHALL retain its exemption in nested queries and both branches of its
set operation. Whole-AST extraction SHALL still include physical tables in unread CTE bodies;
same-WITH ordering, self-reference, recursion and scalar-WITH classification retain their
existing conservative rules.

### Scenario: a nested CTE cannot hide an outer physical table

- **GIVEN** a snapshot governing only `dm.sales_daily` and a ClickHouse or PostgreSQL query
  `SELECT count(*) FROM a WHERE EXISTS (WITH a AS (SELECT 1) SELECT 1 FROM a)`
- **WHEN** the gate grades the reviewed query
- **THEN** extraction includes the outer physical `a`, and `asset.query_ungoverned`
  (severity review) names `a`; raw-SQL schema-RBAC also sees that physical reference
- **AND** an equivalent nested declaration named `dictionary` does not exempt an outer
  `FROM dictionary` from the existing denylisted bare-table rule

### Scenario: only an enclosing WITH exempts both set-operation branches

- **GIVEN** a query `SELECT count(*) FROM a UNION ALL
  (WITH a AS (SELECT 1) SELECT count(*) FROM a)`
- **WHEN** the system extracts physical tables
- **THEN** `a` from the first branch remains physical
- **AND** `WITH a AS (SELECT 1) SELECT count(*) FROM a UNION ALL SELECT count(*) FROM a`
  retains the CTE exemption in both branches

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

For the PostgreSQL family, an unqualified name that is a whole `GROUP BY` element, including
an element of `ROLLUP`, `CUBE`, `GROUPING SETS`, or a parenthesized grouping list, SHALL be
treated as a possible SELECT output column when that SELECT explicitly aliases an expression
to the same PostgreSQL-folded name. An alias of a column with that same name, or of an
expression containing an aggregate or window outside a nested query, SHALL NOT offer this
alternative. PostgreSQL searches local FROM sources first, the SELECT output list next, and
enclosing levels last. In a LATERAL body, preceding FROM items belong to the enclosing level
and SHALL be considered only after that body's SELECT output list for a grouping name. The
gate SHALL grade the input column when exactly one governed local
source declares it, SHALL leave it contested when several declare it, and SHALL report
`asset.query_fields_unchecked` rather than `asset.query_ungoverned_field` when none declares
it and all local sources are governed tables. With no local source, the output name SHALL
leave no remainder; a CTE, derived table, or ungoverned source SHALL retain the existing
unseen-source treatment. A qualified name, a name inside another grouping expression, or a
name preceded by unary `+` SHALL continue to be read as an input column. ClickHouse and chart
impact SHALL retain their existing results.

For the PostgreSQL family, a whole unqualified `ORDER BY` or `DISTINCT ON` item of a SELECT,
including a parenthesized name, SHALL name that SELECT's output column first when its
PostgreSQL-folded name matches an aliased or plain-column output name. It SHALL NOT add a
second input read; otherwise it SHALL be graded as an input column. A name inside a larger
sort or DISTINCT ON expression SHALL always be graded as an input column, even when an output
has that name. A whole unqualified `ORDER BY` item of a `UNION`, `INTERSECT`, or `EXCEPT` SHALL
name the set operation's output column when its folded name matches an output name of the
leftmost branch, and SHALL NOT add a source read. Other set-operation sort items SHALL keep
their existing result. Quoted names SHALL compare exactly; unquoted names SHALL fold to lower
case. A name preceded by unary `+`, including `+(name)`, SHALL be an expression and SHALL be
graded as an input read rather than an output name. An `ORDER BY` written after a
parenthesized SELECT or set operation SHALL belong to the query inside those parentheses.
A plain-column SELECT output enclosed in parentheses SHALL retain that column's output name.
An unaliased SELECT item preceded by unary `+` SHALL NOT offer that column's name as an output
name; an explicit alias SHALL still offer its declared name.
A direct unaliased `COUNT(...)`, `SUM(...)`, `AVG(...)`, `MIN(...)`, or `MAX(...)` SELECT item,
optionally enclosed in parentheses or followed by `FILTER (WHERE ...)` and/or `OVER (...)`,
SHALL offer its function name as the implicit output name for these sort rules. An explicit
alias SHALL replace that name, and unary `+` before the aggregate SHALL prevent the implicit
name. A quoted function name SHALL retain its spelling: `"Count"(...)` does not expose
lowercase `count`, and `"Sum"(...)` does not expose lowercase `sum`. Aggregate arguments,
`FILTER` predicates, window partitioning and window ordering SHALL remain input reads.
The aggregate output SHALL NOT become a `GROUP BY` output-column alternative.
An unaliased PostgreSQL `ROW_NUMBER() OVER (...)` SELECT item, optionally enclosed in
parentheses, SHALL offer `row_number` as its implicit output name for the same sort rules.
An explicit alias SHALL replace that name, and unary `+` before the window call SHALL
prevent it. A quoted function name SHALL retain its spelling. Window partitioning and
ordering SHALL remain input reads, including an
input column itself named `row_number`. The window output SHALL NOT become a `GROUP BY`
output-column alternative. Other window functions SHALL retain their existing rules.
Window ordering and partitioning, `WITHIN GROUP` ordering, aggregate-internal ordering,
and other `GROUP BY` forms SHALL retain their existing input-resolution rules. ClickHouse and
chart impact SHALL retain their existing results.

### Scenario: an implicit PostgreSQL COUNT output supplies a sort name

- **GIVEN** a PostgreSQL-family reviewed query `SELECT count(*) FROM dm.sales_daily ORDER BY
  count`, with no governed physical column named `count`
- **WHEN** the gate grades its field reads
- **THEN** it reports no physical `count` read and no governance finding; a set operation
  sorting by the same output name of its left branch has no sourceless `count` advisory

### Scenario: an implicit PostgreSQL SUM output supplies a sort name

- **GIVEN** a PostgreSQL-family reviewed query `SELECT sum(revenue) FROM dm.sales_daily ORDER
  BY sum`, with `revenue` governed and no physical column named `sum`
- **WHEN** the gate grades its field reads
- **THEN** it reads `revenue`, reports no physical `sum` read and no governance finding; a
  set operation sorting by the same output name of its left branch has no `sum` advisory

### Scenario: an implicit PostgreSQL windowed SUM output supplies a sort name

- **GIVEN** a PostgreSQL-family reviewed query `SELECT sum(revenue) OVER () FROM
  dm.sales_daily ORDER BY sum`, with `revenue` governed and no physical column named `sum`
- **WHEN** the gate grades its field reads
- **THEN** it reads `revenue`, reports no physical `sum` read and no governance finding

### Scenario: an implicit PostgreSQL ROW_NUMBER output supplies a sort name

- **GIVEN** a PostgreSQL-family reviewed query `SELECT row_number() OVER (PARTITION BY
  region ORDER BY revenue) FROM dm.sales_daily ORDER BY row_number`, with `region` and
  `revenue` governed and no physical column named `row_number`
- **WHEN** the gate grades its field reads
- **THEN** it reads `region` and `revenue`, reports no physical `row_number` read and no
  governance finding

### Scenario: a PostgreSQL sort expression reads its input name

- **GIVEN** `dm.sales_daily` declares `region` but not `r`, and a PostgreSQL query
  `SELECT region AS r FROM dm.sales_daily ORDER BY r COLLATE "C"`
- **WHEN** the gate grades the asset
- **THEN** it grades `region` and reports `asset.query_ungoverned_field` for the input
  `dm.sales_daily.r`

### Scenario: a folded whole PostgreSQL sort name is the output

- **GIVEN** `dm.sales_daily` declares `region` but not `r`, and a PostgreSQL query
  `SELECT region AS R FROM dm.sales_daily ORDER BY r`
- **WHEN** the gate grades the asset
- **THEN** it grades only the SELECT-list `region` and reports no finding for `r`

### Scenario: a set operation sorts by its output name

- **GIVEN** `dm.sales_daily` declares `region` and `category`, and a PostgreSQL query
  `SELECT region FROM dm.sales_daily UNION SELECT category FROM dm.sales_daily ORDER BY region`
- **WHEN** the gate grades the asset
- **THEN** it grades the branch inputs and reports no sourceless advisory for `region`

### Scenario: unary plus makes a PostgreSQL sort or grouping name an input expression

- **GIVEN** `dm.sales_daily` declares `region` but not `r`, and a PostgreSQL query
  `SELECT region AS r FROM dm.sales_daily ORDER BY +r`
- **WHEN** the gate grades the asset
- **THEN** it reports `asset.query_ungoverned_field` for input `dm.sales_daily.r`; the
  same input rule applies to `DISTINCT ON (+r)` and `GROUP BY +r`

### Scenario: sorting after a parenthesized query belongs to that query

- **GIVEN** `dm.sales_daily` declares `region` but not `r`, and a PostgreSQL query
  `SELECT x.r FROM ((SELECT region AS r FROM dm.sales_daily) ORDER BY lower(r)) x`
- **WHEN** the gate grades the asset
- **THEN** it reports `asset.query_ungoverned_field` for input `dm.sales_daily.r`

### Scenario: unary plus on a projection does not create a plain-column output name

- **GIVEN** a PostgreSQL query `SELECT +revenue FROM dm.sales_daily UNION SELECT revenue
  FROM dm.sales_daily ORDER BY revenue`, with `dm.sales_daily.revenue` governed
- **WHEN** the gate grades the asset
- **THEN** it reports `asset.query_fields_unchecked` for the sourceless sort name;
  an explicit `AS revenue` on the leftmost projection removes that remainder

### Scenario: a whole PostgreSQL grouping name may be an output

- **GIVEN** `dm.sales_daily` declares `date` and `revenue` but not `month`, and a PostgreSQL
  query `SELECT date_trunc('month', date) AS month, sum(revenue) FROM dm.sales_daily GROUP BY month`
- **WHEN** the gate grades the asset
- **THEN** it grades `date` and `revenue`, and reports one advisory
  `asset.query_fields_unchecked` naming `month` as a GROUP BY output-column possibility,
  without `asset.query_ungoverned_field`

### Scenario: a declared input wins over the output

- **GIVEN** `dm.sales_daily` declares `region`, and a PostgreSQL query
  `SELECT upper(region) AS region, count(*) FROM dm.sales_daily GROUP BY region`
- **WHEN** the gate grades the asset
- **THEN** it grades the input `region` and reports no finding

### Scenario: a joined grouping name has no declarer

- **GIVEN** the governed tables `dm.sales_daily` and `dm.stores` do not declare `r`, and a
  PostgreSQL query `SELECT upper(s.region) AS r, count(*) FROM dm.sales_daily s JOIN
  dm.stores t ON s.store_id = t.id GROUP BY r`
- **WHEN** the gate grades the asset
- **THEN** it reports `asset.query_fields_unchecked` naming `r` and GROUP BY output-column
  resolution, without a field review

### Scenario: aggregate and window outputs cannot supply a grouping name

- **GIVEN** `dm.sales_daily` declares `region` and `revenue` but neither `total` nor `rn`
- **WHEN** the gate grades PostgreSQL queries grouping by `total` after
  `sum(revenue) AS total`, or by `rn` after `rank() OVER (ORDER BY region) AS rn`
- **THEN** each query reports `asset.query_ungoverned_field` for the corresponding input name

### Scenario: PostgreSQL folds only an unquoted output name

- **GIVEN** `dm.sales_daily` does not declare `month`
- **WHEN** the gate grades `SELECT date_trunc('month', date) AS Month, count(*) FROM
  dm.sales_daily GROUP BY MONTH` and the same query with `AS "Month"` and `GROUP BY month`
- **THEN** the unquoted alias produces a GROUP BY output-column advisory, while the quoted
  alias produces `asset.query_ungoverned_field` naming `dm.sales_daily.month`

### Scenario: a grouping output needs no source

- **GIVEN** a PostgreSQL query `SELECT 1 AS m GROUP BY m`
- **WHEN** the gate grades the asset
- **THEN** it reports no finding and no column remainder

### Scenario: a LATERAL grouping output needs no source of its own

- **GIVEN** a PostgreSQL query `SELECT o.region FROM dm.sales_daily o, LATERAL (SELECT 1
  AS m GROUP BY m) x`
- **WHEN** the gate grades the asset
- **THEN** it reports no finding and no column remainder for `m`, because the preceding
  `dm.sales_daily` is outside the LATERAL body's own FROM

### Scenario: a grouping output precedes an enclosing query

- **GIVEN** a PostgreSQL query `SELECT region FROM dm.sales_daily o WHERE EXISTS (SELECT
  upper(i.region) AS r FROM dm.sales_daily i GROUP BY r)` and no local source declares `r`
- **WHEN** the gate grades the asset
- **THEN** it reports an advisory naming the GROUP BY output-column possibility for `r`,
  rather than an enclosing-query remainder

An unqualified name written in `HAVING` or `QUALIFY` SHALL be graded by those same rules. In
the PostgreSQL family an output alias is not visible in `HAVING`, so the name is an input
column of that query level (or a correlated enclosing level). On ClickHouse a visible alias
still wins and is read through under this requirement's ClickHouse-alias rule; every other
name is an input column of the query level. A window's `PARTITION BY` and `ORDER BY` in
`QUALIFY` are part of that clause. The alias rule does not create a second exception for
these clauses.

The sources of a scope are the ones it **reads** — the tables, derived tables, laterals and
CTEs its own FROM/JOIN clauses name. A CTE its FROM/JOIN does not name SHALL NOT count as a
source of that scope, whether its WITH clause declared it or an enclosing query did: among the
sources of a scope the engine resolves a name against the ones it reads, and counting it would
leave a name undecidable that this scope otherwise decides. A CTE no scope in the statement names at all is
the limiting case of the same rule — a query nobody ran — and chart impact reuses the rule there
to decide whether a star is a hole.

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

Widening the candidate set of a scope inside a lateral body changes what the model can decide
there, so the same statement can grade differently after this change — stricter as well as
looser, and between the two finding codes as well. The scenarios below carry the cases. The
one transition widening cannot produce is a clean grade turning into
`asset.query_ungoverned_field`: widening only adds candidates, so a name one governed source
already declared keeps that declarer, and a name resolved to a derived table keeps that source
in scope — either way the model never ends up with a governed source that declares nothing.
That is why the verifier and check versions moved.

### Scenario: a PostgreSQL HAVING input is a read

- **GIVEN** a snapshot governing `dm.sales_daily`, declaring `region` and `revenue` but not
  `cost`, and a PostgreSQL-family reviewed query `SELECT region FROM dm.sales_daily GROUP BY
  region HAVING sum(cost) > 0`
- **WHEN** the gate grades the asset
- **THEN** the findings carry `asset.query_ungoverned_field` naming `dm.sales_daily.cost`

### Scenario: a ClickHouse QUALIFY window input is a read

- **GIVEN** a snapshot governing `dm.sales_daily`, declaring `region` and `revenue` but not
  `cost`, and a ClickHouse reviewed query `SELECT region FROM dm.sales_daily QUALIFY
  row_number() OVER (PARTITION BY cost ORDER BY cost) = 1`
- **WHEN** the gate grades the asset
- **THEN** the findings carry `asset.query_ungoverned_field` naming `dm.sales_daily.cost`

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

### Scenario: a lateral nested in a lateral body still reads the outer sibling mart

- **GIVEN** a snapshot declaring `revenue` on `dm.sales_daily` and not on `dm.stores`, and a
  reviewed query `SELECT t.x FROM dm.sales_daily AS a, LATERAL (SELECT 1 AS x FROM
  dm.stores AS b, LATERAL (SELECT sum(revenue) AS y FROM dm.stores AS d) AS u) AS t`
- **WHEN** the gate grades the asset
- **THEN** there is no finding — the nested lateral still reads the outer lateral's preceding
  FROM item, so `revenue` is graded as a column of `dm.sales_daily`

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

### Scenario: a lateral branch with no FROM of its own is decided by the sibling mart

- **GIVEN** a snapshot governing `dm.sales_daily` and not declaring `discount` on it, and a
  reviewed query `SELECT t.x FROM dm.sales_daily AS a, LATERAL (SELECT sum(discount) AS x
  UNION ALL SELECT 0 AS x) AS t`
- **WHEN** the gate grades the asset
- **THEN** the findings carry `asset.query_ungoverned_field` naming `dm.sales_daily.discount`:
  a branch with no FROM of its own is decided by the sibling walked in from the enclosing
  lateral, so a name that sibling does not declare is a review finding rather than an
  advisory that the branch offers no source of its own

### Scenario: a derived-table name inside a lateral body stays unchecked

- **GIVEN** a snapshot governing `dm.sales_daily`, and a reviewed query `SELECT t.x FROM
  dm.sales_daily AS a, LATERAL (SELECT y AS x FROM (SELECT 1 AS y) AS d UNION ALL SELECT 0
  AS x) AS t`
- **WHEN** the gate grades the asset
- **THEN** the findings carry `asset.query_fields_unchecked` naming `y`: the name's nearest
  source is a derived table written inside the lateral body, and it now sits beside a table
  candidate, so the model cannot attribute it

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
reviewed query could not be graded at field level — a `*` that reads source columns, or a name
neither the statement nor the model attributes — and SHALL state which part and why, so a
partial check never reads as a clean one. The remainder is counted per *reference*, not per
name: one reference the model
resolves SHALL NOT cover another reference of the same name that nothing resolves. The reason
SHALL distinguish a name several governed sources declare, a scope holding a source the
snapshot cannot see into, a qualifier no enclosing scope declares, a scope with no source
of its own, and a name that may have been read from the enclosing query — a wrong reason sends
the reader after a fix the query does not need.

`COUNT(*)` is not a column-reading star: it counts rows, so it does not make the field check
partial. `COUNT(DISTINCT *)` remains a star because its distinct value depends on every source
column where the engine accepts it.

On ClickHouse, `COLUMNS('regex')` selects physical columns without enumerating them in the
reviewed SQL. The field check SHALL report `asset.query_fields_unchecked` and name
`COLUMNS(...)` as the unresolved selector, including when it is inside an expression or a
nested query. The check SHALL NOT treat matching columns declared by the snapshot as the
complete expansion. Named column reads beside the selector are graded normally. Like `*`,
governance currently scans the whole statement tree for this remainder, including an
unreferenced CTE or scalar `WITH`; reachability precision is not changed by this rule.
The rule applies only to ClickHouse AST selectors, not to PostgreSQL-family queries or
literal/comment text that merely contains `COLUMNS(...)`.

For ClickHouse and the PostgreSQL family, a `JOIN ... USING (key, ...)` key is a shared
column read whose SQL form does not identify one table. The field check SHALL report
`asset.query_fields_unchecked` (advisory), name every key gathered from the JOIN AST, and say
that the key cannot be attributed to one table. Explicit neighboring column reads SHALL still
be graded normally, including undeclared-field findings. This remainder SHALL also be found in
nested JOINs; `ON` joins and literal or comment text containing `JOIN USING` do not create it.

For the PostgreSQL family, `NATURAL JOIN` chooses equality keys from column names exposed
by both joined sources. The JOIN clause does not enumerate those names, and this check does
not expand both sources to prove a complete list. The field check SHALL report
`asset.query_fields_unchecked` (advisory) and explain that the names may depend on the
physical schema. It SHALL NOT infer a complete key list from the semantic snapshot.
Explicit neighboring column reads SHALL still be graded normally, including undeclared-field
findings. This remainder SHALL be found in nested JOINs throughout the AST; `ON` joins and
literal or comment text mentioning `NATURAL JOIN` do not create it. ClickHouse behavior is
unchanged.

On ClickHouse, a `SETTINGS` item names a query setting and gives it a value; neither side is a
table-column read, at any SELECT, UNION branch, subquery or CTE query level. A visible alias
with the setting's name is not read through. `additional_table_filters` is the exception to a
complete field check: its value holds row-filter SQL the engine applies to a table, and the
reader does not parse that predicate. It SHALL therefore produce the advisory
`asset.query_fields_unchecked` naming the setting. `additional_result_filter` filters result
columns and does not add a table-column read or a partial-field-check reason.

### Scenario: an ordinary ClickHouse setting is not a column read

- **GIVEN** a snapshot declaring `region` and `revenue` on `dm.sales_daily`, and a reviewed
  query `SELECT region, sum(revenue) FROM dm.sales_daily GROUP BY region SETTINGS max_threads = 8`
- **WHEN** the gate grades the asset
- **THEN** it carries no finding: `max_threads` is a setting, not
  `dm.sales_daily.max_threads`

### Scenario: a ClickHouse row-filter setting makes the field check partial

- **GIVEN** a snapshot declaring `region` on `dm.sales_daily`, and a reviewed query
  `SELECT region FROM dm.sales_daily SETTINGS additional_table_filters = {'dm.sales_daily': 'cost > 0'}`
- **WHEN** the gate grades the asset
- **THEN** it carries `asset.query_fields_unchecked` naming `additional_table_filters`, not a
  review finding for `dm.sales_daily.additional_table_filters`

### Scenario: a row count is not a star

- **GIVEN** a snapshot declaring `region` on `dm.sales_daily`, and a reviewed query
  `SELECT region, count(*) FROM dm.sales_daily GROUP BY region`
- **WHEN** the gate grades the asset
- **THEN** it carries no partial-field-check advisory: `COUNT(*)` counts rows rather than
  source columns

### Scenario: the query selects a star

- **GIVEN** a reviewed query `SELECT * FROM dm.sales_daily`
- **WHEN** the gate grades the asset
- **THEN** the findings carry `asset.query_fields_unchecked` and its detail says the columns read
  depend on the physical table rather than on the SQL

### Scenario: a ClickHouse regex selector leaves a field-check remainder

- **GIVEN** a snapshot governing `dm.sales_daily` and a reviewed ClickHouse query
  `SELECT COLUMNS('rev.*') FROM dm.sales_daily`
- **WHEN** the gate grades the asset
- **THEN** it carries `asset.query_fields_unchecked` (advisory), with a reason naming
  `COLUMNS(...)` and the physical columns that the SQL does not enumerate

### Scenario: a named field beside a selector is still graded

- **GIVEN** a snapshot that does not declare `internal_cost_raw` on `dm.sales_daily`, and a
  reviewed ClickHouse query `SELECT COLUMNS('rev.*'), internal_cost_raw FROM dm.sales_daily`
- **WHEN** the gate grades the asset
- **THEN** it carries both `asset.query_ungoverned_field` (review) for the named field and
  `asset.query_fields_unchecked` (advisory) for the selector

### Scenario: a resolved name does not cover an unresolvable one beside it

- **GIVEN** a snapshot declaring `category` on `dm.sales_daily` and not on `dm.stores`, and a
  reviewed query `SELECT a.region, category, z.category FROM dm.sales_daily AS a JOIN dm.stores
  AS b ON a.store_id = b.id`
- **WHEN** the gate grades the asset
- **THEN** the findings carry `asset.query_fields_unchecked` naming `z.category`, and its detail
  says that no enclosing scope declares that qualifier rather than that a source was invisible

### Scenario: a JOIN USING key is an unattributed field-check remainder

- **GIVEN** a snapshot governing `dm.sales_daily` and a reviewed query
  `SELECT a.region FROM dm.sales_daily AS a JOIN dm.sales_daily AS b USING (region)`
- **WHEN** the gate grades the asset on ClickHouse or the PostgreSQL family
- **THEN** the findings carry only `asset.query_fields_unchecked` (advisory), and its detail
  names `region` and says that the `JOIN USING` key cannot be attributed to one table

### Scenario: a PostgreSQL NATURAL JOIN has schema-dependent keys

- **GIVEN** a snapshot governing `dm.sales_daily` and a reviewed PostgreSQL-family query
  `SELECT a.region FROM dm.sales_daily AS a NATURAL JOIN dm.sales_daily AS b`
- **WHEN** the gate grades the asset
- **THEN** the findings carry `asset.query_fields_unchecked` (advisory), explaining that
  `NATURAL JOIN` keys may depend on the physical schema without naming guessed keys

### Scenario: explicit derived-source outputs do not imply a physical-schema dependency

- **GIVEN** a reviewed PostgreSQL-family query
  `SELECT 1 FROM (SELECT 1 AS k) AS a NATURAL JOIN (SELECT 1 AS k) AS b`
- **WHEN** the gate grades the asset
- **THEN** `asset.query_fields_unchecked` explains that this check does not expand both
  source outputs and that the matching names may depend on a physical schema; it does not
  assert that the explicit `k` depends on one

### Scenario: a subquery's bare name no local source declares is unchecked, not ungoverned

- **GIVEN** a snapshot governing `dm.sales_daily` and `dm.stores`, declaring `store_id` on the
  first and only `id` on the second, and a reviewed query `SELECT a.region FROM dm.sales_daily
  AS a WHERE EXISTS (SELECT 1 FROM dm.stores AS b WHERE b.id = store_id)`
- **WHEN** the gate grades the asset on any engine
- **THEN** the findings carry `asset.query_fields_unchecked` naming `store_id` and saying it may
  be read from the enclosing query, and carry no `asset.query_ungoverned_field`: the valid
  reviewed query is not rejected over a column the outer query declares

## Requirement: a ClickHouse alias is read through, not as a column

On ClickHouse the system SHALL NOT treat a bare name that is an alias visible at the place
it is written as a column read — neither attributed nor unattributed — except inside that
alias's own defining expression, where the name is the column. Inside the definition of a
query-level alias X, a WITH expression read through from it included, X is the column.
Identifiers are compared as written (case-sensitive). A visible alias wins over a table
column of the same name.

A SELECT-list alias, an inline `<expr> AS name` written anywhere else in its Select level,
and `[LEFT] ARRAY JOIN <expr> AS name` SHALL be visible in the query level that defines it —
including other SELECT expressions, WHERE, PREWHERE, GROUP BY, HAVING, QUALIFY, ORDER BY,
LIMIT BY and JOIN conditions — and SHALL NOT be visible in a subquery. The inline form may be
nested inside a SELECT-list expression. A top-level SELECT-list declaration wins when it has
the same name as an inline or ARRAY JOIN declaration; otherwise the first inline or ARRAY JOIN
declaration sqlglot yields wins. Every definition is still graded where it is written. A
`WITH <expr> AS alias` (and `WITH (SELECT …) AS alias`) SHALL be visible in the query that
declares it and in every subquery and UNION branch below it.

A parenthesized `[LEFT] ARRAY JOIN` expression SHALL have the same column reads and alias
visibility as its unparenthesized expression: `(e AS t)` declares `t`, and `(e)` declares
no alias. For `(e AS t) AS a`, the outer alias SHALL replace `t`, so a later `t` SHALL be
graded as a column reference. A parenthesized SELECT used as an ARRAY JOIN item SHALL
retain its own query scope.

When a scope references an expression-form `WITH <expr> AS a` — directly, or through
another alias whose definition references it — the system SHALL grade every column written
in `<expr>` as if written in that referencing scope, under the same `_column_source` /
`_ambiguous_ref` rules. A query nested in `<expr>` SHALL be graded as the same subquery
written at the reference: its own FROM, with bare names enclosed toward the referencing
scope. Referenced from two levels, the columns are graded at each. An alias nothing
references reads nothing. A query-level alias needs no read-through: its definition is
already graded where it is written. A query-level alias that can reach itself through
query-level or WITH definitions is a column wherever it is referenced, a WITH body in the
read-through included. A WITH reference that closes a cycle on the read-through path
in that referencing scope is a column there, and the definitions on the way are still
read. A cycle is keyed by declaration and referencing scope, so a cycle that exists
only in an inner scope SHALL NOT turn an outer reference of the same alias into a
column. The read-through has a depth cap
and an expansion budget; a reference past them is reported unattributed under its own
name, so the gate names a source it did not read rather than skipping it. A cut expansion
is not memoised: a later shallower reference expands it again. A `WITH (SELECT …) AS a`
keeps its own scope and FROM; only the reference `a` is the alias.

The PostgreSQL family is unchanged: there a WITH defines CTEs only and WHERE cannot read a
SELECT alias, so a bare name is a column. A visible ClickHouse query-level alias written on
the right-hand side of `IN` is an alias.

An ARRAY JOIN without `AS name` keeps its array expression as a column, and
`ARRAY JOIN tags AS tags` reads the defining `tags` as that column. A lambda parameter is not
a column, even when named like a visible alias. A name declared both as a WITH alias and as a
SELECT alias at one query level is
read as a column: `WITH revenue AS X, X * 2 AS w SELECT w AS X FROM dm.sales_daily`
carries `asset.query_ungoverned_field` naming `dm.sales_daily.X`. That is the
false-reject direction — it names a column nobody has and hides no read — on a statement
ClickHouse most likely rejects; the risk is accepted (F-T-13-17).

### Scenario: a literal WITH alias in WHERE is not a column of the mart

- **GIVEN** a snapshot declaring `region` and `revenue` on `dm.sales_daily`, and a reviewed
  query `WITH 100 AS floor_value SELECT region, sum(revenue) AS m FROM dm.sales_daily
  WHERE revenue > floor_value GROUP BY region`
- **WHEN** the gate grades the asset
- **THEN** there is no finding — `floor_value` is the WITH alias, not `dm.sales_daily.floor_value`

### Scenario: a WITH alias on the right of IN is not a column of the mart

- **GIVEN** a snapshot declaring `region` and `revenue` on `dm.sales_daily`, and a reviewed
  query `WITH ['north', 'south'] AS allowed SELECT region, sum(revenue) AS m FROM
  dm.sales_daily WHERE region IN allowed GROUP BY region`
- **WHEN** the gate grades the asset
- **THEN** there is no finding — `allowed` is the WITH alias, not `dm.sales_daily.allowed`

### Scenario: a SELECT alias used in another SELECT expression is not a column of the mart

- **GIVEN** a snapshot declaring `region` and `revenue` on `dm.sales_daily`, and a reviewed
  query `SELECT region, sum(revenue) AS m, m * 2 AS m2 FROM dm.sales_daily GROUP BY region`
- **WHEN** the gate grades the asset
- **THEN** there is no finding — `m` is the SELECT alias, not `dm.sales_daily.m`

### Scenario: a SELECT alias in WHERE is not a column of the mart

- **GIVEN** a snapshot declaring `region` and `revenue` on `dm.sales_daily`, and a reviewed
  query `SELECT region AS r, sum(revenue) FROM dm.sales_daily WHERE r != '' GROUP BY r`
- **WHEN** the gate grades the asset
- **THEN** there is no finding — `r` is the SELECT alias; `region` is the column that was read

### Scenario: an inline alias in WHERE is not a column of the mart

- **GIVEN** a snapshot declaring `region` and `revenue` on `dm.sales_daily`, and a reviewed
  query `SELECT region, sum(revenue) FROM dm.sales_daily WHERE (revenue AS y) > 1 AND y < 10
  GROUP BY region`
- **WHEN** the gate grades the asset
- **THEN** there is no finding — `y` is the inline alias; `revenue` is graded where its alias
  is defined

### Scenario: an ARRAY JOIN alias is not a column of the mart

- **GIVEN** a snapshot declaring `revenue` but not `tags` or `a` on `dm.sales_daily`, and a
  reviewed query `SELECT a, sum(revenue) FROM dm.sales_daily ARRAY JOIN tags AS a GROUP BY a`
- **WHEN** the gate grades the asset
- **THEN** the finding names `dm.sales_daily.tags`, and does not name `dm.sales_daily.a` — the
  array source is still a read, while `a` is the unfolded-element alias

### Scenario: a parenthesized ARRAY JOIN item keeps its expression alias

- **GIVEN** a snapshot declaring `tags` on `dm.sales_daily` and a reviewed query
  `SELECT t FROM dm.sales_daily ARRAY JOIN (tags AS t)`
- **WHEN** the gate grades the asset
- **THEN** there is no finding: `tags` is read and `t` is the ARRAY JOIN alias

### Scenario: an outer ARRAY JOIN alias replaces the inner alias

- **GIVEN** a snapshot declaring `tags` but not `t` on `dm.sales_daily` and a reviewed query
  `SELECT t, a FROM dm.sales_daily ARRAY JOIN (tags AS t) AS a`
- **WHEN** the gate grades the asset
- **THEN** `asset.query_ungoverned_field` names `dm.sales_daily.t`; `a` is the alias and
  `tags` is the array source

### Scenario: a parenthesized ARRAY JOIN query retains its scope

- **GIVEN** a reviewed query
  `SELECT a FROM dm.sales_daily ARRAY JOIN (SELECT ['p', 'q']) AS a`
- **WHEN** the gate grades the asset
- **THEN** the parenthesized SELECT keeps its own query scope and is not treated as an
  expression alias declaration

### Scenario: a referenced expression WITH is read through to the undeclared column

- **GIVEN** a snapshot governing `dm.sales_daily` and not declaring `internal_cost_raw` on
  it, and a reviewed query `WITH internal_cost_raw * 2 AS c2 SELECT region, sum(c2) FROM
  dm.sales_daily GROUP BY region`
- **WHEN** the gate grades the asset
- **THEN** the findings carry `asset.query_ungoverned_field` naming
  `dm.sales_daily.internal_cost_raw`, and do not name `c2`

### Scenario: a query nested in a referenced expression WITH is graded as at the reference

- **GIVEN** a snapshot governing `dm.sales_daily` and not declaring `internal_cost_raw` on
  it, and a reviewed query `WITH 1 + (SELECT max(internal_cost_raw) FROM dm.sales_daily)
  AS s SELECT region, sum(revenue) + s AS m FROM dm.sales_daily GROUP BY region`
- **WHEN** the gate grades the asset
- **THEN** the findings carry `asset.query_fields_unchecked` (advisory) naming
  `internal_cost_raw` as unresolved — the nested query is graded as the same subquery
  written at the reference, so the name is enclosed, not a silent pass and not
  `asset.query_ungoverned_field` on the alias

### Scenario: a WITH alias chain past the read-through depth cap is unattributed under its name

- **GIVEN** a snapshot governing `dm.sales_daily` and a reviewed query
  `WITH revenue AS a0, a0 + 1 AS a1, …, a{N-1} + 1 AS aN SELECT aN FROM dm.sales_daily`
  where N exceeds the read-through depth cap
- **WHEN** the gate grades the asset
- **THEN** the findings carry `asset.query_fields_unchecked` naming the alias at the cap,
  with the reason that it "could not be attributed to one table without guessing, and the
  scope offers a source this snapshot cannot see into" — the unexpanded definition is that
  source — not a silent pass and not `dm.sales_daily.revenue`. A later shallower reference
  to an alias whose deeper expansion was cut is expanded again; a cut expansion is not
  memoised.

### Scenario: a SELECT alias that keeps the column name still reads that column

- **GIVEN** a snapshot governing `dm.sales_daily` and not declaring `internal_cost_raw` on
  it, and a reviewed query `WITH internal_cost_raw * 2 AS c2 SELECT region, sum(c2) AS
  internal_cost_raw FROM dm.sales_daily GROUP BY region`
- **WHEN** the gate grades the asset
- **THEN** the findings carry `asset.query_ungoverned_field` naming
  `dm.sales_daily.internal_cost_raw` — inside the definition of SELECT alias
  `internal_cost_raw`, the WITH body is part of that definition, so the name is the column

### Scenario: a cycle across SELECT and WITH aliases is read as columns

- **GIVEN** a snapshot governing `dm.sales_daily` and a reviewed query
  `WITH r + 1 AS w SELECT revenue * 2 AS r, w AS revenue FROM dm.sales_daily`
- **WHEN** the gate grades the asset
- **THEN** the findings carry `asset.query_ungoverned_field` naming `dm.sales_daily.r` —
  both `r` and `revenue` are SELECT aliases on a cycle through WITH, so each is a column
  wherever it is referenced; `revenue` is declared, `r` is not

### Scenario: an alias self-reference still reads the column

- **GIVEN** a snapshot declaring `revenue` on `dm.sales_daily`, and a reviewed query
  `SELECT sum(revenue) AS revenue FROM dm.sales_daily`
- **WHEN** the gate grades the asset
- **THEN** there is no finding — inside the defining expression the name is the column

### Scenario: a visible alias wins over a table column of the same name

- **GIVEN** a snapshot declaring `region` on `dm.sales_daily`, and a reviewed query
  `WITH 1 AS region SELECT region FROM dm.sales_daily`
- **WHEN** the gate grades the asset
- **THEN** there is no finding — `region` is the WITH alias, not a column of the mart

### Scenario: a SELECT alias is not visible in a subquery

- **GIVEN** a snapshot governing `dm.sales_daily` and `dm.stores`, and a reviewed query
  `SELECT region AS r FROM dm.sales_daily WHERE (SELECT r FROM dm.stores LIMIT 1) != ''`
- **WHEN** the gate grades the asset
- **THEN** the findings carry `asset.query_fields_unchecked` naming `r` — the subquery's
  bare name is still graded as a column, because SELECT aliases are not visible there

### Scenario: a WITH alias is visible in a subquery

- **GIVEN** a snapshot declaring `region` on `dm.sales_daily`, and a reviewed query
  `WITH 1 AS k SELECT region FROM dm.sales_daily WHERE (SELECT k FROM dm.stores LIMIT 1) != 0`
- **WHEN** the gate grades the asset
- **THEN** there is no finding — `k` is the WITH alias, visible in the subquery

### Scenario: an unreferenced expression WITH reads nothing

- **GIVEN** a snapshot governing `dm.sales_daily` and not declaring `internal_cost_raw` on
  it, and a reviewed query `WITH internal_cost_raw * 2 AS c2 SELECT region FROM
  dm.sales_daily`
- **WHEN** the gate grades the asset
- **THEN** there is no finding — nothing references `c2`, so the expression is not read

### Scenario: ClickHouse alias names are compared as written

- **GIVEN** a snapshot governing `dm.sales_daily` and not declaring `K` on it, and a
  reviewed query `WITH 1 AS k SELECT K FROM dm.sales_daily`
- **WHEN** the gate grades the asset
- **THEN** the findings carry `asset.query_ungoverned_field` naming `dm.sales_daily.K` —
  `K` is not the alias `k`

### Scenario: the PostgreSQL family still reads a SELECT alias as a column

- **GIVEN** a snapshot declaring `region` and `revenue` on `dm.sales_daily`, and a
  PostgreSQL-family asset whose reviewed query is `SELECT region, sum(revenue) AS m, m * 2
  AS m2 FROM dm.sales_daily GROUP BY region`
- **WHEN** the gate grades the asset
- **THEN** the findings carry `asset.query_ungoverned_field` naming `dm.sales_daily.m` —
  WHERE cannot read a SELECT alias there, so the bare name is a column exactly as before

### Scenario: an inner-only WITH cycle does not hide the outer read

- **GIVEN** a snapshot declaring `revenue` on `dm.sales_daily`, and a reviewed query
  `WITH b + 1 AS a, revenue AS b SELECT a, (WITH a + 1 AS b SELECT a FROM
  dm.sales_daily) AS z FROM dm.sales_daily`
- **WHEN** the gate grades the asset
- **THEN** the outer reference `a` is graded as `dm.sales_daily.revenue`, as it is in
  the outer statement alone — the inner `WITH` that shadows `b` and cycles on `a`
  does not turn the outer alias into `dm.sales_daily.a`

## Requirement: a table on the right of IN is read

On ClickHouse the system SHALL grade `x [GLOBAL] [NOT] IN <name>`, where `<name>` is
the identifier sqlglot parks in `In.args["field"]` (bare or qualified) and is not a
visible alias, as `x IN (SELECT * FROM <name>)`: the same table refs, the same column
reads, the same ungoverned findings. A visible alias on that side SHALL remain an
alias. A CTE named `<name>` in scope SHALL be the CTE, exactly as the same name in
FROM would be. Literal lists (`IN (1, 2)`, `IN ('a')`, `IN tuple(...)`) and subquery
forms SHALL be unchanged. The PostgreSQL family SHALL be unchanged. The function form
`in(x, t)` does not parse in sqlglot 30.14.0 and SHALL remain a parse error.

Two known departures from the subquery form stay open. A visible scalar WITH, SELECT, inline
or ARRAY JOIN alias on the right of `IN` wins over a named CTE of the same name, so
`WITH x AS (SELECT region FROM dm.sales_daily), ('a') AS x … WHERE region IN x` stays
silent where `WHERE region IN (SELECT * FROM x)` opens the CTE and names the star
(F-T-14-3); it needs one name declared twice, which ClickHouse most likely rejects. A
FROM alias named on the right of `IN` is read as a table of that name, as it is in the
subquery form: `SELECT region FROM dm.sales_daily AS rev WHERE region IN rev` carries
`asset.query_ungoverned` naming `rev` (F-T-14-1). That direction over-reports a table
nobody has and never hides a read.

### Scenario: a bare table on the right of IN is the table, not a column of the mart

- **GIVEN** a snapshot governing `dm.sales_daily` and not `rev`, and a reviewed query
  `SELECT region, sum(revenue) AS m FROM dm.sales_daily WHERE region IN rev GROUP BY
  region`
- **WHEN** the gate grades the asset
- **THEN** the findings carry `asset.query_ungoverned` naming `rev` and
  `asset.query_fields_unchecked` for the star, and do not carry
  `asset.query_ungoverned_field` naming `dm.sales_daily.rev` — the grade is the same
  as `WHERE region IN (SELECT * FROM rev)`

### Scenario: a qualified table on the right of IN is read

- **GIVEN** a snapshot governing `dm.sales_daily` and not `dm.rev`, and a reviewed
  query `SELECT region, sum(revenue) AS m FROM dm.sales_daily WHERE region IN dm.rev
  GROUP BY region`
- **WHEN** the gate grades the asset
- **THEN** the findings carry `asset.query_ungoverned` naming `dm.rev` and
  `asset.query_fields_unchecked` for the star — the same as
  `WHERE region IN (SELECT * FROM dm.rev)`

### Scenario: a SELECT alias on the right of IN stays an alias

- **GIVEN** a snapshot declaring `region` on `dm.sales_daily`, and a reviewed query
  `SELECT region AS r FROM dm.sales_daily WHERE region IN r`
- **WHEN** the gate grades the asset
- **THEN** there is no finding — `r` is the SELECT alias, not table `r`

### Scenario: an ARRAY JOIN alias on the right of IN stays an alias

- **GIVEN** a snapshot declaring `region` on `dm.sales_daily`, and a reviewed query
  `SELECT region FROM dm.sales_daily ARRAY JOIN tags AS a WHERE region IN a`
- **WHEN** the gate grades the asset
- **THEN** no table `a` or field `dm.sales_daily.a` is named; `a` is the ARRAY JOIN alias

### Scenario: a CTE named on the right of IN is the CTE

- **GIVEN** a snapshot governing `dm.sales_daily`, and a reviewed query
  `WITH x AS (SELECT region FROM dm.sales_daily) SELECT region FROM dm.sales_daily
  WHERE region IN x`
- **WHEN** the gate grades the asset
- **THEN** the findings carry `asset.query_fields_unchecked` for the star and do not
  name table `x` — `x` is the CTE, the same as `WHERE region IN (SELECT * FROM x)`
