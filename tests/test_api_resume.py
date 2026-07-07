"""Session-resume after a server restart (X-4): a registry miss rehydrates the
session from Store, so dialogues, build results and dashboard links survive a
process restart (and eviction past MAX_SESSIONS).

A "restart" here is a second create_app() over the same Store — a fresh
SessionManager whose in-memory registry is empty, exactly like a new process.
"""

from fastapi.testclient import TestClient

from auto_bi.adapters.base import DashboardRef
from auto_bi.api import create_app
from auto_bi.auth import hash_password
from auto_bi.ir.spec import TargetBI
from auto_bi.store import Store
from tests.test_api import collect_events, make_client, start
from tests.test_machine import AMBIGUOUS_REPORT, CLEAR_REPORT, PATCHED_SPEC, ScriptedLLM
from tests.test_propose import GOOD_SPEC


def store_builder(store: Store):
    """Fake builder that records the build in Store the way compile_and_build does —
    resume tests need the durable rows, not just the in-memory event buffer."""

    def _build(spec, log, session_id):
        log("BUILD done")
        ref = DashboardRef(id=7, title=spec.title, url="/superset/dashboard/7/")
        store.save_build(session_id, None, dashboard_id=ref.id, url=ref.url, status="ok")
        store.set_session_status(session_id, "built")
        return ref

    return _build


def failing_store_builder(store: Store):
    def _build(spec, log, session_id):
        store.save_build(session_id, None, status="failed", error="BI down")
        store.set_session_status(session_id, "failed")
        raise RuntimeError("BI down")

    return _build


def _approve_and_wait(client: TestClient, sid: str) -> list[dict]:
    assert client.post(f"/api/v1/sessions/{sid}/approve").status_code == 202
    return collect_events(client, sid)


# --- dialogue resume -------------------------------------------------------------


def test_approve_phase_session_survives_restart_and_accepts_word_edit(demo_model, tmp_path) -> None:
    store = Store(tmp_path / "resume.sqlite")
    first = make_client(ScriptedLLM([CLEAR_REPORT, GOOD_SPEC]), demo_model, store=store)
    sid = start(first)["session_id"]

    restarted = make_client(ScriptedLLM([PATCHED_SPEC]), demo_model, store=store)
    state = restarted.get(f"/api/v1/sessions/{sid}")
    assert state.status_code == 200
    assert state.json() == {
        "session_id": sid,
        "phase": "approve",
        "build_status": "idle",
        "dashboard_url": "",
    }
    turn = restarted.post(f"/api/v1/sessions/{sid}/reply", json={"text": "переименуй"}).json()
    assert turn["phase"] == "approve"
    assert turn["spec"]["title"] == "Продажи (обновлено)"
    store.close()


def test_resumed_session_approves_and_builds(demo_model, tmp_path) -> None:
    store = Store(tmp_path / "resume.sqlite")
    first = make_client(ScriptedLLM([CLEAR_REPORT, GOOD_SPEC]), demo_model, store=store)
    sid = start(first)["session_id"]

    restarted = make_client(ScriptedLLM([]), demo_model, store=store, builder=store_builder(store))
    events = _approve_and_wait(restarted, sid)
    assert events[-1]["kind"] == "done"
    assert restarted.get(f"/api/v1/sessions/{sid}").json()["build_status"] == "built"
    store.close()


def test_clarify_session_resumes_and_folds_old_answers_into_grounding(demo_model, tmp_path) -> None:
    store = Store(tmp_path / "resume.sqlite")
    first = make_client(ScriptedLLM([AMBIGUOUS_REPORT]), demo_model, store=store)
    turn = start(first, "продажи и маржа")
    assert turn["phase"] == "clarify"
    sid = turn["session_id"]

    llm = ScriptedLLM([CLEAR_REPORT, GOOD_SPEC])
    restarted = make_client(llm, demo_model, store=store)
    turn = restarted.post(f"/api/v1/sessions/{sid}/reply", json={"text": "revenue"}).json()
    assert turn["phase"] == "approve"
    # the original request AND the post-restart answer are both in the grounding prompt
    grounding_prompt = llm.calls[0][1]
    assert "продажи и маржа" in grounding_prompt
    assert "revenue" in grounding_prompt
    store.close()


