"""Deterministic chart-default normalization (dashboard adequacy B1).

After the LLM proposes a spec, a categorical chart (bar / stacked_bar / pie) over a
high-cardinality dimension with no measure ordering renders as a "wall of bars": the
SQL default limit is 5000 *unordered* rows. The propose prompt (rule 5) *asks* the LLM
to add ``order_by desc + limit 10-50``, but that is advisory — a stubborn LLM, a
hand-built spec, or a fields-first seed can omit it, leaving the wall.

This module enforces a sane top-N deterministically at the IR layer, so BOTH BI
adapters (Superset, DataLens) inherit the fix from one place (BI-agnostic — see
``docs/plans/2026-06-14-dashboard-adequacy-fixes.md`` B1). It is pure and idempotent,
and only acts where the author did *not* already express top-N intent.

Skips (left untouched):
- non-categorical viz (line/area/table/pivot/heatmap/big_number);
- a chart whose primary dimension is a TIME column — a column time-series is ordered by
  time, not ranked by value (forcing measure-desc + a tight limit would silently drop
  the tail of a chronological chart). The semantic model resolves the role, which is why
  this needs `model`, unlike the plan's bare `apply_chart_defaults(spec)` sketch;
- a chart that already orders by a measure (the author's explicit top-N).
"""

from __future__ import annotations

from auto_bi.ir.spec import (
    ChartQuery,
    ChartSpec,
    DashboardSpec,
    OrderBy,
    Viz,
    measure_alias,
)
from auto_bi.semantic.model import ColumnRole, SemanticModel

# viz whose x-axis / slices are categorical and degrade into a "wall" without top-N
_CATEGORICAL_VIZ = frozenset({Viz.BAR, Viz.STACKED_BAR, Viz.PIE})
# top-N caps: a pie with >12 slices is unreadable; bars tolerate a few more rows
_DEFAULT_TOP_N = 25
_PIE_TOP_N = 12


def _orders_by_measure(query: ChartQuery) -> bool:
    """True if the author already ordered by a measure (their explicit top-N intent).

    Mirrors SQL_GEN's order-target resolution exactly: a measure is addressable by its
    raw column, its canonical SELECT alias, or its label.
    """
    measure_refs: set[str] = set()
    for m in query.measures:
        measure_refs.add(m.column)
        measure_refs.add(measure_alias(m))
        if m.label:
            measure_refs.add(m.label)
    return any(ob.by in measure_refs for ob in query.order_by)


def _is_time_dimension(ref: str, base_table: str, model: SemanticModel) -> bool:
    """Role of a dimension reference, resolved against the model; False if unresolvable.

    `ref` is either bare (a column of the chart's base table) or fully qualified
    ('dm.stores.city'). Defensive: an unknown table/column resolves to non-time so an
    invalid spec is left for `validate_spec` to reject, not crashed here.
    """
    if "." in ref:
        table_name, _, col = ref.rpartition(".")
    else:
        table_name, col = base_table, ref
    table = model.table(table_name)
    if table is None:
        return False
    column = table.column(col)
    return column is not None and column.role == ColumnRole.TIME


def _normalize_chart(chart: ChartSpec, model: SemanticModel) -> ChartSpec:
    query = chart.query
    if chart.viz not in _CATEGORICAL_VIZ:
        return chart
    if not query.dimensions:  # no categorical axis to rank
        return chart
    if _is_time_dimension(query.dimensions[0], query.table, model):
        return chart  # column time-series: ordered by time, never ranked by value
    if _orders_by_measure(query):  # author already set an explicit top-N
        return chart

    cap = _PIE_TOP_N if chart.viz == Viz.PIE else _DEFAULT_TOP_N
    new_query = query.model_copy(
        update={
            "order_by": [OrderBy(by=measure_alias(query.measures[0]), dir="desc")],
            "limit": min(query.limit, cap),  # tighten only; never widen an explicit small limit
        }
    )
    return chart.model_copy(update={"query": new_query})


def apply_chart_defaults(spec: DashboardSpec, model: SemanticModel) -> DashboardSpec:
    """Return a copy of `spec` with deterministic top-N defaults on categorical charts.

    Pure and idempotent: a second pass is a no-op (after the first, the chart orders by
    a measure, so `_orders_by_measure` short-circuits it).
    """
    charts = [_normalize_chart(chart, model) for chart in spec.charts]
    return spec.model_copy(update={"charts": charts})
