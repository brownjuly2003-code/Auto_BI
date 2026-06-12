"""SQLite store (task 1.9): sessions, messages, specs, builds, llm_calls,
dm_change_requests.

stdlib sqlite3, no ORM (stack rule: no heavy frameworks). One Store per process;
the schema is created on open and is append-mostly — history is data, so specs
and builds are never updated in place, only superseded by new rows.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

_SCHEMA_VERSION = 1  # bump together with a migration when the schema changes

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
    latency_ms    INTEGER NOT NULL
);
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
        self._db = sqlite3.connect(self._path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")  # per-connection; off by default
        self._db.executescript(_SCHEMA)
        if self._db.execute("PRAGMA user_version").fetchone()[0] == 0:
            self._db.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    # --- sessions / messages --------------------------------------------------

    def create_session(self, request: str) -> str:
        session_id = uuid.uuid4().hex
        with self._db:
            self._db.execute(
                "INSERT INTO sessions (id, request) VALUES (?, ?)", (session_id, request)
            )
        return session_id

    def set_session_status(self, session_id: str, status: str) -> None:
        with self._db:
            self._db.execute("UPDATE sessions SET status = ? WHERE id = ?", (status, session_id))

    def add_message(self, session_id: str, role: str, content: str) -> int:
        with self._db:
            cur = self._db.execute(
                "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
                (session_id, role, content),
            )
        return cur.lastrowid

    def messages(self, session_id: str) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM messages WHERE session_id = ? ORDER BY id", session_id)

    # --- specs / builds ---------------------------------------------------------

    def save_spec(self, session_id: str, spec_json: dict, status: str = "proposed") -> int:
        with self._db:
            cur = self._db.execute(
                "INSERT INTO specs (session_id, spec_json, status) VALUES (?, ?, ?)",
                (session_id, json.dumps(spec_json, ensure_ascii=False), status),
            )
        return cur.lastrowid

    def set_spec_status(self, spec_id: int, status: str) -> None:
        with self._db:
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
        with self._db:
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
    ) -> int:
        with self._db:
            cur = self._db.execute(
                "INSERT INTO llm_calls"
                " (session_id, model, prompt_sha256, prompt_chars, reasoning, status, latency_ms)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    model,
                    prompt_sha256,
                    prompt_chars,
                    int(reasoning),
                    status,
                    latency_ms,
                ),
            )
        return cur.lastrowid

    def llm_calls(self, session_id: str | None = None) -> list[dict[str, Any]]:
        if session_id is None:
            return self._rows("SELECT * FROM llm_calls ORDER BY id")
        return self._rows("SELECT * FROM llm_calls WHERE session_id = ? ORDER BY id", session_id)

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
        with self._db:
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

    def set_dm_change_request_status(self, request_id: int, status: str) -> None:
        with self._db:
            self._db.execute(
                "UPDATE dm_change_requests SET status = ? WHERE id = ?", (status, request_id)
            )

    # --- helpers ------------------------------------------------------------------

    def _rows(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        return [dict(r) for r in self._db.execute(sql, params).fetchall()]
