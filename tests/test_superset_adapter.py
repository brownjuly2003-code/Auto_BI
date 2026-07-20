"""SupersetAdapter unit tests on httpx.MockTransport — API payload shapes only.

The real form_data/position_json contract is verified against the live pinned
Superset by tests/test_superset_contract.py (integration, runs on the Mac stand).
"""

import json

import httpx
import pytest

from auto_bi.adapters.base import DatasetRef, DWHConfig
from auto_bi.adapters.superset.adapter import SupersetAdapter
from auto_bi.adapters.superset.client import SupersetAPIError, SupersetClient
from auto_bi.adapters.superset.form_data import build_form_data, build_position_json, ru_kpi_scale
from auto_bi.ir.spec import (
    ChartQuery,
    ChartSpec,
    DashboardSpec,
    LayoutHint,
    Measure,
    MeasureTransform,
    OrderBy,
    Viz,
)
from auto_bi.semantic.model import Aggregation, SemanticModel

DWH = DWHConfig(host="ch", port=8123, database="dm", user="ro", password="pw")
MODEL = SemanticModel.load("semantic/model.yaml")


def make_spec() -> DashboardSpec:
    revenue = Measure(column="revenue", agg=Aggregation.SUM, label="Выручка")
    return DashboardSpec(
        title="Продажи: обзор",
        charts=[
            ChartSpec(
                id="kpi",
                title="Выручка всего",
                viz=Viz.BIG_NUMBER,
                query=ChartQuery(table="dm.sales_daily", measures=[revenue]),
                layout_hint=LayoutHint(w=4, h=2, row=0),
            ),
            ChartSpec(
                id="trend",
                title="Выручка по дням",
                viz=Viz.LINE,
                query=ChartQuery(table="dm.sales_daily", dimensions=["date"], measures=[revenue]),
                layout_hint=LayoutHint(w=8, h=4, row=1),
            ),
        ],
    )


class FakeSuperset:
    """Just enough of the 4.1 REST API; records every mutating request."""

    def __init__(
        self, existing_databases: list[dict] | None = None, kpi_value: float | None = None
    ) -> None:
        self.requests: list[tuple[str, str, dict | None]] = []
        self.databases = existing_databases or []
        self.datasets: list[dict] = []
        self.next_id = 100
        # value the /chart/data probe returns for the KPI-magnitude measurement (None => no rows,
        # so _measure_magnitude yields None and the chart keeps its default format)
        self.kpi_value = kpi_value

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, path, body))

        if path == "/api/v1/security/login":
            return httpx.Response(200, json={"access_token": "jwt"})
        if path == "/api/v1/security/csrf_token/":
            return httpx.Response(200, json={"result": "csrf"})
        if path == "/health":
            return httpx.Response(200, text="OK")
        if path == "/api/v1/database/" and request.method == "GET":
            return httpx.Response(200, json={"result": self.databases})
        if path == "/api/v1/dataset/" and request.method == "GET":
            return httpx.Response(200, json={"result": self.datasets})
        if path == "/api/v1/chart/data" and request.method == "POST":
            alias = body["queries"][0]["metrics"][0]["label"]
            rows = [] if self.kpi_value is None else [{alias: self.kpi_value}]
            return httpx.Response(200, json={"result": [{"data": rows}]})
        if request.method == "POST":
            self.next_id += 1
            return httpx.Response(201, json={"id": self.next_id, "result": body})
        if request.method == "PUT":
            return httpx.Response(200, json={"result": body})
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})


def make_adapter(fake: FakeSuperset, model: SemanticModel | None = None) -> SupersetAdapter:
    http = httpx.Client(base_url="http://superset.test", transport=httpx.MockTransport(fake))
    return SupersetAdapter(
        SupersetClient("http://superset.test", "admin", "pw", http=http), DWH, model=model
    )


# --- form_data templates ----------------------------------------------------


def test_form_data_line() -> None:
    chart = make_spec().charts[1]
    fd = build_form_data(chart, dataset_id=42)
    assert fd["viz_type"] == "echarts_timeseries_line"
    assert fd["datasource"] == "42__table"
    assert fd["x_axis"] == "date"
    assert fd["metrics"][0]["sqlExpression"] == 'SUM("Выручка")'
    assert fd["groupby"] == []
    assert "granularity_sqla" not in fd  # no time_column => no time binding


def test_form_data_source_uses_real_agg_over_raw_column() -> None:
    # D-1 SOURCE: adhoc metric is SUM/AVG/... over the mart column, not identity over alias
    chart = make_spec().charts[1]  # label="Выручка", column=revenue
    fd = build_form_data(chart, dataset_id=42, from_source=True)
    assert fd["metrics"][0]["sqlExpression"] == 'SUM("revenue")'
    assert fd["metrics"][0]["label"] == "Выручка"  # display name unchanged
    avg = _chart(
        Viz.LINE, dimensions=["date"], measures=[Measure(column="check", agg=Aggregation.AVG)]
    )
    assert (
        build_form_data(avg, 1, from_source=True)["metrics"][0]["sqlExpression"] == 'AVG("check")'
    )


def test_form_data_source_kpi_aggregates_multi_row() -> None:
    # SOURCE KPI sits on multi-row grain — IR agg (SUM), never identity MAX
    fd = build_form_data(make_spec().charts[0], dataset_id=7, from_source=True)
    assert fd["metric"]["sqlExpression"] == 'SUM("revenue")'
    assert "MAX" not in fd["metric"]["sqlExpression"]


def test_form_data_source_time_grain_sqla() -> None:
    from auto_bi.ir.spec import TimeGrain

    chart = _chart(
        Viz.LINE,
        dimensions=["date"],
        measures=[Measure(column="revenue", agg=Aggregation.SUM)],
        time_grain=TimeGrain.MONTH,
    )
    fd = build_form_data(chart, dataset_id=1, from_source=True, time_column="date")
    assert fd["time_grain_sqla"] == "P1M"
    assert fd["granularity_sqla"] == "date"
    # OWN path keeps time grain in SQL (toStartOf*), not form_data
    own = build_form_data(chart, dataset_id=1, time_column="date")
    assert "time_grain_sqla" not in own


def test_form_data_source_ratio_is_sql_expression() -> None:
    ratio = Measure(
        column="revenue",
        agg=Aggregation.SUM,
        denominator=Measure(column="orders", agg=Aggregation.SUM),
    )
    fd = build_form_data(
        _chart(Viz.LINE, dimensions=["date"], measures=[ratio]), 1, from_source=True
    )
    expr = fd["metrics"][0]["sqlExpression"]
    assert 'SUM("revenue")' in expr
    assert 'SUM("orders")' in expr
    assert "NULLIF" in expr


