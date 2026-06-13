"""BIAdapter seam (ARCHITECTURE §3.5): one spec -> N BI targets.

The protocol is a design invariant (CLAUDE.md S4): changing it requires updating
ARCHITECTURE.md first.
"""

from dataclasses import dataclass
from typing import Protocol

from auto_bi.ir.spec import ChartQuery, ChartSpec, DashboardSpec


@dataclass(frozen=True)
class DWHConfig:
    host: str
    port: int
    database: str
    user: str
    password: str
    engine: str = "clickhouse"


@dataclass(frozen=True)
class AdapterHealth:
    ok: bool
    message: str = ""


# Ref ids hold the BI-native entity identifier: Superset returns ints, DataLens returns
# string entry ids. Typed `int | str` so one Protocol serves both — refs are consumed only
# inside their own adapter (never in generic code), so no caller has to discriminate, and
# the Superset path keeps emitting ints unchanged (S4-2, 2026-06-13; see ARCHITECTURE §3.5).
@dataclass(frozen=True)
class DatabaseRef:
    id: int | str
    name: str


@dataclass(frozen=True)
class DatasetRef:
    id: int | str
    name: str


@dataclass(frozen=True)
class ChartRef:
    id: int | str
    name: str


@dataclass(frozen=True)
class DashboardRef:
    id: int | str
    title: str
    url: str


class BIAdapter(Protocol):
    def healthcheck(self) -> AdapterHealth: ...

    def ensure_database(self, dwh: DWHConfig) -> DatabaseRef: ...

    def ensure_dataset(self, query: ChartQuery) -> DatasetRef: ...

    def create_chart(self, chart: ChartSpec, ds: DatasetRef) -> ChartRef: ...

    def assemble_dashboard(self, spec: DashboardSpec, charts: list[ChartRef]) -> DashboardRef: ...
