"""Effective filters per chart (ARCHITECTURE §3.3, audit P1-2).

A chart's real WHERE at dashboard-refresh time is not just `query.filters`. A dashboard-level
control (`spec.filters`) narrows the charts it scopes to, so rules that read only
`query.filters` report a full scan on charts that actually open filtered.

Both adapters scope a control the same way (`adapters/superset/native_filters.py`, mirrored by
`adapters/datalens/adapter.py`), and this module encodes the same two conditions:

* the column must be in the chart's grain (`group_columns`) — each chart is its own
  pre-aggregated dataset, and one that didn't select the column has nothing to filter on;
* the control must carry a non-empty `default` — an empty one compiles to a neutral mask, so
  the chart genuinely opens unfiltered.

Counting every `spec.filters` entry regardless would trade the current false positive for a
false negative, which is worse: the advisor would stay silent on a real full scan.

The control's kind comes from the column's semantic role, not `DashboardFilter.type` — the LLM
does not reliably override that field's schema default (same reasoning as `native_filters`).
"""

from __future__ import annotations

from auto_bi.ir.spec import ChartSpec, DashboardSpec, FilterOp, QueryFilter, column_alias
from auto_bi.semantic.model import ColumnRole, SemanticModel


def _is_temporal(column: str, model: SemanticModel) -> bool:
    table_name, _, col = column.rpartition(".")
    table = model.table(table_name)
    c = table.column(col) if table else None
    return c is not None and c.role == ColumnRole.TIME


def chart_column_ref(filter_column: str, chart: ChartSpec) -> str | None:
    """The chart's OWN reference to a dashboard-filtered column, or None if out of scope.

    Doubles as the scope test: a control reaches a chart iff the chart's grain exposes the
    column (matched on the bare alias, as the adapters do). Returning the chart's own ref
    matters for SQL — on a multi-table spec the filter's declared ref may name a table this
    chart never queries, and `WHERE dm.sales_daily.date` over `FROM dm.other` is nonsense.
    """
    alias = column_alias(filter_column)
    for col in chart.query.group_columns():
        if column_alias(col) == alias:
            return col
    return None


def effective_filters(
    chart: ChartSpec,
    spec: DashboardSpec | None,
    model: SemanticModel,
) -> list[QueryFilter]:
    """`query.filters` plus the dashboard controls that actually narrow THIS chart.

    `spec=None` (metadata-only callers reviewing a lone chart) returns `query.filters`
    unchanged — the pre-P1-2 behaviour.
    """
    filters = list(chart.query.filters)
    if spec is None:
        return filters

    constrained = {column_alias(f.column) for f in filters}
    for control in spec.filters:
        alias = column_alias(control.column)
        if alias in constrained:
            continue  # already a SQL WHERE on that column; the control only re-scopes it
        default = control.default.strip()
        if not default:
            continue  # neutral mask -> the chart opens unfiltered after all
        ref = chart_column_ref(control.column, chart)
        if ref is None:
            continue  # out of the control's scope -> this chart ignores it
        if _is_temporal(control.column, model):
            # a period phrase ("last 12 months") bounds the scan from below, exactly like the
            # filter autospec bakes for the same window (P1-1)
            filters.append(QueryFilter(column=ref, op=FilterOp.GTE, value=default))
        else:
            # a select control presets a single value; adapters compile it to `alias IN [value]`
            filters.append(QueryFilter(column=ref, op=FilterOp.IN, value=[default]))
        constrained.add(alias)
    return filters
