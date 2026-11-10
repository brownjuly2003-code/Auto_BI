"""ClickHouse governance reports stars only in parts of the query that run.

The shared SQL extractor keeps ``ColumnReads.starred`` as a whole-AST compatibility
bit.  These tests pin the narrower governance contract: a star in an unread named CTE
or scalar ``WITH`` does not make the reference query partial, while a reachable star
still produces the existing advisory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_bi.agent.sql_guard import extract_column_refs
from tests.test_sql_guard_clickhouse_alias import (
    ungoverned_codes,
    ungoverned_detail,
    ungoverned_pairs,
)


def test_an_unread_named_cte_star_does_not_make_governance_partial(tmp_path: Path) -> None:
    sql = "WITH dormant AS (SELECT * FROM dm.sales_daily) " "SELECT revenue FROM dm.sales_daily"

    # ``starred`` remains the extractor's whole-AST compatibility bit.
    assert extract_column_refs(sql, dialect="clickhouse").starred is True
    assert ungoverned_codes(tmp_path, sql) == []


def test_an_unread_scalar_with_star_does_not_make_governance_partial(tmp_path: Path) -> None:
    sql = "WITH (SELECT * FROM dm.sales_daily) AS dormant " "SELECT revenue FROM dm.sales_daily"

    assert extract_column_refs(sql, dialect="clickhouse").starred is True
    assert ungoverned_codes(tmp_path, sql) == []


@pytest.mark.parametrize(
    "sql",
    [
        "WITH live AS (SELECT * FROM dm.sales_daily) SELECT * FROM live",
        "WITH (SELECT * FROM dm.sales_daily) AS live SELECT live FROM dm.sales_daily",
        (
            "WITH dormant AS (SELECT * FROM dm.sales_daily), "
            "live AS (SELECT revenue FROM dormant) SELECT * FROM live"
        ),
        (
            "WITH tuple(*) AS a "
            "SELECT sum(revenue) FROM (WITH 1 AS a SELECT revenue, a FROM dm.sales_daily) "
            "UNION ALL SELECT a FROM dm.sales_daily"
        ),
    ],
    ids=["named-cte", "scalar-with", "named-cte-chain", "outer-shadowed-independent-read"],
)
def test_reachable_stars_keep_the_existing_partial_advisory(tmp_path: Path, sql: str) -> None:
    assert extract_column_refs(sql, dialect="clickhouse").starred is True
    assert ("asset.query_fields_unchecked", "advisory") in ungoverned_pairs(tmp_path, sql)
    assert "it selects *" in ungoverned_detail(tmp_path / "detail", sql)


def test_a_reader_only_in_an_unread_named_cte_does_not_open_a_nested_star(
    tmp_path: Path,
) -> None:
    sql = (
        "WITH (SELECT * FROM dm.other) AS scalar_value, "
        "reader AS (SELECT scalar_value AS value FROM dm.other) "
        "SELECT sum(revenue) FROM dm.sales_daily"
    )

    assert extract_column_refs(sql, dialect="clickhouse").starred is True
    assert ungoverned_pairs(tmp_path, sql) == [("asset.query_ungoverned", "review")]


def test_a_matching_table_alias_does_not_open_a_scalar_with_star(tmp_path: Path) -> None:
    sql = "WITH tuple(*) AS t SELECT t.region, sum(t.revenue) FROM dm.sales_daily AS t"

    assert extract_column_refs(sql, dialect="clickhouse").starred is True
    assert ungoverned_codes(tmp_path, sql) == []


def test_a_qualified_star_in_a_live_table_keeps_the_partial_advisory(tmp_path: Path) -> None:
    sql = "SELECT dm.sales_daily.* FROM dm.sales_daily"

    assert extract_column_refs(sql, dialect="clickhouse").starred is True
    assert ungoverned_pairs(tmp_path, sql) == [("asset.query_fields_unchecked", "advisory")]
    assert "it selects *" in ungoverned_detail(tmp_path / "detail", sql)


def test_count_distinct_star_keeps_the_partial_advisory(tmp_path: Path) -> None:
    sql = "SELECT COUNT(DISTINCT *) FROM dm.sales_daily"

    assert extract_column_refs(sql, dialect="clickhouse").starred is True
    assert ungoverned_pairs(tmp_path, sql) == [("asset.query_fields_unchecked", "advisory")]
    assert "it selects *" in ungoverned_detail(tmp_path / "detail", sql)


def test_count_star_remains_a_row_count_without_a_partial_advisory(tmp_path: Path) -> None:
    sql = "SELECT COUNT(*) FROM dm.sales_daily"

    assert extract_column_refs(sql, dialect="clickhouse").starred is False
    assert ungoverned_codes(tmp_path, sql) == []


def test_count_star_over_a_live_select_star_keeps_the_star_advisory(tmp_path: Path) -> None:
    sql = "WITH live AS (SELECT * FROM dm.sales_daily) SELECT COUNT(*) FROM live"

    assert extract_column_refs(sql, dialect="clickhouse").starred is True
    assert ("asset.query_fields_unchecked", "advisory") in ungoverned_pairs(tmp_path, sql)
    assert "it selects *" in ungoverned_detail(tmp_path / "detail", sql)


def test_in_table_keeps_the_existing_table_and_star_findings(tmp_path: Path) -> None:
    sql = "SELECT revenue FROM dm.sales_daily WHERE region IN dm.rev"

    assert ungoverned_pairs(tmp_path, sql) == [
        ("asset.query_ungoverned", "review"),
        ("asset.query_fields_unchecked", "advisory"),
    ]
    assert "it selects *" in ungoverned_detail(tmp_path / "detail", sql)


def test_an_unread_in_table_keeps_table_review_without_a_star_advisory(
    tmp_path: Path,
) -> None:
    sql = (
        "WITH dormant AS ("
        "SELECT revenue FROM dm.sales_daily WHERE region IN dm.rev"
        ") SELECT revenue FROM dm.sales_daily"
    )

    pairs = ungoverned_pairs(tmp_path, sql)
    detail = ungoverned_detail(tmp_path / "detail", sql)
    assert pairs == [("asset.query_ungoverned", "review")]
    assert "dm.rev" in detail
    assert "it selects *" not in detail


def test_a_scalar_alias_chain_keeps_a_reachable_star_advisory(tmp_path: Path) -> None:
    sql = "WITH tuple(*) AS a, a AS b SELECT b, sum(revenue) FROM dm.sales_daily"

    assert extract_column_refs(sql, dialect="clickhouse").starred is True
    assert ("asset.query_fields_unchecked", "advisory") in ungoverned_pairs(tmp_path, sql)
    assert "it selects *" in ungoverned_detail(tmp_path / "detail", sql)


@pytest.mark.parametrize("access", ["t.1", "t.a.b"], ids=["tuple-element", "tuple-path"])
def test_tuple_access_keeps_a_scalar_star_reachable(tmp_path: Path, access: str) -> None:
    sql = f"WITH tuple(*) AS t SELECT {access}, sum(revenue) FROM dm.sales_daily"

    assert extract_column_refs(sql, dialect="clickhouse").starred is True
    assert ("asset.query_fields_unchecked", "advisory") in ungoverned_pairs(tmp_path, sql)
    assert "it selects *" in ungoverned_detail(tmp_path / "detail", sql)


def test_nearest_nested_scalar_with_shadows_the_outer_star(tmp_path: Path) -> None:
    sql = (
        "WITH tuple(*) AS t "
        "SELECT sum(revenue) FROM (WITH 1 AS t SELECT revenue, t FROM dm.sales_daily)"
    )

    detail = ungoverned_detail(tmp_path, sql)
    assert "it selects *" not in detail


def test_in_visible_scalar_array_alias_is_not_rewritten_as_a_table(tmp_path: Path) -> None:
    sql = (
        "WITH ['north', 'south'] AS allowed "
        "SELECT region FROM dm.sales_daily WHERE region IN allowed"
    )

    assert extract_column_refs(sql, dialect="clickhouse").starred is False
    assert ungoverned_codes(tmp_path, sql) == []


def test_postgres_keeps_the_existing_whole_ast_star_advisory(tmp_path: Path) -> None:
    sql = "WITH dormant AS (SELECT * FROM dm.sales_daily) " "SELECT revenue FROM dm.sales_daily"

    assert extract_column_refs(sql, dialect="postgres").starred is True
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_fields_unchecked", "advisory")
    ]
    assert "it selects *" in ungoverned_detail(tmp_path / "detail", sql, engine="postgres")


def test_an_unread_named_cte_field_keeps_its_existing_explicit_finding(
    tmp_path: Path,
) -> None:
    sql = (
        "WITH dormant AS (SELECT internal_cost_raw FROM dm.sales_daily) "
        "SELECT revenue FROM dm.sales_daily"
    )

    assert ungoverned_codes(tmp_path, sql) == ["asset.query_ungoverned_field"]
    assert "dm.sales_daily.internal_cost_raw" in ungoverned_detail(tmp_path / "detail", sql)


def test_columns_selector_keeps_its_existing_advisory(tmp_path: Path) -> None:
    sql = "SELECT COLUMNS('rev.*') FROM dm.sales_daily"

    assert ungoverned_pairs(tmp_path, sql) == [("asset.query_fields_unchecked", "advisory")]
    assert "COLUMNS(...)" in ungoverned_detail(tmp_path / "detail", sql)


def test_join_using_keeps_its_existing_advisory(tmp_path: Path) -> None:
    sql = "SELECT revenue FROM dm.sales_daily JOIN dm.stores USING (region)"

    assert ungoverned_pairs(tmp_path, sql) == [("asset.query_fields_unchecked", "advisory")]
    assert "region" in ungoverned_detail(tmp_path / "detail", sql)
