"""form_data templates per viz_type + position_json grid generator.

form_data is UNDOCUMENTED Superset internals (the phase risk): these templates
target the pinned 4.1 and are verified by contract tests on the live stand
("create -> GET -> assert", tests/test_superset_contract.py). LLM never touches
this module's output (invariant 1).

Two dataset shapes (D-1 variant A):

* OWN (default, today's path) — the virtual dataset is already aggregated SQL for
  one chart. Metrics re-aggregate with identity SUM/MAX over the pre-computed
  measure alias.
* SOURCE (`from_source=True`) — the shared semantic-grain dataset is raw mart
  rows (plus label joins). Metrics carry the IR aggregation over the raw column
  (`SUM("revenue")`), time grain is `time_grain_sqla` (not toStartOf* in SQL),
  and ratios are a single adhoc `sqlExpression`.
"""

from __future__ import annotations

from auto_bi.ir.spec import (
    ChartSpec,
    DashboardSpec,
    Measure,
    TimeGrain,
    Viz,
    column_alias,
    is_compact_number,
    is_percent_measure,
    is_ratio_measure,
    measure_alias,
)
from auto_bi.semantic.model import Aggregation

# d3 format for large additive aggregates: 3 significant figures, SI-abbreviated and
# trailing-zero-trimmed (236149963687 -> "236G", 114971033 -> "115M"). Superset renders
# d3 in the en locale, so the suffix is SI (G/M/k), not "млрд" — still scaled, never the raw
# 12-digit number that overflows a big_number tile / collides on an axis (dashboard-craft §4).
_COMPACT_D3 = ".3~s"
# d3 percent format for ratio transforms (pop_pct, share): a 0..1 ratio renders as "50.0%"
# (d3 `%` multiplies by 100 and appends the sign). Display only — the SQL value stays a ratio.
_PERCENT_D3 = ".1%"

# Superset 4.1 time_grain_sqla ISO-duration tokens (granularity control on a temporal column).
_TIME_GRAIN_SQLA: dict[TimeGrain, str] = {
    TimeGrain.DAY: "P1D",
    TimeGrain.WEEK: "P1W",
    TimeGrain.MONTH: "P1M",
    TimeGrain.QUARTER: "P3M",
    TimeGrain.YEAR: "P1Y",
}

_AGG_SQL = {
    Aggregation.SUM: "SUM",
    Aggregation.AVG: "AVG",
    Aggregation.MIN: "MIN",
    Aggregation.MAX: "MAX",
    Aggregation.COUNT: "COUNT",
    Aggregation.COUNT_DISTINCT: "COUNT",
}


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


def _quote_ident(name: str) -> str:
    """Double-quote a SQL identifier, escaping embedded quotes (form_data is un-guarded SQL)."""
    return '"' + name.replace('"', '""') + '"'


def _agg_sql_expr(measure: Measure, *, column: str) -> str:
    """One IR aggregate over a raw column (SOURCE path), including COUNT DISTINCT."""
    quoted = _quote_ident(column)
    if measure.agg is Aggregation.COUNT_DISTINCT:
        return f"COUNT(DISTINCT {quoted})"
    return f"{_AGG_SQL[measure.agg]}({quoted})"


def _adhoc_metric(
    measure: Measure,
    chart_id: str,
    index: int,
    agg: str = "SUM",
    *,
    label: str | None = None,
    from_source: bool = False,
) -> dict:
    """Adhoc SQL metric for form_data.

    OWN: identity re-aggregation over the pre-computed measure alias (`SUM("sum_revenue")`
    / `MAX(...)` for a single-row KPI). SOURCE: real IR aggregation over the mart column
    (`SUM("revenue")`); a ratio becomes one `sqlExpression` over both sides.
    """
    display = label or measure_alias(measure)
    if from_source:
        if is_ratio_measure(measure):
            assert measure.denominator is not None
            num = _agg_sql_expr(measure, column=column_alias(measure.column))
            den = _agg_sql_expr(
                measure.denominator, column=column_alias(measure.denominator.column)
            )
            # floating division with zero-safe denominator — mirrors SQL_GEN _safe_div intent
            sql = f"({num}) / NULLIF(({den}), 0)"
        else:
            sql = _agg_sql_expr(measure, column=column_alias(measure.column))
    else:
        # alias is LLM-controlled (measure.label) -> escape it as a quoted identifier the
        # same way SQL_GEN does (double the quote), so it cannot break out of SUM("...")
        # and inject SQL. form_data is the second, un-guarded SQL path Superset executes.
        quoted_alias = measure_alias(measure).replace('"', '""')
        sql = f'{agg}("{quoted_alias}")'
    return {
        "expressionType": "SQL",
        "sqlExpression": sql,
        # `label` is the legend / tooltip / result-column name: a human measure name ("Выручка")
        # when the caller resolves one from the model, else the bare alias ("sum_revenue"). The
        # SQL still addresses the dataset by column/alias, so the display name and the column are
        # decoupled — autospec leaves measure.label empty (alias) yet the chart still reads human.
        "label": display,
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


def _base_form_data(chart: ChartSpec, dataset_id: int) -> dict:
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": VIZ_TYPE[chart.viz],
        "row_limit": chart.query.limit,
    }


