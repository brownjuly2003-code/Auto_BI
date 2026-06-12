"""form_data templates per viz_type + position_json grid generator.

form_data is UNDOCUMENTED Superset internals (the phase risk): these templates
target the pinned 4.1 and are verified by contract tests on the live stand
("create -> GET -> assert", tests/test_superset_contract.py). LLM never touches
this module's output (invariant 1).

Datasets here are virtual (our validated SQL), so charts re-aggregate already
grouped rows: SUM over one row per group is the identity, MAX for big_number's
single row likewise.
"""

from auto_bi.ir.spec import ChartSpec, DashboardSpec, Measure, Viz, measure_alias

VIZ_TYPE = {
    Viz.BIG_NUMBER: "big_number_total",
    Viz.LINE: "echarts_timeseries_line",
    Viz.BAR: "echarts_timeseries_bar",
}

ROW_HEIGHT_UNITS = 12  # layout_hint.h (1..12) -> superset grid height units


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


def build_form_data(chart: ChartSpec, dataset_id: int) -> dict:
    """Superset chart params for the pinned 4.1, on top of a virtual dataset."""
    datasource = f"{dataset_id}__table"
    metrics = [_adhoc_metric(m, chart.id, i) for i, m in enumerate(chart.query.measures)]
    base = {
        "datasource": datasource,
        "viz_type": VIZ_TYPE[chart.viz],
        "row_limit": chart.query.limit,
    }

    if chart.viz == Viz.BIG_NUMBER:
        # the dataset is a single already-aggregated row -> MAX is the identity
        metric = _adhoc_metric(chart.query.measures[0], chart.id, 0, agg="MAX")
        return {**base, "metric": metric, "subheader": ""}

    x_axis, *rest_dims = chart.query.dimensions
    return {
        **base,
        "x_axis": x_axis,
        "x_axis_sort_asc": True,
        "metrics": metrics,
        "groupby": rest_dims,
    }


def build_position_json(spec: DashboardSpec, placed: list[tuple[ChartSpec, int]]) -> dict:
    """12-column grid from layout_hints: charts grouped into ROWs by layout_hint.row."""
    position: dict = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
        "HEADER_ID": {"type": "HEADER", "id": "HEADER_ID", "meta": {"text": spec.title}},
        "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": [], "parents": ["ROOT_ID"]},
    }
    by_row: dict[int, list[tuple[ChartSpec, int]]] = {}
    for chart, slice_id in placed:
        by_row.setdefault(chart.layout_hint.row, []).append((chart, slice_id))

    for row_no in sorted(by_row):
        row_key = f"ROW-auto_bi_{row_no}"
        position["GRID_ID"]["children"].append(row_key)
        row_children: list[str] = []
        for chart, slice_id in by_row[row_no]:
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
