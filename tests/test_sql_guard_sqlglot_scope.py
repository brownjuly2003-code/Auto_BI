"""What `sql_guard` relies on in sqlglot's private scope API (`_subquery_scopes`).

`_subquery_scopes` is the one call of `_traverse_scope` over `Scope.branch`. If a sqlglot
upgrade changes either, these tests fail before the column grading built on them does.
The last two tests pin the gaps the two callers fill: when sqlglot starts building
those scopes itself, the workaround in the caller can go.
"""

import sqlglot
from sqlglot import expressions as exp
from sqlglot.optimizer.scope import Scope, ScopeType, build_scope

from auto_bi.agent.sql_guard import _subquery_scopes, extract_column_refs

SET_OPERATION = "SELECT a FROM t UNION ALL SELECT a FROM u LIMIT (SELECT max(v.n) FROM v)"


def _root(sql: str, dialect: str) -> tuple[exp.Expression, Scope]:
    root = sqlglot.parse_one(sql, read=dialect)
    scope = build_scope(root)
    assert scope is not None
    return root, scope


def _limit_subquery(root: exp.Expression) -> exp.Subquery:
    limit = root.args["limit"]
    query = limit.expression
    assert isinstance(query, exp.Subquery)
    return query


def test_a_wrapping_subquery_is_unwrapped_into_a_subquery_scope_of_the_parent() -> None:
    root, parent = _root(SET_OPERATION, "postgres")
    query = _limit_subquery(root)

    scopes = _subquery_scopes(parent, query)

    assert len(scopes) == 1
    own = scopes[0]
    assert own.expression is query.this
    assert isinstance(own.expression, exp.Select)
    assert own.scope_type is ScopeType.SUBQUERY
    assert own.parent is parent
    assert sorted(own.sources) == ["v"]
    assert isinstance(own.sources["v"], exp.Table)


def test_a_bare_name_the_query_does_not_resolve_is_an_external_column() -> None:
    root, parent = _root(
        "SELECT a FROM t UNION ALL SELECT a FROM u LIMIT (SELECT max(v.n) + k FROM v)",
        "postgres",
    )

    (own,) = _subquery_scopes(parent, _limit_subquery(root))

    assert sorted(column.sql() for column in own.columns) == ["k", "v.n"]
    assert [column.sql() for column in own.external_columns] == ["k"]


def test_scopes_nested_in_the_query_come_before_it() -> None:
    root, parent = _root(
        "SELECT a FROM t UNION ALL SELECT a FROM u "
        "LIMIT (SELECT max(d.n) FROM (SELECT n FROM v) AS d)",
        "postgres",
    )
    query = _limit_subquery(root)

    derived, own = _subquery_scopes(parent, query)

    assert own.expression is query.this
    assert own.parent is parent
    assert derived.scope_type is ScopeType.DERIVED_TABLE
    assert derived.parent is own
    assert sorted(derived.sources) == ["v"]
    assert own.sources["d"] is derived


def test_cte_names_visible_to_the_parent_stay_visible() -> None:
    _, parent = _root("WITH c AS (SELECT n FROM v) SELECT a FROM c", "postgres")
    cte_scope = parent.cte_sources["c"]
    query = sqlglot.parse_one("SELECT max(n) FROM c", read="postgres")

    (own,) = _subquery_scopes(parent, query)

    assert own.parent is parent
    assert own.sources["c"] is cte_scope


def test_sqlglot_still_skips_subqueries_in_set_operation_modifiers() -> None:
    """`extract_column_refs` grades `LIMIT (SELECT …)` of a set operation itself."""
    root, parent = _root(SET_OPERATION, "postgres")
    query = _limit_subquery(root).this

    assert all(scope.expression is not query for scope in parent.traverse())
    reads = extract_column_refs(SET_OPERATION, dialect="postgres")
    assert ("v", "n") in {(ref.table.name, ref.name) for ref in reads.attributed}


def test_sqlglot_still_builds_no_scope_for_a_query_in_an_expression_form_with() -> None:
    """`_grade_nested_query` grades a query nested in `WITH <expr> AS a` itself."""
    sql = "WITH 1 + (SELECT max(v.n) FROM v) AS s SELECT s FROM t"
    root, parent = _root(sql, "clickhouse")
    nested = next(select for select in root.find_all(exp.Select) if select is not root)

    assert all(scope.expression is not nested for scope in parent.traverse())
    reads = extract_column_refs(sql, dialect="clickhouse")
    assert ("v", "n") in {(ref.table.name, ref.name) for ref in reads.attributed}
