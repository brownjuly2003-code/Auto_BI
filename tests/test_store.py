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
    # P2-1: Anthropic-legacy stop_reason end_turn also counts as success
    _log("propose", "end_turn", 100, 500, 50)
    summary = store.llm_usage_summary()
    t = summary["totals"]
    assert t["calls"] == 4
    assert t["ok"] == 3 and t["failed"] == 1
    assert t["latency_ms_total"] == 2550
    assert t["latency_ms_max"] == 1600
    assert t["prompt_chars"] == 5500 and t["completion_chars"] == 1050
    by_step = {r["step"]: r for r in summary["by_step"]}
    assert by_step["propose"]["calls"] == 3
    assert by_step["propose"]["completion_chars"] == 850
    assert {r["status"] for r in summary["by_status"]} == {
        "completed",
        "transport_error",
        "end_turn",
    }


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


# --- BI-artifact ownership ledger (audit P0-2 criterion 4) --------------------------


def _art(store: Store, session_id, build_token, **over) -> int:
    kw = dict(
        session_id=session_id,
        build_token=build_token,
        target_bi="superset",
        kind="dataset",
        native_id="1",
        name="auto_bi__x",
        owner=None,
        schema_set="dm.sales_daily",
    )
    kw.update(over)
    return store.record_bi_artifact(**kw)


def test_bi_artifact_record_and_list_roundtrip(store: Store) -> None:
    sid = store.create_session("r")
    _art(store, sid, "tok1", kind="database", native_id="7", name="conn", schema_set=None)
    _art(store, sid, "tok1", kind="dataset", native_id="101", name="auto_bi__sales")
    _art(store, sid, "tok1", kind="chart", native_id="102", name="Выручка")
    _art(store, sid, "tok1", kind="dashboard", native_id="103", name="Обзор", schema_set=None)
    rows = store.bi_artifacts(sid)
    assert [r["kind"] for r in rows] == ["database", "dataset", "chart", "dashboard"]
    assert all(r["build_token"] == "tok1" and r["status"] == "live" for r in rows)
    ds = rows[1]
    assert (ds["native_id"], ds["name"]) == ("101", "auto_bi__sales")
    assert ds["schema_set"] == "dm.sales_daily"
    # empty for an unknown session
    assert store.bi_artifacts("no-such") == []


def test_orphan_bi_artifacts_selects_only_prior_build_tokens(store: Store) -> None:
    sid = store.create_session("r")
    old_a = _art(store, sid, "tok1", native_id="10")
    old_b = _art(store, sid, "tok1", kind="chart", native_id="11")
    # with only tok1 present, tok1 is the current build -> nothing from a prior revision
    assert store.orphan_bi_artifacts(sid, "tok1") == []
    _art(store, sid, "tok2", native_id="20")  # a new (current) build — must NOT be an orphan
    orphans = store.orphan_bi_artifacts(sid, "tok2")
    assert {o["id"] for o in orphans} == {old_a, old_b}
    assert all(o["build_token"] == "tok1" for o in orphans)


def test_orphan_bi_artifacts_never_selects_by_name(store: Store) -> None:
    # a same-titled artifact owned by ANOTHER session/owner must never be selected: the
    # selection keys on ownership (session_id/build_token), never on the technical name.
    a = store.create_session("a")
    b = store.create_session("b")
    mine = _art(store, a, "tok1", native_id="1", name="collide", owner="alice")
    _art(store, b, "tokB", native_id="99", name="collide", owner="bob")  # identical name
    orphans = store.orphan_bi_artifacts(a, "tok2")
    assert [o["id"] for o in orphans] == [mine]
    assert [o["native_id"] for o in orphans] == ["1"]  # session b's colliding-name row excluded


