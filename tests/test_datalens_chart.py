"""DataLens chart_config + adapter unit tests (payload SHAPES on a fake client).

The chart `shared` structure is live-verified (a line chart rendered end-to-end on the
stand, 2026-06-14); these pin the IR->shared mapping and the adapter call sequence.
"""

from __future__ import annotations

import pytest

from auto_bi.adapters.base import DWHConfig
from auto_bi.adapters.datalens.adapter import (
    DataLensAdapter,
    build_dashboard_data,
    build_selectors,
    connection_name,
)
from auto_bi.adapters.datalens.chart_config import VIZ_ID, build_chart_shared
from auto_bi.adapters.datalens.client import DataLensAPIError
from auto_bi.ir.spec import (
    ChartQuery,
    ChartSpec,
    DashboardFilter,
    DashboardSpec,
    LayoutHint,
    Measure,
    Viz,
)
from auto_bi.semantic.model import Aggregation, SemanticModel

DS_ID = "ds123"
DS_NAME = "auto_bi__ds"


def _fields(*specs: tuple[str, str, str]) -> dict[str, dict]:
    """alias -> field descriptor; specs are (alias, data_type, DIMENSION|MEASURE)."""
    out: dict[str, dict] = {}
    for i, (alias, dt, role) in enumerate(specs):
        out[alias] = {
            "guid": f"guid-{i}",
            "title": alias,
            "source": alias,
            "data_type": dt,
            "cast": dt,
            "type": role,
            "aggregation": "sum" if role == "MEASURE" else "none",
            "avatar_id": "avatar-0",
        }
    return out


def _chart(viz: Viz, **q) -> ChartSpec:
    q.setdefault("measures", [Measure(column="revenue", agg=Aggregation.SUM, label="rev")])
    return ChartSpec(id="c", title="c", viz=viz, query=ChartQuery(table="dm.sales_daily", **q))


# --- chart_config -----------------------------------------------------------


def test_shared_line_binds_x_y() -> None:
    fields = _fields(("date", "date", "DIMENSION"), ("rev", "float", "MEASURE"))
    chart = _chart(Viz.LINE, dimensions=["date"])
    shared = build_chart_shared(chart, DS_ID, DS_NAME, fields)
    assert shared["visualization"]["id"] == "line"
    assert shared["type"] == "datalens"
    assert shared["version"] == "4"
    assert shared["datasetsIds"] == [DS_ID]
    ph = {p["id"]: p for p in shared["visualization"]["placeholders"]}
    assert [i["source"] for i in ph["x"]["items"]] == ["date"]
    assert [i["source"] for i in ph["y"]["items"]] == ["rev"]
    # field item carries the dataset binding
    x = ph["x"]["items"][0]
    assert x["guid"] == "guid-0" and x["avatar_id"] == "avatar-0"
    assert x["datasetId"] == DS_ID and x["datasetName"] == DS_NAME
    assert x["type"] == "DIMENSION" and x["data_type"] == "date"
    # datasetsPartialFields lists used fields
    assert {f["title"] for f in shared["datasetsPartialFields"][0]} == {"date", "rev"}


def test_shared_line_series_to_colors() -> None:
    fields = _fields(
        ("date", "date", "DIMENSION"), ("city", "string", "DIMENSION"), ("rev", "float", "MEASURE")
    )
    chart = _chart(Viz.LINE, dimensions=["date"], series=["city"])
    shared = build_chart_shared(chart, DS_ID, DS_NAME, fields)
    assert [i["source"] for i in shared["colors"]] == ["city"]


def test_shared_big_number() -> None:
    fields = _fields(("rev", "float", "MEASURE"))
    shared = build_chart_shared(_chart(Viz.BIG_NUMBER), DS_ID, DS_NAME, fields)
    assert shared["visualization"]["id"] == "metric"
    ph = shared["visualization"]["placeholders"]
    assert ph[0]["id"] == "measures" and ph[0]["items"][0]["source"] == "rev"
    # the metric field caption is blanked: the tile header is the KPI label, so the raw alias
    # is not repeated beneath the human title (dashboard-craft §3 — label / value, no noise)
    assert ph[0]["items"][0]["title"] == ""


def _y_item(chart: ChartSpec, fields: dict[str, dict]) -> dict:
    shared = build_chart_shared(chart, DS_ID, DS_NAME, fields)
    ph = {p["id"]: p for p in shared["visualization"]["placeholders"]}
    return ph["y"]["items"][0]


def test_shared_compact_formatting_for_large_aggregate() -> None:
    # a SUM measure (alias sum_revenue) gets the compact `formatting` block (B5)
    fields = _fields(("date", "date", "DIMENSION"), ("sum_revenue", "float", "MEASURE"))
    chart = ChartSpec(
        id="c",
        title="c",
        viz=Viz.LINE,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["date"],
            measures=[Measure(column="revenue", agg=Aggregation.SUM)],
        ),
    )
    fmt = _y_item(chart, fields)["formatting"]
    assert fmt["format"] == "number" and fmt["unit"] == "auto"
    # compact precision is 0 — a round headline figure (236B, not 236,1B)
    assert fmt["precision"] == 0


def test_shared_percent_formatting_for_ratio_transform() -> None:
    from auto_bi.ir.spec import MeasureTransform

    fields = _fields(("date", "date", "DIMENSION"), ("pop_pct_sum_revenue", "float", "MEASURE"))
    chart = ChartSpec(
        id="c",
        title="c",
        viz=Viz.LINE,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["date"],
            measures=[
                Measure(column="revenue", agg=Aggregation.SUM, transform=MeasureTransform.POP_PCT)
            ],
        ),
    )
    fmt = _y_item(chart, fields)["formatting"]
    assert fmt["format"] == "percent" and fmt["precision"] == 1