def test_form_data_from_source_uses_unique_joined_dim_alias() -> None:
    """Finding 2: SOURCE form_data groups by stores_name / products_name, not bare name."""
    from auto_bi.ir.spec import JoinSpec

    stores = JoinSpec(table="dm.stores", on_left="dm.sales_daily.store_id", on_right="dm.stores.id")
    products = JoinSpec(
        table="dm.products", on_left="dm.sales_daily.product_id", on_right="dm.products.id"
    )
    by_store = ChartSpec(
        id="by_store",
        title="s",
        viz=Viz.BAR,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["dm.stores.name"],
            measures=[Measure(column="revenue", agg=Aggregation.SUM)],
            joins=[stores],
        ),
    )
    by_product = ChartSpec(
        id="by_product",
        title="p",
        viz=Viz.BAR,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["dm.products.name"],
            measures=[Measure(column="revenue", agg=Aggregation.SUM)],
            joins=[products],
        ),
    )
    assert build_form_data(by_store, 1, from_source=True)["x_axis"] == "stores_name"
    assert build_form_data(by_product, 1, from_source=True)["x_axis"] == "products_name"
    assert build_form_data(by_store, 1, from_source=False)["x_axis"] == "name"


def test_form_data_time_column_sets_granularity() -> None:
    # B5: a timeseries chart passed its temporal column names it as granularity_sqla so a
    # dashboard native time filter's time_range binds to it (else the ECharts query names no
    # time column and the preset period silently fails to re-scope the chart)
    chart = make_spec().charts[1]
    fd = build_form_data(chart, dataset_id=42, time_column="date")
    assert fd["granularity_sqla"] == "date"
    # a categorical bar has no temporal column, so the adapter passes time_column=None -> unset
    bar = build_form_data(_chart(Viz.BAR, dimensions=["store_id"]), dataset_id=42)
    assert "granularity_sqla" not in bar


def test_temporal_alias_resolves_time_role() -> None:
    adapter = make_adapter(FakeSuperset(), model=MODEL)
    # the line groups by `date` (role=TIME in the model) -> its bare alias
    line = make_spec().charts[1]
    assert adapter._temporal_alias(line.query) == "date"
    # a KPI (no dimensions) and a categorical breakdown have no temporal column
    assert adapter._temporal_alias(make_spec().charts[0].query) is None
    assert adapter._temporal_alias(_chart(Viz.BAR, dimensions=["store_id"]).query) is None
    # without a model the adapter can't judge column roles -> None (no spurious binding)
    assert make_adapter(FakeSuperset())._temporal_alias(line.query) is None


def test_form_data_big_number() -> None:
    chart = make_spec().charts[0]
    fd = build_form_data(chart, dataset_id=42)
    assert fd["viz_type"] == "big_number_total"
    assert fd["metric"]["sqlExpression"] == 'MAX("Выручка")'
    assert "metrics" not in fd


def test_form_data_compacts_large_aggregates() -> None:
    # dashboard-craft §4: a fact sum reaches billions; show it abbreviated (236G), not the raw
    # 12-digit number that overflows a big_number tile / collides on an axis. SUM/COUNT only.
    assert build_form_data(_chart(Viz.BIG_NUMBER), dataset_id=1)["y_axis_format"] == ".3~s"
    assert build_form_data(_chart(Viz.LINE, dimensions=["date"]), 1)["y_axis_format"] == ".3~s"
    assert build_form_data(_chart(Viz.PIE, dimensions=["store_id"]), 1)["number_format"] == ".3~s"
    table = build_form_data(_chart(Viz.TABLE, dimensions=["store_id"]), dataset_id=1)
    assert table["column_config"]["sum_revenue"]["d3NumberFormat"] == ".3~s"


def test_form_data_keeps_full_precision_for_averages() -> None:
    # an average check is 3614, not "3.6k" — only additive aggregates (SUM/COUNT) compact (§4)
    avg = _chart(Viz.BIG_NUMBER, measures=[Measure(column="check", agg=Aggregation.AVG)])
    assert "y_axis_format" not in build_form_data(avg, dataset_id=1)
    avg_table = _chart(
        Viz.TABLE, dimensions=["store_id"], measures=[Measure(column="check", agg=Aggregation.AVG)]
    )
    assert "column_config" not in build_form_data(avg_table, dataset_id=1)


def test_form_data_percent_kpi_moves_percent_to_the_subheader_line() -> None:
    # the KPI row reads as one format: value line + unit line on every tile. A percent
    # KPI therefore renders "1.5" over "%" (metric ×100 in SQL, since d3 "%" would both
    # multiply and glue the sign into the headline); ".1~f" keeps the .1% precision and
    # trims a trailing zero ("34", not "34.0").
    from auto_bi.ir.spec import MeasureTransform

    pct = Measure(column="revenue", agg=Aggregation.SUM, transform=MeasureTransform.POP_PCT)
    fd = build_form_data(_chart(Viz.BIG_NUMBER, measures=[pct]), dataset_id=1)
    assert fd["metric"]["sqlExpression"].endswith("* 100")
    assert fd["subheader"] == "%"
    assert fd["y_axis_format"] == ".1~f"


def test_form_data_big_number_pins_equal_font_proportions() -> None:
    # all KPI tiles share the same value/unit font proportions regardless of scale branch
    from auto_bi.ir.spec import MeasureTransform

    pct = Measure(column="revenue", agg=Aggregation.SUM, transform=MeasureTransform.POP_PCT)
    for fd in (
        build_form_data(_chart(Viz.BIG_NUMBER), dataset_id=1),
        build_form_data(_chart(Viz.BIG_NUMBER), dataset_id=1, kpi_scale=(1e9, "млрд ₽", 236.1)),
        build_form_data(_chart(Viz.BIG_NUMBER, measures=[pct]), dataset_id=1),
    ):
        assert (fd["header_font_size"], fd["subheader_font_size"]) == (0.4, 0.15)


def test_form_data_percent_format_for_ratio_transforms() -> None:
    from auto_bi.ir.spec import MeasureTransform

    pct = Measure(column="revenue", agg=Aggregation.SUM, transform=MeasureTransform.POP_PCT)
    line = _chart(Viz.LINE, dimensions=["date"], measures=[pct])
    assert build_form_data(line, dataset_id=1)["y_axis_format"] == ".1%"
    # a table mixes a compact sum and a percent share, formatted per-column
    share = Measure(
        column="revenue", agg=Aggregation.SUM, transform=MeasureTransform.SHARE_OF_TOTAL
    )
    table = _chart(
        Viz.TABLE,
        dimensions=["store_id"],
        measures=[Measure(column="revenue", agg=Aggregation.SUM, label="Выручка"), share],
    )
    cfg = build_form_data(table, dataset_id=1)["column_config"]
    assert cfg["Выручка"]["d3NumberFormat"] == ".3~s"
    assert cfg["share_of_total_sum_revenue"]["d3NumberFormat"] == ".1%"


