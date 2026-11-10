"""Short alias-read regressions and documented read-through state contracts."""

import gc
import weakref

import pytest
import sqlglot
from sqlglot.optimizer.scope import build_scope

from auto_bi.agent import sql_guard
from auto_bi.agent.sql_guard import AmbiguousRef, ColumnRef, TableRef, extract_column_refs


def test_grouping_table_alias_keeps_later_read() -> None:
    reads = extract_column_refs("SELECT x AS a FROM dm.t AS t(a) GROUP BY a, z", dialect="postgres")
    assert reads.attributed == {
        ColumnRef(TableRef("dm.t", False), "x", False),
        ColumnRef(TableRef("dm.t", False), "z", False),
    }
    assert reads.ambiguous == {
        AmbiguousRef("a", False, (TableRef("dm.t", False),), True, postgres_table_column_alias=True)
    }
    assert reads.unattributed == {"a"}


@pytest.mark.parametrize("name, quoted", [("a", False), ('"A"', True)])
def test_grouping_output_preserves_quoting(name: str, quoted: bool) -> None:
    reads = extract_column_refs(f"SELECT x AS {name} FROM dm.t GROUP BY {name}", dialect="postgres")
    assert reads.ambiguous == {
        AmbiguousRef(name.strip('"'), quoted, (TableRef("dm.t", False),), True, output_column=True)
    }


def test_nested_column_keeps_later_outer_read() -> None:
    reads = extract_column_refs("SELECT (SELECT x) FROM dm.t HAVING y > 0", dialect="postgres")
    assert reads.attributed == {ColumnRef(TableRef("dm.t", False), "y", False)}
    assert reads.ambiguous == {AmbiguousRef("x", False, (), True, enclosed=True)}


def test_setting_keeps_later_having_read() -> None:
    reads = extract_column_refs("SELECT 1 FROM dm.t HAVING y > 0 SETTINGS max_threads=1")
    assert reads.attributed == {ColumnRef(TableRef("dm.t", False), "y", False)}
    assert not reads.ambiguous


def test_inline_owner_adds_omitted_sort_read() -> None:
    reads = extract_column_refs("SELECT 1 AS a FROM dm.t ORDER BY (b + a AS a)")
    assert reads.attributed == {
        ColumnRef(TableRef("dm.t", False), name, False) for name in ("a", "b")
    }
    assert not reads.ambiguous


@pytest.mark.parametrize("source", ["dm.t", "dm.t CROSS JOIN dm.u"])
def test_cyclic_aliases_keep_each_reference(source: str) -> None:
    reads = extract_column_refs(f"WITH b AS a, a AS b SELECT a, b FROM {source}")
    if source == "dm.t":
        assert reads.attributed == {
            ColumnRef(TableRef("dm.t", False), name, False) for name in ("a", "b")
        }
        assert not reads.ambiguous
    else:
        assert not reads.attributed
        assert reads.ambiguous == {
            AmbiguousRef(name, False, (TableRef("dm.t", False), TableRef("dm.u", False)), True)
            for name in ("a", "b")
        }


def test_with_cache_separates_owners() -> None:
    reads = extract_column_refs("WITH x AS a SELECT a, a AS x FROM dm.t")
    assert reads.attributed == {ColumnRef(TableRef("dm.t", False), "x", False)}
    assert not reads.ambiguous


def test_nested_with_keeps_owner() -> None:
    reads = extract_column_refs("WITH x AS a, a AS b SELECT b AS x FROM dm.t")
    assert reads.attributed == {ColumnRef(TableRef("dm.t", False), "x", False)}
    assert not reads.ambiguous


def test_owner_column_keeps_following_read() -> None:
    reads = extract_column_refs("WITH x + y AS a SELECT a AS x FROM dm.t CROSS JOIN dm.u")
    assert not reads.attributed
    assert reads.ambiguous == {
        AmbiguousRef(name, False, (TableRef("dm.t", False), TableRef("dm.u", False)), True)
        for name in ("x", "y")
    }


def test_with_nested_query_reads_own_table() -> None:
    reads = extract_column_refs("WITH (SELECT t.x FROM dm.t AS t) + 1 AS a SELECT a")
    assert reads.attributed == {ColumnRef(TableRef("dm.t", False), "x", False)}
    assert not reads.ambiguous


def test_depth_cut_can_be_read_later(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sql_guard, "CLICKHOUSE_ALIAS_READ_THROUGH_DEPTH", 2)
    reads = extract_column_refs("WITH x AS a, a AS b, b AS c SELECT c, b FROM dm.t")
    assert reads.attributed == {ColumnRef(TableRef("dm.t", False), "x", False)}
    assert reads.ambiguous == {AmbiguousRef("a", False, (), False)}


def test_budget_counts_each_expansion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sql_guard, "CLICKHOUSE_ALIAS_READ_THROUGH_BUDGET", 2)
    reads = extract_column_refs('WITH x AS a, y AS b, z AS "C" SELECT a, b, "C" FROM dm.t')
    assert reads.attributed == {
        ColumnRef(TableRef("dm.t", False), name, False) for name in ("x", "y")
    }
    assert reads.ambiguous == {AmbiguousRef("C", True, (), False)}


def test_completed_alias_reuses_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sql_guard, "CLICKHOUSE_ALIAS_READ_THROUGH_BUDGET", 2)
    reads = extract_column_refs("WITH x AS a, y AS b SELECT a, a, b FROM dm.t")
    assert reads.attributed == {
        ColumnRef(TableRef("dm.t", False), name, False) for name in ("x", "y")
    }
    assert not reads.ambiguous


