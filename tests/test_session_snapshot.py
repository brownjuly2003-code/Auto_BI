"""plan_sol step 8: SessionSnapshot + terminal transitions under one lock."""

from __future__ import annotations

import threading
import time

from auto_bi.agent.machine import AgentPhase, AgentSession
from auto_bi.api.sessions import (
    BUILD_STATUS_BUILT,
    BUILD_STATUS_DEGRADED,
    BUILD_STATUS_FAILED,
    ManagedSession,
    SessionSnapshot,
)
from auto_bi.ir.spec import TargetBI
from tests.test_propose import FakeLLM


def _managed() -> ManagedSession:
    from tests.conftest import demo_model

    agent = AgentSession(
        model=demo_model.__wrapped__(),
        llm=FakeLLM([]),
        advisor=None,
    )
    # Snapshot tests only need phase; no turns are run.
    agent.phase = AgentPhase.APPROVED
    return ManagedSession("sid-test", agent, target_bi=TargetBI.SUPERSET)


def test_snapshot_is_immutable_and_consistent() -> None:
    managed = _managed()
    managed.build_status = "building"
    managed.dashboard_url = ""
    snap = managed.snapshot()
    assert isinstance(snap, SessionSnapshot)
    assert snap.session_id == "sid-test"
    assert snap.phase == AgentPhase.APPROVED.value
    assert snap.build_status == "building"
    assert snap.dashboard_url == ""
    # frozen: cannot mutate
    try:
        snap.build_status = "built"  # type: ignore[misc]
        raised = False
    except Exception:
        raised = True
    assert raised


def test_apply_build_success_sets_status_url_and_event_together() -> None:
    managed = _managed()
    managed.build_status = "building"
    managed.apply_build_success("/superset/dashboard/1/", title="Dash")
    assert managed.build_status == BUILD_STATUS_BUILT
    assert managed.dashboard_url == "/superset/dashboard/1/"
    events = list(managed.stream_events(poll_seconds=0.01))
    assert events[-1] is not None
    assert events[-1].kind == "done"
    assert events[-1].url == "/superset/dashboard/1/"


def test_apply_build_success_degraded_flag() -> None:
    managed = _managed()
    managed.apply_build_success("/d/2/", title="T", degraded=True)
    assert managed.build_status == BUILD_STATUS_DEGRADED
    assert managed.dashboard_url == "/d/2/"


def test_apply_build_failure_sets_status_and_error_event() -> None:
    managed = _managed()
    managed.build_status = "building"
    managed.apply_build_failure("public error msg")
    assert managed.build_status == BUILD_STATUS_FAILED
    events = list(managed.stream_events(poll_seconds=0.01))
    assert events[-1] is not None
    assert events[-1].kind == "error"
    assert "public error" in (events[-1].text or "")


def test_concurrent_snapshot_never_sees_built_without_url() -> None:
    """Readers under snapshot() must not observe split status/URL mid-transition."""
    managed = _managed()
    managed.build_status = "building"
    managed.dashboard_url = ""
    bad: list[SessionSnapshot] = []
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            snap = managed.snapshot()
            if (
                snap.build_status in (BUILD_STATUS_BUILT, BUILD_STATUS_DEGRADED)
                and not snap.dashboard_url
            ):
                bad.append(snap)
            time.sleep(0.0005)

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    # give the reader a moment, then apply success (status+url under one lock)
    time.sleep(0.02)
    managed.apply_build_success("/superset/dashboard/99/", title="OK")
    time.sleep(0.05)
    stop.set()
    t.join(timeout=1)
    assert bad == []
    final = managed.snapshot()
    assert final.build_status == BUILD_STATUS_BUILT
    assert final.dashboard_url == "/superset/dashboard/99/"