def test_form_data_escapes_malicious_label() -> None:
    # an LLM-controlled label must not break out of SUM("...") and inject SQL (F1)
    evil = Measure(column="revenue", agg=Aggregation.SUM, label='x") FROM system.numbers --')
    chart = ChartSpec(
        id="evil",
        title="bad",
        viz=Viz.BIG_NUMBER,
        query=ChartQuery(table="dm.sales_daily", measures=[evil]),
    )
    fd = build_form_data(chart, dataset_id=1)
    expr = fd["metric"]["sqlExpression"]
    # the inner quote is doubled (escaped), so the whole label stays one quoted identifier
    assert expr == 'MAX("x"") FROM system.numbers --")'
    # display label keeps the raw text; only the SQL identifier is escaped
    assert fd["metric"]["label"] == 'x") FROM system.numbers --'


def _chart(viz: Viz, **query_kwargs) -> ChartSpec:
    query_kwargs.setdefault("measures", [Measure(column="revenue", agg=Aggregation.SUM)])
    return ChartSpec(
        id="c", title="c", viz=viz, query=ChartQuery(table="dm.sales_daily", **query_kwargs)
    )


def test_form_data_pie() -> None:
    fd = build_form_data(_chart(Viz.PIE, dimensions=["store_id"]), dataset_id=1)
    assert fd["viz_type"] == "pie"
    assert fd["groupby"] == ["store_id"]
    assert fd["metric"]["sqlExpression"] == 'SUM("sum_revenue")'
    assert "metrics" not in fd


def test_form_data_table_groups_all_roles() -> None:
    fd = build_form_data(_chart(Viz.TABLE, dimensions=["date", "store_id"]), dataset_id=1)
    assert fd["viz_type"] == "table"
    assert fd["query_mode"] == "aggregate"
    assert fd["groupby"] == ["date", "store_id"]


def test_form_data_pivot() -> None:
    fd = build_form_data(_chart(Viz.PIVOT, rows=["store_id"], columns=["manager_id"]), dataset_id=1)
    assert fd["viz_type"] == "pivot_table_v2"
    assert fd["groupbyRows"] == ["store_id"]
    assert fd["groupbyColumns"] == ["manager_id"]
    assert fd["aggregateFunction"] == "Sum"


def test_form_data_heatmap() -> None:
    fd = build_form_data(_chart(Viz.HEATMAP, dimensions=["date", "store_id"]), dataset_id=1)
    assert fd["viz_type"] == "heatmap_v2"
    assert fd["x_axis"] == "date"
    assert fd["groupby"] == "store_id"
    assert fd["metric"]["sqlExpression"] == 'SUM("sum_revenue")'


def test_form_data_heatmap_y_pad_makes_adhoc_groupby() -> None:
    # upstream #33105: numeric 0 on the y-axis renders as <NULL>; the padded adhoc
    # column ("00".."23") fixes the label and the alpha order at once
    chart = _chart(Viz.HEATMAP, dimensions=["cohort_month", "months_since"])
    fd = build_form_data(chart, dataset_id=1, heatmap_y_pad=2)
    assert fd["groupby"] == {
        "expressionType": "SQL",
        "sqlExpression": "LPAD(CAST(\"months_since\" AS VARCHAR), 2, '0')",
        "label": "months_since",
    }
    # without the hint the plain column stays (id-like axes keep natural labels)
    plain = build_form_data(chart, dataset_id=1)
    assert plain["groupby"] == "months_since"


def _cohort_model() -> SemanticModel:
    return SemanticModel.model_validate(
        {
            "tables": [
                {
                    "name": "dm.cohort_retention",
                    "columns": [
                        {"name": "cohort_month", "type": "Date", "role": "time"},
                        {"name": "months_since", "type": "UInt16", "role": "dimension"},
                        {"name": "label", "type": "String", "role": "dimension"},
                        {"name": "customers", "type": "UInt64", "role": "measure", "agg": "sum"},
                    ],
                    "physical": {
                        "engine": "clickhouse",
                        "cardinality": {"months_since": 24, "label": 24},
                    },
                }
            ]
        }
    )


def test_heatmap_y_pad_only_for_small_numeric_dimension() -> None:
    def _heatmap(y: str, table: str = "dm.cohort_retention") -> ChartSpec:
        return ChartSpec(
            id="h",
            title="h",
            viz=Viz.HEATMAP,
            query=ChartQuery(
                table=table,
                dimensions=["cohort_month", y],
                measures=[Measure(column="customers", agg=Aggregation.SUM)],
            ),
        )

    adapter = make_adapter(FakeSuperset(), model=_cohort_model())
    assert adapter._heatmap_y_pad(_heatmap("months_since")) == 2  # 24 ordinals -> width 2
    assert adapter._heatmap_y_pad(_heatmap("label")) is None  # non-numeric type
    # id-like axis: cardinality above the ordinal threshold -> natural labels kept
    sales = make_adapter(FakeSuperset(), model=MODEL)
    big = ChartSpec(
        id="h2",
        title="h2",
        viz=Viz.HEATMAP,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["date", "store_id"],
            measures=[Measure(column="revenue", agg=Aggregation.SUM)],
        ),
    )
    assert sales._heatmap_y_pad(big) is None  # store_id cardinality 4200 > 100
    assert sales._heatmap_y_pad(_chart(Viz.BAR, dimensions=["store_id"])) is None  # not a heatmap
    assert make_adapter(FakeSuperset())._heatmap_y_pad(big) is None  # no model


def test_form_data_stacked_bar_sets_stack_and_series() -> None:
    fd = build_form_data(
        _chart(Viz.STACKED_BAR, dimensions=["date"], series=["store_id"]), dataset_id=1
    )
    assert fd["viz_type"] == "echarts_timeseries_bar"
    assert fd["stack"] == "Stack"
    assert fd["groupby"] == ["store_id"]