def test_shared_bar_is_column() -> None:
    fields = _fields(("store_id", "integer", "DIMENSION"), ("rev", "float", "MEASURE"))
    shared = build_chart_shared(_chart(Viz.BAR, dimensions=["store_id"]), DS_ID, DS_NAME, fields)
    assert shared["visualization"]["id"] == "column"


def test_shared_bar_horizontal_is_bar_viz() -> None:
    # horizontal=True flips a categorical bar's viz id "column" -> "bar" (DataLens horizontal)
    fields = _fields(("store_id", "integer", "DIMENSION"), ("rev", "float", "MEASURE"))
    chart = _chart(Viz.BAR, dimensions=["store_id"])
    horiz = build_chart_shared(chart, DS_ID, DS_NAME, fields, horizontal=True)
    assert horiz["visualization"]["id"] == "bar"
    vert = build_chart_shared(chart, DS_ID, DS_NAME, fields)
    assert vert["visualization"]["id"] == "column"


def test_shared_categorical_bar_sorts_by_measure() -> None:
    # a categorical (horizontal) bar ranks by its measure: DataLens orders a categorical axis
    # alphabetically unless a `sort` field is set (the sorted-bar rule), so set it
    fields = _fields(("city", "string", "DIMENSION"), ("rev", "float", "MEASURE"))
    chart = _chart(Viz.BAR, dimensions=["city"])
    shared = build_chart_shared(chart, DS_ID, DS_NAME, fields, horizontal=True)
    assert [i["source"] for i in shared["sort"]] == ["rev"]
    # a time/continuous bar (horizontal False) keeps its axis order — no value sort imposed
    vert = build_chart_shared(chart, DS_ID, DS_NAME, fields, horizontal=False)
    assert vert["sort"] == []


def test_shared_bar_numeric_dimension_x_is_string_cast() -> None:
    # B2: a numeric dimension on a column chart's X is cast to string -> discrete category
    # axis (live-verified: returns highcharts `categories`, not raw-numeric x points). The
    # field stays a DIMENSION; only its data_type/cast are coerced for this placeholder.
    fields = _fields(("store_id", "integer", "DIMENSION"), ("rev", "float", "MEASURE"))
    shared = build_chart_shared(_chart(Viz.BAR, dimensions=["store_id"]), DS_ID, DS_NAME, fields)
    ph = {p["id"]: p for p in shared["visualization"]["placeholders"]}
    x = ph["x"]["items"][0]
    assert x["data_type"] == "string" and x["cast"] == "string"
    assert x["initial_data_type"] == "string" and x["type"] == "DIMENSION"
    # the measure on Y is never cast
    assert ph["y"]["items"][0]["data_type"] == "float"


def test_shared_bar_date_dimension_x_not_cast() -> None:
    # a date X (column time-series) keeps its date type — only numeric dimensions discretize
    fields = _fields(("date", "date", "DIMENSION"), ("rev", "float", "MEASURE"))
    shared = build_chart_shared(_chart(Viz.BAR, dimensions=["date"]), DS_ID, DS_NAME, fields)
    x = {p["id"]: p for p in shared["visualization"]["placeholders"]}["x"]["items"][0]
    assert x["data_type"] == "date" and x["cast"] == "date"


def test_shared_bar_boolean_dimension_x_not_cast() -> None:
    # only integer/float dimensions discretize (B2); a boolean dimension stays as-is
    fields = _fields(("is_promo", "boolean", "DIMENSION"), ("rev", "float", "MEASURE"))
    shared = build_chart_shared(_chart(Viz.BAR, dimensions=["is_promo"]), DS_ID, DS_NAME, fields)
    x = {p["id"]: p for p in shared["visualization"]["placeholders"]}["x"]["items"][0]
    assert x["data_type"] == "boolean"


def test_shared_line_numeric_dimension_x_not_cast() -> None:
    # line/area read ALONG a continuous axis -> a numeric X stays numeric (B2 is column-only)
    fields = _fields(("store_id", "integer", "DIMENSION"), ("rev", "float", "MEASURE"))
    shared = build_chart_shared(_chart(Viz.LINE, dimensions=["store_id"]), DS_ID, DS_NAME, fields)
    x = {p["id"]: p for p in shared["visualization"]["placeholders"]}["x"]["items"][0]
    assert x["data_type"] == "integer"


def test_shared_stacked_bar_numeric_color_breakdown_is_string_cast() -> None:
    # the color/series breakdown is categorical too: a numeric series would be a gradient,
    # so it is discretized like the X axis. The date X stays a date.
    fields = _fields(
        ("date", "date", "DIMENSION"),
        ("store_id", "integer", "DIMENSION"),
        ("rev", "float", "MEASURE"),
    )
    chart = _chart(Viz.STACKED_BAR, dimensions=["date"], series=["store_id"])
    shared = build_chart_shared(chart, DS_ID, DS_NAME, fields)
    x = {p["id"]: p for p in shared["visualization"]["placeholders"]}["x"]["items"][0]
    assert x["data_type"] == "date"  # time axis unchanged
    color = shared["colors"][0]
    assert color["source"] == "store_id" and color["data_type"] == "string"


