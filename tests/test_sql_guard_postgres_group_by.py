"""PostgreSQL resolves a whole GROUP BY name through inputs, then SELECT outputs."""

from pathlib import Path

import pytest

from auto_bi.agent.sql_guard import extract_column_refs
from tests.test_sql_guard_clickhouse_alias import (
    ungoverned_codes,
    ungoverned_detail,
    ungoverned_pairs,
)


def _attributed(sql: str, *, dialect: str = "postgres") -> set[str]:
    reads = extract_column_refs(sql, dialect=dialect)
    return {f"{ref.table.name}.{ref.name}" for ref in reads.attributed}


BASE = (
    "SELECT date_trunc('month', date) AS month, sum(revenue) AS total "
    "FROM dm.sales_daily GROUP BY "
)


@pytest.mark.parametrize(
    "group, extra",
    [
        ("month", set()),
        ("ROLLUP (month)", set()),
        ("CUBE (month)", set()),
        ("GROUPING SETS ((month), ())", set()),
        ("(month)", set()),
        ("GROUPING SETS ((month, category), category)", {"dm.sales_daily.category"}),
        ("GROUPING SETS (((month)))", set()),
    ],
)
def test_whole_grouping_names_leave_an_output_remainder(
    tmp_path: Path, group: str, extra: set[str]
) -> None:
    sql = BASE + group
    reads = extract_column_refs(sql, dialect="postgres")
    assert _attributed(sql) == {"dm.sales_daily.date", "dm.sales_daily.revenue"} | extra
    assert reads.unattributed == frozenset({"month"})
    assert {ref.name for ref in reads.ambiguous if ref.output_column} == {"month"}
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_fields_unchecked", "advisory")
    ]
    detail = ungoverned_detail(tmp_path / "detail", sql, engine="postgres")
    assert "month" in detail and "GROUP BY" in detail and "output column" in detail


def test_declared_input_wins_over_output_alias(tmp_path: Path) -> None:
    sql = "SELECT upper(region) AS region, count(*) FROM dm.sales_daily GROUP BY region"
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_join_without_declarer_is_unchecked(tmp_path: Path) -> None:
    sql = (
        "SELECT upper(s.region) AS r, count(*) FROM dm.sales_daily AS s "
        "JOIN dm.stores AS t ON s.store_id = t.id GROUP BY r"
    )
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_fields_unchecked", "advisory")
    ]
    assert "GROUP BY" in ungoverned_detail(tmp_path / "detail", sql, engine="postgres")


def test_join_with_one_declarer_is_clean(tmp_path: Path) -> None:
    sql = (
        "SELECT upper(s.region) AS region, count(*) FROM dm.sales_daily AS s "
        "JOIN dm.stores AS t ON s.store_id = t.id GROUP BY region"
    )
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


@pytest.mark.parametrize(
    "sql, name",
    [
        (
            "SELECT date_trunc('month', date) AS month FROM dm.sales_daily "
            "GROUP BY lower(month)",
            "dm.sales_daily.month",
        ),
        (
            "SELECT date_trunc('month', date) AS month FROM dm.sales_daily " "GROUP BY month + 0",
            "dm.sales_daily.month",
        ),
        (
            "SELECT region, sum(revenue) AS total FROM dm.sales_daily GROUP BY region, total",
            "dm.sales_daily.total",
        ),
        (
            "SELECT region, rank() OVER (ORDER BY region) AS rn "
            "FROM dm.sales_daily GROUP BY region, rn",
            "dm.sales_daily.rn",
        ),
        (
            "SELECT date_trunc('month', date) AS \"Month\", count(*) "
            "FROM dm.sales_daily GROUP BY month",
            "dm.sales_daily.month",
        ),
        (
            "SELECT date_trunc('month', s.date) AS month FROM dm.sales_daily AS s "
            "GROUP BY s.month",
            "dm.sales_daily.month",
        ),
    ],
)
def test_non_output_grouping_reads_input(tmp_path: Path, sql: str, name: str) -> None:
    assert name in ungoverned_detail(tmp_path, sql, engine="postgres")
    assert "asset.query_ungoverned_field" in ungoverned_codes(
        tmp_path / "codes", sql, engine="postgres"
    )


def test_simple_same_name_column_is_input(tmp_path: Path) -> None:
    sql = "SELECT s.region AS region, count(*) FROM dm.sales_daily AS s GROUP BY region"
    assert _attributed(sql) == {"dm.sales_daily.region"}
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_unquoted_output_alias_folds(tmp_path: Path) -> None:
    sql = (
        "SELECT date_trunc('month', date) AS Month, count(*) " "FROM dm.sales_daily GROUP BY MONTH"
    )
    reads = extract_column_refs(sql, dialect="postgres")
    assert {ref.name for ref in reads.ambiguous if ref.output_column} == {"MONTH"}
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_fields_unchecked", "advisory")
    ]


