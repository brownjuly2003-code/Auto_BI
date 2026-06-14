"""Adapter factory: `spec.target_bi` -> a fully wired BIAdapter (Phase 4 F1).

The build pipeline (cli/api) dispatches one IR spec to either BI target by calling this
factory with `spec.target_bi`, instead of hardcoding a single adapter. Clients, DWH config
and the semantic model are wired here from settings so the pipeline stays target-agnostic
(it only handles the resolver `Callable[[TargetBI], BIAdapter]`).

DWH note: both live BI-build paths target ClickHouse (the demo/main DM); the connection
host differs per BI (Superset reaches CH via `ch_host_from_bi`, the self-hosted DataLens
stand via `ch_host_from_datalens`). Building dashboards over a Greenplum DM through a BI is
not a verified path yet (GP is wired for introspection/advisor, ARCHITECTURE §3.4) — so the
DWHConfig engine stays ClickHouse here regardless of the model's introspection engine.
"""

from __future__ import annotations

from auto_bi.adapters.base import BIAdapter, DWHConfig
from auto_bi.config import Settings
from auto_bi.ir.spec import TargetBI
from auto_bi.semantic.model import SemanticModel


def make_adapter(target_bi: TargetBI, settings: Settings, model: SemanticModel) -> BIAdapter:
    """Build the adapter for `target_bi`, wired from settings + the semantic model."""
    if target_bi == TargetBI.SUPERSET:
        from auto_bi.adapters.superset.adapter import SupersetAdapter
        from auto_bi.adapters.superset.client import SupersetClient

        dwh = DWHConfig(
            host=settings.ch_host_from_bi or settings.ch_host,
            port=settings.ch_port_from_bi or settings.ch_port,
            database=settings.ch_database,
            user=settings.ch_user,
            password=settings.ch_password,
        )
        client = SupersetClient(
            settings.superset_url, settings.superset_user, settings.superset_password
        )
        return SupersetAdapter(client, dwh, model)

    if target_bi == TargetBI.DATALENS:
        from auto_bi.adapters.datalens.adapter import DataLensAdapter
        from auto_bi.adapters.datalens.client import DataLensClient

        dwh = DWHConfig(
            host=settings.ch_host_from_datalens,
            port=settings.ch_port,
            database=settings.ch_database,
            user=settings.ch_user,
            password=settings.ch_password,
        )
        client = DataLensClient(
            settings.datalens_url, settings.datalens_user, settings.datalens_password
        )
        return DataLensAdapter(client, dwh, model, settings.datalens_workbook_id)

    raise ValueError(f"unsupported BI target: {target_bi!r}")
