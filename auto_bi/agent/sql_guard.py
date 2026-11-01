"""SQL guard (ARCHITECTURE §4): single SELECT-only statement, everything else rejected.

Belt-and-suspenders for generated SQL today, the mandatory gate for the raw_sql
escape hatch later. Plus live validation: EXPLAIN + LIMIT-ed trial run on the DWH.
"""

from collections import defaultdict, deque
from collections.abc import Iterator
from dataclasses import dataclass
from typing import NamedTuple

import sqlglot
from sqlglot import TokenType
from sqlglot import expressions as exp
from sqlglot.optimizer.scope import Scope, ScopeType, _traverse_scope, build_scope

from auto_bi.agent.query_plan import PlanCache
from auto_bi.introspect.base import RunQuery

DIALECT = "clickhouse"
TRIAL_LIMIT = 10
TRIAL_TIMEOUT_S = 30

_POSTGRES_ROOT_QUERY_MODIFIERS = frozenset({"order", "limit", "offset"})

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
    """Parse exactly one SELECT or set-operation statement or raise SQLGuardError."""
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except (sqlglot.errors.ParseError, RecursionError) as e:
        if isinstance(e, RecursionError):
            raise SQLGuardError("SQL statement nests deeper than this parser reads") from e
        raise SQLGuardError(f"SQL does not parse: {e}") from e

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise SQLGuardError(f"expected exactly one statement, got {len(statements)}")

    root = statements[0]
    if dialect == "postgres" and isinstance(root, exp.Subquery):
        root = _normalize_postgres_root_subquery(root)
    if not isinstance(root, exp.Select | exp.SetOperation):
        raise SQLGuardError(f"only SELECT is allowed, got {type(root).__name__}")
    return root


def _normalize_postgres_root_subquery(root: exp.Subquery) -> exp.Expression:
    """Unwrap a bare PostgreSQL root query and preserve supported outer modifiers.

    PostgreSQL parses ``(SELECT ...) ORDER BY ...`` as a ``Subquery`` whose body is
    the query and whose modifier is attached to the wrapper.  The guard and scope
    grader operate on the query body, so move the small set of modifiers they can
    grade there.  A named wrapper, an unsupported modifier, or a duplicate modifier
    stays wrapped and is rejected by the SELECT-only check instead of being silently
    dropped.
    """
    wrappers: list[exp.Subquery] = []
    target: exp.Expression = root
    while isinstance(target, exp.Subquery):
        wrappers.append(target)
        target = target.this
    if not isinstance(target, (exp.Select, exp.SetOperation)):
        return root

    modifiers: dict[str, exp.Expression | list[exp.Expression]] = {}
    for wrapper in reversed(wrappers):
        for name, value in wrapper.args.items():
            if name == "this" or value is None:
                continue
            if name not in _POSTGRES_ROOT_QUERY_MODIFIERS or name in modifiers:
                return root
            modifiers[name] = value

    for name, value in modifiers.items():
        if target.args.get(name) is not None:
            return root
        target.set(name, value)
    return target


def guard_sql(sql: str, *, dialect: str = DIALECT) -> None:
    """Raise unless sql is one read-only SELECT or set operation (CTEs allowed)."""
    root = _parse_one_select(sql, dialect=dialect)

    forbidden = (
        exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter,
        exp.TruncateTable, exp.Grant, exp.Command, exp.Set, exp.Into,
    )  # fmt: skip
    cte_query_ids = _cte_declaring_query_ids(root, dialect=dialect)
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
            name = node.name
            folded_name = name.lower()
            if folded_name in _FORBIDDEN_TABLE_FUNCS and not _is_cte_ref(
                node, cte_query_ids, dialect=dialect
            ):
                raise SQLGuardError(f"forbidden table function in SQL: {folded_name}()")


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
    cte_query_ids = _cte_declaring_query_ids(root, dialect=dialect)
    tables: set[TableRef] = set()
    for table in root.find_all(exp.Table):
        name = table.name
        if not name:  # pragma: no mutate block - sqlglot never emits an unnamed Table
            continue
        # CTE self-reference: bare name matching a WITH alias in an ancestor query.
        if _is_cte_ref(table, cte_query_ids, dialect=dialect):
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
    #: PostgreSQL searches local FROM sources before the SELECT output list and only then
    #: enclosing levels. This flag says the output list offered this whole GROUP BY name;
    #: preceding LATERAL siblings are outside its local FROM/JOIN candidates.
    output_column: bool = False
    #: PostgreSQL table aliases may rename a prefix of a physical table's output columns.
    #: The name is a query output, but its physical ordinal source is not proven by the SQL
    #: or by the governed snapshot, so callers must keep it as an advisory remainder.
    postgres_table_column_alias: bool = False


def star_reads_columns(star: exp.Star) -> bool:
    """Whether this star expands to source columns rather than counting rows.

    Only a direct `COUNT(*)` argument is the row count. A `DISTINCT` node between
    the star and `Count` means `COUNT(DISTINCT *)`, whose value depends on every
    source column where the dialect accepts it.
    """
    return not isinstance(star.parent, exp.Count)


def _ancestors(node: exp.Expr) -> Iterator[exp.Expr]:
    current = node.parent
    while current is not None:
        yield current
        current = current.parent


def _node_runs(node: exp.Expr, open_ids: set[int]) -> bool:
    """A node runs iff every `exp.CTE` among its ancestors is open."""
    return all(
        not isinstance(ancestor, exp.CTE) or id(ancestor) in open_ids
        for ancestor in _ancestors(node)
    )


def _declaring_query(cte: exp.CTE) -> exp.Expr | None:
    """The query that owns this WITH: CTE → With → Select or Union."""
    parent = cte.parent
    if parent is None:
        return None
    return parent.parent


def _cte_declaring_query_ids(root: exp.Expr, *, dialect: str) -> dict[str, frozenset[int]]:
    """Map each CTE name to the query ids that declare it."""
    query_ids: defaultdict[str, set[int]] = defaultdict(set)
    for cte in root.find_all(exp.CTE):
        declaring_query = _declaring_query(cte)
        if declaring_query is None:
            continue
        name = cte.alias_or_name if dialect == "clickhouse" else cte.alias_or_name.lower()
        query_ids[name].add(id(declaring_query))
    return {name: frozenset(ids) for name, ids in query_ids.items()}


def _is_cte_ref(
    table: exp.Table, cte_query_ids: dict[str, frozenset[int]], *, dialect: str
) -> bool:
    """Whether a bare table is a CTE declared by one of its ancestor queries."""
    if table.db or table.catalog:
        return False
    name = table.name if dialect == "clickhouse" else table.name.lower()
    declaring_ids = cte_query_ids.get(name)
    if not declaring_ids:
        return False
    return any(id(ancestor) in declaring_ids for ancestor in _ancestors(table))


@dataclass(frozen=True, slots=True)
class _CteGate:
    cte_id: int
    declaring_id: int


@dataclass(frozen=True, slots=True)
class _CteRef:
    """A name that can open a CTE gate once it sits in a running part of the statement."""

    name: str
    enclosing: frozenset[int]
    ancestors: frozenset[int]
    named: bool
    scalar_declaring_id: int | None = None
    named_declaring_id: int | None = None


