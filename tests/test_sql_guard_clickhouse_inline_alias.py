"""ClickHouse inline and ARRAY JOIN aliases are aliases, not table columns (T-18)."""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_bi.agent.sql_guard import extract_column_refs, extract_table_refs
from auto_bi.decision.impact import ChartRelation
from tests.test_decision_chart_impact_dictionary import charts, field_change
from tests.test_sql_guard_clickhouse_alias import attributed, unattributed, ungoverned_codes


@pytest.mark.parametrize(
    ("sql", "expected", "codes"),
    [
        (
            "SELECT a, sum(revenue) FROM dm.sales_daily ARRAY JOIN tags AS a GROUP BY a",
            ["dm.sales_daily.revenue", "dm.sales_daily.tags"],
            ["asset.query_ungoverned_field"],
        ),
        (
            "SELECT a, b FROM dm.sales_daily LEFT ARRAY JOIN tags AS a, tags AS b",
            ["dm.sales_daily.tags"],
            ["asset.query_ungoverned_field"],
        ),
        (
            "SELECT region, sum(revenue) FROM dm.sales_daily "
            "WHERE (revenue AS y) > 1 AND y < 10 GROUP BY region",
            ["dm.sales_daily.region", "dm.sales_daily.revenue"],
            [],
        ),
        (
            "SELECT region, y FROM dm.sales_daily WHERE (revenue AS y) > 1",
            ["dm.sales_daily.region", "dm.sales_daily.revenue"],
            [],
        ),
        (
            "SELECT region, (revenue AS r) * 2 AS x, r FROM dm.sales_daily",
            ["dm.sales_daily.region", "dm.sales_daily.revenue"],
            [],
        ),
        (
            "SELECT region, sum(revenue) FROM dm.sales_daily " "GROUP BY (region AS g) ORDER BY g",
            ["dm.sales_daily.region", "dm.sales_daily.revenue"],
            [],
        ),
    ],
)
def test_head_reproductions_read_inline_and_array_aliases_as_aliases(
    tmp_path: Path, sql: str, expected: list[str], codes: list[str]
) -> None:
    assert attributed(sql) == expected
    assert unattributed(sql) == []
    assert ungoverned_codes(tmp_path, sql) == codes


def test_an_array_join_alias_is_not_visible_inside_a_subquery(tmp_path: Path) -> None:
    sql = (
        "SELECT region, (SELECT a FROM dm.stores LIMIT 1) AS z FROM dm.sales_daily "
        "ARRAY JOIN tags AS a"
    )
    assert attributed(sql) == ["dm.sales_daily.region", "dm.sales_daily.tags"]
    assert unattributed(sql) == ["a"]
    assert "asset.query_fields_unchecked" in ungoverned_codes(tmp_path, sql)


def test_array_join_without_an_alias_keeps_the_array_column() -> None:
    sql = "SELECT tags FROM dm.sales_daily ARRAY JOIN tags"
    assert attributed(sql) == ["dm.sales_daily.tags"]
    assert unattributed(sql) == []


def test_an_array_join_alias_defining_its_own_name_reads_the_array_column() -> None:
    sql = "SELECT tags FROM dm.sales_daily ARRAY JOIN tags AS tags"
    assert attributed(sql) == ["dm.sales_daily.tags"]
    assert unattributed(sql) == []


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            "WITH tags AS w SELECT tags FROM dm.sales_daily ARRAY JOIN w AS tags",
            ["dm.sales_daily.tags"],
        ),
        (
            "WITH x + 1 AS w SELECT 1 FROM dm.sales_daily WHERE (w AS x) > 1",
            ["dm.sales_daily.x"],
        ),
    ],
)
def test_an_inline_or_array_alias_defining_its_own_name_reads_through_a_with(
    sql: str, expected: list[str]
) -> None:
    assert attributed(sql) == expected
    assert unattributed(sql) == []


def test_an_inline_alias_is_visible_before_its_textual_declaration() -> None:
    sql = "SELECT y, (revenue AS y) * 2 FROM dm.sales_daily"
    assert attributed(sql) == ["dm.sales_daily.revenue"]
    assert unattributed(sql) == []


def test_an_inline_alias_declared_inside_a_select_expression_is_visible() -> None:
    sql = "SELECT region, (revenue AS r) * 2 AS x, r FROM dm.sales_daily"
    assert attributed(sql) == ["dm.sales_daily.region", "dm.sales_daily.revenue"]
    assert unattributed(sql) == []


