"""PostgreSQL table aliases with column lists hide physical ordinal lineage."""

from pathlib import Path

import pytest

from auto_bi.agent.sql_guard import extract_column_refs
from tests.test_sql_guard_clickhouse_alias import ungoverned_codes, ungoverned_detail


def test_qualified_alias_output_name_is_only_advisory(tmp_path: Path) -> None:
    sql = "SELECT s.renamed FROM dm.sales_daily AS s(renamed)"

    assert ungoverned_codes(tmp_path, sql, engine="postgres") == ["asset.query_fields_unchecked"]
    detail = ungoverned_detail(tmp_path / "detail", sql, engine="postgres")
    assert "PostgreSQL table column alias list" in detail
    assert "ordinal" in detail
    assert "source no enclosing scope declares" not in detail


def test_bare_alias_output_name_is_only_advisory(tmp_path: Path) -> None:
    sql = "SELECT renamed FROM dm.sales_daily AS s(renamed)"

    assert ungoverned_codes(tmp_path, sql, engine="postgres") == ["asset.query_fields_unchecked"]
    detail = ungoverned_detail(tmp_path / "detail", sql, engine="postgres")
    assert "source this snapshot cannot see into" not in detail


def test_alias_output_name_does_not_settle_governed_physical_name(tmp_path: Path) -> None:
    sql = "SELECT s.revenue FROM dm.sales_daily AS s(revenue, region, declared_revenue)"

    assert ungoverned_codes(tmp_path, sql, engine="postgres") == ["asset.query_fields_unchecked"]


def test_bare_alias_name_in_multi_source_scope_stays_advisory(tmp_path: Path) -> None:
    sql = (
        "SELECT revenue FROM dm.sales_daily AS s(revenue, region, declared_revenue) "
        "CROSS JOIN dm.stores AS t"
    )

    assert ungoverned_codes(tmp_path, sql, engine="postgres") == ["asset.query_fields_unchecked"]


def test_correlated_alias_output_name_stays_advisory(tmp_path: Path) -> None:
    sql = (
        "SELECT s.renamed FROM dm.sales_daily AS s(renamed) "
        "WHERE EXISTS (SELECT 1 FROM dm.stores AS t WHERE t.id = s.renamed)"
    )

    assert ungoverned_codes(tmp_path, sql, engine="postgres") == ["asset.query_fields_unchecked"]


def test_neighboring_real_field_remains_graded(tmp_path: Path) -> None:
    sql = (
        "SELECT s.renamed, t.id FROM dm.sales_daily AS s(renamed) "
        "JOIN dm.stores AS t ON t.id = s.store_id"
    )

    assert ungoverned_codes(tmp_path, sql, engine="postgres") == ["asset.query_fields_unchecked"]
    assert "dm.stores.id" not in ungoverned_detail(tmp_path / "detail", sql, engine="postgres")


def test_physical_name_outside_alias_list_keeps_ordinary_grading(tmp_path: Path) -> None:
    sql = "SELECT s.revenue FROM dm.sales_daily AS s(renamed)"

    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_order_output_alias_still_precedes_table_column_alias_list(tmp_path: Path) -> None:
    sql = "SELECT 1 AS renamed FROM dm.sales_daily AS s(renamed) ORDER BY renamed"

    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_lateral_group_output_alias_precedes_outer_table_column_alias(tmp_path: Path) -> None:
    sql = (
        "SELECT d.x FROM dm.sales_daily AS s(x) "
        "CROSS JOIN LATERAL (SELECT 1 AS x GROUP BY x) AS d"
    )

    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_parenthesized_join_table_column_alias_stays_advisory(tmp_path: Path) -> None:
    sql = (
        "SELECT s.renamed FROM (dm.sales_daily JOIN dm.stores "
        "ON sales_daily.store_id = stores.id) AS s(renamed)"
    )

    assert ungoverned_codes(tmp_path, sql, engine="postgres") == ["asset.query_fields_unchecked"]


