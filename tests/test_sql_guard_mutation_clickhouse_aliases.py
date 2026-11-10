"""Short ClickHouse alias regressions and documented graph/cache contracts."""

import gc
import weakref

import pytest
import sqlglot
from sqlglot.optimizer.scope import build_scope

from auto_bi.agent import sql_guard
from auto_bi.agent.sql_guard import ColumnRef, TableRef, extract_column_refs, extract_table_refs


def test_inline_alias_keeps_breadth_first_precedence() -> None:
    # The shallower WHERE declaration wins over the deeper inline SELECT alias.
    reads = extract_column_refs("SELECT ((b AS a) + 1) + 1, a AS b FROM dm.t WHERE (1 AS a) > 0")
    assert not reads.attributed
    assert not reads.ambiguous


def test_in_table_fields_accepts_attached_query_subtree() -> None:
    root = sqlglot.parse_one("SELECT (SELECT 1 WHERE 1 IN dm.t)", dialect="clickhouse")
    subquery = root.find(sqlglot.exp.Subquery)
    assert subquery is not None
    fields = sql_guard.clickhouse_in_table_fields(subquery.this)
    assert tuple(field.sql(dialect="clickhouse") for field in fields) == ("dm.t",)


def test_empty_alias_keeps_nested_declaration() -> None:
    reads = extract_column_refs('SELECT ((x AS a) + 1) AS "", a FROM dm.t')
    assert reads.attributed == {ColumnRef(TableRef("dm.t", False), "x", False)}
    assert not reads.ambiguous


def test_empty_projection_alias_keeps_later_declaration() -> None:
    reads = extract_column_refs('SELECT x AS "", y AS a, a FROM dm.t')
    assert reads.attributed == {
        ColumnRef(TableRef("dm.t", False), name, False) for name in ("x", "y")
    }
    assert not reads.ambiguous


@pytest.mark.parametrize("first", ["unused AS (SELECT 1)", 'x AS ""'])
def test_with_keeps_later_scalar_alias(first: str) -> None:
    reads = extract_column_refs(f"WITH {first}, y AS a SELECT a FROM dm.t")
    assert reads.attributed == {ColumnRef(TableRef("dm.t", False), "y", False)}
    assert not reads.ambiguous


def test_nested_alias_expression_keeps_enclosing_owner() -> None:
    reads = extract_column_refs("WITH a AS w SELECT (w + 1 AS b) + b AS a FROM dm.t")
    assert reads.attributed == {ColumnRef(TableRef("dm.t", False), "a", False)}
    assert not reads.ambiguous


def test_projection_star_exclusion_is_not_a_field_read() -> None:
    reads = extract_column_refs("SELECT * EXCEPT (x) AS a FROM dm.t")
    assert reads.starred
    assert not reads.attributed
    assert not reads.ambiguous


def test_in_table_keeps_catalog_and_quoting() -> None:
    assert extract_table_refs('SELECT 1 IN "Cat"."Db"."T"') == {TableRef("Cat.Db.T", True)}


@pytest.mark.parametrize(
    "sql, names",
    [
        ("SELECT x + b AS a, a AS b FROM dm.t", {"x", "a", "b"}),
        ("WITH (SELECT 1) AS a SELECT a + b AS a, a AS b FROM dm.t", {"a", "b"}),
        ("WITH (SELECT 1) AS w SELECT w + b AS a, a AS b FROM dm.t", {"a", "b"}),
    ],
    ids=["physical-column", "owner-with-query", "other-with-query"],
)
def test_graph_keeps_edge_after_skipped_column(sql: str, names: set[str]) -> None:
    reads = extract_column_refs(sql)
    assert reads.attributed == {ColumnRef(TableRef("dm.t", False), name, False) for name in names}
    assert not reads.ambiguous


def test_graph_keeps_unseen_edge_in_every_hash_order() -> None:
    # Both WITH walks insert a then b into the same two-name set. In one query
    # a is already seen and b closes a cycle; in the other their roles swap.
    # Whichever name the frozenset yields first, continue->break loses the
    # cyclic edge in one case. Keep both cases in one test for mutmut selection.
    for projections, names in (
        ("a + w AS c, 1 AS a, c AS b", {"b", "c"}),
        ("b + w AS c, c AS a, 1 AS b", {"a", "c"}),
    ):
        reads = extract_column_refs(f"WITH a + b AS w SELECT {projections} FROM dm.t")
        assert reads.attributed == {
            ColumnRef(TableRef("dm.t", False), name, False) for name in names
        }
        assert not reads.ambiguous


