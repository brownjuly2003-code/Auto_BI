"""D-1 (variant A): which charts share the semantic-grain source dataset.

The BI delivers a dashboard filter to a chart as a WHERE over the chart's
dataset (Superset: extraFormData filters; DataLens: the same model), so a chart
participates in the filter only while its dataset still carries the filter
column. Charts compiled into their own already-aggregated virtual dataset lose
every column outside their grain — which is why a period control used to move
1 chart out of 8.

The plan splits a spec in two:
- SOURCE — the chart renders off the shared per-mart dataset (the mart at its
  own grain plus label joins, no GROUP BY); aggregation moves into the BI as
  native metrics, so every mart column stays filterable.
- OWN — the chart's measures cannot be expressed as a BI-side aggregate
  (window transform, scalar period-compare, raw_sql hatch, histogram binning);
  it keeps today's per-chart aggregated dataset with the baked default period,
  and the preview marks it "the filter does not affect this chart" (Julia's
  gate decision 2026-07-20: honest badge, never a lying control).

Deterministic and model-free by design: the verdict is read off IR features
only (invariant 1 — no LLM in the loop), so it doubles as the honest coverage
number shown in the preview.

Scope for a dashboard filter is computed here (engine-neutral) and reused by
the preview (`spec_summary`) and the Superset native-filter wiring — one
function, never a second approximation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from auto_bi.ir.spec import (
    ChartQuery,
    ChartSpec,
    DashboardFilter,
    DashboardSpec,
    JoinSpec,
    column_alias,
)
from auto_bi.semantic.model import SemanticModel


class DatasetRole(StrEnum):
    SOURCE = "source"  # shared semantic-grain dataset, aggregation in the BI
    OWN = "own"  # per-chart aggregated dataset (today's behavior)


class SourceAliasCollisionError(ValueError):
    """Two source-dataset columns would share the same SELECT alias.

    Raised at plan/SQL time so a collision never reaches the BI as a silently
    ambiguous GROUP BY (the class of bug that once produced wrong numbers with
    green tests).
    """


@dataclass(frozen=True)
class ChartDatasetPlan:
    chart_id: str
    role: DatasetRole
    table: str  # the mart the chart reads ("dm.sales_daily")
    fallback_reason: str | None = None  # human-readable, only for OWN


@dataclass(frozen=True)
class DatasetPlan:
    charts: dict[str, ChartDatasetPlan]  # chart_id -> plan
    source_tables: tuple[str, ...]  # marts that need a shared dataset, spec order

    def chart(self, chart_id: str) -> ChartDatasetPlan:
        return self.charts[chart_id]

    def source_chart_ids(self) -> set[str]:
        return {c.chart_id for c in self.charts.values() if c.role is DatasetRole.SOURCE}


def inexpressible_reason(query: ChartQuery) -> str | None:
    """Why this query cannot render off the shared source dataset (None = it can).

    Mirrors the SQL_GEN branches that leave plain `agg(column)` territory: those
    queries compute in SQL what the BI cannot re-derive from raw rows with a
    native metric. A ratio measure (`denominator`) IS expressible — it becomes a
    single adhoc SQL expression over the source rows — so it does not fall back.
    """
    if query.raw_sql is not None:
        return "raw_sql: запрос написан вручную, вне IR"
    if query.bins is not None:
        return "histogram: биннинг считается в SQL"
    for m in query.measures:
        if m.transform is not None:
            return f"оконная мера {m.transform.value}: окно считается в SQL"
        if m.compare is not None:
            return f"period-compare {m.compare.kind.value}: скаляр считается в SQL"
    return None


def _chart_plan(chart: ChartSpec) -> ChartDatasetPlan:
    reason = inexpressible_reason(chart.query)
    if reason is not None:
        return ChartDatasetPlan(chart.id, DatasetRole.OWN, chart.query.table, reason)
    return ChartDatasetPlan(chart.id, DatasetRole.SOURCE, chart.query.table)


def plan_datasets(spec: DashboardSpec) -> DatasetPlan:
    charts = {chart.id: _chart_plan(chart) for chart in spec.charts}
    source_tables: list[str] = []
    for chart in spec.charts:  # spec order, deduped — deterministic artifact naming
        plan = charts[chart.id]
        if plan.role is DatasetRole.SOURCE and plan.table not in source_tables:
            source_tables.append(plan.table)
    return DatasetPlan(charts=charts, source_tables=tuple(source_tables))


# --- source-dataset column aliases --------------------------------------------


def source_column_alias(ref: str, mart_table: str) -> str:
    """SELECT alias of a column on the shared semantic-grain source dataset.

    Mart's own columns (bare or qualified to the mart) stay bare so filter targets
    and form_data groupby keep the familiar names. Joined label refs become
    deterministic unique aliases: ``dm.stores.name`` -> ``stores_name`` — never the
    bare ``name`` that collides when two joins contribute the same bare column.
    """
    if "." not in ref:
        return ref
    table_qual, _, col = ref.rpartition(".")
    if table_qual == mart_table:
        return col
    table_name = table_qual.rpartition(".")[2]
    return f"{table_name}_{col}"


def collect_source_aliases(
    columns: Sequence[str],
    joined_refs: Sequence[str],
    mart_table: str,
) -> dict[str, str]:
    """Map each source column ref (bare mart name or qualified join ref) -> alias.

    Raises ``SourceAliasCollisionError`` when two inputs would share an alias —
    loud failure at plan time, never a silently ambiguous dataset schema.
    """
    alias_owner: dict[str, str] = {}
    out: dict[str, str] = {}
    for col in columns:
        alias = source_column_alias(col, mart_table)
        if alias in alias_owner:
            raise SourceAliasCollisionError(
                f"source dataset alias {alias!r} collides between "
                f"{alias_owner[alias]!r} and mart column {col!r} on {mart_table}"
            )
        alias_owner[alias] = col
        out[col] = alias
    for ref in joined_refs:
        alias = source_column_alias(ref, mart_table)
        if alias in alias_owner:
            raise SourceAliasCollisionError(
                f"source dataset alias {alias!r} collides between "
                f"{alias_owner[alias]!r} and joined ref {ref!r} on {mart_table}"
            )
        alias_owner[alias] = ref
        out[ref] = alias
    return out


def filter_bound_column(filter_column: str, mart_table: str) -> str:
    """Column name the native filter binds on the source dataset for `mart_table`."""
    return source_column_alias(filter_column, mart_table)


# --- filter scope (preview + native filter wiring share this) -----------------


def qualified_column_ref(ref: str, default_table: str) -> str:
    """Fully qualify a column ref against `default_table` when it is bare.

    Dashboard filters and joined dimensions are already `schema.table.col`; mart grain
    columns are often bare (`date`, `store_id`). Comparing bare names alone wrongly
    equates `dm.products.name` with `dm.stores.name` (both bare-alias to `name`).
    """
    if "." in ref:
        return ref
    return f"{default_table}.{ref}"


def grain_exposes_column(chart: ChartSpec, filter_column: str) -> bool:
    """Whether the chart's GROUP BY grain carries `filter_column` (qualified match)."""
    target = qualified_column_ref(filter_column, chart.query.table)
    for col in chart.query.group_columns():
        if qualified_column_ref(col, chart.query.table) == target:
            return True
    return False


