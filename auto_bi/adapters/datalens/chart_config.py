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

# percent display for ratio transforms (pop_pct, share): a 0..1 ratio renders as "50,0%".
# DataLens `format: "percent"` scales by 100 and appends the sign; no SI unit. Display only.
# NB: this item-level `formatting` alone does NOT reformat the value AXIS — see
# `_AXIS_FORMAT_BY_FIELD` below (C1 fix, live-verified 2026-07-06); the 2026-06-26 "percent
# does not work at placeholder level" finding was missing that flag, not a wrong key.
_PERCENT_FORMATTING = {
    "format": "percent",
    "showRankDelimiter": True,
    "prefix": "",
    "postfix": "",
    "unit": None,
    "precision": 1,
    "labelMode": "absolute",
}


# RU magnitude units for a large ruble/count KPI (N2, mirrors superset form_data.ru_kpi_scale):
# the metric widget's own `unit: "auto"` is locale-bound (the stand renders SI "236B", never
# "млрд"), so the adapter scales the measure in the dataset subselect (dataset.py
# `measure_scale`) and glues the Russian unit word to the figure via the formatting `postfix`
# ("236 млрд ₽"). Display-only: the scaled headline is a round figure (precision 0) — except
# in the 1–10 band, where whole-number rounding would lose up to a third of the figure
# ("1,5 млрд" -> "2 млрд"), so one decimal is kept there (L-1). No SI unit either way.
def _ru_kpi_formatting(unit: str, precision: int = 0) -> dict:
    return {
        "format": "number",
        "showRankDelimiter": True,
        "prefix": "",
        "postfix": f" {unit}",
        "unit": None,
        "precision": precision,
        "labelMode": "absolute",
    }


