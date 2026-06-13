"""Engine seam (Phase 3): map a DM's physical engine to its SQL dialect and traits.

v1 is ClickHouse; v2 adds the Greenplum/Greengage family (PG-based). Universality lives
in the seam (ARCHITECTURE §1.1): SQL_GEN builds a dialect-agnostic sqlglot AST and only
the output dialect + a few per-engine traits (trial-run wrapper, EXPLAIN evidence) differ.
"""

from __future__ import annotations

CLICKHOUSE = "clickhouse"
GREENPLUM = "greenplum"  # Greengage is a Greenplum fork — same dialect/catalogs

# physical.engine -> sqlglot dialect used by SQL_GEN and the SQL guard.
_SQLGLOT_DIALECT = {
    CLICKHOUSE: "clickhouse",
    GREENPLUM: "postgres",  # Greenplum/Greengage speak PostgreSQL
    "greengage": "postgres",
    "postgres": "postgres",
    "postgresql": "postgres",
}


def sqlglot_dialect(engine: str | None) -> str:
    """sqlglot dialect for an engine name; defaults to clickhouse (the v1 engine)."""
    return _SQLGLOT_DIALECT.get((engine or "").lower(), "clickhouse")
