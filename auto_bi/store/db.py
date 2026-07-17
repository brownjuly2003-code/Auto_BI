"""SQLite store (task 1.9): sessions, messages, specs, builds, llm_calls,
dm_change_requests, trace_events.

stdlib sqlite3, no ORM (stack rule: no heavy frameworks). One Store per process;
the schema is created on open and is append-mostly — history is data, so specs
and builds are never updated in place, only superseded by new rows.

Schema v2 (observability, Phase 4): `llm_calls` gained `step` (which agent step the
call served) and `completion_chars` (answer size — GraceKelly returns no token usage,
so chars are the honest size proxy); `trace_events` is a durable per-session timeline
of agent steps (grounding/propose/advisor/approve) and build phases.

Schema v5 (token accounting, E2): `llm_calls` gained nullable `input_tokens` /
`output_tokens`. The Anthropic Messages API returns `usage.input_tokens/output_tokens`,
so calls on that provider carry real tokens; GraceKelly reports no usage and a transport
error has no response, so those rows stay NULL (NULL = "no usage reported", distinct from
a real zero — `completion_chars` remains the universal size proxy for every call).

Schema v6 (B-4 hardening): `auth_tokens.token` now stores sha256(raw token) hex, not the
raw bearer token — a stolen SQLite file no longer yields live sessions directly. The
column keeps its name (no ALTER ... RENAME) to avoid a migration that touches the primary
key's identity; only its content changed meaning. `create_token`/`token_user`/
`delete_token` hash the caller-supplied raw token before every read/write, so callers are
unaffected.

Schema v7 (X-4 session-resume): `sessions` gained `owner` (username when auth is on,
NULL otherwise), `target_bi` (the per-session BI choice, previously in-memory only) and
`pinned` (JSON array of seed-pinned tables) — the three pieces of session state that
cannot be reconstructed from messages/specs/builds after a restart. Legacy rows get
owner=NULL / target_bi='superset' / pinned='[]', which hydration treats as "admin-only
when auth is on, Superset, no pins".
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def _row_id(cur: sqlite3.Cursor) -> int:
    """`Cursor.lastrowid` is Optional in the stubs but is always set by the INSERT that
    precedes each call here; assert it so the insert helpers keep their `-> int`."""
    assert cur.lastrowid is not None
    return cur.lastrowid


_SCHEMA_VERSION = 7  # bump together with a migration when the schema changes

_TOKEN_HASH_RE = re.compile(r"^[0-9a-f]{64}$")  # sha256 hex digest shape


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    request     TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'open',
    owner       TEXT,                                    -- v7: username when auth is on
    target_bi   TEXT NOT NULL DEFAULT 'superset',        -- v7: per-session BI choice
    pinned      TEXT NOT NULL DEFAULT '[]'               -- v7: JSON array of seed tables
);
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    role        TEXT NOT NULL,
    content     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS specs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    spec_json   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'proposed'
);
CREATE TABLE IF NOT EXISTS builds (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT REFERENCES sessions(id),
    spec_id      INTEGER REFERENCES specs(id),
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    dashboard_id INTEGER,
    url          TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'ok',
    error        TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    model         TEXT NOT NULL,
    prompt_sha256 TEXT NOT NULL,
    prompt_chars  INTEGER NOT NULL,
    reasoning     INTEGER NOT NULL,
    status        TEXT NOT NULL,
    latency_ms    INTEGER NOT NULL,
    step          TEXT NOT NULL DEFAULT '',
    completion_chars INTEGER NOT NULL DEFAULT 0,
    input_tokens  INTEGER,  -- NULL = provider reported no usage (GraceKelly / transport error)
    output_tokens INTEGER   -- real tokens only where the provider returns usage (Anthropic)
);
-- llm/budget.py reads this ledger per session and per rolling window on every provider
-- round-trip; index the two scope keys (created via always-run CREATE IF NOT EXISTS, no
-- schema-version bump). Cheap at demo scale, keeps the budget checks off a full scan.
CREATE INDEX IF NOT EXISTS ix_llm_calls_session ON llm_calls(session_id);
CREATE INDEX IF NOT EXISTS ix_llm_calls_created ON llm_calls(created_at);
CREATE TABLE IF NOT EXISTS trace_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    seq         INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'ok',
    latency_ms  INTEGER NOT NULL DEFAULT 0,
    detail      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_trace_events_session ON trace_events(session_id, seq);
CREATE TABLE IF NOT EXISTS dm_change_requests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT REFERENCES sessions(id),
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    table_name  TEXT NOT NULL,
    rule        TEXT NOT NULL,
    severity    TEXT NOT NULL,
    narrative   TEXT NOT NULL DEFAULT '',
    remediation TEXT NOT NULL DEFAULT '',  -- JSON array of Remediation artifacts (runnable DDL)
    status      TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    username        TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'analyst',
    allowed_schemas TEXT NOT NULL DEFAULT '[]'  -- JSON array; ["*"] = all schemas
);
CREATE TABLE IF NOT EXISTS auth_tokens (
    token       TEXT PRIMARY KEY,  -- sha256(raw bearer token) hex, not the raw token (v6)
    user_id     INTEGER NOT NULL REFERENCES users(id),
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_auth_tokens_user ON auth_tokens(user_id);
"""


