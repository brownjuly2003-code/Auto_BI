"""Column-read composition for the chart/data plugins of Superset 4.1.2.

This is a read set, not executable viz SQL or a claim about a metric's formula. SQL
fragments remain separate select items; unused dataset expressions are never included.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, NoReturn

from sqlglot import expressions as exp


class CompositeRefusal(ValueError):
    """A safe, expression-free reason why a read set cannot be composed."""


# Apache Superset tag 4.1.2, commit 72538bda06c0deef2140e0df65bf0a13bcddb16c,
# Apache-2.0. Paths below are relative to superset-frontend/.
# packages/superset-ui-core/src/query/{buildQueryObject,extractQueryFields,
# extractExtras,processFilters,buildQueryContext,normalizeTimeColumn,getXAxis}.ts.
FIELD_ALIASES = {
    "metric": "metrics",
    "metric_2": "metrics",
    "secondary_metric": "metrics",
    "x": "metrics",
    "y": "metrics",
    "size": "metrics",
    "all_columns": "columns",
    "series": "groupby",
    "order_by_cols": "orderby",
}
COMMON_KEYS = frozenset(FIELD_ALIASES) | frozenset(
    [
        "metrics",
        "columns",
        "groupby",
        "orderby",
        "query_mode",
        "adhoc_filters",
        "where",
        "having",
        "filters",
        "extra_filters",
        "granularity",
        "granularity_sqla",
        "time_range",
        "since",
        "until",
        "series_columns",
        "series_limit",
        "series_limit_metric",
        "timeseries_limit_metric",
        "limit",
        "row_limit",
        "row_offset",
        "order_desc",
        "time_grain_sqla",
        "annotation_layers",
        "extra_form_data",
        "custom_params",
        "url_params",
        "datasource",
        "force",
        "result_format",
        "result_type",
        "x_axis",
    ]
)

# Each entry identifies the query builder, its additional column inputs (overrides
# of the base columns are applied in _query), and its other consumed controls.
# All paths start with plugins/ and end with /buildQuery.ts.
VIZ_RULES: dict[str, tuple[str, tuple[str, ...], frozenset[str]]] = {
    "big_number_total": ("plugin-chart-echarts/src/BigNumber/BigNumberTotal", (), frozenset()),
    "big_number": ("plugin-chart-echarts/src/BigNumber/BigNumberWithTrendline", (), frozenset()),
    "box_plot": ("plugin-chart-echarts/src/BoxPlot", (), frozenset({"temporal_columns_lookup"})),
    "bubble_v2": ("plugin-chart-echarts/src/Bubble", ("entity", "series"), frozenset()),
    "funnel": ("plugin-chart-echarts/src/Funnel", (), frozenset({"sort_by_metric"})),
    "gauge_chart": ("plugin-chart-echarts/src/Gauge", (), frozenset({"sort_by_metric"})),
    "graph_chart": (
        "plugin-chart-echarts/src/Graph",
        ("source", "target", "source_category", "target_category"),
        frozenset(),
    ),
    "handlebars": ("plugin-chart-handlebars/src/plugin", (), frozenset()),
    "heatmap_v2": (
        "plugin-chart-echarts/src/Heatmap",
        (),
        frozenset({"normalize_across", "sort_x_axis", "sort_y_axis"}),
    ),
    "histogram_v2": ("plugin-chart-echarts/src/Histogram", ("column",), frozenset()),
    "mixed_timeseries": ("plugin-chart-echarts/src/MixedTimeseries", (), frozenset()),
    "pie": ("plugin-chart-echarts/src/Pie", (), frozenset({"sort_by_metric"})),
    "pivot_table_v2": (
        "plugin-chart-pivot-table/src/plugin",
        ("groupbyRows", "groupbyColumns"),
        frozenset({"temporal_columns_lookup"}),
    ),
    "pop_kpi": (
        "plugin-chart-echarts/src/BigNumber/BigNumberPeriodOverPeriod",
        ("cols",),
        frozenset({"adhoc_custom", "start_date_offset"}),
    ),
    "radar": ("plugin-chart-echarts/src/Radar", (), frozenset()),
    "sankey_v2": (
        "plugin-chart-echarts/src/Sankey",
        ("source", "target"),
        frozenset({"sort_by_metric"}),
    ),
    "sunburst_v2": ("plugin-chart-echarts/src/Sunburst", (), frozenset({"sort_by_metric"})),
    "table": (
        "plugin-chart-table/src",
        (),
        frozenset(
            {
                "percent_metrics",
                "temporal_columns_lookup",
                "adhoc_custom",
                "start_date_offset",
                "server_pagination",
                "server_page_length",
                "show_totals",
                "include_time",
            }
        ),
    ),
    "tree_chart": ("plugin-chart-echarts/src/Tree", ("id", "parent", "name"), frozenset()),
    "treemap_v2": ("plugin-chart-echarts/src/Treemap", (), frozenset({"sort_by_metric"})),
    "waterfall": ("plugin-chart-echarts/src/Waterfall", (), frozenset()),
    "word_cloud": ("plugin-chart-word-cloud/src/plugin", (), frozenset({"sort_by_metric"})),
}
TIMESERIES = frozenset(
    [
        "echarts_area",
        "echarts_timeseries",
        "echarts_timeseries_bar",
        "echarts_timeseries_line",
        "echarts_timeseries_scatter",
        "echarts_timeseries_smooth",
        "echarts_timeseries_step",
    ]
)
for _viz in TIMESERIES:
    VIZ_RULES[_viz] = ("plugin-chart-echarts/src/Timeseries", (), frozenset())

# The builders pass these to dataframe post-processing or change only ordering,
# limits and labels. Time offsets clone the SQL query (common/query_context_processor.py,
# processing_time_offsets); _time includes their possible fallback time column.
# extractExtraMetrics can add the series sort metric: it is always read below.
POSTPROCESS_KEYS = frozenset(
    [
        "time_compare",
        "comparison_type",
        "rolling_type",
        "rolling_periods",
        "min_periods",
        "contributionMode",
        "resample_rule",
        "resample_method",
        "cumulative",
        "forecastEnabled",
        "forecastPeriods",
        "forecastInterval",
        "forecastSeasonalityDaily",
        "forecastSeasonalityWeekly",
        "forecastSeasonalityYearly",
        "sort_series_type",
        "x_axis_sort_asc",
        "x_axis_sort_series",
        "x_axis_sort_series_ascending",
        "whiskerOptions",
        "whisker_options",
        "bins",
        "normalize",
        "aggregateFunction",
    ]
)

# Explicitly checked non-query inputs: no lookup in the above builders/core query
# functions. They are kept by {...formData} only in context.form_data, not queries.
# In particular form_data.queryFields is NOT options.queryFields passed by Graph/Tree.
# Residue of legacy controls is harmless on these plugins, not on legacy viz.py.
IGNORED_KEYS = frozenset(
    [
        "slice_id",
        "dashboards",
        "viz_type",
        "queryFields",
        "compare_lag",
        "compare_suffix",
        "markup_type",
        "country_fieldtype",
        "show_bubbles",
        "color_picker",
        "time_range_fixed",
        "align_pn",
        "allow_render_html",
        "bottom_margin",
        "colOrder",
        "color_pn",
        "color_scheme",
        "date_format",
        "donut",
        "header_font_size",
        "include_search",
        "innerRadius",
        "label_colors",
        "label_line",
        "label_type",
        "labels_outside",
        "left_margin",
        "legendOrientation",
        "legendType",
        "legend_type",
        "linear_color_scheme",
        "markerSize",
        "markerSizeB",
        "markerEnabled",
        "metricsLayout",
        "normalized",
        "number_format",
        "only_total",
        "opacity",
        "opacityB",
        "orientation",
        "outerRadius",
        "page_length",
        "rich_tooltip",
        "rowOrder",
        "seriesType",
        "seriesTypeB",
        "show_cell_bars",
        "show_empty_columns",
        "show_extra_controls",
        "show_labels",
        "show_labels_threshold",
        "show_legend",
        "show_percentage",
        "show_values",
        "show_value",
        "subheader",
        "subheader_font_size",
        "table_timestamp_format",
        "time_format",
        "tooltipTimeFormat",
        "truncateXAxis",
        "truncateYAxis",
        "truncate_metric",
        "valueFormat",
        "value_bounds",
        "xAxisLabelRotation",
        "x_axis_time_format",
        "x_axis_title",
        "x_axis_title_margin",
        "xscale_interval",
        "y_axis_bounds",
        "y_axis_format",
        "y_axis_title",
        "y_axis_title_margin",
        "y_axis_title_position",
        "yscale_interval",
        "logAxis",
        "order_bars",
        "zoomable",
        "x_ticks_layout",
        "max_bubble_size",
        "tooltipSizeFormat",
        "xAxisFormat",
        "legendMargin",
        "percent_calculation_type",
        "show_tooltip_labels",
        "tooltip_label_type",
        "animation",
        "end_angle",
        "font_size",
        "overlap",
        "round_cap",
        "show_axis_tick",
        "show_pointer",
        "show_progress",
        "show_split_line",
        "split_number",
        "start_angle",
        "value_formatter",
        "baseEdgeWidth",
        "baseNodeSize",
        "edgeLength",
        "edgeSymbol",
        "friction",
        "gravity",
        "layout",
        "repulsion",
        "roam",
        "selectedMode",
        "is_circle",
        "label_position",
        "child_label_position",
        "node_label_position",
        "root_node_id",
        "symbol",
        "symbolSize",
        "show_upper_labels",
        "treemap_ratio",
        "decrease_color",
        "increase_color",
        "total_color",
        "rotation",
        "size_from",
        "size_to",
        "show_trend_line",
        "start_y_axis_at_zero",
        "table_filter",
        "yAxisIndex",
        "yAxisIndexB",
        "y_axis_bounds_secondary",
        "y_axis_format_secondary",
        "stack",
    ]
)
_PLUGIN_KEYS = frozenset(
    key for _, columns, controls in VIZ_RULES.values() for key in (*columns, *controls)
)


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {} or value is False


def _form_column(value: Any) -> bool:
    """superset-ui-core `isQueryFormColumn`: what `isXAxisSet` takes for a set x-axis."""
    return isinstance(value, str) or (
        isinstance(value, Mapping)
        and "sqlExpression" in value
        and "label" in value
        and ("expressionType" not in value or value["expressionType"] == "SQL")
    )


def _values(value: Any) -> list[Any]:
    return [] if value is None or value == "" else value if isinstance(value, list) else [value]


def _fail(key: str, reason: str) -> NoReturn:
    raise CompositeRefusal(f"form_data[{key!r}] {reason}")


def _jinja(value: Any) -> bool:
    text = json.dumps(value)
    return "{{" in text or "{%" in text or "{#" in text


# SqlaTable.adhoc_column_to_sqla processes an expression before it looks up a dataset column
# of that name: the template step (Jinja under ENABLE_TEMPLATE_PROCESSING) and
# sql_parse.sanitize_clause, which ends a trailing comment with a line feed.
_UNSTABLE_NAME = ("--", "/*", "#", "\r", "\n")


def _template_text(sql: str) -> str:
    """Jinja's default rendering of text without tags: newlines made LF, one trailing LF cut."""
    text = sql.replace("\r\n", "\n").replace("\r", "\n")
    return text[:-1] if text.endswith("\n") else text


