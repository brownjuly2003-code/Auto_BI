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
`auto_bi/decision/use_cases.py`. The mapping is checked against the pinned Superset 4.1.2,
including the E4b rendered-SQL area below; 6.1 answers are also replayed offline. It is reached through the
settings `AUTO_BI_SUPERSET_URL`, `AUTO_BI_SUPERSET_USER` and `AUTO_BI_SUPERSET_PASSWORD`.

## Requirement: a repository snapshot is the PR run's inventory input (E3)

The input sequence SHALL be: Superset answers → `decision inventory` against the semantic
model of the **default branch** → a repository snapshot (spec files and `inventory.json`) →
`decision verify --dashboards <snapshot> --pr-comment <file>` or the Action's `dashboards` input.
An operator, or a trusted manually dispatched workflow of the default branch, takes the
snapshot using a Superset account restricted to reading the named dashboards. The operator
reviews the result and proposes the snapshot in a separate PR. The PR job SHALL receive no
Superset credentials; neither `action.yml` nor the demo PR workflow references them or
`secrets.`. `pull_request_target` remains forbidden by the Action and the workflows.

The snapshot can lag behind BI. Freshness is the producer's responsibility, **not a verdict**:
the gate reads the supplied specs, does not contact Superset and cannot see later BI changes.
`inventory.json` metadata (including `changed_on` and `reader_version`) does not grade a run.
A delivered ref without a spec, whether refused by inventory or absent from the mapping,
SHALL be `bi.spec_missing`. Refresh with exit 3 removes the formerly written spec of a now
refused dashboard, so the gate cannot keep using it.

The [trusted workflow template](../../examples/decisionops-gate/workflows/inventory-snapshot.yml)
is supplied outside `.github/workflows` and **has not been run on GitHub**. It SHALL:

- accept only `workflow_dispatch`, with a job condition admitting only the default branch;
  checkout the model and mapping from that branch;
- checkout Auto_BI by a full reviewed commit SHA, and read uv's pin from that checkout;
  use its frozen lock, isolated Python and both `--no-env-file` and `AUTO_BI_NO_ENV_FILE=1`
  for inventory. The template pins the published E3 commit, the head of its publication
  pull request, which a merge commit keeps in `main`; the demo workflow's Action pins the
  same commit. A branch, tag, malformed SHA, all-zero SHA or the former
  `REPLACE_WITH_REVIEWED_E3_COMMIT_SHA` marker is refused before the code checkout;
- scope Superset secrets to the inventory step through the `superset-inventory` GitHub
  environment; install dependencies before that step and use `--no-sync` during it;
- grant only `permissions: contents: read`, persist no checkout credentials, pin third-party
  actions by SHA, and upload an artifact for 14 days. It never writes the repository;
- treat inventory exits 0 and 3 as snapshot success; exit 2 and any other failure prevent
  artifact upload. The artifact is input to the operator's separate PR, not automatic publication.

Offline tests: `tests/test_inventory_snapshot.py::test_rest_snapshot_refresh_to_pr_comment`,
`tests/test_inventory_snapshot_workflow.py`, and the existing Action event-refusal tests.

## Requirement: a checked-in mapping uses the same dashboard rules as flags

`--map FILE` SHALL read UTF-8 JSON of exactly this shape (ids are **strings**, also when numeric):

```json
{
  "dashboards": [
    {"ref": "dashboard://sales/overview@1", "superset": "sales-overview"},
    {"ref": "dashboard://sales/regions@1", "superset": "7"}
  ]
}
```

Only `dashboards` is allowed at the top level, only `ref` and `superset` in each entry; both
strings are required. Duplicate JSON keys, unknown keys, wrong types, unreadable/non-UTF-8
files and malformed JSON SHALL be exit 2. No source-body excerpt is printed for these failures.
A pair rule refused after the file has parsed names the ref as `--dashboard` does: refs and
Superset ids are identifiers, and the message is the flag's, word for word.
The nonempty list, unique refs, pinned `dashboard://path@version` grammar, nonblank Superset
id/slug and its UTF-8 encodability SHALL be checked by the **same function** as `--dashboard`.
All mapping validation precedes any access to Superset or `--output`.