def test_shared_pie_sort_and_labels_by_measure() -> None:
    fields = _fields(("city", "string", "DIMENSION"), ("rev", "float", "MEASURE"))
    shared = build_chart_shared(_chart(Viz.PIE, dimensions=["city"]), DS_ID, DS_NAME, fields)
    assert shared["visualization"]["id"] == "pie"
    ph = {p["id"]: p for p in shared["visualization"]["placeholders"]}
    assert ph["dimensions"]["items"][0]["source"] == "city"
    assert ph["measures"]["items"][0]["source"] == "rev"
    assert shared["sort"][0]["source"] == "rev"
    assert shared["labels"][0]["source"] == "rev"


def test_shared_table_lists_all_columns() -> None:
    fields = _fields(
        ("date", "date", "DIMENSION"), ("city", "string", "DIMENSION"), ("rev", "float", "MEASURE")
    )
    chart = _chart(Viz.TABLE, dimensions=["date", "city"])
    shared = build_chart_shared(chart, DS_ID, DS_NAME, fields)
    assert shared["visualization"]["id"] == "flatTable"
    items = shared["visualization"]["placeholders"][0]["items"]
    assert [i["source"] for i in items] == ["date", "city", "rev"]


def test_shared_pivot_rows_columns_measures() -> None:
    fields = _fields(
        ("date", "date", "DIMENSION"), ("city", "string", "DIMENSION"), ("rev", "float", "MEASURE")
    )
    chart = _chart(Viz.PIVOT, rows=["date"], columns=["city"])
    shared = build_chart_shared(chart, DS_ID, DS_NAME, fields)
    ph = {p["id"]: p for p in shared["visualization"]["placeholders"]}
    assert shared["visualization"]["id"] == "pivotTable"
    assert ph["rows"]["items"][0]["source"] == "date"
    assert ph["pivot-table-columns"]["items"][0]["source"] == "city"
    assert ph["measures"]["items"][0]["source"] == "rev"


def test_shared_heatmap_degrades_to_pivot() -> None:
    fields = _fields(
        ("date", "date", "DIMENSION"), ("city", "string", "DIMENSION"), ("rev", "float", "MEASURE")
    )
    chart = _chart(Viz.HEATMAP, dimensions=["date", "city"])
    shared = build_chart_shared(chart, DS_ID, DS_NAME, fields)
    assert VIZ_ID[Viz.HEATMAP] == "pivotTable"
    assert shared["visualization"]["id"] == "pivotTable"
    ph = {p["id"]: p for p in shared["visualization"]["placeholders"]}
    assert ph["rows"]["items"][0]["source"] == "date"
    assert ph["pivot-table-columns"]["items"][0]["source"] == "city"


def test_shared_full_shape_pins_service_blocks() -> None:
    """Pin the complete top-level shape of `shared` (F10): the service blocks
    colorsConfig/extraSettings and version/type/updates aren't covered by the per-viz
    tests, so a DataLens schema tightening would only surface live. Mirrors the Superset
    form_data contract round-trip."""
    fields = _fields(("date", "date", "DIMENSION"), ("rev", "float", "MEASURE"))
    shared = build_chart_shared(_chart(Viz.LINE, dimensions=["date"]), DS_ID, DS_NAME, fields)
    # exact top-level key set (no extra/missing keys vs the reversed Wizard shape)
    assert set(shared) == {
        "colors", "colorsConfig", "datasetsIds", "datasetsPartialFields", "extraSettings",
        "filters", "geopointsConfig", "hierarchies", "labels", "links", "segments",
        "shapes", "shapesConfig", "sort", "tooltips", "type", "updates", "version",
        "visualization",
    }  # fmt: skip
    # service blocks the per-viz tests don't pin
    assert shared["colorsConfig"] == {
        "gradientMode": "2-point",
        "gradientPalette": "default",
        "polygonBorders": "show",
        "reversed": False,
        "thresholdsMode": "auto",
    }
    assert shared["extraSettings"] == {"titleMode": "hide", "title": "", "legendMode": "show"}
    assert shared["type"] == "datalens" and shared["version"] == "4"
    # empty collections the schema requires stay present and empty
    for k in ("filters", "hierarchies", "links", "segments", "shapes", "tooltips", "updates"):
        assert shared[k] == []
    assert shared["geopointsConfig"] == {} and shared["shapesConfig"] == {}
    # service-block dicts are copied per call, not shared module state (no mutable-default aliasing)
    other = build_chart_shared(_chart(Viz.LINE, dimensions=["date"]), DS_ID, DS_NAME, fields)
    assert shared["colorsConfig"] is not other["colorsConfig"]
    assert shared["extraSettings"] is not other["extraSettings"]


# --- adapter ----------------------------------------------------------------


class FakeClient:
    def __init__(self, wb_entries: dict[str, list[dict]] | None = None) -> None:
        self.gateway_calls: list[tuple[str, str, dict]] = []
        self.posts: list[tuple[str, dict]] = []
        self.deletes: list[tuple[str, str]] = []  # (entryId, scope) from mix/deleteEntry
        self.renames: list[tuple[str, str]] = []  # (entryId, new name) from us/renameEntry
        self._wb_entries = wb_entries or {}  # scope -> entries (idempotency lookup)
        self._n = 0

    def gateway(self, service: str, method: str, body: dict) -> dict:
        self.gateway_calls.append((service, method, body))
        self._n += 1
        if method == "getWorkbookEntries":  # idempotency lookup, scope-keyed
            return {"entries": self._wb_entries.get(body["scope"], [])}
        if method == "deleteEntry":  # mix/deleteEntry (idempotency replace)
            self.deletes.append((body["entryId"], body["scope"]))
            return {}
        if method == "renameEntry":  # us/renameEntry (atomic-rebuild promote, F2)
            self.renames.append((body["entryId"], body["name"]))
            return {}
        if method == "createDashboardV1":  # mix dash-create returns the entry envelope
            return {"entry": {"entryId": f"dash-{self._n}"}}
        return {"id": f"{method}-{self._n}"}

    def post(self, path: str, body: dict) -> dict:
        self.posts.append((path, body))
        self._n += 1
        return {"entryId": f"widget-{self._n}"}

    def health(self) -> bool:
        return True


