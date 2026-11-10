"""ClickHouse `x IN <table>` is the table it names (T-14).

sqlglot 30.14.0 parses the right-hand side of `IN rev` / `IN dm.rev` as a Column
(`In.args["field"]`). ClickHouse reads it as `IN (SELECT * FROM <name>)`. A
visible alias on that side (T-13) stays an alias; a CTE of that name is the CTE.
PostgreSQL is unchanged. The function form `in(x, t)` does not parse.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlglot import exp
from sqlglot.optimizer.scope import Scope

from auto_bi.agent import sql_guard
from auto_bi.agent.sql_guard import (
    SQLGuardError,
    extract_column_refs,
    extract_table_refs,
)
from auto_bi.decision.impact import ChartRelation
from tests.test_decision_chart_impact_dictionary import charts, field_change, gaps
from tests.test_sql_guard_clickhouse_alias import (
    ALLOWED,
    attributed,
    unattributed,
    ungoverned_codes,
    ungoverned_detail,
    ungoverned_pairs,
)

HEAD_BARE = (
    "SELECT region, sum(revenue) AS m FROM dm.sales_daily WHERE region IN rev GROUP BY region"
)
HEAD_QUALIFIED = (
    "SELECT region, sum(revenue) AS m FROM dm.sales_daily WHERE region IN dm.rev GROUP BY region"
)
HEAD_SUBQUERY = (
    "SELECT region, sum(revenue) AS m FROM dm.sales_daily "
    "WHERE region IN (SELECT * FROM dm.rev) GROUP BY region"
)
HEAD_BARE_SUBQUERY = (
    "SELECT region, sum(revenue) AS m FROM dm.sales_daily "
    "WHERE region IN (SELECT * FROM rev) GROUP BY region"
)
SELECT_ALIAS_IN = "SELECT region AS r FROM dm.sales_daily WHERE region IN r"
IN_CTE_DICTIONARY = (
    "WITH x AS (SELECT dictGet('dm.rev','revenue',toUInt64(1)) AS v FROM dm.other) "
    "SELECT sum(revenue) FROM dm.sales_daily WHERE region IN x"
)
IN_CTE_DICTIONARY_SUBQUERY = (
    "WITH x AS (SELECT dictGet('dm.rev','revenue',toUInt64(1)) AS v FROM dm.other) "
    "SELECT sum(revenue) FROM dm.sales_daily WHERE region IN (SELECT * FROM x)"
)
IN_LITERAL_CONTROL = "SELECT sum(revenue) FROM dm.sales_daily WHERE region IN ('a')"
OUTER_ALIAS_ALONE = "WITH b + 1 AS a, revenue AS b SELECT a FROM dm.sales_daily"
INNER_ONLY_CYCLE = (
    "WITH b + 1 AS a, revenue AS b "
    "SELECT a, (WITH a + 1 AS b SELECT a FROM dm.sales_daily) AS z FROM dm.sales_daily"
)

OPS = ("IN", "NOT IN", "GLOBAL IN", "GLOBAL NOT IN")
NAMES = ("rev", "dm.rev")


def _stmt(op: str, right: str, *, tuple_left: bool = False) -> str:
    left = "(region, store_id)" if tuple_left else "region"
    extra = ", store_id" if tuple_left else ""
    return (
        f"SELECT region{extra}, sum(revenue) AS m FROM dm.sales_daily "
        f"WHERE {left} {op} {right} GROUP BY region{extra}"
    )


def assert_table_matches_subquery(table_sql: str, subquery_sql: str, tmp_path: Path) -> None:
    assert extract_table_refs(table_sql, dialect="clickhouse") == extract_table_refs(
        subquery_sql, dialect="clickhouse"
    )
    assert extract_column_refs(table_sql, dialect="clickhouse") == extract_column_refs(
        subquery_sql, dialect="clickhouse"
    )
    assert ungoverned_pairs(tmp_path / "table", table_sql) == ungoverned_pairs(
        tmp_path / "subquery", subquery_sql
    )
    assert ungoverned_detail(tmp_path / "table_d", table_sql) == ungoverned_detail(
        tmp_path / "subquery_d", subquery_sql
    )


# --- false-reject control -------------------------------------------------------------------


def test_a_reviewed_query_over_declared_columns_is_still_clean(tmp_path: Path) -> None:
    """Tightening IN-table handling must not reject a valid reference query."""
    sql = "SELECT region, sum(revenue) AS m FROM dm.sales_daily GROUP BY region"
    assert attributed(sql) == ["dm.sales_daily.region", "dm.sales_daily.revenue"]
    assert unattributed(sql) == []
    assert ungoverned_codes(tmp_path, sql) == []


def test_a_literal_in_list_is_still_clean(tmp_path: Path) -> None:
    sql = (
        "SELECT region, sum(revenue) AS m FROM dm.sales_daily "
        "WHERE region IN ('north', 'south') GROUP BY region"
    )
    assert sorted(ref.name for ref in extract_table_refs(sql, dialect="clickhouse")) == [
        "dm.sales_daily"
    ]
    assert attributed(sql) == ["dm.sales_daily.region", "dm.sales_daily.revenue"]
    assert extract_column_refs(sql, dialect="clickhouse").starred is False
    assert ungoverned_codes(tmp_path, sql) == []


# --- table form equals subquery form --------------------------------------------------------


@pytest.mark.parametrize("op", OPS)
@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("tuple_left", [False, True])
def test_in_table_matches_in_star_subquery(
    tmp_path: Path, op: str, name: str, tuple_left: bool
) -> None:
    table_sql = _stmt(op, name, tuple_left=tuple_left)
    subquery_sql = _stmt(op, f"(SELECT * FROM {name})", tuple_left=tuple_left)
    assert_table_matches_subquery(table_sql, subquery_sql, tmp_path)


def test_a_quoted_in_table_matches_the_quoted_subquery(tmp_path: Path) -> None:
    table_sql = 'SELECT region FROM dm.sales_daily WHERE region IN "dm"."rev"'
    subquery_sql = 'SELECT region FROM dm.sales_daily WHERE region IN (SELECT * FROM "dm"."rev")'
    assert_table_matches_subquery(table_sql, subquery_sql, tmp_path)


# --- the three HEAD reproductions -----------------------------------------------------------


def test_head_bare_in_rev_was_a_false_field_and_is_now_the_table(tmp_path: Path) -> None:
    """HEAD: `asset.query_ungoverned_field` naming `dm.sales_daily.rev`; table `rev` unread.

    After: the same as `IN (SELECT * FROM rev)` — ungoverned table + star advisory.
    """
    assert_table_matches_subquery(HEAD_BARE, HEAD_BARE_SUBQUERY, tmp_path)
    assert sorted(ref.name for ref in extract_table_refs(HEAD_BARE, dialect="clickhouse")) == [
        "dm.sales_daily",
        "rev",
    ]
    assert "dm.sales_daily.rev" not in attributed(HEAD_BARE)
    assert extract_column_refs(HEAD_BARE, dialect="clickhouse").starred is True
    pairs = ungoverned_pairs(tmp_path, HEAD_BARE)
    assert ("asset.query_ungoverned", "review") in pairs
    assert ("asset.query_fields_unchecked", "advisory") in pairs
    assert ("asset.query_ungoverned_field", "review") not in pairs
    detail = ungoverned_detail(tmp_path / "detail", HEAD_BARE)
    assert "rev" in detail
    assert "dm.sales_daily.rev" not in detail
    assert "*" in detail or "selects" in detail


def test_head_qualified_in_dm_rev_was_an_advisory_and_is_now_ungoverned(
    tmp_path: Path,
) -> None:
    """HEAD: only `asset.query_fields_unchecked`; ungoverned `dm.rev` raised nothing.

    After: REVIEW `asset.query_ungoverned` naming `dm.rev` plus the star advisory.
    """
    assert_table_matches_subquery(HEAD_QUALIFIED, HEAD_SUBQUERY, tmp_path)
    assert sorted(ref.name for ref in extract_table_refs(HEAD_QUALIFIED, dialect="clickhouse")) == [
        "dm.rev",
        "dm.sales_daily",
    ]
    assert ungoverned_codes(tmp_path, HEAD_QUALIFIED) == [
        "asset.query_ungoverned",
        "asset.query_fields_unchecked",
    ]
    detail = ungoverned_detail(tmp_path / "detail", HEAD_QUALIFIED)
    assert "dm.rev" in detail


def test_head_subquery_form_is_unchanged(tmp_path: Path) -> None:
    assert sorted(ref.name for ref in extract_table_refs(HEAD_SUBQUERY, dialect="clickhouse")) == [
        "dm.rev",
        "dm.sales_daily",
    ]
    assert extract_column_refs(HEAD_SUBQUERY, dialect="clickhouse").starred is True
    assert ungoverned_codes(tmp_path, HEAD_SUBQUERY) == [
        "asset.query_ungoverned",
        "asset.query_fields_unchecked",
    ]
    assert "dm.rev" in ungoverned_detail(tmp_path / "detail", HEAD_SUBQUERY)


# --- alias on the right stays an alias ------------------------------------------------------


def test_a_with_alias_on_the_right_of_in_stays_an_alias(tmp_path: Path) -> None:
    assert attributed(ALLOWED) == ["dm.sales_daily.region", "dm.sales_daily.revenue"]
    assert unattributed(ALLOWED) == []
    assert extract_column_refs(ALLOWED, dialect="clickhouse").starred is False
    assert sorted(ref.name for ref in extract_table_refs(ALLOWED, dialect="clickhouse")) == [
        "dm.sales_daily"
    ]
    assert ungoverned_codes(tmp_path, ALLOWED) == []


def test_a_select_alias_on_the_right_of_in_stays_an_alias(tmp_path: Path) -> None:
    assert attributed(SELECT_ALIAS_IN) == ["dm.sales_daily.region"]
    assert unattributed(SELECT_ALIAS_IN) == []
    assert extract_column_refs(SELECT_ALIAS_IN, dialect="clickhouse").starred is False
    assert sorted(
        ref.name for ref in extract_table_refs(SELECT_ALIAS_IN, dialect="clickhouse")
    ) == ["dm.sales_daily"]
    assert ungoverned_codes(tmp_path, SELECT_ALIAS_IN) == []


# --- a CTE named on the right is the CTE ----------------------------------------------------


def test_a_cte_named_on_the_right_of_in_is_the_cte(tmp_path: Path) -> None:
    table_sql = (
        "WITH x AS (SELECT region FROM dm.sales_daily) "
        "SELECT region FROM dm.sales_daily WHERE region IN x"
    )
    subquery_sql = (
        "WITH x AS (SELECT region FROM dm.sales_daily) "
        "SELECT region FROM dm.sales_daily WHERE region IN (SELECT * FROM x)"
    )
    assert_table_matches_subquery(table_sql, subquery_sql, tmp_path)
    assert sorted(ref.name for ref in extract_table_refs(table_sql, dialect="clickhouse")) == [
        "dm.sales_daily"
    ]
    assert "dm.sales_daily.x" not in attributed(table_sql)
    assert extract_column_refs(table_sql, dialect="clickhouse").starred is True
    assert ungoverned_codes(tmp_path, table_sql) == ["asset.query_fields_unchecked"]


# --- literal lists and tuple() unchanged ----------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT region FROM dm.sales_daily WHERE region IN (1, 2)",
        "SELECT region FROM dm.sales_daily WHERE region IN ('a')",
        "SELECT region FROM dm.sales_daily WHERE region IN tuple(1, 2)",
    ],
)
def test_literal_in_lists_are_unchanged(sql: str) -> None:
    assert sorted(ref.name for ref in extract_table_refs(sql, dialect="clickhouse")) == [
        "dm.sales_daily"
    ]
    assert attributed(sql) == ["dm.sales_daily.region"]
    assert unattributed(sql) == []
    assert extract_column_refs(sql, dialect="clickhouse").starred is False


def test_function_form_in_still_does_not_parse() -> None:
    """`in(x, t)` does not parse in sqlglot 30.14.0; leave it and name it."""
    sql = "SELECT region FROM dm.sales_daily WHERE in(region, t)"
    with pytest.raises(SQLGuardError, match="does not parse"):
        extract_column_refs(sql, dialect="clickhouse")
    with pytest.raises(SQLGuardError, match="does not parse"):
        extract_table_refs(sql, dialect="clickhouse")


# --- PostgreSQL family unchanged ------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "tables", "attr", "unattr", "starred"),
    [
        (
            "SELECT region FROM dm.sales_daily WHERE region IN rev",
            ["dm.sales_daily"],
            ["dm.sales_daily.region", "dm.sales_daily.rev"],
            [],
            False,
        ),
        (
            "SELECT region FROM dm.sales_daily WHERE region IN dm.rev",
            ["dm.sales_daily"],
            ["dm.sales_daily.region"],
            ["rev"],
            False,
        ),
        (
            "SELECT region FROM dm.sales_daily WHERE region NOT IN rev",
            ["dm.sales_daily"],
            ["dm.sales_daily.region", "dm.sales_daily.rev"],
            [],
            False,
        ),
        (
            "SELECT region FROM dm.sales_daily WHERE region IN (SELECT * FROM dm.rev)",
            ["dm.rev", "dm.sales_daily"],
            ["dm.sales_daily.region"],
            [],
            True,
        ),
        (
            "SELECT region FROM dm.sales_daily WHERE region IN (1, 2)",
            ["dm.sales_daily"],
            ["dm.sales_daily.region"],
            [],
            False,
        ),
        (
            "SELECT region FROM dm.sales_daily WHERE region IN ('a')",
            ["dm.sales_daily"],
            ["dm.sales_daily.region"],
            [],
            False,
        ),
    ],
)
def test_postgres_family_unchanged_where_the_text_parses(
    sql: str, tables: list[str], attr: list[str], unattr: list[str], starred: bool
) -> None:
    assert sorted(ref.name for ref in extract_table_refs(sql, dialect="postgres")) == tables
    reads = extract_column_refs(sql, dialect="postgres")
    assert sorted(f"{ref.table.name}.{ref.name}" for ref in reads.attributed) == attr
    assert sorted(reads.unattributed) == unattr
    assert reads.starred is starred


def test_postgres_global_in_still_does_not_parse() -> None:
    sql = "SELECT region FROM dm.sales_daily WHERE region GLOBAL IN rev"
    with pytest.raises(SQLGuardError, match="does not parse"):
        extract_column_refs(sql, dialect="postgres")


# --- chart impact ---------------------------------------------------------------------------


def test_in_table_chart_impact_matches_the_subquery_form(tmp_path: Path) -> None:
    """`IN dm.rev` is a star over that table: no `reads_field` of `dm.rev.revenue`."""
    table_sql = "SELECT sum(revenue) FROM dm.sales_daily WHERE region IN dm.rev"
    subquery_sql = "SELECT sum(revenue) FROM dm.sales_daily WHERE region IN (SELECT * FROM dm.rev)"
    _, table_report = field_change(tmp_path / "table", table_sql, "dm.rev.revenue")
    _, sub_report = field_change(tmp_path / "sub", subquery_sql, "dm.rev.revenue")
    assert charts(table_report) == charts(sub_report) == []
    assert gaps(table_report) == gaps(sub_report)
    assert [gap.detail for gap in table_report.chart_gaps] == [
        "operator SQL was not fully resolved: it selects *"
    ]


def test_a_named_cte_opened_by_in_table_reads_its_dictionary(tmp_path: Path) -> None:
    _, table_report = field_change(tmp_path / "table", IN_CTE_DICTIONARY, "dm.rev.revenue")
    _, sub_report = field_change(tmp_path / "sub", IN_CTE_DICTIONARY_SUBQUERY, "dm.rev.revenue")
    assert (
        charts(table_report)
        == charts(sub_report)
        == [("operator_table", ChartRelation.READS_FIELD, "query reads dm.rev.revenue")]
    )
    assert gaps(table_report) == gaps(sub_report)
    assert any(gap.code == "chart.sql_partial" for gap in table_report.chart_gaps)


def test_a_literal_in_list_does_not_open_a_named_cte(tmp_path: Path) -> None:
    _, report = field_change(tmp_path, IN_LITERAL_CONTROL, "dm.rev.revenue")
    assert charts(report) == []
    assert report.chart_gaps == []


# --- F-T-13-16: cyclic is per (declaration, scope) ------------------------------------------


def test_an_inner_only_with_cycle_does_not_hide_the_outer_read() -> None:
    """A cycle that exists only in an inner scope must not turn the outer `a` into a column."""
    assert attributed(OUTER_ALIAS_ALONE) == ["dm.sales_daily.revenue"]
    assert "dm.sales_daily.revenue" in attributed(INNER_ONLY_CYCLE)
    assert "dm.sales_daily.a" not in attributed(INNER_ONLY_CYCLE)


# --- F-T-14-2: a statement with no Column on the right of IN pays nothing ------------------


def test_a_statement_without_in_table_builds_its_scopes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The IN rewrite looks for a Column on the right of IN before it builds anything."""
    real = sql_guard.build_scope
    calls: list[str] = []

    def counting(expression: exp.Expression) -> Scope | None:
        calls.append(type(expression).__name__)
        return real(expression)

    monkeypatch.setattr(sql_guard, "build_scope", counting)
    extract_column_refs(IN_LITERAL_CONTROL, dialect="clickhouse")
    extract_column_refs(
        "SELECT region, sum(revenue) AS m FROM dm.sales_daily GROUP BY region",
        dialect="clickhouse",
    )
    assert calls == ["Select", "Select"]
    # A Column on the right of IN is decided in its scope, alias or table.
    for sql in (HEAD_BARE, ALLOWED):
        calls.clear()
        extract_column_refs(sql, dialect="clickhouse")
        assert calls == ["Select", "Select"]
    calls.clear()
    extract_column_refs(HEAD_BARE, dialect="postgres")
    assert calls == ["Select"]