def test_form_data_bar_forces_categorical_axis() -> None:
    # a numeric x (store_id) otherwise renders on a continuous value axis:
    # thin bars at numeric positions — the dashboard-6 "фигня" bug
    fd = build_form_data(_chart(Viz.BAR, dimensions=["store_id"]), dataset_id=1)
    assert fd["xAxisForceCategorical"] is True
    line = build_form_data(_chart(Viz.LINE, dimensions=["date"]), dataset_id=1)
    assert "xAxisForceCategorical" not in line  # lines keep the time/value axis


def test_form_data_temporal_bar_keeps_time_axis_not_categorical() -> None:
    # a bar over a TIME column (e.g. cohort month) must NOT be forced categorical — forcing it
    # makes ECharts print the raw epoch-ms of each bucket (the cohort dashboard showed
    # 1769904000000 instead of "июл 2024"); it keeps the time axis with an explicit date format
    fd = build_form_data(_chart(Viz.BAR, dimensions=["date"]), dataset_id=1, time_column="date")
    assert "xAxisForceCategorical" not in fd
    assert fd["x_axis_time_format"] == "smart_date"
    assert fd["granularity_sqla"] == "date"
    # a numeric non-temporal bar (store_id) is still forced categorical, gets no date format
    num = build_form_data(_chart(Viz.BAR, dimensions=["store_id"]), dataset_id=1)
    assert num["xAxisForceCategorical"] is True
    assert "x_axis_time_format" not in num


def test_form_data_bar_horizontal_orientation() -> None:
    # a categorical ranking renders horizontally so long RU labels get the full row width
    # (the adapter computes the flag via is_horizontal_bar; build_form_data just honors it)
    bar = _chart(Viz.BAR, dimensions=["store_id"])
    assert build_form_data(bar, dataset_id=1, horizontal=True)["orientation"] == "horizontal"
    assert "orientation" not in build_form_data(bar, dataset_id=1)  # default vertical


def test_form_data_bar_top_n_sorts_by_the_ordering_measure() -> None:
    top = _chart(
        Viz.BAR,
        dimensions=["store_id"],
        measures=[Measure(column="revenue", agg=Aggregation.SUM, label="Выручка")],
        order_by=[OrderBy(by="Выручка", dir="desc")],
        limit=10,
    )
    fd = build_form_data(top, dataset_id=1)
    assert fd["x_axis_sort"] == "Выручка"
    assert fd["x_axis_sort_asc"] is False
    # ordered by the dimension (bar over dates) -> chronology stays, no metric sort
    by_x = _chart(Viz.BAR, dimensions=["date"], order_by=[OrderBy(by="date", dir="asc")])
    fd = build_form_data(by_x, dataset_id=1)
    assert "x_axis_sort" not in fd
    assert fd["x_axis_sort_asc"] is True
    # series breakdown -> superset has no sort control there, keep the default
    split = _chart(
        Viz.BAR,
        dimensions=["store_id"],
        series=["format"],
        order_by=[OrderBy(by="sum_revenue", dir="desc")],
    )
    fd = build_form_data(split, dataset_id=1)
    assert "x_axis_sort" not in fd


def test_form_data_bar_sort_targets_the_humanized_metric_label() -> None:
    # superset matches x_axis_sort against the metric LABEL; once the legend is humanized the
    # sort key must be the display name too, or superset silently falls back to alphabetical
    # (the regression the human-legends change introduced)
    bar = _chart(
        Viz.BAR,
        dimensions=["store_id"],
        measures=[Measure(column="revenue", agg=Aggregation.SUM)],  # no label -> alias sum_revenue
        order_by=[OrderBy(by="sum_revenue", dir="desc")],
    )
    fd = build_form_data(bar, dataset_id=1, metric_labels={"sum_revenue": "Выручка"})
    assert fd["metrics"][0]["label"] == "Выручка"
    assert fd["x_axis_sort"] == "Выручка"  # matches the humanized legend, not "sum_revenue"
    # no humanization -> the alias is both the legend and the sort key (unchanged)
    assert build_form_data(bar, dataset_id=1)["x_axis_sort"] == "sum_revenue"


def test_form_data_horizontal_bar_inverts_sort_direction() -> None:
    # echarts renders a horizontal bar's category[0] at the BOTTOM, so a desc spec must sort
    # ascending to put the largest bar at the TOP (dashboard-craft §5 "крупнейший первый")
    bar = _chart(
        Viz.BAR,
        dimensions=["store_id"],
        measures=[Measure(column="revenue", agg=Aggregation.SUM, label="Выручка")],
        order_by=[OrderBy(by="Выручка", dir="desc")],
    )
    assert build_form_data(bar, dataset_id=1, horizontal=True)["x_axis_sort_asc"] is True
    assert build_form_data(bar, dataset_id=1)["x_axis_sort_asc"] is False  # vertical: desc as-is


def test_form_data_area_stacks_only_with_series() -> None:
    plain = build_form_data(_chart(Viz.AREA, dimensions=["date"]), dataset_id=1)
    assert plain["viz_type"] == "echarts_area"
    assert "stack" not in plain
    stacked = build_form_data(
        _chart(Viz.AREA, dimensions=["date"], series=["store_id"]), dataset_id=1
    )
    assert stacked["stack"] == "Stack"


def test_form_data_line_merges_series_and_extra_dims_deduped() -> None:
    fd = build_form_data(
        _chart(Viz.LINE, dimensions=["date", "city"], series=["city", "format"]), dataset_id=1
    )
    assert fd["x_axis"] == "date"
    assert fd["groupby"] == ["city", "format"]


def test_form_data_bar_extra_dims_go_to_groupby() -> None:
    chart = ChartSpec(
        id="b",
        title="bar",
        viz=Viz.BAR,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["city", "format"],
            measures=[Measure(column="revenue", agg=Aggregation.SUM)],
        ),
    )
    fd = build_form_data(chart, dataset_id=1)
    assert fd["x_axis"] == "city"
    assert fd["groupby"] == ["format"]


# --- RU KPI scale + humanized legends (build_form_data knobs) ----------------


def test_ru_kpi_scale_tiers() -> None:
    # a large ruble headline scales to a whole figure + its RU magnitude word (§5 "Числа")
    assert ru_kpi_scale(2.3e12) == (1e12, "трлн")
    assert ru_kpi_scale(236e9) == (1e9, "млрд")
    assert ru_kpi_scale(115e6) == (1e6, "млн")
    assert ru_kpi_scale(5_000) == (1e3, "тыс")
    # below 1e3 the figure is small enough to show in full -> no scaling, no unit line
    assert ru_kpi_scale(500) == (1.0, "")
    # magnitude is chosen on the absolute value (a negative delta scales the same)
    assert ru_kpi_scale(-236e9) == (1e9, "млрд")