`--map` and `--dashboard` are mutually exclusive; exactly one is required. `--database ID`
is allowed with either and retains its D6 meaning; the map contains no database id and no
credentials. Entry order is retained in the report. Tests: the mapping cases in
`tests/test_inventory_snapshot.py`, including identical flag/map refusal messages and the
database mismatch returning exit 3.

## Requirement: the inventory asks Superset by GET after its login

For each `--dashboard REF=ID_OR_SLUG` (REF is `dashboard://path@version`) the inventory SHALL read
`GET /api/v1/dashboard/{id_or_slug}`, `.../charts` and `.../datasets`. Reader 2 MAY then read
the saved-chart list, chart detail and rendered SQL, in the order specified below. Content
requests SHALL be GET only. The client's login — `POST /api/v1/security/login`, then
`GET /api/v1/security/csrf_token/` — is made before the first request and once more after an
answer 401, which repeats that request once.

An answer that is not the shape Superset 4.1 gives where the inventory depends on it — not JSON,
JSON nested deeper than the inventory reads, no `result`, no object or list of objects where one
is due, no integer `id`, a `form_data`, `json_metadata`, `columns` or `metrics` of another type, a
dataset `sql` that is not text — SHALL be said, never guessed around. What it only names or
records is read leniently: a title, a slice name, a schema, a table name, a native filter's name
or a `backend` that is missing or not text is read as empty (a chart is then titled
`chart_<id>`, a dataset refused for naming no schema), `changed_on` as its text, and a dataset
`database` that is not an object with an integer `id` as naming none.

Except for a JSON-object 400/404 from the rendered-data endpoint below, an HTTP error or a
refused login SHALL be an error that names the request and the HTTP status
(`login failed: HTTP 401`, `csrf fetch failed: HTTP 500`, `GET <path> -> HTTP 404`); a Superset
not reached, or an answer that is not its JSON, SHALL name the `GET` it was making
(`GET <path>: ConnectError`), a login made for it included; never a body or a credential.

## Requirement: a chart is typed, rendered SQL, composite SQL or conservatively read as SQL

Each chart SHALL use the first available form in this order:

- **typed** — the exact IR `ChartSpec`. It is the only form the gate can name `computes_metric`;
  it SHALL be chosen only when the chart is one the IR says exactly, and the spec passes
  `validate_spec` against the model and the Superset contract compiler. A typed chart that fails
  either proceeds to rendered SQL, composite SQL and then read SQL: a spec `bi.contract` refuses would block every run for a
  reason no change made;
- **rendered SQL** (`rendered_sql`) — a chart inside all six E4a checks below, with one rendered
  query that can be read in the gate's ClickHouse dialect. It SHALL be viz `table`, with
  `raw_sql` holding that query and `table` named as in the conservative form. It has no IR
  measures and cannot be `computes_metric`, even with contribution, rank or other
  post-processing. Its reads are those the gate extracts from the rendered query;
- **composite SQL** (`composite_sql`) — the union of column reads of a supported 4.1.2
  plugin's query, composed from `form_data` and the full dataset definition as specified
  below. Like rendered SQL it is viz `table`, has `raw_sql` and no measures, and never
  claims `computes_metric`. It adds no outer `*`; a star inside virtual dataset SQL can
  still make the gate name `chart.sql_partial`;
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

## Requirement: rendered SQL is read only inside the E4a area (E4b-1)

All six checks SHALL hold:

| Check | Evidence and boundary |
| --- | --- |
| `saved` | The chart-list row has `changed_on_utc` and `last_saved_at`; their absolute difference is **at most 2 seconds**, inclusive. A timestamp without a zone is UTC. |
| `fresh` | `GET chart/{id}` has readable JSON `params` and `query_context`, with nonempty `form_data`. The two forms agree on the union of their keys, except `slice_id`, `datasource`, `dashboards`, `url_params`, `force`, `result_format`, `result_type`. Even an extra null-valued key is a difference. `query_context.datasource` names the chart's table dataset from the dashboard answers, and `form_data.datasource`, when present, names that same dataset. The chart's `viz_type` column (`GET chart/{id}`), the dashboard `form_data` and the saved params name the same viz: the dashboard draws the column's viz, and a REST save of that column alone keeps params and query_context of the viz before it. |
| `api` | The viz is in the positive 4.1.2 chart/data list below; legacy and unknown viz fail. |
| `plain` | Neither the dataset — its SQL and every calculated column's and metric's expression, which Superset 4.1.2 renders too — nor the chart's forms, params or the whole saved `query_context` (its queries' `extras.where`, `extras.having` and every `sqlExpression` included) contains `{{` or `{%`. The dataset is read twice: its dashboard answer first, then its whole definition, `GET /api/v1/dataset/{id}` — the dashboard answer is trimmed to what the charts name in some `form_data` keys (`data_for_slices`), so a calculated column a chart reads only as its time column or sort is not in it. The definition answers 200 with an object whose integer `id` is the dataset's. No native dashboard filter applies: it applies unless `scope.excluded` names the chart and `chartsInScope` does not. An empty `chartsInScope` takes no chart out of a scope; one that lists an excluded chart keeps it in, as D6 reads it. |
| `answer` | Rendered data answered HTTP 200, with a nonempty `result`; every element has a nonempty text `query` and no nonempty `error`. |
| `single` | No query in `query_context.queries` has nonempty `time_offsets` or `annotation_layers`; chart params have no nonempty `time_compare` or `annotation_layers`. |