def test_orphan_bi_artifacts_excludes_shared_database_by_default(store: Store) -> None:
    # the connection (kind='database') is idempotent-by-name and SHARED across builds: the
    # prior revision's row is still referenced by the current build, so the default selection
    # must NOT offer it for deletion; include_shared=True keeps the full audit view.
    sid = store.create_session("r")
    _art(store, sid, "tok1", kind="database", native_id="1", name="conn", schema_set=None)
    per_build = _art(store, sid, "tok1", kind="dataset", native_id="10")
    orphans = store.orphan_bi_artifacts(sid, "tok2")
    assert [o["id"] for o in orphans] == [per_build]  # deletable as returned
    audit = store.orphan_bi_artifacts(sid, "tok2", include_shared=True)
    assert [o["kind"] for o in audit] == ["database", "dataset"]


def test_orphan_bi_artifacts_owner_scoping(store: Store) -> None:
    sid = store.create_session("r")
    alice = _art(store, sid, "tok1", native_id="1", owner="alice")
    _art(store, sid, "tok1", native_id="2", owner="bob")
    # owner-scoped: a non-admin cleanup sees only its own prior artifacts
    scoped = store.orphan_bi_artifacts(sid, "tok2", owner="alice")
    assert [o["id"] for o in scoped] == [alice]
    # owner=None (admin / auth off) sees every owner for the session
    assert len(store.orphan_bi_artifacts(sid, "tok2")) == 2


def test_mark_bi_artifacts_superseded_removes_from_orphan_selection(store: Store) -> None:
    sid = store.create_session("r")
    keep = _art(store, sid, "tok1", native_id="1")
    gone = _art(store, sid, "tok1", native_id="2")
    store.mark_bi_artifacts_superseded([gone])
    orphans = store.orphan_bi_artifacts(sid, "tok2")
    assert [o["id"] for o in orphans] == [keep]  # superseded row no longer selected
    assert store.mark_bi_artifacts_superseded([]) is None  # empty is a no-op


def test_bi_artifacts_table_present_without_version_bump(store: Store) -> None:
    # the table is created via always-run CREATE IF NOT EXISTS; schema_version stays 7 (no bump)
    tables = {r["name"] for r in store._db.execute("SELECT name FROM sqlite_master")}
    assert "bi_artifacts" in tables
    assert store._db.execute("PRAGMA user_version").fetchone()[0] == 7


# --- Store.stale_bi_artifacts: ledger-wide prune candidates (operator `auto_bi prune`) ----


def _build(store: Store, session_id, build_token, *, owner=None) -> dict[str, int]:
    """Record one full build's four artifact kinds under a single build_token.

    Returns kind -> ledger row id so a test can name the prior build's rows precisely.
    """
    return {
        "database": _art(
            store,
            session_id,
            build_token,
            kind="database",
            native_id=f"{build_token}-db",
            name="conn",
            schema_set=None,
            owner=owner,
        ),
        "dataset": _art(
            store,
            session_id,
            build_token,
            kind="dataset",
            native_id=f"{build_token}-ds",
            owner=owner,
        ),
        "chart": _art(
            store,
            session_id,
            build_token,
            kind="chart",
            native_id=f"{build_token}-ch",
            name="Выручка",
            owner=owner,
        ),
        "dashboard": _art(
            store,
            session_id,
            build_token,
            kind="dashboard",
            native_id=f"{build_token}-dash",
            name="Обзор",
            schema_set=None,
            owner=owner,
        ),
    }


def test_stale_bi_artifacts_returns_only_prior_build_excluding_shared(store: Store) -> None:
    # two builds in one session: the newest build_token (max ledger row id) is the delivered
    # dashboard and is kept; only the older build's rows are stale, and the shared database is
    # excluded by default (still referenced by the current build).
    sid = store.create_session("r")
    first = _build(store, sid, "tok1")
    _build(store, sid, "tok2")  # latest build — never selected
    stale = store.stale_bi_artifacts()
    assert {s["id"] for s in stale} == {first["dataset"], first["chart"], first["dashboard"]}
    assert all(s["build_token"] == "tok1" for s in stale)
    assert "database" not in {s["kind"] for s in stale}