def test_form_data_big_number_ru_scale() -> None:
    # kpi_scale divides the metric and moves the RU unit to the (smaller) subheader line, so the
    # tile reads "236" / "млрд ₽" instead of the d3 SI "236G"
    fd = build_form_data(_chart(Viz.BIG_NUMBER), dataset_id=1, kpi_scale=(1e9, "млрд ₽", 236.1))
    assert fd["metric"]["sqlExpression"] == '(MAX("sum_revenue")) / 1000000000'
    assert fd["subheader"] == "млрд ₽"
    assert fd["y_axis_format"] == ",.0f"


def test_form_data_big_number_keeps_a_decimal_in_the_1_10_band() -> None:
    # L-1: a whole-number headline in the 1–10 band loses up to a third of the figure
    # (1,5 млрд -> "2 млрд") -> one decimal there; from 10 up the round figure is fine
    band = build_form_data(_chart(Viz.BIG_NUMBER), dataset_id=1, kpi_scale=(1e9, "млрд ₽", 1.5))
    assert band["y_axis_format"] == ",.1f"
    assert band["subheader"] == "млрд ₽"


def test_form_data_big_number_scale_absent_or_trivial_keeps_default() -> None:
    # no kpi_scale => the old compact format, unscaled metric, empty subheader
    plain = build_form_data(_chart(Viz.BIG_NUMBER), dataset_id=1)
    assert plain["metric"]["sqlExpression"] == 'MAX("sum_revenue")'
    assert plain["subheader"] == ""
    assert plain["y_axis_format"] == ".3~s"
    # a divisor of 1 (figure below 1e3) is ignored -> default format, no subheader unit
    trivial = build_form_data(_chart(Viz.BIG_NUMBER), dataset_id=1, kpi_scale=(1.0, "", 500.0))
    assert trivial["metric"]["sqlExpression"] == 'MAX("sum_revenue")'
    assert trivial["subheader"] == ""


def test_form_data_metric_labels_humanize_legend_but_keep_sql_alias() -> None:
    # a measure with no explicit label keeps its technical alias in SQL, but the legend/tooltip
    # reads the human name passed in metric_labels (display and column decoupled)
    line = _chart(Viz.LINE, dimensions=["date"])
    fd = build_form_data(line, dataset_id=1, metric_labels={"sum_revenue": "Выручка"})
    assert fd["metrics"][0]["label"] == "Выручка"
    assert fd["metrics"][0]["sqlExpression"] == 'SUM("sum_revenue")'  # SQL still by alias
    # absent mapping => the alias is the display name (unchanged behavior)
    assert build_form_data(line, dataset_id=1)["metrics"][0]["label"] == "sum_revenue"


def test_form_data_table_column_config_keyed_by_human_label() -> None:
    # the per-column format must land on the DISPLAY column name when the legend is humanized
    table = _chart(Viz.TABLE, dimensions=["store_id"])
    cfg = build_form_data(table, dataset_id=1, metric_labels={"sum_revenue": "Выручка"})[
        "column_config"
    ]
    assert "Выручка" in cfg
    assert "sum_revenue" not in cfg
    assert cfg["Выручка"]["d3NumberFormat"] == ".3~s"


def test_form_data_axis_scale_puts_ru_unit_on_the_value_axis_title() -> None:
    # d3 SI only speaks k/M/G/T, so the value axis is scaled and the RU unit goes on its title
    line = _chart(Viz.LINE, dimensions=["date"])
    fd = build_form_data(line, dataset_id=1, axis_scale=(1e9, "млрд ₽", 14.0))
    assert fd["metrics"][0]["sqlExpression"] == '(SUM("sum_revenue")) / 1000000000'
    assert fd["y_axis_format"] == ",.1f"  # plain scaled number, not the d3 SI ".3~s"
    assert fd["y_axis_title"] == "млрд ₽"  # y_axis_title is the measure axis (Y here)
    # a horizontal bar flips the value axis to the bottom visually, but superset still models it
    # as y_axis_title (x_axis_title would land on the category axis)
    bar = build_form_data(
        _chart(Viz.BAR, dimensions=["store_id"]),
        dataset_id=1,
        horizontal=True,
        axis_scale=(1e9, "млрд ₽", 14.0),
    )
    assert bar["y_axis_title"] == "млрд ₽"
    assert "x_axis_title" not in bar
    # no axis_scale -> the d3 SI compact format, unscaled metric (unchanged behavior)
    plain = build_form_data(line, dataset_id=1)
    assert plain["y_axis_format"] == ".3~s"
    assert "/ 1000000000" not in plain["metrics"][0]["sqlExpression"]


# --- position_json ----------------------------------------------------------


def test_position_json_grid() -> None:
    spec = make_spec()
    position = build_position_json(spec, [(spec.charts[0], 201), (spec.charts[1], 202)])
    assert position["DASHBOARD_VERSION_KEY"] == "v2"
    assert position["GRID_ID"]["children"] == ["ROW-auto_bi_0", "ROW-auto_bi_1"]
    kpi = position["CHART-auto_bi_kpi"]
    assert kpi["meta"]["chartId"] == 201
    assert kpi["meta"]["width"] == 4
    assert kpi["meta"]["height"] == 2 * 12
    assert kpi["id"] in position["ROW-auto_bi_0"]["children"]


def _bar(cid: str, w: int, row: int) -> ChartSpec:
    return ChartSpec(
        id=cid,
        title=cid,
        viz=Viz.BAR,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["store_id"],
            measures=[Measure(column="revenue", agg=Aggregation.SUM)],
        ),
        layout_hint=LayoutHint(w=w, h=4, row=row),
    )


def test_position_json_wraps_on_overflow() -> None:
    # three 6-wide charts in one hint-row: 6+6=12 fit, the third wraps to a new row
    charts = [_bar("a", 6, 0), _bar("b", 6, 0), _bar("c", 6, 0)]
    pos = build_position_json(make_spec(), [(c, 200 + i) for i, c in enumerate(charts)])
    rows = pos["GRID_ID"]["children"]
    assert rows == ["ROW-auto_bi_0", "ROW-auto_bi_1"]
    assert pos["ROW-auto_bi_0"]["children"] == ["CHART-auto_bi_a", "CHART-auto_bi_b"]
    assert pos["ROW-auto_bi_1"]["children"] == ["CHART-auto_bi_c"]


