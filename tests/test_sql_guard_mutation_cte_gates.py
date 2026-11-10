"""Public CTE regressions and documented WITH reachability helper contracts."""

import gc
import weakref

import pytest
import sqlglot
from sqlglot.optimizer.scope import build_scope

from auto_bi.agent import sql_guard
from auto_bi.agent.sql_guard import (
    AmbiguousRef,
    ColumnRef,
    TableRef,
    clickhouse_has_reachable_column_selector,
    clickhouse_has_reachable_star,
    extract_column_refs,
)


@pytest.mark.parametrize(
    "sql,expected",
    [
        ("WITH tuple(*) AS dm SELECT dm.t.x FROM dm.t", False),
        ("WITH tuple(*) AS dm SELECT dm.t.x FROM other CROSS JOIN dm.t", False),
        ("WITH tuple(*) AS dm SELECT dm.t.x FROM (SELECT 1) CROSS JOIN dm.t", False),
        ("WITH tuple(*) AS t SELECT t.x FROM t", True),
        ("WITH tuple(*) AS dm SELECT dm.t.x FROM dm.t AS t", True),
        ("WITH tuple(*) AS dm SELECT dm.t.x FROM cat.dm.t", True),
        ("WITH tuple(*) AS t SELECT t.x FROM dm.a AS t CROSS JOIN dm.b AS t", True),
        ("WITH tuple(*) AS dm SELECT dm.t.x FROM dm.t CROSS JOIN dm.t AS t2", True),
        ("WITH tuple(*) AS dm SELECT dm.t.x FROM dm.t CROSS JOIN other AS dm", True),
        ("WITH tuple(*) AS cat SELECT cat.dm.t.x FROM dm.t", True),
        (
            "WITH tuple(*) AS dm SELECT dm.t.x FROM dm.t CROSS JOIN dm.{t:Identifier} AS other",
            False,
        ),
    ],
    ids=[
        "qualified-source",
        "joined-source",
        "unnamed-source-before-join",
        "implicit-table-name",
        "aliased-source",
        "catalog-source",
        "duplicate-alias",
        "duplicate-table-pair",
        "database-alias-collision",
        "catalog-column",
        "parameterized-table-is-not-named-source",
    ],
)
def test_scalar_gate_source_qualification(sql: str, expected: bool) -> None:
    # Only an unambiguous named source with the documented qualification wins
    # over a scalar WITH of the same name. Other tuple accesses stay conservative.
    assert clickhouse_has_reachable_star(sql) is expected


@pytest.mark.parametrize(
    "sql",
    [
        "WITH tuple(*) AS live SELECT dm.t.x, live FROM dm.t",
        "WITH tuple(*) AS live SELECT t.x, live FROM dm.t AS t",
        "WITH tuple(*) AS live SELECT (SELECT live) SETTINGS max_threads=1",
        'WITH tuple(*) AS live SELECT "", live',
        'WITH "" AS (SELECT 1), live AS (SELECT * FROM t) SELECT 1 FROM live',
    ],
    ids=["source-column", "aliased-column", "setting", "empty-column", "empty-cte"],
)
def test_skipped_reference_keeps_later_gate(sql: str) -> None:
    assert clickhouse_has_reachable_star(sql) is True


@pytest.mark.parametrize(
    "sql,expected",
    [
        (
            "WITH a AS (SELECT * FROM t) SELECT (WITH a AS (SELECT 1 FROM a) SELECT 1 FROM a)",
            True,
        ),
        ("WITH tuple(*) AS a SELECT (WITH a AS a SELECT a)", True),
        ("SELECT a UNION ALL (WITH tuple(*) AS a SELECT 1)", False),
        (
            "WITH a AS (SELECT 1) SELECT (WITH a AS (SELECT * FROM t) SELECT 1 FROM a)",
            True,
        ),
        ("WITH 1 AS a SELECT (WITH tuple(*) AS a SELECT a)", True),
        (
            "WITH tuple(*) AS secret, "
            "live AS (WITH dormant AS (SELECT secret) SELECT 1) SELECT 1 FROM live",
            False,
        ),
    ],
    ids=[
        "named-self-reference",
        "scalar-self-reference",
        "sibling-declaration",
        "nearest-named",
        "nearest-scalar",
        "all-enclosing-gates-required",
    ],
)
def test_nested_gate_visibility(sql: str, expected: bool) -> None:
    assert clickhouse_has_reachable_star(sql) is expected


