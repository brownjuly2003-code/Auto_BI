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


def test_assemble_rejects_ref_mismatch() -> None:
    adapter = make_adapter(FakeSuperset())
    with pytest.raises(ValueError, match="chart refs"):
        adapter.assemble_dashboard(make_spec(), charts=[])