The positive list has 29 chart viz keys, checked against Apache Superset tag **4.1.2**, commit
`72538bda06c0deef2140e0df65bf0a13bcddb16c` (Apache-2.0): registrations in
`superset-frontend/src/visualizations/presets/MainPreset.js` and `useLegacyApi` metadata in
`superset-frontend/plugins/*`. These plugins do not use the legacy API; native filter
controls are not chart viz. `pop_kpi` is the experimental ECharts registration.

```text
big_number big_number_total box_plot bubble_v2 echarts_area
echarts_timeseries echarts_timeseries_bar echarts_timeseries_line
echarts_timeseries_scatter echarts_timeseries_smooth echarts_timeseries_step
funnel gauge_chart graph_chart handlebars heatmap_v2 histogram_v2
mixed_timeseries pie pivot_table_v2 pop_kpi radar sankey_v2 sunburst_v2
table tree_chart treemap_v2 waterfall word_cloud
```

Typed charts SHALL cost no additional request. For other charts the inventory first checks
everything available from the three D6 answers: `api`, Jinja in the dataset's dashboard answer
and the dashboard `form_data`, native filters, and `single` keys in that form. A missing schema/table or an
unknown SQL backend also prevents a rendered attempt. A dashboard over 12 charts SHALL make
no new request. D6 typing and conservative SQL construction retain their existing rules.

Only if a chart still qualifies, the reader SHALL lazily request **once per dashboard**:

```text
GET /api/v1/chart/
q=(columns:!(id,changed_on_utc,last_saved_at),filters:!((col:dashboards,opr:rel_m_m,value:<dashboard id>)),page_size:12)
```

The id is the numeric id from the dashboard response, never its slug. After `saved` passes,
`GET /api/v1/chart/{id}` supplies `fresh` and the saved forms' part of `plain` and `single`.
Then `GET /api/v1/dataset/{id}` supplies the rest of `plain` — at most **once per dataset** of
a dashboard, kept for its other charts. Only after those pass may
`GET /api/v1/chart/{id}/data/?type=query&format=json` render SQL; a chart with Jinja SHALL never
reach that GET. Thus, besides login, the request count is three D6 GETs, at most one list GET,
at most two GETs per eligible non-typed chart and one per dataset of those charts. The inventory
account reads charts and datasets as well as dashboards.

A rendered-data HTTP **400 or 404 with a JSON object** SHALL fail `answer` and retain the
next SQL form, so the dashboard can still be written. Transport failure, failed login
or CSRF fetch, 401 after re-login, 403, 5xx, any other status (including 202/3xx), and a body
that is not a JSON object SHALL raise `SupersetSourceError`. No body, SQL or credentials
belong in these errors. Missing or malformed saved params/context fail `fresh`. The dataset
definition follows the same policy, except that only an HTTP **404 with a JSON object** — no such
dataset for this account — fails `plain` and prevents composition too; a 400 or 403 is an
error, as for the other GETs of the chart and the dashboard.

Supported backends are `clickhousedb`, `clickhouse` and `postgresql`. ClickHouse SQL SHALL
retain its text after trimming outer whitespace and a terminal semicolon. PostgreSQL SQL
SHALL be parsed in sqlglot's `postgres` dialect and emitted as `READ_SQL_DIALECT`
(`clickhouse`). The entire `extract_column_refs` result before and after translation SHALL
agree, including table references and unresolved reads. Unsupported translation or a
difference falls through to composite SQL. Unknown backends also fall through.

