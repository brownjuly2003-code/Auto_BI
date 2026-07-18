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

# Table-valued / remote-source functions that SELECT-only does not make safe for a
# multi-schema DWH service account. Names are lowercased for comparison.
_FORBIDDEN_TABLE_FUNCS = frozenset(
    {
        "url",
        "s3",
        "hdfs",
        "file",
        "input",
        "remote",
        "remotesecure",
        "cluster",
        "clusterallreplicas",
        "s3cluster",
        "hdfscluster",
        "urlcluster",
        "filecluster",
        "azureblobstorage",
        "azureblobstoragecluster",
        "gcs",
        "oss",
        "deltalake",
        "deltalakecluster",
        "iceberg",
        "icebergcluster",
        "hudi",
        "hudicluster",
        "mysql",
        "postgresql",
        "sqlite",
        "odbc",
        "jdbc",
        "mongodb",
        "redis",
        # RBAC-blind local escapes: merge() reads every table matching a regexp with the
        # CALLER's rights, dictionary() bypasses schema scoping, executable() runs a binary.
        "merge",
        "dictionary",
        "executable",
    }
)


class SQLGuardError(Exception):
    pass


def _parse_one_select(sql: str, *, dialect: str = DIALECT) -> exp.Expression:
    """Parse exactly one SELECT/UNION statement or raise SQLGuardError."""
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except sqlglot.errors.ParseError as e:
        raise SQLGuardError(f"SQL does not parse: {e}") from e

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise SQLGuardError(f"expected exactly one statement, got {len(statements)}")

    root = statements[0]
    if not isinstance(root, exp.Select | exp.Union):
        raise SQLGuardError(f"only SELECT is allowed, got {type(root).__name__}")
    return root


def guard_sql(sql: str, *, dialect: str = DIALECT) -> None:
    """Raise unless sql is exactly one plain SELECT (CTEs allowed, writes/DDL never)."""
    root = _parse_one_select(sql, dialect=dialect)

    forbidden = (
        exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter,
        exp.TruncateTable, exp.Grant, exp.Command, exp.Set,
    )  # fmt: skip
    cte_names = {(cte.alias_or_name or "").lower() for cte in root.find_all(exp.CTE)}
    for node in root.walk():
        if isinstance(node, forbidden):
            raise SQLGuardError(f"forbidden construct in SQL: {type(node).__name__}")
        # Table-valued remote sources: `FROM url(...)` / `FROM s3(...)` etc.
        if isinstance(node, exp.Anonymous):
            name = (node.this or "").lower() if isinstance(node.this, str) else ""
            if name in _FORBIDDEN_TABLE_FUNCS:
                raise SQLGuardError(f"forbidden table function in SQL: {name}()")
        if isinstance(node, exp.Func):
            name = (node.sql_name() or "").lower()
            if name in _FORBIDDEN_TABLE_FUNCS:
                raise SQLGuardError(f"forbidden table function in SQL: {name}()")
        # sqlglot(clickhouse) parses some table functions as plain Tables (e.g.
        # dictionary('x') becomes Table with alias columns), so also flag any
        # UNQUALIFIED table whose name is denylisted. A real table of that name is
        # still reachable schema-qualified (dm.dictionary); CTE alias refs are fine.
        if isinstance(node, exp.Table) and not node.args.get("db"):
            name = (node.name or "").lower()
            if name in _FORBIDDEN_TABLE_FUNCS and name not in cte_names:
                raise SQLGuardError(f"forbidden table function in SQL: {name}()")


def extract_table_names(sql: str, *, dialect: str = DIALECT) -> frozenset[str]:
    """Physical table names referenced by a SELECT (CTE aliases excluded).

    Used by schema-RBAC for the raw_sql hatch: `query.table` is only a dataset label
    and must not be the sole RBAC surface. Returns fully-qualified names when the
    SQL qualifies them (`dm.sales_daily`); bare names stay bare (schema_of then
    treats the whole token as the schema segment — still fail-closed for RBAC if
    the bare name is not an allowed schema).
    """
    root = _parse_one_select(sql, dialect=dialect)
    cte_names = {(cte.alias_or_name or "").lower() for cte in root.find_all(exp.CTE)}
    tables: set[str] = set()
    for table in root.find_all(exp.Table):
        name = table.name
        if not name:
            continue
        # CTE self-reference: bare name matching a WITH alias, no db/catalog.
        if name.lower() in cte_names and not table.db and not table.catalog:
            continue
        parts = [p for p in (table.catalog, table.db, name) if p]
        tables.add(".".join(parts))
    return frozenset(tables)


def _trial_statements(sql: str, dialect: str) -> list[str]:
    """Per-engine LIMIT-ed trial run with a timeout. ClickHouse takes a query-level
    SETTINGS clause; Postgres/Greenplum need a session GUC set first (the RunQuery seam
    keeps one connection, so the SET persists for the following SELECT) and a subquery alias."""
    if dialect == "clickhouse":
        return [
            f"SELECT * FROM ({sql}) LIMIT {TRIAL_LIMIT} "
            f"SETTINGS max_execution_time = {TRIAL_TIMEOUT_S}"
        ]
    return [
        f"SET statement_timeout = '{TRIAL_TIMEOUT_S}s'",
        f"SELECT * FROM ({sql}) AS _auto_bi_trial LIMIT {TRIAL_LIMIT}",
    ]


class LiveSQLValidator:
    """EXPLAIN + trial run with LIMIT/timeout via the read-only RunQuery seam."""

    def __init__(self, run_query: RunQuery, *, dialect: str = DIALECT) -> None:
        self._run = run_query
        self._dialect = dialect

    def validate(self, sql: str) -> None:
        """guard -> EXPLAIN -> LIMIT-ed execution; raises SQLGuardError with context."""
        guard_sql(sql, dialect=self._dialect)
        try:
            self._run(f"EXPLAIN {sql}")
        except Exception as e:
            raise SQLGuardError(f"EXPLAIN failed: {e}") from e
        try:
            for stmt in _trial_statements(sql, self._dialect):
                self._run(stmt)
        except Exception as e:
            raise SQLGuardError(f"trial run failed: {e}") from e
