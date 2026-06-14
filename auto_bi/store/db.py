"""SQLite store (task 1.9): sessions, messages, specs, builds, llm_calls,
dm_change_requests, trace_events.

stdlib sqlite3, no ORM (stack rule: no heavy frameworks). One Store per process;
the schema is created on open and is append-mostly — history is data, so specs
and builds are never updated in place, only superseded by new rows.

Schema v2 (observability, Phase 4): `llm_calls` gained `step` (which agent step the
call served) and `completion_chars` (answer size — GraceKelly returns no token usage,
so chars are the honest size proxy); `trace_events` is a durable per-session timeline
of agent steps (grounding/propose/advisor/approve) and build phases.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

_SCHEMA_VERSION = 2  # bump together with a migration when the schema changes

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    request     TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'open'
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
    completion_chars INTEGER NOT NULL DEFAULT 0
);
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
    status      TEXT NOT NULL DEFAULT 'open'
);
"""


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

        A brand-new DB (user_version 0) just had the current schema created by
        executescript, so we only stamp the version. A v1 DB predates the
        observability columns: `CREATE TABLE IF NOT EXISTS` left its old `llm_calls`
        untouched, so add the columns explicitly (trace_events is created by the
        IF NOT EXISTS above). Idempotent — guarded by the column check.
        """
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            self._db.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            return
        if version < 2:
            self._add_column("llm_calls", "step", "TEXT NOT NULL DEFAULT ''")
            self._add_column("llm_calls", "completion_chars", "INTEGER NOT NULL DEFAULT 0")
        if version < _SCHEMA_VERSION:
            self._db.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

    def _add_column(self, table: str, column: str, decl: str) -> None:
        existing = {r["name"] for r in self._db.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    def close(self) -> None:
        self._db.close()

    # --- sessions / messages --------------------------------------------------

    def create_session(self, request: str) -> str:
        session_id = uuid.uuid4().hex
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO sessions (id, request) VALUES (?, ?)", (session_id, request)
            )
        return session_id

    def set_session_status(self, session_id: str, status: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE sessions SET status = ? WHERE id = ?", (status, session_id))

    def add_message(self, session_id: str, role: str, content: str) -> int:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
                (session_id, role, content),
            )
        return cur.lastrowid

    def messages(self, session_id: str) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM messages WHERE session_id = ? ORDER BY id", session_id)

    # --- specs / builds ---------------------------------------------------------

    def save_spec(self, session_id: str, spec_json: dict, status: str = "proposed") -> int:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO specs (session_id, spec_json, status) VALUES (?, ?, ?)",
                (session_id, json.dumps(spec_json, ensure_ascii=False), status),
            )
        return cur.lastrowid

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
        dashboard_id: int | None = None,
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
        return cur.lastrowid

    def builds(self, session_id: str) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM builds WHERE session_id = ? ORDER BY id", session_id)

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
    ) -> int:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO llm_calls"
                " (session_id, model, prompt_sha256, prompt_chars, reasoning, status,"
                " latency_ms, step, completion_chars)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                ),
            )
        return cur.lastrowid

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
        return cur.lastrowid

    def trace_events(self, session_id: str) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT * FROM trace_events WHERE session_id = ? ORDER BY seq, id", session_id
        )

    def llm_usage_summary(self, session_id: str | None = None) -> dict[str, Any]:
        """Aggregates for the LLM-usage dashboard. GraceKelly exposes no token/cost
        usage, so this is built on measured signals only: call counts, latency, and
        char volumes (size proxies — never presented as tokens or money)."""
        where = "WHERE session_id = ?" if session_id is not None else ""
        params: tuple[Any, ...] = (session_id,) if session_id is not None else ()
        totals_row = self._db_one(
            "SELECT COUNT(*) AS calls,"
            " COALESCE(SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END), 0) AS ok,"
            " COALESCE(SUM(latency_ms), 0) AS latency_ms_total,"
            " COALESCE(CAST(ROUND(AVG(latency_ms)) AS INTEGER), 0) AS latency_ms_avg,"
            " COALESCE(MAX(latency_ms), 0) AS latency_ms_max,"
            " COALESCE(SUM(prompt_chars), 0) AS prompt_chars,"
            " COALESCE(SUM(completion_chars), 0) AS completion_chars,"
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
                " COALESCE(SUM(completion_chars), 0) AS completion_chars"
                f" FROM llm_calls {where} GROUP BY {column} ORDER BY calls DESC",
                *params,
            )

        return {
            "totals": totals,
            "by_model": _breakdown("model"),
            "by_step": _breakdown("step"),
            "by_status": _breakdown("status"),
        }

    # --- dm change requests ---------------------------------------------------------

    def add_dm_change_request(
        self,
        session_id: str | None,
        *,
        table_name: str,
        rule: str,
        severity: str,
        narrative: str = "",
    ) -> int:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO dm_change_requests (session_id, table_name, rule, severity,"
                " narrative) VALUES (?, ?, ?, ?, ?)",
                (session_id, table_name, rule, severity, narrative),
            )
        return cur.lastrowid

    def dm_change_requests(self, status: str | None = None) -> list[dict[str, Any]]:
        if status is None:
            return self._rows("SELECT * FROM dm_change_requests ORDER BY id")
        return self._rows("SELECT * FROM dm_change_requests WHERE status = ? ORDER BY id", status)

    def dm_change_request(self, request_id: int) -> dict[str, Any] | None:
        """One DCR with its session context (what the user was trying to build)."""
        rows = self._rows(
            "SELECT r.*, s.request AS session_request FROM dm_change_requests r"
            " LEFT JOIN sessions s ON s.id = r.session_id WHERE r.id = ?",
            request_id,
        )
        return rows[0] if rows else None

    def set_dm_change_request_status(self, request_id: int, status: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE dm_change_requests SET status = ? WHERE id = ?", (status, request_id)
            )

    # --- helpers ------------------------------------------------------------------

    def _rows(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, params).fetchall()]

    def _db_one(self, sql: str, params: tuple[Any, ...]) -> sqlite3.Row:
        with self._lock:
            return self._db.execute(sql, params).fetchone()
