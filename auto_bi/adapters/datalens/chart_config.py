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

from auto_bi.ir.spec import (
    ChartSpec,
    Viz,
    column_alias,
    is_compact_number,
    is_percent_measure,
    measure_alias,
)

# Compact display for large additive aggregates (dashboard-craft §4): the DataLens `metric`
# widget shows the figure at a fixed large font and CLIPS a raw billions-scale number; an
# abbreviated number ("236B") fits. `unit: "auto"` lets DataLens pick the magnitude (the
# stand renders SI suffixes B/M, not "млрд/млн" — locale-bound); SQL/values are unchanged
# (display only). `precision: 0` — a compact KPI is a round headline figure: a fractional digit
# at the millions/billions scale is noise and reads inconsistently next to integer counts
# ("236B" alongside "115M"/"210M", not "236,1B"). Precision belongs only where it is meaningful
# (an average check is not a compact number — `is_compact_number`). Live-verified on the stand.
_COMPACT_FORMATTING = {
    "format": "number",
    "showRankDelimiter": True,
    "prefix": "",
    "postfix": "",
    "unit": "auto",
    "precision": 0,
    "labelMode": "absolute",
}

# percent display for ratio transforms (pop_pct, share): a 0..1 ratio renders as "50,0 %".
# DataLens `format: "percent"` scales by 100 and appends the sign; no SI unit. Display only.
# NB: the exact `formatting` shape for percent is reversed from the demo Wizard and must be
# live-verified on the stand (the field is otherwise asserted only by unit tests here).
# LIVE FINDING (2026-06-26): this placeholder-level `formatting` does NOT switch the axis to
# percent on the self-hosted stand (the axis stays at the raw 0..1 ratio); the compact
# `format: "number"` variant DOES apply at placeholder level. Switching the format *type* to
# percent likely has to live on the dataset field (result_schema), not the chart placeholder —
# open item, see docs/plans/2026-06-25-derived-metrics-pop.md §6.
_PERCENT_FORMATTING = {
    "format": "percent",
    "showRankDelimiter": True,
    "prefix": "",
    "postfix": "",
    "unit": None,
    "precision": 1,
    "labelMode": "absolute",
}

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

# A numeric dimension (store_id, manager_id, ...) on a DataLens column chart's categorical
# placeholder (X / color) lands on a CONTINUOUS axis — bars at numeric positions 0..N (a wall
# of thin bars) or a color gradient instead of distinct categories. Casting the field to a
# string in the chart placeholder forces a discrete category axis (B2, live-verified: a
# string-cast store_id axis returns highcharts `categories`, a numeric one returns raw-numeric
# x points). Measures and date/string dimensions are unaffected; the dataset field is untouched
# (only the per-chart placeholder casts), so the subselect SQL and dashboard selectors are not.
_NUMERIC_DATA_TYPES = frozenset({"integer", "float"})


def _is_numeric_dimension(field: dict) -> bool:
    return field.get("type") == "DIMENSION" and field.get("data_type") in _NUMERIC_DATA_TYPES


