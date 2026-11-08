"""PostgreSQL ORDER BY placement, GROUP BY output names and set-operation modifiers.

Each case tells a surviving mutant of `sql_guard` apart from the reviewed code (slice S8).
"""

import pytest
import sqlglot
from sqlglot import expressions as exp

from auto_bi.agent import sql_guard
from auto_bi.agent.sql_guard import SQLGuardError, extract_column_refs, guard_sql


def _reads(sql: str) -> tuple[set[str], set[str]]:
    refs = extract_column_refs(sql, dialect="postgres")
    attributed = {f"{ref.table.name}.{ref.name}" for ref in refs.attributed}
    return attributed, set(refs.unattributed)


def _moved(sql: str) -> str:
    root = sqlglot.parse_one(sql, read="postgres")
    sql_guard._rewrite_postgres_parenthesized_orders(root)
    return root.sql(dialect="postgres")


ALIASED_FROM_ITEM = (
    "SELECT d.r FROM (((SELECT region AS r FROM dm.sales_daily) AS d1) ORDER BY lower(r)) AS d"
)


@pytest.mark.parametrize(
    "sql, expected",
    [
        (
            "(SELECT region AS r FROM dm.sales_daily) ORDER BY lower(r)",
            "(SELECT region AS r FROM dm.sales_daily ORDER BY LOWER(r))",
        ),
        (
            "((SELECT region AS r FROM dm.sales_daily)) ORDER BY lower(r)",
            "((SELECT region AS r FROM dm.sales_daily ORDER BY LOWER(r)))",
        ),
        (
            "((SELECT region AS r FROM dm.sales_daily) ORDER BY r) ORDER BY lower(r)",
            "((SELECT region AS r FROM dm.sales_daily ORDER BY r)) ORDER BY LOWER(r)",
        ),
        (ALIASED_FROM_ITEM, ALIASED_FROM_ITEM.replace("lower(r)", "LOWER(r)")),
    ],
    ids=["moved-not-copied", "through-bare-parentheses", "second-order-stays", "alias-stops"],
)
def test_a_trailing_order_by_moves_onto_the_query_in_the_parentheses(
    sql: str, expected: str
) -> None:
    # PostgreSQL's grammar attaches an ORDER BY written after parentheses to the query
    # inside them, through any number of bare parentheses, and refuses a second ORDER BY.
    # An aliased subquery is a FROM item, not a query body, so the walk stops at it.
    assert _moved(sql) == expected


def test_a_second_order_by_is_rejected() -> None:
    # PostgreSQL: "multiple ORDER BY clauses not allowed". The second ORDER BY stays on its
    # wrapper, and the guard refuses the wrapper as the root.
    with pytest.raises(SQLGuardError):
        guard_sql(
            "((SELECT region AS r FROM dm.sales_daily) ORDER BY r) ORDER BY lower(r)",
            dialect="postgres",
        )


def test_an_order_by_after_double_parentheses_reads_through_them() -> None:
    sql = "SELECT d.r FROM (((SELECT region AS r FROM dm.sales_daily)) ORDER BY lower(r)) AS d"
    # `lower(r)` is an expression, so `r` is the input column, not the output alias.
    assert _reads(sql) == ({"dm.sales_daily.region", "dm.sales_daily.r"}, set())


@pytest.mark.parametrize(
    "sql, attributed",
    [
        (
            "SELECT category, region AS r FROM dm.sales_daily GROUP BY category, r",
            {"dm.sales_daily.category", "dm.sales_daily.region"},
        ),
        (
            "SELECT category AS x, region AS r FROM dm.sales_daily GROUP BY category, r",
            {"dm.sales_daily.category", "dm.sales_daily.region"},
        ),
        (
            "SELECT r AS r, region AS r FROM dm.sales_daily GROUP BY r",
            {"dm.sales_daily.r", "dm.sales_daily.region"},
        ),
        (
            "SELECT coalesce((SELECT max(v.n) FROM v), 0) AS r FROM dm.sales_daily GROUP BY r",
            {"v.n"},
        ),
    ],
    ids=["after-a-bare-column", "after-another-alias", "after-a-same-name-column", "nested-agg"],
)
def test_a_group_by_name_any_valid_output_alias_supplies_stays_undecided(
    sql: str, attributed: set[str]
) -> None:
    # Every output alias is weighed, not only the first: a bare column, an alias of another
    # name or an output that only repeats its own column does not end the search. An
    # aggregate inside a nested query does not make the output itself an aggregate.
    assert _reads(sql) == (attributed, {"r"})


