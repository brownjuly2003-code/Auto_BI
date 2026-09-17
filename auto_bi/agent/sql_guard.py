"""SQL guard (ARCHITECTURE §4): single SELECT-only statement, everything else rejected.

Belt-and-suspenders for generated SQL today, the mandatory gate for the raw_sql
escape hatch later. Plus live validation: EXPLAIN + LIMIT-ed trial run on the DWH.
"""

from typing import NamedTuple

import sqlglot
from sqlglot import expressions as exp
from sqlglot.optimizer.scope import Scope, ScopeType, build_scope

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


class AmbiguousRef(NamedTuple):
    """An unattributed column together with the sources its scope offered it.

    SQL alone does not say which of them the name came from, but the scope is a fact of the
    statement, not a guess about it — and a caller holding a model of those tables may find
    that only one of them declares the name. Handing over the scope lets that caller decide
    with what it knows; withholding it would force the same question to be answered blind.
    """

    name: str
    #: The column identifier was written inside double quotes; see `TableRef.quoted`.
    quoted: bool
    #: The tables the scope offered, one entry per *source* rather than per distinct table:
    #: a self-join offers the same table twice, and two sources are two places the name
    #: could have been read from however few tables they denote. Empty when the reference
    #: resolves against nothing at all — a qualification no enclosing scope declares has no
    #: candidates, only a mistake — and empty too when the scope has no source of its own.
    candidates: tuple[TableRef, ...]
    #: Every source in that scope was a table. When it was not, the missing candidate is a
    #: CTE or subquery whose columns no model of the physical marts can rule in or out.
    all_tables: bool
    #: The source the reference named (`z` in `z.category`), empty when it named none. With
    #: no candidates that is what separates a qualifier nothing declares from a scope that
    #: offered nothing — one is a mistake in the statement, the other a shape of it.
    qualifier: str = ""
    #: The name was written in a scope that can read its enclosing query (a scalar, `IN` or
    #: `EXISTS` subquery, a lateral written inside one of those, or a branch of a set
    #: operation written in one of them), so the candidates are the *local* sources only:
    #: SQL resolves such a name at the innermost level that has the column and outward from
    #: there, and the enclosing levels are candidates this slice does not enumerate. A
    #: lateral's own readable outer sources are its FROM siblings, already local candidates,
    #: so a root lateral is not marked. A derived table and a CTE body cannot read around
    #: them, even nested inside a subquery, and are not marked. A caller that finds the name
    #: declared on none of the local candidates has therefore learnt nothing about it.
    enclosed: bool = False


class ColumnReads(NamedTuple):
    """What a SELECT reads at column level, and what saying more would take a guess.

    `unattributed` and `starred` are the honest remainder, reported rather than resolved:
    a caller grading governance must be able to say the check was partial, because a
    silent skip and a clean result look identical to whoever reads the verdict.
    """

    attributed: frozenset[ColumnRef]
    #: Names SQL alone cannot pin to one table — unqualified with several of the sources its
    #: scope reads, unqualified in a scalar/`IN`/`EXISTS` subquery, a lateral written inside
    #: one of those, or a branch of a set operation written in one of those (the name may
    #: come from this level or the query around it), or qualified by a name no enclosing
    #: scope declares.
    unattributed: frozenset[str]
    #: The statement selects `*` somewhere, so the columns it reads depend on the physical
    #: table and cannot be known from the text.
    starred: bool
    #: The same remainder with its scope kept, one entry per unattributed reference. Names
    #: alone are what `unattributed` reports; this is what a caller needs to narrow them.
    ambiguous: frozenset[AmbiguousRef] = frozenset()


