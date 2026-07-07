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


def test_session_status_getter(store: Store) -> None:
    sid = store.create_session("r")
    assert store.session_status(sid) == "open"
    store.set_session_status(sid, "built")
    assert store.session_status(sid) == "built"
    assert store.session_status("no-such-session") is None


def test_ping_succeeds_on_open_store(store: Store) -> None:
    store.ping()  # must not raise


def test_ping_raises_on_closed_store(tmp_path) -> None:
    import sqlite3

    s = Store(tmp_path / "closed.sqlite")
    s.close()
    with pytest.raises(sqlite3.ProgrammingError):
        s.ping()


def test_reap_stuck_builds_is_noop_when_nothing_is_building(store: Store) -> None:
    sid = store.create_session("r")
    store.set_session_status(sid, "built")
    assert store.reap_stuck_builds() == []
    assert store.session_status(sid) == "built"


def test_reap_stuck_builds_records_interrupted_build_and_fails_session(store: Store) -> None:
    # simulates a process killed mid-build (B-7): compile_and_build marked the session
    # 'building' and never got to write a builds-table row before the process died.
    sid = store.create_session("r")
    spec_id = store.save_spec(sid, {"title": "Продажи"})
    store.set_session_status(sid, "building")

    reaped = store.reap_stuck_builds()

    assert reaped == [sid]
    assert store.session_status(sid) == "failed"
    (build,) = store.builds(sid)
    assert build["spec_id"] == spec_id
    assert build["status"] == "failed"
    assert "interrupted" in build["error"]
    # idempotent: a second reap on an already-failed session finds nothing new
    assert store.reap_stuck_builds() == []


def test_foreign_keys_enforced(store: Store) -> None:
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        store.add_message("no-such-session", "user", "x")


def test_schema_version_stamped(store: Store) -> None:
    version = store._db.execute("PRAGMA user_version").fetchone()[0]
    assert version == 7


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
        "input_tokens": 0,
        "output_tokens": 0,
        "token_calls": 0,
        "reasoning_calls": 0,
    }


def test_llm_usage_summary_counts_real_tokens(store: Store) -> None:
    # Two Anthropic-style calls report usage; one GraceKelly-style call reports none.
    # The token sums ignore the NULL row, and token_calls counts only the rows with usage,
    # so the proxy-only provider never dilutes the real-token figures.
    sid = store.create_session("r")

    def _log(input_tokens: int | None, output_tokens: int | None) -> None:
        store.log_llm_call(
            session_id=sid,
            model="claude-sonnet-4-6",
            prompt_sha256="x",
            prompt_chars=1000,
            reasoning=False,
            status="completed",
            latency_ms=100,
            step="propose",
            completion_chars=500,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    _log(1200, 340)
    _log(800, 110)
    _log(None, None)  # GraceKelly-style: no usage reported
    totals = store.llm_usage_summary()["totals"]
    assert totals["input_tokens"] == 2000
    assert totals["output_tokens"] == 450
    assert totals["token_calls"] == 2  # the NULL row is not counted
    assert totals["calls"] == 3
    by_model = {r["model"]: r for r in store.llm_usage_summary()["by_model"]}
    assert by_model["claude-sonnet-4-6"]["input_tokens"] == 2000


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
    assert store._db.execute("PRAGMA user_version").fetchone()[0] == 7
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


def test_migrates_legacy_v0_db_with_old_llm_calls(tmp_path) -> None:
    # a pre-versioning DB: user_version stayed 0 but llm_calls predates the
    # observability columns. CREATE TABLE IF NOT EXISTS leaves it untouched, so the
    # migration must still ALTER it (must NOT early-return on version 0).
    import sqlite3

    path = tmp_path / "v0.sqlite"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, created_at TEXT, request TEXT, status TEXT);"
        "CREATE TABLE llm_calls ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, created_at TEXT,"
        " model TEXT NOT NULL, prompt_sha256 TEXT NOT NULL, prompt_chars INTEGER NOT NULL,"
        " reasoning INTEGER NOT NULL, status TEXT NOT NULL, latency_ms INTEGER NOT NULL);"
    )
    db.commit()
    assert db.execute("PRAGMA user_version").fetchone()[0] == 0
    db.close()

    store = Store(path)
    assert store._db.execute("PRAGMA user_version").fetchone()[0] == 7
    cols = {r["name"] for r in store._db.execute("PRAGMA table_info(llm_calls)")}
    assert {"step", "completion_chars"} <= cols
    # the first observability-aware write no longer crashes with "no such column: step"
    sid = store.create_session("r")
    store.log_llm_call(
        session_id=sid,
        model="m",
        prompt_sha256="h",
        prompt_chars=1,
        reasoning=False,
        status="completed",
        latency_ms=1,
        step="grounding",
        completion_chars=7,
    )
    (row,) = store.llm_calls(sid)
    assert row["step"] == "grounding" and row["completion_chars"] == 7
    store.close()


