# Capability: What keeps a dbt formula `external_ref`

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

A metric contract may take its formula from a dbt `semantic_manifest.json`
([ADR 0008](../adr/0008-dbt-semantic-manifest-external-formulas.md)): `formula.kind:
external_ref`, `external.source: dbt_semantic_manifest`, `external.ref` the dbt metric and
`external.source_revision` the pin `decision pin` prints. The resolver
(`auto_bi/semantic/metricflow_formula.py`) answers with a typed formula — `typed_aggregate` or
`typed_ratio` — or with gaps, never both. A formula with a gap stays `external_ref`, as every dbt
formula was before ADR 0008: impact reads it as `chart.formula_untyped`, and no reference SQL is
compiled for it. Its binding still carries the pin, and the closure digest wherever the artifact
was read, accepted and holds the metric, so a change of the dbt definition is named all the same
(`metric.binding_changed`) and an unpinned one blocks (`gap.external_revision_mismatch`).

The snapshot files each gap at `metrics.<id>.formula`, or at `metrics.<id>.formula:<JSON path>`
where the gap has a path in the artifact. The tables below are every reason the reader and the
resolver give: the gap, its severity and the JSON path it carries. In a path `[m]` and `[n]` are
positions in `$.metrics`, `[s]` in `$.semantic_models`, `[j]` in the measures and `[d]` in the
dimensions of that semantic model; `none` is a gap without a path.

`tests/test_dbt_formula_inventory.py` resolves one case per row — on the artifacts dbt-core
1.12.6 wrote (`tests/fixtures/dbt_semantic_manifest`), changed in memory where they do not carry
the construct — and holds the tables to the code.

## Requirement: every reason is a row, and a row is what the resolver gives

Each gap code the reader (`auto_bi/semantic/metricflow.py`) and the resolver define SHALL be the
gap of a row below, and a gap SHALL carry no code other than these. Each place in the resolver that
names or makes a gap SHALL be reached by the case of a row, and no other place SHALL name or make
one. The test sees a gap named or made by a call of `_construct`, `_gap` or `ExternalGap`, records
what made each gap a case reaches, and refuses every other form it knows: another mention of these
three — an alias, an import under another name, a call through another object or the module, the
name as a string — and a copy of an object — `copy`, `dataclasses.replace`, `__class__`, a call of
what a call returns. Each key the resolver reads as a construct when it is set SHALL be a row of
its own — for each side of a ratio where the key is a side's — and so SHALL each aggregation,
`agg_params` key and metric type of the schema the resolver does not resolve. A key of
`type_params` SHALL give its row on each kind of metric that does not take its number from it
(below): which keys those are follows from the schema, not from the resolver's tables. Each value
of an enumeration the resolver compares — the aggregation, the type of the aggregation time
dimension and its granularity — SHALL resolve or give the row of its kind, and each value of an
enumeration a key read as a construct takes — a metric's `datatype`, a `grain_to_date` — SHALL
give the key's row, on a simple metric and on a ratio. A key read as a construct that the reader
takes as any string — a metric's `time_granularity`, which may name a custom granularity, a
ratio's `expr`, an alias, an `offset_to_grain` — SHALL give its row for each string. Only null
leaves a key read as a construct unset. Its empty value — the empty string, 0, an empty list, an
empty object where the object has no required key — SHALL give its row, on a simple metric and
on a ratio where the key is one of each, unless the row says the value leaves the number alone:
an empty list of input metrics, a `join_to_timespine` of `false`. So SHALL a percentile of 0,
while `false`, the default of the other `agg_params` keys, resolves. Where an expression or a
name stands in for a column — a measure's or a V2 metric's `expr`, a time dimension's `expr` or
name, a legacy metric's `expr` held to its measure's — the empty string SHALL be one like
another, and an `agg_time_dimension` that is the empty string, where no dimension of the model
has that name, SHALL be refused like any other name the model lacks. Where a column comes from an
`expr` or, without one, from a name — a measure's, a time dimension's — each source SHALL be a
row of its own. The case of a row SHALL give that row's gap and severity and no other, at a path
of the row's shape. A change of the resolver that adds, removes or moves a reason SHALL change
its row.

#### Scenario: a dbt metric with a filter

- **GIVEN** a contract whose formula is the dbt metric `revenue`, pinned, and the metric
  carries a `filter`
