"""Contract tests against the LIVE self-hosted DataLens stand: build -> render.

This is the Phase 3.5 "spec builds in DataLens" coverage — the deterministic
counterpart to test_superset_contract.py. It proves the SAME IR spec the Superset
adapter compiles also compiles to a working DataLens dashboard, satisfying the Phase 3
exit criterion ("один и тот же spec собирается в Superset и DataLens"). Runs on the Mac
stand only (tunnel :8090 -> Mac :8080):

    uv run pytest -m integration tests/test_datalens_contract.py

Requires the DataLens compose stand up (admin/admin) + the ClickHouse demo-DM, and
AUTO_BI_DATALENS_* settings (defaults target the local tunnel + OpenSource Demo workbook).
"""

from __future__ import annotations

import pytest

from auto_bi.adapters.base import DWHConfig
from auto_bi.adapters.datalens.adapter import DataLensAdapter
from auto_bi.adapters.datalens.client import DataLensClient
from auto_bi.adapters.datalens.dataset import safe_entry_name
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
)
from auto_bi.semantic.model import Aggregation, SemanticModel

pytestmark = pytest.mark.integration

REVENUE = Measure(column="revenue", agg=Aggregation.SUM, label="Выручка")
ORDERS = Measure(column="orders", agg=Aggregation.SUM, label="Заказы")
FEW_STORES = QueryFilter(column="store_id", op=FilterOp.IN, value=[1, 2, 3, 4])
STORE_JOIN = JoinSpec(table="dm.stores", on_left="dm.sales_daily.store_id", on_right="dm.stores.id")

# Every chart_config VIZ_ID branch (heatmap degrades to pivotTable) + a cross-table join.
CHARTS = [
    ChartSpec(
        id="dl_big_number",
        title="[dl-contract] big_number",
        viz=Viz.BIG_NUMBER,
        query=ChartQuery(table="dm.sales_daily", measures=[REVENUE]),
    ),
    ChartSpec(
        id="dl_line",
        title="[dl-contract] line",
        viz=Viz.LINE,
        query=ChartQuery(table="dm.sales_daily", dimensions=["date"], measures=[REVENUE]),
    ),
    ChartSpec(
        id="dl_bar",
        title="[dl-contract] bar",
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
        id="dl_stacked_bar",
        title="[dl-contract] stacked_bar",
        viz=Viz.STACKED_BAR,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["date"],
            series=["store_id"],
            measures=[REVENUE],
            filters=[FEW_STORES],
        ),
    ),
    ChartSpec(
        id="dl_area",
        title="[dl-contract] area",
        viz=Viz.AREA,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["date"],
            series=["store_id"],
            measures=[REVENUE],
            filters=[FEW_STORES],
        ),
    ),
    ChartSpec(
        id="dl_pie",
        title="[dl-contract] pie",
        viz=Viz.PIE,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["store_id"],
            measures=[REVENUE],
            filters=[FEW_STORES],
        ),
    ),
    ChartSpec(
        id="dl_table",
        title="[dl-contract] table",
        viz=Viz.TABLE,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["date", "store_id"],
            measures=[REVENUE, ORDERS],
            filters=[FEW_STORES],
            limit=100,
        ),
    ),
    ChartSpec(
        id="dl_pivot",
        title="[dl-contract] pivot",
        viz=Viz.PIVOT,
        query=ChartQuery(
            table="dm.sales_daily",
            rows=["store_id"],
            columns=["manager_id"],
            measures=[REVENUE],
            filters=[FEW_STORES],
        ),
    ),
    ChartSpec(
        id="dl_heatmap",
        title="[dl-contract] heatmap",
        viz=Viz.HEATMAP,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["store_id", "manager_id"],
            measures=[REVENUE],
            filters=[FEW_STORES],
        ),
    ),
    ChartSpec(
        id="dl_join_bar",
        title="[dl-contract] join bar",
        viz=Viz.BAR,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["dm.stores.city"],
            measures=[REVENUE],
            joins=[STORE_JOIN],
            order_by=[OrderBy(by="Выручка", dir="desc")],
            limit=10,
        ),
    ),
]


def _rendered_with_data(run: dict) -> bool:
    """A /api/run render carries real data (viz-agnostic): no error, `data` non-empty.
    metric -> list of value blobs; line/bar/area/pie -> data.graphs[].data points;
    flatTable -> data.rows; pivotTable -> non-empty dict (shape varies)."""
    if run.get("error") or run.get("errorType"):
        return False
    data = run.get("data")
    if isinstance(data, list):
        return len(data) > 0
    if isinstance(data, dict):
        if "graphs" in data:
            return any(g.get("data") for g in data["graphs"])
        if "rows" in data:
            return len(data["rows"]) > 0
        return bool(data)
    return False


