"""SupersetAdapter unit tests on httpx.MockTransport — API payload shapes only.

The real form_data/position_json contract is verified against the live pinned
Superset by tests/test_superset_contract.py (integration, runs on the Mac stand).
"""

import json

import httpx
import pytest

from auto_bi.adapters.base import DWHConfig
from auto_bi.adapters.superset.adapter import SupersetAdapter
from auto_bi.adapters.superset.client import SupersetClient
from auto_bi.adapters.superset.form_data import build_form_data, build_position_json
from auto_bi.ir.spec import (
    ChartQuery,
    ChartSpec,
    DashboardSpec,
    LayoutHint,
    Measure,
    OrderBy,
    Viz,
)
from auto_bi.semantic.model import Aggregation

DWH = DWHConfig(host="ch", port=8123, database="dm", user="ro", password="pw")


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

    def __init__(self, existing_databases: list[dict] | None = None) -> None:
        self.requests: list[tuple[str, str, dict | None]] = []
        self.databases = existing_databases or []
        self.datasets: list[dict] = []
        self.next_id = 100

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
        if request.method == "POST":
            self.next_id += 1
            return httpx.Response(201, json={"id": self.next_id, "result": body})
        if request.method == "PUT":
            return httpx.Response(200, json={"result": body})
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})


def make_adapter(fake: FakeSuperset) -> SupersetAdapter:
    http = httpx.Client(base_url="http://superset.test", transport=httpx.MockTransport(fake))
    return SupersetAdapter(SupersetClient("http://superset.test", "admin", "pw", http=http), DWH)


# --- form_data templates ----------------------------------------------------


def test_form_data_line() -> None:
    chart = make_spec().charts[1]
    fd = build_form_data(chart, dataset_id=42)
    assert fd["viz_type"] == "echarts_timeseries_line"
    assert fd["datasource"] == "42__table"
    assert fd["x_axis"] == "date"
    assert fd["metrics"][0]["sqlExpression"] == 'SUM("Выручка")'
    assert fd["groupby"] == []


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
