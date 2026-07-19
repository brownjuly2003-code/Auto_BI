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

from collections.abc import Callable

from auto_bi.adapters.base import AdapterHealth, BIAdapter, DWHConfig
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
        superset_client = SupersetClient(
            settings.superset_url, settings.superset_user, settings.superset_password
        )
        return SupersetAdapter(
            superset_client, dwh, model, strict_connection=settings.bi_connection_strict
        )

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
        datalens_client = DataLensClient(
            settings.datalens_url, settings.datalens_user, settings.datalens_password
        )
        return DataLensAdapter(
            datalens_client,
            dwh,
            model,
            settings.datalens_workbook_id,
            strict_connection=settings.bi_connection_strict,
        )

    raise ValueError(f"unsupported BI target: {target_bi!r}")


def close_adapter(adapter: BIAdapter) -> None:
    """Release the adapter's HTTP pool, if it has one (D-2 lifecycle).

    `close()` is a concrete helper on both real adapters, NOT part of the BIAdapter
    Protocol (S4 — like drain_build_artifacts), so release goes through getattr: fakes
    and minimal adapters without a pool are fine to pass here.
    """
    close = getattr(adapter, "close", None)
    if callable(close):
        close()


def probe_health(
    adapter_for: Callable[[TargetBI], BIAdapter], target_bi: TargetBI
) -> AdapterHealth:
    """Healthcheck `target_bi` through a throwaway adapter, releasing its HTTP pool.

    Readiness probes (`/ready`) construct an adapter per call; without the release each
    probe leaked one connection pool for the life of the process (D-2 lifecycle) —
    the demo keepalive alone pings readiness a few times a minute.
    """
    adapter = adapter_for(target_bi)
    try:
        return adapter.healthcheck()
    finally:
        close_adapter(adapter)
