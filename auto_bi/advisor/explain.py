"""Universal detection layer: engine estimates the query cost (ARCHITECTURE §3.3).

ClickHouse `EXPLAIN ESTIMATE` returns the rows/marks/parts it expects to read; we
turn that into a scan-fraction against the table's known size. Engine-agnostic in
spirit (every engine has a dry-run), ClickHouse-specific in syntax. Read-only: runs
through the same RunQuery seam as introspection and the SQL guard.
"""

from __future__ import annotations

import re

from auto_bi.introspect.base import RunQuery

_TABLE_RE = re.compile(r"^(\w+)\.(\w+)$")


def live_row_count(run_query: RunQuery, table: str) -> int | None:
    """Current row count of `db.table` from system.tables; None if unavailable.

    The committed model's `physical.rows` is a git-frozen snapshot while every environment
    differs (P1-6: model 20M vs compose 100M vs HF demo 1M), so a scan fraction computed
    against it lies whenever the model is stale. When we can ask the live engine anyway
    (we just ran EXPLAIN through the same seam), the denominator should be live too.
    Never raises; a malformed name or a failed query degrades to None (model fallback).
    """
    m = _TABLE_RE.match(table)
    if not m:
        return None  # quoted/exotic identifiers: not worth an injection surface
    db, name = m.groups()
    try:
        rows = run_query(
            f"SELECT total_rows FROM system.tables WHERE database = '{db}' AND name = '{name}'"
        )
    except Exception:  # advisory only: no live count => static fallback, never raise
        return None
    if not rows:
        return None
    total = rows[0].get("total_rows")
    # NULL (non-MergeTree) or 0 (dropped/detached vs a model that says millions) carry no
    # usable signal for a denominator — fall back to the modeled size instead
    return int(total) if total else None


def estimate_scan(run_query: RunQuery, sql: str) -> dict | None:
    """`EXPLAIN ESTIMATE sql` -> {est_rows, est_marks, est_parts}; None if unavailable.

    Never raises: the advisor is advisory-only, so a failed estimate degrades to
    "no measured evidence", not an error.
    """
    try:
        rows = run_query(f"EXPLAIN ESTIMATE {sql}")
    except Exception:  # advisory only: any failure => no measured evidence, never raise
        return None
    if not rows:
        return None
    return {
        "est_rows": sum(int(r.get("rows", 0) or 0) for r in rows),
        "est_marks": sum(int(r.get("marks", 0) or 0) for r in rows),
        "est_parts": sum(int(r.get("parts", 0) or 0) for r in rows),
    }
