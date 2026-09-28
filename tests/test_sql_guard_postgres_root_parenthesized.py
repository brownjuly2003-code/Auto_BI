"""PostgreSQL accepts a parenthesized SELECT/UNION as the root query."""

from pathlib import Path

import pytest

from auto_bi.agent.sql_guard import SQLGuardError, extract_column_refs, guard_sql
from tests.test_sql_guard_clickhouse_alias import ungoverned_codes, ungoverned_pairs


def _reads(sql: str) -> set[str]:
    refs = extract_column_refs(sql, dialect="postgres")
    return {f"{ref.table.name}.{ref.name}" for ref in refs.attributed}


@pytest.mark.parametrize(
    "sql, expected, governance_codes",
    [
        (
            "(SELECT region AS r FROM dm.sales_daily)",
            {"dm.sales_daily.region"},
            [],
        ),
        (
            "(SELECT region AS r FROM dm.sales_daily) ORDER BY r",
            {"dm.sales_daily.region"},
            [],
        ),
        (
            "(SELECT region AS r FROM dm.sales_daily) ORDER BY lower(r)",
            {"dm.sales_daily.region", "dm.sales_daily.r"},
            ["asset.query_ungoverned_field"],
        ),
        (
            "((SELECT region AS r FROM dm.sales_daily)) ORDER BY r",
            {"dm.sales_daily.region"},
            [],
        ),
        (
            "((SELECT region AS r FROM dm.sales_daily)) ORDER BY lower(r) LIMIT 5",
            {"dm.sales_daily.region", "dm.sales_daily.r"},
            ["asset.query_ungoverned_field"],
        ),
        (
            "(SELECT region AS r FROM dm.sales_daily) OFFSET 2",
            {"dm.sales_daily.region"},
            [],
        ),
        (
            "(SELECT region AS r FROM dm.sales_daily UNION "
            "SELECT category FROM dm.sales_daily) ORDER BY r",
            {"dm.sales_daily.region", "dm.sales_daily.category"},
            [],
        ),
    ],
)
def test_root_parenthesized_query_is_guarded_and_attributed(
    tmp_path: Path, sql: str, expected: set[str], governance_codes: list[str]
) -> None:
    guard_sql(sql, dialect="postgres")
    assert _reads(sql) == expected
    refs = extract_column_refs(sql, dialect="postgres")
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == governance_codes


def test_root_parenthesized_order_expression_reaches_governance(tmp_path: Path) -> None:
    sql = "(SELECT region AS r FROM dm.sales_daily) ORDER BY lower(r)"
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]


@pytest.mark.parametrize(
    "sql",
    [
        "(SELECT region FROM dm.sales_daily INTERSECT SELECT category FROM dm.sales_daily) "
        "ORDER BY region",
        "(SELECT region FROM dm.sales_daily EXCEPT SELECT category FROM dm.sales_daily) "
        "ORDER BY region",
        "(VALUES (1)) ORDER BY 1",
        "(SELECT region FROM dm.sales_daily) AS q",
        "(SELECT region FROM dm.sales_daily) FOR UPDATE",
        "(SELECT region FROM dm.sales_daily) ORDER BY region; SELECT 1",
        "(INSERT INTO dm.sales_daily SELECT region FROM dm.other)",
    ],
)
def test_root_parenthesized_non_select_forms_stay_rejected(sql: str) -> None:
    with pytest.raises(SQLGuardError):
        guard_sql(sql, dialect="postgres")


def test_root_parenthesized_forbidden_table_function_is_scanned() -> None:
    sql = "(SELECT * FROM url('s3://bucket/file.csv')) ORDER BY 1"
    with pytest.raises(SQLGuardError, match="forbidden table function"):
        guard_sql(sql, dialect="postgres")


def test_root_parenthesized_outer_order_forbidden_table_function_is_scanned() -> None:
    sql = "(SELECT region FROM dm.sales_daily) ORDER BY url('s3://bucket/file.csv')"
    with pytest.raises(SQLGuardError, match="forbidden table function"):
        guard_sql(sql, dialect="postgres")


def test_root_parenthesized_outer_order_subquery_forbidden_source_is_scanned() -> None:
    sql = "(SELECT 1) ORDER BY (SELECT * FROM url('s3://bucket/file.csv'))"
    with pytest.raises(SQLGuardError, match="forbidden table function"):
        guard_sql(sql, dialect="postgres")


def test_root_parenthesized_scalar_order_subquery_keeps_its_reads() -> None:
    sql = (
        "(SELECT region AS r FROM dm.sales_daily) "
        "ORDER BY (SELECT max(s.category) FROM dm.sales_other AS s)"
    )
    guard_sql(sql, dialect="postgres")
    assert _reads(sql) == {"dm.sales_daily.region", "dm.sales_other.category"}
    refs = extract_column_refs(sql, dialect="postgres")
    assert not refs.ambiguous and not refs.unattributed


def test_root_parenthesized_offset_subquery_keeps_its_reads() -> None:
    sql = "(SELECT region FROM dm.sales_daily) OFFSET (SELECT l.n FROM dm.limits AS l)"
    guard_sql(sql, dialect="postgres")
    assert _reads(sql) == {"dm.sales_daily.region", "dm.limits.n"}


@pytest.mark.parametrize(
    "sql",
    [
        "(SELECT region FROM dm.sales_daily ORDER BY region) ORDER BY region",
        "(SELECT region FROM dm.sales_daily LIMIT 1) LIMIT 2",
    ],
)
def test_root_parenthesized_duplicate_modifiers_stay_rejected(sql: str) -> None:
    with pytest.raises(SQLGuardError, match="only SELECT is allowed"):
        guard_sql(sql, dialect="postgres")


def test_clickhouse_root_parenthesized_select_stays_rejected() -> None:
    sql = "(SELECT region AS r FROM dm.sales_daily) ORDER BY r"
    with pytest.raises(SQLGuardError, match="only SELECT is allowed"):
        guard_sql(sql, dialect="clickhouse")
