"""form_data templates per viz_type + position_json grid generator.

form_data is UNDOCUMENTED Superset internals (the phase risk): these templates
target the pinned 4.1 and are verified by contract tests on the live stand
("create -> GET -> assert", tests/test_superset_contract.py). LLM never touches
this module's output (invariant 1).

Datasets here are virtual (our validated SQL), so charts re-aggregate already
grouped rows: SUM over one row per group is the identity, MAX for big_number's
single row likewise.
"""

from auto_bi.ir.spec import (
    ChartSpec,
    DashboardSpec,
    Measure,
    Viz,
    column_alias,
    is_compact_number,
    is_percent_measure,
    measure_alias,
)

# d3 format for large additive aggregates: 3 significant figures, SI-abbreviated and
# trailing-zero-trimmed (236149963687 -> "236G", 114971033 -> "115M"). Superset renders
# d3 in the en locale, so the suffix is SI (G/M/k), not "млрд" — still scaled, never the raw
# 12-digit number that overflows a big_number tile / collides on an axis (dashboard-craft §4).
_COMPACT_D3 = ".3~s"
# d3 percent format for ratio transforms (pop_pct, share): a 0..1 ratio renders as "50.0%"
# (d3 `%` multiplies by 100 and appends the sign). Display only — the SQL value stays a ratio.
_PERCENT_D3 = ".1%"


def _measure_d3(measure: Measure) -> str:
    """d3 number format for one measure: percent for ratio transforms, compact for large
    additive aggregates, else "" (leave Superset's default — averages/extrema, contract specs)."""
    if is_percent_measure(measure):
        return _PERCENT_D3
    return _COMPACT_D3 if is_compact_number(measure) else ""


VIZ_TYPE = {
    Viz.BIG_NUMBER: "big_number_total",
    Viz.LINE: "echarts_timeseries_line",
    Viz.BAR: "echarts_timeseries_bar",
    Viz.STACKED_BAR: "echarts_timeseries_bar",
    Viz.AREA: "echarts_area",
    Viz.PIE: "pie",
    Viz.TABLE: "table",
    Viz.PIVOT: "pivot_table_v2",
    Viz.HEATMAP: "heatmap_v2",
}

ROW_HEIGHT_UNITS = 12  # layout_hint.h (1..12) -> superset grid height units
GRID_WIDTH = 12  # superset dashboard grid is 12 columns wide


def _adhoc_metric(measure: Measure, chart_id: str, index: int, agg: str = "SUM") -> dict:
    alias = measure_alias(measure)
    # alias is LLM-controlled (measure.label) -> escape it as a quoted identifier the
    # same way SQL_GEN does (double the quote), so it cannot break out of SUM("...")
    # and inject SQL. form_data is the second, un-guarded SQL path Superset executes.
    quoted_alias = alias.replace('"', '""')
    return {
        "expressionType": "SQL",
        "sqlExpression": f'{agg}("{quoted_alias}")',
        "label": alias,
        "optionName": f"metric_auto_bi_{chart_id}_{index}",
    }


def _chart_format(measures: list[Measure]) -> str:
    """Chart-level value format from the primary (first) measure: percent for a ratio
    transform, compact for a large aggregate, else Superset's default ("").

    The auto-overview charts are single-measure; for an LLM multi-measure chart the primary
    measure sets the single axis format — the common, sensible default (a table mixing
    families instead formats per-column via column_config below).
    """
    return _measure_d3(measures[0]) if measures else ""