@pytest.fixture(scope="module")
def model() -> SemanticModel:
    return SemanticModel.load("semantic/model.yaml")


@pytest.fixture(scope="module")
def adapter(model: SemanticModel) -> DataLensAdapter:
    s = get_settings()
    client = DataLensClient(s.datalens_url, s.datalens_user, s.datalens_password)
    dwh = DWHConfig(
        host=s.ch_host_from_datalens,
        port=s.ch_port,
        database=s.ch_database,
        user=s.ch_user,
        password=s.ch_password,
        engine="clickhouse",
    )
    a = DataLensAdapter(client, dwh, model, workbook_id=s.datalens_workbook_id)
    assert a.healthcheck().ok, "DataLens /ping failed — is the stand up (tunnel :8090)?"
    a.ensure_database()
    yield a
    client.close()  # -W error: an unclosed pool raises ResourceWarning at GC


@pytest.mark.parametrize("chart", CHARTS, ids=lambda c: c.id)
def test_chart_compiles_and_renders(adapter: DataLensAdapter, chart: ChartSpec) -> None:
    """Each viz template: IR -> dataset -> chart -> /api/run returns real CH data."""
    ds = adapter.ensure_dataset(chart.query, name=f"auto_bi__{chart.id}")
    ref = adapter.create_chart(chart, ds)
    run = adapter._client.post("/api/run", {"id": str(ref.id), "workbookId": adapter._workbook_id})
    assert _rendered_with_data(run), f"{chart.id} rendered no data: keys={sorted(run)}"


def test_histogram_buckets_render_in_numeric_order(adapter: DataLensAdapter) -> None:
    """C7 regression: a histogram's numeric buckets render on the discrete axis in NUMERIC
    order (50,100,…,350 — NOT lexicographic '50' after '350'). DataLens sorts a
    numeric-string-cast categorical axis numerically by default, so the adapter sets no `sort`
    for a histogram (chart_config, live-verified S12 2026-07-04). This guards against a
    DataLens version drift (the self-hosted stand tracks a floating image tag) reintroducing a
    lexicographic axis order, and against a future change that adds a (direction-less) sort
    here — which would flip the buckets to DESC."""
    chart = ChartSpec(
        id="dl_histogram",
        title="[dl-contract] histogram",
        viz=Viz.HISTOGRAM,
        query=ChartQuery(
            table="dm.products",
            dimensions=["price"],
            measures=[Measure(column="price", agg=Aggregation.COUNT, label="Товаров")],
            bins=8,
        ),
    )
    ds = adapter.ensure_dataset(chart.query, name="auto_bi__dl_histogram")
    ref = adapter.create_chart(chart, ds)
    run = adapter._client.post("/api/run", {"id": str(ref.id), "workbookId": adapter._workbook_id})
    assert _rendered_with_data(run), f"histogram rendered no data: keys={sorted(run)}"
    categories = run["data"]["categories"]  # the highcharts x-axis order as rendered
    values = [float(c) for c in categories]
    assert len(values) >= 2, f"expected multiple buckets, got {categories}"
    assert values == sorted(values), f"buckets not in ascending numeric order: {categories}"


def test_percent_axis_formats_by_field(adapter: DataLensAdapter) -> None:
    """C1: a share-transform chart's VALUE axis renders as percent. The placeholder-item
    `formatting` alone is not enough — the engine reads it into the axis ONLY under
    `settings.axisFormatMode="by-field"` (chart_config._AXIS_FORMAT_BY_FIELD): the run's
    highchartsConfig must carry chartKitFormat="percent" in axesFormatting.yAxis (the
    un-flagged baseline returns an empty axesFormatting — the pre-fix raw 0..1 axis)."""
    import json

    from auto_bi.ir.spec import MeasureTransform

    chart = ChartSpec(
        id="dl_pct_axis",
        title="[dl-contract] percent axis",
        viz=Viz.LINE,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["date"],
            measures=[
                Measure(
                    column="revenue",
                    agg=Aggregation.SUM,
                    transform=MeasureTransform.SHARE_OF_TOTAL,
                )
            ],
        ),
    )
    ds = adapter.ensure_dataset(chart.query, name="auto_bi__dl_pct_axis")
    ref = adapter.create_chart(chart, ds)
    run = adapter._client.post("/api/run", {"id": str(ref.id), "workbookId": adapter._workbook_id})
    assert _rendered_with_data(run), f"percent chart rendered no data: keys={sorted(run)}"
    hc = run["highchartsConfig"]
    if isinstance(hc, str):
        hc = json.loads(hc)
    y_formats = hc["axesFormatting"]["yAxis"]
    assert y_formats, "axesFormatting.yAxis is empty — the by-field flag was not honored"
    assert y_formats[0]["chartKitFormat"] == "percent"


