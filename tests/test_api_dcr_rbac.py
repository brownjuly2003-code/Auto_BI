"""Audit P1-4: DCR list/detail/patch and global LLM observability are owner-scoped."""

from __future__ import annotations

from fastapi.testclient import TestClient

from auto_bi.adapters.base import DashboardRef
from auto_bi.api import create_app
from auto_bi.auth import hash_password
from auto_bi.store import Store
from tests.test_machine import ScriptedLLM


def _fake_builder(spec, log, session_id):
    return DashboardRef(id=1, title=spec.title, url="/x/")


def _client(demo_model, store: Store) -> TestClient:
    store.upsert_user("alice", hash_password("pw"), "analyst", ["dm"])
    store.upsert_user("bob", hash_password("pw"), "analyst", ["finance"])
    store.upsert_user("admin", hash_password("pw"), "admin", ["*"])
    app = create_app(
        model=demo_model,
        llm=ScriptedLLM([]),
        store=store,
        builder=_fake_builder,
        auth_enabled=True,
    )
    return TestClient(app)


def _login(client: TestClient, username: str) -> dict:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": "pw"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _seed_dcr(store: Store, *, owner: str, table: str, request: str = "r") -> int:
    sid = store.create_session(request, owner=owner)
    return store.add_dm_change_request(
        sid, table_name=table, rule="no_filter", severity="warn", narrative="n"
    )


def test_dcr_list_hides_foreign_sessions(demo_model, tmp_path) -> None:
    store = Store(tmp_path / "s.sqlite")
    alice_id = _seed_dcr(store, owner="alice", table="dm.sales")
    bob_id = _seed_dcr(store, owner="bob", table="finance.ledger")
    client = _client(demo_model, store)

    alice = _login(client, "alice")
    rows = client.get("/api/v1/dm-change-requests", headers=alice).json()
    assert [r["id"] for r in rows] == [alice_id]

    bob = _login(client, "bob")
    rows = client.get("/api/v1/dm-change-requests", headers=bob).json()
    assert [r["id"] for r in rows] == [bob_id]

    admin = _login(client, "admin")
    rows = client.get("/api/v1/dm-change-requests", headers=admin).json()
    assert {r["id"] for r in rows} == {alice_id, bob_id}


def test_dcr_detail_foreign_is_404_not_200(demo_model, tmp_path) -> None:
    store = Store(tmp_path / "s.sqlite")
    foreign = _seed_dcr(store, owner="alice", table="dm.sales", request="secret ask")
    client = _client(demo_model, store)
    bob = _login(client, "bob")
    r = client.get(f"/api/v1/dm-change-requests/{foreign}", headers=bob)
    assert r.status_code == 404
    # no session_request leak in the body
    assert "secret ask" not in r.text


def test_dcr_detail_own_ok_and_hides_wrong_schema(demo_model, tmp_path) -> None:
    store = Store(tmp_path / "s.sqlite")
    # alice owns the session but the DCR points at finance — outside her schemas
    wrong = _seed_dcr(store, owner="alice", table="finance.secret")
    own = _seed_dcr(store, owner="alice", table="dm.sales")
    client = _client(demo_model, store)
    alice = _login(client, "alice")
    assert client.get(f"/api/v1/dm-change-requests/{own}", headers=alice).status_code == 200
    assert client.get(f"/api/v1/dm-change-requests/{wrong}", headers=alice).status_code == 404
    listed = client.get("/api/v1/dm-change-requests", headers=alice).json()
    assert [r["id"] for r in listed] == [own]


def test_dcr_patch_admin_only(demo_model, tmp_path) -> None:
    store = Store(tmp_path / "s.sqlite")
    dcr_id = _seed_dcr(store, owner="alice", table="dm.sales")
    foreign = _seed_dcr(store, owner="bob", table="finance.ledger")
    client = _client(demo_model, store)

    alice = _login(client, "alice")
    # own DCR but not admin -> 403 (visible, not allowed to mutate workflow)
    r = client.patch(
        f"/api/v1/dm-change-requests/{dcr_id}",
        headers=alice,
        json={"status": "accepted"},
    )
    assert r.status_code == 403
    # foreign DCR -> 404 (no existence probe)
    assert (
        client.patch(
            f"/api/v1/dm-change-requests/{foreign}",
            headers=alice,
            json={"status": "accepted"},
        ).status_code
        == 404
    )

    admin = _login(client, "admin")
    ok = client.patch(
        f"/api/v1/dm-change-requests/{dcr_id}",
        headers=admin,
        json={"status": "accepted"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "accepted"


def test_observability_llm_scoped_to_owner(demo_model, tmp_path) -> None:
    store = Store(tmp_path / "s.sqlite")
    alice_sid = store.create_session("a", owner="alice")
    bob_sid = store.create_session("b", owner="bob")
    for sid, n in ((alice_sid, 10), (bob_sid, 99)):
        store.log_llm_call(
            session_id=sid,
            model="m",
            prompt_sha256="x",
            prompt_chars=n,
            reasoning=False,
            status="completed",
            latency_ms=1,
            completion_chars=n,
        )
    client = _client(demo_model, store)

    alice = _login(client, "alice")
    t = client.get("/api/v1/observability/llm", headers=alice).json()["totals"]
    assert t["calls"] == 1 and t["prompt_chars"] == 10

    admin = _login(client, "admin")
    t = client.get("/api/v1/observability/llm", headers=admin).json()["totals"]
    assert t["calls"] == 2 and t["prompt_chars"] == 109