def test_clarify_round_counter_survives_restart(demo_model, tmp_path) -> None:
    # two rounds were burned before the restart; the resumed session must propose with
    # what it has (MAX_CLARIFY_ROUNDS) instead of asking a third time
    store = Store(tmp_path / "resume.sqlite")
    first = make_client(ScriptedLLM([AMBIGUOUS_REPORT, AMBIGUOUS_REPORT]), demo_model, store=store)
    turn = start(first, "продажи")
    sid = turn["session_id"]
    turn = first.post(f"/api/v1/sessions/{sid}/reply", json={"text": "не знаю"}).json()
    assert turn["phase"] == "clarify"

    restarted = make_client(ScriptedLLM([AMBIGUOUS_REPORT, GOOD_SPEC]), demo_model, store=store)
    turn = restarted.post(
        f"/api/v1/sessions/{sid}/reply", json={"text": "всё равно не знаю"}
    ).json()
    assert turn["phase"] == "approve"
    store.close()


def test_session_that_died_before_first_agent_output_is_not_resumable(demo_model, tmp_path) -> None:
    # a session row + user message but no spec and no clarify round: the original
    # process died mid-grounding and the client never received the session id
    store = Store(tmp_path / "resume.sqlite")
    sid = store.create_session("выручка")
    store.add_message(sid, "user", "выручка")
    client = make_client(ScriptedLLM([]), demo_model, store=store)
    assert client.get(f"/api/v1/sessions/{sid}").status_code == 404
    store.close()


# --- build state resume ----------------------------------------------------------


def test_built_session_resumes_with_absolute_url_and_terminal_sse(demo_model, tmp_path) -> None:
    store = Store(tmp_path / "resume.sqlite")
    base_urls = {TargetBI.SUPERSET: "http://bi.example:8088/"}
    first = make_client(
        ScriptedLLM([CLEAR_REPORT, GOOD_SPEC]),
        demo_model,
        store=store,
        builder=store_builder(store),
        bi_base_urls=base_urls,
    )
    sid = start(first)["session_id"]
    _approve_and_wait(first, sid)

    restarted = make_client(
        ScriptedLLM([]),
        demo_model,
        store=store,
        builder=store_builder(store),
        bi_base_urls=base_urls,
    )
    state = restarted.get(f"/api/v1/sessions/{sid}").json()
    assert state["build_status"] == "built"
    # the pipeline stores the BI-relative url; hydration re-absolutizes it (F-1)
    assert state["dashboard_url"] == "http://bi.example:8088/superset/dashboard/7/"
    # a late SSE reader gets a synthetic terminal event, not heartbeats forever
    events = collect_events(restarted, sid)
    assert events[-1]["kind"] == "done"
    assert events[-1]["url"] == "http://bi.example:8088/superset/dashboard/7/"
    store.close()


def test_failed_build_resumes_and_approve_retries(demo_model, tmp_path) -> None:
    store = Store(tmp_path / "resume.sqlite")
    first = make_client(
        ScriptedLLM([CLEAR_REPORT, GOOD_SPEC]),
        demo_model,
        store=store,
        builder=failing_store_builder(store),
    )
    sid = start(first)["session_id"]
    events = _approve_and_wait(first, sid)
    assert events[-1]["kind"] == "error"

    restarted = make_client(ScriptedLLM([]), demo_model, store=store, builder=store_builder(store))
    assert restarted.get(f"/api/v1/sessions/{sid}").json()["build_status"] == "failed"
    events = _approve_and_wait(restarted, sid)  # retry rebuilds the same approved spec
    assert events[-1]["kind"] == "done"
    assert restarted.get(f"/api/v1/sessions/{sid}").json()["build_status"] == "built"
    store.close()


def test_approved_session_without_build_row_resumes_as_failed_and_retries(
    demo_model, tmp_path
) -> None:
    # the process died in the approve->build window: spec row is 'approved' but no build
    # row exists (reap covers the mid-build case; this is the pre-build one)
    store = Store(tmp_path / "resume.sqlite")
    first = make_client(ScriptedLLM([CLEAR_REPORT, GOOD_SPEC]), demo_model, store=store)
    sid = start(first)["session_id"]
    (spec_row,) = store.specs(sid)
    store.set_spec_status(spec_row["id"], "approved")

    restarted = make_client(ScriptedLLM([]), demo_model, store=store, builder=store_builder(store))
    state = restarted.get(f"/api/v1/sessions/{sid}").json()
    assert state["phase"] == "approved"
    assert state["build_status"] == "failed"
    events = collect_events(restarted, sid)
    assert events[-1]["kind"] == "error"
    assert "restart" in events[-1]["text"]
    events = _approve_and_wait(restarted, sid)
    assert events[-1]["kind"] == "done"
    store.close()


