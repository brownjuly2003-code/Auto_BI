"""SQL guard (ARCHITECTURE §4): single SELECT-only statement, everything else rejected.

Belt-and-suspenders for generated SQL today, the mandatory gate for the raw_sql
escape hatch later. Plus live validation: EXPLAIN + LIMIT-ed trial run on the DWH.
"""

import sqlglot
from sqlglot import expressions as exp

from auto_bi.introspect.base import RunQuery

DIALECT = "clickhouse"
TRIAL_LIMIT = 10
TRIAL_TIMEOUT_S = 30


class SQLGuardError(Exception):
    pass


def guard_sql(sql: str) -> None:
    """Raise unless sql is exactly one plain SELECT (CTEs allowed, writes/DDL never)."""
    try:
        statements = sqlglot.parse(sql, read=DIALECT)
    except sqlglot.errors.ParseError as e:
        raise SQLGuardError(f"SQL does not parse: {e}") from e

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise SQLGuardError(f"expected exactly one statement, got {len(statements)}")

    root = statements[0]
    if not isinstance(root, exp.Select | exp.Union):
        raise SQLGuardError(f"only SELECT is allowed, got {type(root).__name__}")

    forbidden = (
        exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter,
        exp.TruncateTable, exp.Grant, exp.Command, exp.Set,
    )  # fmt: skip
    for node in root.walk():
        if isinstance(node, forbidden):
            raise SQLGuardError(f"forbidden construct in SQL: {type(node).__name__}")


class LiveSQLValidator:
    """EXPLAIN + trial run with LIMIT/timeout via the read-only RunQuery seam."""

    def __init__(self, run_query: RunQuery) -> None:
        self._run = run_query

    def validate(self, sql: str) -> None:
        """guard -> EXPLAIN -> LIMIT-ed execution; raises SQLGuardError with context."""
        guard_sql(sql)
        try:
            self._run(f"EXPLAIN {sql}")
        except Exception as e:
            raise SQLGuardError(f"EXPLAIN failed: {e}") from e
        try:
            self._run(
                f"SELECT * FROM ({sql}) LIMIT {TRIAL_LIMIT} "
                f"SETTINGS max_execution_time = {TRIAL_TIMEOUT_S}"
            )
        except Exception as e:
            raise SQLGuardError(f"trial run failed: {e}") from e
