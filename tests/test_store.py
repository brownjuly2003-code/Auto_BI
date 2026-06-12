"""SQLite store (task 1.9): CRUD + linkage from the pipeline."""

import pytest

from auto_bi.store import Store


@pytest.fixture
def store(tmp_path) -> Store:
    s = Store(tmp_path / "auto_bi.sqlite")
    yield s
    s.close()


def test_session_messages_roundtrip(store: Store) -> None:
    sid = store.create_session("выручка по магазинам")
    store.add_message(sid, "user", "выручка по магазинам")
    store.add_message(sid, "agent", "предлагаю 3 чарта")
    msgs = store.messages(sid)
    assert [(m["role"], m["content"]) for m in msgs] == [
        ("user", "выручка по магазинам"),
        ("agent", "предлагаю 3 чарта"),
    ]
    assert all(m["session_id"] == sid for m in msgs)


def test_spec_json_roundtrip_and_status(store: Store) -> None:
    sid = store.create_session("r")
    spec_id = store.save_spec(sid, {"title": "Продажи", "charts": []})
    store.set_spec_status(spec_id, "approved")
    (spec,) = store.specs(sid)
    assert spec["spec_json"]["title"] == "Продажи"
    assert spec["status"] == "approved"


def test_builds_linked_to_spec(store: Store) -> None:
    sid = store.create_session("r")
    spec_id = store.save_spec(sid, {})
    store.save_build(sid, spec_id, dashboard_id=7, url="/superset/dashboard/7/", status="ok")
    store.save_build(sid, spec_id, status="failed", error="boom")
    ok, failed = store.builds(sid)
    assert ok["dashboard_id"] == 7
    assert failed["status"] == "failed"
    assert failed["error"] == "boom"


def test_llm_calls_filtered_by_session(store: Store) -> None:
    sid = store.create_session("r")
    store.log_llm_call(
        session_id=sid,
        model="claude-sonnet-4-6",
        prompt_sha256="abc",
        prompt_chars=100,
        reasoning=True,
        status="completed",
        latency_ms=1200,
    )
    store.log_llm_call(
        session_id=None,
        model="claude-sonnet-4-6",
        prompt_sha256="def",
        prompt_chars=50,
        reasoning=False,
        status="failed",
        latency_ms=10,
    )
    assert len(store.llm_calls()) == 2
    (call,) = store.llm_calls(sid)
    assert call["prompt_sha256"] == "abc"
    assert call["reasoning"] == 1


def test_dm_change_requests_lifecycle(store: Store) -> None:
    sid = store.create_session("r")
    req_id = store.add_dm_change_request(
        sid,
        table_name="dm.sales_daily",
        rule="filter_not_in_sorting_key_prefix",
        severity="critical",
        narrative="фильтр мимо ключа сортировки — скан 96%",
    )
    assert [r["id"] for r in store.dm_change_requests("open")] == [req_id]
    store.set_dm_change_request_status(req_id, "submitted")
    assert store.dm_change_requests("open") == []
    assert store.dm_change_requests()[0]["status"] == "submitted"


def test_session_status(store: Store) -> None:
    sid = store.create_session("r")
    store.set_session_status(sid, "built")
    # re-open the same file: data must persist on disk
    path = store._path
    store2 = Store(path)
    rows = store2._rows("SELECT status FROM sessions WHERE id = ?", sid)
    assert rows == [{"status": "built"}]
    store2.close()


def test_foreign_keys_enforced(store: Store) -> None:
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        store.add_message("no-such-session", "user", "x")


def test_schema_version_stamped(store: Store) -> None:
    version = store._db.execute("PRAGMA user_version").fetchone()[0]
    assert version == 1