E4b-1 SHALL accept exactly **one** rendered query. Multiple results, such as totals or
`mixed_timeseries`, fall back rather than selecting one query or combining them without a
proved read-set contract. Source and translated SQL SHALL pass `guard_sql`; the rendered
tile SHALL pass `gate_refusals`. A failure falls back, not by itself refusing the dashboard;
the existing conservative form must still pass its own checks.

For rendered SQL, `reasons` SHALL contain why D6 did not type the tile. For either later SQL
form it SHALL additionally contain the **first** failed check, prefixed `saved:`, `fresh:`, `api:`,
`plain:`, `answer:` or `single:`. Representation failures (backend, multiple queries,
translation, guard or contract), and the dashboard limit, instead use `rendered_sql:`.
No new report field is required; `form` gains the enum value `rendered_sql`.

Offline contracts: `tests/test_superset_inventory_rendered.py` exercises each boundary,
inclusive timestamps, asymmetric/null keys, ignored keys, the viz the dashboard draws, Jinja in
the context's queries and in the definition outside the dashboard answer, filter scope, every
positive viz, no-query ordering, lazy list and definition sharing, HTTP policy, dialect/read
equality and both guards.
`tests/test_superset_inventory_corpus.py` checks the fixed five-run corpus, including 6.1,
unchanged typed/written/refused counts and zero missed reads. Snapshot compatibility is in
`tests/test_inventory_snapshot.py`; CLI text and JSON forms are tested in the rendered suite.

## Requirement: composite SQL follows the 4.1.2 query builders (E4b-2)

Composition SHALL follow a failed rendered attempt, before conservative SQL. Its candidates
are exactly the 29 modern viz listed above; legacy `viz.py` charts SHALL retain conservative
SQL. D6 typing and E4b-1 rendered selection SHALL remain unchanged. A native filter applying
under D6 `applies_to`, Jinja (`{{` or `{%`) anywhere in `form_data` or dataset SQL, a nonempty
`annotation_layers`, an unreadable definition, a missing schema/table, or a backend outside
the E4b-1 dialect map prevents composition; that check comes before the definition `GET`.
Jinja in an expression actually used by composition also prevents it; unused saved expressions
are not read by this form. Composition itself also takes a Jinja comment (`{#`), which the
template step drops, for Jinja in `form_data`, dataset SQL and used expressions. Unknown nonempty keys fail closed. Null, empty and false values
carry no additional query input.

`auto_bi/inventory/composite.py` records the whitelist in `COMMON_KEYS`, `FIELD_ALIASES`,
`VIZ_RULES`, `POSTPROCESS_KEYS` and `IGNORED_KEYS`. The source pin is Apache Superset **4.1.2**,
commit `72538bda06c0deef2140e0df65bf0a13bcddb16c`, Apache-2.0. Common rules are traced through
`superset-frontend/packages/superset-ui-core/src/query/`: `buildQueryObject.ts`,
`extractQueryFields.ts`, `extractExtras.ts`, `processFilters.ts`, `buildQueryContext.ts`,
`getXAxis.ts` and `normalizeTimeColumn.ts`. The following builder paths are relative to
`superset-frontend/plugins/` and end in `/buildQuery.ts`. Every row also uses the common rules.

