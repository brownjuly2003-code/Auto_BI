"""Contract tests against the LIVE pinned Superset: create -> GET -> assert.

This is the de-risking step for the undocumented form_data (PLAN 0.7 / stopper S5).
Runs on the Mac stand only:

    uv run pytest -m integration tests/test_superset_contract.py

Requires docker compose up (Superset + ClickHouse demo-DM) and .env credentials.
"""

import json

import pytest

from auto_bi.adapters.base import DWHConfig
from auto_bi.adapters.superset.adapter import SupersetAdapter
from auto_bi.adapters.superset.client import SupersetClient
from auto_bi.adapters.superset.form_data import VIZ_TYPE
from auto_bi.config import get_settings
from auto_bi.ir.spec import ChartQuery, ChartSpec, Measure, OrderBy, Viz
from auto_bi.semantic.model import Aggregation

pytestmark = pytest.mark.integration

REVENUE = Measure(column="revenue", agg=Aggregation.SUM, label="Выручка")

CHARTS = [
    ChartSpec(
        id="contract_big_number",
        title="[contract] big_number",
        viz=Viz.BIG_NUMBER,
        query=ChartQuery(table="dm.sales_daily", measures=[REVENUE]),
    ),
    ChartSpec(
        id="contract_line",
        title="[contract] line",
        viz=Viz.LINE,
        query=ChartQuery(table="dm.sales_daily", dimensions=["date"], measures=[REVENUE]),
    ),
    ChartSpec(
        id="contract_bar",
        title="[contract] bar",
        viz=Viz.BAR,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["store_id"],
            measures=[REVENUE],
            order_by=[OrderBy(by="Выручка", dir="desc")],
            limit=10,
        ),
    ),
]


@pytest.fixture(scope="module")
def adapter() -> SupersetAdapter:
    settings = get_settings()
    client = SupersetClient(
        settings.superset_url, settings.superset_user, settings.superset_password
    )
    dwh = DWHConfig(
        host=settings.ch_host_from_bi or settings.ch_host,
        port=settings.ch_port_from_bi or settings.ch_port,
        database=settings.ch_database,
        user=settings.ch_user,
        password=settings.ch_password,
    )
    a = SupersetAdapter(client, dwh)
    assert a.healthcheck().ok, "Superset /health failed — is the stand up?"
    a.ensure_database()
    return a


@pytest.mark.parametrize("chart", CHARTS, ids=lambda c: c.id)
def test_create_get_assert(adapter: SupersetAdapter, chart: ChartSpec) -> None:
    ds = adapter.ensure_dataset(chart.query, name=f"auto_bi__{chart.id}")
    ref = adapter.create_chart(chart, ds)

    fetched = adapter._client.get(f"/api/v1/chart/{ref.id}")["result"]
    assert fetched["viz_type"] == VIZ_TYPE[chart.viz]
    params = json.loads(fetched["params"])
    assert params["viz_type"] == VIZ_TYPE[chart.viz]
    assert params["datasource"] == f"{ds.id}__table"


def test_chart_data_endpoint(adapter: SupersetAdapter) -> None:
    """v1 chart data: the saved form_data must execute against ClickHouse."""
    chart = CHARTS[1]
    ds = adapter.ensure_dataset(chart.query, name=f"auto_bi__{chart.id}")
    ref = adapter.create_chart(chart, ds)
    fetched = adapter._client.get(f"/api/v1/chart/{ref.id}")["result"]
    form_data = json.loads(fetched["params"])
    result = adapter._client.post(
        "/api/v1/chart/data",
        json={
            "datasource": {"id": ds.id, "type": "table"},
            "force": True,
            "queries": [
                {
                    "columns": [form_data["x_axis"]],
                    "metrics": form_data["metrics"],
                    "row_limit": form_data["row_limit"],
                }
            ],
            "result_format": "json",
            "result_type": "full",
        },
    )
    first = result["result"][0]
    assert first["status"] in ("success", "Success")
    assert first["rowcount"] > 0