def test_an_inline_alias_in_a_join_condition_is_visible() -> None:
    sql = (
        "SELECT sales.region FROM dm.sales_daily AS sales JOIN dm.stores AS stores "
        "ON (sales.store_id AS joined_store) = stores.store_id WHERE joined_store > 0"
    )
    assert attributed(sql) == [
        "dm.sales_daily.region",
        "dm.sales_daily.store_id",
        "dm.stores.store_id",
    ]
    assert unattributed(sql) == []


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            "SELECT 1 FROM dm.sales_daily HAVING (internal_cost_raw AS y) > 1 ORDER BY y",
            ["dm.sales_daily.internal_cost_raw"],
        ),
        (
            "SELECT row_number() OVER () FROM dm.sales_daily "
            "QUALIFY (revenue AS y) > 1 ORDER BY y",
            ["dm.sales_daily.revenue"],
        ),
        (
            "SELECT region FROM dm.sales_daily GROUP BY region HAVING (sum(cost) AS s) > 0",
            ["dm.sales_daily.cost", "dm.sales_daily.region"],
        ),
    ],
)
def test_an_inline_alias_definition_in_having_or_qualify_is_graded(
    sql: str, expected: list[str]
) -> None:
    assert attributed(sql) == expected
    assert unattributed(sql) == []


def test_an_inline_alias_definition_in_having_is_governed(tmp_path: Path) -> None:
    sql = "SELECT region FROM dm.sales_daily GROUP BY region HAVING (sum(cost) AS s) > 0"
    assert ungoverned_codes(tmp_path, sql) == ["asset.query_ungoverned_field"]


def test_a_nested_inline_alias_keeps_its_outer_owner_for_with_read_through() -> None:
    sql = "WITH x + 1 AS w SELECT (w AS y) * 2 AS x FROM dm.sales_daily"
    assert attributed(sql) == ["dm.sales_daily.x"]
    assert unattributed(sql) == []


def test_an_inline_alias_through_with_keeps_all_enclosing_owners() -> None:
    sql = "WITH revenue + 1 AS w SELECT (w AS y) + y AS revenue FROM dm.sales_daily"
    assert attributed(sql) == ["dm.sales_daily.revenue"]
    assert unattributed(sql) == []


def test_an_inline_alias_through_with_does_not_raise_a_governance_finding_on_its_name(
    tmp_path: Path,
) -> None:
    sql = "WITH revenue + 1 AS w SELECT (w AS y) + y AS revenue FROM dm.sales_daily"
    assert ungoverned_codes(tmp_path, sql) == []


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            "WITH internal_cost_raw AS revenue "
            "SELECT revenue FROM (SELECT revenue AS revenue FROM dm.sales_daily)",
            ["dm.sales_daily.internal_cost_raw"],
        ),
        (
            "WITH 1 AS x SELECT x FROM (SELECT x AS x FROM dm.sales_daily)",
            [],
        ),
    ],
)
def test_a_self_named_select_alias_still_reads_an_enclosing_scalar_with(
    sql: str, expected: list[str]
) -> None:
    assert attributed(sql) == expected
    assert unattributed(sql) == []


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            "WITH internal_cost_raw AS revenue "
            "SELECT revenue FROM (SELECT revenue AS revenue FROM dm.sales_daily)",
            ["asset.query_ungoverned_field"],
        ),
        (
            "WITH 1 AS x SELECT x FROM (SELECT x AS x FROM dm.sales_daily)",
            [],
        ),
    ],
)
def test_a_self_named_select_alias_keeps_enclosing_scalar_with_governance(
    tmp_path: Path, sql: str, expected: list[str]
) -> None:
    assert ungoverned_codes(tmp_path, sql) == expected


def test_an_inline_alias_inside_a_subquery_is_not_visible_to_the_outer_query() -> None:
    sql = (
        "SELECT y FROM dm.sales_daily WHERE " "(SELECT (store_id AS y) FROM dm.stores LIMIT 1) > 0"
    )
    assert attributed(sql) == ["dm.sales_daily.y"]
    assert unattributed(sql) == ["store_id"]


def test_a_top_level_select_alias_wins_over_an_inline_alias_of_the_same_name() -> None:
    sql = "SELECT revenue AS x, (region AS x) + x AS z FROM dm.sales_daily"
    assert attributed(sql) == ["dm.sales_daily.region", "dm.sales_daily.revenue"]
    assert unattributed(sql) == []


def test_the_first_inline_alias_of_one_name_is_indexed() -> None:
    sql = "SELECT x FROM dm.sales_daily " "WHERE (y AS x) > 1 AND (z AS x) > 1 AND (x AS y) > 1"
    assert attributed(sql) == [
        "dm.sales_daily.x",
        "dm.sales_daily.y",
        "dm.sales_daily.z",
    ]
    assert unattributed(sql) == []


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 FROM dm.sales_daily WHERE (1 AS x) > 0 AND (x AS x) > 0",
        "SELECT 1 AS x FROM dm.sales_daily ARRAY JOIN x AS x",
    ],
)
def test_each_duplicate_alias_definition_grades_its_own_name(sql: str) -> None:
    assert attributed(sql) == ["dm.sales_daily.x"]
    assert unattributed(sql) == []