def _enclosing_and_ancestors(node: exp.Expression) -> tuple[frozenset[int], frozenset[int]]:
    """CTE ids among *node*'s ancestors, and the ids of every ancestor."""
    enclosing: list[int] = []
    ancestors: list[int] = []
    for ancestor in _ancestors(node):
        ancestors.append(id(ancestor))
        if isinstance(ancestor, exp.CTE):
            enclosing.append(id(ancestor))
    return frozenset(enclosing), frozenset(ancestors)


def _column_leftmost_name(column: exp.Column) -> str:
    """The leftmost written part of a column: catalog, else db, else table, else name.

    sqlglot parks a deeper tuple access in the qualifier slots (`t.a.b` is
    Column(this=b, table=a, db=t); `t.a.b.c` puts `t` in catalog). Scalar gates
    read that leftmost part as the candidate alias.
    """
    for part in (column.catalog, column.db, column.table, column.name):
        if part:
            return part
    return ""


def _open_cte_gates(root: exp.Expr) -> set[int]:
    """Ids of CTE gates that a running part of the statement opens.

    Linear worklist: each reference is processed once, each gate opens once. A name
    opens the nearest named CTE declaration it could name, compared
    case-sensitively, whose declaring query encloses the name. A column names a
    scalar alias through its leftmost written part, so a tuple access at any depth
    (`t.1`, `t.a.b`) opens the nearest scalar declaration. An ordinary two-part
    column qualified by one unambiguous explicit named-table alias, or an ordinary
    three-part column whose database/table pair matches one unaliased named-table source,
    in its own SELECT names that source instead. Same-level duplicates and self-references
    stay conservative. Named CTEs open through an unqualified table and through a bare
    `IN <name>` that is not a visible alias.
    """
    named_by_alias: dict[str, list[_CteGate]] = defaultdict(list)
    scalar_by_alias: dict[str, list[_CteGate]] = defaultdict(list)
    for cte in root.find_all(exp.CTE):
        alias = cte.alias
        if not alias:
            continue
        declaring = _declaring_query(cte)
        if declaring is None:
            continue
        gate = _CteGate(id(cte), id(declaring))
        if cte.args.get("scalar"):
            scalar_by_alias[alias].append(gate)
        else:
            named_by_alias[alias].append(gate)
    if not named_by_alias and not scalar_by_alias:
        return set()
    named_declaring_ids = {
        alias: {gate.declaring_id for gate in gates} for alias, gates in named_by_alias.items()
    }
    scalar_declaring_ids = {
        alias: {gate.declaring_id for gate in gates} for alias, gates in scalar_by_alias.items()
    }

    setting_items = frozenset(id(item) for item in _clickhouse_setting_items(root))
    refs: list[_CteRef] = []
    source_names: dict[int, dict[str, int]] = defaultdict(dict)
    source_table_pairs: dict[int, dict[tuple[str, str], int]] = defaultdict(dict)
    unaliased_source_tables: dict[int, dict[tuple[str, str], int]] = defaultdict(dict)
    explicit_table_aliases: dict[int, set[str]] = defaultdict(set)

    def add_named_ref(node: exp.Expression, name: str) -> None:
        enclosing, ancestors = _enclosing_and_ancestors(node)
        nearest_declaring = next(
            (
                id(ancestor)
                for ancestor in _ancestors(node)
                if id(ancestor) in named_declaring_ids.get(name, ())
            ),
            None,
        )
        if any(
            gate.declaring_id == nearest_declaring and gate.cte_id in enclosing
            for gate in named_by_alias.get(name, ())
        ):
            nearest_declaring = None
        refs.append(_CteRef(name, enclosing, ancestors, True, named_declaring_id=nearest_declaring))

    for source_clause in root.find_all(exp.From, exp.Join):
        source = source_clause.this
        if not isinstance(source, exp.Expression):
            continue
        query = source_clause.find_ancestor(exp.Select)
        if query is None:
            continue
        name = source.alias
        if not name and isinstance(source, exp.Table) and isinstance(source.this, exp.Identifier):
            name = source.name
        if not name:
            continue
        query_id = id(query)
        names = source_names[query_id]
        names[name] = names.get(name, 0) + 1
        if (
            isinstance(source, exp.Table)
            and isinstance(source.this, exp.Identifier)
            and source.db
            and source.name
        ):
            key = (source.db, source.name)
            tables = source_table_pairs[query_id]
            tables[key] = tables.get(key, 0) + 1
            if not source.alias and not source.catalog:
                eligible_tables = unaliased_source_tables[query_id]
                eligible_tables[key] = eligible_tables.get(key, 0) + 1
        if (
            isinstance(source, exp.Table)
            and isinstance(source.this, exp.Identifier)
            and source.alias
        ):
            explicit_table_aliases[query_id].add(name)

    for table in root.find_all(exp.Table):
        if table.db or table.catalog or not table.name:
            continue
        add_named_ref(table, table.name)
    for column in root.find_all(exp.Column):
        if _is_clickhouse_setting_column(column, setting_items):
            continue
        query = column.find_ancestor(exp.Select)
        if query is not None:
            query_id = id(query)
            if (
                column.db
                and column.table
                and not column.catalog
                and isinstance(column.this, exp.Identifier)
                and unaliased_source_tables[query_id].get((column.db, column.table)) == 1
                and source_table_pairs[query_id].get((column.db, column.table)) == 1
                and not source_names[query_id].get(column.db)
            ):
                continue
            if (
                column.table
                and not column.db
                and not column.catalog
                and isinstance(column.this, exp.Identifier)
                and column.table in explicit_table_aliases.get(query_id, ())
                and source_names[query_id].get(column.table) == 1
            ):
                continue
        alias = _column_leftmost_name(column)
        if not alias:
            continue
        enclosing, ancestors = _enclosing_and_ancestors(column)
        nearest_declaring = None
        declaring_ids = scalar_declaring_ids.get(alias)
        if declaring_ids:
            nearest_declaring = next(
                (id(ancestor) for ancestor in _ancestors(column) if id(ancestor) in declaring_ids),
                None,
            )
            if any(
                gate.declaring_id == nearest_declaring and gate.cte_id in enclosing
                for gate in scalar_by_alias[alias]
            ):
                nearest_declaring = None
        refs.append(_CteRef(alias, enclosing, ancestors, False, nearest_declaring))
    if named_by_alias:
        for column in clickhouse_in_table_fields(root):
            if column.table or column.db or column.catalog or not column.name:
                continue
            add_named_ref(column, column.name)

    waiting: dict[int, list[int]] = defaultdict(list)
    for index, ref in enumerate(refs):
        for cte_id in ref.enclosing:
            waiting[cte_id].append(index)

    open_ids: set[int] = set()
    queued = [not ref.enclosing for ref in refs]
    work: deque[int] = deque(index for index, ready in enumerate(queued) if ready)

    def _open_gate(gate: _CteGate, ref: _CteRef) -> None:
        if gate.cte_id in open_ids:
            return
        if gate.declaring_id not in ref.ancestors:
            return
        if not ref.named and gate.cte_id in ref.enclosing:
            return
        open_ids.add(gate.cte_id)
        for waiter in waiting.get(gate.cte_id, ()):
            if not queued[waiter] and refs[waiter].enclosing <= open_ids:
                queued[waiter] = True
                work.append(waiter)

    while work:
        ref = refs[work.popleft()]
        if ref.named:
            for gate in named_by_alias.get(ref.name, ()):
                if (
                    ref.named_declaring_id is not None
                    and gate.declaring_id != ref.named_declaring_id
                ):
                    continue
                _open_gate(gate, ref)
        else:
            for gate in scalar_by_alias.get(ref.name, ()):
                if (
                    ref.scalar_declaring_id is not None
                    and gate.declaring_id != ref.scalar_declaring_id
                ):
                    continue
                _open_gate(gate, ref)
    return open_ids


