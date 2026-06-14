"""DataLensAdapter: deterministic IR compiler -> DataLens self-hosted (reversal §5).

Flow (mirrors SupersetAdapter, ARCHITECTURE §3.5):
  ensure_database  -> bi/createConnection            (DataLens connection entry)
  ensure_dataset   -> bi/createDataset               (one validated-SQL subselect / chart)
  create_chart     -> POST /api/charts/v1/charts     (US widget-entry; engine adds JS stubs)
  assemble_dashboard -> mix/createDashboardV1         (US dash-entry, scope=Dash)

All five steps are LIVE-VERIFIED end-to-end on the self-hosted stand: connection ->
datasets -> charts render real ClickHouse data, and the dashboard entry is created via the
`mix/createDashboardV1` gateway action (reversal §5.3; the action injects schemeVersion,
gathers chart links and runs Dash.validateData server-side, then calls us._createEntry).
"""

from __future__ import annotations

import logging
import uuid

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


# Grid: charts in a 2-column layout, each 12-of-24 columns wide and 4 rows tall.
_GRID_W = 12
_GRID_H = 4


def build_dashboard_data(spec: DashboardSpec, widget_ids: list[str]) -> dict:
    """US dash-entry `data` blob (reversal §5.3), shaped for the `mix/createDashboardV1`
    gateway action (zod `dataSchema` minus `schemeVersion`, which the action injects).

    One tab; charts packed into a 2-column grid. Each chart becomes a `widget` item whose
    single inner tab binds to the chart's US entryId. `validateData` (server-side) requires
    every item to have exactly one layout entry keyed by its id, and all ids unique — both
    hold here (item id, inner widget-tab id and the tab id are distinct). Selectors/
    connections (spec.filters scope-to-applicable) are not emitted yet. `widget_ids` align
    with spec.charts.
    """
    if len(widget_ids) != len(spec.charts):
        raise ValueError(f"got {len(widget_ids)} widget ids for {len(spec.charts)} charts")
    # Deterministic, non-empty salt; ids are explicit so the salt is not used to derive them.
    salt = uuid.uuid5(uuid.NAMESPACE_URL, f"auto_bi_dash:{spec.title}").hex
    tab_id = f"auto_bi_tab_{salt[:8]}"
    items, layout = [], []
    for i, (chart, wid) in enumerate(zip(spec.charts, widget_ids, strict=True)):
        item_id = f"auto_bi_item_{chart.id}"
        items.append(
            {
                "id": item_id,
                "namespace": "default",
                "type": "widget",
                "data": {
                    "hideTitle": False,
                    "tabs": [
                        {
                            "id": f"auto_bi_wt_{chart.id}",
                            "title": chart.title,
                            "description": "",
                            "chartId": wid,
                            "isDefault": True,
                            "params": {},
                        }
                    ],
                },
            }
        )
        layout.append(
            {
                "i": item_id,
                "x": (i % 2) * _GRID_W,
                "y": (i // 2) * _GRID_H,
                "w": _GRID_W,
                "h": _GRID_H,
            }
        )
    # counter = next free hashid index (1 tab id + n item ids + n widget-tab ids), >= 1.
    counter = 1 + 2 * len(items)
    return {
        "salt": salt,
        "counter": counter,
        "tabs": [
            {
                "id": tab_id,
                "title": spec.title,
                "items": items,
                "layout": layout,
                "connections": [],
                "aliases": {},
            }
        ],
        "settings": {
            "autoupdateInterval": None,
            "maxConcurrentRequests": None,
            "silentLoading": False,
            "dependentSelectors": True,
            "hideTabs": False,
            "hideDashTitle": False,
            "expandTOC": False,
        },
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
        # idempotent by name (mirror SupersetAdapter): DataLens enforces unique entry keys
        # per workbook, and CONNECTION_NAME is constant, so a second build would otherwise
        # fail with US "entity already exists". Reuse the existing connection if present.
        existing = self._find_entry_id("connection", CONNECTION_NAME)
        if existing is not None:
            self._connection_id = existing
            logger.info("datalens connection reused: id=%s", existing)
            return DatabaseRef(id=existing, name=CONNECTION_NAME)
        body = build_connection_payload(dwh, name=CONNECTION_NAME, workbook_id=self._workbook_id)
        created = self._client.gateway("bi", "createConnection", body)
        self._connection_id = created["id"]
        logger.info("datalens connection created: id=%s", self._connection_id)
        return DatabaseRef(id=self._connection_id, name=CONNECTION_NAME)

    def _find_entry_id(self, scope: str, name: str) -> str | None:
        """Encoded entryId of a workbook entry with this exact name and scope, or None.

        Uses `us/getWorkbookEntries` (reversal §5.4): the server `filters.name` narrows the
        page, and the exact name is matched on the `key` tail (`<id>/<name>`) case-folded —
        US lowercases keys, so a substring filter alone is not authoritative.
        """
        res = self._client.gateway(
            "us",
            "getWorkbookEntries",
            {"workbookId": self._workbook_id, "scope": scope, "filters": {"name": name}},
        )
        target = name.casefold()
        for entry in res.get("entries", []):
            key = entry.get("key") or ""
            if key.rsplit("/", 1)[-1].casefold() == target:
                return entry["entryId"]
        return None

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
        # `mix/createDashboardV1` injects schemeVersion, gathers chart links and runs
        # Dash.validateData server-side, then us._createEntry (scope=Dash). The `data` blob
        # must omit schemeVersion (reversal §5.3); workbook entries take workbookId+name.
        data = build_dashboard_data(spec, [str(c.id) for c in charts])
        created = self._client.gateway(
            "mix",
            "createDashboardV1",
            {
                "entry": {
                    "data": data,
                    "meta": None,
                    "workbookId": self._workbook_id,
                    "name": spec.title,
                },
                "mode": "publish",
            },
        )
        dash_id = created["entry"]["entryId"]
        url = f"/{dash_id}"  # DataLens serves entries at GET /:entryId
        logger.info("datalens dashboard created: id=%s url=%s charts=%d", dash_id, url, len(charts))
        return DashboardRef(id=dash_id, title=spec.title, url=url)

    # --- happy path ----------------------------------------------------------

    def build(self, spec: DashboardSpec) -> DashboardRef:
        """Full compile: connection -> per-chart datasets -> charts -> dashboard entry
        (mirrors SupersetAdapter.build, ARCHITECTURE §3.5)."""
        self.ensure_database()
        refs: list[ChartRef] = []
        for chart in spec.charts:
            ds = self.ensure_dataset(chart.query, name=dataset_name(spec.title, chart.id))
            refs.append(self.create_chart(chart, ds))
        return self.assemble_dashboard(spec, refs)