def test_a_cycle_through_inline_aliases_reads_the_names_as_columns() -> None:
    sql = "SELECT (x AS y) + 1, (y AS x) + 1 FROM dm.sales_daily"
    assert attributed(sql) == ["dm.sales_daily.x", "dm.sales_daily.y"]
    assert unattributed(sql) == []


def test_a_cycle_between_select_and_inline_aliases_reads_the_names_as_columns() -> None:
    sql = "SELECT revenue AS r FROM dm.sales_daily WHERE (r AS revenue) > 0"
    assert attributed(sql) == ["dm.sales_daily.r", "dm.sales_daily.revenue"]
    assert unattributed(sql) == []


def test_an_array_join_alias_on_the_right_of_in_is_not_a_table() -> None:
    sql = "SELECT region FROM dm.sales_daily ARRAY JOIN tags AS a WHERE region IN a"
    assert sorted(ref.name for ref in extract_table_refs(sql, dialect="clickhouse")) == [
        "dm.sales_daily"
    ]
    assert attributed(sql) == ["dm.sales_daily.region", "dm.sales_daily.tags"]
    assert unattributed(sql) == []


def test_postgresql_keeps_treating_the_inline_alias_reference_as_a_column() -> None:
    sql = (
        "SELECT region, sum(revenue) FROM dm.sales_daily "
        "WHERE (revenue AS y) > 1 AND y < 10 GROUP BY region"
    )
    assert attributed(sql, dialect="postgres") == [
        "dm.sales_daily.region",
        "dm.sales_daily.revenue",
        "dm.sales_daily.y",
    ]
    assert unattributed(sql, dialect="postgres") == []


def test_chart_impact_names_the_real_array_column_and_never_the_alias(tmp_path: Path) -> None:
    sql = "SELECT a, sum(revenue) FROM dm.sales_daily ARRAY JOIN tags AS a GROUP BY a"
    _, report = field_change(tmp_path, sql, "dm.sales_daily.tags")
    assert charts(report) == [
        ("operator_table", ChartRelation.READS_FIELD, "query reads dm.sales_daily.tags")
    ]
    assert report.chart_gaps == []
    assert all(".a" not in detail for _, _, detail in charts(report))


def test_a_nested_with_does_not_take_an_outer_select_alias_as_its_owner() -> None:
    sql = (
        "SELECT (WITH s.cost AS region, region AS b SELECT b FROM dm.sales_daily AS s LIMIT 1) "
        "AS region FROM dm.sales_daily"
    )
    reads = extract_column_refs(sql, dialect="clickhouse")
    assert sorted(f"{ref.table.name}.{ref.name}" for ref in reads.attributed) == [
        "dm.sales_daily.cost"
    ]
    assert reads.unattributed == frozenset()


def test_a_nested_with_does_not_hide_its_ungoverned_cost(tmp_path: Path) -> None:
    sql = (
        "SELECT (WITH s.cost AS region, region AS b SELECT b FROM dm.sales_daily AS s LIMIT 1) "
        "AS region FROM dm.sales_daily"
    )
    assert ungoverned_codes(tmp_path, sql) == ["asset.query_ungoverned_field"]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT cost FROM dm.sales_daily WHERE 1 IN "
        "((SELECT 1 UNION ALL SELECT 2) ORDER BY (1 AS cost))",
        "SELECT cost FROM dm.sales_daily WHERE 1 IN ((SELECT 1) LIMIT (1 AS cost))",
    ],
)
def test_a_parenthesized_query_alias_does_not_hide_the_outer_column(sql: str) -> None:
    reads = extract_column_refs(sql, dialect="clickhouse")
    assert sorted(f"{ref.table.name}.{ref.name}" for ref in reads.attributed) == [
        "dm.sales_daily.cost"
    ]
    assert reads.unattributed == frozenset()


def test_a_parenthesized_union_alias_keeps_outer_column_governance(tmp_path: Path) -> None:
    sql = (
        "SELECT cost FROM dm.sales_daily WHERE 1 IN "
        "((SELECT 1 UNION ALL SELECT 2) ORDER BY (1 AS cost))"
    )
    assert ungoverned_codes(tmp_path, sql) == ["asset.query_ungoverned_field"]


def test_a_parenthesized_query_keeps_enclosing_alias_ownership() -> None:
    sql = "SELECT ((SELECT region FROM dm.sales_daily) ORDER BY cost) AS cost FROM dm.stores"
    reads = extract_column_refs(sql, dialect="clickhouse")
    assert sorted(f"{ref.table.name}.{ref.name}" for ref in reads.attributed) == ["dm.stores.cost"]
    assert reads.unattributed == frozenset({"region"})