class _Reads:
    def __init__(self, definition: Mapping[str, Any]) -> None:
        self.definition = definition
        self.columns = self._index("columns", "column_name")
        self.metrics = self._index("metrics", "metric_name")
        self.items: dict[str, None] = {}

    def _index(self, key: str, name: str) -> dict[str, Mapping[str, Any]]:
        values = self.definition.get(key)
        if not isinstance(values, list) or not all(
            isinstance(v, Mapping) and isinstance(v.get(name), str) for v in values
        ):
            raise CompositeRefusal(f"the dataset definition has no readable {key}")
        result = {v[name]: v for v in values}
        if len(result) != len(values):
            raise CompositeRefusal(f"the dataset definition has duplicate {key}")
        return result

    def fragment(self, value: Any, key: str) -> None:
        if not isinstance(value, str) or not value.strip():
            _fail(key, "has no SQL expression")
        if _jinja(value):
            _fail(key, "reads an expression carrying Jinja")
        if "\r" in value:
            # The template step makes CR a line feed, inside a quoted name too.
            _fail(
                key, "reads an expression with a carriage return, which the template step changes"
            )
        self.items[f"(\n{value.strip()}\n)"] = None

    def column(self, value: Any, key: str) -> None:
        if isinstance(value, Mapping):
            # utils.is_adhoc_column: an object column is an expression only with both keys.
            if "sqlExpression" not in value or "label" not in value:
                _fail(key, "has a column object Superset does not read as an expression")
            sql = value.get("sqlExpression")
            processed = _template_text(sql) if isinstance(sql, str) else sql
            if processed != sql and processed in self.columns:
                _fail(key, "has an expression the template step can turn into a column name")
            # SqlaTable.adhoc_column_to_sqla resolves an expression equal to a
            # dataset column name through that column's metadata, too.
            if isinstance(sql, str) and sql in self.columns:
                self.column(sql, key)
            else:
                self.fragment(sql, key)
            return
        if not isinstance(value, str) or value not in self.columns:
            _fail(key, "names a column absent from the dataset definition")
        expression = self.columns[value].get("expression")
        if expression:
            self.fragment(expression, key)
        else:
            self.items[exp.to_identifier(value, quoted=True).sql(dialect="clickhouse")] = None

    def metric(self, value: Any, key: str) -> None:
        if isinstance(value, str):
            if value not in self.metrics:
                _fail(key, "names a metric absent from the dataset definition")
            self.fragment(self.metrics[value].get("expression"), key)
        elif isinstance(value, Mapping) and value.get("expressionType") == "SQL":
            self.fragment(value.get("sqlExpression"), key)
        elif isinstance(value, Mapping) and value.get("expressionType") == "SIMPLE":
            col = value.get("column")
            if not isinstance(col, Mapping) or not value.get("aggregate"):
                _fail(key, "has no readable SIMPLE metric")
            self.column(col.get("column_name"), key)
        else:
            _fail(key, "has no readable metric")

    def order(self, value: Any, key: str, metrics: list[Any]) -> None:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                _fail(key, "has an unreadable orderby pair")
        if not isinstance(value, list) or len(value) != 2 or type(value[1]) is not bool:
            _fail(key, "has an unreadable orderby pair")
        item = value[0]
        if isinstance(item, str) and item in self.columns:
            self.column(item, key)
        elif isinstance(item, str):
            # Backend resolves a selected metric label before a saved metric name.
            matches = [m for m in metrics if isinstance(m, Mapping) and m.get("label") == item]
            if matches:
                for metric in matches:
                    self.metric(metric, key)
            else:
                self.metric(item, key)
        elif isinstance(item, Mapping) and item.get("expressionType") in ("SIMPLE", "SQL"):
            self.metric(item, key)
            if item.get("expressionType") == "SQL":
                # Adhoc column and metric representations overlap: cover both backend paths.
                self.column(item, key)
        else:
            _fail(key, "has no readable sort metric")

    def temporal(self, value: Any, key: str) -> None:
        if value is None or value == "":
            return
        if not isinstance(value, str) or value not in self.columns:
            _fail(key, "names a time column absent from the dataset definition")
        if not self.columns[value].get("is_dttm"):
            value = self.definition.get("main_dttm_col")
            if not value:
                _fail(key, "has no temporal column or dataset main time column")
        self.column(value, key)

    def query(self, form: Mapping[str, Any], viz: str, suffix: str = "") -> None:
        def key(name: str) -> str:
            return name + suffix

        for name, value in form.items():
            if (
                name not in COMMON_KEYS | POSTPROCESS_KEYS | IGNORED_KEYS | _PLUGIN_KEYS
                and not _empty(value)
            ):
                _fail(key(name), "is not a key whose query effect is known")
        for name in ("extra_form_data", "custom_params"):
            if not _empty(form.get(name)):
                _fail(key(name), "has query overrides that are not composed")
        if form.get("annotation_layers"):
            _fail(key("annotation_layers"), "can query another source")

        columns: list[tuple[Any, str]] = []
        metrics: list[tuple[Any, str]] = []
        order: list[tuple[Any, str]] = []
        mode = form.get("query_mode")
        for name, value in form.items():
            field = FIELD_ALIASES.get(name, name)
            if mode == "aggregate" and field == "columns":
                continue
            if mode == "raw" and field in {"groupby", "metrics"}:
                continue
            dest = (
                columns
                if field in {"columns", "groupby"}
                else metrics if field == "metrics" else order if field == "orderby" else None
            )
            if dest is not None:
                dest.extend((v, key(name)) for v in _values(value))

        def cols(*names: str) -> list[tuple[Any, str]]:
            return [(v, key(n)) for n in names for v in _values(form.get(n))]

        axis = form.get("x_axis")
        if not _empty(axis) and not _form_column(axis):
            # getXAxis.isXAxisSet: such an axis is not set. The time-series, big number and
            # heatmap builders put a time column in its place (is_timeseries, DTTM_ALIAS);
            # waterfall passes the object on (`x_axis || granularity_sqla`). Neither is
            # repeated here: refusing is stricter than waterfall.
            _fail(key("x_axis"), "is not a column the 4.1.2 builders take for an axis")
        timeseries = False
        if viz in TIMESERIES | {"mixed_timeseries", "big_number"}:
            columns = cols("x_axis") + ([] if viz == "big_number" else cols("groupby"))
            timeseries = _empty(axis)
        elif viz == "heatmap_v2":
            columns = cols("x_axis", "groupby")
            timeseries = _empty(axis) and bool(form.get("granularity_sqla"))
        elif viz == "waterfall":
            columns = cols("x_axis" if axis else "granularity_sqla", "groupby")
        elif viz == "box_plot":
            columns = cols("columns" if form.get("columns") else "granularity_sqla", "groupby")
        elif viz == "histogram_v2":
            columns, metrics = cols("groupby", "column"), []
        elif viz in {"bubble_v2", "pivot_table_v2"}:
            columns = cols(*VIZ_RULES[viz][1])
        elif viz in {"sankey_v2", "pop_kpi"} or (
            viz in {"tree_chart", "graph_chart"} and mode != "aggregate"
        ):
            columns += cols(*VIZ_RULES[viz][1])

        table_mode = (
            mode
            if mode in {"raw", "aggregate"}
            else "raw" if form.get("all_columns") else "aggregate"
        )
        if viz == "table" and table_mode == "aggregate":
            metrics += [(v, key("percent_metrics")) for v in _values(form.get("percent_metrics"))]
        if form.get("sort_by_metric") and "sort_by_metric" in VIZ_RULES[viz][2]:
            metrics += [(v, key("metric")) for v in _values(form.get("metric"))]
        # Conservative union: these can be used by plugin sorting even without a
        # series limit. The series-limit prequery reads the same grouping/filter fields.
        for name in ("series_limit_metric", "timeseries_limit_metric"):
            metrics += [(v, key(name)) for v in _values(form.get(name))]
        columns += cols("series_columns")
        for col, where in columns:
            if col == "__timestamp":
                # normalizeTimeColumn and the temporal column lookups of table, box plot and
                # pivot table make a column string an expression: absent from the metadata,
                # `__timestamp` is literal SQL (a table column), not the time column.
                _fail(where, "names the time alias __timestamp, which a builder can make SQL")
            self.column(col, where)
        for metric, where in metrics:
            self.metric(metric, where)
        for item, where in order:
            self.order(item, where, [m for m, _ in metrics])

        temporal_filter = False
        granularity = form.get("granularity") or form.get("granularity_sqla")
        time_range = form.get("time_range")
        for name in ("filters", "extra_filters", "adhoc_filters"):
            values = form.get(name) or []
            if not isinstance(values, list):
                _fail(key(name), "is not a list of filters")
            for item in values:
                if not isinstance(item, Mapping):
                    _fail(key(name), "has an unreadable filter")
                if name == "adhoc_filters" and item.get("expressionType") == "SQL":
                    self.fragment(item.get("sqlExpression"), key(name))
                    continue
                if name == "adhoc_filters" and item.get("expressionType") != "SIMPLE":
                    _fail(key(name), "has an unreadable filter expression type")
                column = item.get("subject" if name == "adhoc_filters" else "col")
                operator = item.get("operator" if name == "adhoc_filters" else "op")
                value = item.get("comparator" if name == "adhoc_filters" else "val")
                if name == "extra_filters" and isinstance(column, str) and column.startswith("__"):
                    if column == "__time_range":
                        time_range = value
                    elif column == "__time_col":
                        granularity = (
                            value or form.get("granularity") or form.get("granularity_sqla")
                        )
                    elif column not in {"__time_grain", "__granularity"}:
                        _fail(key(name), "has an unknown reserved filter column")
                    continue
                if column == "__timestamp":
                    timeseries = True
                else:
                    self.column(column, key(name))
                temporal_filter |= operator == "TEMPORAL_RANGE" and value != "No filter"
        for name in ("where", "having"):
            if form.get(name):
                self.fragment(form[name], key(name))

        # QueryObjectFactory._process_time_range also takes the first temporal filter.
        # QueryContextFactory._apply_granularity replaces a temporal x-axis even
        # without a range; this selected column is distinct from the time predicate.
        axis_sql = axis.get("sqlExpression") if isinstance(axis, Mapping) else axis
        if (
            granularity
            and isinstance(axis_sql, str)
            and self.columns.get(axis_sql, {}).get("is_dttm")
        ):
            self.column(granularity, key("granularity"))
        ranged = time_range not in (None, "", "No filter") or temporal_filter
        ranged |= bool(form.get("since") or form.get("until"))
        compare = viz in TIMESERIES | {"mixed_timeseries", "table", "pop_kpi"} and bool(
            form.get("time_compare")
        )
        if timeseries or ranged or compare:
            self.temporal(granularity, key("granularity"))
        if compare and not granularity:
            # normalizeTimeColumn and Table's temporal lookup can mark a column as
            # BASE_AXIS. Offsets resolve its label against dataset metadata, not SQL.
            for candidate, where in [*columns, (axis, key("x_axis"))]:
                label = candidate.get("label") if isinstance(candidate, Mapping) else candidate
                if isinstance(label, str) and label in self.columns:
                    self.temporal(label, where)
        if (ranged and self.definition.get("always_filter_main_dttm")) or compare:
            # Offsets can set granularity to the x-axis label; a non-temporal label
            # is replaced by main_dttm_col in helpers.get_sqla_query.
            self.temporal(self.definition.get("main_dttm_col"), key("time_range"))