def _apply_time_grain(form_data: dict, chart: ChartSpec, *, from_source: bool) -> None:
    """SOURCE only: bucket the temporal axis via Superset's time_grain_sqla (not toStartOf*)."""
    if not from_source:
        return
    grain = chart.query.time_grain
    if grain is None or grain is TimeGrain.DAY:
        return
    form_data["time_grain_sqla"] = _TIME_GRAIN_SQLA[grain]


def _apply_series_limit(form_data: dict, chart: ChartSpec, *, from_source: bool) -> None:
    """SOURCE only: top-N lives in form_data (no LIMIT in the shared source SQL)."""
    if not from_source:
        return
    q = chart.query
    if _ordering_measure(q.measures, q.order_by) is not None:
        form_data["series_limit"] = q.limit


def _fd_raw(chart: ChartSpec, base: dict) -> dict:
    # X-5 escape hatch: the dataset is the operator's raw SELECT (not an aggregated IR
    # query), so the table shows its result columns verbatim — RAW query mode, no
    # groupby/metrics. `dimensions`, if given, name the columns to display; empty => Superset
    # renders every column of the result. Validation pins raw_sql to viz=TABLE.
    fd = {**base, "query_mode": "raw"}
    if chart.query.dimensions:
        fd["all_columns"] = [column_alias(c) for c in chart.query.dimensions]
    return fd


def _fd_big_number(
    chart: ChartSpec,
    base: dict,
    *,
    label_of,
    kpi_scale: tuple[float, str, float] | None,
    from_source: bool,
) -> dict:
    q = chart.query
    m0 = q.measures[0]
    # OWN single-row dataset: MAX is the identity. SOURCE multi-row grain: use the IR agg
    # (SUM/AVG/…) so a period/city filter re-aggregates instead of taking a MAX of rows.
    own_agg = "MAX"
    metric = _adhoc_metric(
        m0, chart.id, 0, agg=own_agg, label=label_of(m0), from_source=from_source
    )
    # every KPI tile shares one shape: value line + unit line, equal font proportions
    # (pinned to Superset's defaults so a default drift can't desynchronize the row).
    # Without the pin the tiles also diverge live: a subheader-less tile lets the
    # value auto-grow into the freed height. Centering is dashboard CSS (adapter).
    base = {**base, "header_font_size": 0.4, "subheader_font_size": 0.15}
    fmt = _chart_format(q.measures)
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


def _fd_pie(chart: ChartSpec, base: dict, metrics: list[dict], fmt: str) -> dict:
    # shape-validated to exactly one dimension + one measure
    fd = {
        **base,
        "groupby": [column_alias(c) for c in chart.query.dimensions],
        "metric": metrics[0],
        "sort_by_metric": True,
    }
    if fmt:
        fd["number_format"] = fmt
    return fd


def _fd_table(chart: ChartSpec, base: dict, metrics: list[dict], *, label_of) -> dict:
    q = chart.query
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
        (label_of(m) or measure_alias(m)): {"d3NumberFormat": _measure_d3(m)}
        for m in q.measures
        if _measure_d3(m)
    }
    if column_config:
        fd["column_config"] = column_config
    return fd