def clickhouse_has_reachable_star(sql: str) -> bool:
    """Whether a column-reading star is in a reachable part of a ClickHouse query."""
    root = _parse_one_select(sql, dialect="clickhouse")
    open_ids = _open_cte_gates(root)
    return any(
        star_reads_columns(star) and _node_runs(star, open_ids) for star in root.find_all(exp.Star)
    ) or any(_node_runs(column, open_ids) for column in clickhouse_in_table_fields(root))


def clickhouse_has_reachable_column_selector(sql: str) -> bool:
    """Whether a ClickHouse ``COLUMNS(...)`` selector is in a reachable query part."""
    root = _parse_one_select(sql, dialect="clickhouse")
    open_ids = _open_cte_gates(root)
    return any(_node_runs(columns, open_ids) for columns in root.find_all(exp.Columns))


def clickhouse_reachable_join_using_keys(sql: str) -> frozenset[str]:
    """Names of ``JOIN ... USING`` keys in reachable ClickHouse query parts."""
    root = _parse_one_select(sql, dialect="clickhouse")
    open_ids = _open_cte_gates(root)
    keys: set[str] = set()
    for join in root.find_all(exp.Join):
        if not _node_runs(join, open_ids):
            continue
        using = join.args.get("using")
        items = using if isinstance(using, list) else [using]
        keys.update(item.name for item in items if isinstance(item, exp.Identifier))
    return frozenset(keys)