def test_migrates_v3_db_adds_remediation_column(tmp_path) -> None:
    # a v3 DB: dm_change_requests exists but predates the remediation column. CREATE TABLE
    # IF NOT EXISTS leaves it untouched, so the v4 migration must ALTER it in place and the
    # old rows must back-fill with the '' default.
    import sqlite3

    path = tmp_path / "v3.sqlite"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, created_at TEXT, request TEXT, status TEXT);"
        "CREATE TABLE dm_change_requests ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, created_at TEXT,"
        " table_name TEXT NOT NULL, rule TEXT NOT NULL, severity TEXT NOT NULL,"
        " narrative TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open');"
    )
    db.execute(
        "INSERT INTO dm_change_requests (table_name, rule, severity, narrative)"
        " VALUES ('dm.sales', 'r', 'critical', 'legacy request')"
    )
    db.execute("PRAGMA user_version = 3")
    db.commit()
    db.close()

    store = Store(path)
    assert store._db.execute("PRAGMA user_version").fetchone()[0] == 7
    cols = {r["name"] for r in store._db.execute("PRAGMA table_info(dm_change_requests)")}
    assert "remediation" in cols
    # the pre-existing row survived and back-fills the new column with its default
    (row,) = store.dm_change_requests()
    assert row["narrative"] == "legacy request" and row["remediation"] == ""
    store.close()


def test_migrates_v4_db_adds_token_columns(tmp_path) -> None:
    # a v4 DB: llm_calls has the v2 observability columns but predates the token columns.
    # CREATE TABLE IF NOT EXISTS leaves it untouched, so the v5 migration must ALTER it in
    # place; the new columns are nullable, so the old row back-fills to NULL (no usage data).
    import sqlite3

    path = tmp_path / "v4.sqlite"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, created_at TEXT, request TEXT, status TEXT);"
        "CREATE TABLE llm_calls ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, created_at TEXT,"
        " model TEXT NOT NULL, prompt_sha256 TEXT NOT NULL, prompt_chars INTEGER NOT NULL,"
        " reasoning INTEGER NOT NULL, status TEXT NOT NULL, latency_ms INTEGER NOT NULL,"
        " step TEXT NOT NULL DEFAULT '', completion_chars INTEGER NOT NULL DEFAULT 0);"
    )
    db.execute(
        "INSERT INTO llm_calls"
        " (model, prompt_sha256, prompt_chars, reasoning, status, latency_ms, step,"
        " completion_chars)"
        " VALUES ('claude-sonnet-4-6', 'h', 10, 0, 'completed', 5, 'propose', 200)"
    )
    db.execute("PRAGMA user_version = 4")
    db.commit()
    db.close()

    store = Store(path)
    assert store._db.execute("PRAGMA user_version").fetchone()[0] == 7
    cols = {r["name"] for r in store._db.execute("PRAGMA table_info(llm_calls)")}
    assert {"input_tokens", "output_tokens"} <= cols
    # the pre-existing row survived; its token columns back-fill to NULL (not a fake 0)
    (row,) = store.llm_calls()
    assert row["completion_chars"] == 200
    assert row["input_tokens"] is None and row["output_tokens"] is None
    # the NULL row is excluded from token_calls; a fresh write with usage is summed
    sid = store.create_session("r")
    store.log_llm_call(
        session_id=sid,
        model="claude-sonnet-4-6",
        prompt_sha256="h",
        prompt_chars=1,
        reasoning=False,
        status="completed",
        latency_ms=1,
        step="grounding",
        completion_chars=7,
        input_tokens=42,
        output_tokens=9,
    )
    totals = store.llm_usage_summary()["totals"]
    assert totals["input_tokens"] == 42 and totals["output_tokens"] == 9
    assert totals["token_calls"] == 1
    store.close()