def _field_item(
    field: dict,
    field_id: str,
    dataset_id: str,
    dataset_name: str,
    *,
    as_string: bool = False,
    title: str | None = None,
) -> dict:
    """One placeholder item: a dataset field bound into a chart section.

    Shape reversed from the demo Wizard charts (reversal §5.2); the chart binds to the
    dataset by `guid` + `avatar_id`, both produced by build_dataset_payload. `as_string`
    casts this placeholder's field to a string (data_type + cast) for a discrete category
    axis — see `_NUMERIC_DATA_TYPES`. `title` overrides the item caption (default = the
    field's dataset title); a big-number metric blanks it so the tile header is the sole label.
    """
    data_type = "string" if as_string else field["data_type"]
    cast = "string" if as_string else field["cast"]
    return {
        "type": field["type"],
        "calc_mode": "direct",
        "data_type": data_type,
        "initial_data_type": data_type,
        "cast": cast,
        "aggregation": field["aggregation"],
        "source": field["source"],
        "guid": field["guid"],
        "title": field["title"] if title is None else title,
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
    *,
    horizontal: bool = False,
) -> dict:
    """IR chart -> DataLens `shared` config. `fields_by_alias` maps a bare alias to its
    dataset result_schema descriptor (guid/avatar_id/data_type/type/aggregation/cast).

    `horizontal` swaps a categorical bar's viz id "column" -> "bar" (DataLens horizontal
    bar); the adapter computes it from the model (agent.normalize.is_horizontal_bar)."""
    q = chart.query
    viz_id = VIZ_ID[chart.viz]
    if horizontal and chart.viz in (Viz.BAR, Viz.STACKED_BAR):
        # DataLens "bar" = horizontal bars (vs "column" vertical) so long RU category labels
        # get the full row width; same x/y placeholders + B2 discretization. Live-verified on
        # the stand 2026-06-26: Регион/Категория/Город render horizontally, labels readable
        # (screenshot autobi_hbars_datalens_01), console clean.
        viz_id = "bar"

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
    # measure aliases whose figure should display abbreviated (236B, not 236149963687)
    compact_aliases = {measure_alias(m) for m in q.measures if is_compact_number(m)}
    # ratio-transform aliases (pop_pct, share) that display as a percent
    percent_aliases = {measure_alias(m) for m in q.measures if is_percent_measure(m)}

    def item(alias: str, *, discrete: bool = False, title: str | None = None) -> dict:
        used.setdefault(alias, None)
        field = fields_by_alias[alias]
        # on a column chart, a numeric dimension must be string-cast to render as categories
        as_string = discrete and _is_numeric_dimension(field)
        out = _field_item(
            field, ids[alias], dataset_id, dataset_name, as_string=as_string, title=title
        )
        if alias in percent_aliases:
            out["formatting"] = dict(_PERCENT_FORMATTING)
        elif alias in compact_aliases:
            out["formatting"] = dict(_COMPACT_FORMATTING)
        return out

    def dims(refs: list[str], *, discrete: bool = False) -> list[dict]:
        return [item(column_alias(r), discrete=discrete) for r in refs]

    def measures() -> list[dict]:
        return [item(measure_alias(m)) for m in q.measures]

    colors: list[dict] = []
    sort: list[dict] = []
    labels: list[dict] = []

    if chart.viz == Viz.BIG_NUMBER:
        # the tile header already names the KPI (the widget title is chart.title), so blank the
        # metric field caption — the card shows one human label + the value, not the raw alias
        # "sum_revenue" beneath it (dashboard-craft §3: a KPI card is label / value, no noise).
        placeholders = [{"id": "measures", "items": [item(measure_alias(q.measures[0]), title="")]}]
    elif chart.viz in (Viz.LINE, Viz.AREA, Viz.BAR, Viz.STACKED_BAR):
        # series + any extra dimensions become the color breakdown (deduped, order kept)
        breakdown: dict[str, None] = {}
        for r in (*q.series, *q.dimensions[1:]):
            breakdown.setdefault(r, None)
        # bar/stacked_bar -> "column" viz: a numeric dimension on the categorical X / color
        # would land on a continuous axis (a wall of thin bars) / a color gradient, so discretize
        # it (B2). line/area keep a continuous axis (time / number) — they read along it.
        discrete = chart.viz in (Viz.BAR, Viz.STACKED_BAR)
        colors = dims(list(breakdown), discrete=discrete)
        placeholders = [
            {"id": "x", "items": dims(q.dimensions[:1], discrete=discrete)},
            {"id": "y", "items": measures()},
        ]
        # a categorical bar ranks by its measure: DataLens orders a categorical axis
        # alphabetically unless a `sort` field is set, so the biggest bar would not come first
        # even though the SQL orders by the measure. Mirror the measure order (the sorted-bar
        # rule) for a horizontal (categorical) bar; a time/continuous bar keeps its axis order.
        if horizontal and q.measures:
            sort = [item(measure_alias(q.measures[0]))]
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
