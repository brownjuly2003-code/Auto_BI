"""PostgreSQL resolves whole sort names through outputs and expressions through inputs."""

from pathlib import Path

import pytest

from auto_bi.agent.sql_guard import SQLGuardError, extract_column_refs
from tests.test_sql_guard_clickhouse_alias import (
    ungoverned_codes,
    ungoverned_detail,
    ungoverned_pairs,
)


def _reads(sql: str, *, dialect: str = "postgres") -> set[str]:
    refs = extract_column_refs(sql, dialect=dialect)
    return {f"{ref.table.name}.{ref.name}" for ref in refs.attributed}


@pytest.mark.parametrize(
    "sql, expected",
    [
        (
            'SELECT region AS r FROM dm.sales_daily ORDER BY r COLLATE "C"',
            {"dm.sales_daily.region", "dm.sales_daily.r"},
        ),
        (
            "SELECT DISTINCT ON (r || '') region AS r FROM dm.sales_daily ORDER BY r || ''",
            {"dm.sales_daily.region", "dm.sales_daily.r"},
        ),
        (
            "SELECT DISTINCT ON (r || '') region AS r FROM dm.sales_daily",
            {"dm.sales_daily.region", "dm.sales_daily.r"},
        ),
        (
            "SELECT region AS r FROM dm.sales_daily ORDER BY r + 1",
            {"dm.sales_daily.region", "dm.sales_daily.r"},
        ),
        (
            "SELECT region AS r FROM dm.sales_daily ORDER BY lower(r)",
            {"dm.sales_daily.region", "dm.sales_daily.r"},
        ),
    ],
)
def test_sort_expression_reads_input(tmp_path: Path, sql: str, expected: set[str]) -> None:
    assert _reads(sql) == expected
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]
    assert "dm.sales_daily.r" in ungoverned_detail(tmp_path / "detail", sql, engine="postgres")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT region AS R FROM dm.sales_daily ORDER BY r",
        'SELECT region AS R FROM dm.sales_daily ORDER BY "r"',
        "SELECT region AS R FROM dm.sales_daily ORDER BY (r)",
        "SELECT DISTINCT ON (r) region AS R FROM dm.sales_daily ORDER BY r",
        "SELECT DISTINCT ON (r) region AS R FROM dm.sales_daily",
        "SELECT upper(region) AS region FROM dm.sales_daily ORDER BY region",
    ],
)
def test_whole_sort_name_reads_output(tmp_path: Path, sql: str) -> None:
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == {"dm.sales_daily.region"}
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT region FROM dm.sales_daily UNION "
        "SELECT category FROM dm.sales_daily ORDER BY region",
        "SELECT region AS r FROM dm.sales_daily UNION ALL "
        "SELECT category FROM dm.sales_daily ORDER BY r",
        "SELECT s.r FROM (SELECT region AS r FROM dm.sales_daily INTERSECT "
        "SELECT category FROM dm.sales_daily ORDER BY r) AS s",
        "SELECT s.r FROM (SELECT region AS r FROM dm.sales_daily EXCEPT "
        "SELECT category FROM dm.sales_daily ORDER BY r) AS s",
        "(SELECT region AS r FROM dm.sales_daily) UNION ALL "
        "(SELECT category FROM dm.sales_daily) ORDER BY r LIMIT 5",
    ],
)
def test_set_operation_sort_name_reads_output(tmp_path: Path, sql: str) -> None:
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == {"dm.sales_daily.region", "dm.sales_daily.category"}
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_quoted_output_alias_does_not_match_unquoted_name(tmp_path: Path) -> None:
    sql = 'SELECT region AS "R" FROM dm.sales_daily ORDER BY r'
    assert _reads(sql) == {"dm.sales_daily.region", "dm.sales_daily.r"}
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]


def test_join_expression_name_remains_ambiguous(tmp_path: Path) -> None:
    sql = (
        "SELECT s.region AS r FROM dm.sales_daily AS s "
        "JOIN dm.stores AS t ON s.store_id = t.id ORDER BY lower(r)"
    )
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == {
        "dm.sales_daily.region",
        "dm.sales_daily.store_id",
        "dm.stores.id",
    }
    assert {ref.name for ref in refs.ambiguous} == {"r"}
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]
    assert "r (read from one of dm.sales_daily, dm.stores)" in ungoverned_detail(
        tmp_path / "detail", sql, engine="postgres"
    )