@pytest.mark.parametrize(
    "source",
    ["dm.t", "cat..t"],
    ids=["database", "catalog"],
)
def test_qualified_table_does_not_open_named_gate(source: str) -> None:
    sql = f"WITH t AS (SELECT COLUMNS('x') FROM src) SELECT 1 FROM {source}"
    assert clickhouse_has_reachable_column_selector(sql) is False


@pytest.mark.parametrize(
    "source",
    ["dm.a", 'dm."".a', 'cat.""."".a'],
    ids=["table-part", "database-part", "catalog-part"],
)
def test_qualified_in_does_not_open_named_gate(source: str) -> None:
    # COLUMNS distinguishes gate reachability even though IN itself reads a star.
    sql = f"WITH a AS (SELECT COLUMNS('x') FROM t) SELECT 1 WHERE 1 IN {source}"
    assert clickhouse_has_reachable_column_selector(sql) is False


def test_qualified_in_keeps_later_bare_in_gate() -> None:
    sql = "WITH live AS (SELECT COLUMNS('x') FROM t) SELECT 1 WHERE 1 IN dm.a AND 1 IN live"
    assert clickhouse_has_reachable_column_selector(sql) is True


@pytest.mark.parametrize(
    "declarations,reader",
    [("r + 1 AS w", "w"), ("r + 1 AS w1, w1 + 1 AS w2", "w2")],
    ids=["direct", "transitive"],
)
def test_with_reaches_select_alias_cycle(declarations: str, reader: str) -> None:
    sql = f"WITH {declarations} SELECT revenue * 2 AS r, {reader} AS revenue FROM dm.sales_daily"
    reads = extract_column_refs(sql, dialect="clickhouse")
    assert reads.attributed == {
        ColumnRef(TableRef("dm.sales_daily", False), "r", False),
        ColumnRef(TableRef("dm.sales_daily", False), "revenue", False),
    }
    assert reads.unattributed == frozenset()
    assert reads.ambiguous == frozenset()


@pytest.mark.parametrize(
    "projections",
    ["w + a AS b, w AS a", "w AS a, w + a AS b"],
    ids=["owner-first", "reader-first"],
)
def test_with_reachability_cache_separates_alias_owners(projections: str) -> None:
    # In b's definition, WITH w reaches the physical b; in a's, it reaches
    # alias b. Sharing that cached result would hide the a <-> b dependency.
    reads = extract_column_refs(f"WITH b AS w SELECT {projections} FROM dm.t", dialect="clickhouse")
    assert reads.attributed == {
        ColumnRef(TableRef("dm.t", False), "a", False),
        ColumnRef(TableRef("dm.t", False), "b", False),
    }
    assert reads.unattributed == frozenset()
    assert reads.ambiguous == frozenset()


@pytest.mark.parametrize(
    "sql,columns",
    [
        (
            "WITH r AS left_w, 1 AS shared, shared + shared AS right_w, "
            "left_w + right_w AS start SELECT revenue AS r, start AS revenue "
            "FROM dm.sales_daily",
            {"r", "revenue"},
        ),
        (
            "WITH r AS w0, w0 AS w1 SELECT revenue AS r, w0 + w1 AS revenue FROM dm.sales_daily",
            {"r", "revenue"},
        ),
        (
            "WITH r AS left_w, r2 AS w0, left_w + w0 AS start "
            "SELECT w0 + start AS revenue, revenue AS r, revenue AS r2 "
            "FROM dm.sales_daily",
            {"r", "r2", "revenue"},
        ),
        (
            "WITH t.x + r AS w SELECT revenue AS r, w AS revenue FROM dm.sales_daily t",
            {"r", "revenue", "x"},
        ),
        (
            "WITH revenue + r AS w SELECT revenue AS r, w AS revenue FROM dm.sales_daily",
            {"r", "revenue"},
        ),
        (
            "WITH r AS inner_w, inner_w + r2 AS start "
            "SELECT revenue AS r, revenue AS r2, start AS revenue FROM dm.sales_daily",
            {"r", "r2", "revenue"},
        ),
        (
            "WITH w2 + r AS w1, w1 AS w2 SELECT revenue AS r, w1 AS revenue FROM dm.sales_daily",
            {"r", "revenue", "w1"},
        ),
    ],
    ids=[
        "revisited-branch",
        "cached-body",
        "cached-branch-before-live-branch",
        "physical-column-before-alias",
        "owner-column-before-alias",
        "with-alias-before-select-alias",
        "cyclic-with",
    ],
)
def test_with_walk_keeps_all_references(sql: str, columns: set[str]) -> None:
    reads = extract_column_refs(sql, dialect="clickhouse")
    assert reads.attributed == {
        ColumnRef(TableRef("dm.sales_daily", False), column, False) for column in columns
    }
    assert reads.unattributed == frozenset()
    assert reads.ambiguous == frozenset()