def test_position_json_distinct_hint_rows_split() -> None:
    # different layout_hint.row values always start a new physical row, even when narrow
    charts = [_bar("a", 4, 0), _bar("b", 4, 2)]
    pos = build_position_json(make_spec(), [(c, 300 + i) for i, c in enumerate(charts)])
    assert pos["GRID_ID"]["children"] == ["ROW-auto_bi_0", "ROW-auto_bi_1"]
    assert pos["ROW-auto_bi_0"]["children"] == ["CHART-auto_bi_a"]
    assert pos["ROW-auto_bi_1"]["children"] == ["CHART-auto_bi_b"]


# --- adapter flow ------------------------------------------------------------


def test_ensure_database_idempotent() -> None:
    fake = FakeSuperset(existing_databases=[{"id": 7, "database_name": "Auto_BI ClickHouse"}])
    ref = make_adapter(fake).ensure_database()
    assert ref.id == 7
    assert not any(m == "POST" and p == "/api/v1/database/" for m, p, _ in fake.requests)


def test_database_created_with_clickhouse_uri() -> None:
    fake = FakeSuperset()
    make_adapter(fake).ensure_database()
    body = next(b for m, p, b in fake.requests if m == "POST" and p == "/api/v1/database/")
    assert body["sqlalchemy_uri"] == "clickhousedb://ro:pw@ch:8123/dm"


def test_build_full_flow() -> None:
    fake = FakeSuperset()
    dashboard = make_adapter(fake).build(make_spec())

    chart_posts = [b for m, p, b in fake.requests if m == "POST" and p == "/api/v1/chart/"]
    assert [c["viz_type"] for c in chart_posts] == ["big_number_total", "echarts_timeseries_line"]
    params = json.loads(chart_posts[0]["params"])
    assert params["viz_type"] == "big_number_total"

    dataset_posts = [b for m, p, b in fake.requests if m == "POST" and p == "/api/v1/dataset/"]
    assert all("SELECT" in b["sql"] for b in dataset_posts)
    assert dataset_posts[0]["table_name"].startswith("auto_bi__")

    dash_post = next(b for m, p, b in fake.requests if m == "POST" and p == "/api/v1/dashboard/")
    # KPI tiles center as one visual row — the alignment knob is dashboard CSS, not form_data
    from auto_bi.adapters.superset.adapter import KPI_CENTER_CSS

    assert dash_post["css"] == KPI_CENTER_CSS
    position = json.loads(dash_post["position_json"])
    chart_ids = {
        node["meta"]["chartId"] for node in position.values()
        if isinstance(node, dict) and node.get("type") == "CHART"
    }  # fmt: skip
    link_puts = [
        (p, b) for m, p, b in fake.requests if m == "PUT" and p.startswith("/api/v1/chart/")
    ]
    assert {b["dashboards"][0] for _, b in link_puts} == {dashboard.id}
    assert chart_ids == {int(p.rsplit("/", 1)[1]) for p, _ in link_puts}
    assert dashboard.url == f"/superset/dashboard/{dashboard.id}/"


def test_build_source_vs_own_dataset_roles() -> None:
    """D-1: SOURCE charts share one source dataset; OWN keeps a per-chart aggregated one."""
    from auto_bi.ir.spec import MeasureTransform

    revenue = Measure(column="revenue", agg=Aggregation.SUM)
    share = Measure(
        column="revenue", agg=Aggregation.SUM, transform=MeasureTransform.SHARE_OF_TOTAL
    )
    spec = DashboardSpec(
        title="roles",
        charts=[
            ChartSpec(
                id="kpi",
                title="KPI",
                viz=Viz.BIG_NUMBER,
                query=ChartQuery(table="dm.sales_daily", measures=[revenue]),
                layout_hint=LayoutHint(w=4, h=2, row=0),
            ),
            ChartSpec(
                id="trend",
                title="Trend",
                viz=Viz.LINE,
                query=ChartQuery(table="dm.sales_daily", dimensions=["date"], measures=[revenue]),
                layout_hint=LayoutHint(w=8, h=4, row=1),
            ),
            ChartSpec(
                id="share",
                title="Share",
                viz=Viz.BAR,
                query=ChartQuery(table="dm.sales_daily", dimensions=["store_id"], measures=[share]),
                layout_hint=LayoutHint(w=6, h=4, row=2),
            ),
        ],
    )
    fake = FakeSuperset()
    make_adapter(fake, model=MODEL).build(spec)
    dataset_posts = [b for m, p, b in fake.requests if m == "POST" and p == "/api/v1/dataset/"]
    # one shared source + one OWN (share) dataset
    assert len(dataset_posts) == 2
    source = next(b for b in dataset_posts if "source" in b["table_name"])
    own = next(b for b in dataset_posts if "source" not in b["table_name"])
    # source SQL: no GROUP BY / WHERE / LIMIT
    assert "GROUP BY" not in source["sql"].upper()
    assert "WHERE" not in source["sql"].upper()
    assert "LIMIT" not in source["sql"].upper()
    # OWN still aggregated
    assert "GROUP BY" in own["sql"].upper() or "share" in own["sql"].lower() or "SUM" in own["sql"]

    chart_posts = [b for m, p, b in fake.requests if m == "POST" and p == "/api/v1/chart/"]
    by_name = {c["slice_name"]: json.loads(c["params"]) for c in chart_posts}
    # SOURCE charts share the same datasource id (the source dataset)
    assert by_name["KPI"]["datasource"] == by_name["Trend"]["datasource"]
    assert by_name["Share"]["datasource"] != by_name["KPI"]["datasource"]
    # SOURCE form_data aggregates raw column; OWN re-aggregates the measure alias
    assert 'SUM("revenue")' in by_name["KPI"]["metric"]["sqlExpression"]
    assert (
        "share_of_total" in by_name["Share"]["metrics"][0]["sqlExpression"]
        or "SUM(" in by_name["Share"]["metrics"][0]["sqlExpression"]
    )


