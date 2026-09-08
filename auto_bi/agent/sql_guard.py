"""SQL guard (ARCHITECTURE §4): single SELECT-only statement, everything else rejected.

Belt-and-suspenders for generated SQL today, the mandatory gate for the raw_sql
escape hatch later. Plus live validation: EXPLAIN + LIMIT-ed trial run on the DWH.
"""

from typing import NamedTuple

import sqlglot
from sqlglot import expressions as exp
from sqlglot.optimizer.scope import Scope, build_scope

from auto_bi.agent.query_plan import PlanCache
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
    cte_names = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}
    for node in root.walk():
        if isinstance(node, forbidden):
            raise SQLGuardError(f"forbidden construct in SQL: {type(node).__name__}")
        # Table-valued remote sources: `FROM url(...)` / `FROM s3(...)` etc.
        if isinstance(node, exp.Anonymous):
            name = str(node.this).lower()
            if name in _FORBIDDEN_TABLE_FUNCS:
                raise SQLGuardError(f"forbidden table function in SQL: {name}()")
        if isinstance(node, exp.Func):
            name = node.sql_name().lower()
            if name in _FORBIDDEN_TABLE_FUNCS:
                raise SQLGuardError(f"forbidden table function in SQL: {name}()")
        # sqlglot(clickhouse) parses some table functions as plain Tables (e.g.
        # dictionary('x') becomes Table with alias columns), so also flag any
        # UNQUALIFIED table whose name is denylisted. A real table of that name is
        # still reachable schema-qualified (dm.dictionary); CTE alias refs are fine.
        if isinstance(node, exp.Table) and not node.args.get("db"):
            name = node.name.lower()
            if name in _FORBIDDEN_TABLE_FUNCS and name not in cte_names:
                raise SQLGuardError(f"forbidden table function in SQL: {name}()")


class TableRef(NamedTuple):
    """One table a SELECT reads, as written."""

    #: Fully qualified when the SQL qualifies it (`dm.sales_daily`); bare names stay bare.
    name: str
    #: Any part of the name was written inside double quotes. Only an unquoted identifier
    #: is case-folded by the engines that fold, so a caller comparing this name against a
    #: declared one needs to know which it is (`verification/repository.py`).
    quoted: bool


def extract_table_refs(sql: str, *, dialect: str = DIALECT) -> frozenset[TableRef]:
    """Physical tables referenced by a SELECT, with their quoting (CTE aliases excluded)."""
    root = _parse_one_select(sql, dialect=dialect)
    cte_names = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}
    tables: set[TableRef] = set()
    for table in root.find_all(exp.Table):
        name = table.name
        if not name:  # pragma: no mutate block - sqlglot never emits an unnamed Table
            continue
        # CTE self-reference: bare name matching a WITH alias, no db/catalog.
        if name.lower() in cte_names and not table.db and not table.catalog:
            continue
        tables.add(_table_ref(table))
    return frozenset(tables)


def _table_ref(table: exp.Table) -> TableRef:
    identifiers = [
        part
        for part in (table.args.get("catalog"), table.args.get("db"), table.this)
        if isinstance(part, exp.Identifier) and part.name
    ]
    return TableRef(
        name=".".join(part.name for part in identifiers),
        quoted=any(part.quoted for part in identifiers),
    )


def extract_table_names(sql: str, *, dialect: str = DIALECT) -> frozenset[str]:
    """Physical table names referenced by a SELECT (CTE aliases excluded).

    Used by schema-RBAC for the raw_sql hatch: `query.table` is only a dataset label
    and must not be the sole RBAC surface. Returns fully-qualified names when the
    SQL qualifies them (`dm.sales_daily`); bare names stay bare (schema_of then
    treats the whole token as the schema segment — still fail-closed for RBAC if
    the bare name is not an allowed schema).
    """
    return frozenset(ref.name for ref in extract_table_refs(sql, dialect=dialect))


class ColumnRef(NamedTuple):
    """One column a SELECT reads, attributed to the table the statement says it is from."""

    table: TableRef
    name: str
    #: The column identifier was written inside double quotes; see `TableRef.quoted`.
    quoted: bool


