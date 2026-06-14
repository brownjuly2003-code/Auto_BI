"""HTTP-level auth + RBAC (Phase 4, opt-in). Builds the app with auth_enabled=True
and a store seeded with users; verifies 401/login/logout/me, schema-scoped fields,
and the approve RBAC gate. Auth-off behaviour is covered by the rest of test_api.py.
"""

from fastapi.testclient import TestClient

from auto_bi.adapters.base import DashboardRef
from auto_bi.api import create_app
from auto_bi.auth import hash_password
from auto_bi.store import Store
from tests.test_machine import CLEAR_REPORT, ScriptedLLM
from tests.test_propose import GOOD_SPEC


def _fake_builder(spec, log, session_id):
    log("BUILD done")
    return DashboardRef(id=7, title=spec.title, url="/superset/dashboard/7/")


def _auth_client(llm, demo_model, store, users, model_path=None) -> TestClient:
    for username, password, role, schemas in users:
        store.upsert_user(username, hash_password(password), role, schemas)
    app = create_app(
        model=demo_model,
        llm=llm,
        store=store,
        builder=_fake_builder,
        auth_enabled=True,
        model_path=model_path,
    )
    return TestClient(app)


def _login(client: TestClient, username: str, password: str) -> str:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_health_is_open_and_reports_auth(demo_model, tmp_path) -> None:
    client = _auth_client(ScriptedLLM([]), demo_model, Store(tmp_path / "s.sqlite"), [])
    assert client.get("/api/v1/health").json() == {"ok": True, "auth": True}


def test_protected_route_requires_token(demo_model, tmp_path) -> None:
    client = _auth_client(ScriptedLLM([]), demo_model, Store(tmp_path / "s.sqlite"), [])
    r = client.get("/api/v1/model/fields")
    assert r.status_code == 401
    assert r.headers.get("www-authenticate") == "Bearer"


def test_login_bad_credentials(demo_model, tmp_path) -> None:
    client = _auth_client(
        ScriptedLLM([]),
        demo_model,
        Store(tmp_path / "s.sqlite"),
        [("alice", "pw", "analyst", ["dm"])],
    )
    assert (
        client.post("/api/v1/auth/login", json={"username": "alice", "password": "x"}).status_code
        == 401
    )
    assert (
        client.post("/api/v1/auth/login", json={"username": "ghost", "password": "x"}).status_code
        == 401
    )


def test_login_and_me(demo_model, tmp_path) -> None:
    client = _auth_client(
        ScriptedLLM([]),
        demo_model,
        Store(tmp_path / "s.sqlite"),
        [("alice", "pw", "analyst", ["dm"])],
    )
    token = _login(client, "alice", "pw")
    me = client.get("/api/v1/auth/me", headers=_bearer(token)).json()
    assert me == {"username": "alice", "role": "analyst", "schemas": ["dm"]}


def test_logout_invalidates_token(demo_model, tmp_path) -> None:
    client = _auth_client(
        ScriptedLLM([]),
        demo_model,
        Store(tmp_path / "s.sqlite"),
        [("alice", "pw", "analyst", ["dm"])],
    )
    token = _login(client, "alice", "pw")
    assert client.post("/api/v1/auth/logout", headers=_bearer(token)).status_code == 204
    assert client.get("/api/v1/auth/me", headers=_bearer(token)).status_code == 401


def test_fields_scoped_to_allowed_schemas(demo_model, tmp_path) -> None:
    client = _auth_client(
        ScriptedLLM([]),
        demo_model,
        Store(tmp_path / "s.sqlite"),
        [("alice", "pw", "analyst", ["dm"]), ("bob", "pw", "analyst", ["finance"])],
    )
    alice = client.get("/api/v1/model/fields", headers=_bearer(_login(client, "alice", "pw")))
    bob = client.get("/api/v1/model/fields", headers=_bearer(_login(client, "bob", "pw")))
    assert {t["table"] for t in alice.json()} == {"dm.sales_daily", "dm.stores"}
    assert bob.json() == []  # bob has no access to the dm schema


def test_session_owner_isolation(demo_model, tmp_path) -> None:
    # a session is addressable only by its owner or an admin; a foreign non-admin gets
    # 404 (existence hidden) on every session-scoped endpoint — no reading another's
    # trace, no injecting a reply into another's in-progress spec (S6 P2-2).
    store = Store(tmp_path / "s.sqlite")
    client = _auth_client(
        ScriptedLLM([CLEAR_REPORT, GOOD_SPEC]),
        demo_model,
        store,
        [
            ("alice", "pw", "analyst", ["dm"]),
            ("bob", "pw", "analyst", ["dm"]),  # same schemas, different user
            ("root", "pw", "admin", ["*"]),
        ],
    )
    alice = _bearer(_login(client, "alice", "pw"))
    bob = _bearer(_login(client, "bob", "pw"))
    root = _bearer(_login(client, "root", "pw"))
    sid = client.post(
        "/api/v1/sessions", json={"request": "выручка по дням"}, headers=alice
    ).json()["session_id"]

    assert client.get(f"/api/v1/sessions/{sid}", headers=bob).status_code == 404
    assert client.get(f"/api/v1/sessions/{sid}/trace", headers=bob).status_code == 404
    assert (
        client.post(f"/api/v1/sessions/{sid}/reply", json={"text": "x"}, headers=bob).status_code
        == 404
    )
    assert client.get(f"/api/v1/sessions/{sid}", headers=alice).status_code == 200  # owner
    assert client.get(f"/api/v1/sessions/{sid}", headers=root).status_code == 200  # admin sees all


def test_enrichment_patch_requires_schema_access(demo_model, tmp_path) -> None:
    # write-side RBAC (S6 P2-1): a user cannot edit the shared model.yaml for a table
    # outside their schemas. The 403 fires before the model-path check.
    store = Store(tmp_path / "s.sqlite")
    model_path = tmp_path / "model.yaml"
    demo_model.dump(model_path)
    client = _auth_client(
        ScriptedLLM([]),
        demo_model,
        store,
        [("alice", "pw", "analyst", ["dm"]), ("bob", "pw", "analyst", ["finance"])],
        model_path=model_path,
    )
    alice = _bearer(_login(client, "alice", "pw"))
    bob = _bearer(_login(client, "bob", "pw"))
    assert (
        client.patch(
            "/api/v1/model/tables/dm.stores", json={"description": "x"}, headers=bob
        ).status_code
        == 403
    )
    assert (
        client.patch(
            "/api/v1/model/tables/dm.stores", json={"description": "ok"}, headers=alice
        ).status_code
        == 200
    )


def test_authorized_user_completes_full_flow(demo_model, tmp_path) -> None:
    store = Store(tmp_path / "s.sqlite")
    client = _auth_client(
        ScriptedLLM([CLEAR_REPORT, GOOD_SPEC]),
        demo_model,
        store,
        [("alice", "pw", "analyst", ["dm"])],
    )
    auth = _bearer(_login(client, "alice", "pw"))
    started = client.post("/api/v1/sessions", json={"request": "выручка по дням"}, headers=auth)
    assert started.status_code == 200, started.text
    sid = started.json()["session_id"]
    assert started.json()["phase"] == "approve"
    assert client.post(f"/api/v1/sessions/{sid}/approve", headers=auth).status_code == 202
