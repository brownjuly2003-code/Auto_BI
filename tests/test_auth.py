"""Auth + RBAC unit tests (auto_bi.auth) and the store user/token methods.

No HTTP here — the API-level auth/RBAC wiring is in test_api_auth.py.
"""

import pytest

from auto_bi.auth import (
    AuthUser,
    filter_model_by_schemas,
    forbidden_tables,
    hash_password,
    is_table_allowed,
    load_users_file,
    new_token,
    schema_of,
    seed_users,
    spec_tables,
    verify_password,
)
from auto_bi.config import Settings
from auto_bi.ir.spec import (
    ChartQuery,
    ChartSpec,
    DashboardSpec,
    JoinSpec,
    Measure,
    Viz,
)
from auto_bi.semantic.model import Aggregation, ColumnRole, Join, SemanticModel, Table
from auto_bi.semantic.model import Column as Col
from auto_bi.store import Store
from auto_bi.store.db import _hash_token

# --- passwords + tokens -------------------------------------------------------


def test_hash_verify_roundtrip() -> None:
    stored = hash_password("correct horse")
    assert stored.startswith("pbkdf2_sha256$")
    assert verify_password("correct horse", stored)
    assert not verify_password("wrong", stored)


def test_hash_is_salted() -> None:
    assert hash_password("same") != hash_password("same")  # random salt


def test_verify_rejects_malformed_hash() -> None:
    assert not verify_password("x", "")
    assert not verify_password("x", "not-a-valid-format")
    assert not verify_password("x", "bcrypt$1$aa$bb")  # unknown algo


def test_new_token_unique_and_urlsafe() -> None:
    a, b = new_token(), new_token()
    assert a != b and len(a) > 20


# --- RBAC helpers -------------------------------------------------------------


def test_schema_of() -> None:
    assert schema_of("dm.sales_daily") == "dm"
    assert schema_of("finance.ledger") == "finance"


def test_is_table_allowed() -> None:
    assert is_table_allowed("dm.sales", ["dm"])
    assert is_table_allowed("dm.sales", ["*"])  # wildcard
    assert not is_table_allowed("dm.sales", ["finance"])
    assert not is_table_allowed("dm.sales", [])


def _two_schema_model() -> SemanticModel:
    return SemanticModel(
        tables=[
            Table(
                name="dm.sales", columns=[Col(name="cur", type="String", role=ColumnRole.DIMENSION)]
            ),
            Table(
                name="ext.rates",
                columns=[Col(name="cur", type="String", role=ColumnRole.DIMENSION)],
            ),
        ],
        joins=[Join(left="dm.sales.cur", right="ext.rates.cur")],
    )


def test_filter_model_wildcard_returns_same_object() -> None:
    model = _two_schema_model()
    assert filter_model_by_schemas(model, ["*"]) is model


def test_filter_model_subset_drops_tables_and_dangling_joins() -> None:
    model = _two_schema_model()
    scoped = filter_model_by_schemas(model, ["dm"])
    assert [t.name for t in scoped.tables] == ["dm.sales"]
    assert scoped.joins == []  # join referenced ext.rates -> dropped
    # original is untouched (deep copy)
    assert [t.name for t in model.tables] == ["dm.sales", "ext.rates"]


def test_filter_model_keeps_join_when_both_sides_allowed() -> None:
    model = _two_schema_model()
    scoped = filter_model_by_schemas(model, ["dm", "ext"])
    assert {t.name for t in scoped.tables} == {"dm.sales", "ext.rates"}
    assert len(scoped.joins) == 1


def _spec_over(table: str, *, join_table: str | None = None) -> DashboardSpec:
    query = ChartQuery(table=table, measures=[Measure(column="x", agg=Aggregation.SUM)])
    if join_table:
        query.joins = [JoinSpec(table=join_table, on_left=f"{table}.k", on_right=f"{join_table}.k")]
    return DashboardSpec(
        title="t", charts=[ChartSpec(id="c1", title="c1", viz=Viz.TABLE, query=query)]
    )


def test_spec_tables_includes_joins() -> None:
    spec = _spec_over("dm.sales", join_table="ext.rates")
    assert spec_tables(spec) == {"dm.sales", "ext.rates"}