def test_auto_overview_session_resumes_without_user_message(demo_model, tmp_path) -> None:
    # adopt_spec records only the agent summary — no user message; the session label
    # stands in for the request and the session must still resume into APPROVE
    store = Store(tmp_path / "resume.sqlite")
    first = make_client(ScriptedLLM([]), demo_model, store=store)
    r = first.post("/api/v1/sessions/auto", json={"table": "dm.sales_daily"})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]

    restarted = make_client(ScriptedLLM([]), demo_model, store=store, builder=store_builder(store))
    assert restarted.get(f"/api/v1/sessions/{sid}").json()["phase"] == "approve"
    events = _approve_and_wait(restarted, sid)
    assert events[-1]["kind"] == "done"
    store.close()


# --- deletion / eviction ----------------------------------------------------------


def test_deleted_session_stays_deleted_after_restart(demo_model, tmp_path) -> None:
    store = Store(tmp_path / "resume.sqlite")
    first = make_client(ScriptedLLM([CLEAR_REPORT, GOOD_SPEC]), demo_model, store=store)
    sid = start(first)["session_id"]
    assert first.delete(f"/api/v1/sessions/{sid}").status_code == 204
    assert store.session_status(sid) == "deleted"  # tombstone, rows survive
    assert store.messages(sid)

    restarted = make_client(ScriptedLLM([]), demo_model, store=store)
    assert restarted.get(f"/api/v1/sessions/{sid}").status_code == 404
    store.close()


def test_evicted_session_is_resurrected_from_store(demo_model, tmp_path, monkeypatch) -> None:
    # eviction past MAX_SESSIONS used to lose the dialogue; with hydration it does not
    monkeypatch.setattr("auto_bi.api.sessions.MAX_SESSIONS", 2)
    store = Store(tmp_path / "resume.sqlite")
    client = make_client(
        ScriptedLLM([CLEAR_REPORT, GOOD_SPEC] * 3 + [PATCHED_SPEC]), demo_model, store=store
    )
    first = start(client)["session_id"]
    start(client)
    start(client)  # evicts `first` from the registry
    turn = client.post(f"/api/v1/sessions/{first}/reply", json={"text": "переименуй"}).json()
    assert turn["spec"]["title"] == "Продажи (обновлено)"
    store.close()


# --- auth / RBAC -------------------------------------------------------------------


def _auth_app(llm, demo_model, store) -> TestClient:
    return TestClient(create_app(model=demo_model, llm=llm, store=store, auth_enabled=True))


def _token(client: TestClient, username: str) -> dict:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": "pw"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_session_owner_survives_restart(demo_model, tmp_path) -> None:
    store = Store(tmp_path / "resume.sqlite")
    for name, role in (("alice", "analyst"), ("bob", "analyst"), ("root", "admin")):
        store.upsert_user(name, hash_password("pw"), role, ["dm"])
    first = _auth_app(ScriptedLLM([CLEAR_REPORT, GOOD_SPEC]), demo_model, store)
    alice = _token(first, "alice")
    r = first.post("/api/v1/sessions", json={"request": "выручка"}, headers=alice)
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]

    restarted = _auth_app(ScriptedLLM([]), demo_model, store)
    # a foreign user probes 404 (not 403 — existence stays hidden); owner and admin see it
    assert (
        restarted.get(f"/api/v1/sessions/{sid}", headers=_token(restarted, "bob")).status_code
        == 404
    )
    assert (
        restarted.get(f"/api/v1/sessions/{sid}", headers=_token(restarted, "alice")).status_code
        == 200
    )
    assert (
        restarted.get(f"/api/v1/sessions/{sid}", headers=_token(restarted, "root")).status_code
        == 200
    )
    store.close()


def test_legacy_ownerless_session_is_admin_only_when_auth_is_on(demo_model, tmp_path) -> None:
    # a session created while auth was off (owner=NULL) must not become claimable by a
    # random analyst once auth is switched on — only the admin can address it
    store = Store(tmp_path / "resume.sqlite")
    open_client = make_client(ScriptedLLM([CLEAR_REPORT, GOOD_SPEC]), demo_model, store=store)
    sid = start(open_client)["session_id"]

    for name, role in (("alice", "analyst"), ("root", "admin")):
        store.upsert_user(name, hash_password("pw"), role, ["dm"])
    restarted = _auth_app(ScriptedLLM([]), demo_model, store)
    assert (
        restarted.get(f"/api/v1/sessions/{sid}", headers=_token(restarted, "alice")).status_code
        == 404
    )
    assert (
        restarted.get(f"/api/v1/sessions/{sid}", headers=_token(restarted, "root")).status_code
        == 200
    )
    store.close()