def _model() -> SemanticModel:
    from auto_bi.semantic.model import Column, ColumnRole, Physical, Table

    return SemanticModel(
        tables=[
            Table(
                name="dm.sales_daily",
                columns=[
                    Column(name="date", type="Date", role=ColumnRole.TIME),
                    Column(name="store_id", type="Int32", role=ColumnRole.DIMENSION),
                    Column(
                        name="revenue",
                        type="Decimal(18,2)",
                        role=ColumnRole.MEASURE,
                        agg=Aggregation.SUM,
                    ),
                ],
                physical=Physical(engine="clickhouse"),
            )
        ]
    )


DWH = DWHConfig(host="h", port=8123, database="dm", user="ro", password="pw")


def _adapter(fake: FakeClient) -> DataLensAdapter:
    return DataLensAdapter(fake, DWH, _model(), workbook_id="wb1")


def test_adapter_build_calls_connection_dataset_chart_dashboard() -> None:
    from auto_bi.adapters.datalens.dataset import dataset_name

    fake = FakeClient()
    spec = DashboardSpec(
        title="dash",
        charts=[
            ChartSpec(
                id="t",
                title="trend",
                viz=Viz.LINE,
                query=ChartQuery(
                    table="dm.sales_daily",
                    dimensions=["date"],
                    measures=[Measure(column="revenue", agg=Aggregation.SUM, label="rev")],
                ),
            )
        ],
    )
    ref = _adapter(fake).build(spec)
    methods = [m for _, m, _ in fake.gateway_calls]
    # Atomic rebuild (F2): every entry is created under a temp `__wip` name first, then
    # promoted (delete stale canonical + rename temp->canonical) only after the whole build
    # succeeds. Each create/promote is preceded by an idempotency lookup -> all miss here.
    # The chart create is a POST, so only its lookups show in gateway_calls.
    assert methods == [
        "getWorkbookEntries",  # connection lookup (canonical, never temp)
        "createConnection",
        "getWorkbookEntries",  # wip dataset lookup
        "createDataset",
        "getWorkbookEntries",  # wip widget lookup
        "getWorkbookEntries",  # wip dashboard lookup
        "createDashboardV1",
        "getWorkbookEntries",  # promote dataset: canonical lookup (delete-if-exists)
        "renameEntry",
        "getWorkbookEntries",  # promote widget: canonical lookup
        "renameEntry",
        "getWorkbookEntries",  # promote dash: canonical lookup
        "renameEntry",
    ]
    assert fake.gateway_calls[0][2] == {
        "workbookId": "wb1",
        "scope": "connection",
        "filters": {"name": "Auto_BI ClickHouse"},
    }
    # connection carries workbook_id (snake) and clickhouse type
    conn_body = fake.gateway_calls[1][2]
    assert conn_body["workbook_id"] == "wb1" and conn_body["type"] == "clickhouse"
    # dataset created under the temp name, carries workbook_id (snake)
    ds_create = fake.gateway_calls[3][2]
    assert ds_create["workbook_id"] == "wb1"
    assert ds_create["name"].endswith("__wip")  # created under the temp name
    # all lookups missed -> nothing deleted; temp entries promoted to canonical names
    assert fake.deletes == []
    assert [name for _, name in fake.renames] == [
        dataset_name("dash", "t"),  # dataset canonical
        "trend",  # widget canonical (safe_entry_name of chart title)
        "dash",  # dashboard canonical (safe_entry_name of spec title)
    ]
    # chart posted to the charts engine with template=datalens + shared
    assert fake.posts[0][0] == "/api/charts/v1/charts"
    assert fake.posts[0][1]["template"] == "datalens"
    assert fake.posts[0][1]["data"]["visualization"]["id"] == "line"
    # build returns a dashboard ref; its blob links the created widget by entryId
    from auto_bi.adapters.base import DashboardRef

    assert isinstance(ref, DashboardRef)
    assert str(ref.id).startswith("dash-") and ref.url == f"/{ref.id}"
    dash_body = next(c[2] for c in fake.gateway_calls if c[1] == "createDashboardV1")
    linked = dash_body["entry"]["data"]["tabs"][0]["items"][0]["data"]["tabs"][0]["chartId"]
    assert linked.startswith("widget-")


def test_ensure_database_reuses_existing_connection() -> None:
    # a connection with the same name already exists -> reuse it, no createConnection
    fake = FakeClient(
        wb_entries={
            "connection": [{"entryId": "conn-enc-id", "key": "999999/auto_bi clickhouse"}],
        }
    )
    ref = _adapter(fake).ensure_database()
    methods = [m for _, m, _ in fake.gateway_calls]
    assert methods == ["getWorkbookEntries"]  # lookup hit -> no create
    assert ref.id == "conn-enc-id"
    assert fake.deletes == []  # connection is reused (not replaced), never deleted


