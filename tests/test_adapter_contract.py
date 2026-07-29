"""plan_sol step 7: BIAdapter contract suite (BuildContext / BuildResult / lifecycle).

A fake third adapter cannot pass without ownership ledger + delete + close.
Characterization: pipeline records artifacts from BuildResult, not getattr drain.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from auto_bi.adapters.artifacts import BuildArtifact
from auto_bi.adapters.base import (
    REQUIRED_ADAPTER_METHODS,
    AdapterHealth,
    BuildAttempt,
    BuildContext,
    BuildReconcileResult,
    BuildResult,
    ChartRef,
    DashboardRef,
    DatabaseRef,
    DatasetRef,
    DWHConfig,
    validate_adapter_contract,
)
from auto_bi.adapters.factory import close_adapter, make_adapter
from auto_bi.agent.pipeline import compile_and_build
from auto_bi.agent.sql_guard import LiveSQLValidator
from auto_bi.config import Settings
from auto_bi.ir.spec import ChartQuery, ChartSpec, DashboardSpec, TargetBI
from auto_bi.store import Store
from tests.test_pipeline import demo_model_fixtureless, stub_run_query
from tests.test_propose import GOOD_SPEC

# --- factory contract validation ----------------------------------------------------


def test_make_adapter_validates_full_contract() -> None:
    model = demo_model_fixtureless()
    for target in (TargetBI.SUPERSET, TargetBI.DATALENS):
        adapter = make_adapter(target, Settings(_env_file=None), model)
        validate_adapter_contract(adapter)
        # no network: close must still be safe on a never-used client
        close_adapter(adapter)


def test_validate_adapter_contract_rejects_partial_fake() -> None:
    class Partial:
        def healthcheck(self) -> AdapterHealth:
            return AdapterHealth(ok=True)

        def build(self, spec, ctx=None) -> BuildResult:
            return BuildResult(dashboard=DashboardRef(id=1, title="t", url="/x"))

    with pytest.raises(TypeError, match="missing"):
        validate_adapter_contract(Partial())


def test_required_methods_cover_ownership_and_lifecycle() -> None:
    assert "build" in REQUIRED_ADAPTER_METHODS
    assert "reconcile_build_attempt" in REQUIRED_ADAPTER_METHODS
    assert "delete_artifact" in REQUIRED_ADAPTER_METHODS
    assert "close" in REQUIRED_ADAPTER_METHODS
    assert "set_artifact_namespace" not in REQUIRED_ADAPTER_METHODS
    assert "drain_build_artifacts" not in REQUIRED_ADAPTER_METHODS


# --- minimal third adapter that satisfies the contract ------------------------------


@dataclass
class FakeThirdAdapter:
    """Minimal third-party-shaped adapter: required surface only, no getattr hooks."""

    closed: bool = False
    deleted: list[tuple[str, str]] = field(default_factory=list)
    last_ctx: BuildContext | None = None
    _arts: list[BuildArtifact] = field(default_factory=list)

    def healthcheck(self) -> AdapterHealth:
        return AdapterHealth(ok=True)

    def ensure_database(self, dwh: DWHConfig | None = None) -> DatabaseRef:
        return DatabaseRef(id="db1", name="fake-db")

    def ensure_dataset(self, query: ChartQuery, name: str | None = None, **kwargs) -> DatasetRef:
        return DatasetRef(id="ds1", name=name or "ds")

    def create_chart(self, chart: ChartSpec, ds: DatasetRef, **kwargs) -> ChartRef:
        return ChartRef(id="c1", name=chart.id)

    def assemble_dashboard(
        self, spec: DashboardSpec, charts: list[ChartRef], **kwargs
    ) -> DashboardRef:
        return DashboardRef(id="d1", title=spec.title, url="/fake/d1")

    def build(self, spec: DashboardSpec, ctx: BuildContext | None = None) -> BuildResult:
        self.last_ctx = ctx
        ns = (ctx.namespace if ctx else "") or "none"
        dash = DashboardRef(id="d1", title=spec.title, url=f"/fake/{ns}")
        arts = (
            BuildArtifact("dashboard", "d1", spec.title),
            BuildArtifact("chart", "c1", "c1", "dm.sales_daily"),
        )
        return BuildResult(dashboard=dash, artifacts=arts)

    def delete_artifact(self, kind: str, native_id: str) -> None:
        self.deleted.append((kind, native_id))

    def reconcile_build_attempt(self, attempt: BuildAttempt) -> BuildReconcileResult:
        return BuildReconcileResult()

    def close(self) -> None:
        self.closed = True


def test_fake_third_adapter_passes_contract_and_pipeline_ledger(tmp_path) -> None:
    fake = FakeThirdAdapter()
    validate_adapter_contract(fake)
    store = Store(tmp_path / "s.sqlite")
    sid = store.create_session("contract")
    spec = DashboardSpec.model_validate(GOOD_SPEC)
    spec_id = store.save_spec(sid, spec.model_dump(mode="json"))
    ref = compile_and_build(
        spec,
        demo_model_fixtureless(),
        LiveSQLValidator(stub_run_query),
        adapter_for=lambda _t: fake,
        store=store,
        session_id=sid,
        spec_id=spec_id,
        log=lambda s: None,
        prune_orphans=False,
    )
    assert ref.url.startswith("/fake/")
    assert fake.closed
    assert fake.last_ctx is not None
    assert fake.last_ctx.namespace  # pipeline always supplies build_token namespace
    arts = store.bi_artifacts(sid)
    kinds = {a["kind"] for a in arts}
    assert "dashboard" in kinds
    assert "chart" in kinds
    store.close()


def test_pipeline_passes_plans_in_build_context() -> None:
    fake = FakeThirdAdapter()
    compile_and_build(
        DashboardSpec.model_validate(GOOD_SPEC),
        demo_model_fixtureless(),
        LiveSQLValidator(stub_run_query),
        adapter_for=lambda _t: fake,
        log=lambda s: None,
        prune_orphans=False,
    )
    assert fake.last_ctx is not None
    # SQL guard fills PlanCache; empty cache is still a PlanCache instance, not None
    assert fake.last_ctx.plans is not None


def test_close_adapter_requires_close_method() -> None:
    with pytest.raises(AttributeError):
        close_adapter(object())  # type: ignore[arg-type]


def test_build_result_proxies_dashboard_fields() -> None:
    dash = DashboardRef(id=9, title="T", url="/u")
    result = BuildResult(dashboard=dash, artifacts=(BuildArtifact("dashboard", "9", "T"),))
    assert result.id == 9
    assert result.title == "T"
    assert result.url == "/u"
    assert len(result.artifacts) == 1