def _fd_pivot(chart: ChartSpec, base: dict, metrics: list[dict]) -> dict:
    # OWN: cells re-aggregate with Sum over a one-row-per-cell grain (identity).
    # SOURCE: Sum over raw rows is the real aggregate (IR measures are SUM-family for pivots
    # in practice; Superset's pivot aggregateFunction is chart-level, not per-metric).
    q = chart.query
    return {
        **base,
        "groupbyRows": [column_alias(c) for c in q.rows],
        "groupbyColumns": [column_alias(c) for c in q.columns],
        "metrics": metrics,
        "metricsLayout": "COLUMNS",
        "aggregateFunction": "Sum",
        "rowOrder": "key_a_to_z",
    }


def _fd_heatmap(
    chart: ChartSpec,
    base: dict,
    metrics: list[dict],
    *,
    heatmap_y_pad: int | None,
) -> dict:
    q = chart.query
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


def _fd_timeseries(
    chart: ChartSpec,
    base: dict,
    metrics: list[dict],
    *,
    label_of,
    horizontal: bool,
    axis_scale: tuple[float, str, float] | None,
    time_column: str | None,
    fmt: str,
    from_source: bool,
) -> dict:
    # echarts timeseries family: line, bar, stacked_bar, area
    q = chart.query
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
    _apply_time_grain(form_data, chart, from_source=from_source)
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
            form_data["x_axis_sort"] = label_of(measure) or measure_alias(measure)
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
    _apply_series_limit(form_data, chart, from_source=from_source)
    return form_data


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
    from_source: bool = False,
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

    `time_column` is the alias of the chart's temporal x-axis (or, on a SOURCE KPI, the mart's
    TIME column so a dashboard native time filter's time_range binds). A dashboard native time
    filter delivers a time_range, but Superset's ECharts query names no time column, so without
    granularity_sqla the range binds to nothing and the preset period (B5) silently fails to
    re-scope the chart. None => no time binding.

    `heatmap_y_pad` (heatmap only) zero-pads the y-axis dimension to this width via an adhoc
    SQL column: heatmap_v2 renders a numeric 0 as `<NULL>` on the axis (upstream #33105, the
    js preparer treats 0/false as missing) and sorts numeric keys alphabetically (#31318) —
    padded strings ("00".."23") fix both the label and the order. The adapter computes the
    width from the model for small-cardinality numeric dimensions (ordinal cohort periods);
    None => the plain column (id-like axes keep their natural labels).

    `from_source` (D-1): when True the dataset is the shared semantic-grain source (raw mart
    rows). Metrics use the IR aggregation over the raw column; KPI no longer assumes a single
    row; time grain is `time_grain_sqla`; top-N is `series_limit`. When False (default) the
    OWN per-chart aggregated dataset path is unchanged.
    """
    q = chart.query
    labels = metric_labels or {}

    def _label(m: Measure) -> str | None:
        return labels.get(measure_alias(m))

    base = _base_form_data(chart, dataset_id)

    if q.raw_sql is not None:
        return _fd_raw(chart, base)

    metrics = [
        _adhoc_metric(m, chart.id, i, label=_label(m), from_source=from_source)
        for i, m in enumerate(q.measures)
    ]
    fmt = _chart_format(q.measures)

    if chart.viz == Viz.BIG_NUMBER:
        fd = _fd_big_number(
            chart, base, label_of=_label, kpi_scale=kpi_scale, from_source=from_source
        )
        if time_column:
            # SOURCE KPI: bind the mart's temporal column so a dashboard time filter re-scopes
            # the multi-row aggregate (OWN KPI has no time column on its single-row dataset).
            fd["granularity_sqla"] = time_column
        return fd

    if chart.viz == Viz.PIE:
        fd = _fd_pie(chart, base, metrics, fmt)
        _apply_series_limit(fd, chart, from_source=from_source)
        return fd

    if chart.viz == Viz.TABLE:
        fd = _fd_table(chart, base, metrics, label_of=_label)
        _apply_series_limit(fd, chart, from_source=from_source)
        return fd

    if chart.viz == Viz.PIVOT:
        return _fd_pivot(chart, base, metrics)

    if chart.viz == Viz.HEATMAP:
        return _fd_heatmap(chart, base, metrics, heatmap_y_pad=heatmap_y_pad)

    return _fd_timeseries(
        chart,
        base,
        metrics,
        label_of=_label,
        horizontal=horizontal,
        axis_scale=axis_scale,
        time_column=time_column,
        fmt=fmt,
        from_source=from_source,
    )


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
