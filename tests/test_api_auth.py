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


def _auth_client(llm, demo_model, store, users) -> TestClient:
    for username, password, role, schemas in users:
        store.upsert_user(username, hash_password(password), role, schemas)
    app = create_app(
        model=demo_model,
        llm=llm,
        store=store,
        builder=_fake_builder,
        auth_enabled=True,
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


def test_approve_blocked_for_forbidden_schema(demo_model, tmp_path) -> None:
    # admin starts a session whose spec touches dm.*; a user without dm access must not be
    # able to build it (RBAC gate at approve — sessions are not per-user-owned by design).
    store = Store(tmp_path / "s.sqlite")
    client = _auth_client(
        ScriptedLLM([CLEAR_REPORT, GOOD_SPEC]),
        demo_model,
        store,
        [("admin", "pw", "admin", ["*"]), ("bob", "pw", "analyst", ["finance"])],
    )
    admin = _bearer(_login(client, "admin", "pw"))
    bob = _bearer(_login(client, "bob", "pw"))

    started = client.post("/api/v1/sessions", json={"request": "выручка по дням"}, headers=admin)
    assert started.status_code == 200, started.text
    sid = started.json()["session_id"]

    denied = client.post(f"/api/v1/sessions/{sid}/approve", headers=bob)
    assert denied.status_code == 403
    assert "dm." in denied.json()["detail"]

    allowed = client.post(f"/api/v1/sessions/{sid}/approve", headers=admin)
    assert allowed.status_code == 202


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
