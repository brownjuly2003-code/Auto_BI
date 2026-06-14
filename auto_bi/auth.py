"""Authentication + RBAC (Phase 4, opt-in via AUTO_BI_AUTH_ENABLED).

stdlib only: pbkdf2_hmac password hashing, `secrets` tokens, `hmac.compare_digest`
verification. RBAC restricts which DWH schemas a user may ground/build over — the
schema is the segment before the first dot in a fully qualified table name
('dm.sales' -> 'dm'). `["*"]` grants all schemas.

The whole layer is OFF by default: the CLI, the test suite and the single-user §2.1
flow keep working unchanged. When enabled, the API requires a bearer token and the
agent only ever sees the caller's permitted tables.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from auto_bi.config import Settings
    from auto_bi.ir.spec import DashboardSpec
    from auto_bi.semantic.model import SemanticModel
    from auto_bi.store import Store

_ALGO = "pbkdf2_sha256"
_PBKDF2_ITERATIONS = 240_000


# --- passwords + tokens -------------------------------------------------------


def hash_password(
    password: str, *, salt: bytes | None = None, iterations: int = _PBKDF2_ITERATIONS
) -> str:
    """`pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>` (self-describing)."""
    salt = salt if salt is not None else secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{_ALGO}${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iter_s, salt_hex, hash_hex = stored.split("$")
        if algo != _ALGO:
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iter_s)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


def new_token() -> str:
    return secrets.token_urlsafe(32)


# --- RBAC by DWH schema -------------------------------------------------------


@dataclass(frozen=True)
class AuthUser:
    username: str
    role: str = "analyst"
    allowed_schemas: list[str] = field(default_factory=list)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


# Used when auth is disabled: full access, behaves like today's single-user flow.
ANONYMOUS_ADMIN = AuthUser(username="anonymous", role="admin", allowed_schemas=["*"])


def schema_of(table_name: str) -> str:
    """Schema = the segment before the first dot ('dm.sales' -> 'dm')."""
    return table_name.split(".", 1)[0]


def allows_all(allowed_schemas: list[str]) -> bool:
    return "*" in allowed_schemas


def is_table_allowed(table_name: str, allowed_schemas: list[str]) -> bool:
    return allows_all(allowed_schemas) or schema_of(table_name) in set(allowed_schemas)


def filter_model_by_schemas(model: SemanticModel, allowed_schemas: list[str]) -> SemanticModel:
    """A copy of the model with only tables/joins in the allowed schemas.

    `["*"]` returns the model unchanged. A join whose either side references a dropped
    table is removed, so the filtered model stays self-consistent for grounding/SQL_GEN.
    """
    if allows_all(allowed_schemas):
        return model
    allowed = set(allowed_schemas)
    tables = [t for t in model.tables if schema_of(t.name) in allowed]
    names = {t.name for t in tables}

    def _join_ok(join) -> bool:
        # left/right are 'schema.table.column' -> the table is the first two segments
        left_table = ".".join(join.left.split(".")[:2])
        right_table = ".".join(join.right.split(".")[:2])
        return left_table in names and right_table in names

    joins = [j for j in model.joins if _join_ok(j)]
    return model.model_copy(update={"tables": tables, "joins": joins}, deep=True)


def spec_tables(spec: DashboardSpec) -> set[str]:
    """Every DWH table a spec touches: each chart's base table + its joined tables."""
    tables: set[str] = set()
    for chart in spec.charts:
        tables.add(chart.query.table)
        tables.update(j.table for j in chart.query.joins)
    return tables


def forbidden_tables(spec: DashboardSpec, allowed_schemas: list[str]) -> list[str]:
    """Spec tables outside the allowed schemas (sorted); empty list = the build is allowed."""
    if allows_all(allowed_schemas):
        return []
    return sorted(t for t in spec_tables(spec) if not is_table_allowed(t, allowed_schemas))


# --- users file + seeding -----------------------------------------------------


def load_users_file(path: str | Path) -> list[dict]:
    """Parse the users YAML: a top-level `users:` list of {username,password,role,schemas}.

    Passwords are plaintext here (an operator-managed secret, like .env — keep the file
    out of version control); they are hashed before they ever reach the store.
    """
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    users = data.get("users", [])
    if not isinstance(users, list):
        raise ValueError("users file: top-level `users` must be a list")
    parsed: list[dict] = []
    for i, u in enumerate(users):
        if not u.get("username") or not u.get("password"):
            raise ValueError(f"users file: entry #{i} needs both username and password")
        parsed.append(
            {
                "username": str(u["username"]),
                "password": str(u["password"]),
                "role": str(u.get("role", "analyst")),
                "schemas": list(u.get("schemas", [])),
            }
        )
    return parsed


def seed_users(store: Store, settings: Settings) -> int:
    """Seed users into the store (idempotent upsert by username). Returns the count.

    From `auth_users_file` if set; otherwise a single bootstrap admin from
    `admin_user`/`admin_password` (admin has all schemas). Called once on `serve`
    startup when auth is enabled.
    """
    if settings.auth_users_file:
        entries = load_users_file(settings.auth_users_file)
    elif settings.admin_password:
        entries = [
            {
                "username": settings.admin_user,
                "password": settings.admin_password,
                "role": "admin",
                "schemas": ["*"],
            }
        ]
    else:
        entries = []
    for e in entries:
        schemas = e["schemas"] if e["role"] != "admin" or e["schemas"] else ["*"]
        store.upsert_user(e["username"], hash_password(e["password"]), e["role"], schemas)
    return len(entries)
