"""BIAdapter seam (ARCHITECTURE §3.5): one spec -> N BI targets.

The protocol is a design invariant (CLAUDE.md S4): changing it requires updating
ARCHITECTURE.md first. plan_sol step 7 formalized ownership/cleanup/lifecycle that
used to hide behind optional getattr hooks (audit P1-1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from auto_bi.adapters.artifacts import BuildArtifact
from auto_bi.ir.spec import ChartQuery, ChartSpec, DashboardSpec

if TYPE_CHECKING:
    from auto_bi.agent.query_plan import PlanCache


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


@dataclass(frozen=True)
class BuildContext:
    """Per-build input for `BIAdapter.build` (plan_sol step 7 / audit P1-1).

    Replaces the optional concrete hooks `set_artifact_namespace` / `set_query_plans`
    that the pipeline used to call via getattr. A new adapter cannot "forget" namespace
    isolation by simply omitting a helper — the orchestrator always passes context.
    """

    namespace: str = ""
    plans: PlanCache | None = None
    session_id: str | None = None
    owner: str | None = None


@dataclass(frozen=True)
class BuildResult:
    """Per-build output: delivered dashboard + ownership ledger payload.

    Replaces post-build `drain_build_artifacts` getattr. Pipeline records
    `artifacts` into Store.bi_artifacts; public API still surfaces `dashboard`.

    Attribute aliases (`id`/`title`/`url`) mirror `DashboardRef` so unit tests that
    treated `build()` as returning a ref keep working during migration.
    """

    dashboard: DashboardRef
    artifacts: tuple[BuildArtifact, ...] = ()

    @property
    def id(self) -> int | str:
        return self.dashboard.id

    @property
    def title(self) -> str:
        return self.dashboard.title

    @property
    def url(self) -> str:
        return self.dashboard.url


# Methods every production adapter must expose. Factory validates once at construction
# so a partial fake fails loudly instead of silently skipping ownership/cleanup.
REQUIRED_ADAPTER_METHODS: tuple[str, ...] = (
    "healthcheck",
    "ensure_database",
    "ensure_dataset",
    "create_chart",
    "assemble_dashboard",
    "build",
    "delete_artifact",
    "close",
)


def validate_adapter_contract(adapter: object) -> None:
    """Raise TypeError if *adapter* is missing any required BIAdapter method."""
    missing = [
        name for name in REQUIRED_ADAPTER_METHODS if not callable(getattr(adapter, name, None))
    ]
    if missing:
        raise TypeError(
            f"{type(adapter).__name__} does not satisfy BIAdapter contract; "
            f"missing: {', '.join(missing)}"
        )


class BIAdapter(Protocol):
    def healthcheck(self) -> AdapterHealth: ...

    def ensure_database(self, dwh: DWHConfig) -> DatabaseRef: ...

    def ensure_dataset(self, query: ChartQuery) -> DatasetRef: ...

    def create_chart(self, chart: ChartSpec, ds: DatasetRef) -> ChartRef: ...

    def assemble_dashboard(self, spec: DashboardSpec, charts: list[ChartRef]) -> DashboardRef: ...

    # Orchestrator entry point: full compile (database -> datasets -> charts -> dashboard).
    # `ctx` carries namespace / query plans / session identity (plan_sol step 7). Semantic
    # model stays constructor-injected so both adapters share this signature.
    def build(self, spec: DashboardSpec, ctx: BuildContext | None = None) -> BuildResult: ...

    # Ownership live-cleanup (ledger prune / `auto_bi prune`) — required, not getattr.
    def delete_artifact(self, kind: str, native_id: str) -> None: ...

    # HTTP pool release (D-2 lifecycle) — required, not getattr.
    def close(self) -> None: ...
