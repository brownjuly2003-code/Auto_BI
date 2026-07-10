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
    Viz.HISTOGRAM: "echarts_timeseries_bar",  # vertical bars over the SQL-computed buckets
}

ROW_HEIGHT_UNITS = 12  # layout_hint.h (1..12) -> superset grid height units
GRID_WIDTH = 12  # superset dashboard grid is 12 columns wide


def _adhoc_metric(
    measure: Measure, chart_id: str, index: int, agg: str = "SUM", *, label: str | None = None
) -> dict:
    alias = measure_alias(measure)
    # alias is LLM-controlled (measure.label) -> escape it as a quoted identifier the
    # same way SQL_GEN does (double the quote), so it cannot break out of SUM("...")
    # and inject SQL. form_data is the second, un-guarded SQL path Superset executes.
    quoted_alias = alias.replace('"', '""')
    return {
        "expressionType": "SQL",
        "sqlExpression": f'{agg}("{quoted_alias}")',
        # `label` is the legend / tooltip / result-column name: a human measure name ("Выручка")
        # when the caller resolves one from the model, else the bare alias ("sum_revenue"). The
        # SQL still addresses the dataset by `alias`, so the display name and the column are
        # decoupled — autospec leaves measure.label empty (alias) yet the chart still reads human.
        "label": label or alias,
        "optionName": f"metric_auto_bi_{chart_id}_{index}",
    }


# KPI scale dictionary (dashboard-craft §5 "Числа"): a large ruble aggregate must read as
# "236 млрд" (a scaled headline + the unit on a smaller, separate line), never "236G" (d3's
# SI giga suffix, unreadable for money) nor the raw 12-digit number. The engine cannot render
# Russian magnitude words (d3 SI is hard-coded k/M/G/T), so we scale the metric ourselves and
# put the RU unit in the big_number *subheader* (smaller font, its own line — the unit is not
# glued to the figure at the same size). Tiers mirror agent.insights._compact.
_RU_KPI_SCALE: list[tuple[float, str]] = [
    (1e12, "трлн"),
    (1e9, "млрд"),
    (1e6, "млн"),
    (1e3, "тыс"),
]


def ru_kpi_scale(value: float) -> tuple[float, str]:
    """(divisor, unit word) for a big-number headline: 236e9 -> (1e9, "млрд"). Below 1e3 the
    figure is small enough to show in full -> (1, "") (no scaling, no unit line)."""
    a = abs(value)
    for divisor, word in _RU_KPI_SCALE:
        if a >= divisor:
            return divisor, word
    return 1.0, ""


def _chart_format(measures: list[Measure]) -> str:
    """Chart-level value format from the primary (first) measure: percent for a ratio
    transform, compact for a large aggregate, else Superset's default ("").

    The auto-overview charts are single-measure; for an LLM multi-measure chart the primary
    measure sets the single axis format — the common, sensible default (a table mixing
    families instead formats per-column via column_config below).
    """
    return _measure_d3(measures[0]) if measures else ""