@pytest.mark.parametrize(
    "sql, expected",
    [
        (
            "SELECT region AS r, rank() OVER (PARTITION BY r ORDER BY r) FROM dm.sales_daily",
            {"dm.sales_daily.region", "dm.sales_daily.r"},
        ),
        (
            "SELECT region AS r, percentile_cont(0.5) WITHIN GROUP (ORDER BY r) "
            "FROM dm.sales_daily",
            {"dm.sales_daily.region", "dm.sales_daily.r"},
        ),
        (
            "SELECT string_agg(region, ',' ORDER BY r) AS agg, max(region) AS r "
            "FROM dm.sales_daily",
            {"dm.sales_daily.region", "dm.sales_daily.r"},
        ),
        (
            "SELECT array_agg(region ORDER BY r) AS agg, max(region) AS r " "FROM dm.sales_daily",
            {"dm.sales_daily.region", "dm.sales_daily.r"},
        ),
    ],
)
def test_nested_ordering_keeps_input_reads(tmp_path: Path, sql: str, expected: set[str]) -> None:
    assert _reads(sql) == expected
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]


def test_group_by_keeps_input_first_rule(tmp_path: Path) -> None:
    sql = "SELECT upper(region) AS region, count(*) FROM dm.sales_daily GROUP BY region"
    assert _reads(sql) == {"dm.sales_daily.region"}
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_clickhouse_sort_expression_keeps_current_reads(tmp_path: Path) -> None:
    sql = "SELECT region AS r FROM dm.sales_daily ORDER BY r + 1"
    assert _reads(sql, dialect="clickhouse") == {"dm.sales_daily.region"}
    assert ungoverned_codes(tmp_path, sql, engine="clickhouse") == []


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT region FROM dm.sales_daily s ORDER BY s.*",
        "SELECT DISTINCT ON (s.*) region FROM dm.sales_daily s",
    ],
)
def test_qualified_star_keeps_star_remainder(tmp_path: Path, sql: str) -> None:
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == {"dm.sales_daily.region"}
    assert refs.starred
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_fields_unchecked", "advisory")
    ]


def test_set_operation_expression_keeps_sourceless_result(tmp_path: Path) -> None:
    sql = (
        "SELECT region AS r FROM dm.sales_daily UNION ALL "
        "SELECT category FROM dm.sales_daily ORDER BY lower(r)"
    )
    refs = extract_column_refs(sql, dialect="postgres")
    assert refs.unattributed == frozenset({"r"})
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_fields_unchecked", "advisory")
    ]


@pytest.mark.parametrize("dialect", ["postgres", "clickhouse"])
@pytest.mark.parametrize("operator", ["INTERSECT", "EXCEPT"])
def test_root_intersect_and_except_stay_refused(dialect: str, operator: str) -> None:
    sql = f"SELECT region FROM dm.sales_daily {operator} SELECT category FROM dm.sales_daily"
    with pytest.raises(SQLGuardError, match="only SELECT is allowed"):
        extract_column_refs(sql, dialect=dialect)


@pytest.mark.parametrize(
    "sql, expected",
    [
        ("SELECT revenue AS R FROM dm.sales_daily ORDER BY +r", "dm.sales_daily.r"),
        ("SELECT revenue AS R FROM dm.sales_daily ORDER BY +(r)", "dm.sales_daily.r"),
        ("SELECT revenue AS R FROM dm.sales_daily ORDER BY +(+r)", "dm.sales_daily.r"),
        ("SELECT DISTINCT ON (+r) region AS r FROM dm.sales_daily", "dm.sales_daily.r"),
        ('SELECT region AS "R" FROM dm.sales_daily ORDER BY +"R"', "dm.sales_daily.R"),
        ("SELECT upper(region) AS m FROM dm.sales_daily GROUP BY +m", "dm.sales_daily.m"),
    ],
)
def test_unary_plus_name_is_input(tmp_path: Path, sql: str, expected: str) -> None:
    assert expected in _reads(sql)
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]