def test_a_group_by_name_only_an_aggregate_output_offers_is_the_table_column() -> None:
    # An aggregate cannot be a grouping key, however deep in the output it sits.
    sql = "SELECT (sum(revenue) + 1) * 2 AS r FROM dm.sales_daily GROUP BY r"
    assert _reads(sql) == ({"dm.sales_daily.revenue", "dm.sales_daily.r"}, set())


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT $$it's$$ AS x, region FROM dm.sales_daily",
        r"SELECT E'\'' AS x, region FROM dm.sales_daily",
    ],
    ids=["dollar-quoted", "escape-string"],
)
def test_postgres_strings_are_tokenized_as_postgres(sql: str) -> None:
    # The sort-token pass reads the statement with the PostgreSQL tokenizer; the default
    # one would end these strings early and fail on the rest.
    assert _reads(sql) == ({"dm.sales_daily.region"}, set())


def test_a_bare_cast_ending_the_statement_after_a_plus_is_a_column() -> None:
    # sqlglot reads a bare `cast` as a column; the look-ahead for `CAST(` after a unary plus
    # stops at the last token instead of reading past it.
    sql = "SELECT a FROM t WHERE b = +cast"
    assert _reads(sql) == ({"t.a", "t.b", "t.cast"}, set())


@pytest.mark.parametrize(
    "sql, attributed",
    [
        ("SELECT a FROM t GROUP BY +", {"t.a"}),
        ("SELECT a FROM t WHERE b = 1 ORDER BY a, +", {"t.a", "t.b"}),
    ],
    ids=["group-by", "order-by"],
)
def test_a_plus_ending_the_statement_is_not_read_past(sql: str, attributed: set[str]) -> None:
    # sqlglot 30.14.0 drops a `+` that ends a GROUP BY or ORDER BY list; the search for the
    # token the plus applies to stops at the end of the statement.
    assert _reads(sql) == (attributed, set())


@pytest.mark.parametrize(
    "sql, attributed",
    [
        ("SELECT MAX(a) FROM t ORDER BY max", {"t.a"}),
        ("SELECT ROW_NUMBER() OVER () FROM t ORDER BY row_number", set()),
    ],
    ids=["aggregate", "row-number"],
)
def test_an_unquoted_function_name_folds_to_its_output_name(sql: str, attributed: set[str]) -> None:
    # Only a quoted spelling keeps its case; `MAX(a)` is the output column `max`, so
    # `ORDER BY max` sorts by it and reads no column of that name.
    assert _reads(sql) == (attributed, set())


@pytest.mark.parametrize(
    "modifier",
    ["ORDER BY (SELECT max(v.n) FROM v)", "FETCH FIRST (SELECT max(v.n) FROM v) ROWS ONLY"],
    ids=["order-by", "fetch-first"],
)
def test_a_subquery_in_a_set_operation_modifier_is_graded(modifier: str) -> None:
    sql = f"SELECT a FROM t UNION ALL SELECT a FROM u {modifier}"
    assert _reads(sql) == ({"t.a", "u.a", "v.n"}, set())


def test_sqlglot_still_stores_a_set_operations_fetch_under_limit() -> None:
    # `extract_column_refs` also reads a set operation's `fetch` key, which sqlglot 30.14.0
    # never fills: FETCH FIRST lands under `limit`, graded above. Renaming that key changes
    # nothing until a sqlglot upgrade starts filling it, and then this test fails first.
    sql = "SELECT a FROM t UNION ALL SELECT a FROM u FETCH FIRST 3 ROWS ONLY"
    root = sqlglot.parse_one(sql, read="postgres")
    assert isinstance(root, exp.SetOperation)
    assert isinstance(root.args.get("limit"), exp.Fetch)
    assert root.args.get("fetch") is None