def test_disjoint_alias_cycles_are_all_read() -> None:
    reads = extract_column_refs("SELECT b AS a, a AS b, d AS c, c AS d FROM dm.t")
    assert reads.attributed == {
        ColumnRef(TableRef("dm.t", False), name, False) for name in ("a", "b", "c", "d")
    }
    assert not reads.ambiguous


def test_with_self_name_reads_definition_quoting() -> None:
    # The unquoted name in the definition is the physical read. Referencing
    # that WITH again with quotes must not turn it into a quoted physical read.
    reads = extract_column_refs('WITH a AS a SELECT a, "a" FROM dm.t')
    assert reads.attributed == {ColumnRef(TableRef("dm.t", False), "a", False)}
    assert not reads.ambiguous


def test_lateral_parent_without_aliases_keeps_source_read() -> None:
    reads = extract_column_refs("SELECT 1 FROM dm.t, LATERAL (SELECT x) AS sub")
    assert reads.attributed == {ColumnRef(TableRef("dm.t", False), "x", False)}
    assert not reads.ambiguous


def test_with_cycle_does_not_mark_preceding_alias() -> None:
    reads = extract_column_refs("WITH b AS head, c AS b, b AS c SELECT head, head FROM dm.t")
    assert reads.attributed == {ColumnRef(TableRef("dm.t", False), "b", False)}
    assert not reads.ambiguous


def test_union_has_no_query_level_alias_graph() -> None:
    # These helpers describe the aliases at the supplied scope's level. The
    # UNION's branches have SELECT aliases, but the UNION itself has none.
    root = sqlglot.parse_one("SELECT 1 AS a UNION ALL SELECT 2 AS b", dialect="clickhouse")
    scope = build_scope(root)
    assert scope is not None
    aliases = sql_guard._clickhouse_aliases(root)
    assert sql_guard._select_alias_graph(scope, aliases) == {}
    assert sql_guard._select_aliases_on_cycle(scope, aliases) == frozenset()


def test_select_cycle_cache_holds_scope_alive() -> None:
    # _select_aliases_on_cycle promises to hold the cached Scope alive so its
    # identity cannot be reused by a later read-through branch.
    root = sqlglot.parse_one("SELECT 1 AS a", dialect="clickhouse")
    scope = build_scope(root)
    assert scope is not None
    reference = weakref.ref(scope)
    aliases = sql_guard._clickhouse_aliases(root)
    assert sql_guard._select_aliases_on_cycle(scope, aliases) == frozenset()
    del scope
    gc.collect()
    assert reference() is not None


@pytest.mark.parametrize(
    "projections, expected",
    [("1 AS a", frozenset()), ("b AS a, a AS b", frozenset({"a", "b"}))],
    ids=["empty", "cycle"],
)
def test_select_cycle_result_is_cached(
    projections: str, expected: frozenset[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The helper promises caching; the index documents select_on_cycle as the
    # lazily filled SCC result per Scope identity, including an empty result.
    root = sqlglot.parse_one(f"SELECT {projections}", dialect="clickhouse")
    scope = build_scope(root)
    assert scope is not None
    aliases = sql_guard._clickhouse_aliases(root)
    assert sql_guard._select_aliases_on_cycle(scope, aliases) == expected
    assert aliases.select_on_cycle[id(scope)] == expected

    def reject_rescan(*args: object) -> None:
        pytest.fail("a cached cycle result must not rebuild the alias graph")

    monkeypatch.setattr(sql_guard, "_select_alias_graph", reject_rescan)
    assert sql_guard._select_aliases_on_cycle(scope, aliases) == expected


def test_with_cycle_state_holds_scope_alive() -> None:
    # _ReadThroughState documents retaining the Scope for cyclic declarations,
    # preventing an inner-only cycle from contaminating a later scope's reads.
    root = sqlglot.parse_one("WITH b AS a, a AS b SELECT a", dialect="clickhouse")
    scope = build_scope(root)
    assert scope is not None
    reference = weakref.ref(scope)
    aliases = sql_guard._clickhouse_aliases(root)
    declarations = aliases.with_[id(root)]
    state = sql_guard._ReadThroughState()
    state.path.extend((declarations["a"], declarations["b"]))
    sql_guard._mark_cyclic(state, declarations["a"], scope)
    del scope
    gc.collect()
    assert reference() is not None