class ColumnReads(NamedTuple):
    """What a SELECT reads at column level, and what saying more would take a guess.

    `unattributed`, `starred`, `column_selectors`, `join_using_keys`, and `natural_join` are
    the honest remainder, reported rather than resolved: a caller grading governance must
    be able to say the check was partial, because a silent skip and a clean result look
    identical to whoever reads the verdict.
    """

    attributed: frozenset[ColumnRef]
    #: Names SQL alone cannot pin to one table — unqualified with several of the sources its
    #: scope reads, unqualified in a scalar/`IN`/`EXISTS` subquery, a lateral written inside
    #: one of those, or a branch of a set operation written in one of those (the name may
    #: come from this level or the query around it), or qualified by a name no enclosing
    #: scope declares.
    unattributed: frozenset[str]
    #: A star in the statement reads source columns, so those columns depend on the physical
    #: table and cannot be known from the text. A direct `COUNT(*)` argument counts rows.
    starred: bool
    #: ClickHouse `COLUMNS(...)` selects a physical set of columns that the SQL text does not
    #: enumerate. The selector is kept as a typed remainder even though it leaves no `Star`.
    column_selectors: frozenset[str] = frozenset()
    #: `JOIN ... USING` names a key shared by both sources, but gives no table to attribute it
    #: to. The keys are kept as a typed remainder so governance can name the incomplete check.
    join_using_keys: frozenset[str] = frozenset()
    #: PostgreSQL `NATURAL JOIN` chooses equality keys from matching column names exposed by
    #: joined sources, including derived tables. SQL does not enumerate those names, so the
    #: join is kept as a typed remainder without guessing them.
    natural_join: bool = False
    #: The same remainder with its scope kept, one entry per unattributed reference. Names
    #: alone are what `unattributed` reports; this is what a caller needs to narrow them.
    ambiguous: frozenset[AmbiguousRef] = frozenset()
    #: ClickHouse SETTINGS that carry a row filter as SQL text. Its columns are not in the
    #: statement tree, so the query is only partly read at field level.
    opaque_settings: frozenset[str] = frozenset()


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
    inside a subquery. A set-operation branch shares the range of its parent query, so
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
    the definition of a query-level alias X, a WITH expression read through from it included,
    X is the column. A SELECT-list, inline, or ARRAY JOIN alias is visible in the query level
    that defines it (including other SELECT expressions, WHERE, PREWHERE, GROUP BY, HAVING,
    QUALIFY, ORDER BY, LIMIT BY, and JOIN conditions) and not in a subquery. A
    `WITH <expr> AS alias` (and `WITH (SELECT …) AS alias`) is visible
    in the query that declares it and in every subquery and set-operation branch below it. A visible
    alias wins over a table column of the same name; identifiers are compared as written.
    When a scope references an expression-form `WITH <expr> AS a` — directly, or through
    another alias whose definition references it — every column written in `<expr>` is
    graded as if written in that referencing scope. A query nested in `<expr>` is graded as
    the same subquery written at the reference: its own FROM, with bare names enclosed
    toward the referencing scope. sqlglot 30.14.0 builds no scope for those bodies
    (`scope.py:708` "Cannot traverse scope"), so the read-through builds one with
    `Scope.branch` and `_traverse_scope` — the same helpers `_traverse_subqueries` uses for
    an inline subquery. A query-level alias that can reach itself through query-level or
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
    and subquery forms are unchanged. In the PostgreSQL family WITH defines CTEs only,
    and WHERE cannot read a SELECT alias. A whole GROUP BY name offered by a valid
    SELECT output alias may also resolve to that output after local FROM sources;
    `IN <name>` stays the column sqlglot parsed. PostgreSQL also moves an ORDER BY
    after a parenthesized query onto that query before scopes are built. ClickHouse ARRAY JOIN
    items that sqlglot wraps in a non-query Subquery or Paren are unwrapped before alias indexing
    and scoping; ClickHouse 24.8.14.39 EXPLAIN SYNTAX rewrites `(tags AS t) AS a` to `tags AS a`,
    while `ARRAY JOIN (SELECT ['p','q']) AS a` remains a query; the observed HEAD result for
    `SELECT a FROM dm.sales_daily ARRAY JOIN (SELECT ['p','q']) AS a` is no attributed column
    and an unattributed `a`, pinned by the tests.
    """
    root = _parse_one_select(sql, dialect=dialect)
    unary_plus_offsets, quoted_function_names = (
        _postgres_sort_token_info(sql, dialect) if dialect != "clickhouse" else (None, {})
    )
    if dialect == "clickhouse":
        _normalize_clickhouse_array_joins(root)
        aliases = _clickhouse_aliases(root)
        _rewrite_clickhouse_in_tables(root, aliases)
    else:
        aliases = None
        _rewrite_postgres_parenthesized_orders(root)
    root_scope = build_scope(root)
    if root_scope is None:  # pragma: no mutate block - SELECT/set ops define a scope
        raise SQLGuardError("SELECT does not define a query scope")
    scopes = list(root_scope.traverse())
    for scope in scopes:
        if not isinstance(scope.expression, exp.SetOperation):
            continue
        # sqlglot traverses set-operation branches, but omits subqueries in their
        # shared modifiers (for example, LIMIT (SELECT ...)). Grade those scopes too.
        for name in ("order", "limit", "offset", "fetch"):
            modifier = scope.expression.args.get(name)
            if isinstance(modifier, exp.Expression):
                for query in _outermost_nested_queries(modifier):
                    scopes.extend(
                        _traverse_scope(scope.branch(query, scope_type=ScopeType.SUBQUERY))
                    )
    owners = {id(scope.expression): scope for scope in scopes}
    attributed: set[ColumnRef] = set()
    ambiguous: set[AmbiguousRef] = set()
    _grade_columns(
        scopes,
        owners,
        aliases,
        attributed,
        ambiguous,
        _ReadThroughState(),
        unary_plus_offsets,
        quoted_function_names,
    )
    return ColumnReads(
        attributed=frozenset(attributed),
        unattributed=frozenset(ref.name for ref in ambiguous),
        starred=any(star_reads_columns(star) for star in root.find_all(exp.Star)),
        column_selectors=(
            frozenset({"COLUMNS(...)"})
            if dialect == "clickhouse" and root.find(exp.Columns) is not None
            else frozenset()
        ),
        join_using_keys=_join_using_keys(root),
        natural_join=(dialect == "postgres" and _has_natural_join(root)),
        ambiguous=frozenset(ambiguous),
        opaque_settings=(
            _clickhouse_opaque_settings(root) if dialect == "clickhouse" else frozenset()
        ),
    )


def _join_using_keys(root: exp.Expression) -> frozenset[str]:
    """Names of keys in ``JOIN ... USING`` clauses found in the parsed statement."""
    keys: set[str] = set()
    for join in root.find_all(exp.Join):
        using = join.args.get("using")
        if using is None:
            continue
        items = using if isinstance(using, list) else [using]
        keys.update(item.name for item in items if isinstance(item, exp.Identifier))
    return frozenset(keys)


def _has_natural_join(root: exp.Expression) -> bool:
    """Whether the statement contains a PostgreSQL ``NATURAL JOIN`` anywhere in its AST."""
    return any(join.args.get("method") == "NATURAL" for join in root.find_all(exp.Join))


class _ClickHouseAlias(NamedTuple):
    """One ClickHouse query-level or scalar WITH alias, as written."""

    name: str
    definition: exp.Expression
    #: Expression-form `WITH <expr> AS name` is read through; a SELECT-list alias and a
    #: `WITH (SELECT …) AS name` are not — the former is already graded where written,
    #: the latter keeps its own scope and FROM.
    read_through: bool


class _ClickHouseAliasIndex(NamedTuple):
    """Aliases declared on each Select/set operation, keyed by `id(expression)`."""

    select: dict[int, dict[str, _ClickHouseAlias]]
    with_: dict[int, dict[str, _ClickHouseAlias]]
    #: Non-projection declarations whose loose reads Scope.columns may omit.
    local: dict[int, tuple[_ClickHouseAlias, ...]]
    #: Enclosing local declarations for nodes in an alias definition.
    owners: dict[int, tuple[_ClickHouseAlias, ...]]
    #: Bare fields on the right of `IN`, collected with the alias walk.
    in_fields: tuple[exp.Column, ...]
    #: Direct SETTINGS-item expressions, whose Column nodes are query options rather than reads.
    setting_items: frozenset[int]
    #: `Scope.columns` by expression identity. Read-through branches reuse the
    #: same nested Select expressions, so their columns do not need recollecting.
    scope_columns: dict[int, tuple[exp.Column, ...]]
    #: Query-level aliases whose SCC has more than one member, filled lazily
    #: per `id(scope)` — a branch scope's WITH visibility depends on its parent.
    select_on_cycle: dict[int, frozenset[str]]
    #: Scope objects held so `select_on_cycle` / `with_reaches` keys stay unique.
    held_scopes: list[Scope]
    #: Query-level alias names each expression-form WITH reaches at a scope,
    #: keyed by `(id(definition), id(scope), enclosing definition identities)`.
    with_reaches: dict[tuple[int, int, int | tuple[int, ...]], frozenset[str]]


class _ReadThroughState:
    """Mutable WITH read-through state for one `extract_column_refs` call.

    `path` is the aliases being expanded right now, across scopes, keyed by
    declaration identity (`id(definition)`), never by name — an inner WITH
    alias that shadows an outer name is a different declaration. `done` holds
    the `(alias, scope, owners)` triples already expanded in full; the Scope
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
        self.done: dict[tuple[int, int, int | tuple[int, ...]], Scope] = {}
        self.cyclic: dict[tuple[int, int], Scope] = {}
        self.expansions = 0


_QUERY_BODY = (exp.Select, exp.SetOperation, exp.Subquery)


_CLICKHOUSE_OPAQUE_SETTINGS = frozenset({"additional_table_filters"})


def _clickhouse_setting_items(root: exp.Expr) -> tuple[exp.Expression, ...]:
    """Direct expressions of every query branch's SETTINGS clause."""
    return tuple(
        item
        for query in root.find_all(exp.Query)
        for item in query.args.get("settings") or ()
        if isinstance(item, exp.Expression)
    )


def clickhouse_setting_names(sql: str) -> frozenset[str]:
    """Case-folded SETTINGS names from every branch of the guard's SELECT parse."""
    root = _parse_one_select(sql, dialect="clickhouse")
    return frozenset(
        item.this.name.casefold()
        for item in _clickhouse_setting_items(root)
        if isinstance(item, exp.EQ) and isinstance(item.this, exp.Column)
    )


def _clickhouse_opaque_settings(root: exp.Expression) -> frozenset[str]:
    """SETTINGS whose SQL-text value can add a table row filter outside this AST."""
    names: set[str] = set()
    for item in _clickhouse_setting_items(root):
        name = item.this if isinstance(item, exp.EQ) else None
        if (
            isinstance(name, exp.Column)
            and not name.table
            and (name.name.casefold() in _CLICKHOUSE_OPAQUE_SETTINGS)
        ):
            names.add(name.name)
    return frozenset(names)


def _is_clickhouse_setting_column(column: exp.Column, setting_items: frozenset[int]) -> bool:
    """Whether a Column belongs directly to SETTINGS rather than to an invalid nested query."""
    current: exp.Expr = column
    while current.parent is not None:
        parent = current.parent
        if isinstance(parent, _QUERY_BODY):
            return False
        if id(parent) in setting_items:
            return True
        current = parent
    return False


def _clickhouse_aliases(root: exp.Expr) -> _ClickHouseAliasIndex:
    """Query-level and scalar WITH aliases declared on each query node."""
    select_map: dict[int, dict[str, _ClickHouseAlias]] = {}
    with_map: dict[int, dict[str, _ClickHouseAlias]] = {}
    local_map: dict[int, list[_ClickHouseAlias]] = {}
    owner_map: dict[int, tuple[_ClickHouseAlias, ...]] = {}
    in_fields: list[exp.Column] = []
    projected_ids: dict[int, set[int]] = {}
    contexts: dict[int, tuple[exp.Select | None, tuple[_ClickHouseAlias, ...]]] = {}

    # `find_all(exp.Alias)` from every Select revisits nested query levels, which
    # makes a deeply nested statement quadratic. A single breadth-first walk both
    # preserves sqlglot's declaration order and assigns every physical alias to
    # the nearest Select without climbing its ancestors again.
    for node in root.walk(bfs=True):
        parent = node.parent
        context, node_owners = (
            contexts.get(id(parent), (None, ())) if parent is not None else (None, ())
        )
        if isinstance(node, exp.Select):
            context, node_owners = node, ()
        elif isinstance(node, exp.CTE):
            context, node_owners = None, ()
        elif isinstance(node, (exp.Subquery, exp.SetOperation)):
            context = None

        if isinstance(node, exp.In):
            field = node.args.get("field")
            if isinstance(field, exp.Column):
                in_fields.append(field)

        if not isinstance(node, exp.Query):
            if not isinstance(node, exp.Alias) or context is None:
                if node_owners and isinstance(node, exp.Column):
                    owner_map[id(node)] = node_owners
                contexts[id(node)] = (context, node_owners)
                continue
            sid = id(context)
            name = node.alias
            if not name:
                if node_owners and isinstance(node, exp.Column):
                    owner_map[id(node)] = node_owners
                contexts[id(node)] = (context, node_owners)
                continue
            declared = _ClickHouseAlias(name=name, definition=node.this, read_through=False)
            if id(node) not in projected_ids[sid]:
                local_map[sid].append(declared)
                # A top-level SELECT alias wins. The first remaining declaration is what
                # sqlglot yields, matching ClickHouse's rejection of conflicting aliases.
                select_map[sid].setdefault(name, declared)
            node_owners = (*node_owners, declared)
            owner_map[id(node.this)] = node_owners
            contexts[id(node)] = (context, node_owners)
            continue

        nid = id(node)
        if isinstance(node, exp.Select):
            select_aliases: dict[str, _ClickHouseAlias] = {}
            select_map[nid] = select_aliases
            local_map[nid] = []
            projected_ids[nid] = {id(projected) for projected in node.expressions}
            for projected in node.expressions:
                if not isinstance(projected, exp.Alias):
                    continue
                name = projected.alias
                if not name:
                    continue
                select_aliases[name] = _ClickHouseAlias(
                    name=name, definition=projected.this, read_through=False
                )
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
        contexts[nid] = (context, node_owners)
    return _ClickHouseAliasIndex(
        select=select_map,
        with_=with_map,
        local={sid: tuple(declared) for sid, declared in local_map.items() if declared},
        owners=owner_map,
        in_fields=tuple(in_fields),
        setting_items=frozenset(id(item) for item in _clickhouse_setting_items(root)),
        scope_columns={},
        select_on_cycle={},
        held_scopes=[],
        with_reaches={},
    )


def _inside(node: exp.Expression, ancestor: exp.Expression) -> bool:
    """Whether *node* is *ancestor* or sits under it."""
    current: exp.Expr | None = node
    while current is not None:
        if current is ancestor:
            return True
        current = current.parent
    return False


def _inside_own_alias_definition(column: exp.Column, aliases: _ClickHouseAliasIndex) -> bool:
    """Whether a local query-level alias with this name directly encloses *column*."""
    return any(owner.name == column.name for owner in aliases.owners.get(id(column), ()))


def _clickhouse_alias_ref(
    column: exp.Column,
    scope: Scope,
    aliases: _ClickHouseAliasIndex,
    *,
    ignore_cycle: bool = False,
) -> _ClickHouseAlias | None:
    """The visible alias this bare name is, or ``None`` when it is a column.

    A query-level alias of this query level wins over a WITH alias of the same name,
    except inside that alias's defining expression — there the name is the
    column, or a WITH alias still visible. A WITH alias is visible in this query and
    every subquery and set-operation branch below the query that declared it. Inside the
    expression that defines alias X, the name X is the column, not that alias.
    A query-level alias that would close a cycle through query-level or WITH
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
    inside_own_definition = _inside_own_alias_definition(column, aliases)
    if select_alias is not None and not inside_own_definition:
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


def _normalize_clickhouse_array_join_item(item: exp.Expression) -> exp.Expr:
    """Unwrap a non-query parenthesized ClickHouse ARRAY JOIN item."""
    if not isinstance(item, (exp.Subquery, exp.Paren)):
        return item

    wrappers: list[exp.Subquery | exp.Paren] = []
    current: exp.Expression = item
    while isinstance(current, (exp.Subquery, exp.Paren)):
        wrappers.append(current)
        current = current.this
        if isinstance(current, exp.Query) and not isinstance(current, (exp.Subquery, exp.Paren)):
            return item

    alias: exp.Identifier | None = None
    for wrapper in wrappers:
        table_alias = wrapper.args.get("alias")
        if isinstance(table_alias, exp.TableAlias) and table_alias.this is not None:
            alias = table_alias.this.copy()
            break

    if alias is None:
        return current

    expression = current.this if isinstance(current, exp.Alias) else current
    return exp.alias_(expression, alias, copy=False)


def _normalize_clickhouse_array_joins(root: exp.Expr) -> None:
    """Make non-query parenthesized ARRAY JOIN items look like ClickHouse's AST."""
    for join in root.find_all(exp.Join):
        if join.args.get("kind") != "ARRAY":
            continue
        join.set("this", _normalize_clickhouse_array_join_item(join.this))
        expressions = join.args.get("expressions")
        if expressions:
            join.set(
                "expressions",
                [_normalize_clickhouse_array_join_item(item) for item in expressions],
            )


def clickhouse_in_table_fields(
    root: exp.Expr, aliases: _ClickHouseAliasIndex | None = None
) -> tuple[exp.Column, ...]:
    """Columns that ClickHouse reads as `IN (SELECT * FROM <name>)`, not as aliases.

    `<name>` is the Column in `In.args["field"]` (bare or qualified). A visible
    alias — `_clickhouse_alias_ref` in that column's scope — stays an alias, so a
    cyclic SELECT alias counts as a column there and becomes a table. A column
    sqlglot did not put in any query scope is left alone. A statement with no
    Column on the right of any IN returns at once, without building scopes or the
    alias index: most statements have none, and the grading builds both anyway.
    """
    fields: tuple[exp.Column, ...] | list[exp.Column]
    if aliases is None:
        _normalize_clickhouse_array_joins(root)
        fields = [
            field
            for field in (node.args.get("field") for node in root.find_all(exp.In))
            if isinstance(field, exp.Column)
        ]
        if not fields:
            return ()
        aliases = _clickhouse_aliases(root)
    else:
        fields = aliases.in_fields
    if not fields:
        return ()
    root_scope = build_scope(root)
    if root_scope is None:  # pragma: no mutate block - SELECT/set ops define a scope
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


def _rewrite_clickhouse_in_tables(
    root: exp.Expression, aliases: _ClickHouseAliasIndex | None = None
) -> None:
    """Replace `x IN <table>` with `x IN (SELECT * FROM <table>)` when not an alias."""
    for column in clickhouse_in_table_fields(root, aliases):
        node = column.parent
        if not isinstance(node, exp.In):
            continue
        table = _column_as_table(column)
        node.set("field", None)
        node.set("query", _star_subquery_from(table))


def _column_owners(
    node: exp.Expression, aliases: _ClickHouseAliasIndex
) -> tuple[_ClickHouseAlias, ...]:
    """Actual query-level alias definitions of *scope*'s level enclosing *node*.

    Every declaration counts, including one that lost the indexed-name precedence:
    inside its own definition its name is still a column. The alias index records
    this while walking the AST, so grading does not walk the ancestors again.
    """
    return aliases.owners.get(id(node), ())


def _owner_key(owners: tuple[_ClickHouseAlias, ...]) -> int | tuple[int, ...]:
    if not owners:
        return 0
    return tuple(id(owner.definition) for owner in owners)


def _loose_columns(node: exp.Expression) -> list[exp.Column]:
    """Columns written in *node* that are not inside a nested query.

    Nested SELECT/set-operation/subquery bodies keep their own FROM. sqlglot 30.14.0
    builds no scope for a query nested in an expression-form WITH body, so the
    read-through grades those bodies as a subquery of the referencing scope
    rather than pruning them and leaving them ungraded.
    """

    def prune(child: exp.Expr) -> bool:
        return child is not node and isinstance(child, _QUERY_BODY)

    return [child for child in node.walk(prune=prune) if isinstance(child, exp.Column)]


def _having_qualify_columns(scope: Scope) -> list[exp.Column]:
    """Bare columns sqlglot omits from ``Scope.columns`` in HAVING and QUALIFY.

    sqlglot treats those names as potential projection aliases.  Alias visibility is an
    engine rule, though: PostgreSQL has no output alias there, and ClickHouse resolves a
    visible alias through the ordinary ClickHouse alias index before this column can be
    attributed.  Nested query bodies keep their own scope.
    """
    columns: list[exp.Column] = []
    for name in ("having", "qualify"):
        clause = scope.expression.args.get(name)
        if clause is None:
            continue
        columns.extend(column for column in _loose_columns(clause) if not column.table)
    return columns


def _attribute_column(
    scope: Scope,
    column: exp.Column,
    attributed: set[ColumnRef],
    ambiguous: set[AmbiguousRef],
    *,
    postgres_table_column_aliases: bool = False,
) -> None:
    """Grade one column against *scope*'s sources."""
    identifier = column.this
    quoted = isinstance(identifier, exp.Identifier) and identifier.quoted
    source = _column_source(scope, column)
    if source is None:
        ambiguous.add(
            _ambiguous_ref(
                scope,
                column,
                quoted=quoted,
                postgres_table_column_aliases=postgres_table_column_aliases,
            )
        )
        return
    if (
        postgres_table_column_aliases
        and isinstance(source, (exp.Table, Scope))
        and _postgres_table_alias_matches(source, column, scope)
    ):
        ambiguous.add(
            _ambiguous_ref(scope, column, quoted=quoted)._replace(postgres_table_column_alias=True)
        )
        return
    if not isinstance(source, exp.Table):
        return  # a CTE or subquery column: real, but not a table's
    attributed.add(ColumnRef(table=_table_ref(source), name=column.name, quoted=quoted))


def _postgres_name(identifier: exp.Expression) -> str:
    """PostgreSQL's spelling for a quoted or folded identifier."""
    if isinstance(identifier, exp.Identifier) and identifier.quoted:
        return identifier.name
    return identifier.name.lower()


def _postgres_table_alias_matches(
    source: exp.Table | Scope, column: exp.Column, scope: Scope
) -> bool:
    """Whether a PostgreSQL table expression renames this column output name."""
    if isinstance(source, Scope):
        inner = source.expression
        if not isinstance(inner, exp.Table):
            return False
        columns = None
        wrapper = inner.parent
    else:
        alias = source.args.get("alias")
        columns = alias.args.get("columns") if isinstance(alias, exp.TableAlias) else None
        wrapper = (
            source.parent
            if any(candidate is source for candidate in _read_sources(scope))
            else None
        )
    while isinstance(wrapper, exp.Subquery):
        alias = wrapper.args.get("alias")
        if isinstance(alias, exp.TableAlias) and isinstance(alias.args.get("columns"), list):
            columns = alias.args["columns"]
        wrapper = wrapper.parent
    if not isinstance(columns, list):
        return False
    name = _postgres_name(column.this)
    return any(
        isinstance(item, exp.Identifier) and _postgres_name(item) == name for item in columns
    )


def _postgres_table_column_alias_in_scope(scope: Scope, column: exp.Column) -> bool:
    """Whether a local physical source exposes this alias-column output name."""
    sources = [_referenced_source(node, scope.cte_sources) for _alias, node in scope.references]
    return any(
        isinstance(source, (exp.Table, Scope))
        and _postgres_table_alias_matches(source, column, scope)
        for source in sources
    )


def _postgres_sort_token_info(
    sql: str, dialect: str
) -> tuple[frozenset[int] | None, dict[int, str]]:
    """Track unary plus offsets and quoted function spellings erased by sqlglot."""
    tokens = sqlglot.Dialect.get_or_raise(dialect).tokenize(sql)
    offsets: set[int] = set()
    quoted_function_names: dict[int, str] = {}
    has_plus = False
    for index, token in enumerate(tokens):
        if token.token_type == TokenType.IDENTIFIER and token.text.lower() in {
            "count",
            "sum",
            "avg",
            "min",
            "max",
            "row_number",
        }:
            quoted_function_names[token.start] = token.text
        if token.token_type != TokenType.PLUS:
            continue
        has_plus = True
        following = index + 1
        while following < len(tokens):
            if tokens[following].token_type == TokenType.L_PAREN:
                following += 1
            elif (
                tokens[following].token_type == TokenType.VAR
                and tokens[following].text.upper() == "CAST"
                and following + 1 < len(tokens)
                and tokens[following + 1].token_type == TokenType.L_PAREN
            ):
                # CAST has no offset metadata; carry the erased plus to its argument.
                following += 2
            else:
                break
        if following < len(tokens):
            offsets.add(tokens[following].start)
    return (frozenset(offsets) if has_plus else None), quoted_function_names


def _postgres_plus_expression(column: exp.Column, offsets: frozenset[int] | None) -> bool:
    """A column written after a unary plus, which sqlglot drops while parsing."""
    if offsets is None:
        return False
    starts = [part.meta.get("start") for part in column.parts]
    if not all(isinstance(start, int) for start in starts):
        return True
    return any(start in offsets for start in starts)


def _rewrite_postgres_parenthesized_orders(root: exp.Expression) -> None:
    """Attach a parenthesized query's trailing ORDER BY to its query body."""
    for wrapper in root.find_all(exp.Subquery):
        order = wrapper.args.get("order")
        if not isinstance(order, exp.Order):
            continue
        target = wrapper.this
        while (
            isinstance(target, exp.Subquery)
            and not target.args.get("alias")
            and not target.args.get("order")
        ):
            target = target.this
        if isinstance(target, (exp.Select, exp.SetOperation)) and not target.args.get("order"):
            wrapper.set("order", None)
            target.set("order", order)


def _postgres_output_name(
    projected: exp.Expression,
    unary_plus_offsets: frozenset[int] | None,
    quoted_function_names: dict[int, str] | None = None,
) -> str | None:
    """Known strong SELECT output name, preserved through parentheses, windows and casts."""
    if isinstance(projected, exp.Alias):
        alias = projected.args.get("alias")
        return _postgres_name(alias) if isinstance(alias, exp.Identifier) else None
    windowed = False
    while isinstance(projected, (exp.Paren, exp.Window, exp.Cast)):
        if isinstance(projected, exp.Cast) and projected.meta:
            # Normalized function calls carry positions of their own function name.
            return None
        windowed = windowed or isinstance(projected, exp.Window)
        projected = projected.this
    if isinstance(projected, exp.Filter):
        filtered = projected.this
        if not isinstance(filtered, (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)):
            return None
        projected = filtered
    if isinstance(projected, exp.Column) and not _postgres_plus_expression(
        projected, unary_plus_offsets
    ):
        return _postgres_name(projected.this)
    if windowed and isinstance(projected, exp.RowNumber):
        start = projected.meta.get("start")
        if unary_plus_offsets is not None and (
            not isinstance(start, int) or start in unary_plus_offsets
        ):
            return None
        if quoted_function_names is not None and isinstance(start, int):
            return quoted_function_names.get(start, "row_number")
        return "row_number"
    if isinstance(projected, (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)):
        start = projected.meta.get("start")
        if unary_plus_offsets is not None and (
            not isinstance(start, int) or start in unary_plus_offsets
        ):
            return None
        function_name = {
            exp.Count: "count",
            exp.Sum: "sum",
            exp.Avg: "avg",
            exp.Min: "min",
            exp.Max: "max",
        }[type(projected)]
        if quoted_function_names is not None and isinstance(start, int):
            return quoted_function_names.get(start, function_name)
        return function_name
    return None


def _postgres_sort_output(
    scope: Scope,
    column: exp.Column,
    unary_plus_offsets: frozenset[int] | None,
    quoted_function_names: dict[int, str] | None,
) -> bool:
    """A whole SELECT or set-operation sort item naming its output."""
    if column.table or _postgres_plus_expression(column, unary_plus_offsets):
        return False
    node: exp.Expression = column
    while isinstance(node.parent, exp.Paren):
        node = node.parent
    parent = node.parent
    if isinstance(parent, exp.Ordered):
        order = parent.parent
        if not isinstance(order, exp.Order) or order.parent is not scope.expression:
            return False
    elif isinstance(parent, exp.Tuple):
        distinct = parent.parent
        if not isinstance(distinct, exp.Distinct) or distinct.parent is not scope.expression:
            return False
    else:
        return False
    output: exp.Expr = scope.expression
    while isinstance(output, (exp.SetOperation, exp.Subquery, exp.Paren)):
        output = output.this
    if not isinstance(output, exp.Select):
        return False
    name = _postgres_name(column.this)
    return any(
        _postgres_output_name(item, unary_plus_offsets, quoted_function_names) == name
        for item in output.expressions
    )


def _postgres_sort_columns(scope: Scope) -> list[exp.Column]:
    """Restore SELECT sort columns omitted by sqlglot's raw output-name filter."""
    if not isinstance(scope.expression, exp.Select):
        return []
    columns: list[exp.Column] = []
    order = scope.expression.args.get("order")
    if isinstance(order, exp.Expression):
        columns.extend(column for column in _loose_columns(order) if not column.is_star)
    distinct = scope.expression.args.get("distinct")
    if isinstance(distinct, exp.Distinct):
        on = distinct.args.get("on")
        if isinstance(on, exp.Expression):
            columns.extend(column for column in _loose_columns(on) if not column.is_star)
    return columns


def _postgres_grouping_output(
    scope: Scope, column: exp.Column, unary_plus_offsets: frozenset[int] | None
) -> bool:
    """A whole grouping element that a valid SELECT output alias could supply."""
    if (
        column.table
        or not isinstance(scope.expression, exp.Select)
        or _postgres_plus_expression(column, unary_plus_offsets)
    ):
        return False
    node: exp.Expression = column
    while node.parent is not None:
        parent = node.parent
        if isinstance(parent, exp.Group):
            if parent.parent is not scope.expression:
                return False
            break
        if not isinstance(parent, (exp.Rollup, exp.Cube, exp.GroupingSets, exp.Paren, exp.Tuple)):
            return False
        node = parent
    else:
        return False
    name = _postgres_name(column.this)
    for projected in scope.expression.expressions:
        if not isinstance(projected, exp.Alias):
            continue
        alias = projected.args.get("alias")
        if (
            not isinstance(alias, exp.Identifier)
            or _postgres_output_name(projected, unary_plus_offsets) != name
        ):
            continue
        definition = projected.this
        if isinstance(definition, exp.Column) and _postgres_name(definition.this) == name:
            continue
        if any(
            isinstance(part, (exp.AggFunc, exp.Window))
            for part in definition.walk(
                prune=lambda child, root=definition: child is not root
                and isinstance(child, _QUERY_BODY)
            )
        ):
            continue
        return True
    return False


