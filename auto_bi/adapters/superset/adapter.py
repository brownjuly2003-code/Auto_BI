"""SupersetAdapter: deterministic IR compiler -> Superset REST API (v1 target).

Flow per ARCHITECTURE §3.5: ensure_database (connection inside BI, idempotent by
name) -> ensure_dataset (virtual dataset per chart with our validated SQL,
idempotent by table_name) -> create_chart (form_data template) ->
assemble_dashboard (position_json grid + chart linkage).
"""

from __future__ import annotations

import hashlib
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
from auto_bi.adapters.superset.native_filters import (
    build_native_filter_configuration,
    participating_chart_ids,
)
from auto_bi.agent.sqlgen import generate_chart_sql
from auto_bi.ir.spec import ChartQuery, ChartSpec, DashboardSpec
from auto_bi.semantic.model import SemanticModel

logger = logging.getLogger(__name__)

DATABASE_NAME = "Auto_BI ClickHouse"


def _slug(text: str, max_len: int = 40) -> str:
    return re.sub(r"\W+", "_", text.lower()).strip("_")[:max_len] or "dataset"


def _int_id(ref_id: int | str) -> int:
    """Superset entity ids are ints; refs type them `int | str` only to share the BIAdapter
    Protocol with DataLens (string entry ids, see base.py). Narrow back at the Superset
    boundary where the REST API and form_data/position helpers genuinely require ints."""
    return int(ref_id)


def _dataset_name(title: str, chart_id: str) -> str:
    """Readable, collision-free dataset name: slugs can truncate-collide, so a short
    hash of the full chart_id (unique per spec) keeps two charts on distinct datasets."""
    suffix = hashlib.sha1(chart_id.encode()).hexdigest()[:8]
    return f"auto_bi__{_slug(title)}__{_slug(chart_id)}__{suffix}"


class SupersetAdapter:
    def __init__(
        self, client: SupersetClient, dwh: DWHConfig, model: SemanticModel | None = None
    ) -> None:
        self._client = client
        self._dwh = dwh
        # `model` (constructor-injected, mirrors DataLensAdapter) lets build() scope native
        # filters by column role/grain; without it filters degrade to the documented warning.
        self._model = model
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

    def ensure_dataset(
        self, query: ChartQuery, name: str | None = None, *, apply_limit: bool = True
    ) -> DatasetRef:
        db = self._database or self.ensure_database()
        sql = generate_chart_sql(query, apply_limit=apply_limit)
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
                "database": db.id,
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
                "params": json.dumps(build_form_data(chart, _int_id(ds.id)), ensure_ascii=False),
            },
        )
        logger.info("chart %r created: id=%s", chart.title, created["id"])
        return ChartRef(id=created["id"], name=chart.title)

    def assemble_dashboard(
        self,
        spec: DashboardSpec,
        charts: list[ChartRef],
        datasets: list[DatasetRef] | None = None,
        model: SemanticModel | None = None,
    ) -> DashboardRef:
        if len(charts) != len(spec.charts):
            raise ValueError(f"got {len(charts)} chart refs for {len(spec.charts)} spec charts")

        native_filters: list[dict] = []
        if spec.filters:
            if datasets is not None and model is not None:
                placements = [
                    (chart, _int_id(ref.id), _int_id(ds.id))
                    for chart, ref, ds in zip(spec.charts, charts, datasets, strict=True)
                ]
                native_filters, applied = build_native_filter_configuration(spec, placements, model)
                for f, in_scope, excluded in applied:
                    logger.info(
                        "native filter %s wired: scope=%s excluded=%s",
                        f.column,
                        in_scope,
                        excluded,
                    )
                wired = {f.column for f, _, _ in applied}
                for f in spec.filters:
                    if f.column not in wired:
                        # no chart exposes the column in its grain -> can't be a native
                        # filter; the baked query.filters still constrain the data
                        logger.warning(
                            "dashboard filter %s not applicable to any chart's grain, skipped",
                            f.column,
                        )
            else:  # build() always supplies datasets+model; this is the bare-protocol path
                logger.warning(
                    "dashboard filters skipped (no model/datasets passed to assemble): %s",
                    [f.column for f in spec.filters],
                )

        placed = list(zip(spec.charts, [_int_id(c.id) for c in charts], strict=True))
        position = build_position_json(spec, placed)
        json_metadata: dict = {"chart_configuration": {}}
        if native_filters:
            json_metadata["native_filter_configuration"] = native_filters
        created = self._client.post(
            "/api/v1/dashboard/",
            json={
                "dashboard_title": spec.title,
                "position_json": json.dumps(position, ensure_ascii=False),
                "json_metadata": json.dumps(json_metadata, ensure_ascii=False),
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
        """Full compile: database -> per-chart datasets -> charts -> dashboard.

        The constructor-injected model lets the dashboard wire native filters (scope by
        column role/grain); without it the filters degrade to the documented "skipped"
        warning. Signature mirrors DataLensAdapter.build so the pipeline can dispatch by
        `spec.target_bi` (Phase 4 F1).
        """
        model = self._model
        self.ensure_database()
        # charts in a dashboard filter's scope drop the SQL top-N LIMIT (it moves to
        # form_data) so the filter re-ranks after filtering — computable from the spec
        in_filter_scope = participating_chart_ids(spec, model) if model is not None else set()
        refs: list[ChartRef] = []
        datasets: list[DatasetRef] = []
        for chart in spec.charts:
            ds = self.ensure_dataset(
                chart.query,
                name=_dataset_name(spec.title, chart.id),
                apply_limit=chart.id not in in_filter_scope,
            )
            datasets.append(ds)
            refs.append(self.create_chart(chart, ds))
        return self.assemble_dashboard(spec, refs, datasets=datasets, model=model)