def test_one_eligible_output_among_duplicate_names_qualifies() -> None:
    sql = (
        "SELECT sum(revenue) AS month, date_trunc('month', date) AS month "
        "FROM dm.sales_daily GROUP BY month"
    )
    reads = extract_column_refs(sql, dialect="postgres")
    assert any(ref.name == "month" and ref.output_column for ref in reads.ambiguous)


def test_output_name_without_sources_is_complete(tmp_path: Path) -> None:
    """HEAD reported the sourceless m as an unchecked field."""
    sql = "SELECT 1 AS m GROUP BY m"
    reads = extract_column_refs(sql, dialect="postgres")
    assert not reads.attributed and not reads.ambiguous and not reads.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_lateral_grouping_output_without_own_source_is_complete(tmp_path: Path) -> None:
    sql = "SELECT o.region FROM dm.sales_daily AS o, " "LATERAL (SELECT 1 AS m GROUP BY m) AS x"
    reads = extract_column_refs(sql, dialect="postgres")
    assert "m" not in reads.unattributed
    assert all(ref.name != "m" for ref in reads.ambiguous)
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_lateral_grouping_name_uses_only_own_sources(tmp_path: Path) -> None:
    sql = (
        "SELECT t.id FROM dm.stores AS t, LATERAL "
        "(SELECT upper(s.region) AS id FROM dm.sales_daily AS s GROUP BY id) AS x"
    )
    reads = extract_column_refs(sql, dialect="postgres")
    grouping_refs = [ref for ref in reads.ambiguous if ref.name == "id" and ref.output_column]
    assert len(grouping_refs) == 1
    assert tuple(table.name for table in grouping_refs[0].candidates) == ("dm.sales_daily",)
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_fields_unchecked", "advisory")
    ]
    detail = ungoverned_detail(tmp_path / "detail", sql, engine="postgres")
    assert "id" in detail and "GROUP BY" in detail and "output column" in detail


def test_output_name_precedes_enclosing_query(tmp_path: Path) -> None:
    sql = (
        "SELECT region FROM dm.sales_daily AS o WHERE EXISTS "
        "(SELECT upper(i.region) AS r FROM dm.sales_daily AS i GROUP BY r)"
    )
    detail = ungoverned_detail(tmp_path, sql, engine="postgres")
    assert "r" in detail and "GROUP BY" in detail and "output column" in detail
    assert "enclosing query" not in detail


@pytest.mark.parametrize(
    "sql",
    [
        "WITH x AS (SELECT date FROM dm.sales_daily) "
        "SELECT date_trunc('month', date) AS month FROM x GROUP BY month",
        "SELECT date_trunc('month', date) AS month "
        "FROM (SELECT date FROM dm.sales_daily) AS x GROUP BY month",
    ],
)
def test_only_non_table_source_keeps_its_existing_result(tmp_path: Path, sql: str) -> None:
    reads = extract_column_refs(sql, dialect="postgres")
    assert not reads.ambiguous and "month" not in reads.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_mixed_table_and_derived_source_keeps_unseen_remainder(tmp_path: Path) -> None:
    sql = (
        "SELECT upper(s.region) AS r FROM dm.sales_daily AS s "
        "JOIN (SELECT id FROM dm.stores) AS t ON s.store_id = t.id GROUP BY r"
    )
    reads = extract_column_refs(sql, dialect="postgres")
    assert any(
        ref.name == "r" and ref.output_column and not ref.all_tables for ref in reads.ambiguous
    )
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_fields_unchecked", "advisory")
    ]
    assert "source this snapshot cannot see into" in ungoverned_detail(
        tmp_path / "detail", sql, engine="postgres"
    )


def test_two_declarers_stay_contested(tmp_path: Path) -> None:
    sql = (
        "SELECT upper(a.region) AS region FROM dm.sales_daily a "
        "JOIN dm.sales_daily b ON a.store_id = b.store_id GROUP BY region"
    )
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_fields_unchecked", "advisory")
    ]


def test_clickhouse_grouping_is_unchanged(tmp_path: Path) -> None:
    sql = "SELECT toStartOfMonth(date) AS month, sum(revenue) " "FROM dm.sales_daily GROUP BY month"
    assert _attributed(sql, dialect="clickhouse") == {
        "dm.sales_daily.date",
        "dm.sales_daily.revenue",
    }
    assert ungoverned_codes(tmp_path, sql) == []