# Placeholder `settings` that make the value axis read the bound field's `formatting`.
# The charts engine populates axis formatting ONLY when `placeholder.settings.axisFormatMode`
# is "by-field" (read the first item's `formatting`) or "manual"; the default (no settings) is
# null -> no axis formatting -> a share/ratio axis shows the raw 0..1 number (C1). Reversed
# from datalens-ui 0.3831.0 (`preparers/helpers/axis/get-axis-formatting.js`:
# `getFormatOptions(field)` reads `field.formatting`, `getFormatOptionsFromFieldFormatting`
# carries `format: "percent"` through as `chartKitFormat`) and confirmed on the live stand:
# an inline /api/run with this flag returns `axesFormatting.yAxis[0].chartKitFormat=="percent"`
# while the un-flagged baseline returns an empty axesFormatting (2026-07-06).
_AXIS_FORMAT_BY_FIELD = {"axisFormatMode": "by-field"}

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
    Viz.HISTOGRAM: "column",  # vertical bars over the SQL-computed buckets (binning is in SQL_GEN)
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
    kpi_unit: str | None = None,
    kpi_precision: int = 0,
    axis_unit: str | None = None,
) -> dict:
    """IR chart -> DataLens `shared` config. `fields_by_alias` maps a bare alias to its
    dataset result_schema descriptor (guid/avatar_id/data_type/type/aggregation/cast).

    `horizontal` swaps a categorical bar's viz id "column" -> "bar" (DataLens horizontal
    bar); the adapter computes it from the model (agent.normalize.is_horizontal_bar).

    `kpi_unit` (big_number only) is the RU magnitude unit line ("млрд ₽") for a headline the
    adapter has already scaled in the dataset subselect (N2): the metric formatting becomes
    round-figure + the unit as a postfix ("236 млрд ₽" instead of the SI "236B"). None =>
    the compact/percent formatting as before. `kpi_precision` is the headline's decimal
    places: 1 when the scaled figure sits in the 1–10 band (L-1), else 0.

    `axis_unit` (line/bar/area) is the same unit line for a scaled VALUE axis: it becomes a
    manual axis title ("млрд ₽") on the values placeholder, so the scaled ticks read "15"
    against the titled axis instead of "15B" (mirrors Superset's y_axis_title)."""
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

    extra_settings = dict(_EXTRA_SETTINGS)
    if chart.viz == Viz.BIG_NUMBER:
        # the tile header already names the KPI (the widget title is chart.title), so blank the
        # metric field caption — the card shows one human label + the value, not the raw alias
        # "sum_revenue" beneath it (dashboard-craft §3: a KPI card is label / value, no noise).
        kpi_item = item(measure_alias(q.measures[0]), title="")
        if kpi_unit:
            # N2: the adapter scaled the measure in the dataset SQL; show "236 млрд ₽"
            kpi_item["formatting"] = _ru_kpi_formatting(kpi_unit, kpi_precision)
        placeholders = [{"id": "measures", "items": [kpi_item]}]
        # the default metric font ('' == "m") CLIPS a figure with a RU unit line at the
        # standard 6-col KPI tile ("236 млрд ₽" lost its currency; live-verified 2026-07-06);
        # "s" fits with room to spare and is set uniformly so the KPI row reads as one size.
        extra_settings["metricFontSize"] = "s"
    elif chart.viz in (Viz.LINE, Viz.AREA, Viz.BAR, Viz.STACKED_BAR, Viz.HISTOGRAM):
        # series + any extra dimensions become the color breakdown (deduped, order kept)
        breakdown: dict[str, None] = {}
        for r in (*q.series, *q.dimensions[1:]):
            breakdown.setdefault(r, None)
        # bar/stacked_bar/histogram -> "column" viz: a numeric dimension on the categorical X /
        # color would land on a continuous axis (a wall of thin bars) / a color gradient, so
        # discretize it (B2; a histogram's bucket bounds are numeric -> each bucket a category).
        # line/area keep a continuous axis (time / number) — they read along it.
        discrete = chart.viz in (Viz.BAR, Viz.STACKED_BAR, Viz.HISTOGRAM)
        colors = dims(list(breakdown), discrete=discrete)
        # Placeholder ids per orientation. The engine's DATA preparers are positional
        # (placeholders[0] = category, placeholders[1] = values), but the AXIS paths are
        # id-based, and the horizontal "bar" (bar-y preparer) swaps them: the value axis
        # reads placeholder id "x", the category axis id "y" ("for some reason, the vertical
        # axis for the horizontal bar is considered the X axis" — datalens-ui 0.3831.0
        # preparers/bar-y/highcharts.js). So a horizontal bar must carry the wizard's ids
        # (dimension -> "y", measures -> "x") in the same positional order, or axis
        # formatting (C1 percent) would land on the category axis and be dropped.
        dim_ph_id, values_ph_id = ("y", "x") if viz_id == "bar" else ("x", "y")
        values_placeholder: dict = {"id": values_ph_id, "items": measures()}
        if q.measures and measure_alias(q.measures[0]) in percent_aliases:
            # C1: the value axis reads the primary measure's percent `formatting` only with
            # this flag (the engine takes placeholder.items[0], so the primary measure decides).
            values_placeholder["settings"] = dict(_AXIS_FORMAT_BY_FIELD)
        elif axis_unit:
            # N2: the adapter scaled the measures in the dataset SQL; name the unit on the
            # value axis ("млрд ₽") so the scaled ticks read "15", not the SI "15B".
            values_placeholder["settings"] = {"title": "manual", "titleValue": axis_unit}
        placeholders = [
            {"id": dim_ph_id, "items": dims(q.dimensions[:1], discrete=discrete)},
            values_placeholder,
        ]
        # a categorical bar ranks by its measure: DataLens orders a *string* categorical axis
        # alphabetically unless a `sort` field is set, so the biggest bar would not come first
        # even though the SQL orders by the measure. Mirror the measure order (the sorted-bar
        # rule) for a horizontal (categorical) bar; a time/continuous bar keeps its axis order.
        #
        # HISTOGRAM intentionally gets NO sort (C7, live-verified S12 2026-07-04): its bucket
        # bounds are a NUMERIC dimension string-cast to a discrete axis (B2 above), and DataLens
        # sorts a numeric-string-cast categorical axis NUMERICALLY by default — buckets render
        # 50,100,…,350 (not lexicographic "50" after "350"), even when the subselect returns them
        # out of order (verified with a deliberately scrambled SQL order). The audit's C7 concern
        # ("categorical axis → alphabetical") holds only for genuine string categories (city
        # names → the measure sort above), NOT for numeric buckets. Do NOT add a sort here: a
        # measure/dimension sort with no explicit direction resorts the bars DESC (verified), which
        # would BREAK the ascending bucket order that DataLens already gives for free.
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
        "extraSettings": extra_settings,
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
