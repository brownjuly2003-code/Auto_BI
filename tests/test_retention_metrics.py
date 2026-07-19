"""Store retention sweep + Prometheus metrics endpoint (audit D-3).

Two halves of one pointfrom: the telemetry tables stop growing without bound, and an
operator can see both that growth and the spend it comes from.
"""

import pytest
from fastapi.testclient import TestClient

from auto_bi.api import create_app
from auto_bi.api.metrics import LiveMetrics, render
from auto_bi.auth import hash_password
from auto_bi.llm.budget import parse_prices
from auto_bi.store import Store
from tests.test_machine import ScriptedLLM


def _store(tmp_path) -> Store:
    return Store(tmp_path / "s.sqlite")


def _age(store: Store, table: str, row_id: int, days: int) -> None:
    with store._lock, store._db:
        store._db.execute(
            f"UPDATE {table} SET created_at = datetime('now', ?) WHERE id = ?",
            (f"-{days} days", row_id),
        )


def _log_call(store: Store, session_id: str | None = None) -> int:
    return store.log_llm_call(
        session_id=session_id,
        model="claude-sonnet-5",
        prompt_sha256="a" * 64,
        prompt_chars=10,
        reasoning=False,
        status="ok",
        latency_ms=1200,
        step="propose",
        completion_chars=5,
        input_tokens=1000,
        output_tokens=500,
    )


# --- retention ------------------------------------------------------------------------


