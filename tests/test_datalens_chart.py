"""DataLens chart_config + adapter unit tests (payload SHAPES on a fake client).

The chart `shared` structure is live-verified (a line chart rendered end-to-end on the
stand, 2026-06-14); these pin the IR->shared mapping and the adapter call sequence.
"""

from __future__ import annotations

import pytest

from auto_bi.adapters.base import DWHConfig
from auto_bi.adapters.datalens.adapter import DataLensAdapter, build_dashboard_data
from auto_bi.adapters.datalens.chart_config import VIZ_ID, build_chart_shared
from auto_bi.ir.spec import (
    ChartQuery,
    ChartSpec,
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
    def __init__(self) -> None:
        self.gateway_calls: list[tuple[str, str, dict]] = []
        self.posts: list[tuple[str, dict]] = []
        self._n = 0

    def gateway(self, service: str, method: str, body: dict) -> dict:
        self.gateway_calls.append((service, method, body))
        self._n += 1
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


def test_adapter_build_calls_connection_dataset_chart() -> None:
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
    refs = _adapter(fake).build(spec)
    methods = [m for _, m, _ in fake.gateway_calls]
    assert methods == ["createConnection", "createDataset"]
    # connection carries workbook_id (snake) and clickhouse type
    conn_body = fake.gateway_calls[0][2]
    assert conn_body["workbook_id"] == "wb1" and conn_body["type"] == "clickhouse"
    # dataset carries workbook_id (snake)
    assert fake.gateway_calls[1][2]["workbook_id"] == "wb1"
    # chart posted to the charts engine with template=datalens + shared
    assert fake.posts[0][0] == "/api/charts/v1/charts"
    assert fake.posts[0][1]["template"] == "datalens"
    assert fake.posts[0][1]["data"]["visualization"]["id"] == "line"
    assert len(refs) == 1 and str(refs[0].id).startswith("widget-")


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
    assert data["schemeVersion"] == 7
    tab = data["tabs"][0]
    assert [it["data"]["tabs"][0]["chartId"] for it in tab["items"]] == ["w1", "w2"]
    assert tab["layout"][0]["x"] == 0 and tab["layout"][1]["x"] == 6  # two columns


def test_assemble_dashboard_not_implemented_but_blob_builds() -> None:
    fake = FakeClient()
    spec = DashboardSpec(
        title="dash",
        charts=[ChartSpec(id="a", title="A", viz=Viz.BIG_NUMBER, query=ChartQuery(
            table="dm.sales_daily", measures=[Measure(column="revenue", agg=Aggregation.SUM)]))],
    )  # fmt: skip
    from auto_bi.adapters.base import ChartRef

    with pytest.raises(NotImplementedError, match="dashboard create endpoint"):
        _adapter(fake).assemble_dashboard(spec, [ChartRef(id="w1", name="A")])