- **WHEN** the resolver reads it
- **THEN** the formula stays `external_ref`, with one review gap
  `gap.external_construct_unsupported` at `$.metrics[m].filter` — the row `metric.filter`

## The artifact

The resolver stops at the first of these, and each is a block.

| Id | What | Gap | Severity | Path |
|---|---|---|---|---|
| `artifact.missing` | The contract names the dbt source, and no artifact was read with the native model; `decision verify` and `decision capture` refuse such a side with exit 2 before the gate | `gap.external_source_missing` | block | none |
| `artifact.invalid` | The reader refused the artifact — bytes that are not strict JSON, an object the schema version does not hold, two objects of one name, a metric that depends on itself, a reference to an absent object (`tests/test_metricflow_manifest.py`); one gap per problem | `gap.external_artifact_invalid` | block | the problem's |
| `artifact.version` | `project_configuration.dsi_package_version` is not one the reader encodes; today that is 0.213.0 alone | `gap.external_schema_unsupported` | block | `$.project_configuration.dsi_package_version` |
| `artifact.ref` | The artifact has no metric named `external.ref` | `gap.external_ref_missing` | block | `$.metrics` |
| `artifact.pin` | The closure digest of the metric is not `external.source_revision`; the reason names both | `gap.external_revision_mismatch` | block | none |

## Compatibility with the contract

Checked for a simple metric and a ratio whatever constructs they hold (ADR 0008, item 3), and
every one found is reported. The relation and the time are those of the semantic model the
metric aggregates in. Three constructs end the reading early, and what it would find after them
is not looked for:

- a metric of another type is answered by its type alone (`type.*`), and by `private` where it
  is private, which is asked before the type: a cumulative metric over another table stays
  review, not block;
- a ratio without a side, or over a metric that is not simple, or over one with both or neither
  way to aggregate, is answered by that, by the ratio's own keys and by the rest of what its
  sides hold, time included — without the relation, `ratio.models`, `ratio.times`,
  `ratio.input-measures` and additivity;
- a simple metric with both or neither way to aggregate (`simple.both-or-neither`) is answered
  by its own keys, without the time, the relation and additivity.

| Id | What | Gap | Severity | Path |
|---|---|---|---|---|
| `relation.table` | The semantic model's `schema_name.alias` is not the contract's grain table | `gap.external_relation_mismatch` | block | `$.semantic_models[s].node_relation` |
| `relation.database` | The semantic model's database is not the dbt database declared with the artifact (`--base-dbt-database`, `--candidate-dbt-database`) | `gap.external_relation_mismatch` | block | `$.semantic_models[s].node_relation.database` |
| `relation.database-undeclared` | The semantic model names a database, and none was declared with the artifact | `gap.external_relation_unproven` | review | `$.semantic_models[s].node_relation.database` |
| `time.none-legacy` | A legacy measure names no `agg_time_dimension`, and neither do the `defaults` of its semantic model | `gap.external_time_unproven` | review | `$.semantic_models[s].measures[j].agg_time_dimension` |
| `time.none-v2` | A V2 metric's `metric_aggregation_params.agg_time_dimension` is null; the `defaults` of the semantic model are not read for a V2 metric | `gap.external_time_unproven` | review | `$.metrics[m].type_params.metric_aggregation_params.agg_time_dimension` |
| `time.not-time` | The aggregation time dimension is not of type `time` | `gap.external_time_unproven` | review | `$.semantic_models[s].dimensions[d].type` |
| `time.expression` | The dimension's `expr` is not one lower-case column | `gap.external_time_unproven` | review | `$.semantic_models[s].dimensions[d].expr` |
| `time.name` | The dimension has no `expr`, and its name — the column dbt reads then — is not one lower-case column: a value word such as `null`, an upper-case name; the path is that of the `expr` the name stands in for | `gap.external_time_unproven` | review | `$.semantic_models[s].dimensions[d].expr` |
| `time.column-name` | The dimension has no `expr`, and its name is a column other than the contract's `time.event_time_column` | `gap.external_time_mismatch` | block | `$.semantic_models[s].dimensions[d].name` |
| `time.column-expr` | The dimension's `expr` is a column other than the contract's event time column | `gap.external_time_mismatch` | block | `$.semantic_models[s].dimensions[d].expr` |
| `time.no-granularity` | The artifact gives the dimension no granularity (`type_params: null`) — as dbt-core 1.12.6 writes every V2 time dimension, which keeps every V2 metric review | `gap.external_time_unproven` | review | `$.semantic_models[s].dimensions[d].type_params` |
| `time.granularity` | The dimension's granularity is not `day` | `gap.external_time_unproven` | review | `$.semantic_models[s].dimensions[d].type_params.time_granularity` |
| `ratio.models` | The numerator and the denominator aggregate in two semantic models | `gap.external_ratio_unproven` | review | `$.metrics[m].type_params.denominator` |
| `ratio.times` | The numerator and the denominator aggregate over two time dimensions | `gap.external_ratio_unproven` | review | `$.metrics[m].type_params.denominator` |
| `additivity.aggregate-legacy` | A legacy measure aggregates by `count_distinct`, `min`, `max` or `average`, and the contract declares `additivity: additive` | `gap.external_additivity_unproven` | review | `$.semantic_models[s].measures[j].agg` |
| `additivity.aggregate-v2` | A V2 metric aggregates by one of those, and the contract declares `additivity: additive` | `gap.external_additivity_unproven` | review | `$.metrics[m].type_params.metric_aggregation_params.agg` |
| `additivity.ratio` | A ratio, and the contract declares `additivity: additive` | `gap.external_additivity_unproven` | review | `$.metrics[m].type` |
| `private` | The contract's own metric is private in dbt (`is_private: true`); a private input of a public ratio is no gap | `gap.external_private_metric` | review | `$.metrics[m].type_params.is_private` |