def test_build_drains_all_four_artifact_kinds() -> None:
    # ownership ledger (P0-2 criterion 4): build() records every BI entity it creates on the
    # concrete adapter; drain_build_artifacts returns them (NOT a BIAdapter Protocol method).
    spec = make_spec()
    fake = FakeSuperset()
    adapter = make_adapter(fake, model=MODEL)
    adapter.set_artifact_namespace("sess:abc")
    dashboard = adapter.build(spec)

    arts = adapter.drain_build_artifacts()
    by_kind: dict[str, list] = {}
    for a in arts:
        by_kind.setdefault(a.kind, []).append(a)
    # one database, one shared source dataset for the mart (both charts are SOURCE), one
    # chart per spec chart, one dashboard — D-1 no longer creates one dataset per chart
    assert len(by_kind["database"]) == 1
    assert len(by_kind["dataset"]) == 1
    assert len(by_kind["chart"]) == len(spec.charts)
    assert len(by_kind["dashboard"]) == 1
    assert by_kind["database"][0].name == "Auto_BI ClickHouse"
    # native ids are stringified; the dashboard's matches the returned ref
    assert all(isinstance(a.native_id, str) for a in arts)
    assert by_kind["dashboard"][0].native_id == str(dashboard.id)
    # schema_set carries the DWH schema.table for datasets/charts, None for db/dashboard
    assert all(a.schema_set == "dm.sales_daily" for a in by_kind["dataset"])
    assert all(a.schema_set == "dm.sales_daily" for a in by_kind["chart"])
    assert by_kind["database"][0].schema_set is None
    assert by_kind["dashboard"][0].schema_set is None
    # the dataset technical name is display/debug only (carries the P0-2 namespace fingerprint)
    assert by_kind["dataset"][0].name.startswith("auto_bi__")
    assert "source" in by_kind["dataset"][0].name
    # draining clears the buffer -> a second drain is empty (no double-report)
    assert adapter.drain_build_artifacts() == []


def test_dataset_names_unique_even_when_slugs_collide() -> None:
    from auto_bi.adapters.superset.adapter import _dataset_name, _slug

    # two chart ids that slugify to the same string still get distinct dataset names (F7)
    a = _dataset_name("Обзор", "chart-a")
    b = _dataset_name("Обзор", "chart!a")  # same slug "chart_a", different raw id
    assert _slug("chart-a") == _slug("chart!a")
    assert a != b


def test_assemble_rejects_ref_mismatch() -> None:
    adapter = make_adapter(FakeSuperset())
    with pytest.raises(ValueError, match="chart refs"):
        adapter.assemble_dashboard(make_spec(), charts=[])


# --- KPI magnitude + humanized legends + currency (model-backed) -------------


def _bignum(measure: Measure) -> ChartSpec:
    return ChartSpec(
        id="kpi",
        title="Итог",
        viz=Viz.BIG_NUMBER,
        query=ChartQuery(table="dm.sales_daily", measures=[measure]),
    )


def test_human_label_prefers_explicit_then_short_description() -> None:
    adapter = make_adapter(FakeSuperset(), model=MODEL)
    labeled = Measure(column="revenue", agg=Aggregation.SUM, label="Итоговая выручка")
    assert adapter._human_label(labeled, "dm.sales_daily") == "Итоговая выручка"
    # no label -> short form of the model description ("Выручка, руб" -> "Выручка")
    bare = Measure(column="revenue", agg=Aggregation.SUM)
    assert adapter._human_label(bare, "dm.sales_daily") == "Выручка"
    # a description with no separator is used whole ("Число заказов")
    orders = Measure(column="orders", agg=Aggregation.SUM)
    assert adapter._human_label(orders, "dm.sales_daily") == "Число заказов"


def test_human_label_none_without_model() -> None:
    adapter = make_adapter(FakeSuperset())  # no model
    assert (
        adapter._human_label(Measure(column="revenue", agg=Aggregation.SUM), "dm.sales_daily")
        is None
    )


def test_metric_labels_maps_alias_to_human_name() -> None:
    adapter = make_adapter(FakeSuperset(), model=MODEL)
    chart = _chart(Viz.LINE, dimensions=["date"])  # revenue measure, no label -> alias sum_revenue
    assert adapter._metric_labels(chart) == {"sum_revenue": "Выручка"}


def test_measure_currency_money_vs_count() -> None:
    adapter = make_adapter(FakeSuperset(), model=MODEL)
    # revenue reads as money in the model ("Выручка, руб") -> ₽
    assert (
        adapter._measure_currency(Measure(column="revenue", agg=Aggregation.SUM), "dm.sales_daily")
        == "₽"
    )
    # a count ("Число заказов") gets no spurious currency sign
    assert (
        adapter._measure_currency(Measure(column="orders", agg=Aggregation.SUM), "dm.sales_daily")
        == ""
    )


def test_kpi_scale_large_ruble_measures_magnitude_and_unit() -> None:
    adapter = make_adapter(FakeSuperset(kpi_value=236e9), model=MODEL)
    scale = adapter._kpi_scale(
        _bignum(Measure(column="revenue", agg=Aggregation.SUM)), DatasetRef(id=42, name="t")
    )
    assert scale == (1e9, "млрд ₽", 236.0)


def test_kpi_scale_count_has_no_currency() -> None:
    adapter = make_adapter(FakeSuperset(kpi_value=115e6), model=MODEL)
    scale = adapter._kpi_scale(
        _bignum(Measure(column="orders", agg=Aggregation.SUM)), DatasetRef(id=42, name="t")
    )
    assert scale == (1e6, "млн", 115.0)  # count -> unit word only, no ₽


def test_kpi_scale_none_for_percent_or_non_bignumber() -> None:
    adapter = make_adapter(FakeSuperset(kpi_value=236e9), model=MODEL)
    ds = DatasetRef(id=42, name="t")
    # a percent transform is never SI-compacted -> no RU scaling
    pct = Measure(column="revenue", agg=Aggregation.SUM, transform=MeasureTransform.SHARE_OF_TOTAL)
    assert adapter._kpi_scale(_bignum(pct), ds) is None
    # not a big_number -> no scaling
    line = _chart(Viz.LINE, dimensions=["date"])
    assert adapter._kpi_scale(line, ds) is None


def test_measure_magnitude_best_effort_returns_none_on_no_rows() -> None:
    # the probe finds no data (or fails) -> None, and the chart silently keeps its default format
    adapter = make_adapter(FakeSuperset(kpi_value=None), model=MODEL)
    measure = Measure(column="revenue", agg=Aggregation.SUM)
    assert adapter._measure_magnitude(DatasetRef(id=42, name="t"), measure) is None
    assert adapter._kpi_scale(_bignum(measure), DatasetRef(id=42, name="t")) is None


def test_measure_magnitude_from_source_probes_raw_column_agg() -> None:
    """Finding 3: SOURCE KPI probe uses SUM(\"revenue\"), never MAX(\"sum_revenue\")."""
    fake = FakeSuperset(kpi_value=236e9)
    adapter = make_adapter(fake, model=MODEL)
    measure = Measure(column="revenue", agg=Aggregation.SUM)
    kpi = _bignum(measure)
    scale = adapter._kpi_scale(kpi, DatasetRef(id=42, name="t"), from_source=True)
    assert scale == (1e9, "млрд ₽", 236.0)
    probe = next(b for m, p, b in fake.requests if m == "POST" and p == "/api/v1/chart/data")
    sql = probe["queries"][0]["metrics"][0]["sqlExpression"]
    assert 'SUM("revenue")' in sql
    assert "sum_revenue" not in sql
    assert "MAX" not in sql
    assert "groupby" not in probe["queries"][0]


