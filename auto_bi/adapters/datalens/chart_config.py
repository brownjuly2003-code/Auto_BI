"""IR ChartSpec -> DataLens wizard chart `shared` config (reversal §5.2).

A chart is a US widget-entry created via ``POST /api/charts/v1/charts`` with
``{data: <shared>, template: "datalens", workbookId, name}``: the charts engine wraps the
config with the JS provider stubs (buildGraph/buildHighchartsConfig/...), so this module
only builds ``shared`` — the visualization id + placeholders bound to dataset fields. The
DataLens analogue of Superset's form_data.py.

Live-verified 2026-06-14: a `line` chart built this way rendered end-to-end (date -> X,
revenue -> Y) against real ClickHouse data on the self-hosted stand.

Fields come from the created dataset's result_schema (see dataset.py): each carries the
guid/avatar_id the chart must bind to. The adapter passes them in `fields_by_alias`,
keyed by the bare alias (column_alias / measure_alias) the SQL_GEN subselect emits.
"""

from __future__ import annotations

from auto_bi.ir.spec import ChartSpec, Viz, column_alias, measure_alias

# IR Viz -> DataLens visualization.id (reversal §5.2). bar/stacked_bar -> "column"
# (vertical bars, mirrors Superset echarts_timeseries_bar). heatmap has no cartesian
# Wizard viz -> degrades to pivotTable (native heatmap is Editor/Highcharts-only).
VIZ_ID: dict[Viz, str] = {
    Viz.BIG_NUMBER: "metric",
    Viz.LINE: "line",
    Viz.BAR: "column",
    Viz.STACKED_BAR: "column",
    Viz.AREA: "area",
    Viz.PIE: "pie",
    Viz.TABLE: "flatTable",
    Viz.PIVOT: "pivotTable",
    Viz.HEATMAP: "pivotTable",
}

# viz that degrade from their IR intent (callers should surface this in the build log,
# invariant §3.4): heatmap -> pivot table because Wizard has no cartesian heatmap.
DEGRADED: dict[Viz, str] = {
    Viz.HEATMAP: "DataLens Wizard has no cartesian heatmap; rendered as a pivot table",
}

_COLORS_CONFIG = {
    "gradientMode": "2-point",
    "gradientPalette": "default",
    "polygonBorders": "show",
    "reversed": False,
    "thresholdsMode": "auto",
}
_EXTRA_SETTINGS = {"titleMode": "hide", "title": "", "legendMode": "show"}


def _field_item(field: dict, field_id: str, dataset_id: str, dataset_name: str) -> dict:
    """One placeholder item: a dataset field bound into a chart section.

    Shape reversed from the demo Wizard charts (reversal §5.2); the chart binds to the
    dataset by `guid` + `avatar_id`, both produced by build_dataset_payload.
    """
    return {
        "type": field["type"],
        "calc_mode": "direct",
        "data_type": field["data_type"],
        "initial_data_type": field["data_type"],
        "cast": field["cast"],
        "aggregation": field["aggregation"],
        "source": field["source"],
        "guid": field["guid"],
        "title": field["title"],
        "avatar_id": field["avatar_id"],
        "datasetId": dataset_id,
        "datasetName": dataset_name,
        "id": field_id,
        "managed_by": "user",
        "valid": True,
        "hidden": False,
        "formula": "",
        "description": "",
        "autoaggregated": False,
        "virtual": False,
        "lock_aggregation": False,
        "aggregation_locked": False,
        "has_auto_aggregation": False,
        "guid_formula": "",
        "default_value": None,
        "value_constraint": None,
    }


def build_chart_shared(
    chart: ChartSpec,
    dataset_id: str,
    dataset_name: str,
    fields_by_alias: dict[str, dict],
) -> dict:
    """IR chart -> DataLens `shared` config. `fields_by_alias` maps a bare alias to its
    dataset result_schema descriptor (guid/avatar_id/data_type/type/aggregation/cast)."""
    q = chart.query
    viz_id = VIZ_ID[chart.viz]

    # stable per-field id within the chart: the same field keeps one id across placeholders
    # (Wizard convention). Number dimensions and measures independently.
    ids: dict[str, str] = {}
    d_n = m_n = 0
    for alias, field in fields_by_alias.items():
        if field["type"] == "MEASURE":
            m_n += 1
            ids[alias] = f"measure-{m_n}"
        else:
            d_n += 1
            ids[alias] = f"dimension-{d_n}"

    used: dict[str, None] = {}  # aliases referenced by this chart, order-preserving

    def item(alias: str) -> dict:
        used.setdefault(alias, None)
        return _field_item(fields_by_alias[alias], ids[alias], dataset_id, dataset_name)

    def dims(refs: list[str]) -> list[dict]:
        return [item(column_alias(r)) for r in refs]

    def measures() -> list[dict]:
        return [item(measure_alias(m)) for m in q.measures]

    colors: list[dict] = []
    sort: list[dict] = []
    labels: list[dict] = []

    if chart.viz == Viz.BIG_NUMBER:
        placeholders = [{"id": "measures", "items": measures()[:1]}]
    elif chart.viz in (Viz.LINE, Viz.AREA, Viz.BAR, Viz.STACKED_BAR):
        # series + any extra dimensions become the color breakdown (deduped, order kept)
        breakdown: dict[str, None] = {}
        for r in (*q.series, *q.dimensions[1:]):
            breakdown.setdefault(r, None)
        colors = dims(list(breakdown))
        placeholders = [
            {"id": "x", "items": dims(q.dimensions[:1])},
            {"id": "y", "items": measures()},
        ]
    elif chart.viz == Viz.PIE:
        placeholders = [
            {"id": "dimensions", "items": dims(q.dimensions[:1])},
            {"id": "measures", "items": measures()[:1]},
        ]
        sort = [item(measure_alias(q.measures[0]))]
        labels = [item(measure_alias(q.measures[0]))]
    elif chart.viz == Viz.TABLE:
        placeholders = [{"id": "flat-table-columns", "items": dims(q.group_columns()) + measures()}]
    else:  # PIVOT, or HEATMAP degraded to a pivot table
        if chart.viz == Viz.HEATMAP:
            row_refs, col_refs = q.dimensions[:1], q.dimensions[1:2]
        else:
            row_refs, col_refs = q.rows, q.columns
        placeholders = [
            {"id": "rows", "items": dims(row_refs)},
            {"id": "pivot-table-columns", "items": dims(col_refs)},
            {"id": "measures", "items": measures()},
        ]

    partial = [
        {"guid": fields_by_alias[a]["guid"], "title": a, "calc_mode": "direct"} for a in used
    ]
    return {
        "colors": colors,
        "colorsConfig": dict(_COLORS_CONFIG),
        "datasetsIds": [dataset_id],
        "datasetsPartialFields": [partial],
        "extraSettings": dict(_EXTRA_SETTINGS),
        "filters": [],
        "geopointsConfig": {},
        "hierarchies": [],
        "labels": labels,
        "links": [],
        "segments": [],
        "shapes": [],
        "shapesConfig": {},
        "sort": sort,
        "tooltips": [],
        "type": "datalens",
        "updates": [],
        "version": "4",
        "visualization": {"id": viz_id, "placeholders": placeholders},
    }