def _decode_user(row: dict[str, Any]) -> dict[str, Any]:
    """Row -> user dict with allowed_schemas decoded from its JSON-array column."""
    user = dict(row)
    try:
        user["allowed_schemas"] = json.loads(user.get("allowed_schemas") or "[]")
    except (TypeError, ValueError):
        user["allowed_schemas"] = []
    return user


class Store:
    def __init__(self, path: str | Path = "data/auto_bi.sqlite") -> None:
        self._path = Path(path)
        if self._path.name != ":memory:":
            self._path.parent.mkdir(parents=True, exist_ok=True)
        # one connection shared across threads (HTTP API: threadpool handlers + the
        # build thread); our own lock serializes transactions, so the sqlite3
        # same-thread guard is unnecessary
        self._db = sqlite3.connect(self._path, check_same_thread=False)
        self._lock = threading.Lock()
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")  # per-connection; off by default
        self._db.executescript(_SCHEMA)
        self._migrate()
        self._db.commit()

    def _migrate(self) -> None:
        """Bring an existing DB up to _SCHEMA_VERSION.

        executescript ran first with `CREATE TABLE IF NOT EXISTS`, so a brand-new DB
        already has the full current schema (the guarded _add_column calls below are
        no-ops) and the newer tables (trace_events v2, users/auth_tokens v3) exist on
        any DB. The ALTERs add the v2 observability columns to a legacy `llm_calls` that
        IF NOT EXISTS left untouched — this covers both a v1 DB and a pre-versioning v0
        DB whose old llm_calls lacks the columns (so we must NOT early-return on version
        0). v3 added only new tables (no ALTER), so the version bump below suffices. v4
        adds dm_change_requests.remediation; v5 adds llm_calls.input_tokens/output_tokens
        (both guarded ALTERs, no-op on a fresh DB; the new columns are nullable so legacy
        rows back-fill to NULL = "no usage reported"). v6 rewrites existing plaintext
        `auth_tokens.token` values to their sha256 hex digest in place (B-4) — guarded by
        shape (`_TOKEN_HASH_RE`), not just the version check, so re-entering this branch
        (e.g. a v6 DB that somehow re-runs it) can never double-hash an already-hashed
        value. v7 adds sessions.owner/target_bi/pinned (guarded ALTERs, defaults cover
        legacy rows).
        Idempotent — guarded by the column check, so it is safe to run on any schema.
        """
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version < 2:
            self._add_column("llm_calls", "step", "TEXT NOT NULL DEFAULT ''")
            self._add_column("llm_calls", "completion_chars", "INTEGER NOT NULL DEFAULT 0")
        if version < 4:
            # v4: dm_change_requests carries the advisor's concrete fix artifact (DDL)
            self._add_column("dm_change_requests", "remediation", "TEXT NOT NULL DEFAULT ''")
        if version < 5:
            # v5: real token usage on providers that report it (Anthropic); nullable so
            # legacy/GraceKelly rows stay NULL rather than a misleading zero
            self._add_column("llm_calls", "input_tokens", "INTEGER")
            self._add_column("llm_calls", "output_tokens", "INTEGER")
        if version < 6:
            self._hash_legacy_tokens()
        if version < 7:
            # v7: durable session identity for restart-resume (X-4) — see module docstring
            self._add_column("sessions", "owner", "TEXT")
            self._add_column("sessions", "target_bi", "TEXT NOT NULL DEFAULT 'superset'")
            self._add_column("sessions", "pinned", "TEXT NOT NULL DEFAULT '[]'")
        if version < _SCHEMA_VERSION:
            self._db.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

    def _hash_legacy_tokens(self) -> None:
        tables = {r["name"] for r in self._db.execute("SELECT name FROM sqlite_master")}
        if "auth_tokens" not in tables:
            return
        for row in self._db.execute("SELECT token FROM auth_tokens").fetchall():
            raw = row["token"]
            if _TOKEN_HASH_RE.match(raw):
                continue  # already a hash — never re-hash
            self._db.execute(
                "UPDATE auth_tokens SET token = ? WHERE token = ?", (_hash_token(raw), raw)
            )

    def _add_column(self, table: str, column: str, decl: str) -> None:
        existing = {r["name"] for r in self._db.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    def close(self) -> None:
        self._db.close()

    def ping(self) -> None:
        """Cheapest possible liveness probe (B-6 readiness): raises if the connection is
        closed or the file is unreadable, returns nothing otherwise."""
        self._rows("SELECT 1")

    # --- sessions / messages --------------------------------------------------

    def create_session(
        self,
        request: str,
        *,
        owner: str | None = None,
        target_bi: str = "superset",
        pinned: Iterable[str] = (),
    ) -> str:
        """owner/target_bi/pinned (v7) persist the session state that hydration cannot
        reconstruct from messages/specs after a restart (X-4)."""
        session_id = uuid.uuid4().hex
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO sessions (id, request, owner, target_bi, pinned)"
                " VALUES (?, ?, ?, ?, ?)",
                (session_id, request, owner, target_bi, json.dumps(sorted(pinned))),
            )
        return session_id

    def session_row(self, session_id: str) -> dict[str, Any] | None:
        """Full session row for restart-resume (X-4); `pinned` decoded from JSON."""
        rows = self._rows("SELECT * FROM sessions WHERE id = ?", session_id)
        if not rows:
            return None
        row = rows[0]
        try:
            row["pinned"] = json.loads(row.get("pinned") or "[]")
        except (TypeError, ValueError):
            row["pinned"] = []
        return row

    def set_session_status(self, session_id: str, status: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE sessions SET status = ? WHERE id = ?", (status, session_id))

    def session_status(self, session_id: str) -> str | None:
        rows = self._rows("SELECT status FROM sessions WHERE id = ?", session_id)
        return rows[0]["status"] if rows else None

    def add_message(self, session_id: str, role: str, content: str) -> int:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
                (session_id, role, content),
            )
        return _row_id(cur)

    def messages(self, session_id: str) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM messages WHERE session_id = ? ORDER BY id", session_id)

    # --- specs / builds ---------------------------------------------------------

    def save_spec(self, session_id: str, spec_json: dict, status: str = "proposed") -> int:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO specs (session_id, spec_json, status) VALUES (?, ?, ?)",
                (session_id, json.dumps(spec_json, ensure_ascii=False), status),
            )
        return _row_id(cur)

    def set_spec_status(self, spec_id: int, status: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE specs SET status = ? WHERE id = ?", (status, spec_id))

    def specs(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._rows("SELECT * FROM specs WHERE session_id = ? ORDER BY id", session_id)
        for row in rows:
            row["spec_json"] = json.loads(row["spec_json"])
        return rows

    def save_build(
        self,
        session_id: str,
        spec_id: int | None,
        *,
        # DataLens entry ids are strings, Superset ids are ints (SQLite stores either)
        dashboard_id: int | str | None = None,
        url: str = "",
        status: str = "ok",
        error: str = "",
    ) -> int:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO builds (session_id, spec_id, dashboard_id, url, status, error)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, spec_id, dashboard_id, url, status, error),
            )
        return _row_id(cur)

    def builds(self, session_id: str) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM builds WHERE session_id = ? ORDER BY id", session_id)

    def reap_stuck_builds(self) -> list[str]:
        """Sessions left at status='building' by a process that died mid-build (kill/OOM/
        crash) have no builds-table row and no 'failed' status — a daemon build thread
        dying with the process leaves no trace of its own (B-7). Call once at server
        startup, before any new build starts: gives each orphan a synthetic 'failed'
        build row and flips it to 'failed' so a restart never silently loses the fact
        that a build was interrupted."""
        with self._lock, self._db:
            stuck = [
                r["id"]
                for r in self._db.execute("SELECT id FROM sessions WHERE status = 'building'")
            ]
            for session_id in stuck:
                spec_row = self._db.execute(
                    "SELECT id FROM specs WHERE session_id = ? ORDER BY id DESC LIMIT 1",
                    (session_id,),
                ).fetchone()
                self._db.execute(
                    "INSERT INTO builds (session_id, spec_id, status, error)"
                    " VALUES (?, ?, 'failed', ?)",
                    (
                        session_id,
                        spec_row["id"] if spec_row else None,
                        "interrupted: process restarted while build was in-flight",
                    ),
                )
                self._db.execute(
                    "UPDATE sessions SET status = 'failed' WHERE id = ?", (session_id,)
                )
        return stuck

    # --- llm calls ----------------------------------------------------------------

    def log_llm_call(
        self,
        *,
        session_id: str | None,
        model: str,
        prompt_sha256: str,
        prompt_chars: int,
        reasoning: bool,
        status: str,
        latency_ms: int,
        step: str = "",
        completion_chars: int = 0,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> int:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO llm_calls"
                " (session_id, model, prompt_sha256, prompt_chars, reasoning, status,"
                " latency_ms, step, completion_chars, input_tokens, output_tokens)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    model,
                    prompt_sha256,
                    prompt_chars,
                    int(reasoning),
                    status,
                    latency_ms,
                    step,
                    completion_chars,
                    input_tokens,
                    output_tokens,
                ),
            )
        return _row_id(cur)

    def llm_calls(self, session_id: str | None = None) -> list[dict[str, Any]]:
        if session_id is None:
            return self._rows("SELECT * FROM llm_calls ORDER BY id")
        return self._rows("SELECT * FROM llm_calls WHERE session_id = ? ORDER BY id", session_id)

    # --- observability: trace events + LLM usage ----------------------------------

    def add_trace_event(
        self,
        session_id: str | None,
        *,
        kind: str,
        status: str = "ok",
        latency_ms: int = 0,
        detail: str = "",
    ) -> int:
        """Append one step to a session's timeline. `seq` orders steps within the
        session (wall-clock created_at is too coarse — steps can share a second)."""
        with self._lock, self._db:
            seq = self._db.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM trace_events WHERE session_id IS ?",
                (session_id,),
            ).fetchone()[0]
            cur = self._db.execute(
                "INSERT INTO trace_events (session_id, seq, kind, status, latency_ms, detail)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, seq, kind, status, latency_ms, detail),
            )
        return _row_id(cur)

    def trace_events(self, session_id: str) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT * FROM trace_events WHERE session_id = ? ORDER BY seq, id", session_id
        )

    # Provider-native success statuses that mean "call produced a usable completion".
    # GraceKelly writes `completed`; Anthropic historically stored stop_reason (`end_turn`)
    # (audit P2-1). Both count as ok; genuine failures stay failed/refusal/transport_error.
    _LLM_OK_STATUSES = ("completed", "end_turn", "max_tokens", "stop_sequence")

    def llm_usage_summary(
        self, session_id: str | None = None, *, owner: str | None = None
    ) -> dict[str, Any]:
        """Aggregates for the LLM-usage dashboard. Char volumes are a universal size
        proxy (every call has them). Real `input_tokens`/`output_tokens` are summed
        NULL-ignoring — they are populated only on providers that report usage (Anthropic);
        GraceKelly reports none, so its rows stay NULL. `token_calls` counts the rows that
        carry real tokens, so callers can show token figures only when they exist rather
        than presenting a NULL-driven 0 as if it were measured.

        `owner` (P1-4): when set, only calls whose session is owned by that username —
        used for non-admin observability so a user never sees foreign spend.
        """
        if session_id is not None and owner is not None:
            raise ValueError("llm_usage_summary: pass session_id or owner, not both")
        if session_id is not None:
            where = "WHERE session_id = ?"
            params: tuple[Any, ...] = (session_id,)
        elif owner is not None:
            where = "WHERE session_id IN (SELECT id FROM sessions WHERE owner = ?)"
            params = (owner,)
        else:
            where = ""
            params = ()
        ok_list = ", ".join(f"'{s}'" for s in self._LLM_OK_STATUSES)
        totals_row = self._db_one(
            "SELECT COUNT(*) AS calls,"
            f" COALESCE(SUM(CASE WHEN status IN ({ok_list}) THEN 1 ELSE 0 END), 0) AS ok,"
            " COALESCE(SUM(latency_ms), 0) AS latency_ms_total,"
            " COALESCE(CAST(ROUND(AVG(latency_ms)) AS INTEGER), 0) AS latency_ms_avg,"
            " COALESCE(MAX(latency_ms), 0) AS latency_ms_max,"
            " COALESCE(SUM(prompt_chars), 0) AS prompt_chars,"
            " COALESCE(SUM(completion_chars), 0) AS completion_chars,"
            " COALESCE(SUM(input_tokens), 0) AS input_tokens,"
            " COALESCE(SUM(output_tokens), 0) AS output_tokens,"
            " COALESCE(SUM(CASE WHEN input_tokens IS NOT NULL THEN 1 ELSE 0 END), 0)"
            " AS token_calls,"
            " COALESCE(SUM(reasoning), 0) AS reasoning_calls"
            f" FROM llm_calls {where}",
            params,
        )
        totals = dict(totals_row)
        totals["failed"] = totals["calls"] - totals["ok"]

        def _breakdown(column: str) -> list[dict[str, Any]]:
            return self._rows(
                f"SELECT {column} AS {column}, COUNT(*) AS calls,"
                " COALESCE(SUM(latency_ms), 0) AS latency_ms_total,"
                " COALESCE(SUM(prompt_chars), 0) AS prompt_chars,"
                " COALESCE(SUM(completion_chars), 0) AS completion_chars,"
                " COALESCE(SUM(input_tokens), 0) AS input_tokens,"
                " COALESCE(SUM(output_tokens), 0) AS output_tokens"
                f" FROM llm_calls {where} GROUP BY {column} ORDER BY calls DESC",
                *params,
            )

        return {
            "totals": totals,
            "by_model": _breakdown("model"),
            "by_step": _breakdown("step"),
            "by_status": _breakdown("status"),
        }

    def _llm_usage(self, where: str, params: tuple[Any, ...]) -> dict[str, Any]:
        """Budget-scoped usage from `llm_calls` (audit P0-3 item 4, llm/budget.py).

        Returns `calls`, `latency_ms`, total estimated `tokens`, and a per-`model`
        breakdown so the enforcer can price cost. Tokens are the provider's real usage
        where reported (Anthropic), else char-estimated (chars / 4) so a token budget
        still bites on GraceKelly, which reports none. Every attempt is counted (a repair
        is a distinct row), independent of status — a budget must see all round-trips.
        """
        rows = self._rows(
            "SELECT model,"
            " COALESCE(SUM(COALESCE(input_tokens, prompt_chars / 4)), 0) AS input_tokens,"
            " COALESCE(SUM(COALESCE(output_tokens, completion_chars / 4)), 0) AS output_tokens,"
            " COUNT(*) AS calls,"
            " COALESCE(SUM(latency_ms), 0) AS latency_ms"
            f" FROM llm_calls WHERE {where} GROUP BY model",
            *params,
        )
        return {
            "calls": sum(r["calls"] for r in rows),
            "latency_ms": sum(r["latency_ms"] for r in rows),
            "tokens": sum(r["input_tokens"] + r["output_tokens"] for r in rows),
            "by_model": rows,
        }

    def session_llm_usage(self, session_id: str | None) -> dict[str, Any]:
        """Budget usage for one conversation, all-time (per-session scope)."""
        if session_id is None:
            return self._llm_usage("session_id IS NULL", ())
        return self._llm_usage("session_id = ?", (session_id,))

    def actor_llm_usage(self, owner: str | None, *, window_hours: int = 24) -> dict[str, Any]:
        """Budget usage in a rolling window for one actor, or globally.

        `owner` set -> only calls whose session that owner owns (per-actor/day scope when
        auth is on). `owner is None` -> every call in the window (the single global bucket
        when auth is off — a total-spend circuit breaker for the anonymous public demo).
        """
        window = f"-{int(window_hours)} hours"
        if owner is None:
            return self._llm_usage("created_at >= datetime('now', ?)", (window,))
        return self._llm_usage(
            "created_at >= datetime('now', ?)"
            " AND session_id IN (SELECT id FROM sessions WHERE owner = ?)",
            (window, owner),
        )

    # --- dm change requests ---------------------------------------------------------

    def add_dm_change_request(
        self,
        session_id: str | None,
        *,
        table_name: str,
        rule: str,
        severity: str,
        narrative: str = "",
        remediation: str = "",  # JSON array of Remediation artifacts, "" when none
    ) -> int:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO dm_change_requests (session_id, table_name, rule, severity,"
                " narrative, remediation) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, table_name, rule, severity, narrative, remediation),
            )
        return _row_id(cur)

    def dm_change_requests(
        self, status: str | None = None, *, owner: str | None = None
    ) -> list[dict[str, Any]]:
        """List DCRs with session context (request text + owner for RBAC P1-4).

        `owner` when set restricts to rows whose session is owned by that username
        (non-admin list path). Admin/auth-off pass owner=None for the full list.
        """
        sql = (
            "SELECT r.*, s.request AS session_request, s.owner AS session_owner"
            " FROM dm_change_requests r"
            " LEFT JOIN sessions s ON s.id = r.session_id"
        )
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("r.status = ?")
            params.append(status)
        if owner is not None:
            clauses.append("s.owner = ?")
            params.append(owner)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY r.id"
        return self._rows(sql, *params)

    def dm_change_requests_for_session(self, session_id: str) -> list[dict[str, Any]]:
        """One session's DCRs — hydration rebuilds the agent's dedup set from these (X-4)."""
        return self._rows(
            "SELECT * FROM dm_change_requests WHERE session_id = ? ORDER BY id", session_id
        )

    def dm_change_request(self, request_id: int) -> dict[str, Any] | None:
        """One DCR with its session context (what the user was trying to build + owner)."""
        rows = self._rows(
            "SELECT r.*, s.request AS session_request, s.owner AS session_owner"
            " FROM dm_change_requests r"
            " LEFT JOIN sessions s ON s.id = r.session_id WHERE r.id = ?",
            request_id,
        )
        return rows[0] if rows else None

    def set_dm_change_request_status(self, request_id: int, status: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE dm_change_requests SET status = ? WHERE id = ?", (status, request_id)
            )

    # --- auth: users + tokens (schema v3) -----------------------------------------

    def upsert_user(
        self, username: str, password_hash: str, role: str, allowed_schemas: list[str]
    ) -> None:
        """Create or update a user by username (idempotent seed from the users file)."""
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO users (username, password_hash, role, allowed_schemas)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(username) DO UPDATE SET"
                " password_hash = excluded.password_hash, role = excluded.role,"
                " allowed_schemas = excluded.allowed_schemas",
                (username, password_hash, role, json.dumps(allowed_schemas)),
            )

    def get_user(self, username: str) -> dict[str, Any] | None:
        rows = self._rows("SELECT * FROM users WHERE username = ?", username)
        return _decode_user(rows[0]) if rows else None

    def list_users(self) -> list[dict[str, Any]]:
        return [_decode_user(r) for r in self._rows("SELECT * FROM users ORDER BY username")]

    def create_token(self, token: str, user_id: int, ttl_hours: int) -> str:
        """Store sha256(token), return the raw token (B-4: the raw value never touches
        disk — a stolen store file cannot be replayed as a live bearer token)."""
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO auth_tokens (token, user_id, expires_at)"
                f" VALUES (?, ?, datetime('now', '+{int(ttl_hours)} hours'))",
                (_hash_token(token), user_id),
            )
        return token

    def token_user(self, token: str) -> dict[str, Any] | None:
        """Resolve a non-expired token to its user, else None."""
        rows = self._rows(
            "SELECT u.* FROM auth_tokens t JOIN users u ON u.id = t.user_id"
            " WHERE t.token = ? AND t.expires_at > datetime('now')",
            _hash_token(token),
        )
        return _decode_user(rows[0]) if rows else None

    def delete_token(self, token: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM auth_tokens WHERE token = ?", (_hash_token(token),))

    def purge_expired_tokens(self) -> int:
        with self._lock, self._db:
            cur = self._db.execute("DELETE FROM auth_tokens WHERE expires_at <= datetime('now')")
        return cur.rowcount

    # --- helpers ------------------------------------------------------------------

    def _rows(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, params).fetchall()]

    def _db_one(self, sql: str, params: tuple[Any, ...]) -> sqlite3.Row:
        with self._lock:
            return self._db.execute(sql, params).fetchone()