@pytest.mark.parametrize(
    "tail",
    ["", " CROSS JOIN dm.stores AS t"],
)
def test_bare_parenthesized_join_column_alias_stays_advisory(tmp_path: Path, tail: str) -> None:
    sql = (
        "SELECT renamed FROM (dm.sales_daily JOIN dm.stores "
        f"ON sales_daily.store_id = stores.id) AS s(renamed){tail}"
    )

    assert ungoverned_codes(tmp_path, sql, engine="postgres") == ["asset.query_fields_unchecked"]


def test_nested_parenthesized_join_column_alias_stays_advisory(tmp_path: Path) -> None:
    sql = (
        "SELECT s.renamed FROM ((dm.sales_daily JOIN dm.stores "
        "ON sales_daily.store_id = stores.id)) AS s(renamed)"
    )

    assert ungoverned_codes(tmp_path, sql, engine="postgres") == ["asset.query_fields_unchecked"]


def test_parenthesized_join_on_keeps_physical_field_review(tmp_path: Path) -> None:
    sql = (
        "SELECT s.renamed FROM (dm.sales_daily JOIN dm.stores "
        "ON sales_daily.renamed = stores.id) AS s(renamed)"
    )

    assert ungoverned_codes(tmp_path, sql, engine="postgres") == [
        "asset.query_ungoverned_field",
        "asset.query_fields_unchecked",
    ]
    detail = ungoverned_detail(tmp_path / "detail", sql, engine="postgres")
    assert "dm.sales_daily.renamed" in detail


def test_quoted_alias_output_name_uses_postgres_exact_matching(tmp_path: Path) -> None:
    sql = 'SELECT s."Renamed" FROM dm.sales_daily AS s("Renamed")'

    assert ungoverned_codes(tmp_path, sql, engine="postgres") == ["asset.query_fields_unchecked"]


@pytest.mark.parametrize(
    "sql",
    [
        (
            "WITH q AS ("
            "SELECT s.renamed FROM dm.sales_daily AS s(renamed)"
            ") SELECT q.renamed FROM q"
        ),
        ("SELECT d.renamed FROM (" "SELECT s.renamed FROM dm.sales_daily AS s(renamed)" ") AS d"),
    ],
)
def test_alias_output_name_stays_inside_cte_and_derived_scopes(tmp_path: Path, sql: str) -> None:
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == ["asset.query_fields_unchecked"]


def test_unknown_neighboring_physical_field_keeps_review(tmp_path: Path) -> None:
    sql = (
        "SELECT s.renamed, s.internal_cost_raw, t.ghost "
        "FROM dm.sales_daily AS s(renamed) "
        "JOIN dm.stores AS t ON t.id = s.store_id"
    )

    assert ungoverned_codes(tmp_path, sql, engine="postgres") == [
        "asset.query_ungoverned_field",
        "asset.query_fields_unchecked",
    ]
    detail = ungoverned_detail(tmp_path / "detail", sql, engine="postgres")
    assert "dm.sales_daily.internal_cost_raw" in detail
    assert "dm.stores.ghost" in detail


def test_plain_table_alias_keeps_existing_behavior(tmp_path: Path) -> None:
    sql = "SELECT s.revenue FROM dm.sales_daily AS s"

    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_clickhouse_table_alias_behavior_is_unchanged(tmp_path: Path) -> None:
    sql = "SELECT s.revenue FROM dm.sales_daily AS s"

    assert ungoverned_codes(tmp_path, sql, engine="clickhouse") == []


def test_alias_list_remainder_is_typed_for_advisory_grading() -> None:
    sql = "SELECT s.renamed FROM dm.sales_daily AS s(renamed)"
    reads = extract_column_refs(sql, dialect="postgres")

    assert any(ref.postgres_table_column_alias for ref in reads.ambiguous)