def test_forbidden_tables() -> None:
    spec = _spec_over("dm.sales", join_table="ext.rates")
    assert forbidden_tables(spec, ["*"]) == []
    assert forbidden_tables(spec, ["dm", "ext"]) == []
    assert forbidden_tables(spec, ["dm"]) == ["ext.rates"]
    assert forbidden_tables(spec, ["finance"]) == ["dm.sales", "ext.rates"]


def test_auth_user_is_admin() -> None:
    assert AuthUser("a", role="admin", allowed_schemas=["*"]).is_admin
    assert not AuthUser("a", role="analyst", allowed_schemas=["dm"]).is_admin


# --- users file + seeding -----------------------------------------------------


def test_load_users_file(tmp_path) -> None:
    p = tmp_path / "users.yaml"
    p.write_text(
        "users:\n"
        "  - username: alice\n"
        "    password: pw1\n"
        "    role: analyst\n"
        "    schemas: [dm]\n"
        "  - username: root\n"
        "    password: pw2\n"
        "    role: admin\n"
        "    schemas: ['*']\n",
        encoding="utf-8",
    )
    users = load_users_file(p)
    assert [u["username"] for u in users] == ["alice", "root"]
    assert users[0]["schemas"] == ["dm"] and users[1]["role"] == "admin"


def test_load_users_file_requires_password(tmp_path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("users:\n  - username: alice\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_users_file(p)


def test_seed_users_bootstrap_admin(tmp_path) -> None:
    store = Store(tmp_path / "s.sqlite")
    settings = Settings(auth_users_file="", admin_user="root", admin_password="secret")
    assert seed_users(store, settings) == 1
    row = store.get_user("root")
    assert row["role"] == "admin" and row["allowed_schemas"] == ["*"]
    assert verify_password("secret", row["password_hash"])
    store.close()


def test_seed_users_from_file_is_idempotent(tmp_path) -> None:
    users = tmp_path / "users.yaml"
    users.write_text(
        "users:\n  - username: alice\n    password: pw\n    role: analyst\n    schemas: [dm]\n",
        encoding="utf-8",
    )
    store = Store(tmp_path / "s.sqlite")
    settings = Settings(auth_users_file=str(users))
    seed_users(store, settings)
    seed_users(store, settings)  # second seed must not duplicate (upsert by username)
    assert [u["username"] for u in store.list_users()] == ["alice"]
    assert store.get_user("alice")["allowed_schemas"] == ["dm"]
    store.close()


# --- store: tokens ------------------------------------------------------------


def test_token_create_resolve_delete(tmp_path) -> None:
    store = Store(tmp_path / "s.sqlite")
    store.upsert_user("alice", hash_password("pw"), "analyst", ["dm"])
    uid = store.get_user("alice")["id"]
    token = store.create_token(new_token(), uid, ttl_hours=24)
    assert store.token_user(token)["username"] == "alice"
    store.delete_token(token)
    assert store.token_user(token) is None
    store.close()


def test_expired_token_not_resolved(tmp_path) -> None:
    store = Store(tmp_path / "s.sqlite")
    store.upsert_user("alice", hash_password("pw"), "analyst", ["dm"])
    uid = store.get_user("alice")["id"]
    token = store.create_token(new_token(), uid, ttl_hours=24)
    # force expiry into the past, deterministically. auth_tokens.token stores sha256(token)
    # (B-4), not the raw value, so the WHERE clause must match on the hash too.
    with store._lock, store._db:
        store._db.execute(
            "UPDATE auth_tokens SET expires_at = datetime('now', '-1 hour') WHERE token = ?",
            (_hash_token(token),),
        )
    assert store.token_user(token) is None
    assert store.purge_expired_tokens() == 1
    store.close()


def test_token_stored_as_sha256_hash_not_plaintext(tmp_path) -> None:
    store = Store(tmp_path / "s.sqlite")
    store.upsert_user("alice", hash_password("pw"), "analyst", ["dm"])
    uid = store.get_user("alice")["id"]
    token = store.create_token(new_token(), uid, ttl_hours=24)
    (row,) = store._rows("SELECT token FROM auth_tokens")
    assert row["token"] == _hash_token(token)
    assert row["token"] != token
    store.close()