def test_kpi_ru_units_scale_headline(adapter: DataLensAdapter) -> None:
    """N2: a billions-scale ruble KPI builds with the measure scaled in the dataset SQL and
    the RU unit glued as a formatting postfix — the run returns the scaled scalar (a round
    figure < 1000) with " млрд ₽", not the raw 12-digit number the SI locale would show as
    "236B". The scaling decision lives in build() (live magnitude probe), so the whole
    spec is built."""
    spec = DashboardSpec(
        title="[dl-contract] ru units",
        charts=[
            ChartSpec(
                id="dlru_kpi",
                title="[dl-contract] ru kpi",
                viz=Viz.BIG_NUMBER,
                query=ChartQuery(table="dm.sales_daily", measures=[REVENUE]),
            )
        ],
    )
    adapter.build(spec)
    wid = adapter._find_entry_id("widget", safe_entry_name("[dl-contract] ru kpi"))
    assert wid is not None
    run = adapter._client.post("/api/run", {"id": wid, "workbookId": adapter._workbook_id})
    current = run["data"][0]["content"]["current"]
    assert current["postfix"] == " млрд ₽"
    assert 0 < float(current["value"]) < 1000  # scaled headline, not the raw aggregate


def test_selector_default_period_narrows_chart_data(adapter: DataLensAdapter) -> None:
    """B5: a DashboardFilter.default period phrase must narrow the in-scope chart's DATA,
    not just show a badge (the Superset B5 lesson). Two assertions: (1) the built dash
    control carries the relative-interval token in BOTH source.defaultValue and the item
    `defaults` (the charts' initial params); (2) running the built line chart with that
    same param (as the dashboard does on open) returns strictly fewer rows than without."""
    token = "__interval___relative_-3M___relative_+0d"
    spec = DashboardSpec(
        title="[dl-contract] b5 period",
        filters=[DashboardFilter(column="dm.sales_daily.date", default="last 3 months")],
        charts=[
            ChartSpec(
                id="dlb5_line",
                title="[dl-contract] b5 line",
                viz=Viz.LINE,
                query=ChartQuery(table="dm.sales_daily", dimensions=["date"], measures=[REVENUE]),
            )
        ],
    )
    dash = adapter.build(spec)
    entry = adapter._client.gateway("us", "getEntry", {"entryId": str(dash.id)})
    tab = entry["data"]["tabs"][0]
    control = next(it for it in tab["items"] if it["type"] == "control")
    assert control["data"]["source"]["defaultValue"] == token
    assert list(control["defaults"].values()) == [token]
    (guid,) = control["defaults"].keys()

    widget = next(it for it in tab["items"] if it["type"] == "widget")
    wid = widget["data"]["tabs"][0]["chartId"]
    full = adapter._client.post("/api/run", {"id": wid, "workbookId": adapter._workbook_id})
    narrowed = adapter._client.post(
        "/api/run", {"id": wid, "workbookId": adapter._workbook_id, "params": {guid: token}}
    )
    n_full = len(full["data"]["graphs"][0]["data"])
    n_narrowed = len(narrowed["data"]["graphs"][0]["data"])
    assert 0 < n_narrowed < n_full, f"period param did not narrow: {n_narrowed} vs {n_full}"
    assert n_narrowed <= 100  # ~3 months of daily points, not the full history


def test_build_dashboard_with_selector_is_idempotent(adapter: DataLensAdapter) -> None:
    """Full build() of a multi-chart spec with a dashboard selector creates a dash entry;
    re-building the same (constant-title) spec succeeds — idempotency via delete-then-
    create, no 'entity already exists'. The 2nd dashboard's id differs (entries churn)."""
    spec = DashboardSpec(
        title="[dl-contract] dashboard",
        filters=[DashboardFilter(column="dm.sales_daily.store_id")],
        charts=[
            ChartSpec(
                id="dlb_bar",
                title="[dl-contract] dash bar",
                viz=Viz.BAR,
                query=ChartQuery(
                    table="dm.sales_daily", dimensions=["store_id"], measures=[REVENUE]
                ),
            ),
            ChartSpec(
                id="dlb_kpi",
                title="[dl-contract] dash kpi",
                viz=Viz.BIG_NUMBER,
                query=ChartQuery(table="dm.sales_daily", measures=[REVENUE]),
            ),
        ],
    )
    # the entry is stored under the sanitized name (title has brackets -> coerced)
    entry_name = safe_entry_name(spec.title)
    assert entry_name == "dl-contract dashboard"  # sanitization actually fired

    first = adapter.build(spec)
    assert adapter._find_entry_id("dash", entry_name) == first.id

    second = adapter.build(spec)  # would 400 (entity already exists) without idempotency
    assert second.id != first.id
    assert adapter._find_entry_id("dash", entry_name) == second.id