| Viz | Builder | Additional read inputs or overrides |
| --- | --- | --- |
| `big_number_total` | `plugin-chart-echarts/src/BigNumber/BigNumberTotal` | Base metrics |
| `big_number` | `plugin-chart-echarts/src/BigNumber/BigNumberWithTrendline` | `x_axis`, otherwise legacy time column |
| `echarts_area`, `echarts_timeseries`, `echarts_timeseries_bar`, `echarts_timeseries_line`, `echarts_timeseries_scatter`, `echarts_timeseries_smooth`, `echarts_timeseries_step` | `plugin-chart-echarts/src/Timeseries` | `x_axis`, `groupby`, series sort metric, time offsets |
| `mixed_timeseries` | `plugin-chart-echarts/src/MixedTimeseries` | Both queries: `_b` overrides of metrics, groups, filters and time controls; original `x_axis` in both |
| `box_plot` | `plugin-chart-echarts/src/BoxPlot` | `columns` or `granularity_sqla`, plus `groupby`; temporal lookup changes grain only |
| `bubble_v2` | `plugin-chart-echarts/src/Bubble` | `entity`, `series`; metric aliases `x`, `y`, `size` |
| `graph_chart` | `plugin-chart-echarts/src/Graph` | `source`, `target`, `source_category`, `target_category` via builder `queryFields` |
| `tree_chart` | `plugin-chart-echarts/src/Tree` | `id`, `parent`, `name` via builder `queryFields` |
| `heatmap_v2` | `plugin-chart-echarts/src/Heatmap` | `x_axis`, `groupby`, otherwise legacy time column; normalization and sorting keep reads |
| `histogram_v2` | `plugin-chart-echarts/src/Histogram` | `column`, `groupby`; removes base metrics |
| `pivot_table_v2` | `plugin-chart-pivot-table/src/plugin` | `groupbyRows`, `groupbyColumns`; temporal lookup changes grain only |
| `pop_kpi` | `plugin-chart-echarts/src/BigNumber/BigNumberPeriodOverPeriod` | `cols`, time comparison |
| `sankey_v2` | `plugin-chart-echarts/src/Sankey` | `source`, `target`, `sort_by_metric` |
| `table` | `plugin-chart-table/src` | Aggregate `percent_metrics`; raw/aggregate mode, time comparison, temporal lookup; totals and pagination retain reads |
| `waterfall` | `plugin-chart-echarts/src/Waterfall` | `x_axis` or `granularity_sqla`, `groupby`; orders by those columns |
| `funnel`, `gauge_chart`, `pie`, `sunburst_v2`, `treemap_v2` | `plugin-chart-echarts/src/{Funnel,Gauge,Pie,Sunburst,Treemap}` respectively | Base groups and metrics, `sort_by_metric` |
| `word_cloud` | `plugin-chart-word-cloud/src/plugin` | Base groups and metrics, `sort_by_metric` |
| `radar` | `plugin-chart-echarts/src/Radar` | Base groups and metrics |
| `handlebars` | `plugin-chart-handlebars/src/plugin` | Base query |

Common field aliases SHALL be resolved: `metric`, `metric_2`, `secondary_metric`, `x`, `y`
and `size` to metrics; `all_columns` to columns; `series` to groupby; `order_by_cols` to
orderby, including JSON-encoded pairs. `query_mode=aggregate` drops base columns;
`query_mode=raw` drops base groupby and metrics. Builder overrides above are then applied.
Both `series_limit_metric` and `timeseries_limit_metric` are included conservatively, even
when the series limit is zero: plugin sorting can use them independently. `series_columns`
are read too; the series-limit subquery uses the same groups and filters. `limit`, row/series
limits, offsets and order direction change no column reads. An order mapping must be a
SIMPLE or SQL adhoc metric; an unknown shape fails with the order key named.

Saved metric names SHALL resolve to `expression` in the **full** dataset definition;
SIMPLE metrics resolve their column, SQL metrics retain their literal expression. A column
with an expression uses that expression rather than its name. A column object is an expression
only with both `sqlExpression` and `label` (`utils.is_adhoc_column`); another object fails. A
nonempty `x_axis` SHALL be what superset-ui-core `isQueryFormColumn` takes — a string, or an
object with `sqlExpression` and `label` and no `expressionType` other than `SQL`; another axis
is not set for `isXAxisSet` — the time-series, big number and heatmap builders read a time
column in its place, waterfall passes the object on (`x_axis || granularity_sqla`) — and it
fails with `form_data['x_axis']` in the reason, stricter than waterfall. A dataset with a
column named `__timestamp` (`DTTM_ALIAS`) is not composed: Superset reads that column where
it is an axis or the filter subject of a query that is not a time series, and the time
column where `__timestamp` stands for it. Nor is a chart that names `__timestamp` as a column —
axis, group, column or pivot row: `normalizeTimeColumn` and the temporal column lookups of
table, box plot and pivot table make such a string an expression, and with no such column in
the metadata Superset writes it as literal SQL — a table column the definition does not list.
`adhoc_column_to_sqla` processes an expression before it looks up a column of that name — the
template step (Jinja under `ENABLE_TEMPLATE_PROCESSING` makes CR a line feed and cuts one
trailing line feed), `sanitize_clause` (a line feed after a trailing comment). So a dataset
with a column name holding `--`, `/*`, `#`, CR or a line feed is not composed, used or not;
nor is an adhoc column whose expression the template step turns into a dataset column name.
A carriage return in an expression composition reads, or in the dataset SQL, prevents it:
the template step makes it a line feed inside a quoted name too.
Missing referenced columns
or metrics fail; the trimmed dashboard answer is not proof of their definitions. SIMPLE
filters read their subject; SQL filters, `where` and `having` each contribute their own SQL
fragment. `filters` and `extra_filters` are included. Nonempty `extra_form_data` and
`custom_params` are consumed by core query construction but their overrides are not composed:
they fail with `form_data['<key>']` in the reason.