def test_purge_drops_aged_rows_and_keeps_fresh_ones(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.create_session("r")
    old_call, fresh_call = _log_call(store, sid), _log_call(store, sid)
    _age(store, "llm_calls", old_call, days=100)
    old_trace = store.add_trace_event(sid, kind="build_start")
    store.add_trace_event(sid, kind="build_done")
    _age(store, "trace_events", old_trace, days=40)

    deleted = store.purge_retention(llm_calls_days=90, trace_events_days=30)

    assert deleted == {"llm_calls": 1, "trace_events": 1}
    assert [r["id"] for r in store.llm_calls(sid)] == [fresh_call]
    assert [r["kind"] for r in store.trace_events(sid)] == ["build_done"]
    store.close()


def test_purge_skips_a_table_whose_limit_is_zero(tmp_path) -> None:
    """0 days = keep that ledger forever (cost accounting) while still sweeping the rest."""
    store = _store(tmp_path)
    sid = store.create_session("r")
    _age(store, "llm_calls", _log_call(store, sid), days=1000)
    _age(store, "trace_events", store.add_trace_event(sid, kind="build_start"), days=1000)

    deleted = store.purge_retention(llm_calls_days=0, trace_events_days=30)

    assert deleted == {"trace_events": 1}
    assert len(store.llm_calls(sid)) == 1
    store.close()


def test_purge_never_drops_a_live_bi_artifact(tmp_path) -> None:
    """A live row is what ownership cleanup selects on — ageing it out would strand a real
    dashboard in the BI with nothing recording that we own it."""
    store = _store(tmp_path)
    sid = store.create_session("r")
    live = store.record_bi_artifact(
        session_id=sid, build_token="b1", target_bi="superset", kind="dashboard", native_id="7"
    )
    dead = store.record_bi_artifact(
        session_id=sid, build_token="b0", target_bi="superset", kind="dashboard", native_id="6"
    )
    store.mark_bi_artifacts_superseded([dead])
    for row_id in (live, dead):
        _age(store, "bi_artifacts", row_id, days=365)

    deleted = store.purge_retention(bi_artifacts_days=30)

    assert deleted == {"bi_artifacts": 1}
    remaining = store.metrics_snapshot()["table_rows"]["bi_artifacts"]
    assert remaining == 1
    store.close()


def test_purge_never_touches_the_users_own_work(tmp_path) -> None:
    """Sessions/messages/specs/builds are conversation history, not telemetry."""
    store = _store(tmp_path)
    sid = store.create_session("r")
    store.add_message(sid, "user", "выручка по дням")
    spec_id = store.save_spec(sid, {"title": "t", "charts": []})
    store.save_build(sid, spec_id, dashboard_id=7, url="/d/7/", status="ok")
    for table, row in (("sessions", None), ("messages", 1), ("specs", spec_id), ("builds", 1)):
        if row is not None:
            _age(store, table, row, days=9999)

    store.purge_retention(llm_calls_days=1, trace_events_days=1, bi_artifacts_days=1)

    assert store.session_row(sid) is not None
    assert len(store.messages(sid)) == 1
    assert len(store.specs(sid)) == 1
    assert len(store.builds(sid)) == 1
    store.close()


# --- live counters --------------------------------------------------------------------


def test_live_metrics_track_builds_and_dwh_queries() -> None:
    live = LiveMetrics(build_slots_total=2)
    live.build_started()
    live.build_started()
    assert live.snapshot()["builds_in_flight"] == 2
    live.build_finished()
    assert live.snapshot()["builds_in_flight"] == 1
    live.dwh_query(0.25)
    live.dwh_query(0.75)
    snap = live.snapshot()
    assert snap["dwh_queries"] == 2
    assert snap["dwh_seconds"] == pytest.approx(1.0)


def test_build_gauge_never_goes_negative() -> None:
    """A double release would otherwise poison the gauge for the process's lifetime."""
    live = LiveMetrics()
    live.build_started()
    live.build_finished()
    live.build_finished()
    assert live.snapshot()["builds_in_flight"] == 0


# --- rendering ------------------------------------------------------------------------


def test_render_prices_spend_with_the_budget_price_table(tmp_path) -> None:
    store = _store(tmp_path)
    _log_call(store, store.create_session("r"))  # 1000 in / 500 out
    prices = parse_prices("claude-sonnet-5:0.003/0.015")

    text = render(store.metrics_snapshot(), LiveMetrics(2).snapshot(), prices)

    # 1000/1000*0.003 + 500/1000*0.015 = 0.0105
    assert "auto_bi_llm_cost_usd_total 0.010500" in text
    assert 'auto_bi_llm_tokens_total{direction="input",model="claude-sonnet-5"} 1000' in text
    assert 'auto_bi_llm_seconds_total{model="claude-sonnet-5"} 1.200000' in text
    assert "# TYPE auto_bi_llm_cost_usd_total counter" in text
    store.close()


def test_render_emits_help_and_type_even_with_no_samples(tmp_path) -> None:
    """A scraper must see 'known metric, zero series', not a metric that vanished."""
    store = _store(tmp_path)
    text = render(store.metrics_snapshot(), LiveMetrics(2).snapshot(), {})
    assert "# HELP auto_bi_builds_total" in text
    assert "# TYPE auto_bi_builds_total counter" in text
    assert "\nauto_bi_builds_total{" not in text  # no builds recorded yet
    assert text.endswith("\n")
    store.close()


def test_render_escapes_label_values(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.create_session("r")
    store.log_llm_call(
        session_id=sid,
        model='we"ird\\model',
        prompt_sha256="a" * 64,
        prompt_chars=1,
        reasoning=False,
        status="ok",
        latency_ms=0,
    )
    text = render(store.metrics_snapshot(), LiveMetrics(1).snapshot(), {})
    assert 'model="we\\"ird\\\\model"' in text
    store.close()


# --- endpoint -------------------------------------------------------------------------


def _client(demo_model, *, store, enabled: bool, auth: bool = False) -> TestClient:
    return TestClient(
        create_app(
            model=demo_model,
            llm=ScriptedLLM([]),
            store=store,
            metrics_enabled=enabled,
            auth_enabled=auth,
            llm_prices=parse_prices("claude-sonnet-5:0.003/0.015"),
        )
    )


def test_endpoint_is_404_when_disabled(demo_model, tmp_path) -> None:
    store = _store(tmp_path)
    response = _client(demo_model, store=store, enabled=False).get("/api/v1/metrics")
    assert response.status_code == 404
    store.close()


def test_endpoint_serves_exposition_text(demo_model, tmp_path) -> None:
    store = _store(tmp_path)
    _log_call(store, store.create_session("r"))
    response = _client(demo_model, store=store, enabled=True).get("/api/v1/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert "auto_bi_llm_cost_usd_total 0.010500" in response.text
    assert 'auto_bi_store_rows{table="llm_calls"} 1' in response.text
    assert "auto_bi_build_slots_total 2" in response.text
    store.close()


def test_endpoint_is_admin_only_when_auth_is_on(demo_model, tmp_path) -> None:
    """Global spend has no per-owner view to degrade to, unlike /observability/llm."""
    store = _store(tmp_path)
    store.upsert_user("analyst", hash_password("pw"), "analyst", ["dm"])
    client = _client(demo_model, store=store, enabled=True, auth=True)
    token = client.post(
        "/api/v1/auth/login", json={"username": "analyst", "password": "pw"}
    ).json()["token"]

    response = client.get("/api/v1/metrics", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403
    store.close()
