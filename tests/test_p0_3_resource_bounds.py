"""Audit P0-3: concurrent build cap, work quota, fail-closed remote defaults."""

from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient

from auto_bi.adapters.base import DashboardRef
from auto_bi.api import create_app
from auto_bi.config import Settings
from tests.test_machine import CLEAR_REPORT, GOOD_SPEC, ScriptedLLM


def _fake_builder(spec, log, session_id):
    log("BUILD done")
    return DashboardRef(id=7, title=spec.title, url="/superset/dashboard/7/")


def _client(llm, demo_model, *, client_ip: str = "1.2.3.4", **kwargs) -> TestClient:
    app = create_app(model=demo_model, llm=llm, builder=_fake_builder, **kwargs)
    return TestClient(app, client=(client_ip, 50000))


def test_settings_p0_3_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.work_rate_enabled is False
    assert s.work_rate_per_day == 50
    assert s.max_concurrent_builds == 2
    assert s.allow_insecure_remote is False


def test_work_rate_gates_auto_when_enabled(demo_model) -> None:
    llm = ScriptedLLM([])
    client = _client(llm, demo_model, work_rate_enabled=True, work_rate_per_day=1)
    first = client.post("/api/v1/sessions/auto", json={"table": "dm.sales_daily"})
    assert first.status_code == 200, first.text
    second = client.post("/api/v1/sessions/auto", json={"table": "dm.sales_daily"})
    assert second.status_code == 429
    assert "Retry-After" in second.headers


def test_demo_auto_only_forces_work_rate(demo_model) -> None:
    # demo profile must not leave auto/approve unbounded even if work_rate_enabled is off
    llm = ScriptedLLM([])
    client = _client(
        llm, demo_model, demo_auto_only=True, work_rate_enabled=False, work_rate_per_day=1
    )
    first = client.post("/api/v1/sessions/auto", json={"table": "dm.sales_daily"})
    assert first.status_code == 200, first.text
    second = client.post("/api/v1/sessions/auto", json={"table": "dm.sales_daily"})
    assert second.status_code == 429


def test_session_rate_still_exempts_auto(demo_model) -> None:
    # regression: O-2 LLM quota must not gate auto (no LLM spend)
    llm = ScriptedLLM([])
    client = _client(llm, demo_model, session_rate_enabled=True, session_rate_per_day=1)
    for _ in range(3):
        r = client.post("/api/v1/sessions/auto", json={"table": "dm.sales_daily"})
        assert r.status_code == 200, r.text


def test_max_concurrent_builds_returns_503(demo_model) -> None:
    # Slot is acquired in approve *before* the worker thread starts, so a second
    # approve while the first build is still running must 503 regardless of how
    # far the worker has progressed.
    release = threading.Event()

    def blocking_builder(spec, log, session_id):
        release.wait(timeout=5)
        log("BUILD done")
        return DashboardRef(id=7, title=spec.title, url="/superset/dashboard/7/")

    llm = ScriptedLLM([CLEAR_REPORT, GOOD_SPEC, CLEAR_REPORT, GOOD_SPEC])
    app = create_app(
        model=demo_model,
        llm=llm,
        builder=blocking_builder,
        max_concurrent_builds=1,
    )
    client = TestClient(app)

    s1 = client.post("/api/v1/sessions", json={"request": "выручка по дням"}).json()["session_id"]
    s2 = client.post("/api/v1/sessions", json={"request": "выручка по дням"}).json()["session_id"]

    a1 = client.post(f"/api/v1/sessions/{s1}/approve")
    assert a1.status_code == 202, a1.text

    a2 = client.post(f"/api/v1/sessions/{s2}/approve")
    assert a2.status_code == 503, a2.text
    assert "Retry-After" in a2.headers

    release.set()
    deadline = time.time() + 5
    while time.time() < deadline:
        st = client.get(f"/api/v1/sessions/{s1}").json()
        if st["build_status"] in ("built", "failed"):
            break
        time.sleep(0.05)
    assert client.get(f"/api/v1/sessions/{s1}").json()["build_status"] == "built"