def test_measure_magnitude_from_source_grouped_orders_by_metric() -> None:
    """Finding 3: SOURCE line/bar probe groups by dims and takes the tallest point."""
    fake = FakeSuperset(kpi_value=14e9)
    adapter = make_adapter(fake, model=MODEL)
    line = _chart(Viz.LINE, dimensions=["date"])
    scale = adapter._axis_scale(line, DatasetRef(id=42, name="t"), from_source=True)
    assert scale == (1e9, "млрд ₽", 14.0)
    probe = next(b for m, p, b in fake.requests if m == "POST" and p == "/api/v1/chart/data")
    q = probe["queries"][0]
    assert 'SUM("revenue")' in q["metrics"][0]["sqlExpression"]
    assert "sum_revenue" not in q["metrics"][0]["sqlExpression"]
    assert q["groupby"] == ["date"]
    assert q["row_limit"] == 1
    assert q["orderby"][0][1] is False  # descending


def test_axis_scale_large_ruble_line_but_not_percent_or_kpi() -> None:
    adapter = make_adapter(FakeSuperset(kpi_value=14e9), model=MODEL)
    ds = DatasetRef(id=42, name="t")
    line = _chart(Viz.LINE, dimensions=["date"])  # compact revenue line -> scaled
    assert adapter._axis_scale(line, ds) == (1e9, "млрд ₽", 14.0)
    # a percent share bar renders as % on the axis -> never magnitude-scaled
    share = Measure(
        column="revenue", agg=Aggregation.SUM, transform=MeasureTransform.SHARE_OF_TOTAL
    )
    assert (
        adapter._axis_scale(_chart(Viz.BAR, dimensions=["store_id"], measures=[share]), ds) is None
    )
    # big_number is handled by _kpi_scale, not the axis path
    assert adapter._axis_scale(_bignum(Measure(column="revenue", agg=Aggregation.SUM)), ds) is None


def test_axis_scale_none_for_multi_measure_chart() -> None:
    # F-3: the divisor is tiered from one measure but would divide EVERY metric, so a
    # "revenue (billions) + orders (millions)" line would render orders in ruble-billions
    # units. Multi-measure charts keep the d3 SI default instead.
    adapter = make_adapter(FakeSuperset(kpi_value=14e9), model=MODEL)
    two_measures = _chart(
        Viz.LINE,
        dimensions=["date"],
        measures=[
            Measure(column="revenue", agg=Aggregation.SUM),
            Measure(column="orders", agg=Aggregation.SUM),
        ],
    )
    assert adapter._axis_scale(two_measures, DatasetRef(id=42, name="t")) is None


def test_build_full_flow_scales_ruble_kpi_and_humanizes_legend() -> None:
    fake = FakeSuperset(kpi_value=236e9)
    make_adapter(fake, model=MODEL).build(make_spec())
    chart_posts = [b for m, p, b in fake.requests if m == "POST" and p == "/api/v1/chart/"]
    kpi_params = json.loads(chart_posts[0]["params"])
    # the KPI headline is scaled to млрд with the RU unit on the subheader line
    assert kpi_params["subheader"] == "млрд ₽"
    assert "/ 1000000000" in kpi_params["metric"]["sqlExpression"]
    # the line chart legend reads the human measure name resolved from the model
    line_params = json.loads(chart_posts[1]["params"])
    assert line_params["metrics"][0]["label"] == "Выручка"
    # D-1 SOURCE: both the line and the KPI bind the mart's TIME column so a dashboard
    # time filter re-scopes them (KPI is multi-row under the shared source dataset)
    assert line_params["granularity_sqla"] == "date"
    assert kpi_params["granularity_sqla"] == "date"
    # SOURCE metrics aggregate the raw column, not the pre-computed measure alias
    assert 'SUM("revenue")' in kpi_params["metric"]["sqlExpression"]
    assert 'SUM("revenue")' in line_params["metrics"][0]["sqlExpression"]


# --- delete_artifact (ownership live-cleanup) -------------------------------


class FakeDeleteSuperset:
    """Login/CSRF plus a DELETE endpoint answering one fixed status; records DELETE paths."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.deletes: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/security/login":
            return httpx.Response(200, json={"access_token": "jwt"})
        if path == "/api/v1/security/csrf_token/":
            return httpx.Response(200, json={"result": "csrf"})
        if request.method == "DELETE":
            self.deletes.append(path)
            if self.status >= 400:
                return httpx.Response(self.status, json={"message": "boom"})
            return httpx.Response(self.status, json={"message": "OK"})
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})


def _delete_adapter(fake: FakeDeleteSuperset) -> SupersetAdapter:
    http = httpx.Client(base_url="http://superset.test", transport=httpx.MockTransport(fake))
    return SupersetAdapter(SupersetClient("http://superset.test", "admin", "pw", http=http), DWH)


def test_delete_artifact_maps_kinds_to_rest_endpoints() -> None:
    fake = FakeDeleteSuperset()
    adapter = _delete_adapter(fake)
    adapter.delete_artifact("chart", "370")
    adapter.delete_artifact("dashboard", "42")
    adapter.delete_artifact("dataset", "80")
    assert fake.deletes == ["/api/v1/chart/370", "/api/v1/dashboard/42", "/api/v1/dataset/80"]


def test_delete_artifact_tolerates_already_gone_404() -> None:
    fake = FakeDeleteSuperset(status=404)
    _delete_adapter(fake).delete_artifact("chart", "370")  # no raise: already deleted
    assert fake.deletes == ["/api/v1/chart/370"]


def test_delete_artifact_reraises_non_404_with_status_code() -> None:
    fake = FakeDeleteSuperset(status=500)
    with pytest.raises(SupersetAPIError) as err:
        _delete_adapter(fake).delete_artifact("dashboard", "42")
    assert err.value.status_code == 500


def test_delete_artifact_refuses_shared_and_unknown_kinds() -> None:
    fake = FakeDeleteSuperset()
    adapter = _delete_adapter(fake)
    with pytest.raises(ValueError, match="shared/unknown"):
        adapter.delete_artifact("database", "1")
    with pytest.raises(ValueError, match="shared/unknown"):
        adapter.delete_artifact("mystery", "9")
    assert fake.deletes == []  # refused before any HTTP call
