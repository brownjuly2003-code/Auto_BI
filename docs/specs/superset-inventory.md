# Capability: The delivered Superset dashboards as the specs the gate reads

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

`decision verify --dashboards <dir>` reads one `DashboardSpec` per delivered ref
([dashboard-spec-paths](dashboard-spec-paths.md)), and until ADR 0012, D6 an operator wrote each
by hand: a ref without a spec is `bi.spec_missing`, and the gate answers for no tile of it.
`auto_bi decision inventory` reads the dashboards a Superset holds and writes those specs. It is a
read model: the spec has to get right what each tile reads and computes, because that is all the
gate asks of it (chart impact, `bi.contract`). It reads by `GET` only, after its login, writes no
BI and reaches no engine, and adds no verdict: the gate grades the specs it writes.

The reader is `auto_bi/inventory/superset.py`; the use case is `inventory` in
`auto_bi/decision/use_cases.py`. The Superset is the one pinned (4.1.2), reached through the
settings `AUTO_BI_SUPERSET_URL`, `AUTO_BI_SUPERSET_USER` and `AUTO_BI_SUPERSET_PASSWORD`.

## Requirement: the inventory asks Superset by GET after its login

For each `--dashboard REF=ID_OR_SLUG` (REF is `dashboard://path@version`) the inventory SHALL read
`GET /api/v1/dashboard/{id_or_slug}`, `.../charts` and `.../datasets`, and send no other request
but the client's login — `POST /api/v1/security/login`, then `GET /api/v1/security/csrf_token/` —
made before the first request and once more after an answer 401, which repeats that request once.

An answer that is not the shape Superset 4.1 gives where the inventory depends on it — not JSON,
JSON nested deeper than the inventory reads, no `result`, no object or list of objects where one
is due, no integer `id`, a `form_data`, `json_metadata`, `columns` or `metrics` of another type, a
dataset `sql` that is not text — SHALL be said, never guessed around. What it only names or
records is read leniently: a title, a slice name, a schema, a table name, a native filter's name
or a `backend` that is missing or not text is read as empty (a chart is then titled
`chart_<id>`, a dataset refused for naming no schema), `changed_on` as its text, and a dataset
`database` that is not an object with an integer `id` as naming none.

An HTTP error or a refused login SHALL be an error that names the request and the HTTP status
(`login failed: HTTP 401`, `csrf fetch failed: HTTP 500`, `GET <path> -> HTTP 404`); a Superset
not reached, or an answer that is not its JSON, SHALL name the `GET` it was making
(`GET <path>: ConnectError`), a login made for it included; never a body or a credential.

## Requirement: a chart is typed or read as SQL

Each chart of a dashboard SHALL be written in one of two forms, and in no third:

- **typed** — the exact IR `ChartSpec`. It is the only form the gate can name `computes_metric`;
  it SHALL be chosen only when the chart is one the IR says exactly, and the spec passes
  `validate_spec` against the model and the Superset contract compiler. A typed chart that fails
  either is read SQL, not refused: a spec `bi.contract` refuses would block every run for a
  reason no change made;
- **read SQL** — every other chart: viz `table` with a `raw_sql` that is `SELECT *` plus every SQL
  fragment of the chart and every saved metric and calculated column of its dataset, each on its
  own lines, `FROM schema.table` or `FROM (<dataset sql>) AS virtual_table`, the dataset SQL
  without the tabs, line ends, spaces and semicolons Superset 4.1.2 strips off its ends. It is
  not what Superset runs; it reads at least what Superset reads, so the gate names the tile
  `chart.sql_partial`. It SHALL pass the SQL guard in the ClickHouse dialect, the one chart
  impact reads operator SQL in.

A tile SHALL be read as the dashboard opens it. What a viewer picks after that — a native
filter's value, a cross-filter, a drill, the stacking an area chart's extra controls offer — is
not read. A key a chart's saved `form_data` lacks is read as the dashboard fills it in, with the
default of its viz's control: a `row_limit` (below), an empty `temporal_columns_lookup`, an
`aggregateFunction` of `Sum`.

A chart SHALL be typed only when all of these hold:

- the viz is one of `big_number_total` (`big_number`), `pie`, `table` in aggregate mode,
  `echarts_timeseries_line` (`line`, no groupby), `echarts_timeseries_bar` (`bar`, or `stacked_bar`
  with a groupby and a stack), `echarts_area` (`area`, the groupby is the series),
  `pivot_table_v2` (`pivot`, rows and columns) or `heatmap_v2` (`heatmap`, the x axis then the
  groupby); a line, bar, area or heatmap has an `x_axis` — without one Superset groups it by its
  legacy time column;