class ColumnReads(NamedTuple):
    """What a SELECT reads at column level, and what saying more would take a guess.

    `unattributed` and `starred` are the honest remainder, reported rather than resolved:
    a caller grading governance must be able to say the check was partial, because a
    silent skip and a clean result look identical to whoever reads the verdict.
    """

    attributed: frozenset[ColumnRef]
    #: Names SQL alone cannot pin to one table — unqualified with several sources in scope,
    #: or qualified by a name no enclosing scope declares.
    unattributed: frozenset[str]
    #: The statement selects `*` somewhere, so the columns it reads depend on the physical
    #: table and cannot be known from the text.
    starred: bool


def extract_column_refs(sql: str, *, dialect: str = DIALECT) -> ColumnReads:
    """Columns a SELECT reads, attributed only where SQL itself leaves no choice.

    Attribution is scope-local and follows the language's own resolution rule: a qualified
    column resolves through the enclosing scopes, an unqualified one only when its scope has
    exactly one source. Nothing is inferred beyond that — a bare `category` in a two-table
    join could come from either, and a caller that guesses which has stopped describing the
    query. Columns of a CTE or subquery are not a table's columns and are simply not
    reported; projection aliases are not reads at all and sqlglot already excludes them.
    """
    root = _parse_one_select(sql, dialect=dialect)
    scopes = list(build_scope(root).traverse())
    owners = {id(scope.expression): scope for scope in scopes}
    attributed: set[ColumnRef] = set()
    unattributed: set[str] = set()
    for scope in scopes:
        for column in scope.columns:
            # A scope lists the columns of its nested subqueries too, and resolving one of
            # those against *this* scope's sources would attribute it to a table it was
            # never written against. Each column is graded once, where it was written.
            if _owning_scope(column, owners) is not scope:
                continue
            source = _column_source(scope, column)
            if source is None:
                unattributed.add(column.name)
                continue
            if not isinstance(source, exp.Table):
                continue  # a CTE or subquery column: real, but not a table's
            identifier = column.this
            attributed.add(
                ColumnRef(
                    table=_table_ref(source),
                    name=column.name,
                    quoted=isinstance(identifier, exp.Identifier) and identifier.quoted,
                )
            )
    return ColumnReads(
        attributed=frozenset(attributed),
        unattributed=frozenset(unattributed),
        starred=root.find(exp.Star) is not None,
    )


def _owning_scope(column: exp.Column, owners: dict[int, Scope]) -> Scope | None:
    """The innermost scope the column was written in; `None` only if it has none."""
    node: exp.Expression | None = column.parent
    while node is not None:
        scope = owners.get(id(node))
        if scope is not None:
            return scope
        node = node.parent
    return None


def _column_source(scope: Scope, column: exp.Column) -> exp.Expression | Scope | None:
    """The source a column resolves to, or ``None`` when SQL does not decide it."""
    if not column.table:
        sources = list(scope.sources.values())
        return sources[0] if len(sources) == 1 else None
    # A correlated column is qualified by an alias its own scope never declares; the
    # language resolves that outward, and so must this.
    current: Scope | None = scope
    while current is not None:
        source = current.sources.get(column.table)
        if source is not None:
            return source
        current = current.parent
    return None


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

    def validate(self, sql: str, *, plans: PlanCache | None = None) -> None:
        """guard -> EXPLAIN -> LIMIT-ed execution; raises SQLGuardError with context.

        `plans` (D-2 §3) lets the guard skip its EXPLAIN when the Advisor already planned
        this EXACT statement cleanly earlier in the same build — the DWH has then already
        parsed, resolved and permission-checked it, so the second round trip measures
        nothing new. On a miss (a different statement, a failed plan, or no cache at all)
        the EXPLAIN runs as before. D-2 §5: when a cache is present the LIMIT-trial rows
        are recorded into it (parallel store, exact-SQL key) so OWN magnitude / CLI insights
        can reuse a complete trial; the trial itself stays unconditional (invariant 3).
        """
        guard_sql(sql, dialect=self._dialect)
        if plans is None or not plans.planned_ok(sql):
            try:
                self._run(f"EXPLAIN {sql}")
            except Exception as e:
                raise SQLGuardError(f"EXPLAIN failed: {e}") from e
        try:
            statements = _trial_statements(sql, self._dialect)
            for stmt in statements[:-1]:
                self._run(stmt)
            trial_rows = list(self._run(statements[-1]))
            if plans is not None:
                plans.record_trial(sql, trial_rows, trial_limit=TRIAL_LIMIT)
        except Exception as e:
            raise SQLGuardError(f"trial run failed: {e}") from e
