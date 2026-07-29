"""RR-4 RED: pre-return BaseException leaves remote BI side effects unreconciled.

Fault-injection against *current* production recovery only. No new adapter seam or
Store attempt schema is implemented here — the final assertion encodes the desired
safety property from ADR 0002 and is expected to fail on base HEAD.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from auto_bi.adapters.artifacts import stable_build_token
from auto_bi.adapters.base import (
    AdapterHealth,
    BuildContext,
    BuildResult,
    ChartRef,
    DashboardRef,
    DatabaseRef,
    DatasetRef,
    DWHConfig,
)
from auto_bi.agent.pipeline import compile_and_build
from auto_bi.agent.sql_guard import LiveSQLValidator
from auto_bi.ir.spec import ChartQuery, ChartSpec, DashboardSpec
from auto_bi.store import Store
from tests.test_pipeline import demo_model_fixtureless, stub_run_query
from tests.test_propose import GOOD_SPEC


class SimulatedProcessDeath(BaseException):
    """Process-death class fault: bypasses ``except Exception`` in pipeline and adapters.

    Not ``SystemExit`` / ``KeyboardInterrupt`` — those interact with the test runner.
    """


@dataclass
class FakeRemoteRegistry:
    """In-process stand-in for remote BI entities, keyed by ``BuildContext.namespace``."""

    # namespace -> list of created entities (kind, native_id, name)
    by_namespace: dict[str, list[dict[str, str]]] = field(default_factory=dict)

    def create(self, namespace: str, *, kind: str, native_id: str, name: str) -> None:
        self.by_namespace.setdefault(namespace, []).append(
            {"kind": kind, "native_id": native_id, "name": name}
        )

    def entities(self, namespace: str) -> list[dict[str, str]]:
        return list(self.by_namespace.get(namespace, []))

    def delete(self, namespace: str, native_id: str) -> None:
        rows = self.by_namespace.get(namespace, [])
        self.by_namespace[namespace] = [r for r in rows if r["native_id"] != native_id]


@dataclass
class CrashBeforeReturnAdapter:
    """Satisfies the call surface ``compile_and_build`` uses before/around ``build``.

    Creates one remote entity under the build namespace, then raises
    ``SimulatedProcessDeath`` so no ``BuildResult`` (and thus no ledger payload) returns.
    """

    remote: FakeRemoteRegistry
    closed: bool = False
    last_ctx: BuildContext | None = None

    def healthcheck(self) -> AdapterHealth:
        return AdapterHealth(ok=True)

    def ensure_database(self, dwh: DWHConfig | None = None) -> DatabaseRef:
        return DatabaseRef(id="db-shared", name="fake-db")

    def ensure_dataset(self, query: ChartQuery, name: str | None = None, **kwargs) -> DatasetRef:
        return DatasetRef(id="ds-unused", name=name or "ds")

    def create_chart(self, chart: ChartSpec, ds: DatasetRef, **kwargs) -> ChartRef:
        return ChartRef(id="c-unused", name=chart.id)

    def assemble_dashboard(
        self, spec: DashboardSpec, charts: list[ChartRef], **kwargs
    ) -> DashboardRef:
        return DashboardRef(id="d-unused", title=spec.title, url="/fake/unused")

    def build(self, spec: DashboardSpec, ctx: BuildContext | None = None) -> BuildResult:
        self.last_ctx = ctx
        namespace = (ctx.namespace if ctx is not None else "") or ""
        assert namespace, "pipeline must supply BuildContext.namespace before remote work"
        # First successful remote side effect for this attempt (RR-4 window opens here).
        self.remote.create(
            namespace,
            kind="dataset",
            native_id="remote-ds-1",
            name=f"auto_bi__partial__{namespace}",
        )
        # Process death / BaseException before BuildResult — memory ledger never returns.
        raise SimulatedProcessDeath(
            f"injected crash after remote create in namespace={namespace!r}"
        )

    def delete_artifact(self, kind: str, native_id: str) -> None:
        # Current startup recovery never calls this without durable native ids.
        for ns, rows in list(self.remote.by_namespace.items()):
            for row in rows:
                if row["kind"] == kind and row["native_id"] == native_id:
                    self.remote.delete(ns, native_id)
                    return

    def close(self) -> None:
        self.closed = True


def _startup_recovery(store: Store) -> list[str]:
    """Mirror ``auto_bi.cli`` serve startup: reap stuck builds, then pending ledgers."""
    reaped = store.reap_stuck_builds()
    store.reconcile_pending_ledgers()
    return reaped


def test_pre_return_baseexception_leaves_remote_entity_unreconciled_after_startup(
    tmp_path,
) -> None:
    """RED characterization of RR-4: current recovery has no native artifact evidence.

    Desired safety property (ADR 0002): after restart recovery, every remote entity
    created under the interrupted attempt's stable namespace is either present in the
    durable ownership ledger or removed from the remote registry.
    """
    db_path = tmp_path / "rr4.sqlite"
    remote = FakeRemoteRegistry()
    adapter = CrashBeforeReturnAdapter(remote=remote)

    store = Store(db_path)
    sid = store.create_session("rr4 pre-return crash", owner="alice")
    spec = DashboardSpec.model_validate(GOOD_SPEC)
    spec_id = store.save_spec(sid, spec.model_dump(mode="json"), status="approved")
    expected_namespace = stable_build_token(sid, spec_id)

    with pytest.raises(SimulatedProcessDeath):
        compile_and_build(
            spec,
            demo_model_fixtureless(),
            LiveSQLValidator(stub_run_query),
            adapter_for=lambda _t: adapter,
            store=store,
            session_id=sid,
            spec_id=spec_id,
            log=lambda _s: None,
            prune_orphans=False,
        )

    # BaseException bypassed commit_build_failure; session remains mid-build until reopen.
    assert store.session_status(sid) == "building"
    assert store.bi_artifacts(sid) == []
    assert remote.entities(expected_namespace), "fault must leave a remote side effect"
    assert adapter.last_ctx is not None
    assert adapter.last_ctx.namespace == expected_namespace
    store.close()

    # Restart: new Store handle + current CLI recovery only.
    store2 = Store(db_path)
    reaped = _startup_recovery(store2)
    assert sid in reaped
    assert store2.session_status(sid) == "failed"
    builds = store2.builds(sid)
    assert builds, "reap_stuck_builds should insert a synthetic failed build row"
    assert all(not (b.get("build_token") or "") for b in builds)
    assert store2.bi_artifacts(sid) == []

    remote_after = remote.entities(expected_namespace)
    ledger_ids = {a["native_id"] for a in store2.bi_artifacts(sid)}
    remote_ids = {e["native_id"] for e in remote_after}

    # Preconditions that document the current gap (must hold for a meaningful RED).
    assert remote_ids, "remote entity still present after startup recovery"
    assert not ledger_ids, "no durable native artifact evidence after startup recovery"

    # --- RR-4 safety property (not implemented): this assertion is the intended RED ---
    # After startup recovery, remote side effects of the interrupted attempt must be
    # reconciled: either cleaned from the remote registry or finalized into the ledger.
    try:
        assert remote_ids <= ledger_ids or not remote_ids, (
            "RR-4: interrupted pre-return build left remote entities unreconciled; "
            f"namespace={expected_namespace!r} remote_ids={sorted(remote_ids)} "
            f"ledger_ids={sorted(ledger_ids)}"
        )
    finally:
        store2.close()
