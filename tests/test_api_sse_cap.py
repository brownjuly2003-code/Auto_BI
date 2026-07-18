"""C-7: bounded concurrent SSE consumers — the N+1th stream gets 429 + Retry-After and
a rejected consumer never costs build events.

TestClient buffers streaming responses (the server generator finishes before the body
is read), so true concurrent connections cannot be held here. The gate itself is
unit-tested directly; the endpoint contract (429/Retry-After when saturated, full
event replay after release) is exercised by saturating `app.state.sse_gate`.
"""

from fastapi.testclient import TestClient

from auto_bi.api import create_app
from auto_bi.api.ratelimit import SSEGate
from auto_bi.store import Store
from tests.test_api import collect_events, fake_builder, start
from tests.test_machine import CLEAR_REPORT, ScriptedLLM
from tests.test_propose import GOOD_SPEC

# --- gate mechanics -------------------------------------------------------


def test_gate_per_session_cap() -> None:
    gate = SSEGate(max_total=10, max_per_session=2)
    assert gate.acquire("a") and gate.acquire("a")
    assert not gate.acquire("a")  # 3rd consumer of the same session
    assert gate.acquire("b")  # other sessions unaffected
    gate.release("a")
    assert gate.acquire("a")  # slot freed


def test_gate_global_cap() -> None:
    gate = SSEGate(max_total=2, max_per_session=0)  # per-session unlimited
    assert gate.acquire("a") and gate.acquire("a")
    assert not gate.acquire("b")  # global cap hit even for a fresh session
    gate.release("a")
    assert gate.acquire("b")


def test_gate_zero_means_unlimited() -> None:
    gate = SSEGate(max_total=0, max_per_session=0)
    for _ in range(100):
        assert gate.acquire("a")
    assert gate.active() == 100


def test_gate_release_never_goes_negative() -> None:
    gate = SSEGate(max_total=1, max_per_session=1)
    gate.release("ghost")  # spurious release must not create capacity debt
    assert gate.active() == 0
    assert gate.acquire("a")
    assert not gate.acquire("a")


# --- endpoint contract ----------------------------------------------------


def _approved_client(demo_model, tmp_path) -> tuple[TestClient, str]:
    app = create_app(
        model=demo_model,
        llm=ScriptedLLM([CLEAR_REPORT, GOOD_SPEC]),
        store=Store(tmp_path / "sse.sqlite"),
        builder=fake_builder,
        sse_max_streams=1,
        sse_max_streams_per_session=1,
    )
    client = TestClient(app)
    sid = start(client)["session_id"]
    assert client.post(f"/api/v1/sessions/{sid}/approve").status_code == 202
    return client, sid


def test_saturated_gate_returns_429_and_events_survive(demo_model, tmp_path) -> None:
    client, sid = _approved_client(demo_model, tmp_path)
    gate = client.app.state.sse_gate
    assert gate.acquire(sid)  # simulate the N connections already streaming
    try:
        refused = client.get(f"/api/v1/sessions/{sid}/events")
        assert refused.status_code == 429
        assert refused.headers.get("Retry-After")
    finally:
        gate.release(sid)
    # the refused consumer swallowed nothing: a fresh stream replays the full history
    events = collect_events(client, sid)
    assert [e["kind"] for e in events] == ["log", "log", "done"]
    assert gate.active() == 0  # the endpoint's finally released its slot


def test_stream_releases_slot_after_completion(demo_model, tmp_path) -> None:
    client, sid = _approved_client(demo_model, tmp_path)
    for _ in range(3):  # sequential streams under cap=1 all succeed
        events = collect_events(client, sid)
        assert events[-1]["kind"] == "done"
    assert client.app.state.sse_gate.active() == 0