def extract_column_refs(sql: str, *, dialect: str = DIALECT) -> ColumnReads:
    """Columns a SELECT reads, attributed only where SQL itself leaves no choice.

    Attribution is scope-local and follows the language's own resolution rule: a qualified
    column resolves through the enclosing scopes, an unqualified one only when the sources
    its scope *reads* come to exactly one. Two things follow from taking that rule literally.

    A scope's sources are its own FROM/JOIN items — the CTEs it names included, the ones it
    does not excluded. `WITH x AS (…) SELECT category FROM dm.sales_daily` reads one source;
    `x` is a query nobody ran, and counting it would make a decidable name undecidable for
    no reason the statement gives.

    An unqualified name written where *this* scope can read its enclosing query is not
    decided by a single local source either. That is a fact of the scope's own shape — a
    scalar, `IN` or `EXISTS` subquery, a lateral written inside one of those, or a branch
    of a set operation written in one of those — not of an inherited flag: a derived table
    and a CTE body introduce a new range SQL does not look out of, even when they sit
    inside a subquery. A UNION branch shares the range of the set operation around it, so
    it is enclosed exactly when that set operation is. A LATERAL inherits the same way:
    what it reads beyond its own FROM are the FROM items written before it, already local
    candidates, so a root lateral is not enclosed and a lateral inside a subquery is.
    SQL resolves a correlated name at the innermost level whose sources have the column
    and only then outward, so `WHERE b.id = store_id` inside an `EXISTS` may be the outer
    query's `store_id` — the text alone does not say. One structural rule serves every
    engine: which server accepts a correlated reference is not something this function can
    know. Such a reference comes back in `ambiguous` marked `enclosed`, with its local
    candidates and no claim about the levels above.

    Nothing is inferred beyond that — a bare `category` in a two-table join could come from
    either, and a caller that guesses which has stopped describing the query. What the
    remainder's scope offered is reported (`ColumnReads.ambiguous`) so a caller holding a
    model of those tables can narrow it on evidence rather than on a coin toss. Columns of a
    CTE or subquery are not a table's columns and are simply not reported; projection aliases
    are not reads at all and sqlglot already excludes them.
    """
    root = _parse_one_select(sql, dialect=dialect)
    root_scope = build_scope(root)
    if root_scope is None:  # pragma: no mutate block - Select/Union always defines a scope
        raise SQLGuardError("SELECT does not define a query scope")
    scopes = list(root_scope.traverse())
    owners = {id(scope.expression): scope for scope in scopes}
    attributed: set[ColumnRef] = set()
    ambiguous: set[AmbiguousRef] = set()
    for scope in scopes:
        for column in scope.columns:
            # A scope lists the columns of its nested subqueries too, and resolving one of
            # those against *this* scope's sources would attribute it to a table it was
            # never written against. Each column is graded once, where it was written.
            if _owning_scope(column, owners) is not scope:
                continue
            identifier = column.this
            quoted = isinstance(identifier, exp.Identifier) and identifier.quoted
            source = _column_source(scope, column)
            if source is None:
                ambiguous.add(_ambiguous_ref(scope, column, quoted=quoted))
                continue
            if not isinstance(source, exp.Table):
                continue  # a CTE or subquery column: real, but not a table's
            attributed.add(ColumnRef(table=_table_ref(source), name=column.name, quoted=quoted))
    return ColumnReads(
        attributed=frozenset(attributed),
        unattributed=frozenset(ref.name for ref in ambiguous),
        starred=root.find(exp.Star) is not None,
        ambiguous=frozenset(ambiguous),
    )


def _ambiguous_ref(scope: Scope, column: exp.Column, *, quoted: bool) -> AmbiguousRef:
    """The unresolved column with whatever its scope offered — nothing where nothing did.

    A qualified name gets no candidates: it names a source, and no enclosing scope declares
    that name, so the tables standing beside it are not what it was written against. The
    sources are counted, not deduplicated: `dm.sales_daily AS a JOIN dm.sales_daily AS b`
    leaves a bare name as undecidable as any other two-source join does, and reporting one
    candidate there would invite a caller to attribute what the engine itself rejects. In a
    scalar/`IN`/`EXISTS` subquery, a lateral written inside one of those, or a set-operation
    branch written in one of those the candidates are complete only for this level, which
    `enclosed` says out loud rather than leaving the caller to assume otherwise.
    """
    sources = [] if column.table else _read_sources(scope)
    return AmbiguousRef(
        name=column.name,
        quoted=quoted,
        candidates=tuple(
            sorted(_table_ref(source) for source in sources if isinstance(source, exp.Table))
        ),
        all_tables=all(isinstance(source, exp.Table) for source in sources),
        qualifier=column.table,
        enclosed=not column.table and _reads_enclosing(scope),
    )


def _read_sources(scope: Scope) -> list[exp.Expression | Scope]:
    """The sources a scope reads: the tables, derived tables, laterals and CTEs its own
    FROM/JOIN clauses name.

    `Scope.sources` is wider than the question an unqualified name asks. sqlglot puts every
    CTE of a WITH clause there whether or not the FROM names it, and `Scope.branch` hands
    those entries down into every nested scope — so a CTE nobody reads, and a CTE read two
    levels up, both arrive as things this scope "offers". Neither is a place the engine could
    have read the column from. `Scope.references` lists exactly the FROM/JOIN items.

    Each FROM/JOIN item is identified from the reference node itself, never by looking the
    alias up in `Scope.sources`. That dict is keyed by a de-duplicated name while
    `references` repeats the raw name; the lookup then returns whichever entry won the
    short name. The shapes that collapse are the ones where `references` is
    `['sales_daily', 'sales_daily']` and `sources` has renamed the second entry
    `sales_daily_2`: `FROM sales_daily JOIN dm.sales_daily` (the CTE wins the short name
    and swallows the mart) and `FROM dm.sales_daily JOIN stg.sales_daily` (the lookup
    would merge two distinct marts into one candidate). An unaliased self-join collapses
    harmlessly because the lookup returns the same node twice. sqlglot's `sales_daily_2`
    renaming is private to the library; the node is what the statement listed, and two
    items stay two sources.

    A LATERAL body also reads the FROM items written before it. sqlglot hangs those
    siblings on the UDTF scope (`FROM a, LATERAL (SELECT …) AS t` is a UDTF wrapping an
    inner SUBQUERY whose `references` list only its own FROM); the body itself may sit
    several scopes below it — a set operation inserts a SUBQUERY and a UNION branch
    scope (one more UNION level per further branch), and a lateral nested in a lateral
    body sits under another UDTF. Walking ancestors and stopping at a derived table or
    CTE body is what keeps the siblings visible at every level that can actually see
    them: a derived table or CTE written inside a lateral is a wall, because it cannot
    read what is written around it.
    """
    read: list[exp.Expression | Scope] = []
    cte_sources = scope.cte_sources
    for _alias, node in scope.references:
        read.append(_referenced_source(node, cte_sources))
    node_scope = scope
    while node_scope is not None:
        read.extend(node_scope.lateral_sources.values())
        if node_scope.scope_type in (ScopeType.DERIVED_TABLE, ScopeType.CTE):
            break
        node_scope = node_scope.parent
    return read