def test_healthcheck_makes_authorized_call_after_ping() -> None:
    """F6: a 200 /ping alone is not health — healthcheck also makes one cheap *authorized*
    getWorkbookEntries on the target workbook, so a live UI with a dead session/gateway is
    reported unhealthy here instead of failing later inside build with a worse error."""
    # happy path: ping ok + authorized call ok
    fake = FakeClient()
    health = _adapter(fake).healthcheck()
    assert health.ok and health.message == ""
    assert [m for _, m, _ in fake.gateway_calls] == ["getWorkbookEntries"]
    assert fake.gateway_calls[0][2] == {"workbookId": "wb1", "scope": "connection"}

    # ping ok but the authorized probe (getWorkbookEntries) 401s -> unhealthy, clear message
    class AuthFailClient(FakeClient):
        def gateway(self, service: str, method: str, body: dict) -> dict:
            if method == "getWorkbookEntries":
                raise DataLensAPIError("us/getWorkbookEntries -> 401: session expired")
            return super().gateway(service, method, body)

    bad = _adapter(AuthFailClient()).healthcheck()
    assert not bad.ok and "authorized check failed" in bad.message

    # ping itself down -> unhealthy, the authorized call is never attempted
    class DeadPingClient(FakeClient):
        def health(self) -> bool:
            return False

    dead_client = DeadPingClient()
    dead = _adapter(dead_client).healthcheck()
    assert not dead.ok and "ping failed" in dead.message
    assert dead_client.gateway_calls == []


def test_connection_name_is_engine_aware() -> None:
    # F11: a CH and a GP connection in one workbook get distinct names, so idempotent
    # reuse never conflates them. CH keeps its existing spelling (backward compatible).
    assert connection_name("clickhouse") == "Auto_BI ClickHouse"
    assert connection_name("greenplum") == "Auto_BI Greenplum"
    assert connection_name("greengage") == "Auto_BI Greengage"
    assert connection_name("postgres") == "Auto_BI PostgreSQL"
    assert connection_name("mystery") == "Auto_BI mystery"  # unknown -> raw engine string


def test_ensure_database_greenplum_uses_engine_named_connection() -> None:
    fake = FakeClient()
    gp_dwh = DWHConfig(
        host="h", port=5432, database="dm", user="ro", password="pw", engine="greenplum"
    )
    DataLensAdapter(fake, gp_dwh, _model(), workbook_id="wb1").ensure_database()
    # both the idempotency lookup and the create use the GP-specific name + type (F11)
    assert fake.gateway_calls[0][2]["filters"]["name"] == "Auto_BI Greenplum"
    conn_body = fake.gateway_calls[1][2]
    assert conn_body["name"] == "Auto_BI Greenplum" and conn_body["type"] == "greenplum"


def test_build_replaces_existing_dataset_chart_dashboard() -> None:
    """A re-build whose dataset/chart/dashboard names already exist deletes each (by exact
    name+scope, via mix/deleteEntry) before re-creating it — DataLens entry keys are unique
    per workbook+scope, so a plain create would 400. The connection is reused, not deleted.
    """
    from auto_bi.adapters.datalens.dataset import dataset_name

    spec = DashboardSpec(
        title="dash",
        charts=[
            ChartSpec(
                id="t",
                title="trend",
                viz=Viz.LINE,
                query=ChartQuery(
                    table="dm.sales_daily",
                    dimensions=["date"],
                    measures=[Measure(column="revenue", agg=Aggregation.SUM, label="rev")],
                ),
            )
        ],
    )
    ds_name = dataset_name(spec.title, "t")
    fake = FakeClient(
        wb_entries={
            "connection": [{"entryId": "conn-old", "key": "1/Auto_BI ClickHouse"}],
            "dataset": [{"entryId": "ds-old", "key": f"2/{ds_name}"}],
            "widget": [{"entryId": "w-old", "key": "3/trend"}],
            "dash": [{"entryId": "b-old", "key": "4/dash"}],
        }
    )
    _adapter(fake).build(spec)
    # connection reused (no create, no delete). The other three are built under temp names,
    # then promoted: the stale canonical entry is deleted and the temp one renamed onto it
    # (atomic rebuild, F2). So the deletes happen at promote time, in promote order.
    methods = [m for _, m, _ in fake.gateway_calls]
    assert "createConnection" not in methods
    assert fake.deletes == [("ds-old", "dataset"), ("w-old", "widget"), ("b-old", "dash")]
    assert [name for _, name in fake.renames] == [ds_name, "trend", "dash"]


def test_build_is_atomic_old_version_survives_mid_build_failure() -> None:
    """A chart-create failure mid-build propagates (the session is marked failed and the
    user retries — build never returns a half-built dashboard). Atomic rebuild (F2): the new
    entries are built under temp `__wip` names and the stale canonical entries are deleted
    ONLY at promote, which a mid-build failure never reaches. So the previous working version
    (old dataset+widget+dash) is left fully intact — nothing canonical is deleted and nothing
    is renamed."""

    from auto_bi.adapters.datalens.dataset import dataset_name

    class FailingChartClient(FakeClient):
        def post(self, path: str, body: dict) -> dict:  # charts-engine create fails
            raise RuntimeError("charts-engine 503")

    spec = DashboardSpec(
        title="dash",
        charts=[
            ChartSpec(
                id="t",
                title="trend",
                viz=Viz.LINE,
                query=ChartQuery(
                    table="dm.sales_daily",
                    dimensions=["date"],
                    measures=[Measure(column="revenue", agg=Aggregation.SUM, label="rev")],
                ),
            )
        ],
    )
    fake = FailingChartClient(
        wb_entries={
            "connection": [{"entryId": "conn-old", "key": "1/Auto_BI ClickHouse"}],
            "dataset": [{"entryId": "ds-old", "key": f"2/{dataset_name(spec.title, 't')}"}],
            "widget": [{"entryId": "w-old", "key": "3/trend"}],
            "dash": [{"entryId": "b-old", "key": "4/dash"}],
        }
    )
    with pytest.raises(RuntimeError, match="charts-engine 503"):
        _adapter(fake).build(spec)
    # promote was never reached -> no canonical entry deleted, none renamed. The old working
    # dashboard, its chart and dataset all survive the failed rebuild.
    assert fake.deletes == []
    assert fake.renames == []


