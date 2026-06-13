"""DataLensAdapter: deterministic IR compiler -> DataLens self-hosted (reversal §5).

Flow (mirrors SupersetAdapter, ARCHITECTURE §3.5):
  ensure_database  -> bi/createConnection            (DataLens connection entry)
  ensure_dataset   -> bi/createDataset               (one validated-SQL subselect / chart)
  create_chart     -> POST /api/charts/v1/charts     (US widget-entry; engine adds JS stubs)
  assemble_dashboard -> US dash-entry                (blob built here; create endpoint TBD)

Connection/dataset/chart are LIVE-VERIFIED end-to-end (2026-06-14): a line chart rendered
against real ClickHouse data on the self-hosted stand. Dashboard assembly builds the
reversed blob (build_dashboard_data) but its US create endpoint is not yet reversed.
"""

from __future__ import annotations

import logging

from auto_bi.adapters.base import (
    AdapterHealth,
    ChartRef,
    DashboardRef,
    DatabaseRef,
    DatasetRef,
    DWHConfig,
)
from auto_bi.adapters.datalens.chart_config import DEGRADED, build_chart_shared
from auto_bi.adapters.datalens.client import DataLensClient
from auto_bi.adapters.datalens.dataset import (
    build_connection_payload,
    build_dataset_payload,
    dataset_name,
)
from auto_bi.ir.spec import ChartSpec, DashboardSpec
from auto_bi.semantic.model import SemanticModel

logger = logging.getLogger(__name__)

CONNECTION_NAME = "Auto_BI ClickHouse"


def build_dashboard_data(spec: DashboardSpec, widget_ids: list[str]) -> dict:
    """US dash-entry `data` blob (reversal §5.3): tabs/items/layout grid.

    Charts are packed into a simple 2-column grid (each 6 wide). Selectors/connections
    (spec.filters scope-to-applicable) are not emitted yet — added when the dash create
    endpoint is reversed. `widget_ids` align with spec.charts.
    """
    if len(widget_ids) != len(spec.charts):
        raise ValueError(f"got {len(widget_ids)} widget ids for {len(spec.charts)} charts")
    items, layout = [], []
    for i, (chart, wid) in enumerate(zip(spec.charts, widget_ids, strict=True)):
        item_id = f"auto_bi_{chart.id}"
        items.append(
            {
                "id": item_id,
                "type": "widget",
                "namespace": "default",
                "data": {
                    "tabs": [{"id": f"{item_id}_t", "chartId": wid, "title": chart.title}],
                    "hideTitle": False,
                    "title": chart.title,
                },
            }
        )
        layout.append({"i": item_id, "x": (i % 2) * 6, "y": (i // 2) * 4, "w": 6, "h": 4})
    return {
        "tabs": [
            {
                "id": "auto_bi_tab",
                "title": spec.title,
                "items": items,
                "layout": layout,
                "aliases": {"default": []},
                "connections": [],
            }
        ],
        "settings": {"autoupdateInterval": None, "dependentSelectors": True},
        "schemeVersion": 7,
    }


class DataLensAdapter:
    def __init__(
        self,
        client: DataLensClient,
        dwh: DWHConfig,
        model: SemanticModel,
        workbook_id: str,
    ) -> None:
        self._client = client
        self._dwh = dwh
        self._model = model
        self._workbook_id = workbook_id
        self._connection_id: str | None = None
        # dataset id -> (name, fields_by_alias) for binding charts to dataset fields
        self._datasets: dict[str, tuple[str, dict[str, dict]]] = {}

    # --- BIAdapter ----------------------------------------------------------

    def healthcheck(self) -> AdapterHealth:
        ok = self._client.health()
        return AdapterHealth(ok=ok, message="" if ok else "ping failed")

    def ensure_database(self, dwh: DWHConfig | None = None) -> DatabaseRef:
        dwh = dwh or self._dwh
        body = build_connection_payload(dwh, name=CONNECTION_NAME, workbook_id=self._workbook_id)
        created = self._client.gateway("bi", "createConnection", body)
        self._connection_id = created["id"]
        logger.info("datalens connection created: id=%s", self._connection_id)
        return DatabaseRef(id=self._connection_id, name=CONNECTION_NAME)

    def ensure_dataset(self, query, name: str | None = None) -> DatasetRef:
        if self._connection_id is None:
            self.ensure_database()
        ds_name = name or dataset_name(query.table, query.table)
        payload = build_dataset_payload(
            query,
            self._model,
            workbook_id=self._workbook_id,
            connection_id=self._connection_id,
            name=ds_name,
        )
        created = self._client.gateway("bi", "createDataset", payload)
        ds_id = created["id"]
        # createDataset preserves our supplied guids/avatar_id, so the chart binds to the
        # payload's result_schema fields directly (live-verified 2026-06-14)
        fields = {f["title"]: f for f in payload["dataset"]["result_schema"]}
        self._datasets[ds_id] = (ds_name, fields)
        logger.info("datalens dataset created: id=%s name=%s", ds_id, ds_name)
        return DatasetRef(id=ds_id, name=ds_name)

    def create_chart(self, chart: ChartSpec, ds: DatasetRef) -> ChartRef:
        ds_name, fields = self._datasets[str(ds.id)]
        shared = build_chart_shared(chart, str(ds.id), ds_name, fields)
        if chart.viz in DEGRADED:
            logger.warning("chart %r: %s", chart.title, DEGRADED[chart.viz])
        created = self._client.post(
            "/api/charts/v1/charts",
            {
                "data": shared,
                "template": "datalens",
                "workbookId": self._workbook_id,
                "name": chart.title,
            },
        )
        chart_id = created["entryId"]
        logger.info("datalens chart created: id=%s viz=%s", chart_id, chart.viz.value)
        return ChartRef(id=chart_id, name=chart.title)

    def assemble_dashboard(self, spec: DashboardSpec, charts: list[ChartRef]) -> DashboardRef:
        # blob is reversed and built; the US dash-entry create endpoint is not yet reversed
        build_dashboard_data(spec, [str(c.id) for c in charts])
        raise NotImplementedError(
            "DataLens dashboard create endpoint not yet reversed (reversal §5.3); "
            "build_dashboard_data() produces the blob, the US dash-entry POST is pending"
        )

    # --- happy path ----------------------------------------------------------

    def build(self, spec: DashboardSpec) -> list[ChartRef]:
        """Compile to connection -> per-chart datasets -> charts. Returns the chart refs;
        dashboard assembly is pending the dash create endpoint."""
        self.ensure_database()
        refs: list[ChartRef] = []
        for chart in spec.charts:
            ds = self.ensure_dataset(chart.query, name=dataset_name(spec.title, chart.id))
            refs.append(self.create_chart(chart, ds))
        return refs