def build_form_data(
    chart: ChartSpec,
    dataset_id: int,
    *,
    horizontal: bool = False,
    kpi_scale: tuple[float, str, float] | None = None,
    axis_scale: tuple[float, str, float] | None = None,
    metric_labels: dict[str, str] | None = None,
    time_column: str | None = None,
    heatmap_y_pad: int | None = None,
) -> dict:
    """Superset chart params for the pinned 4.1, on top of a virtual dataset.

    `horizontal` orients a categorical bar chart horizontally (see
    `agent.normalize.is_horizontal_bar`); the adapter computes it from the model and the
    flag is ignored for non-bar viz.

    `kpi_scale` (divisor, unit word, scaled magnitude) applies only to big_number: the adapter
    measures the KPI's magnitude and passes e.g. (1e9, "млрд", 236.1) so the headline reads
    "236" with "млрд" on the subheader line, instead of the d3 SI "236G". In the 1–10 band the
    headline keeps one decimal ("1,5 млрд", not "2 млрд" — L-1). None => the old raw/compact
    format.

    `axis_scale` (divisor, unit line, scaled magnitude) is the cartesian-axis analog of
    `kpi_scale` for line/bar/area: d3's SI is hard-coded to k/M/G/T, so to read "15 млрд ₽"
    instead of "15G" the metric is scaled and the RU unit goes on the value-axis TITLE.
    None => the d3 SI (~s) axis format.

    `metric_labels` maps a measure alias -> human name ("sum_revenue" -> "Выручка") so legends,
    tooltips and table columns read human instead of the raw alias. The adapter resolves it from
    the model (autospec leaves measure.label empty). Absent => the alias is the display name.

    `time_column` is the alias of the chart's temporal x-axis, set only for a timeseries chart
    over a TIME column. A dashboard native time filter delivers a time_range, but Superset's
    ECharts query names no time column, so without granularity_sqla the range binds to nothing
    and the preset period (B5) silently fails to re-scope the chart. None => no time binding.

    `heatmap_y_pad` (heatmap only) zero-pads the y-axis dimension to this width via an adhoc
    SQL column: heatmap_v2 renders a numeric 0 as `<NULL>` on the axis (upstream #33105, the
    js preparer treats 0/false as missing) and sorts numeric keys alphabetically (#31318) —
    padded strings ("00".."23") fix both the label and the order. The adapter computes the
    width from the model for small-cardinality numeric dimensions (ordinal cohort periods);
    None => the plain column (id-like axes keep their natural labels).
    """
    q = chart.query
    labels = metric_labels or {}

    def _label(m: Measure) -> str | None:
        return labels.get(measure_alias(m))

    metrics = [_adhoc_metric(m, chart.id, i, label=_label(m)) for i, m in enumerate(q.measures)]
    fmt = _chart_format(q.measures)
    base = {
        "datasource": f"{dataset_id}__table",
        "viz_type": VIZ_TYPE[chart.viz],
        "row_limit": q.limit,
    }

    if chart.viz == Viz.BIG_NUMBER:
        # the dataset is a single already-aggregated row -> MAX is the identity
        metric = _adhoc_metric(q.measures[0], chart.id, 0, agg="MAX", label=_label(q.measures[0]))
        # every KPI tile shares one shape: value line + unit line, equal font proportions
        # (pinned to Superset's defaults so a default drift can't desynchronize the row).
        # Without the pin the tiles also diverge live: a subheader-less tile lets the
        # value auto-grow into the freed height. Centering is dashboard CSS (adapter).
        base = {**base, "header_font_size": 0.4, "subheader_font_size": 0.15}
        if kpi_scale is not None and kpi_scale[0] > 1:
            # scale the headline into RU magnitude units: divide the metric and round to a whole
            # number ("236"), with the unit ("млрд") on the smaller subheader line. Grouped
            # thousands (",") stay readable if the scaled figure is itself in the thousands.
            # In the 1–10 band whole-number rounding would lose up to a third of the figure
            # (1,5 млрд -> "2 млрд"), so the headline keeps one decimal there (L-1).
            divisor, unit, scaled = kpi_scale
            metric = {**metric, "sqlExpression": f"({metric['sqlExpression']}) / {divisor:.0f}"}
            headline_fmt = ",.1f" if scaled < 10 else ",.0f"
            return {**base, "metric": metric, "subheader": unit, "y_axis_format": headline_fmt}
        if fmt == _PERCENT_D3:
            # a percent KPI mirrors the unit tiles: plain value + "%" on the subheader line
            # ("1.5" over "%"), NOT "1.5%" glued into the headline — the lone long string
            # shrank the font and, with no subheader line, the tile centered differently
            # from its neighbours (the "одинаковый формат" fix). d3 "%" implies ×100, so
            # the metric is scaled in SQL; ".1~f" keeps the .1% precision, trim drops a
            # trailing zero ("34", not "34.0" — the L-1 band rule without a probe).
            metric = {**metric, "sqlExpression": f"({metric['sqlExpression']}) * 100"}
            return {**base, "metric": metric, "subheader": "%", "y_axis_format": ".1~f"}
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
        # per-metric format (a table can mix a big sum, a small average, and a percent share),
        # keyed by the column's DISPLAY name (the metric label) so the format lands on the right
        # column when the legend is humanized
        column_config = {
            (_label(m) or measure_alias(m)): {"d3NumberFormat": _measure_d3(m)}
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
        y_alias = column_alias(y_axis)
        groupby: str | dict = y_alias
        if heatmap_y_pad is not None:
            # zero-pad an ordinal numeric y (cohort periods 0..N): value 0 otherwise renders
            # as <NULL> on the axis and alpha sort shuffles numbers (see the docstring).
            # ANSI LPAD/CAST renders on both ClickHouse and Greenplum through the virtual
            # dataset; live-verified on the pinned 4.1 (probe chart, stand 2026-07-11).
            groupby = {
                "expressionType": "SQL",
                "sqlExpression": f"LPAD(CAST(\"{y_alias}\" AS VARCHAR), {heatmap_y_pad}, '0')",
                "label": y_alias,
            }
        return {
            **base,
            "x_axis": column_alias(x_axis),
            "groupby": groupby,
            "metric": metrics[0],
            "sort_x_axis": "alpha_asc",
            "sort_y_axis": "alpha_asc",
            "normalize_across": "heatmap",
            "legend_type": "continuous",
        }

    # echarts timeseries family: line, bar, stacked_bar, area
    x_axis, *rest_dims = q.dimensions
    # a temporal x (model role=TIME) is the chart's own x column, not just a group column: the
    # adapter passes its alias as time_column AND it is dimensions[0]. Bars over such a column
    # must keep the time axis (see below), so distinguish it from a numeric categorical x.
    x_is_temporal = time_column is not None and time_column == column_alias(x_axis)
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
    if time_column:
        # designate the temporal column so a dashboard native time filter's time_range binds to
        # it (the ECharts query sets no granularity otherwise -> the preset period never applies).
        # Harmless without a filter: no chart-level time_range => no WHERE (see B5, ARCHITECTURE
        # §3.5). Superset must also mark this column is_dttm on the dataset (auto on a fresh Date
        # column); this pairs with that.
        form_data["granularity_sqla"] = time_column
    if x_is_temporal:
        # a temporal x renders on a time axis whose tick labels need an explicit date format;
        # without it Superset's ECharts prints the raw epoch-ms of each bucket (a bar over
        # cohort months showed 1769904000000 instead of "июл 2024"). smart_date is Superset's
        # adaptive date format. A line already defaults to a time axis (this is a no-op label
        # pin there); the fix matters for a bar, which is otherwise forced categorical below.
        form_data["x_axis_time_format"] = "smart_date"
    if fmt:
        form_data["y_axis_format"] = fmt
    if chart.viz in (Viz.BAR, Viz.STACKED_BAR, Viz.HISTOGRAM) and not x_is_temporal:
        # a numeric dimension (store_id) / a histogram's numeric bucket bounds otherwise land on
        # a continuous value axis: thin bars at their numeric positions instead of labeled
        # buckets. A TEMPORAL x is the exception — forcing it categorical makes ECharts print
        # the bucket's raw epoch-ms; it stays on the time axis with x_axis_time_format instead.
        form_data["xAxisForceCategorical"] = True
    if chart.viz in (Viz.BAR, Viz.STACKED_BAR):
        if horizontal:
            # categorical ranking -> horizontal bars so long RU labels get the full row
            # width instead of truncating/rotating on a vertical x-axis (Cleveland/Few)
            form_data["orientation"] = "horizontal"
        ordering_measure = _ordering_measure(q.measures, q.order_by)
        if ordering_measure is not None and len(metrics) == 1 and not breakdown:
            # the spec asked for top-N by this measure; superset honors the sort
            # control only for single-metric charts without a series breakdown
            measure, direction = ordering_measure
            # sort by the metric's DISPLAY label: superset matches x_axis_sort against the
            # metric label, which is humanized ("Выручка") when metric_labels resolves one, so
            # the raw alias ("sum_revenue") would no longer match and the sort would silently
            # fall back to alphabetical order
            form_data["x_axis_sort"] = _label(measure) or measure_alias(measure)
            asc = direction == "asc"
            # a horizontal bar renders category[0] at the BOTTOM (echarts origin), so to place
            # the largest bar at the TOP (dashboard-craft §5 "крупнейший первый") the sort
            # direction is inverted relative to the vertical case
            form_data["x_axis_sort_asc"] = (not asc) if horizontal else asc
    if chart.viz == Viz.STACKED_BAR or (chart.viz == Viz.AREA and q.series):
        form_data["stack"] = "Stack"  # echarts "Stacked Style" select: None/Stack/Stream
    if axis_scale is not None and axis_scale[0] > 1:
        # RU magnitude units on the value axis (the kpi_scale analog): scale the metric and put
        # the RU unit on the value-axis TITLE, since d3's SI axis format only speaks k/M/G/T. The
        # value axis is Y for a vertical chart, X for a horizontal bar.
        divisor, unit, _ = axis_scale
        form_data["metrics"] = [
            {**m, "sqlExpression": f"({m['sqlExpression']}) / {divisor:.0f}"} for m in metrics
        ]
        form_data["y_axis_format"] = ",.1f"
        # y_axis_title is the MEASURE (value) axis in superset's echarts family regardless of
        # orientation — a horizontal bar only flips it visually to the bottom, so the RU unit
        # always goes here (x_axis_title would land on the category axis)
        form_data["y_axis_title"] = unit
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