def test_with_reachability_keeps_branch_scope_visibility() -> None:
    # The nested SELECT is expanded twice, under different visible WITH aliases.
    sql = (
        "WITH w AS body, 1 + (SELECT b AS a, body AS b FROM dm.inner_t) AS f "
        "SELECT (WITH 1 AS w SELECT f FROM dm.one) AS q1, "
        "(WITH a AS w SELECT f FROM dm.two) AS q2 FROM dm.sales_daily"
    )
    reads = extract_column_refs(sql, dialect="clickhouse")
    assert reads.attributed == frozenset()
    assert reads.unattributed == {"a", "b"}
    assert reads.ambiguous == {
        AmbiguousRef(name, False, (TableRef("dm.inner_t", False),), True, enclosed=True)
        for name in ("a", "b")
    }


def test_with_reachability_without_query_level_aliases() -> None:
    # The helper documents aliases at the supplied scope's level. A UNION has
    # WITH declarations but no query-level SELECT aliases of its own.
    root = sqlglot.parse_one(
        "WITH (SELECT 1) AS sub, sub AS start SELECT start UNION ALL SELECT 1",
        dialect="clickhouse",
    )
    scope = build_scope(root)
    assert scope is not None
    aliases = sql_guard._clickhouse_aliases(root)
    start = aliases.with_[id(root)]["start"]
    assert sql_guard._select_aliases_reached_from_with(start, scope, aliases, ()) == frozenset()


def test_with_reachability_retains_memoized_scope() -> None:
    # The alias index documents keeping scopes alive so id-based memo keys stay
    # unique. Check that lifetime, without depending on the storage container.
    root = sqlglot.parse_one("WITH r AS w SELECT 1 AS r", dialect="clickhouse")
    scope = build_scope(root)
    assert scope is not None
    aliases = sql_guard._clickhouse_aliases(root)
    scope_ref = weakref.ref(scope)
    assert sql_guard._select_aliases_reached_from_with(
        aliases.with_[id(root)]["w"], scope, aliases, ()
    ) == {"r"}
    del scope
    gc.collect()
    assert scope_ref() is not None


@pytest.mark.parametrize("reader", ["w0", "w1"], ids=["same-body", "transitive-body"])
def test_with_reachability_memoizes_declarations(
    monkeypatch: pytest.MonkeyPatch, reader: str
) -> None:
    # _select_aliases_reached_from_with explicitly promises memoisation per
    # (declaration, scope). Reusing a completed declaration must not rescan it.
    root = sqlglot.parse_one("WITH r AS w0, w0 AS w1 SELECT 1 AS r", dialect="clickhouse")
    scope = build_scope(root)
    assert scope is not None
    aliases = sql_guard._clickhouse_aliases(root)
    declarations = aliases.with_[id(root)]
    cached = declarations["w0"]
    assert sql_guard._select_aliases_reached_from_with(cached, scope, aliases, ()) == {"r"}
    loose_columns = sql_guard._loose_columns

    def reject_rescan(node: sqlglot.exp.Expression) -> list[sqlglot.exp.Column]:
        assert node is not cached.definition, "a memoised WITH body must not be scanned again"
        return loose_columns(node)

    monkeypatch.setattr(sql_guard, "_loose_columns", reject_rescan)
    assert sql_guard._select_aliases_reached_from_with(
        declarations[reader], scope, aliases, ()
    ) == {"r"}