def test_promote_partial_failure_window_is_narrowed_not_atomic() -> None:
    """The promote loop is NOT atomic across entries (Phase 4 F2 audit P3): a `us/renameEntry`
    failure partway through leaves a partially-promoted state. This pins the documented
    narrowed window — once the build has fully succeeded under temp names, promote walks
    (dataset, widget, dash) deleting each stale canonical then renaming its temp onto it; a
    rename failure on the widget propagates AFTER the widget's stale canonical was deleted but
    BEFORE its temp is renamed, so the old dashboard (dash, not yet promoted) transiently
    references a deleted entry until the next build re-creates the missing canonical."""

    from auto_bi.adapters.datalens.dataset import dataset_name

    spec = DashboardSpec(
        title="dash",
        charts=[
            ChartSpec(
                id="t",
                title="trend",
                viz=Viz.LINE,
                query=ChartQuery(
                    table="dm.sales_daily",
                    dimensions=["date"],
                    measures=[Measure(column="revenue", agg=Aggregation.SUM, label="rev")],
                ),
            )
        ],
    )
    ds_canon = dataset_name(spec.title, "t")

    class FailWidgetRenameClient(FakeClient):
        def gateway(self, service: str, method: str, body: dict) -> dict:
            if method == "renameEntry" and body["name"] == "trend":  # widget = 2nd promote step
                raise DataLensAPIError("us/renameEntry -> 500")
            return super().gateway(service, method, body)

    fake = FailWidgetRenameClient(
        wb_entries={
            "connection": [{"entryId": "conn-old", "key": "1/Auto_BI ClickHouse"}],
            "dataset": [{"entryId": "ds-old", "key": f"2/{ds_canon}"}],
            "widget": [{"entryId": "w-old", "key": "3/trend"}],
            "dash": [{"entryId": "b-old", "key": "4/dash"}],
        }
    )
    with pytest.raises(DataLensAPIError, match="renameEntry"):
        _adapter(fake).build(spec)
    # dataset fully promoted (old deleted + temp renamed); widget's stale canonical deleted but
    # its rename failed; the dashboard is never reached (not deleted, not renamed).
    assert fake.deletes == [("ds-old", "dataset"), ("w-old", "widget")]
    assert ("b-old", "dash") not in fake.deletes
    assert [name for _, name in fake.renames] == [ds_canon]  # only the dataset got renamed


def test_build_cleans_up_wip_orphans_on_failure() -> None:
    """A mid-build failure sweeps the temp `__wip` entries created so far (F2 audit P3), so a
    failed build leaves no orphans even if the next attempt's spec differs. Uses a fake that
    serves back created entries so the cleanup lookup can actually find and delete them."""

    class TrackingFailClient(FakeClient):
        """Records created dataset entries + serves them on lookup; fails at chart create."""

        def __init__(self) -> None:
            super().__init__()
            self._live: dict[str, list[dict]] = {}

        def gateway(self, service: str, method: str, body: dict) -> dict:
            if method == "getWorkbookEntries":
                self.gateway_calls.append((service, method, body))
                entries = self._live.get(body["scope"], [])
                name = (body.get("filters") or {}).get("name")
                if name is not None:
                    t = name.casefold()
                    entries = [e for e in entries if e["key"].rsplit("/", 1)[-1].casefold() == t]
                return {"entries": entries}
            if method == "createDataset":
                r = super().gateway(service, method, body)
                self._live.setdefault("dataset", []).append(
                    {"entryId": r["id"], "key": f"9/{body['name']}"}
                )
                return r
            if method == "deleteEntry":
                r = super().gateway(service, method, body)  # records into self.deletes
                self._live[body["scope"]] = [
                    e for e in self._live.get(body["scope"], []) if e["entryId"] != body["entryId"]
                ]
                return r
            return super().gateway(service, method, body)

        def post(self, path: str, body: dict) -> dict:
            raise DataLensAPIError("charts-engine 503")  # fail right after the dataset is created

    spec = DashboardSpec(
        title="dash",
        charts=[
            ChartSpec(
                id="t",
                title="trend",
                viz=Viz.LINE,
                query=ChartQuery(
                    table="dm.sales_daily",
                    dimensions=["date"],
                    measures=[Measure(column="revenue", agg=Aggregation.SUM, label="rev")],
                ),
            )
        ],
    )
    fake = TrackingFailClient()
    with pytest.raises(DataLensAPIError, match="charts-engine 503"):
        _adapter(fake).build(spec)
    # the wip dataset created before the chart-create failure was swept -> no orphan remains
    assert any(scope == "dataset" for _, scope in fake.deletes), "wip dataset was not cleaned up"
    assert fake._live.get("dataset", []) == []