def test_unary_plus_set_sort_stays_sourceless(tmp_path: Path) -> None:
    sql = (
        "SELECT region AS r FROM dm.sales_daily UNION ALL "
        "SELECT category FROM dm.sales_daily ORDER BY +r"
    )
    assert extract_column_refs(sql, dialect="postgres").unattributed == frozenset({"r"})
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_fields_unchecked", "advisory")
    ]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT x.r FROM ((SELECT region AS r FROM dm.sales_daily) ORDER BY lower(r)) x",
        "WITH c AS ((SELECT region AS r FROM dm.sales_daily) ORDER BY lower(r)) " "SELECT r FROM c",
        "SELECT x.r FROM ((SELECT s.region AS r FROM dm.sales_daily s) " "ORDER BY lower(s.r)) x",
    ],
)
def test_parenthesized_select_expression_sort_reads_input(tmp_path: Path, sql: str) -> None:
    assert _reads(sql) == {"dm.sales_daily.region", "dm.sales_daily.r"}
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT x.r FROM ((SELECT region AS R FROM dm.sales_daily) ORDER BY r) x",
        "SELECT x.r FROM ((SELECT region AS r FROM dm.sales_daily UNION "
        "SELECT category FROM dm.sales_daily) ORDER BY r) x",
    ],
)
def test_parenthesized_output_sort_is_clean(tmp_path: Path, sql: str) -> None:
    assert "r" not in extract_column_refs(sql, dialect="postgres").unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_parenthesized_query_with_inner_order_keeps_existing_reads(tmp_path: Path) -> None:
    sql = (
        "SELECT x.r FROM ((SELECT region AS r FROM dm.sales_daily ORDER BY region) "
        "ORDER BY lower(r)) x"
    )
    assert _reads(sql) == {"dm.sales_daily.region"}
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


@pytest.mark.parametrize("projected", ["(region)", "((region))"])
def test_parenthesized_leftmost_set_output_name_is_clean(tmp_path: Path, projected: str) -> None:
    sql = (
        f"SELECT {projected} FROM dm.sales_daily UNION "
        "SELECT category FROM dm.sales_daily ORDER BY region"
    )
    assert extract_column_refs(sql, dialect="postgres").unattributed == frozenset()
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_parenthesized_plain_output_wins_join_ambiguity(tmp_path: Path) -> None:
    sql = "SELECT (s.id) FROM dm.stores s " "JOIN dm.stores t ON s.id = t.id ORDER BY id"
    assert extract_column_refs(sql, dialect="postgres").ambiguous == frozenset()
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_unary_plus_projection_does_not_name_set_output(tmp_path: Path) -> None:
    sql = (
        "SELECT +revenue FROM dm.sales_daily UNION "
        "SELECT revenue FROM dm.sales_daily ORDER BY revenue"
    )
    assert extract_column_refs(sql, dialect="postgres").unattributed == frozenset({"revenue"})
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_fields_unchecked", "advisory")
    ]


def test_unary_plus_projection_does_not_hide_join_ambiguity(tmp_path: Path) -> None:
    sql = "SELECT +(s.id) FROM dm.stores s " "JOIN dm.stores t ON s.id = t.id ORDER BY id"
    assert {ref.name for ref in extract_column_refs(sql, dialect="postgres").ambiguous} == {"id"}
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_fields_unchecked", "advisory")
    ]


def test_explicit_alias_on_unary_plus_projection_names_output(tmp_path: Path) -> None:
    sql = (
        "SELECT +revenue AS revenue FROM dm.sales_daily UNION "
        "SELECT revenue FROM dm.sales_daily ORDER BY revenue"
    )
    assert extract_column_refs(sql, dialect="postgres").unattributed == frozenset()
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


@pytest.mark.parametrize("projected", ["+dm.stores.id", "+(dm.stores.id)"])
def test_unary_plus_three_part_projection_does_not_name_set_output(
    tmp_path: Path, projected: str
) -> None:
    sql = f"SELECT {projected} FROM dm.stores UNION SELECT id FROM dm.stores ORDER BY id"
    assert extract_column_refs(sql, dialect="postgres").unattributed == frozenset({"id"})
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_fields_unchecked", "advisory")
    ]


def test_explicit_alias_on_unary_plus_three_part_projection_names_output(tmp_path: Path) -> None:
    sql = "SELECT +dm.stores.id AS id FROM dm.stores UNION SELECT id FROM dm.stores ORDER BY id"
    assert extract_column_refs(sql, dialect="postgres").unattributed == frozenset()
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []
