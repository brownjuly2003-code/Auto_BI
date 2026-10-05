# Capability: Validation of a delivered dashboard before its BI contract is compiled

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

`bi.contract` compiles every dashboard a product delivers into the payloads its BI target would
receive, without touching the BI (DECISIONOPS_ARCHITECTURE §8.5). The build path never compiles a
spec it has not validated: invariant 2 validates a `DashboardSpec` against the semantic model
before any BI call and rejects an unknown table or field, and invariant 3 admits operator SQL only
as a single plain SELECT. The compile seam reuses the build path's pure builders, but until now it
skipped that validation. A delivered dashboard whose KPI reads a column the candidate model does
not declare (`SUM(ghost_revenue)` on `dm.sales_daily`) compiled for Superset and for DataLens with
no finding, and `bi.contract` passed with `bi.compiled` — the same outcome as for the valid
`SUM(revenue)`. So did an unknown table, an operator `raw_sql` the SELECT-only guard refuses, and
an operator `raw_sql` on DataLens, which has no raw-table path. The release would ship a
dashboard the build path refuses to build, while the gate reported it compiled.

## Requirement: a delivered dashboard is validated against the candidate model before it is compiled

Before `bi.contract` compiles a delivered dashboard, the system SHALL validate its spec against
the candidate semantic model — the model the dashboards are compiled against — with the
validator the build path runs before any BI call (`auto_bi.ir.validate.validate_spec`, operator
`raw_sql` allowed, as on the operator build path). A spec with at least one validation error
SHALL NOT be compiled: no compiler SHALL be invoked for it and no BI payload SHALL be stored for
it. `bi.contract` SHALL block with summary code `bi.spec_invalid`. Its evidence SHALL list every
invalid dashboard with its ref and the validator's errors verbatim, in delivery order, and its
detail SHALL name the invalid dashboard refs and say that their specs do not validate against
the candidate semantic model. Every other delivered dashboard SHALL be compiled and graded as
before. The validator's rules SHALL NOT change in this capability, and other checks (product
impact included) SHALL read a delivered dashboard as before: only `bi.contract` grades whether
its spec validates.

### Scenario: an unknown measure column blocks on Superset

- **GIVEN** a product delivering one dashboard whose `big_number` chart `kpi` has the measure
  `SUM(ghost_revenue)` on `dm.sales_daily`, a column the candidate model does not declare, and
  `target_bi` Superset
- **WHEN** the gate runs
- **THEN** `bi.contract` blocks with `bi.spec_invalid`; its evidence names the dashboard ref with
  the error `chart 'kpi': unknown measure column 'ghost_revenue' in dm.sales_daily`; no
  `bi-…` payload blob is stored for the dashboard; and the verdict is block

### Scenario: an unknown measure column blocks on DataLens

- **GIVEN** the same chart with `target_bi` DataLens
- **WHEN** the gate runs
- **THEN** the outcome is the same as on Superset: `bi.contract` blocks with `bi.spec_invalid`,
  the error is named in the evidence and no payload is stored for the dashboard

### Scenario: an unknown table blocks

- **GIVEN** a delivered chart whose query table is `dm.ghost`, which the candidate model does not
  declare
- **WHEN** the gate runs
- **THEN** `bi.contract` blocks with `bi.spec_invalid` and the evidence carries the validator's
  error `chart '<id>': unknown table 'dm.ghost' (known tables: …)`

### Scenario: an unknown dimension blocks

- **GIVEN** a delivered `bar` chart that sums `revenue` by the dimension `ghost_region`, which
  `dm.sales_daily` does not declare
- **WHEN** the gate runs
- **THEN** `bi.contract` blocks with `bi.spec_invalid` and the evidence carries the validator's
  error for `ghost_region`

### Scenario: an unknown dashboard filter column blocks instead of an advisory

- **GIVEN** a delivered dashboard with a valid `bar` chart and a dashboard filter on
  `dm.sales_daily.ghost`, which the candidate model does not declare — today `bi.contract`
  compiles it and reports only the advisory `bi.filter_unwired`
- **WHEN** the gate runs
- **THEN** `bi.contract` blocks with `bi.spec_invalid` and the evidence carries the error
  `dashboard filter references unknown column: 'dm.sales_daily.ghost'`; no payload is stored for
  the dashboard

### Scenario: a column the candidate model drops blocks

- **GIVEN** a delivered dashboard whose chart sums `revenue` on `dm.sales_daily`, valid against
  the base model, and a candidate model in which `dm.sales_daily` no longer declares `revenue`
- **WHEN** the gate runs with that candidate model
- **THEN** `bi.contract` blocks with `bi.spec_invalid`, naming the unknown measure column
  `revenue`; the dashboard is validated against the candidate model, not the base

### Scenario: operator SQL the SELECT-only guard refuses blocks

- **GIVEN** a delivered Superset `table` chart whose operator `raw_sql` is `this is not SQL`
- **WHEN** the gate runs
- **THEN** `bi.contract` blocks with `bi.spec_invalid` and the evidence carries the validator's
  error `chart '<id>': raw_sql is not a single plain SELECT: …`; `impact.products` still names
  the tile `chart.opaque_sql` as before

### Scenario: operator SQL on DataLens blocks

- **GIVEN** a delivered DataLens `table` chart with operator `raw_sql`
  `SELECT region FROM dm.sales_daily`
- **WHEN** the gate runs
- **THEN** `bi.contract` blocks with `bi.spec_invalid` and the evidence carries the error
  `chart '<id>': raw_sql is supported only with target_bi=superset, got 'datalens'`

### Scenario: a valid dashboard next to an invalid one still compiles

- **GIVEN** a product delivering two dashboards, one valid and one with `SUM(ghost_revenue)`
- **WHEN** the gate runs
- **THEN** the valid dashboard is compiled and its payloads are stored exactly as when it is
  delivered alone; the invalid one is listed with its errors and has no payload; `bi.contract`
  blocks with `bi.spec_invalid`, and its detail counts one compiled dashboard and names the
  invalid ref

### Scenario: an invalid and a missing dashboard together

- **GIVEN** a product delivering two dashboards, one with an invalid spec and one with no spec
  supplied
- **WHEN** the gate runs
- **THEN** `bi.contract` blocks with summary code `bi.spec_invalid` (a block outranks the
  review of a missing spec); the evidence lists the invalid dashboard and the missing ref; the
  detail names both

### Scenario: identical inputs give identical evidence

- **GIVEN** the same invalid dashboard and inputs
- **WHEN** the gate runs twice
- **THEN** the `bi.contract` evidence is byte-identical, invalid dashboards in delivery order
  with their errors in the validator's order

## Requirement: a dashboard whose spec validates keeps today's outcome

A delivered dashboard whose spec validates against the candidate model SHALL be compiled exactly
as before: the same compiler findings and severities, the same payloads and the same payload
digests. A delivered dashboard with no supplied spec SHALL keep the review `bi.spec_missing`.
With no invalid dashboard, the summary code SHALL stay `bi.compiled` or `bi.spec_missing` as
today. The evidence SHALL always carry the list of invalid dashboards, empty when there is none.

### Scenario: a valid dashboard compiles on both targets as before

- **GIVEN** a delivered dashboard whose `big_number` chart sums `revenue` on `dm.sales_daily`,
  first with `target_bi` Superset and then DataLens
- **WHEN** the gate runs
- **THEN** `bi.contract` passes with `bi.compiled`, the stored payload blobs are byte-identical
  to those compiled before this capability, and the evidence lists no invalid dashboard

### Scenario: valid operator SQL on Superset compiles as before

- **GIVEN** a delivered Superset `table` chart with operator `raw_sql`
  `SELECT region, revenue FROM dm.sales_daily`
- **WHEN** the gate runs
- **THEN** the dashboard is compiled as before and `bi.contract` does not report it invalid

### Scenario: a valid dashboard filter on a qualified column compiles as before

- **GIVEN** a delivered dashboard with a dashboard filter on `dm.sales_daily.region` and a chart
  that groups `revenue` by `region`
- **WHEN** the gate runs
- **THEN** the dashboard is compiled with the same findings as before and is not reported
  invalid

### Scenario: a missing dashboard keeps its review

- **GIVEN** a product delivering one dashboard whose spec is not supplied
- **WHEN** the gate runs
- **THEN** `bi.contract` gives the review `bi.spec_missing` with the same detail as before, and
  the evidence lists no invalid dashboard

## Requirement: a delivered dashboard the compile refuses is named, not raised

A spec that validates may still not compile. The compile runs the build path's own builders,
and they refuse a spec they do not build by raising a `ValueError`: the planner of the shared
source dataset when two of its columns would share an alias (`SourceAliasCollisionError`), the
SQL generator when a filter holds a list where its operator takes one value, the contract when
a value has no canonical form to address (`CanonicalizationError`) or a name it must carry is
blank (a schema `ValidationError`), the DataLens dataset builder when the mart's engine has no
source type. Until now whatever they raised ended `decision verify` with a traceback, no report
and no bundle. The system SHALL NOT let such a refusal end the run.

A delivered dashboard whose spec validates and whose compile raises a `ValueError` SHALL be
listed by `bi.contract` as refused, in delivery order, with its ref and one line of what the
compile said: the class of what was raised, then its words with each run of whitespace as one
space — or the class alone when it said nothing. Where a model of the contract does not take
what the compile hands it, the words SHALL be the model's name and, for its first three
refusals, where and why, without the value, followed by the number of the rest. No BI payload
SHALL be stored for a refused dashboard, and the compile SHALL be asked of it once.

`bi.contract` SHALL block. Its summary code SHALL be `bi.spec_invalid` when a delivered spec is
invalid, otherwise `bi.compile_refused` (a block outranks the review of a missing spec). Its
detail SHALL name the refused refs after the invalid ones and before the missing ones, as
`; not compiled, the compiler refuses the spec: <refs>`. The evidence SHALL always carry the
list of refused dashboards, empty when there is none. Every other delivered dashboard SHALL be
compiled and graded as before, the dashboards after a refused one included, and no other check
SHALL move with the refusal.

A spec that does not validate SHALL NOT be handed to the compile, as before. Anything the
compile raises that is not a `ValueError` is not a refusal: it SHALL NOT be caught, and ends the
run as before. The gate adds no rule of its own: a spec the compile takes is compiled, whatever
it holds. Neither the validator's rules, nor the builders, nor the `DashboardSpec` schema change
in this capability.

### Scenario: a mart column named as the alias of a joined label blocks on Superset

- **GIVEN** a candidate model whose mart `dm.sales_daily` declares a column `stores_id`, and a
  delivered Superset dashboard whose `bar` chart sums `revenue` by the joined label
  `dm.stores.id` — the shared source dataset would carry `stores_id` twice
- **WHEN** the gate runs
- **THEN** `bi.contract` blocks with `bi.compile_refused`; its evidence lists the dashboard ref
  with `SourceAliasCollisionError: source dataset alias 'stores_id' collides between 'stores_id'
  and joined ref 'dm.stores.id' on dm.sales_daily`; no `bi-…` payload blob is stored for the
  dashboard; the detail is `0 dashboard(s) compiled without touching the BI; not compiled, the
  compiler refuses the spec: <ref>`; and the verdict is block

### Scenario: the same model and spec compile on DataLens

- **GIVEN** the same model and chart with `target_bi` DataLens, which plans one dataset per
  chart and no shared source dataset
- **WHEN** the gate runs
- **THEN** the dashboard is compiled as before and the evidence lists no refused dashboard

### Scenario: a mart column the model declares twice blocks on Superset

