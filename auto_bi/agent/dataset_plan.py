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
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from auto_bi.ir.spec import ChartQuery, ChartSpec, DashboardSpec, JoinSpec
from auto_bi.semantic.model import SemanticModel


class DatasetRole(StrEnum):
    SOURCE = "source"  # shared semantic-grain dataset, aggregation in the BI
    OWN = "own"  # per-chart aggregated dataset (today's behavior)


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


def filter_preview_notes(spec: DashboardSpec) -> list[str]:
    """Honest preview badges for OWN charts when the dashboard has filters.

    Julia's gate decision (2026-07-20): inexpressible charts keep their own dataset
    and the interactive control does not move them — so the preview must say so,
    never leave a lying control. Empty when there are no dashboard filters (nothing
    for the badge to warn about) or every chart is SOURCE.
    """
    if not spec.filters:
        return []
    plan = plan_datasets(spec)
    notes: list[str] = []
    for chart in spec.charts:
        cp = plan.charts[chart.id]
        if cp.role is DatasetRole.OWN and cp.fallback_reason:
            notes.append(f"«{chart.title}»: фильтр не влияет: {cp.fallback_reason}")
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
    qualified refs are the union across the table's SOURCE charts (B3 already
    guarantees bare-alias collisions were rejected at normalize time).
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
    return SourceDatasetInputs(
        table=table, columns=columns, joins=tuple(joins), joined_refs=tuple(joined_refs)
    )