def source_exposes_column(
    spec: DashboardSpec,
    plan: DatasetPlan,
    model: SemanticModel,
    table: str,
    filter_column: str,
) -> bool:
    """Whether the shared source dataset for `table` carries `filter_column`."""
    inputs = source_dataset_inputs(spec, plan, model, table)
    target = qualified_column_ref(filter_column, table)
    # mart's own column: filter names schema.table.col and the bare name is in columns
    ft, _, fname = filter_column.rpartition(".")
    if ft == table and fname in inputs.columns:
        return True
    if target in inputs.joined_refs or filter_column in inputs.joined_refs:
        return True
    # bare filter (unusual for DashboardFilter) against mart columns
    return "." not in filter_column and filter_column in inputs.columns


def filter_binding_alias(
    spec: DashboardSpec,
    plan: DatasetPlan,
    model: SemanticModel,
    filter_: DashboardFilter,
) -> str:
    """Dataset column name the dashboard control binds to — decided once per spec.

    The BI applies one control as a WHERE by ONE column name across every in-scope
    chart, so the binding must be chosen spec-wide, not per chart. When any SOURCE
    chart's shared dataset exposes the column, the control binds the source alias
    (joined refs alias to ``stores_name``) and an OWN chart participates only if its
    dataset happens to carry that same name. When NO source dataset takes the filter
    (e.g. every chart fell back to OWN), the control binds the bare pre-D-1 alias
    instead — every OWN dataset carries ``column_alias`` for a grain column, so the
    filter keeps working rather than being dropped entirely.

    Single source of truth for the preview, the scope rule and the Superset wiring —
    never re-derive the binding elsewhere.
    """
    for chart in spec.charts:
        cp = plan.chart(chart.id)
        if cp.role is DatasetRole.SOURCE and source_exposes_column(
            spec, plan, model, cp.table, filter_.column
        ):
            return filter_bound_column(filter_.column, cp.table)
    return column_alias(filter_.column)


