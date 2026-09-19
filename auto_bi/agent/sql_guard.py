"""SQL guard (ARCHITECTURE §4): single SELECT-only statement, everything else rejected.

Belt-and-suspenders for generated SQL today, the mandatory gate for the raw_sql
escape hatch later. Plus live validation: EXPLAIN + LIMIT-ed trial run on the DWH.
"""

from typing import NamedTuple

import sqlglot
from sqlglot import expressions as exp
from sqlglot.optimizer.scope import Scope, ScopeType, _traverse_scope, build_scope

from auto_bi.agent.query_plan import PlanCache
from auto_bi.introspect.base import RunQuery

DIALECT = "clickhouse"
TRIAL_LIMIT = 10
TRIAL_TIMEOUT_S = 30

# A nested-query WITH chain costs a few Python frames per alias (read-through,
# nested-query grade, column loop). 64 keeps that chain clear of RecursionError
# under pytest's default limit of 1000, and is deep enough that valid ClickHouse
# SQL is unchanged under the cap.
CLICKHOUSE_ALIAS_READ_THROUGH_DEPTH = 64
CLICKHOUSE_ALIAS_READ_THROUGH_BUDGET = 10_000

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
    if dialect == "clickhouse":
        _rewrite_clickhouse_in_tables(root)
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
    CTE or subquery are not a table's columns and are simply not reported.

    On ClickHouse a bare name that is an alias visible at the place it is written is not a
    column read — neither attributed nor unattributed — except inside that alias's own
    defining expression, where the name is the column (`sum(revenue) AS revenue`). Inside
    the definition of SELECT-list alias X, a WITH expression read through from it included,
    X is the column. A SELECT-list alias is visible in the query level that defines it
    (other SELECT expressions, WHERE, PREWHERE, GROUP BY, HAVING, ORDER BY, LIMIT BY) and
    not in a subquery. A `WITH <expr> AS alias` (and `WITH (SELECT …) AS alias`) is visible
    in the query that declares it and in every subquery and UNION branch below it. A visible
    alias wins over a table column of the same name; identifiers are compared as written.
    When a scope references an expression-form `WITH <expr> AS a` — directly, or through
    another alias whose definition references it — every column written in `<expr>` is
    graded as if written in that referencing scope. A query nested in `<expr>` is graded as
    the same subquery written at the reference: its own FROM, with bare names enclosed
    toward the referencing scope. sqlglot 30.14.0 builds no scope for those bodies
    (`scope.py:708` "Cannot traverse scope"), so the read-through builds one with
    `Scope.branch` and `_traverse_scope` — the same helpers `_traverse_subqueries` uses for
    an inline subquery. A SELECT-list alias that can reach itself through SELECT-list or
    WITH definitions is a column wherever it is referenced, a WITH body in the read-through
    included. A WITH reference that closes a cycle on the read-through path is a column, and
    the definitions on the way are still read. Read-through of a WITH alias is bounded by a
    depth cap and an expansion budget; a reference past them is reported unattributed under
    its own name (no candidates, not enclosed), so the gate names a source it did not read
    rather than skipping it. A cut expansion is not memoised: a later shallower reference
    expands it again. An alias nothing references reads nothing. A SELECT-list alias needs
    no read-through: its definition is already graded where it is written. A
    `WITH (SELECT …) AS a` keeps its own scope and FROM; only the reference `a` is the
    alias. On ClickHouse, `x [GLOBAL] [NOT] IN <name>` where `<name>` is the Column in
    `In.args["field"]` and not a visible alias is rewritten to `x IN (SELECT * FROM
    <name>)` before scoping, so it grades as that subquery: the same TableRef, the
    same star outcome. A CTE of that name is the CTE, as it is in FROM. Literal lists
    and subquery forms are unchanged. The PostgreSQL family is unchanged: there a
    WITH defines CTEs only and WHERE cannot read a SELECT alias, so a bare name is a
    column, and `IN <name>` stays the column sqlglot parsed.
    """
    root = _parse_one_select(sql, dialect=dialect)
    if dialect == "clickhouse":
        _rewrite_clickhouse_in_tables(root)
    root_scope = build_scope(root)
    if root_scope is None:  # pragma: no mutate block - Select/Union always defines a scope
        raise SQLGuardError("SELECT does not define a query scope")
    scopes = list(root_scope.traverse())
    owners = {id(scope.expression): scope for scope in scopes}
    aliases = _clickhouse_aliases(root) if dialect == "clickhouse" else None
    attributed: set[ColumnRef] = set()
    ambiguous: set[AmbiguousRef] = set()
    _grade_columns(scopes, owners, aliases, attributed, ambiguous, _ReadThroughState())
    return ColumnReads(
        attributed=frozenset(attributed),
        unattributed=frozenset(ref.name for ref in ambiguous),
        starred=root.find(exp.Star) is not None,
        ambiguous=frozenset(ambiguous),
    )


class _ClickHouseAlias(NamedTuple):
    """One ClickHouse SELECT-list or scalar WITH alias, as written."""

    name: str
    definition: exp.Expression
    #: Expression-form `WITH <expr> AS name` is read through; a SELECT-list alias and a
    #: `WITH (SELECT …) AS name` are not — the former is already graded where written,
    #: the latter keeps its own scope and FROM.
    read_through: bool


class _ClickHouseAliasIndex(NamedTuple):
    """Aliases declared on each Select/Union, keyed by `id(expression)`."""

    select: dict[int, dict[str, _ClickHouseAlias]]
    with_: dict[int, dict[str, _ClickHouseAlias]]
    #: SELECT-list aliases whose SCC has more than one member, filled lazily
    #: per `id(scope)` — a branch scope's WITH visibility depends on its parent.
    select_on_cycle: dict[int, frozenset[str]]
    #: Scope objects held so `select_on_cycle` / `with_reaches` keys stay unique.
    held_scopes: list[Scope]
    #: SELECT-list alias names each expression-form WITH reaches at a scope,
    #: keyed by `(id(definition), id(scope))`.
    with_reaches: dict[tuple[int, int], frozenset[str]]


class _ReadThroughState:
    """Mutable WITH read-through state for one `extract_column_refs` call.

    `path` is the aliases being expanded right now, across scopes, keyed by
    declaration identity (`id(definition)`), never by name — an inner WITH
    alias that shadows an outer name is a different declaration. `done` holds
    the `(alias, scope, owner)` triples already expanded in full; the Scope
    objects themselves are kept as values so a branch scope from
    `_grade_nested_query` cannot be garbage-collected and reuse an `id()` that
    would hide a later read. `cyclic` is keyed by `(id(definition), id(scope))`
    and holds the Scope so a cycle that exists only in an inner scope does not
    turn an outer reference of the same declaration into a column. A truncated
    expansion is not recorded: a later shallower reference expands it again.
    """

    __slots__ = ("cyclic", "done", "expansions", "on_path", "path")

    def __init__(self) -> None:
        self.path: list[_ClickHouseAlias] = []
        self.on_path: set[int] = set()
        self.done: dict[tuple[int, int, int], Scope] = {}
        self.cyclic: dict[tuple[int, int], Scope] = {}
        self.expansions = 0


_QUERY_BODY = (exp.Select, exp.Union, exp.Subquery)


def _clickhouse_aliases(root: exp.Expression) -> _ClickHouseAliasIndex:
    """SELECT-list and scalar WITH aliases declared on each query node."""
    select_map: dict[int, dict[str, _ClickHouseAlias]] = {}
    with_map: dict[int, dict[str, _ClickHouseAlias]] = {}
    seen: set[int] = set()
    nodes: list[exp.Expression] = []
    if isinstance(root, exp.Query):
        nodes.append(root)
        seen.add(id(root))
    for node in root.find_all(exp.Query):
        nid = id(node)
        if nid in seen:
            continue
        seen.add(nid)
        nodes.append(node)
    for node in nodes:
        nid = id(node)
        select_aliases: dict[str, _ClickHouseAlias] = {}
        if isinstance(node, exp.Select):
            for projected in node.expressions:
                if not isinstance(projected, exp.Alias):
                    continue
                name = projected.alias
                if not name:
                    continue
                select_aliases[name] = _ClickHouseAlias(
                    name=name, definition=projected.this, read_through=False
                )
        select_map[nid] = select_aliases
        with_aliases: dict[str, _ClickHouseAlias] = {}
        clause = node.args.get("with_")
        if isinstance(clause, exp.With):
            for cte in clause.expressions:
                if not isinstance(cte, exp.CTE) or not cte.args.get("scalar"):
                    continue
                name = cte.alias
                body = cte.this
                if not name or body is None:
                    continue
                with_aliases[name] = _ClickHouseAlias(
                    name=name,
                    definition=body,
                    read_through=not isinstance(body, _QUERY_BODY),
                )
        with_map[nid] = with_aliases
    return _ClickHouseAliasIndex(
        select=select_map,
        with_=with_map,
        select_on_cycle={},
        held_scopes=[],
        with_reaches={},
    )


def _inside(node: exp.Expression, ancestor: exp.Expression) -> bool:
    """Whether *node* is *ancestor* or sits under it."""
    current: exp.Expression | None = node
    while current is not None:
        if current is ancestor:
            return True
        current = current.parent
    return False


def _clickhouse_alias_ref(
    column: exp.Column,
    scope: Scope,
    aliases: _ClickHouseAliasIndex,
    *,
    ignore_cycle: bool = False,
) -> _ClickHouseAlias | None:
    """The visible alias this bare name is, or ``None`` when it is a column.

    A SELECT-list alias of this query level wins over a WITH alias of the same name,
    except inside that SELECT alias's defining expression — there the name is the
    column, or a WITH alias still visible. A WITH alias is visible in this query and
    every subquery and UNION branch below the query that declared it. Inside the
    expression that defines alias X, the name X is the column, not that alias.
    A SELECT-list alias that would close a cycle through SELECT-list or WITH
    definitions is a column: ClickHouse rejects the statement, and reporting that
    it reads nothing would hide the names. Graph construction passes
    ``ignore_cycle=True`` so the on-cycle set is not consulted while it is built.
    """
    if column.table:
        return None
    name = column.name
    if not name:
        return None
    select_aliases = aliases.select.get(id(scope.expression), {})
    select_alias = select_aliases.get(name)
    if select_alias is not None and not _inside(column, select_alias.definition):
        if not ignore_cycle and name in _select_aliases_on_cycle(scope, aliases):
            return None
        return select_alias
    current: Scope | None = scope
    while current is not None:
        with_alias = aliases.with_.get(id(current.expression), {}).get(name)
        if with_alias is not None and not _inside(column, with_alias.definition):
            return with_alias
        current = current.parent
    return None


def _column_as_table(column: exp.Column) -> exp.Table:
    """Map sqlglot's Column encoding of a table name to a Table.

    `IN db.table` is Column(this=table, table=db); a three-part name uses db as
    the catalog slot. Quoted identifiers travel with the Identifier nodes.
    """
    idents = [
        part
        for part in (
            column.args.get("catalog"),
            column.args.get("db"),
            column.args.get("table"),
            column.args.get("this"),
        )
        if isinstance(part, exp.Identifier)
    ]
    this = idents[-1].copy() if idents else None
    db = idents[-2].copy() if len(idents) >= 2 else None
    catalog = idents[-3].copy() if len(idents) >= 3 else None
    return exp.Table(this=this, db=db, catalog=catalog)


def _star_subquery_from(table: exp.Table) -> exp.Subquery:
    """`(SELECT * FROM <table>)` — ClickHouse's reading of `IN <table>`."""
    select = exp.Select().select(exp.Star()).from_(table, copy=False)
    return exp.Subquery(this=select)