- **GIVEN** a candidate model whose mart declares the column `revenue` twice and a delivered
  Superset dashboard with a chart that renders off the shared source dataset
- **WHEN** the gate runs
- **THEN** `bi.contract` blocks with `bi.compile_refused` and the evidence carries
  `SourceAliasCollisionError: source dataset alias 'revenue' collides between 'revenue' and mart
  column 'revenue' on dm.sales_daily`; a dashboard whose only chart keeps a dataset of its own
  is compiled over the same model as before

### Scenario: two joined labels of one alias block

- **GIVEN** a candidate model that joins the mart to `dm.stores` and to `ref.stores`, and a
  delivered Superset dashboard with one chart grouped by `dm.stores.id` and another grouped by
  `ref.stores.id`
- **WHEN** the gate runs
- **THEN** `bi.contract` blocks with `bi.compile_refused`, naming the alias `stores_id` and both
  joined refs

### Scenario: a list where the SQL generator takes one value blocks

- **GIVEN** a delivered chart whose query filter is `region = ['a', 'b']`, compiled to SQL — a
  Superset chart that keeps a dataset of its own, or any DataLens chart
- **WHEN** the gate runs
- **THEN** `bi.contract` blocks with `bi.compile_refused` and the evidence carries
  `ValueError: operator '=' expects a scalar, got list: ['a', 'b']`

### Scenario: a filter value the contract cannot address blocks

- **GIVEN** a delivered chart whose query filter value is infinite, is no number, or is an
  integer no IEEE-754 double holds
- **WHEN** the gate runs on either target
- **THEN** `bi.contract` blocks with `bi.compile_refused`, and the evidence names the place of
  the value in the spec and why it has no canonical form — `CanonicalizationError:
  $.charts[0].query.filters[0].value: non-finite numbers have no canonical form`; an integer
  past the safe range that a double still holds compiles as before

### Scenario: a name the contract cannot hold blocks

- **GIVEN** a delivered dashboard whose title is empty or blanks, or a chart whose id is
- **WHEN** the gate runs on either target
- **THEN** `bi.contract` blocks with `bi.compile_refused` and the evidence carries
  `ValidationError: CompiledArtifact: subject: String should have at least 1 character`; a
  chart with an id and no title compiles as before

### Scenario: DataLens over an engine it has no source for blocks

- **GIVEN** a candidate model whose mart declares the engine `duckdb` and a delivered DataLens
  dashboard
- **WHEN** the gate runs
- **THEN** `bi.contract` blocks with `bi.compile_refused` and the evidence carries
  `ValueError: no DataLens source_type for engine 'duckdb'`

### Scenario: a refused dashboard next to a valid one

- **GIVEN** a product delivering two dashboards, one valid and one the compile refuses, in
  either order
- **WHEN** the gate runs
- **THEN** the valid dashboard is compiled and its payloads are stored exactly as when it is
  delivered alone; the refused one is listed with what the compile said and has no payload;
  `bi.contract` blocks with `bi.compile_refused`, and its detail counts one compiled dashboard
  and names the refused ref

### Scenario: a refused dashboard next to an invalid or a missing one

- **GIVEN** a product delivering two dashboards, one the compile refuses and one whose spec is
  invalid — or is not supplied
- **WHEN** the gate runs
- **THEN** with the invalid one the summary code is `bi.spec_invalid`, the evidence lists each
  dashboard in its own list and the detail names the invalid ref and then the refused one;
  with the missing one the summary code is `bi.compile_refused`, the check blocks, and the
  detail names the refused ref and then the missing one

### Scenario: identical inputs give identical evidence

- **GIVEN** two refused dashboards and the same inputs
- **WHEN** the gate runs twice
- **THEN** the `bi.contract` evidence is byte-identical, refused dashboards in delivery order

### Scenario: what is raised and is no refusal ends the run

- **GIVEN** a compile that raises an error that is not a `ValueError`
- **WHEN** the gate runs
- **THEN** the error is not caught: the run ends in it, as before