def chart_accepts_filter(
    chart: ChartSpec,
    filter_: DashboardFilter,
    spec: DashboardSpec,
    plan: DatasetPlan,
    model: SemanticModel,
) -> bool:
    """Whether a dashboard filter's WHERE can reach this chart's dataset.

    SOURCE: the shared source dataset for the chart's mart must expose the column
    (mart columns + label joins collected from SOURCE charts).

    OWN: the column must be in the chart's GROUP BY grain AND the OWN dataset's
    column name for that ref (always the bare SQL_GEN ``column_alias``) must equal
    the control's spec-wide binding (`filter_binding_alias`). With a source dataset
    in play the binding is the source alias (``stores_name``), so an OWN chart whose
    SQL emits bare ``name`` is excluded rather than wired to a dead control. With no
    source taker the binding IS the bare alias and the OWN chart participates —
    an OWN-only dashboard no longer loses the joined filter entirely.
    """
    cp = plan.chart(chart.id)
    if cp.role is DatasetRole.SOURCE:
        return source_exposes_column(spec, plan, model, cp.table, filter_.column)
    if not grain_exposes_column(chart, filter_.column):
        return False
    # OWN dataset column is always the bare SQL_GEN alias (column_alias)
    own_dataset_col = column_alias(filter_.column)
    return own_dataset_col == filter_binding_alias(spec, plan, model, filter_)


def own_filter_alias_mismatch(
    chart: ChartSpec,
    filter_: DashboardFilter,
    spec: DashboardSpec,
    plan: DatasetPlan,
    model: SemanticModel,
) -> bool:
    """True when an OWN chart's grain has the filter column but aliases diverge.

    Used for honest preview notes: the control cannot reach this chart even though
    the chart groups by the same logical column. Judged against the control's real
    spec-wide binding (`filter_binding_alias`), so an OWN-only dashboard — where the
    control falls back to the bare alias and DOES move the chart — gets no note.
    """
    cp = plan.chart(chart.id)
    if cp.role is not DatasetRole.OWN:
        return False
    if not grain_exposes_column(chart, filter_.column):
        return False
    own_dataset_col = column_alias(filter_.column)
    return own_dataset_col != filter_binding_alias(spec, plan, model, filter_)