def clickhouse_in_table_fields(root: exp.Expression) -> tuple[exp.Column, ...]:
    """Columns that ClickHouse reads as `IN (SELECT * FROM <name>)`, not as aliases.

    `<name>` is the Column in `In.args["field"]` (bare or qualified). A visible
    alias — `_clickhouse_alias_ref` in that column's scope — stays an alias, so a
    cyclic SELECT alias counts as a column there and becomes a table. A column
    sqlglot did not put in any query scope is left alone. A statement with no
    Column on the right of any IN returns at once, without building scopes or the
    alias index: most statements have none, and the grading builds both anyway.
    """
    fields = [
        field
        for field in (node.args.get("field") for node in root.find_all(exp.In))
        if isinstance(field, exp.Column)
    ]
    if not fields:
        return ()
    aliases = _clickhouse_aliases(root)
    root_scope = build_scope(root)
    if root_scope is None:  # pragma: no mutate block - Select/Union always defines a scope
        return ()
    owners = {id(scope.expression): scope for scope in root_scope.traverse()}
    found: list[exp.Column] = []
    for field in fields:
        scope = _owning_scope(field, owners)
        if scope is None:
            continue
        if _clickhouse_alias_ref(field, scope, aliases) is not None:
            continue
        found.append(field)
    return tuple(found)


def _rewrite_clickhouse_in_tables(root: exp.Expression) -> None:
    """Replace `x IN <table>` with `x IN (SELECT * FROM <table>)` when not an alias."""
    for column in clickhouse_in_table_fields(root):
        node = column.parent
        if not isinstance(node, exp.In):
            continue
        table = _column_as_table(column)
        node.set("field", None)
        node.set("query", _star_subquery_from(table))


def _column_owner(
    column: exp.Column, scope: Scope, aliases: _ClickHouseAliasIndex
) -> _ClickHouseAlias | None:
    """The SELECT-list alias of *scope*'s level whose definition contains *column*.

    Walks the column's ancestors, not the SELECT list: a generated chain of
    thousands of aliases would otherwise be quadratic in `_grade_columns`.
    """
    select_aliases = aliases.select.get(id(scope.expression), {})
    if not select_aliases:
        return None
    current: exp.Expression | None = column
    while current is not None and current is not scope.expression:
        parent = current.parent
        if parent is None:
            break
        if isinstance(parent, exp.Alias) and parent.parent is scope.expression:
            return select_aliases.get(parent.alias)
        current = parent
    return None


def _owner_key(owner: _ClickHouseAlias | None) -> int:
    return id(owner.definition) if owner is not None else 0


def _loose_columns(node: exp.Expression) -> list[exp.Column]:
    """Columns written in *node* that are not inside a nested query.

    Nested SELECT/UNION/subquery bodies keep their own FROM. sqlglot 30.14.0
    builds no scope for a query nested in an expression-form WITH body, so the
    read-through grades those bodies as a subquery of the referencing scope
    rather than pruning them and leaving them ungraded.
    """

    def prune(child: exp.Expression) -> bool:
        return child is not node and isinstance(child, _QUERY_BODY)

    return [child for child in node.walk(prune=prune) if isinstance(child, exp.Column)]


def _attribute_column(
    scope: Scope,
    column: exp.Column,
    attributed: set[ColumnRef],
    ambiguous: set[AmbiguousRef],
) -> None:
    """Grade one column against *scope*'s sources."""
    identifier = column.this
    quoted = isinstance(identifier, exp.Identifier) and identifier.quoted
    source = _column_source(scope, column)
    if source is None:
        ambiguous.add(_ambiguous_ref(scope, column, quoted=quoted))
        return
    if not isinstance(source, exp.Table):
        return  # a CTE or subquery column: real, but not a table's
    attributed.add(ColumnRef(table=_table_ref(source), name=column.name, quoted=quoted))


def _grade_columns(
    scopes: list[Scope],
    owners: dict[int, Scope],
    aliases: _ClickHouseAliasIndex | None,
    attributed: set[ColumnRef],
    ambiguous: set[AmbiguousRef],
    state: _ReadThroughState,
) -> bool:
    """Grade each column once, in the innermost scope that contains it.

    Returns whether any WITH expansion in this walk hit the depth cap or budget.
    """
    truncated = False
    for scope in scopes:
        for column in scope.columns:
            # A scope lists the columns of its nested subqueries too, and resolving one of
            # those against *this* scope's sources would attribute it to a table it was
            # never written against. Each column is graded once, where it was written.
            if _owning_scope(column, owners) is not scope:
                continue
            if aliases is not None:
                alias = _clickhouse_alias_ref(column, scope, aliases)
                if alias is not None:
                    owner = _column_owner(column, scope, aliases) if alias.read_through else None
                    if _read_through_alias(
                        alias, column, scope, aliases, attributed, ambiguous, state, owner
                    ):
                        truncated = True
                    continue
            _attribute_column(scope, column, attributed, ambiguous)
    return truncated


def _select_aliases_reached_from_with(
    start: _ClickHouseAlias, scope: Scope, aliases: _ClickHouseAliasIndex
) -> frozenset[str]:
    """SELECT-list aliases of *scope*'s level reached through *start*'s WITH body.

    Nested queries are not followed: SELECT aliases are not visible there. Names
    resolve with the raw rule that does not consult the on-cycle set, so the
    SELECT graph does not depend on itself. Memoised per (declaration, scope).
    """
    key = (id(start.definition), id(scope))
    cached = aliases.with_reaches.get(key)
    if cached is not None:
        return cached
    aliases.held_scopes.append(scope)
    select_map = aliases.select.get(id(scope.expression), {})
    reached: set[str] = set()
    seen: set[int] = set()
    stack: list[_ClickHouseAlias] = [start]
    while stack:
        alias = stack.pop()
        ident = id(alias.definition)
        if ident in seen:
            continue
        memo = aliases.with_reaches.get((ident, id(scope)))
        if memo is not None:
            reached.update(memo)
            continue
        seen.add(ident)
        for column in _loose_columns(alias.definition):
            resolved = _clickhouse_alias_ref(column, scope, aliases, ignore_cycle=True)
            if resolved is None:
                continue
            if resolved.read_through:
                stack.append(resolved)
                continue
            if resolved.name in select_map:
                reached.add(resolved.name)
    result = frozenset(reached)
    aliases.with_reaches[key] = result
    return result


def _select_alias_graph(scope: Scope, aliases: _ClickHouseAliasIndex) -> dict[str, list[str]]:
    """a → b when a's definition, following WITH bodies at *scope*, reaches SELECT alias b.

    Reaching a itself is the owner case (a column), not a self-edge. Nested queries
    are not followed. Names resolve without the on-cycle set.
    """
    select_aliases = aliases.select.get(id(scope.expression), {})
    graph: dict[str, list[str]] = {name: [] for name in select_aliases}
    for name, alias in select_aliases.items():
        seen: set[str] = set()
        for column in _loose_columns(alias.definition):
            resolved = _clickhouse_alias_ref(column, scope, aliases, ignore_cycle=True)
            if resolved is None:
                continue
            if resolved.read_through:
                others = _select_aliases_reached_from_with(resolved, scope, aliases)
            elif resolved.name in select_aliases:
                others = frozenset({resolved.name})
            else:
                continue
            for other in others:
                if other == name or other not in select_aliases or other in seen:
                    continue
                graph[name].append(other)
                seen.add(other)
    return graph


def _strongly_connected_multi(graph: dict[str, list[str]]) -> frozenset[str]:
    """Vertices whose strongly connected component has more than one member.

    Iterative Tarjan, linear in aliases and edges, so a generated chain that
    names the previous alias twice cannot hang the gate, and a long forward
    chain cannot raise RecursionError.
    """
    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_cycle: set[str] = set()
    work: list[tuple[str, int]] = []

    def enter(vertex: str) -> None:
        nonlocal index
        indices[vertex] = index
        lowlink[vertex] = index
        index += 1
        stack.append(vertex)
        on_stack.add(vertex)
        work.append((vertex, 0))

    for start in graph:
        if start in indices:
            continue
        enter(start)
        while work:
            vertex, i = work[-1]
            neighbors = graph[vertex]
            if i < len(neighbors):
                work[-1] = (vertex, i + 1)
                other = neighbors[i]
                if other not in indices:
                    enter(other)
                elif other in on_stack:
                    lowlink[vertex] = min(lowlink[vertex], indices[other])
                continue
            work.pop()
            if work:
                parent, _ = work[-1]
                lowlink[parent] = min(lowlink[parent], lowlink[vertex])
            if lowlink[vertex] != indices[vertex]:
                continue
            scc: list[str] = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                scc.append(member)
                if member == vertex:
                    break
            if len(scc) > 1:
                on_cycle.update(scc)
    return frozenset(on_cycle)


def _select_aliases_on_cycle(scope: Scope, aliases: _ClickHouseAliasIndex) -> frozenset[str]:
    """SELECT-list aliases that can reach themselves through SELECT or WITH definitions.

    The name inside an alias's own definition is the column, so the graph has no
    self-edge from that case: an alias is on a cycle iff its strongly connected
    component has more than one member. Cached on the Scope object held alive —
    a branch scope's WITH visibility depends on its parent.
    """
    key = id(scope)
    cached = aliases.select_on_cycle.get(key)
    if cached is not None:
        return cached
    aliases.held_scopes.append(scope)
    select_aliases = aliases.select.get(id(scope.expression), {})
    if len(select_aliases) < 2:
        aliases.select_on_cycle[key] = frozenset()
        return frozenset()
    result = _strongly_connected_multi(_select_alias_graph(scope, aliases))
    aliases.select_on_cycle[key] = result
    return result


def _outermost_nested_queries(node: exp.Expression) -> list[exp.Expression]:
    """Outermost Select/Union/Subquery bodies nested in *node*."""
    found: list[exp.Expression] = []

    def prune(child: exp.Expression) -> bool:
        if child is not node and isinstance(child, _QUERY_BODY):
            found.append(child)
            return True
        return False

    for _ in node.walk(prune=prune):
        pass
    return found


def _grade_nested_query(
    query: exp.Expression,
    parent: Scope,
    aliases: _ClickHouseAliasIndex,
    attributed: set[ColumnRef],
    ambiguous: set[AmbiguousRef],
    state: _ReadThroughState,
) -> bool:
    """Grade a query nested in a read-through WITH as a subquery of *parent*.

    sqlglot 30.14.0 does not build a scope for a query nested in an expression-form
    WITH body (`scope.py:708` "Cannot traverse scope"). `_traverse_scope` and
    `Scope.branch` are the same helpers `_traverse_subqueries` uses for an inline
    subquery, so the nested query gets its own FROM, its parent is the referencing
    scope, and `_reads_enclosing` marks its bare names enclosed. The outer SELECT
    alias that owned the reference is not visible here; the owner is computed
    again from this branch's own SELECT list.
    """
    body = query.this if isinstance(query, exp.Subquery) else query
    nested_scopes = list(_traverse_scope(parent.branch(body, scope_type=ScopeType.SUBQUERY)))
    owners = {id(scope.expression): scope for scope in nested_scopes}
    return _grade_columns(nested_scopes, owners, aliases, attributed, ambiguous, state)


def _unread_alias_ref(column: exp.Column) -> AmbiguousRef:
    """A WITH alias whose definition this call did not read (depth cap or budget)."""
    identifier = column.this
    quoted = isinstance(identifier, exp.Identifier) and identifier.quoted
    return AmbiguousRef(
        name=column.name,
        quoted=quoted,
        candidates=(),
        all_tables=False,
        enclosed=False,
    )


def _mark_cyclic(state: _ReadThroughState, alias: _ClickHouseAlias, scope: Scope) -> None:
    """Mark *alias* and every alias after it on the expansion path as cyclic in *scope*."""
    ident = id(alias.definition)
    started = False
    for item in state.path:
        item_id = id(item.definition)
        if item_id == ident:
            started = True
        if started:
            state.cyclic[(item_id, id(scope))] = scope


def _read_through_alias(
    alias: _ClickHouseAlias,
    column: exp.Column,
    scope: Scope,
    aliases: _ClickHouseAliasIndex,
    attributed: set[ColumnRef],
    ambiguous: set[AmbiguousRef],
    state: _ReadThroughState,
    owner: _ClickHouseAlias | None,
) -> bool:
    """Grade columns of a referenced expression-form WITH as if written in *scope*.

    Loose columns are graded in *scope*. A query nested in the definition is graded
    as the same subquery written at the reference — its own FROM, enclosed toward
    *scope* — because sqlglot builds no scope for those bodies. Expansion is keyed
    by declaration identity and memoised per (alias, scope, owner); a cycle in
    *scope* is a column there, keyed by (declaration, scope), so an inner-only
    cycle does not hide an outer read of the same alias; a reference past the
    depth cap or expansion budget is unattributed under its own name. A loose
    column that resolves to *owner* (the SELECT-list alias whose definition this
    expansion is part of) is the column, not the alias. Returns True when this
    expansion or a subtree hit the cap or budget; such an expansion is not
    recorded in ``done``.
    """
    if not alias.read_through:
        return False
    ident = id(alias.definition)
    if ident in state.on_path:
        _mark_cyclic(state, alias, scope)
        _attribute_column(scope, column, attributed, ambiguous)
        return False
    if (ident, id(scope)) in state.cyclic:
        _attribute_column(scope, column, attributed, ambiguous)
        return False
    done_key = (ident, id(scope), _owner_key(owner))
    if done_key in state.done:
        return False
    if (
        len(state.path) >= CLICKHOUSE_ALIAS_READ_THROUGH_DEPTH
        or state.expansions >= CLICKHOUSE_ALIAS_READ_THROUGH_BUDGET
    ):
        ambiguous.add(_unread_alias_ref(column))
        return True
    state.path.append(alias)
    state.on_path.add(ident)
    state.expansions += 1
    truncated = False
    try:
        for nested_column in _loose_columns(alias.definition):
            nested = _clickhouse_alias_ref(nested_column, scope, aliases)
            if nested is not None:
                if owner is not None and nested is owner:
                    _attribute_column(scope, nested_column, attributed, ambiguous)
                    continue
                if _read_through_alias(
                    nested,
                    nested_column,
                    scope,
                    aliases,
                    attributed,
                    ambiguous,
                    state,
                    owner,
                ):
                    truncated = True
                continue
            _attribute_column(scope, nested_column, attributed, ambiguous)
        for nested_query in _outermost_nested_queries(alias.definition):
            if _grade_nested_query(nested_query, scope, aliases, attributed, ambiguous, state):
                truncated = True
    finally:
        state.path.pop()
        state.on_path.discard(ident)
    if not truncated:
        state.done[done_key] = scope
    return truncated


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
