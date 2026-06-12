"""Universal detection layer: engine estimates the query cost (ARCHITECTURE §3.6).

ClickHouse `EXPLAIN ESTIMATE` returns the rows/marks/parts it expects to read; we
turn that into a scan-fraction against the table's known size. Engine-agnostic in
spirit (every engine has a dry-run), ClickHouse-specific in syntax. Read-only: runs
through the same RunQuery seam as introspection and the SQL guard.
"""

from __future__ import annotations

from auto_bi.introspect.clickhouse import RunQuery


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