## Constructs outside the supported subset

Each is review `gap.external_construct_unsupported` (ADR 0008, items 7 and 8): the typed formula —
an aggregate of one column, or a ratio of two such — would not see the construct, and a formula
that ignored it could say another number. A set key is a construct whatever its value says:
filter templates and SQL expressions are not parsed. A simple metric takes its number from the
`measure`, `expr` and `metric_aggregation_params` of its `type_params`, a ratio from `numerator`
and `denominator`; each other key there, but `input_measures` and `is_private`, which have rows
of their own, is a construct on that kind. A simple metric or a ratio may carry several
constructs, and each is reported, short of the three that end the reading early (above).

| Id | What | Gap | Severity | Path |
|---|---|---|---|---|
| `type.cumulative` | A cumulative metric | `gap.external_construct_unsupported` | review | `$.metrics[m].type` |
| `type.derived` | A derived metric | `gap.external_construct_unsupported` | review | `$.metrics[m].type` |
| `type.conversion` | A conversion metric | `gap.external_construct_unsupported` | review | `$.metrics[m].type` |
| `ratio.input-type` | A ratio whose numerator or denominator is not a simple metric; the path is the input's | `gap.external_construct_unsupported` | review | `$.metrics[n].type` |
| `ratio.numerator-missing` | A ratio without a numerator | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.numerator` |
| `ratio.denominator-missing` | A ratio without a denominator | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.denominator` |
| `ratio.input-measures` | A ratio's `input_measures` are not the measures of its numerator, then those of its denominator, each once | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.input_measures` |
| `metric.filter` | A metric filter — of the contract's metric, or of a ratio's numerator or denominator metric | `gap.external_construct_unsupported` | review | `$.metrics[m].filter` |
| `metric.time_granularity` | A metric `time_granularity` | `gap.external_construct_unsupported` | review | `$.metrics[m].time_granularity` |
| `metric.datatype` | A declared metric `datatype` | `gap.external_construct_unsupported` | review | `$.metrics[m].datatype` |
| `params.window` | A cumulative window | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.window` |
| `params.grain_to_date` | A grain to date | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.grain_to_date` |
| `params.metrics` | Input metrics; an empty list leaves the number alone | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.metrics` |
| `params.conversion_type_params` | Conversion parameters | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.conversion_type_params` |
| `params.cumulative_type_params` | Cumulative parameters | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.cumulative_type_params` |
| `params.join_to_timespine` | `join_to_timespine: true` | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.join_to_timespine` |
| `params.fill_nulls_with` | A value that fills NULL | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.fill_nulls_with` |
| `params.numerator` | A numerator of a simple metric | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.numerator` |
| `params.denominator` | A denominator of a simple metric | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.denominator` |
| `params.measure` | A measure of a ratio | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.measure` |
| `params.expr` | An expression of a ratio | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.expr` |
| `params.metric_aggregation_params` | Aggregation parameters of a ratio | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.metric_aggregation_params` |
| `measure-input.filter` | A filter of the measure a legacy metric names | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.measure.filter` |
| `measure-input.alias` | An alias of that measure | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.measure.alias` |
| `measure-input.join_to_timespine` | `join_to_timespine: true` of that measure | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.measure.join_to_timespine` |
| `measure-input.fill_nulls_with` | A value that fills NULL of that measure | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.measure.fill_nulls_with` |
| `metric-input.numerator.filter` | A filter of a ratio's numerator | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.numerator.filter` |
| `metric-input.numerator.alias` | An alias of a ratio's numerator | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.numerator.alias` |
| `metric-input.numerator.offset_window` | An offset window of a ratio's numerator | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.numerator.offset_window` |
| `metric-input.numerator.offset_to_grain` | An offset to grain of a ratio's numerator | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.numerator.offset_to_grain` |
| `metric-input.denominator.filter` | A filter of a ratio's denominator | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.denominator.filter` |
| `metric-input.denominator.alias` | An alias of a ratio's denominator | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.denominator.alias` |
| `metric-input.denominator.offset_window` | An offset window of a ratio's denominator | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.denominator.offset_window` |
| `metric-input.denominator.offset_to_grain` | An offset to grain of a ratio's denominator | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.denominator.offset_to_grain` |
| `simple.both-or-neither` | A simple metric that names both a measure and `metric_aggregation_params`, or neither | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params` |
| `legacy.input-measures` | A legacy metric whose `input_measures` are not its measure alone | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.input_measures` |
| `legacy.agg.sum_boolean` | A measure aggregated by `sum_boolean` | `gap.external_construct_unsupported` | review | `$.semantic_models[s].measures[j].agg` |
| `legacy.agg.percentile` | A measure aggregated by `percentile` | `gap.external_construct_unsupported` | review | `$.semantic_models[s].measures[j].agg` |
| `legacy.agg.median` | A measure aggregated by `median` | `gap.external_construct_unsupported` | review | `$.semantic_models[s].measures[j].agg` |
| `legacy.agg_params.percentile` | A measure's `agg_params.percentile` set | `gap.external_construct_unsupported` | review | `$.semantic_models[s].measures[j].agg_params` |
| `legacy.agg_params.use_discrete_percentile` | A measure's `agg_params.use_discrete_percentile: true` | `gap.external_construct_unsupported` | review | `$.semantic_models[s].measures[j].agg_params` |
| `legacy.agg_params.use_approximate_percentile` | A measure's `agg_params.use_approximate_percentile: true` | `gap.external_construct_unsupported` | review | `$.semantic_models[s].measures[j].agg_params` |
| `legacy.non-additive-dimension` | A measure's `non_additive_dimension` | `gap.external_construct_unsupported` | review | `$.semantic_models[s].measures[j].non_additive_dimension` |
| `legacy.expr` | A measure `expr` that is not one lower-case column: an expression, a quoted or upper-case name, a value word such as `null` or `true` | `gap.external_construct_unsupported` | review | `$.semantic_models[s].measures[j].expr` |
| `legacy.measure-name` | A measure without `expr` whose name — the column dbt reads then — is not one lower-case column: a value word such as `null`, an upper-case name | `gap.external_construct_unsupported` | review | `$.semantic_models[s].measures[j].name` |
| `legacy.metric-expr` | A legacy metric `expr` other than its measure's column | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.expr` |
| `v2.input-measures` | A V2 metric with `input_measures` | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.input_measures` |
| `v2.agg.sum_boolean` | A V2 metric aggregated by `sum_boolean` | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.metric_aggregation_params.agg` |
| `v2.agg.percentile` | A V2 metric aggregated by `percentile` | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.metric_aggregation_params.agg` |
| `v2.agg.median` | A V2 metric aggregated by `median` | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.metric_aggregation_params.agg` |
| `v2.agg_params.percentile` | A V2 metric's `agg_params.percentile` set | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.metric_aggregation_params.agg_params` |
| `v2.agg_params.use_discrete_percentile` | A V2 metric's `agg_params.use_discrete_percentile: true` | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.metric_aggregation_params.agg_params` |
| `v2.agg_params.use_approximate_percentile` | A V2 metric's `agg_params.use_approximate_percentile: true` | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.metric_aggregation_params.agg_params` |
| `v2.non-additive-dimension` | A V2 metric's `non_additive_dimension` | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.metric_aggregation_params.non_additive_dimension` |
| `v2.no-expr` | A V2 metric without `expr` | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.expr` |
| `v2.expr` | A V2 metric `expr` that is not one lower-case column | `gap.external_construct_unsupported` | review | `$.metrics[m].type_params.expr` |
| `formula.table` | The contract's grain table is not a lower-case `schema.table` name, which a typed term does not take | `gap.external_construct_unsupported` | review | `$.metrics[m]` |

## Parity

Asked last, and only of a formula with no other gap (ADR 0008, item 7). A resolution relies on
rows of the parity matrix in its context `<engine>.<event time>`; a row no recorded run proved is
review `gap.external_parity_unproven` at the metric, and the reason names the rows: a row
proven in a context does not prove another there. Proven today are 164 rows in the context
`clickhouse.date` and 164 rows in the context `postgres.date`
([parity report](../reviews/DECISIONOPS_METRICFLOW_PARITY_CLICKHOUSE_2026-11-07.md)).

| Id | What | Gap | Severity | Path |
|---|---|---|---|---|
| `parity.engine` | The native model declares the grain table on an engine where the rows the formula relies on are not all proven — today any but PostgreSQL and ClickHouse: Greenplum — or on none | `gap.external_parity_unproven` | review | `$.metrics[m]` |
| `parity.event-time-type` | The event time column is a grain key of a type other than `date`, such as `timestamp` | `gap.external_parity_unproven` | review | `$.metrics[m]` |
| `parity.event-time-outside-grain` | The event time column is not a grain key | `gap.external_parity_unproven` | review | `$.metrics[m]` |
| `parity.column-type` | The aggregated column is of a type outside the classes `decimal`, `integer` and `float` — `text`, `date`, an array — or of no declared type | `gap.external_parity_unproven` | review | `$.metrics[m]` |
| `parity.ratio-average` | A ratio term under `average` | `gap.external_parity_unproven` | review | `$.metrics[m]` |
| `parity.ratio-float` | A ratio term under `sum`, `min` or `max` over a `float` column | `gap.external_parity_unproven` | review | `$.metrics[m]` |
| `parity.v2` | A V2 metric: no V2 row was run, since no V2 metric gets this far today (`time.no-granularity`) | `gap.external_parity_unproven` | review | `$.metrics[m]` |

## What dbt-core 1.12.6 writes

The corpus of ADR 0008, slice S5b, asks dbt which of these rows a dbt project brings
([report](../reviews/DECISIONOPS_DBT_CORPUS_2026-11-07.md)). `scripts/dbt_corpus.py` holds an
entry per construct, as an author writes it in the legacy dialect or in V2, with the rows its
metric is to give: the row the entry aims at, and those dbt's writing brings along. dbt-core
1.12.6 (dbt-postgres 1.11.0, metricflow 0.213.0) parsed each entry alone, then the entries it
accepted together, once per dialect; there each metric resolves as on its own project, or the
run stops. `tests/fixtures/dbt_semantic_manifest/corpus` keeps both artifacts and the record,
and `tests/test_dbt_corpus.py` holds the lists and the facts below to the record.

An entry carries its rows when dbt accepted it and the resolver gives its metric, on the
artifact of the entries accepted, the gaps of those rows and no other. A gap is the row with
its code, severity and path shape; where rows share those, the resolver's reason, whole, tells
them apart — for a parity gap, the rows of the matrix it names — and where they share the reason
too — a time dimension's `expr` and its name, the keys of `agg_params` — the value the artifact
has at the gap's path. One gap can be two rows: the `agg_params` of a percentile with
`use_discrete_percentile: true` are `legacy.agg_params.percentile` and
`legacy.agg_params.use_discrete_percentile`.

**Written** (63) — an entry carries the row.

`relation.table`, `relation.database`, `time.none-v2`, `time.expression`, `time.name`,
`time.column-name`, `time.column-expr`, `time.no-granularity`, `time.granularity`, `ratio.models`,
`ratio.times`, `additivity.aggregate-legacy`, `additivity.aggregate-v2`, `additivity.ratio`,
`private`, `type.cumulative`, `type.derived`, `type.conversion`, `ratio.input-type`,
`metric.filter`, `metric.time_granularity`, `params.window`, `params.grain_to_date`,
`params.metrics`, `params.conversion_type_params`, `params.join_to_timespine`, `params.numerator`,
`params.denominator`, `params.measure`, `params.expr`, `measure-input.filter`,
`measure-input.alias`, `measure-input.join_to_timespine`, `measure-input.fill_nulls_with`,
`metric-input.numerator.filter`, `metric-input.numerator.alias`,
`metric-input.numerator.offset_window`, `metric-input.numerator.offset_to_grain`,
`metric-input.denominator.filter`, `metric-input.denominator.alias`,
`metric-input.denominator.offset_window`, `metric-input.denominator.offset_to_grain`,
`legacy.agg.sum_boolean`, `legacy.agg.percentile`, `legacy.agg.median`,
`legacy.agg_params.percentile`, `legacy.agg_params.use_discrete_percentile`,
`legacy.agg_params.use_approximate_percentile`, `legacy.non-additive-dimension`, `legacy.expr`,
`legacy.measure-name`, `legacy.metric-expr`, `v2.agg.sum_boolean`, `v2.agg.percentile`,
`v2.agg.median`, `v2.agg_params.percentile`, `v2.agg_params.use_discrete_percentile`,
`v2.agg_params.use_approximate_percentile`, `v2.non-additive-dimension`, `v2.no-expr`, `v2.expr`,
`formula.table`, `parity.ratio-average`.

**Refused** (10) — dbt refuses every entry built for the row.

`time.none-legacy`, `time.not-time`, `ratio.numerator-missing`, `ratio.denominator-missing`,
`ratio.input-measures`, `metric.datatype`, `params.metric_aggregation_params`,
`simple.both-or-neither`, `legacy.input-measures`, `v2.input-measures`.

**Not carried** (3) — dbt accepts an entry built for the row, and none carries it.

`params.cumulative_type_params`, `params.fill_nulls_with`, `parity.v2`.

**No entry** (11) — no entry is built for the row: it turns on the artifact as a whole, or on
what the corpus holds the same for every entry — the contract's reference, pin, event time and
grain keys, the database declared with the artifact, and the native model's declaration of the
grain table.

`artifact.missing`, `artifact.invalid`, `artifact.version`, `artifact.ref`, `artifact.pin`,
`relation.database-undeclared`, `parity.engine`, `parity.event-time-type`,
`parity.event-time-outside-grain`, `parity.column-type`, `parity.ratio-float`.

What dbt does with the constructs it does not carry:

- The JSON schema of dbt's properties refuses a key it does not know: a legacy metric's
  `datatype` and `hidden`, and `join_to_timespine`, `fill_nulls_with`,
  `metric_aggregation_params` and `input_measures` in its `type_params`; a V2 metric's
  `datatype` and `input_measures`.
- `window` and `grain_to_date` at the top of a legacy `type_params` are refused unless the
  project sets the behaviour flag `require_nested_cumulative_type_params: false`; with it, dbt
  writes them there, and the resolver gives their rows.
- dbt writes as null the `cumulative_type_params` of a legacy simple metric, and the `window`,
  `grain_to_date` and `fill_nulls_with` of a V2 simple metric: the fill value is lost, `0` or `5`.
- A V2 `percentile_type` of `discrete` sets `use_discrete_percentile`, `continuous` sets
  `use_approximate_percentile`, and another value — `approximate_continuous` — neither. The
  resolver leaves each percentile to review all the same (`v2.agg.percentile`).
- A name dbt reserves (`null`) or with an upper-case letter is refused; `true` as a measure's
  name and `current_date` as a time dimension's are taken, and give `legacy.measure-name` and
  `time.name`.
- No V2 metric resolves: dbt writes no `type_params` for a V2 time dimension, so each V2 metric
  stops at `time.no-granularity` — but the one whose semantic model sets no aggregation time
  dimension (`time.none-v2`) — and none reaches `parity.v2`.

## What the artifact does not show

The resolver reads the artifact, not the dbt project. dbt-core 1.12.6 writes a V2 metric's
`fill_nulls_with` as `null`, whatever its value, and no granularity for a V2 time dimension
(ADR 0008, item 8; the corpus above). The second keeps every V2 metric review: at
`time.no-granularity`, or at `time.none-v2` where its semantic model sets no aggregation time
dimension. The schema version does not guard the first: `dsi_package_version`
marks the package, not the schema (ADR 0008, item 6), and a dbt release could write the
granularity under 0.213.0. The reader would accept that artifact, and its V2 metrics would stop
at `parity.v2`, since no V2 row is proven. A V2 row joins the matrix only through a recorded
parity run (the matrix is the run's `proven`), and that run compares MetricFlow with the
reference SQL on the artifact as written: the fill value it lost is lost on both sides, so the
run has to check that value apart.