- the dataset is physical and has a schema;
- every column the chart reads — an axis, a groupby, a pivot row or column, the column of an
  adhoc metric, a filter's subject, `granularity_sqla` — is a plain column the dataset answer describes, with an
  `is_dttm` of `true` or `false`: Superset trims a dashboard's datasets to the columns some
  `form_data` keys name, and of a column it left out the expression, the type and whether it is
  temporal are not known; no calculated column is read anywhere, and no column is grouped twice;
- every metric is a SIMPLE `SUM`, `AVG`, `MIN`, `MAX`, `COUNT` or `COUNT_DISTINCT` of a plain
  column, or a saved metric that is exactly `AGG(column)` or `COUNT(DISTINCT column)`; `COUNT(*)`
  is not typed, the IR has no such measure. No two metrics have one label (a saved metric's
  name, an adhoc metric's own label, else `AGG(column)`): Superset computes the first of them
  only. A pivot's measures are `SUM` or `COUNT` only: it sums the records it folds into one cell,
  and a NULL and an empty text share one;
- every filter is a SIMPLE `WHERE` with `==`, `!=`, `IN`, `>=` or `<=` on a value Superset compares
  the column with as written: not `<NULL>` or `<empty string>`, not text with a tab or a line feed
  at an end (Superset strips them), not text on a column Superset casts the value for (a number,
  a bool), not a number on a time column (Superset reads it as epoch milliseconds), and on a bool
  column no number but 0 and 1;
- the chart's `time_range` is absent, empty or `No filter` (Superset 4.1.2 reads an empty one as
  `No filter`), and each time-range filter's is exactly `No filter`;
- each post-processing key holds the value at which Superset 4.1.2 applies none: `rolling_type`,
  `contributionMode`, `resample_method`, `resample_rule` and `forecastEnabled` empty or `false`
  (`rolling_type` also `None` or `null` as text; `contributionMode` tested for truth, so the text
  `null` is a share), `comparison_type` empty, `false` or `values`, `time_compare` null or `[]`,
  `aggregateFunction` absent or `Sum`;
- `granularity_sqla`, when set, is a temporal column — Superset puts the dataset's main time
  column in place of one that is not — and is the x axis itself when the x axis is temporal; no
  filter is on the column it makes Superset drop the filters of (the temporal x axis, else the
  column of a time range): Superset 4.1.2 puts it in place of that axis and filters that column
  by the time range instead;
- the time grain is `P1D`, `P1W`, `P1M`, `P3M` or `P1Y`, and the typed spec carries exactly the
  grain Superset 4.1.2 groups by. Superset groups by the grain each temporal column it sends as
  an axis — the x axis of a line, bar, area or heatmap; the first column a table's
  `temporal_columns_lookup` flags, moved to the front (a table with none groups by no grain at
  all); each pivot row or column the lookup flags or that is `granularity_sqla` — and any other
  column that is `granularity_sqla`. The IR grains the first dimension only, so a chart Superset
  groups otherwise is not typed, a pivot it groups by a grain at all among them. Superset starts
  the period of the column cast to `DateTime` (`toStartOfMonth(toDateTime(col))`), whose range
  is 1970 to 2106, so a grain is typed only on a `Date`, `DateTime` or `DateTime('<zone>')`
  column (`Nullable` or `LowCardinality` around it too): not on `Date32`, `DateTime64` or an
  aggregate state of one, whose range is wider, nor on a column whose type is not named. `P1D` is
  the IR's day only on a `Date` column: of a column of time Superset starts the day, and the IR's
  day is the column as it is;
- `stack` draws each value as it is (none, `Stack` or `Stream`); `Expand` draws its share;
- no native filter of the dashboard applies to the chart: one with a default adds it to the query
  as the dashboard opens, and whether a filter has one is not read. The tabs a filter's scope
  names are not read either: a filter scoped to some tabs is taken to apply on every tab;
- every `form_data` key that holds a value (not null, empty or `false`) is one the inventory
  knows; display keys do not keep a chart from being typed;
- `row_limit` is 1 to 50000. A chart without one reads the default of its viz — 100 for a pie,
  1000 for a table, 10000 for a line, bar, area, heatmap or pivot; a big number, which has no
  such control, and a `row_limit` saved null or empty read Superset's `ROW_LIMIT`, 50000.

