"""HTTP-level integration tests for the O-2 per-IP/per-day LLM-call quota (opt-in,
config.py::session_rate_enabled) on the LLM-triggering session endpoints. Unit-level
sliding-window/lockout behavior is covered in tests/test_ratelimit.py; this file only
exercises the API wiring (auto_bi.api.app::_check_session_quota), mirroring the style of
test_api_auth.py's login-lockout test.
"""

from fastapi.testclient import TestClient

from auto_bi.adapters.base import DashboardRef
from auto_bi.api import create_app
from auto_bi.config import Settings
from tests.test_machine import CLEAR_REPORT, GOOD_SPEC, PATCHED_SPEC, ScriptedLLM


def _fake_builder(spec, log, session_id):
    log("BUILD done")
    return DashboardRef(id=7, title=spec.title, url="/superset/dashboard/7/")


def _client(llm, demo_model, *, client_ip: str = "1.2.3.4", **create_app_kwargs) -> TestClient:
    app = create_app(model=demo_model, llm=llm, builder=_fake_builder, **create_app_kwargs)
    return TestClient(app, client=(client_ip, 50000))


def _start(client: TestClient):
    return client.post("/api/v1/sessions", json={"request": "выручка по дням"})


def test_settings_default_off() -> None:
    settings = Settings(_env_file=None)
    assert settings.session_rate_enabled is False
    assert settings.session_rate_per_day == 100


def test_disabled_by_default_never_gates(demo_model) -> None:
    # config.py default is session_rate_enabled=False; pass an explicit tiny per_day
    # quota too, to prove disabling truly bypasses the gate rather than just defaulting
    # to a limit high enough not to trip within this test.
    llm = ScriptedLLM([CLEAR_REPORT, GOOD_SPEC] * 3)
    client = _client(llm, demo_model, session_rate_enabled=False, session_rate_per_day=1)
    for _ in range(3):
        r = _start(client)
        assert r.status_code == 200, r.text


def test_under_limit_passes_through(demo_model) -> None:
    llm = ScriptedLLM([CLEAR_REPORT, GOOD_SPEC])
    client = _client(llm, demo_model, session_rate_enabled=True, session_rate_per_day=2)
    r = _start(client)
    assert r.status_code == 200, r.text


def test_over_limit_returns_429_with_retry_after(demo_model) -> None:
    # only ONE script pair queued: if the quota didn't short-circuit BEFORE the LLM call,
    # the second /sessions call would drain an empty ScriptedLLM queue (IndexError) rather
    # than return a clean 429 — this proves the gate protects LLM spend, not just the HTTP
    # response.
    llm = ScriptedLLM([CLEAR_REPORT, GOOD_SPEC])
    client = _client(llm, demo_model, session_rate_enabled=True, session_rate_per_day=1)
    first = _start(client)
    assert first.status_code == 200, first.text
    second = _start(client)
    assert second.status_code == 429
    assert "Retry-After" in second.headers


def test_reply_shares_the_same_quota_as_sessions(demo_model) -> None:
    # /sessions and /reply both draw from the same per-IP bucket: the O-2 goal is
    # protecting overall LLM spend per caller, not a separate budget per endpoint.
    llm = ScriptedLLM([CLEAR_REPORT, GOOD_SPEC, PATCHED_SPEC])
    client = _client(llm, demo_model, session_rate_enabled=True, session_rate_per_day=2)
    sid = _start(client).json()["session_id"]
    ok = client.post(f"/api/v1/sessions/{sid}/reply", json={"text": "уточнение"})
    assert ok.status_code == 200, ok.text
    blocked = client.post(f"/api/v1/sessions/{sid}/reply", json={"text": "ещё"})
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


def test_per_ip_isolation(demo_model) -> None:
    # two TestClients pinned to different simulated client IPs, sharing one app instance
    # (and therefore one in-process limiter) — IP A exhausting its quota must not affect IP B.
    llm = ScriptedLLM([CLEAR_REPORT, GOOD_SPEC, CLEAR_REPORT, GOOD_SPEC])
    app = create_app(
        model=demo_model,
        llm=llm,
        builder=_fake_builder,
        session_rate_enabled=True,
        session_rate_per_day=1,
    )
    client_a = TestClient(app, client=("1.2.3.4", 50000))
    client_b = TestClient(app, client=("5.6.7.8", 50000))
    a1 = _start(client_a)
    assert a1.status_code == 200, a1.text
    a2 = _start(client_a)
    assert a2.status_code == 429  # IP A's daily quota is used up
    b1 = _start(client_b)
    assert b1.status_code == 200, b1.text  # a different IP is unaffected


def test_auto_session_is_never_gated(demo_model) -> None:
    # deterministic, no LLM (ARCHITECTURE "auto-overview") — the O-2 quota exists to
    # protect LLM spend, so this endpoint stays exempt even with a quota of 1.
    llm = ScriptedLLM([])
    client = _client(llm, demo_model, session_rate_enabled=True, session_rate_per_day=1)
    for _ in range(3):
        r = client.post("/api/v1/sessions/auto", json={"table": "dm.sales_daily"})
        assert r.status_code == 200, r.text