def test_build_dashboard_data_grid() -> None:
    spec = DashboardSpec(
        title="dash",
        charts=[
            ChartSpec(id="a", title="A", viz=Viz.BIG_NUMBER, query=ChartQuery(
                table="dm.sales_daily", measures=[Measure(column="revenue", agg=Aggregation.SUM)])),
            ChartSpec(id="b", title="B", viz=Viz.LINE, query=ChartQuery(
                table="dm.sales_daily", dimensions=["date"],
                measures=[Measure(column="revenue", agg=Aggregation.SUM)])),
        ],
    )  # fmt: skip
    data = build_dashboard_data(spec, ["w1", "w2"])
    # schemeVersion is injected server-side by mix/createDashboardV1, never sent
    assert "schemeVersion" not in data
    assert data["salt"] and isinstance(data["counter"], int) and data["counter"] >= 1
    # settings must carry every field the zod settingsSchema requires
    for k in (
        "autoupdateInterval",
        "maxConcurrentRequests",
        "silentLoading",
        "dependentSelectors",
        "hideTabs",
        "expandTOC",
    ):
        assert k in data["settings"]  # fmt: skip
    tab = data["tabs"][0]
    # tabSchema is .strict() — exactly these keys, no extras
    assert set(tab) == {"id", "title", "items", "layout", "connections", "aliases"}
    assert [it["data"]["tabs"][0]["chartId"] for it in tab["items"]] == ["w1", "w2"]
    # each widget inner-tab has the fields widgetSchema requires
    wt = tab["items"][0]["data"]["tabs"][0]
    assert wt["isDefault"] is True and wt["params"] == {} and "description" in wt
    assert tab["items"][0]["type"] == "widget" and tab["items"][0]["namespace"] == "default"
    # layout: two half-width tiles share a row; one entry per item, keyed by item id
    # (validateData requires this)
    assert tab["layout"][0]["x"] == 0 and tab["layout"][1]["x"] == 12
    assert tab["layout"][0]["y"] == tab["layout"][1]["y"] == 0
    assert [lo["i"] for lo in tab["layout"]] == [it["id"] for it in tab["items"]]
    # auto-scaled heights (no longer a flat h=4): KPI compact, line chart a real plot area
    assert tab["layout"][0]["h"] == 6  # big_number floor
    assert tab["layout"][1]["h"] == 9  # line floor


def test_build_dashboard_data_auto_scales_by_viz_and_hint() -> None:
    # Auto-scaling: a full-width table is tall and spans the grid; a wide line honors its
    # hint width; a tall hint raises the height above the viz floor; tiles never overlap.
    spec = DashboardSpec(
        title="dash",
        charts=[
            ChartSpec(id="t", title="T", viz=Viz.TABLE, layout_hint=LayoutHint(w=12, h=4, row=0),
                      query=ChartQuery(table="dm.sales_daily", dimensions=["store_id"],
                                       measures=[Measure(column="revenue", agg=Aggregation.SUM)])),
            ChartSpec(id="l", title="L", viz=Viz.LINE, layout_hint=LayoutHint(w=12, h=6, row=1),
                      query=ChartQuery(table="dm.sales_daily", dimensions=["date"],
                                       measures=[Measure(column="revenue", agg=Aggregation.SUM)])),
        ],
    )  # fmt: skip
    layout = {lo["i"]: lo for lo in build_dashboard_data(spec, ["w1", "w2"])["tabs"][0]["layout"]}
    table, line = layout["auto_bi_item_t"], layout["auto_bi_item_l"]
    assert table["w"] == 24 and table["h"] == 12  # hint w=12 -> full grid; table floor h=12
    assert line["w"] == 24 and line["h"] == 9 + 2 * 2  # line floor 9 + (hint.h 6 - default 4)*2
    # row-hint change starts a new shelf below the table (no overlap)
    assert table["x"] == 0 and table["y"] == 0
    assert line["x"] == 0 and line["y"] == table["h"]


def test_assemble_dashboard_creates_dash_entry() -> None:
    fake = FakeClient()
    spec = DashboardSpec(
        title="dash",
        charts=[ChartSpec(id="a", title="A", viz=Viz.BIG_NUMBER, query=ChartQuery(
            table="dm.sales_daily", measures=[Measure(column="revenue", agg=Aggregation.SUM)]))],
    )  # fmt: skip
    from auto_bi.adapters.base import ChartRef, DashboardRef

    ref = _adapter(fake).assemble_dashboard(spec, [ChartRef(id="wEnc", name="A")])
    svc, method, body = next(c for c in fake.gateway_calls if c[1] == "createDashboardV1")
    assert (svc, method) == ("mix", "createDashboardV1")
    assert body["mode"] == "publish"
    assert body["entry"]["workbookId"] == "wb1" and body["entry"]["name"] == "dash"
    assert "schemeVersion" not in body["entry"]["data"]  # action injects it
    # chart linked by its (encoded) entryId — US decodeId(chartId) must succeed
    linked = body["entry"]["data"]["tabs"][0]["items"][0]["data"]["tabs"][0]["chartId"]
    assert linked == "wEnc"
    assert isinstance(ref, DashboardRef) and ref.url == f"/{ref.id}"


def test_assemble_dashboard_rejects_chart_spec_mismatch() -> None:
    # F5: mirror SupersetAdapter — a chart/spec length mismatch fails early and clearly,
    # before any gateway call, with the same diagnostic.
    from auto_bi.adapters.base import ChartRef

    fake = FakeClient()
    spec = DashboardSpec(
        title="dash",
        charts=[
            ChartSpec(id="a", title="A", viz=Viz.BIG_NUMBER, query=ChartQuery(
                table="dm.sales_daily", measures=[Measure(column="revenue", agg=Aggregation.SUM)])),
            ChartSpec(id="b", title="B", viz=Viz.BIG_NUMBER, query=ChartQuery(
                table="dm.sales_daily", measures=[Measure(column="revenue", agg=Aggregation.SUM)])),
        ],
    )  # fmt: skip
    with pytest.raises(ValueError, match="1 chart refs for 2 spec charts"):
        _adapter(fake).assemble_dashboard(spec, [ChartRef(id="w", name="A")])
    assert fake.gateway_calls == []