Each chart of the written spec SHALL be named `chart_<superset id>`, titled by its `slice_name`
(by its name `chart_<id>` when it has none), in the order of the Superset ids. `inventory.json`
SHALL list each chart it read — its form, the Superset `database` and `backend` of its dataset
and, when it is read SQL, the reasons; a chart that refuses its dashboard is named in `refusals`
only. A reason SHALL name a saved metric by its name, and quote a `form_data` key that is not a
name (`form_data['my key']`), escapes and all. Of a saved metric's SQL, a reason names at most the
one column an `AGG(column)` metric aggregates — the model's check names it when the model has no
such column — and never a literal or any other part of it.

## Requirement: a dashboard is written whole or not at all

A spec missing a tile would read as the dashboard without it, so a dashboard SHALL be written only
when every chart is. It SHALL be refused, with the reason named, when:

- it has more than 12 charts — `DashboardSpec` holds at most 12, and lifting it is an IR change,
  the owner's decision;
- it has no charts;
- a chart's dataset is not among the datasets the dashboard holds;
- with `--database ID`, a chart's dataset is on another Superset database or names none: the model
  names the tables of one database, and a table of the same name on another is not one of them;
- a chart's dataset names no schema, physical or virtual (the spec names its source
  `schema.table`);
- a read SQL does not pass the SQL guard in the ClickHouse dialect, including SQL ClickHouse does
  not parse (a `UNION` without `ALL`), a saved metric that does not parse, and a fragment that
  makes the read more than a `SELECT`;
- the dashboard's spec, every chart of it passing on its own, does not pass `validate_spec` or the
  Superset contract compiler (a chart answered twice).

A refusal of a read SQL SHALL name where it fails (a line and a column, a forbidden function),
never quote it: a dataset's SQL can hold a credential. SQL the parser fails on with an error of
another kind than its own (sqlglot 30 raises an `AttributeError` on `"region" ('North')`, an `IN`
left out) SHALL be named by the class of that error. The gate then says `bi.spec_missing` for a
refused ref instead of answering for tiles nobody read.

## Requirement: the files are where the gate reads them

For each written dashboard the inventory SHALL write `<out>/<segments>/<name>@<version>.yaml` for
its ref — the path `decision verify --dashboards <out>` reads — and the spec SHALL read back as
the `DashboardSpec` it was written from: SQL kept as a block where YAML can, double-quoted where
a block would not read back (a text holding U+0085). It SHALL write `<out>/inventory.json`:
`superset_version` and, per dashboard, `ref`, `superset`, `superset_id`, `title`, `changed_on`,
`written` (the path relative to the output, null when refused), `charts` (`chart`,
`superset_id`, `viz_type`, `form`, `reasons`, and the Superset `database` and `backend` of its
dataset) and `refusals`.

The output SHALL be a new directory: one that exists is refused before Superset is asked.
The files SHALL be written into a staging directory beside it, made under the umask as the
output would be, and renamed at the end. An answer of Superset that does not come leaves nothing;
a failure of the write or an interrupt leaves neither the output nor the staging directory, only
the directories made above the output.

## Requirement: the exit code says what was written

`auto_bi decision inventory <semantic> --dashboard REF=ID_OR_SLUG [--dashboard ...] --output NEW_DIR
[--database ID] [--json]` takes the directory holding `model.yaml` of the semantic ref; `--database`
is the id of the Superset database that model describes. It SHALL exit:

- 0 when every named dashboard was written;
- 3 when a dashboard was refused: each refused one is named with why, and the gate will report
  `bi.spec_missing` for it;