def test_stale_bi_artifacts_include_shared_returns_prior_database(store: Store) -> None:
    # include_shared=True restores the full audit view: the prior build's database row IS returned
    sid = store.create_session("r")
    first = _build(store, sid, "tok1")
    _build(store, sid, "tok2")
    stale = store.stale_bi_artifacts(include_shared=True)
    assert {s["id"] for s in stale} == set(first.values())  # all four of tok1, database included
    assert "database" in {s["kind"] for s in stale}


def test_stale_bi_artifacts_keeps_each_sessions_latest_build(store: Store) -> None:
    # two sessions, one build each: every session's only build is its latest -> nothing is stale
    a = store.create_session("a")
    b = store.create_session("b")
    _build(store, a, "tokA")
    _build(store, b, "tokB")
    assert store.stale_bi_artifacts() == []


def test_stale_bi_artifacts_session_filter_narrows_to_one_session(store: Store) -> None:
    a = store.create_session("a")
    b = store.create_session("b")
    a_first = _build(store, a, "tokA1")
    _build(store, a, "tokA2")
    b_first = _build(store, b, "tokB1")
    _build(store, b, "tokB2")
    # unfiltered: both sessions' prior-build non-shared rows
    assert {s["id"] for s in store.stale_bi_artifacts()} == {
        a_first["dataset"],
        a_first["chart"],
        a_first["dashboard"],
        b_first["dataset"],
        b_first["chart"],
        b_first["dashboard"],
    }
    # session_id= narrows to that session only
    scoped = store.stale_bi_artifacts(session_id=a)
    assert {s["id"] for s in scoped} == {
        a_first["dataset"],
        a_first["chart"],
        a_first["dashboard"],
    }
    assert all(s["session_id"] == a for s in scoped)


def test_stale_bi_artifacts_never_returns_superseded_rows(store: Store) -> None:
    # rows already marked superseded (removed by an earlier prune) are never re-selected
    sid = store.create_session("r")
    first = _build(store, sid, "tok1")
    _build(store, sid, "tok2")
    store.mark_bi_artifacts_superseded([first["dataset"], first["chart"], first["dashboard"]])
    assert store.stale_bi_artifacts() == []


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


# --- close() vs in-flight operations (CI segfault 2026-07-20) ------------------


def test_close_serializes_with_in_flight_operations(tmp_path) -> None:
    """close() must take the connection lock: the API build thread writes its final
    build_done trace AFTER the client already saw the done-event over SSE, so a
    teardown close() can arrive mid-execute — unserialized, that crashes the
    interpreter in sqlite3's C layer (CI exit 139 inside add_trace_event)."""
    import threading

    store = Store(tmp_path / "close.sqlite")
    in_flight = threading.Event()
    finish_write = threading.Event()

    def writer() -> None:
        with store._lock:  # a writer mid-execute holds exactly this lock
            in_flight.set()
            finish_write.wait(timeout=5)

    closed = threading.Event()

    def closer() -> None:
        store.close()
        closed.set()

    w = threading.Thread(target=writer)
    w.start()
    assert in_flight.wait(timeout=5)
    c = threading.Thread(target=closer)
    c.start()
    # while the writer holds the lock, close() must block, not yank the connection away
    assert not closed.wait(timeout=0.3)
    finish_write.set()
    assert closed.wait(timeout=5)
    w.join(timeout=5)
    c.join(timeout=5)


def test_write_after_close_raises_cleanly(tmp_path) -> None:
    """The late writer's failure mode is a clean ProgrammingError (build tracing
    swallows it), never a crash."""
    import sqlite3

    store = Store(tmp_path / "closed.sqlite")
    store.close()
    with pytest.raises(sqlite3.ProgrammingError):
        store.add_trace_event(None, kind="late_write")