The time column follows `extractExtras`: `extra_filters.__time_col`, then `granularity`,
then `granularity_sqla`; `__granularity` does not override that result. A non-temporal
granularity uses `main_dttm_col` as `superset/models/helpers.py:get_sqla_query` does. Time
ranges, temporal filters and legacy timeseries include their time column. The context
factory's temporal-axis replacement includes `granularity` even with `No filter`.
`always_filter_main_dttm` adds the dataset main time column when a range applies.
`query_context_processor.py:processing_time_offsets` clones the query for `time_compare`:
composition includes the time column and possible BASE_AXIS **label** resolution against
dataset metadata, plus the main time column. Comparison changes dates, not the other reads.
This read set comes from the 4.1.2 sources only: no time comparison tile of the pinned corpus
was measured against the engine (`docs/pilot/2026-11-19-e4b-inventory-reads/README.md`).
Rolling, resampling, contribution, cumulative and forecast operations process the returned
dataframe; their column reads stay the same. Series sorting's extra metric is included.

A key is ignored only when the pinned builders and their query helpers do not read it, or
when it changes presentation/dataframe operations without adding source reads. Spreading
`formData` into `context.form_data` alone is not query consumption. This includes explicit
display controls, `compare_lag`, `markup_type` and `form_data.queryFields` (distinct from the
builder's `options.queryFields`). Inputs specific to another builder have no effect unless
the common rules consume them. A key whose effect is unknown SHALL fail, not be inferred
from its spelling. Mixed queries apply the same check after stripping `_b`.

The composed tile SHALL be `SELECT` of deduplicated identifiers and SQL fragments, with
each fragment parenthesized on separate lines, `FROM schema.table` or
`FROM (\n<full dataset SQL>\n) AS virtual_table`. Physical identifiers are quoted. Dataset SQL
has the same outer whitespace/semicolon trimming as conservative SQL; its inner `*` is
preserved. The composed SQL takes the E4b-1 dialect step: ClickHouse as is; PostgreSQL parsed
as `postgres` and emitted as ClickHouse only when the `extract_column_refs` result is the same
before and after, otherwise the conservative tile with `composite_sql: translation changes
column reads`. `ChartQuery.table` retains the conservative form's `schema.table`. `guard_sql`
and `gate_refusals` must accept the result. Their failure retains the conservative tile,
without exposing SQL or exception text. A composed SQL read set does not assert that the
original viz query executes successfully.

Only `GET /api/v1/dataset/{id}` may be added by composition, using the same per-dashboard
`definitions` cache as E4b-1. If rendering already fetched it, composition reuses it even
after a rendered-data refusal; otherwise it fetches it after the local composition
prechecks. There is at most one definition GET per dataset per dashboard. Typed charts,
legacy/unknown viz, and dashboards over 12 charts add none. HTTP 404 with a JSON object
means an unreadable definition and conservative SQL; all other source failures follow the
E4b-1 error policy. Across both forms the bound is three D6 GETs, at most one save-list GET,
at most two GETs per eligible rendered chart, and one definition GET per dataset needed by
either form. Composition does not request chart detail or rendered data on its own.

A composite tile retains the D6 reasons and the first rendered refusal. A conservative
tile additionally records the first composition failure with prefix `composite_sql:`.
Keys from input are quoted/escaped; expressions, response bodies and credentials are never
included. CLI text SHALL count composite tiles separately; `--json` uses the report's
`form` enum, without a separate count schema.

Offline contracts in `tests/test_superset_inventory_composite.py` exercise aliases, mode
exclusion, plugin fields, expression resolution, all filter classes, sorting, time/offset
substitution, mixed queries, ignored and unsupported keys, Jinja, native scope, shared GETs,
HTTP policy, guard/contract fallback, virtual stars and CLI text/JSON. The rendered suite
retains typed/rendered priority, legacy and chart-limit request checks. Snapshot replacement
tests include reader 2; the pinned corpus remains the independent acceptance measure. Its one
amendment after the code counts a tile Superset refused to the dashboard (every request 4xx,
nothing run) as refused rather than unmeasured: such a tile reads nothing.

## Requirement: chart identity and reasons are recorded

Each chart of the written spec SHALL be named `chart_<superset id>`, titled by its `slice_name`
(by its name `chart_<id>` when it has none), in the order of the Superset ids. `inventory.json`
SHALL list each chart it read — its form, the Superset `database` and `backend` of its dataset
and, when it is any SQL form, the reasons; a chart that refuses its dashboard is named in `refusals`
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
`superset_version`, `reader_version` and, per dashboard, `ref`, `superset`, `superset_id`, `title`, `changed_on`,
`written` (the path relative to the output, null when refused), `charts` (`chart`,
`superset_id`, `viz_type`, `form`, `reasons`, and the Superset `database` and `backend` of its
dataset) and `refusals`.

Without `--replace`, output SHALL be a new directory: one that exists is refused before Superset is asked.
The files SHALL be written into a staging directory beside it, made under the umask as the
output would be, and renamed at the end. An answer of Superset that does not come leaves nothing;
a failure of the write or an interrupt leaves neither the output nor the staging directory, only
the directories made above the output.

## Requirement: explicit replacement preserves the previous snapshot on failure

`--replace` SHALL require an existing inventory-owned directory; a missing output or a directory
without `inventory.json` is refused as such (exit 2), not reported as a failed write. Before contacting Superset,
its `inventory.json` must parse as an `InventoryReport` with `superset_version` and `dashboards`;
duplicate keys and unknown fields are refused. Each `written` must equal the canonical path
for its pinned ref, all written files must exist, and only those regular files, the report and
their necessary parent directories may exist. A refused entry has no `written` and names a
refusal. Symlinks (including the output itself), hard-linked files, Windows reparse points,
special files, foreign files and extra directories SHALL be refused with exit 2 before any
mutation. The first unexpected entry in sorted depth-first order is named and escaped.
This is a structural ownership check, not cryptographic proof of the producer's identity.

For exits 0 and 3, replacement SHALL publish a complete new tree, never merge into the old one:

1. Read and retain the old snapshot's bytes. Read Superset and fully write the new snapshot
   into sibling `.<output-name>.<random-16-hex>` under the usual umask.
2. Recheck the old tree and bytes before publication; changes during the remote read are refused.
3. Rename old output to sibling `<staging-name>.previous`, then staging to output.
4. Remove the backup. Bytes retained until cleanup finishes allow a partially removed backup
   to be repaired if cleanup fails, then renamed back. No backup or staging remains on success.

An argument/model/Superset/write error SHALL return exit 2 with the old bytes intact. An
`OSError` or `KeyboardInterrupt` during publication SHALL restore the old snapshot by rename
and remove staging; an interrupt then propagates. On Windows an open file can refuse the
directory rename: exit 2, old snapshot still in place. The caller must serialize writers and
not modify the snapshot while it is being replaced.

Power loss or a process killed without exception handling is outside rollback: before the
first rename, old output and possibly staging remain; between renames, output is absent and
the complete old tree is `.previous`, with the new tree at staging; after the second rename,
output is new and `.previous` may remain (possibly partly removed during cleanup). No automatic
recovery or durable `fsync` transaction is claimed. The operator must inspect these paths before
another refresh. A **second filesystem failure during rollback** is also outside the byte/cleanup
guarantee: it is exit 2 with recovery paths named, and surviving recovery material is retained.

Tests in `tests/test_inventory_snapshot.py` inject failures before and after writes, both
renames and cleanup, including partial backup deletion, and check bytes plus sibling entries.
They also cover unowned/malformed/missing snapshots, links and Windows open-file refusal.

## Requirement: the inventory records the reader version independently

`reader_version` SHALL be a positive integer, initially **1** for the D6 reader. Increment it
when the mapping from Superset answers and semantic model to specs, chart forms, reasons or
refusals changes. Packaging, mapping-file input and transactional publication do not change
that mapping: E3 writes version 1, E4b-1 writes version 2, and E4b-2 SHALL write
**reader version 3** for composite SQL.
`superset_version` remains `4.1.2`; `VERIFIER_VERSION` and
`CHECK_VERSION` do not change. Replacement SHALL accept a D6 report without `reader_version`
as reader 1, and reports explicitly naming reader 1 or 2; the new report explicitly writes version 3. A future positive
version is accepted if the rest of the report still satisfies the same ownership contract.
Tests: `test_map_accepts_database_and_writes_reader_version`, `test_replace_whole_snapshot_including_d6`
and the report-key contract in `tests/test_superset_inventory.py`.

## Requirement: the exit code says what was written

`auto_bi decision inventory <semantic> (--map FILE | --dashboard REF=ID_OR_SLUG [--dashboard ...])
--output DIR [--replace] [--database ID] [--json]` takes the directory holding `model.yaml` of the semantic ref; `--database`
is the id of the Superset database that model describes. It SHALL exit:

- 0 when every named dashboard was written;
- 3 when a dashboard was refused: each refused one is named with why, and the gate will report
  `bi.spec_missing` for it;
- 2 when the inventory could not be taken: an argument (a `--dashboard` that is not `REF=ID`, an
  id or slug that is not UTF-8 text, an invalid mapping), a model the gate cannot read, an output
  that exists without `--replace` or fails replacement ownership checks, a
  Superset address that is not a URL, Superset unreachable, a login refused, an HTTP error, an
  answer not shaped as Superset's, a write that fails — a text of an answer that is not Unicode
  (a lone surrogate, which JSON's `\uD800` escape carries) cannot be written to `inventory.json`,
  and nothing new is published. An arguments error SHALL ask Superset nothing. One `error:` line is printed, except for
  what argparse refuses (missing/both `--map`/`--dashboard`, missing `--output`, a `--database` that is not an
  integer): it prints its usage and its own error line.

The answer is, per dashboard,
`wrote    <path>  (<superset>: <n> typed, <r> rendered SQL, <c> composite SQL, <m> read as SQL)` or
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

- **GIVEN** `--output` names a directory that exists and `--replace` is absent
- **WHEN** the inventory runs
- **THEN** the exit is 2 and Superset is not asked

## Known boundaries

- D6 measured on Superset's own `load-examples` corpus (11 dashboards, 110 charts): 9 dashboards
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
- Rendered SQL is the SQL for the **inventory account at snapshot time**. Relative time is
  rendered as dates at that instant, so spec text can change between snapshots of unchanged
  saved forms. E4a proves column reads inside its area, not equality of result rows or sort.
  Missing saved context proceeds to composition. The account/time-specific text and absence of
  typed measures are exercised by `test_rendered_sql_is_the_account_and_time_specific_answer`.
- A native dashboard filter is not mapped to the IR: a chart it applies to is read SQL (D6b).
- Row-level security and viewer selection are a shared boundary of this spec, **not** a
  seventh area check. The reader neither inventories RLS policies nor chooses viewers.
  Rendered SQL includes what Superset renders for the inventory account, which does not
  establish another viewer's numbers.
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
- Superset 6.1 answers are readable (the fixed corpus replays both versions), but the rendered
  area and viz whitelist are verified against **4.1.2**. This is not a 6.1 compatibility
  certification or a change to the Superset pin. A 6.1 UI save adds form keys outside D6's
  known set and can keep a tile from being typed.

## Usage

```bash
# From a checkout of the default branch; superset-map.json has the shape above.
AUTO_BI_NO_ENV_FILE=1 uv run --no-env-file auto_bi decision inventory semantic \
    --map superset-map.json \
    --database 2 --output dashboards
# Later, replace a previously generated snapshot, including all refusals:
AUTO_BI_NO_ENV_FILE=1 uv run --no-env-file auto_bi decision inventory semantic \
    --map superset-map.json --database 2 --output dashboards --replace
# Commit the reviewed snapshot in a separate PR; the PR job only reads files:
uv run auto_bi decision verify contracts/ --base semantic/base --candidate semantic/head \
    --evidence .evidence --dashboards dashboards --pr-comment comment.md
```
