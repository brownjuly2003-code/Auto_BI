"""SupersetAdapter: deterministic IR compiler -> Superset REST API (v1 target).

Flow per ARCHITECTURE §3.5: ensure_database (connection inside BI, idempotent by
name) -> ensure_dataset (virtual dataset per chart with our validated SQL,
idempotent by table_name) -> create_chart (form_data template) ->
assemble_dashboard (position_json grid + chart linkage).
"""

from __future__ import annotations

import json
import logging
import re

from auto_bi.adapters.base import (
    AdapterHealth,
    ChartRef,
    DashboardRef,
    DatabaseRef,
    DatasetRef,
    DWHConfig,
)
from auto_bi.adapters.superset.client import SupersetClient, rison_eq_filter
from auto_bi.adapters.superset.form_data import VIZ_TYPE, build_form_data, build_position_json
from auto_bi.agent.sqlgen import generate_chart_sql
from auto_bi.ir.spec import ChartQuery, ChartSpec, DashboardSpec

logger = logging.getLogger(__name__)

DATABASE_NAME = "Auto_BI ClickHouse"


def _slug(text: str, max_len: int = 40) -> str:
    return re.sub(r"\W+", "_", text.lower()).strip("_")[:max_len] or "dataset"


class SupersetAdapter:
    def __init__(self, client: SupersetClient, dwh: DWHConfig) -> None:
        self._client = client
        self._dwh = dwh
        self._database: DatabaseRef | None = None

    # --- BIAdapter ----------------------------------------------------------

    def healthcheck(self) -> AdapterHealth:
        ok = self._client.health()
        return AdapterHealth(ok=ok, message="" if ok else "GET /health failed")

    def ensure_database(self, dwh: DWHConfig | None = None) -> DatabaseRef:
        dwh = dwh or self._dwh
        existing = self._client.get(
            "/api/v1/database/", params={"q": rison_eq_filter("database_name", DATABASE_NAME)}
        )
        for item in existing.get("result", []):
            self._database = DatabaseRef(id=item["id"], name=DATABASE_NAME)
            logger.info("database already in superset: id=%s", item["id"])
            return self._database

        uri = f"clickhousedb://{dwh.user}:{dwh.password}@{dwh.host}:{dwh.port}/{dwh.database}"
        created = self._client.post(
            "/api/v1/database/",
            json={"database_name": DATABASE_NAME, "sqlalchemy_uri": uri},
        )
        self._database = DatabaseRef(id=created["id"], name=DATABASE_NAME)
        logger.info("database created in superset: id=%s", created["id"])
        return self._database

    def ensure_dataset(self, query: ChartQuery, name: str | None = None) -> DatasetRef:
        if self._database is None:
            self.ensure_database()
        sql = generate_chart_sql(query)
        table_name = name or f"auto_bi__{_slug(query.table)}"

        existing = self._client.get(
            "/api/v1/dataset/", params={"q": rison_eq_filter("table_name", table_name)}
        )
        for item in existing.get("result", []):
            self._client.put(f"/api/v1/dataset/{item['id']}", json={"sql": sql})
            logger.info("dataset %s updated: id=%s", table_name, item["id"])
            return DatasetRef(id=item["id"], name=table_name)

        created = self._client.post(
            "/api/v1/dataset/",
            json={
                "database": self._database.id,
                "table_name": table_name,
                "sql": sql,
                "schema": self._dwh.database,
            },
        )
        logger.info("dataset %s created: id=%s", table_name, created["id"])
        return DatasetRef(id=created["id"], name=table_name)

    def create_chart(self, chart: ChartSpec, ds: DatasetRef) -> ChartRef:
        created = self._client.post(
            "/api/v1/chart/",
            json={
                "slice_name": chart.title,
                "viz_type": VIZ_TYPE[chart.viz],
                "datasource_id": ds.id,
                "datasource_type": "table",
                "params": json.dumps(build_form_data(chart, ds.id), ensure_ascii=False),
            },
        )
        logger.info("chart %r created: id=%s", chart.title, created["id"])
        return ChartRef(id=created["id"], name=chart.title)

    def assemble_dashboard(self, spec: DashboardSpec, charts: list[ChartRef]) -> DashboardRef:
        if spec.filters:
            logger.warning(
                "dashboard filters are not wired in Phase 0, skipped: %s",
                [f.column for f in spec.filters],
            )
        if len(charts) != len(spec.charts):
            raise ValueError(f"got {len(charts)} chart refs for {len(spec.charts)} spec charts")

        placed = list(zip(spec.charts, [c.id for c in charts], strict=True))
        position = build_position_json(spec, placed)
        created = self._client.post(
            "/api/v1/dashboard/",
            json={
                "dashboard_title": spec.title,
                "position_json": json.dumps(position, ensure_ascii=False),
                "json_metadata": json.dumps({"chart_configuration": {}}),
                "published": True,
            },
        )
        dashboard_id = created["id"]
        for ref in charts:  # link charts to the dashboard
            self._client.put(f"/api/v1/chart/{ref.id}", json={"dashboards": [dashboard_id]})

        url = f"/superset/dashboard/{dashboard_id}/"
        logger.info("dashboard %r assembled: id=%s url=%s", spec.title, dashboard_id, url)
        return DashboardRef(id=dashboard_id, title=spec.title, url=url)

    # --- happy path ----------------------------------------------------------

    def build(self, spec: DashboardSpec) -> DashboardRef:
        """Full compile: database -> per-chart datasets -> charts -> dashboard."""
        self.ensure_database()
        refs: list[ChartRef] = []
        for chart in spec.charts:
            ds = self.ensure_dataset(
                chart.query, name=f"auto_bi__{_slug(spec.title)}__{_slug(chart.id)}"
            )
            refs.append(self.create_chart(chart, ds))
        return self.assemble_dashboard(spec, refs)