@pytest.mark.parametrize(
    "clauses",
    ["WHERE (a AS q) > 0 HAVING c > 0", "HAVING (a AS q) > 0 QUALIFY c > 0"],
    ids=["where-having", "having-qualify"],
)
def test_omitted_columns_spend_budget_once(clauses: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # A cut expansion cannot be memoised. Grading the same AST column twice
    # would spend the remaining budget before the later independent alias c.
    monkeypatch.setattr(sql_guard, "CLICKHOUSE_ALIAS_READ_THROUGH_DEPTH", 1)
    monkeypatch.setattr(sql_guard, "CLICKHOUSE_ALIAS_READ_THROUGH_BUDGET", 2)
    reads = extract_column_refs(f"WITH b AS a, x AS b, y AS c SELECT 1 FROM dm.t {clauses}")
    assert reads.attributed == {ColumnRef(TableRef("dm.t", False), "y", False)}
    assert reads.ambiguous == {AmbiguousRef("b", False, (), False)}


@pytest.mark.parametrize("limit", [0, 1])
def test_grade_reports_whether_expansion_was_cut(
    limit: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    # _grade_columns promises a boolean saying whether this walk hit a cap.
    monkeypatch.setattr(sql_guard, "CLICKHOUSE_ALIAS_READ_THROUGH_DEPTH", limit)
    root = sqlglot.parse_one("WITH x AS a SELECT a FROM dm.t", dialect="clickhouse")
    scope = build_scope(root)
    assert scope is not None
    scopes = list(scope.traverse())
    attributed: set[ColumnRef] = set()
    ambiguous: set[AmbiguousRef] = set()
    cut = sql_guard._grade_columns(
        scopes,
        {id(item.expression): item for item in scopes},
        sql_guard._clickhouse_aliases(root),
        attributed,
        ambiguous,
        sql_guard._ReadThroughState(),
        None,
        None,
    )
    assert cut is (limit == 0)
    if limit == 0:
        assert ambiguous == {AmbiguousRef("a", False, (), False)}
    else:
        assert attributed == {ColumnRef(TableRef("dm.t", False), "x", False)}


@pytest.mark.parametrize(
    "sql, limit, expected",
    [
        ("WITH x AS a SELECT a FROM dm.t", 1, False),
        ("WITH x AS a SELECT a FROM dm.t", 0, True),
        ("WITH x AS b, b AS a SELECT a FROM dm.t", 1, True),
        ("WITH x AS b, (SELECT b) + 1 AS a SELECT a FROM dm.t", 1, True),
        ("SELECT x AS a, a FROM dm.t", 1, False),
    ],
    ids=["complete", "cut", "cut-child", "cut-subquery", "select-alias"],
)
def test_read_through_reports_cut(
    sql: str, limit: int, expected: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    # _read_through_alias documents both its return value and that a truncated
    # expansion must not be memoised as complete.
    monkeypatch.setattr(sql_guard, "CLICKHOUSE_ALIAS_READ_THROUGH_DEPTH", limit)
    root = sqlglot.parse_one(sql, dialect="clickhouse")
    scope = build_scope(root)
    assert scope is not None
    aliases = sql_guard._clickhouse_aliases(root)
    alias = (aliases.with_[id(root)] or aliases.select[id(root)])["a"]
    column = root.expressions[-1]
    state = sql_guard._ReadThroughState()
    cut = sql_guard._read_through_alias(alias, column, scope, aliases, set(), set(), state, ())
    assert cut is expected
    if expected:
        assert not state.done


@pytest.mark.parametrize("cached", ["done", "on-path", "cyclic"])
def test_reused_alias_does_not_report_cut(cached: str) -> None:
    # A memoised expansion and a cycle are not a depth/budget cut. Cycles read
    # the reference as a column in this scope, as the helper docstring states.
    root = sqlglot.parse_one("WITH x AS a SELECT a FROM dm.t", dialect="clickhouse")
    scope = build_scope(root)
    assert scope is not None
    aliases = sql_guard._clickhouse_aliases(root)
    alias = aliases.with_[id(root)]["a"]
    state = sql_guard._ReadThroughState()
    ident = id(alias.definition)
    if cached == "done":
        assert (
            sql_guard._read_through_alias(
                alias, root.expressions[0], scope, aliases, set(), set(), state, ()
            )
            is False
        )
    elif cached == "on-path":
        state.path.append(alias)
        state.on_path.add(ident)
    else:
        state.cyclic[(ident, id(scope))] = scope
    attributed: set[ColumnRef] = set()
    cut = sql_guard._read_through_alias(
        alias, root.expressions[0], scope, aliases, attributed, set(), state, ()
    )
    assert cut is False
    assert attributed == (
        set() if cached == "done" else {ColumnRef(TableRef("dm.t", False), "a", False)}
    )


def test_completed_expansion_holds_scope_alive() -> None:
    # _ReadThroughState documents that done retains Scope objects, preventing
    # a collected branch's id from being reused to suppress another read.
    root = sqlglot.parse_one("WITH x AS a SELECT a FROM dm.t", dialect="clickhouse")
    scope = build_scope(root)
    assert scope is not None
    reference = weakref.ref(scope)
    aliases = sql_guard._clickhouse_aliases(root)
    alias = aliases.with_[id(root)]["a"]
    state = sql_guard._ReadThroughState()
    assert (
        sql_guard._read_through_alias(
            alias, root.expressions[0], scope, aliases, set(), set(), state, ()
        )
        is False
    )
    del scope
    gc.collect()
    assert reference() is not None
