"""ClickHouse governance reports COLUMNS selectors only in query parts that run."""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_bi.agent.sql_guard import extract_column_refs
from tests.test_sql_guard_clickhouse_alias import (
    ungoverned_codes,
    ungoverned_detail,
    ungoverned_pairs,
)


def test_an_unread_named_cte_columns_selector_does_not_make_governance_partial(
    tmp_path: Path,
) -> None:
    sql = (
        "WITH dormant AS (SELECT COLUMNS('rev.*') FROM dm.sales_daily) "
        "SELECT revenue FROM dm.sales_daily"
    )

    # The shared extractor remains whole-AST; governance must narrow reachability.
    assert extract_column_refs(sql, dialect="clickhouse").column_selectors == frozenset(
        {"COLUMNS(...)"}
    )
    assert ungoverned_codes(tmp_path, sql) == []


def test_an_unread_scalar_with_columns_selector_does_not_make_governance_partial(
    tmp_path: Path,
) -> None:
    sql = (
        "WITH (SELECT COLUMNS('rev.*') FROM dm.sales_daily) AS dormant "
        "SELECT revenue FROM dm.sales_daily"
    )

    assert extract_column_refs(sql, dialect="clickhouse").column_selectors == frozenset(
        {"COLUMNS(...)"}
    )
    assert ungoverned_codes(tmp_path, sql) == []


@pytest.mark.parametrize(
    "sql",
    [
        "WITH live AS (SELECT COLUMNS('rev.*') FROM dm.sales_daily) SELECT revenue FROM live",
        (
            "WITH (SELECT COLUMNS('rev.*') FROM dm.sales_daily) AS live "
            "SELECT live FROM dm.sales_daily"
        ),
        (
            "WITH dormant AS (SELECT COLUMNS('rev.*') FROM dm.sales_daily), "
            "live AS (SELECT revenue FROM dormant) SELECT revenue FROM live"
        ),
        "SELECT (SELECT COLUMNS('rev.*') FROM dm.sales_daily) FROM dm.sales_daily",
    ],
    ids=["named-cte", "scalar-with", "named-cte-chain", "nested-select"],
)
def test_reachable_columns_selectors_keep_the_partial_advisory(tmp_path: Path, sql: str) -> None:
    assert extract_column_refs(sql, dialect="clickhouse").column_selectors == frozenset(
        {"COLUMNS(...)"}
    )
    assert ungoverned_pairs(tmp_path, sql) == [("asset.query_fields_unchecked", "advisory")]
    assert "COLUMNS(...)" in ungoverned_detail(tmp_path / "detail", sql)


def test_nested_named_cte_shadowing_keeps_the_outer_selector_unread(tmp_path: Path) -> None:
    sql = (
        "WITH A AS (SELECT COLUMNS('rev.*') FROM dm.sales_daily) "
        "SELECT revenue FROM (WITH A AS (SELECT revenue FROM dm.sales_daily) "
        "SELECT revenue FROM A)"
    )

    assert ungoverned_codes(tmp_path, sql) == []


def test_a_selector_referenced_only_from_an_unread_named_cte_stays_unchecked(
    tmp_path: Path,
) -> None:
    sql = (
        "WITH dormant AS (SELECT COLUMNS('rev.*') FROM dm.sales_daily), "
        "reader AS (SELECT revenue FROM dormant) "
        "SELECT revenue FROM dm.sales_daily"
    )

    assert ungoverned_codes(tmp_path, sql) == []


def test_a_selector_referenced_only_from_an_unread_scalar_chain_stays_unchecked(
    tmp_path: Path,
) -> None:
    sql = (
        "WITH (SELECT COLUMNS('rev.*') FROM dm.sales_daily) AS dormant, "
        "dormant AS reader SELECT revenue FROM dm.sales_daily"
    )

    assert ungoverned_codes(tmp_path, sql) == []


def test_dead_and_live_selectors_keep_one_reachable_partial_advisory(tmp_path: Path) -> None:
    sql = (
        "WITH dormant AS (SELECT COLUMNS('rev.*') FROM dm.sales_daily), "
        "live AS (SELECT COLUMNS('rev.*') FROM dm.sales_daily) "
        "SELECT revenue FROM live"
    )

    assert ungoverned_pairs(tmp_path, sql) == [("asset.query_fields_unchecked", "advisory")]
    assert "COLUMNS(...)" in ungoverned_detail(tmp_path / "detail", sql)


@pytest.mark.parametrize(
    "sql,expected",
    [
        (
            "WITH A AS (SELECT COLUMNS('rev.*') FROM dm.sales_daily), "
            "a AS (SELECT revenue FROM dm.sales_daily) SELECT revenue FROM a",
            [],
        ),
        (
            "WITH A AS (SELECT COLUMNS('rev.*') FROM dm.sales_daily), "
            "a AS (SELECT revenue FROM dm.sales_daily) SELECT revenue FROM A",
            [("asset.query_fields_unchecked", "advisory")],
        ),
    ],
    ids=["lowercase-reference-keeps-uppercase-unread", "uppercase-reference-opens-uppercase"],
)
def test_clickhouse_cte_names_remain_case_sensitive(
    tmp_path: Path, sql: str, expected: list[tuple[str, str]]
) -> None:
    assert ungoverned_pairs(tmp_path, sql) == expected


def test_an_unread_selector_does_not_hide_an_unknown_field(tmp_path: Path) -> None:
    sql = (
        "WITH dormant AS (SELECT COLUMNS('rev.*'), internal_cost_raw FROM dm.sales_daily) "
        "SELECT revenue FROM dm.sales_daily"
    )

    assert ungoverned_codes(tmp_path, sql) == ["asset.query_ungoverned_field"]
    assert "dm.sales_daily.internal_cost_raw" in ungoverned_detail(tmp_path / "detail", sql)


def test_an_unread_selector_does_not_hide_an_unknown_table(tmp_path: Path) -> None:
    sql = (
        "WITH dormant AS (SELECT COLUMNS('rev.*') FROM dm.unknown_table) "
        "SELECT revenue FROM dm.sales_daily"
    )

    assert ungoverned_codes(tmp_path, sql) == ["asset.query_ungoverned"]
    assert "dm.unknown_table" in ungoverned_detail(tmp_path / "detail", sql)


def test_join_using_keeps_its_existing_advisory(tmp_path: Path) -> None:
    sql = (
        "WITH dormant AS (SELECT COLUMNS('rev.*') FROM dm.sales_daily) "
        "SELECT revenue FROM dm.sales_daily JOIN dm.stores USING (region)"
    )

    assert ungoverned_pairs(tmp_path, sql) == [("asset.query_fields_unchecked", "advisory")]
    assert "region" in ungoverned_detail(tmp_path / "detail", sql)


def test_a_reachable_star_keeps_its_existing_advisory(tmp_path: Path) -> None:
    sql = (
        "WITH dormant AS (SELECT COLUMNS('rev.*') FROM dm.sales_daily), "
        "live AS (SELECT * FROM dm.sales_daily) SELECT revenue FROM live"
    )

    assert ungoverned_pairs(tmp_path, sql) == [("asset.query_fields_unchecked", "advisory")]
    assert "it selects *" in ungoverned_detail(tmp_path / "detail", sql)


def test_postgres_keeps_the_existing_whole_ast_columns_contract(tmp_path: Path) -> None:
    sql = (
        "WITH dormant AS (SELECT COLUMNS('rev.*') FROM dm.sales_daily) "
        "SELECT revenue FROM dm.sales_daily"
    )

    assert extract_column_refs(sql, dialect="postgres").column_selectors == frozenset()
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []
