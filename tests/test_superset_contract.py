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
from auto_bi.ir.spec import (
    ChartQuery,
    ChartSpec,
    DashboardFilter,
    DashboardSpec,
    FilterOp,
    JoinSpec,
    Measure,
    OrderBy,
    QueryFilter,
    Viz,
    column_alias,
)
from auto_bi.semantic.model import Aggregation, SemanticModel

pytestmark = pytest.mark.integration

REVENUE = Measure(column="revenue", agg=Aggregation.SUM, label="Выручка")
ORDERS = Measure(column="orders", agg=Aggregation.SUM, label="Заказы")
FEW_STORES = QueryFilter(column="store_id", op=FilterOp.IN, value=[1, 2, 3, 4])
JUNE = QueryFilter(column="date", op=FilterOp.GTE, value="2026-06-01")

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
    ChartSpec(
        id="contract_stacked_bar",
        title="[contract] stacked_bar",
        viz=Viz.STACKED_BAR,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["date"],
            series=["store_id"],
            measures=[REVENUE],
            filters=[FEW_STORES, JUNE],
        ),
    ),
    ChartSpec(
        id="contract_area",
        title="[contract] area",
        viz=Viz.AREA,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["date"],
            series=["store_id"],
            measures=[REVENUE],
            filters=[FEW_STORES, JUNE],
        ),
    ),
    ChartSpec(
        id="contract_join_bar",
        title="[contract] join bar",
        viz=Viz.BAR,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["dm.stores.city"],
            measures=[REVENUE],
            joins=[
                JoinSpec(
                    table="dm.stores",
                    on_left="dm.sales_daily.store_id",
                    on_right="dm.stores.id",
                )
            ],
            filters=[JUNE],
            order_by=[OrderBy(by="Выручка", dir="desc")],
            limit=10,
        ),
    ),
    ChartSpec(
        id="contract_pie",
        title="[contract] pie",
        viz=Viz.PIE,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["store_id"],
            measures=[REVENUE],
            filters=[FEW_STORES],
        ),
    ),
    ChartSpec(
        id="contract_table",
        title="[contract] table",
        viz=Viz.TABLE,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["date", "store_id"],
            measures=[REVENUE, ORDERS],
            filters=[FEW_STORES, JUNE],
            limit=100,
        ),
    ),
    ChartSpec(
        id="contract_pivot",
        title="[contract] pivot",
        viz=Viz.PIVOT,
        query=ChartQuery(
            table="dm.sales_daily",
            rows=["store_id"],
            columns=["manager_id"],
            measures=[REVENUE],
            filters=[FEW_STORES, JUNE],
        ),
    ),
    ChartSpec(
        id="contract_heatmap",
        title="[contract] heatmap",
        viz=Viz.HEATMAP,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["date", "store_id"],
            measures=[REVENUE],
            filters=[FEW_STORES, JUNE],
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
    a = SupersetAdapter(client, dwh, SemanticModel.load("semantic/model.yaml"))
    assert a.healthcheck().ok, "Superset /health failed — is the stand up?"
    a.ensure_database()
    yield a
    client.close()  # -W error: an unclosed pool raises ResourceWarning at GC


@pytest.mark.parametrize("chart", CHARTS, ids=lambda c: c.id)
def test_create_get_assert(adapter: SupersetAdapter, chart: ChartSpec) -> None:
    ds = adapter.ensure_dataset(chart.query, name=f"auto_bi__{chart.id}")
    ref = adapter.create_chart(chart, ds)

    fetched = adapter._client.get(f"/api/v1/chart/{ref.id}")["result"]
    assert fetched["viz_type"] == VIZ_TYPE[chart.viz]
    params = json.loads(fetched["params"])
    assert params["viz_type"] == VIZ_TYPE[chart.viz]
    assert params["datasource"] == f"{ds.id}__table"


@pytest.mark.parametrize("chart", CHARTS, ids=lambda c: c.id)
def test_chart_data_endpoint(adapter: SupersetAdapter, chart: ChartSpec) -> None:
    """v1 chart data: the saved form_data metrics must execute against ClickHouse
    through the virtual dataset, for every viz template."""
    ds = adapter.ensure_dataset(chart.query, name=f"auto_bi__{chart.id}")
    ref = adapter.create_chart(chart, ds)
    fetched = adapter._client.get(f"/api/v1/chart/{ref.id}")["result"]
    form_data = json.loads(fetched["params"])
    metrics = form_data.get("metrics") or [form_data["metric"]]
    result = adapter._client.post(
        "/api/v1/chart/data",
        json={
            "datasource": {"id": ds.id, "type": "table"},
            "force": True,
            "queries": [
                {
                    "columns": [column_alias(c) for c in chart.query.group_columns()],
                    "metrics": metrics,
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


# --- native dashboard filters (scope-to-applicable) ---------------------------------

NF_SPEC = DashboardSpec(
    title="[contract] native filters",
    filters=[
        DashboardFilter(column="dm.stores.city", type="value"),
        DashboardFilter(column="dm.sales_daily.date", type="time_range"),
    ],
    charts=[
        ChartSpec(
            id="contract_nf_kpi",
            title="[contract-nf] KPI",
            viz=Viz.BIG_NUMBER,
            query=ChartQuery(table="dm.sales_daily", measures=[REVENUE], filters=[JUNE]),
        ),
        ChartSpec(
            id="contract_nf_city",
            title="[contract-nf] city",
            viz=Viz.BAR,
            query=ChartQuery(
                table="dm.sales_daily",
                dimensions=["dm.stores.city"],
                measures=[REVENUE],
                joins=[
                    JoinSpec(
                        table="dm.stores",
                        on_left="dm.sales_daily.store_id",
                        on_right="dm.stores.id",
                    )
                ],
                filters=[JUNE],
                order_by=[OrderBy(by="Выручка", dir="desc")],
                limit=10,
            ),
        ),
        ChartSpec(
            id="contract_nf_day",
            title="[contract-nf] day",
            viz=Viz.LINE,
            query=ChartQuery(table="dm.sales_daily", dimensions=["date"], measures=[REVENUE]),
        ),
    ],
)


def test_native_filter_configuration_roundtrip(adapter: SupersetAdapter) -> None:
    """build() wires spec.filters into native_filter_configuration, scoped only to the
    charts whose grain exposes the column; the city filter must leave the KPI + day
    charts out, and the participating chart's dataset must drop its SQL top-N LIMIT."""
    ref = adapter.build(NF_SPEC)  # adapter is constructed with the model (fixture)

    dash = adapter._client.get(f"/api/v1/dashboard/{ref.id}")["result"]
    pos = json.loads(dash["position_json"])
    slice_of = {  # spec chart id -> superset slice id
        v["meta"]["sliceName"]: v["meta"]["chartId"]
        for v in pos.values()
        if isinstance(v, dict) and v.get("type") == "CHART"
    }
    nfc = json.loads(dash["json_metadata"])["native_filter_configuration"]
    by_col = {f["targets"][0].get("column", {}).get("name", f["filterType"]): f for f in nfc}

    city = by_col["city"]
    assert city["filterType"] == "filter_select"
    assert slice_of["[contract-nf] city"] in city["chartsInScope"]
    assert slice_of["[contract-nf] KPI"] in city["scope"]["excluded"]
    assert slice_of["[contract-nf] day"] in city["scope"]["excluded"]

    time_filter = by_col["filter_time"]  # empty target -> keyed by filterType above
    assert time_filter["filterType"] == "filter_time"
    assert slice_of["[contract-nf] day"] in time_filter["chartsInScope"]

    # the city chart is in a filter's scope -> its virtual dataset must have no LIMIT
    # (the top-N moved to form_data so the filter re-ranks after filtering)
    city_slice = slice_of["[contract-nf] city"]
    chart = adapter._client.get(f"/api/v1/chart/{city_slice}")["result"]
    dataset_id = json.loads(chart["params"])["datasource"].split("__")[0]
    ds_full = adapter._client.get(f"/api/v1/dataset/{dataset_id}")["result"]
    assert "LIMIT" not in ds_full["sql"].upper()


def test_time_filter_actually_narrows_timeseries(adapter: SupersetAdapter) -> None:
    """B5 end-to-end: the previous mechanism only proved the filter *config* round-trips, so a
    time preset that never re-scoped the data still passed. This asserts the real guarantee — a
    freshly built timeseries chart names its temporal column (granularity_sqla) and its freshly
    introspected virtual dataset marks that column is_dttm, so a dashboard time_range genuinely
    narrows the series instead of silently no-op'ing (24 months -> ~12)."""
    day = ChartSpec(
        id="contract_narrow_day",
        title="[contract] narrow day",
        viz=Viz.LINE,
        query=ChartQuery(table="dm.sales_daily", dimensions=["date"], measures=[REVENUE]),
    )
    ds = adapter.ensure_dataset(day.query, name="auto_bi__contract_narrow_day")
    ref = adapter.create_chart(day, ds)
    form_data = json.loads(adapter._client.get(f"/api/v1/chart/{ref.id}")["result"]["params"])
    gran = form_data.get("granularity_sqla")
    assert gran == "date", "the timeseries chart must name its temporal column for a time filter"
    metrics = form_data.get("metrics") or [form_data["metric"]]

    def rowcount(time_range: str) -> int:
        result = adapter._client.post(
            "/api/v1/chart/data",
            json={
                "datasource": {"id": ds.id, "type": "table"},
                "force": True,
                "queries": [
                    {
                        "columns": ["date"],
                        "metrics": metrics,
                        "granularity": gran,
                        "time_range": time_range,
                        "row_limit": 50000,
                    }
                ],
                "result_format": "json",
                "result_type": "full",
            },
        )
        return result["result"][0]["rowcount"]

    full = rowcount("No filter")
    narrowed = rowcount("2025-01-01 : 2026-01-01")  # one calendar year of a two-year fact
    assert 0 < narrowed < full, f"time_range must re-scope the series (got {narrowed} vs {full})"