# --- selectors --------------------------------------------------------------


def _bar(cid: str, dims: list[str]) -> ChartSpec:
    return ChartSpec(
        id=cid, title=cid, viz=Viz.BAR,
        query=ChartQuery(
            table="dm.sales_daily", dimensions=dims,
            measures=[Measure(column="revenue", agg=Aggregation.SUM)],
        ),
    )  # fmt: skip


def _ds_field(guid: str, data_type: str = "integer", type_: str = "DIMENSION") -> dict:
    return {"guid": guid, "data_type": data_type, "type": type_}


def test_build_selectors_select_control_scope_and_no_alias_single_dataset() -> None:
    bar = _bar("bar", ["store_id"])
    kpi = ChartSpec(
        id="kpi",
        title="Total",
        viz=Viz.BIG_NUMBER,
        query=ChartQuery(
            table="dm.sales_daily", measures=[Measure(column="revenue", agg=Aggregation.SUM)]
        ),
    )
    spec = DashboardSpec(
        title="dash", filters=[DashboardFilter(column="dm.sales_daily.store_id")], charts=[bar, kpi]
    )
    placements = [(bar, "wbar", "ds_bar"), (kpi, "wkpi", "ds_kpi")]
    fields = {"ds_bar": {"store_id": _ds_field("g_bar")}, "ds_kpi": {}}  # KPI lacks store_id grain
    controls, alias_groups, applied = build_selectors(spec, placements, fields, _model())
    assert len(controls) == 1
    src = controls[0]["data"]["source"]
    assert src["datasetId"] == "ds_bar" and src["datasetFieldId"] == "g_bar"
    assert src["elementType"] == "select" and src["multiselectable"] is True
    assert src["defaultValue"] == "" and controls[0]["defaults"] == {"g_bar": ""}
    assert controls[0]["type"] == "control" and controls[0]["data"]["sourceType"] == "dataset"
    # only the bar chart is in scope -> KPI excluded; one dataset -> nothing to tie
    _, scoped, excluded = applied[0]
    assert scoped == ["bar"] and excluded == ["kpi"]
    assert alias_groups == []


def test_build_selectors_ties_field_across_datasets() -> None:
    bar, line = _bar("bar", ["store_id"]), _bar("line", ["store_id"])
    spec = DashboardSpec(
        title="dash",
        filters=[DashboardFilter(column="dm.sales_daily.store_id")],
        charts=[bar, line],
    )
    placements = [(bar, "wb", "ds_b"), (line, "wl", "ds_l")]
    fields = {"ds_b": {"store_id": _ds_field("gb")}, "ds_l": {"store_id": _ds_field("gl")}}
    _, alias_groups, applied = build_selectors(spec, placements, fields, _model())
    # field tied across both in-scope datasets so the one selector filters both charts
    assert alias_groups == [["gb", "gl"]]
    assert set(applied[0][1]) == {"bar", "line"}


def test_build_selectors_time_column_is_date_range() -> None:
    line = _bar("line", ["date"])
    spec = DashboardSpec(
        title="dash", filters=[DashboardFilter(column="dm.sales_daily.date")], charts=[line]
    )
    fields = {"ds_l": {"date": _ds_field("gd", data_type="genericdatetime")}}
    controls, _, _ = build_selectors(spec, [(line, "wl", "ds_l")], fields, _model())
    src = controls[0]["data"]["source"]
    assert src["elementType"] == "date" and src["isRange"] is True and "multiselectable" not in src


def test_build_selectors_skips_unscoped_filter() -> None:
    bar = _bar("bar", ["store_id"])
    spec = DashboardSpec(
        title="dash", filters=[DashboardFilter(column="dm.sales_daily.date")], charts=[bar]
    )
    fields = {"ds_bar": {"store_id": _ds_field("g_bar")}}
    controls, alias_groups, applied = build_selectors(
        spec, [(bar, "w", "ds_bar")], fields, _model()
    )
    assert controls == [] and alias_groups == [] and applied == []


def test_build_dashboard_data_places_controls_and_aliases() -> None:
    spec = DashboardSpec(
        title="dash",
        charts=[_bar("a", ["store_id"]), _bar("b", ["store_id"])],
    )
    control = {
        "id": "sel1", "namespace": "default", "type": "control",
        "data": {"id": "sel1", "namespace": "default", "title": "Store",
                 "sourceType": "dataset", "source": {}},
        "defaults": {"g": ""},
    }  # fmt: skip
    data = build_dashboard_data(spec, ["w1", "w2"], controls=[control], alias_groups=[["g1", "g2"]])
    tab = data["tabs"][0]
    # control first, then the two widgets
    assert tab["items"][0]["id"] == "sel1" and tab["items"][0]["type"] == "control"
    assert [it["id"] for it in tab["items"][1:]] == ["auto_bi_item_a", "auto_bi_item_b"]
    # validateData: one layout entry per item, keyed by id; control on top, charts below it
    assert len(tab["layout"]) == len(tab["items"])
    assert {lo["i"] for lo in tab["layout"]} == {it["id"] for it in tab["items"]}
    assert next(lo for lo in tab["layout"] if lo["i"] == "sel1")["y"] == 0
    assert next(lo for lo in tab["layout"] if lo["i"] == "auto_bi_item_a")["y"] == 2
    assert tab["aliases"] == {"default": [["g1", "g2"]]}