def filter_preview_notes(spec: DashboardSpec, model: SemanticModel | None = None) -> list[str]:
    """Honest preview badges for charts a dashboard filter cannot move.

    Julia's gate decision (2026-07-20): inexpressible charts keep their own dataset
    and the interactive control does not move them — so the preview must say so,
    never leave a lying control. Also surfaces the joined-alias trap: OWN charts
    whose grain matches a joined filter column but whose dataset alias differs from
    the filter's bound source alias. Empty when there are no dashboard filters.
    """
    if not spec.filters:
        return []
    plan = plan_datasets(spec)
    notes: list[str] = []
    for chart in spec.charts:
        cp = plan.charts[chart.id]
        if model is None:
            # without a model neither the scope rule nor the binding is computable
            # (both read the source dataset shape) — keep the coarse pre-model badge
            if cp.role is DatasetRole.OWN and cp.fallback_reason:
                notes.append(f"«{chart.title}»: фильтр не влияет: {cp.fallback_reason}")
            continue
        # the badge must not lie in either direction: an OWN chart that some control
        # DOES move (mart-grain match, or the OWN-only bare binding) gets no blanket
        # "фильтр не влияет" note
        moved = any(chart_accepts_filter(chart, f, spec, plan, model) for f in spec.filters)
        if cp.role is DatasetRole.OWN and cp.fallback_reason and not moved:
            notes.append(f"«{chart.title}»: фильтр не влияет: {cp.fallback_reason}")
        for filter_ in spec.filters:
            if own_filter_alias_mismatch(chart, filter_, spec, plan, model):
                own_col = column_alias(filter_.column)
                bound = filter_binding_alias(spec, plan, model, filter_)
                notes.append(
                    f"«{chart.title}»: фильтр «{filter_.column}» не влияет: "
                    f"колонка датасета «{own_col}» ≠ bound «{bound}»"
                )
    return notes


@dataclass(frozen=True)
class SourceDatasetInputs:
    """Everything `generate_source_sql` needs for one mart's shared dataset."""

    table: str
    columns: tuple[str, ...]  # the mart's own columns, bare names, model order
    joins: tuple[JoinSpec, ...]  # label joins used by the table's SOURCE charts, deduped
    joined_refs: tuple[str, ...]  # qualified label columns those charts group by


def source_dataset_inputs(
    spec: DashboardSpec, plan: DatasetPlan, model: SemanticModel, table: str
) -> SourceDatasetInputs:
    """Collect the source dataset's shape from the model and the SOURCE charts.

    The dataset carries EVERY mart column (not only the ones today's charts touch):
    a dashboard filter may target any mart column, and a stable schema means chart
    edits re-use the dataset instead of rewriting it. Label joins and their
    qualified refs are the union across the table's SOURCE charts. Alias uniqueness
    is validated here (plan time) so a colliding pair never reaches SQL_GEN.
    """
    tbl = model.table(table)
    columns = tuple(c.name for c in tbl.columns) if tbl else ()
    joins: list[JoinSpec] = []
    seen_joins: set[tuple[str, str, str]] = set()
    joined_refs: list[str] = []
    for chart in spec.charts:
        cp = plan.charts.get(chart.id)
        if cp is None or cp.role is not DatasetRole.SOURCE or cp.table != table:
            continue
        for j in chart.query.joins:
            key = (j.table, j.on_left, j.on_right)
            if key not in seen_joins:
                seen_joins.add(key)
                joins.append(j)
        for ref in chart.query.group_columns():
            # a qualified ref to the mart itself is already covered by the bare
            # mart columns (its chart-side alias IS the bare name) — only refs
            # into joined label tables add a column to the dataset
            if "." in ref and not ref.startswith(f"{table}.") and ref not in joined_refs:
                joined_refs.append(ref)
    # loud failure if the alias set is not unique
    collect_source_aliases(columns, joined_refs, table)
    return SourceDatasetInputs(
        table=table, columns=columns, joins=tuple(joins), joined_refs=tuple(joined_refs)
    )