- 2 when the inventory could not be taken: an argument (a `--dashboard` that is not `REF=ID`, an
  id or slug that is not UTF-8 text), a model the gate cannot read, an output that exists, a
  Superset address that is not a URL, Superset unreachable, a login refused, an HTTP error, an
  answer not shaped as Superset's, a write that fails — a text of an answer that is not Unicode
  (a lone surrogate, which JSON's `\uD800` escape carries) cannot be written to `inventory.json`,
  and nothing is. An arguments error SHALL ask Superset nothing. One `error:` line is printed, except for
  what argparse refuses (a missing `--dashboard` or `--output`, a `--database` that is not an
  integer): it prints its usage and its own error line.

The answer is, per dashboard, `wrote    <path>  (<superset>: <n> typed, <m> read as SQL)` or
`refused  <ref>  (<superset>)` followed by one `  - <reason>` line per refusal, then
`report   <out>/inventory.json`; `<path>` is relative to the output and `<superset>` is the id or
slug as named. `--json` prints the report instead. What is printed from the arguments and the
answers — the id or slug the operator named, a name out of Superset's answers in a refusal — is
printed escaped; the dashboard title appears only in `--json`, JSON-escaped.

### Scenario: the gate reads what was written

- **GIVEN** a dashboard over `dm.sales_daily` with six charts: three of the IR's shapes over the
  revenue metric, three of another
- **WHEN** the inventory writes it and `decision verify --dashboards <out>` runs
- **THEN** the three typed tiles are named `computes_metric` for `metric://sales/revenue`, the
  three read SQL tiles `chart.sql_partial`, and `bi.contract` passes

### Scenario: a dashboard past the limit

- **GIVEN** a dashboard of 24 charts
- **WHEN** the inventory runs
- **THEN** nothing is written for it, `refusals` names the count and the limit of 12, and the
  exit is 3

### Scenario: Superset does not answer

- **GIVEN** a Superset unreachable, or a login it refuses
- **WHEN** the inventory runs
- **THEN** the exit is 2, one `error:` line names the request without a body or a credential, and
  the output directory does not exist

### Scenario: an output that exists

- **GIVEN** `--output` names a directory that exists
- **WHEN** the inventory runs
- **THEN** the exit is 2 and Superset is not asked

## Known boundaries

- Measured on Superset's own `load-examples` corpus (11 dashboards, 110 charts): 9 dashboards
  written (5 charts typed, 60 read as SQL) and 2 refused (21 and 24 charts; the second also has
  dataset SQL with a bare `UNION`, which the SQL guard's ClickHouse dialect does not parse). Over
  all 110 charts: 6 typed, 103 read as SQL, 1 refused. The corpus is not the gate's own domain; a
  typed chart is the exception there, by design. Its `TIMESTAMP WITHOUT TIME ZONE` columns are no
  ClickHouse type, so a chart with a grain on one is read as SQL.
- The read SQL is an over-approximation: it names every column the chart or its dataset could read,
  not the ones a non-typed viz reads. Exact composition per viz is backlog (D6b). It holds the
  expressions of the trimmed dataset answer only: a calculated column a chart reads through
  `x_axis` or a pivot row or column alone is not among them (`SELECT *` still reads every column
  of the source).
- The rendered SQL and the saved `query_context` are not read: `GET chart/{id}/data?type=query`
  answered 400 for 109 of the 110 example charts on the stand (2026-11-14; backlog).
- A native dashboard filter is not mapped to the IR: a chart it applies to is read SQL (D6b).
- Row-level security is not read: the three `GET`s do not show it, and it changes a tile's numbers
  per viewer.
- `order_desc`, `sort_by_metric` and a heatmap's `sort_x_axis` and `sort_y_axis` are read as
  display: a typed tile over its row limit shows the rows they choose, each still the measure the
  IR names.
- Without `--database`, no chart's database is checked; `inventory.json` still records it.
- The 12-chart limit is the IR's; a dashboard of more is refused, not truncated.
- A dataset without a schema is refused.
- `COUNT(*)` is not typed: the IR has no such measure.
- A `Date` column holds days 1970-01-01 to 2149-06-06, and Superset starts a period of it cast to
  `DateTime`: the day's midnight in the server's time zone, which `DateTime` holds from 1970-01-01
  00:00 UTC to 2106-02-07 06:28:15 UTC. ClickHouse 24.8 (clickhouse-local 24.8.14.39, checked
  live) wraps a later midnight into 1970 under the default `date_time_overflow_behavior`: a day
  from 2106-02-08 on — from 2106-02-07 when the server is more than 6 h 28 min west of UTC
  (America/Los_Angeles) — falls into another period there than in a typed spec at every grain. It
  moves an earlier midnight to the midnight of 1970-01-02 under every
  `date_time_overflow_behavior`: when the server's time zone was east of UTC in 1970
  (Europe/Moscow), 1970-01-01, ClickHouse's default `Date`, falls into another day at `P1D`, and
  into the same week, month, quarter and year. The inventory reads neither the values nor the
  server's time zone, so this is not checked.
- Superset 4.1.2 only; another version is an update of the pin with its own run.

## Usage

```bash
uv run auto_bi decision inventory semantic/head \
    --dashboard dashboard://sales/overview@1=sales-overview \
    --dashboard dashboard://sales/regions@1=7 \
    --database 2 --output dashboards
uv run auto_bi decision verify contracts/ --base semantic/base --candidate semantic/head \
    --evidence .evidence --dashboards dashboards
```