def compose_sql(
    form: Mapping[str, Any], dataset: Mapping[str, Any], definition: Mapping[str, Any]
) -> str:
    """Compose the union of SQL column reads, or raise an expression-free refusal."""
    viz = str(form.get("viz_type"))
    if viz not in VIZ_RULES:
        raise CompositeRefusal("the viz is not in the Superset 4.1.2 chart/data list")
    if _jinja(form):
        raise CompositeRefusal("form_data carries Jinja")
    reads = _Reads(definition)
    if "__timestamp" in reads.columns:
        # DTTM_ALIAS: Superset reads such a column through its metadata where it is an axis
        # (normalizeTimeColumn) or a filter subject of a query that is not a time series —
        # and the time column where `__timestamp` stands for it. Not told apart here.
        raise CompositeRefusal("the dataset has a column named __timestamp, Superset's time alias")
    if any(mark in name for name in reads.columns for mark in _UNSTABLE_NAME):
        # A name with a comment or a line break is not the name the processed expression
        # looks up: an axis, a temporal group or an adhoc column naming it reads other SQL.
        raise CompositeRefusal(
            "the dataset has a column name Superset's expression processing can change"
        )
    if viz == "mixed_timeseries":
        # plugins/plugin-chart-echarts/src/utils/formDataSuffix.ts: all _b keys,
        # with B taking precedence and other controls inherited from A.
        first = {k: v for k, v in form.items() if not k.endswith("_b")}
        second = {**first, **{k[:-2]: v for k, v in form.items() if k.endswith("_b")}}
        # MixedTimeseries/buildQuery.ts uses the original axis in BOTH query callbacks;
        # the suffix changes groupby/metrics/filter/time controls, not that axis.
        second["x_axis"] = first.get("x_axis")
        reads.query(first, viz)
        reads.query(second, viz, "_b")
    else:
        reads.query(form, viz)
    sql = definition.get("sql")
    if _jinja(sql):
        raise CompositeRefusal("the dataset SQL carries Jinja")
    if isinstance(sql, str) and "\r" in sql:
        raise CompositeRefusal(
            "the dataset SQL has a carriage return, which the template step changes"
        )
    schema, table = dataset.get("schema"), dataset.get("table_name")
    if not isinstance(schema, str) or not schema or not isinstance(table, str) or not table:
        raise CompositeRefusal("the dataset names no schema or table")
    if sql:
        if not isinstance(sql, str):
            raise CompositeRefusal("the dataset SQL is not text")
        source = "(\n" + sql.strip("\t\r\n; ") + "\n) AS virtual_table"
    else:
        source = exp.Table(this=exp.to_identifier(table), db=exp.to_identifier(schema)).sql(
            dialect="clickhouse"
        )
    if not reads.items:
        raise CompositeRefusal("the query names no select items")
    return "SELECT\n" + ",\n".join(reads.items) + "\nFROM " + source