def _grade_columns(
    scopes: list[Scope],
    owners: dict[int, Scope],
    aliases: _ClickHouseAliasIndex | None,
    attributed: set[ColumnRef],
    ambiguous: set[AmbiguousRef],
    state: _ReadThroughState,
    unary_plus_offsets: frozenset[int] | None,
    quoted_function_names: dict[int, str] | None,
) -> bool:
    """Grade each column once, in the innermost scope that contains it.

    Returns whether any WITH expansion in this walk hit the depth cap or budget.
    """
    truncated = False
    for scope in scopes:
        columns: tuple[exp.Column, ...] | list[exp.Column]
        if aliases is None:
            columns = list(scope.columns)
            seen_columns = {id(column) for column in columns}
            for column in _postgres_sort_columns(scope):
                if id(column) not in seen_columns:
                    columns.append(column)
                    seen_columns.add(id(column))
        else:
            scope_id = id(scope.expression)
            cached_columns = aliases.scope_columns.get(scope_id)
            if cached_columns is None:
                cached_columns = tuple(scope.columns)
                aliases.scope_columns[scope_id] = cached_columns
            columns = cached_columns
        if aliases is not None and aliases.local:
            declarations = aliases.local.get(id(scope.expression))
        else:
            declarations = None
        clause_columns = _having_qualify_columns(scope)
        if declarations or clause_columns:
            columns = list(columns)
            seen_columns = {id(column) for column in columns}
            if declarations:
                for declared in declarations:
                    for column in _loose_columns(declared.definition):
                        if id(column) in seen_columns:
                            continue
                        seen_columns.add(id(column))
                        columns.append(column)
            for column in clause_columns:
                if id(column) in seen_columns:
                    continue
                seen_columns.add(id(column))
                columns.append(column)
        for column in columns:
            # A scope lists the columns of its nested subqueries too, and resolving one of
            # those against *this* scope's sources would attribute it to a table it was
            # never written against. Each column is graded once, where it was written.
            if _owning_scope(column, owners) is not scope:
                continue
            if aliases is not None and _is_clickhouse_setting_column(column, aliases.setting_items):
                continue
            if aliases is not None:
                alias = _clickhouse_alias_ref(column, scope, aliases)
                if alias is not None:
                    alias_owners = _column_owners(column, aliases) if alias.read_through else ()
                    if _read_through_alias(
                        alias, column, scope, aliases, attributed, ambiguous, state, alias_owners
                    ):
                        truncated = True
                    continue
            if aliases is None and _postgres_grouping_output(scope, column, unary_plus_offsets):
                if _postgres_table_column_alias_in_scope(scope, column):
                    _attribute_column(
                        scope,
                        column,
                        attributed,
                        ambiguous,
                        postgres_table_column_aliases=True,
                    )
                    continue
                sources = [
                    _referenced_source(node, scope.cte_sources) for _alias, node in scope.references
                ]
                if sources and not (len(sources) == 1 and not isinstance(sources[0], exp.Table)):
                    identifier = column.this
                    quoted = isinstance(identifier, exp.Identifier) and identifier.quoted
                    ambiguous.add(
                        _ambiguous_ref(scope, column, quoted=quoted, sources=sources)._replace(
                            output_column=True
                        )
                    )
                continue
            if aliases is None and _postgres_sort_output(
                scope, column, unary_plus_offsets, quoted_function_names
            ):
                continue
            _attribute_column(
                scope,
                column,
                attributed,
                ambiguous,
                postgres_table_column_aliases=aliases is None,
            )
    return truncated


def _select_aliases_reached_from_with(
    start: _ClickHouseAlias,
    scope: Scope,
    aliases: _ClickHouseAliasIndex,
    owners: tuple[_ClickHouseAlias, ...],
) -> frozenset[str]:
    """Query-level aliases of *scope*'s level reached through *start*'s WITH body.

    Nested queries are not followed: query-level aliases are not visible there. Names
    resolve with the raw rule that does not consult the on-cycle set, so the
    SELECT graph does not depend on itself. Memoised per (declaration, scope).
    """
    owner_key = _owner_key(owners)
    key = (id(start.definition), id(scope), owner_key)
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
        memo = aliases.with_reaches.get((ident, id(scope), owner_key))
        if memo is not None:
            reached.update(memo)
            continue
        seen.add(ident)
        for column in _loose_columns(alias.definition):
            resolved = _clickhouse_alias_ref(column, scope, aliases, ignore_cycle=True)
            if resolved is None:
                continue
            if owners and any(resolved.name == owner.name for owner in owners):
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
    """a → b when a's definition, following WITH bodies at *scope*, reaches query-level alias b.

    Reaching a itself is the owner case (a column), not a self-edge. Nested queries
    are not followed. Names resolve without the on-cycle set.
    """
    select_aliases = aliases.select.get(id(scope.expression), {})
    graph: dict[str, list[str]] = {name: [] for name in select_aliases}
    for name, alias in select_aliases.items():
        seen: set[str] = set()
        owners = _column_owners(alias.definition, aliases)
        for column in _loose_columns(alias.definition):
            resolved = _clickhouse_alias_ref(column, scope, aliases, ignore_cycle=True)
            if resolved is None:
                continue
            if resolved.read_through:
                others = _select_aliases_reached_from_with(resolved, scope, aliases, owners)
            elif owners and any(resolved.name == owner.name for owner in owners):
                continue
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
    """Query-level aliases that can reach themselves through query-level or WITH definitions.

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
    """Outermost SELECT/set-operation/Subquery bodies nested in *node*."""
    found: list[exp.Expression] = []

    def prune(child: exp.Expr) -> bool:
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
    return _grade_columns(nested_scopes, owners, aliases, attributed, ambiguous, state, None, None)


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
    owners: tuple[_ClickHouseAlias, ...],
) -> bool:
    """Grade columns of a referenced expression-form WITH as if written in *scope*.

    Loose columns are graded in *scope*. A query nested in the definition is graded
    as the same subquery written at the reference — its own FROM, enclosed toward
    *scope* — because sqlglot builds no scope for those bodies. Expansion is keyed
    by declaration identity and memoised per (alias, scope, owners); a cycle in
    *scope* is a column there, keyed by (declaration, scope), so an inner-only
    cycle does not hide an outer read of the same alias; a reference past the
    depth cap or expansion budget is unattributed under its own name. A loose
    column that resolves to an owner (a query-level alias whose definition this
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
    done_key = (ident, id(scope), _owner_key(owners))
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
                if owners and any(nested.name == owner.name for owner in owners):
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
                    owners,
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


def _ambiguous_ref(
    scope: Scope,
    column: exp.Column,
    *,
    quoted: bool,
    sources: list[exp.Selectable | Scope] | None = None,
    postgres_table_column_aliases: bool = False,
) -> AmbiguousRef:
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
    if sources is None:
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
        postgres_table_column_alias=(
            postgres_table_column_aliases
            and not column.table
            and any(
                isinstance(source, (exp.Table, Scope))
                and _postgres_table_alias_matches(source, column, scope)
                for source in sources
            )
        ),
    )


def _read_sources(scope: Scope) -> list[exp.Selectable | Scope]:
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
    several scopes below it — a set operation inserts a SUBQUERY and a ScopeType.UNION
    branch (one more level per further branch), and a lateral nested in a lateral
    body sits under another UDTF. Walking ancestors and stopping at a derived table or
    CTE body is what keeps the siblings visible at every level that can actually see
    them: a derived table or CTE written inside a lateral is a wall, because it cannot
    read what is written around it.
    """
    read: list[exp.Selectable | Scope] = []
    cte_sources = scope.cte_sources
    for _alias, node in scope.references:
        read.append(_referenced_source(node, cte_sources))
    node_scope: Scope | None = scope
    while node_scope is not None:
        read.extend(node_scope.lateral_sources.values())
        if node_scope.scope_type in (ScopeType.DERIVED_TABLE, ScopeType.CTE):
            break
        node_scope = node_scope.parent
    return read


def _referenced_source(
    node: exp.Selectable, cte_sources: dict[str, exp.Table | Scope]
) -> exp.Selectable | Scope:
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
    enclosing query the subquery around the set operation can see. Recursing through
    ScopeType.UNION only — not through DERIVED_TABLE or CTE — keeps a root set operation,
    and one inside a FROM-subquery or CTE body, un-enclosed. Nested set operations walk
    up until they hit a non-set-operation parent.

    A LATERAL or table-function body (`ScopeType.UDTF`) inherits enclosure from the level
    that contains it, the same way a set-operation branch does. What it reads beyond its own FROM
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


def _column_source(scope: Scope, column: exp.Column) -> exp.Selectable | Scope | None:
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
