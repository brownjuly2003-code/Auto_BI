"""DataLens chart_config + adapter unit tests (payload SHAPES on a fake client).

The chart `shared` structure is live-verified (a line chart rendered end-to-end on the
stand, 2026-06-14); these pin the IR->shared mapping and the adapter call sequence.
"""

from __future__ import annotations

from auto_bi.adapters.base import DWHConfig
from auto_bi.adapters.datalens.adapter import (
    DataLensAdapter,
    build_dashboard_data,
    build_selectors,
)
from auto_bi.adapters.datalens.chart_config import VIZ_ID, build_chart_shared
from auto_bi.ir.spec import (
    ChartQuery,
    ChartSpec,
    DashboardFilter,
    DashboardSpec,
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


def test_shared_bar_is_column() -> None:
    fields = _fields(("store_id", "integer", "DIMENSION"), ("rev", "float", "MEASURE"))
    shared = build_chart_shared(_chart(Viz.BAR, dimensions=["store_id"]), DS_ID, DS_NAME, fields)
    assert shared["visualization"]["id"] == "column"


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


# --- adapter ----------------------------------------------------------------


class FakeClient:
    def __init__(self, wb_entries: dict[str, list[dict]] | None = None) -> None:
        self.gateway_calls: list[tuple[str, str, dict]] = []
        self.posts: list[tuple[str, dict]] = []
        self.deletes: list[tuple[str, str]] = []  # (entryId, scope) from mix/deleteEntry
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
    # each create is preceded by an idempotency lookup (getWorkbookEntries) -> all miss
    # here -> create. The chart create is a POST, so only its lookup shows in gateway_calls.
    assert methods == [
        "getWorkbookEntries",  # connection lookup
        "createConnection",
        "getWorkbookEntries",  # dataset lookup
        "createDataset",
        "getWorkbookEntries",  # widget (chart) lookup
        "getWorkbookEntries",  # dashboard lookup
        "createDashboardV1",
    ]
    assert fake.gateway_calls[0][2] == {
        "workbookId": "wb1",
        "scope": "connection",
        "filters": {"name": "Auto_BI ClickHouse"},
    }
    # connection carries workbook_id (snake) and clickhouse type
    conn_body = fake.gateway_calls[1][2]
    assert conn_body["workbook_id"] == "wb1" and conn_body["type"] == "clickhouse"
    # dataset carries workbook_id (snake)
    assert fake.gateway_calls[3][2]["workbook_id"] == "wb1"
    # all lookups missed -> nothing deleted
    assert fake.deletes == []
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
    # connection reused (no create, no delete); the other three replaced in place
    methods = [m for _, m, _ in fake.gateway_calls]
    assert "createConnection" not in methods
    assert fake.deletes == [("ds-old", "dataset"), ("w-old", "widget"), ("b-old", "dash")]


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
    # layout: two columns; one entry per item, keyed by item id (validateData requires this)
    assert tab["layout"][0]["x"] == 0 and tab["layout"][1]["x"] == 12
    assert [lo["i"] for lo in tab["layout"]] == [it["id"] for it in tab["items"]]


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
