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
    assert version == 2


def test_trace_events_ordered_by_seq(store: Store) -> None:
    sid = store.create_session("r")
    store.add_trace_event(sid, kind="grounding", latency_ms=900, detail="2 таблицы")
    store.add_trace_event(sid, kind="propose", latency_ms=1500, detail="3 чарта")
    store.add_trace_event(sid, kind="build_error", status="error", detail="boom")
    other = store.create_session("other")
    store.add_trace_event(other, kind="grounding")
    events = store.trace_events(sid)
    assert [e["seq"] for e in events] == [1, 2, 3]  # per-session counter, not global
    assert [e["kind"] for e in events] == ["grounding", "propose", "build_error"]
    assert events[2]["status"] == "error" and events[2]["detail"] == "boom"
    assert [e["seq"] for e in store.trace_events(other)] == [1]


def test_llm_call_records_step_and_completion_chars(store: Store) -> None:
    sid = store.create_session("r")
    store.log_llm_call(
        session_id=sid,
        model="claude-sonnet-4-6",
        prompt_sha256="abc",
        prompt_chars=100,
        reasoning=True,
        status="completed",
        latency_ms=1200,
        step="propose",
        completion_chars=640,
    )
    (call,) = store.llm_calls(sid)
    assert call["step"] == "propose"
    assert call["completion_chars"] == 640


def test_llm_usage_summary_aggregates(store: Store) -> None:
    sid = store.create_session("r")

    def _log(step: str, status: str, latency: int, pc: int, cc: int) -> None:
        store.log_llm_call(
            session_id=sid,
            model="claude-sonnet-4-6",
            prompt_sha256="x",
            prompt_chars=pc,
            reasoning=False,
            status=status,
            latency_ms=latency,
            step=step,
            completion_chars=cc,
        )

    _log("grounding", "completed", 800, 1000, 200)
    _log("propose", "completed", 1600, 2000, 800)
    _log("propose", "transport_error", 50, 2000, 0)
    summary = store.llm_usage_summary()
    t = summary["totals"]
    assert t["calls"] == 3
    assert t["ok"] == 2 and t["failed"] == 1
    assert t["latency_ms_total"] == 2450
    assert t["latency_ms_max"] == 1600
    assert t["prompt_chars"] == 5000 and t["completion_chars"] == 1000
    by_step = {r["step"]: r for r in summary["by_step"]}
    assert by_step["propose"]["calls"] == 2
    assert by_step["propose"]["completion_chars"] == 800
    assert {r["status"] for r in summary["by_status"]} == {"completed", "transport_error"}


def test_llm_usage_summary_empty_is_zero(store: Store) -> None:
    t = store.llm_usage_summary()["totals"]
    assert t == {
        "calls": 0,
        "ok": 0,
        "failed": 0,
        "latency_ms_total": 0,
        "latency_ms_avg": 0,
        "latency_ms_max": 0,
        "prompt_chars": 0,
        "completion_chars": 0,
        "reasoning_calls": 0,
    }


def test_migrates_v1_db_to_v2(tmp_path) -> None:
    import sqlite3

    path = tmp_path / "v1.sqlite"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, created_at TEXT, request TEXT, status TEXT);"
        "CREATE TABLE llm_calls ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, created_at TEXT,"
        " model TEXT NOT NULL, prompt_sha256 TEXT NOT NULL, prompt_chars INTEGER NOT NULL,"
        " reasoning INTEGER NOT NULL, status TEXT NOT NULL, latency_ms INTEGER NOT NULL);"
    )
    db.execute(
        "INSERT INTO llm_calls (model, prompt_sha256, prompt_chars, reasoning, status, latency_ms)"
        " VALUES ('m', 'h', 10, 0, 'completed', 5)"
    )
    db.execute("PRAGMA user_version = 1")
    db.commit()
    db.close()

    store = Store(path)
    assert store._db.execute("PRAGMA user_version").fetchone()[0] == 2
    cols = {r["name"] for r in store._db.execute("PRAGMA table_info(llm_calls)")}
    assert {"step", "completion_chars"} <= cols
    # the pre-existing row survived and back-fills with defaults
    (row,) = store.llm_calls()
    assert row["step"] == "" and row["completion_chars"] == 0
    # trace_events is now usable
    sid = store.create_session("r")
    store.add_trace_event(sid, kind="grounding")
    assert len(store.trace_events(sid)) == 1
    store.close()