def _referenced_source(
    node: exp.Expression, cte_sources: dict[str, exp.Expression | Scope]
) -> exp.Expression | Scope:
    """The source a FROM/JOIN item denotes, decided from the node rather than from a name.

    A derived table, UDTF or subquery is not a table. An `exp.Table` whose unqualified
    name is a CTE visible here (`Scope.cte_sources`, which already merges inherited names)
    and which carries no catalog/db qualification is that CTE — `FROM sales_daily` beside
    a CTE of that name is the CTE, `FROM dm.sales_daily` is still the mart. Anything else
    is the table the node names. Returning the CTE Scope (not the Table) is what keeps
    `isinstance(..., exp.Table)` from treating a query as a physical mart.
    """
    if not isinstance(node, exp.Table):
        return node
    if not node.db and not node.catalog and node.name in cte_sources:
        return cte_sources[node.name]
    return node


def _reads_enclosing(scope: Scope) -> bool:
    """Whether this scope can see the query around it.

    sqlglot's `can_be_correlated` is inherited by every nested scope (`Scope.branch` ORs
    it down), including a derived table and a CTE body that cannot actually read outward.
    Decide from this scope's own shape: a scalar/`IN`/`EXISTS` subquery (`SUBQUERY`) can;
    a derived table and a CTE body cannot, wherever they sit.

    A set-operation branch (`ScopeType.UNION`) shares the range of the set operation it is
    written in: the engine still resolves a bare name in that branch against the same
    enclosing query the subquery around the UNION can see. Recursing through UNION only —
    not through DERIVED_TABLE or CTE — is what keeps a UNION at the root, and a UNION
    inside a FROM-subquery or CTE body, un-enclosed. A UNION of UNIONs walks up until it
    hits a non-UNION parent.

    A LATERAL or table-function body (`ScopeType.UDTF`) inherits enclosure from the level
    that contains it, the same way a UNION branch does. What it reads beyond its own FROM
    are the FROM items written before it, and `_read_sources` already offers exactly those
    as local candidates (`lateral_sources`, including every enclosing UDTF up to a derived
    table or CTE body) — so at a level that cannot itself read outward the candidate set
    is complete and one source decides, while a lateral inside a scalar/`IN`/`EXISTS`
    subquery stays enclosed because that subquery's own enclosing query is still readable.
    sqlglot 30.14.0 nests a LATERAL as a UDTF wrapping an inner SUBQUERY: that inner SELECT
    is decided through its UDTF parent, before the plain SUBQUERY test, or a root lateral's
    body would be marked enclosed just for being a SUBQUERY.
    """
    if scope.scope_type is ScopeType.UNION:
        return scope.parent is not None and _reads_enclosing(scope.parent)
    if scope.scope_type is ScopeType.UDTF:
        return scope.parent is not None and _reads_enclosing(scope.parent)
    if scope.parent is not None and scope.parent.scope_type is ScopeType.UDTF:
        return _reads_enclosing(scope.parent)
    return scope.scope_type is ScopeType.SUBQUERY


def _owning_scope(column: exp.Column, owners: dict[int, Scope]) -> Scope | None:
    """The innermost scope the column was written in; `None` only if it has none."""
    node: exp.Expr | None = column.parent
    while node is not None:
        scope = owners.get(id(node))
        if scope is not None:
            return scope
        node = node.parent
    return None


def _column_source(scope: Scope, column: exp.Column) -> exp.Expression | Scope | None:
    """The source a column resolves to, or ``None`` when SQL does not decide it."""
    if not column.table:
        # A scope decides a bare name only through the sources it reads (`_read_sources`),
        # and only where nothing above it could have supplied the name instead: inside a
        # scalar/IN/EXISTS subquery, a lateral written inside one of those, or a
        # set-operation branch written in one of those the engine looks at this level first
        # and at the enclosing query next, so one source here is a candidate rather than an
        # answer. That is this scope's shape, not an inherited flag: a derived table or CTE
        # body nested inside a subquery still cannot read around it, and a root lateral's
        # siblings are already local candidates.
        if _reads_enclosing(scope):
            return None
        sources = _read_sources(scope)
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