def build_form_data(chart: ChartSpec, dataset_id: int) -> dict:
    """Superset chart params for the pinned 4.1, on top of a virtual dataset."""
    q = chart.query
    metrics = [_adhoc_metric(m, chart.id, i) for i, m in enumerate(q.measures)]
    fmt = _chart_format(q.measures)
    base = {
        "datasource": f"{dataset_id}__table",
        "viz_type": VIZ_TYPE[chart.viz],
        "row_limit": q.limit,
    }

    if chart.viz == Viz.BIG_NUMBER:
        # the dataset is a single already-aggregated row -> MAX is the identity
        metric = _adhoc_metric(q.measures[0], chart.id, 0, agg="MAX")
        fd = {**base, "metric": metric, "subheader": ""}
        if fmt:
            fd["y_axis_format"] = fmt
        return fd

    if chart.viz == Viz.PIE:
        # shape-validated to exactly one dimension + one measure
        fd = {
            **base,
            "groupby": [column_alias(c) for c in q.dimensions],
            "metric": metrics[0],
            "sort_by_metric": True,
        }
        if fmt:
            fd["number_format"] = fmt
        return fd

    if chart.viz == Viz.TABLE:
        fd = {
            **base,
            "query_mode": "aggregate",
            "groupby": [column_alias(c) for c in q.group_columns()],
            "metrics": metrics,
        }
        # per-metric format (a table can mix a big sum, a small average, and a percent share)
        column_config = {
            measure_alias(m): {"d3NumberFormat": _measure_d3(m)}
            for m in q.measures
            if _measure_d3(m)
        }
        if column_config:
            fd["column_config"] = column_config
        return fd

    if chart.viz == Viz.PIVOT:
        # cells re-aggregate with Sum: the dataset grain is exactly rows x columns,
        # so each cell holds one source row and Sum is the identity
        return {
            **base,
            "groupbyRows": [column_alias(c) for c in q.rows],
            "groupbyColumns": [column_alias(c) for c in q.columns],
            "metrics": metrics,
            "metricsLayout": "COLUMNS",
            "aggregateFunction": "Sum",
            "rowOrder": "key_a_to_z",
        }

    if chart.viz == Viz.HEATMAP:
        x_axis, y_axis = q.dimensions  # shape-validated to exactly two
        return {
            **base,
            "x_axis": column_alias(x_axis),
            "groupby": column_alias(y_axis),
            "metric": metrics[0],
            "sort_x_axis": "alpha_asc",
            "sort_y_axis": "alpha_asc",
            "normalize_across": "heatmap",
            "legend_type": "continuous",
        }

    # echarts timeseries family: line, bar, stacked_bar, area
    x_axis, *rest_dims = q.dimensions
    breakdown: dict[str, None] = {}  # series + extra dimensions, deduped, order kept
    for col in (*q.series, *rest_dims):
        breakdown.setdefault(col, None)
    form_data = {
        **base,
        "x_axis": column_alias(x_axis),
        "x_axis_sort_asc": True,
        "metrics": metrics,
        "groupby": [column_alias(c) for c in breakdown],
    }
    if fmt:
        form_data["y_axis_format"] = fmt
    if chart.viz in (Viz.BAR, Viz.STACKED_BAR):
        # a numeric dimension (store_id) otherwise lands on a continuous value
        # axis: thin bars at their numeric positions instead of categories
        form_data["xAxisForceCategorical"] = True
        ordering_measure = _ordering_measure(q.measures, q.order_by)
        if ordering_measure is not None and len(metrics) == 1 and not breakdown:
            # the spec asked for top-N by this measure; superset honors the sort
            # control only for single-metric charts without a series breakdown
            form_data["x_axis_sort"] = measure_alias(ordering_measure[0])
            form_data["x_axis_sort_asc"] = ordering_measure[1] == "asc"
    if chart.viz == Viz.STACKED_BAR or (chart.viz == Viz.AREA and q.series):
        form_data["stack"] = "Stack"  # echarts "Stacked Style" select: None/Stack/Stream
    return form_data


def _ordering_measure(measures: list[Measure], order_by: list) -> tuple[Measure, str] | None:
    """The measure the spec's first ORDER BY refers to (by column or alias), if any.

    Sorting categories by the measure is only correct when the spec itself orders
    by it (top-N intent); ordering by the x dimension (e.g. dates) must keep the
    ascending x sort, or a bar-over-time chart would shuffle its chronology.
    """
    if not order_by:
        return None
    head = order_by[0]
    for m in measures:
        if head.by in (m.column, measure_alias(m)):
            return m, head.dir
    return None


def _pack_rows(
    placed: list[tuple[ChartSpec, int]],
) -> list[list[tuple[ChartSpec, int]]]:
    """Pack charts into physical rows of at most 12 columns.

    layout_hint.row is an ordering/grouping hint (a new hint-row starts a new physical
    row); within a hint-row, charts wrap to a new physical row once their widths would
    exceed the 12-column grid, so wide charts never overflow. (layout_hint.w is already
    validated to 1..12 by the IR, so no clamping is needed here.)
    """
    ordered = sorted(enumerate(placed), key=lambda item: (item[1][0].layout_hint.row, item[0]))
    rows: list[list[tuple[ChartSpec, int]]] = []
    current: list[tuple[ChartSpec, int]] = []
    used = 0
    last_hint_row: int | None = None
    for _, (chart, slice_id) in ordered:
        width = chart.layout_hint.w
        hint_row = chart.layout_hint.row
        starts_group = last_hint_row is not None and hint_row != last_hint_row
        if current and (starts_group or used + width > GRID_WIDTH):
            rows.append(current)
            current, used = [], 0
        current.append((chart, slice_id))
        used += width
        last_hint_row = hint_row
    if current:
        rows.append(current)
    return rows


def build_position_json(spec: DashboardSpec, placed: list[tuple[ChartSpec, int]]) -> dict:
    """12-column grid from layout_hints: charts packed into ROWs (overflow wraps)."""
    position: dict = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
        "HEADER_ID": {"type": "HEADER", "id": "HEADER_ID", "meta": {"text": spec.title}},
        "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": [], "parents": ["ROOT_ID"]},
    }
    for row_index, row_charts in enumerate(_pack_rows(placed)):
        row_key = f"ROW-auto_bi_{row_index}"
        position["GRID_ID"]["children"].append(row_key)
        row_children: list[str] = []
        for chart, slice_id in row_charts:
            chart_key = f"CHART-auto_bi_{chart.id}"
            row_children.append(chart_key)
            position[chart_key] = {
                "type": "CHART",
                "id": chart_key,
                "children": [],
                "parents": ["ROOT_ID", "GRID_ID", row_key],
                "meta": {
                    "chartId": slice_id,
                    "sliceName": chart.title,
                    "width": chart.layout_hint.w,
                    "height": chart.layout_hint.h * ROW_HEIGHT_UNITS,
                },
            }
        position[row_key] = {
            "type": "ROW",
            "id": row_key,
            "children": row_children,
            "parents": ["ROOT_ID", "GRID_ID"],
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
        }
    return position