def test_migrates_v5_db_hashes_plaintext_tokens(tmp_path) -> None:
    # a v5 DB: auth_tokens exists (created by the initial schema, back when tokens were
    # stored raw). The v6 migration must rewrite the plaintext value to its sha256 hex
    # digest in place (B-4) without losing the row (user_id/expires_at survive).
    import sqlite3

    from auto_bi.store.db import _hash_token

    path = tmp_path / "v5.sqlite"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT,"
        " username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,"
        " role TEXT NOT NULL DEFAULT 'analyst', allowed_schemas TEXT NOT NULL DEFAULT '[]');"
        "INSERT INTO users (username, password_hash, allowed_schemas) VALUES"
        " ('alice', 'h', '[\"dm\"]');"
        "CREATE TABLE auth_tokens (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL,"
        " created_at TEXT, expires_at TEXT NOT NULL);"
        "INSERT INTO auth_tokens (token, user_id, expires_at)"
        " VALUES ('plaintext-raw-token-value', 1, datetime('now', '+1 hour'));"
    )
    db.execute("PRAGMA user_version = 5")
    db.commit()
    db.close()

    store = Store(path)
    assert store._db.execute("PRAGMA user_version").fetchone()[0] == 7
    (row,) = store._rows("SELECT token, user_id FROM auth_tokens")
    assert row["token"] == _hash_token("plaintext-raw-token-value")
    assert row["user_id"] == 1
    # the row is resolvable through the normal API with the original raw token
    assert store.token_user("plaintext-raw-token-value")["username"] == "alice"
    store.close()


def test_dm_change_request_persists_remediation(store: Store) -> None:
    sid = store.create_session("r")
    req_id = store.add_dm_change_request(
        sid,
        table_name="dm.sales_daily",
        rule="filter_not_in_sorting_key_prefix",
        severity="critical",
        narrative="фильтр мимо ключа сортировки",
        remediation='[{"kind": "ch_projection", "ddl": "ALTER TABLE ..."}]',
    )
    row = store.dm_change_request(req_id)
    assert row is not None
    assert row["remediation"] == '[{"kind": "ch_projection", "ddl": "ALTER TABLE ..."}]'


# --- schema v7: session identity for restart-resume (X-4) ---------------------------


def test_create_session_persists_owner_target_and_pins(store: Store) -> None:
    sid = store.create_session(
        "выручка", owner="alice", target_bi="datalens", pinned=["dm.stores", "dm.sales_daily"]
    )
    row = store.session_row(sid)
    assert row is not None
    assert row["owner"] == "alice"
    assert row["target_bi"] == "datalens"
    assert row["pinned"] == ["dm.sales_daily", "dm.stores"]  # stored sorted
    # defaults: auth off, Superset, no seed
    bare = store.session_row(store.create_session("r"))
    assert bare["owner"] is None
    assert bare["target_bi"] == "superset"
    assert bare["pinned"] == []
    assert store.session_row("no-such-session") is None


def test_dm_change_requests_for_session_scopes_by_session(store: Store) -> None:
    sid = store.create_session("r")
    other = store.create_session("other")
    store.add_dm_change_request(sid, table_name="dm.sales_daily", rule="a", severity="warn")
    store.add_dm_change_request(other, table_name="dm.stores", rule="b", severity="warn")
    rows = store.dm_change_requests_for_session(sid)
    assert [(r["table_name"], r["rule"]) for r in rows] == [("dm.sales_daily", "a")]


def test_migrates_v6_db_adds_session_resume_columns(tmp_path) -> None:
    # a v6 DB: sessions predates owner/target_bi/pinned. CREATE TABLE IF NOT EXISTS leaves
    # it untouched, so the v7 migration must ALTER in place; the legacy row back-fills to
    # owner=NULL / target_bi='superset' / pinned='[]' (hydration's safe defaults).
    import sqlite3

    path = tmp_path / "v6.sqlite"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE sessions ("
        " id TEXT PRIMARY KEY,"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')),"
        " request TEXT NOT NULL DEFAULT '',"
        " status TEXT NOT NULL DEFAULT 'open');"
        "INSERT INTO sessions (id, request, status) VALUES ('legacy1', 'выручка', 'built');"
    )
    db.execute("PRAGMA user_version = 6")
    db.commit()
    db.close()

    store = Store(path)
    assert store._db.execute("PRAGMA user_version").fetchone()[0] == 7
    cols = {r["name"] for r in store._db.execute("PRAGMA table_info(sessions)")}
    assert {"owner", "target_bi", "pinned"} <= cols
    row = store.session_row("legacy1")
    assert row["status"] == "built"
    assert row["owner"] is None
    assert row["target_bi"] == "superset"
    assert row["pinned"] == []
    store.close()
