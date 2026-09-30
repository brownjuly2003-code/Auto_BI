"""ClickHouse governance reports JOIN USING keys only in query parts that run."""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_bi.agent.sql_guard import extract_column_refs
from tests.test_sql_guard_clickhouse_alias import (
    ungoverned_codes,
    ungoverned_detail,
    ungoverned_pairs,
)


def test_an_unread_named_cte_join_using_does_not_make_governance_partial(
    tmp_path: Path,
) -> None:
    sql = (
        "WITH dormant AS ("
        "SELECT 1 FROM dm.sales_daily AS a JOIN dm.sales_daily AS b USING (region)"
        ") SELECT revenue FROM dm.sales_daily"
    )

    assert ungoverned_pairs(tmp_path, sql) == []


def test_an_unread_scalar_with_join_using_does_not_make_governance_partial(
    tmp_path: Path,
) -> None:
    sql = (
        "WITH ("
        "SELECT any(a.region) FROM dm.sales_daily AS a "
        "JOIN dm.sales_daily AS b USING (region)"
        ") AS dormant SELECT revenue FROM dm.sales_daily"
    )

    assert ungoverned_pairs(tmp_path, sql) == []


def test_dead_join_using_key_does_not_hide_a_live_join_using_key(tmp_path: Path) -> None:
    sql = (
        "WITH dormant AS ("
        "SELECT 1 FROM dm.sales_daily AS a JOIN dm.sales_daily AS b USING (region)"
        ") SELECT a.revenue FROM dm.sales_daily AS a "
        "JOIN dm.sales_daily AS b USING (date)"
    )

    assert ungoverned_pairs(tmp_path, sql) == [("asset.query_fields_unchecked", "advisory")]
    detail = ungoverned_detail(tmp_path / "detail", sql)
    assert "JOIN USING key date" in detail
    assert "region" not in detail


@pytest.mark.parametrize(
    "sql",
    [
        (
            "WITH live AS ("
            "SELECT 1 FROM dm.sales_daily AS a JOIN dm.sales_daily AS b USING (region)"
            ") SELECT 1 FROM live"
        ),
        (
            "WITH ("
            "SELECT any(a.region) FROM dm.sales_daily AS a "
            "JOIN dm.sales_daily AS b USING (region)"
            ") AS live SELECT live FROM dm.sales_daily"
        ),
    ],
    ids=["live-named-cte", "live-scalar-with"],
)
def test_live_named_and_scalar_with_join_using_keys_keep_the_advisory(
    tmp_path: Path, sql: str
) -> None:
    assert ungoverned_pairs(tmp_path, sql) == [("asset.query_fields_unchecked", "advisory")]
    assert "JOIN USING key region" in ungoverned_detail(tmp_path / "detail", sql)


@pytest.mark.parametrize(
    ("dead_key", "live_key"),
    [("region", "date"), ("region", "region"), ("date", "region")],
    ids=["different-keys", "same-key", "reversed-keys"],
)
def test_only_reachable_join_using_keys_are_named(
    tmp_path: Path, dead_key: str, live_key: str
) -> None:
    sql = (
        f"WITH dormant AS (SELECT 1 FROM dm.sales_daily AS a "
        f"JOIN dm.sales_daily AS b USING ({dead_key})) "
        f"SELECT a.revenue FROM dm.sales_daily AS a "
        f"JOIN dm.sales_daily AS b USING ({live_key})"
    )

    detail = ungoverned_detail(tmp_path, sql)
    assert ungoverned_pairs(tmp_path / "pairs", sql) == [
        ("asset.query_fields_unchecked", "advisory")
    ]
    assert f"JOIN USING key {live_key}" in detail
    if dead_key != live_key:
        assert dead_key not in detail


def test_a_chain_of_unread_named_ctes_does_not_open_join_using_keys(tmp_path: Path) -> None:
    sql = (
        "WITH dormant AS (SELECT a.date FROM dm.sales_daily AS a "
        "JOIN dm.sales_daily AS b USING (region)), "
        "wrapper AS (SELECT 1 FROM dormant JOIN dm.sales_daily AS c USING (date)) "
        "SELECT revenue FROM dm.sales_daily"
    )

    assert ungoverned_codes(tmp_path, sql) == []


def test_exact_nested_cte_shadowing_keeps_an_outer_join_unread(tmp_path: Path) -> None:
    sql = (
        "WITH dormant AS (SELECT 1 FROM dm.sales_daily AS a "
        "JOIN dm.sales_daily AS b USING (region)) "
        "SELECT revenue FROM (WITH dormant AS (SELECT revenue FROM dm.sales_daily) "
        "SELECT revenue FROM dormant)"
    )

    assert ungoverned_codes(tmp_path, sql) == []


@pytest.mark.parametrize(
    ("reference", "live_key", "dead_key"),
    [("A", "region", "date"), ("a", "date", "region")],
    ids=["uppercase-reference", "lowercase-reference"],
)
def test_clickhouse_named_cte_join_using_reachability_remains_case_sensitive(
    tmp_path: Path, reference: str, live_key: str, dead_key: str
) -> None:
    sql = (
        "WITH A AS (SELECT 1 FROM dm.sales_daily AS x "
        "JOIN dm.sales_daily AS y USING (region)), "
        "a AS (SELECT 1 FROM dm.sales_daily AS x "
        "JOIN dm.sales_daily AS y USING (date)) "
        f"SELECT 1 FROM {reference}"
    )

    detail = ungoverned_detail(tmp_path, sql)
    assert ungoverned_pairs(tmp_path / "pairs", sql) == [
        ("asset.query_fields_unchecked", "advisory")
    ]
    assert f"JOIN USING key {live_key}" in detail
    assert dead_key not in detail


def test_in_named_cte_opens_its_join_using_key(tmp_path: Path) -> None:
    sql = (
        "WITH live AS (SELECT a.region FROM dm.sales_daily AS a "
        "JOIN dm.sales_daily AS b USING (region)) "
        "SELECT revenue FROM dm.sales_daily WHERE region IN live"
    )

    detail = ungoverned_detail(tmp_path, sql)
    assert ungoverned_pairs(tmp_path / "pairs", sql) == [
        ("asset.query_fields_unchecked", "advisory")
    ]
    assert "JOIN USING key region" in detail
    assert "it selects *" in detail


def test_unread_join_using_does_not_hide_an_unknown_field(tmp_path: Path) -> None:
    sql = (
        "WITH dormant AS (SELECT a.internal_cost_raw FROM dm.sales_daily AS a "
        "JOIN dm.sales_daily AS b USING (region)) "
        "SELECT revenue FROM dm.sales_daily"
    )

    assert ungoverned_codes(tmp_path, sql) == ["asset.query_ungoverned_field"]
    detail = ungoverned_detail(tmp_path / "detail", sql)
    assert "dm.sales_daily.internal_cost_raw" in detail
    assert "JOIN USING" not in detail


def test_unread_join_using_does_not_hide_an_unknown_table(tmp_path: Path) -> None:
    sql = (
        "WITH dormant AS (SELECT a.region FROM dm.unknown_table AS a "
        "JOIN dm.sales_daily AS b USING (region)) "
        "SELECT revenue FROM dm.sales_daily"
    )

    assert ungoverned_codes(tmp_path, sql) == ["asset.query_ungoverned"]
    detail = ungoverned_detail(tmp_path / "detail", sql)
    assert "dm.unknown_table" in detail
    assert "JOIN USING" not in detail


@pytest.mark.parametrize(
    ("projection", "marker"),
    [
        ("*", "it selects *"),
        ("COLUMNS('rev.*')", "COLUMNS(...)"),
        (
            "revenue FROM dm.sales_daily SETTINGS additional_table_filters = "
            "{'dm.sales_daily': 'cost > 0'}",
            "additional_table_filters",
        ),
    ],
    ids=["star", "columns-selector", "opaque-setting"],
)
def test_other_advisory_reasons_survive_a_dead_join_using(
    tmp_path: Path, projection: str, marker: str
) -> None:
    if projection.startswith("revenue FROM"):
        sql = (
            "WITH dormant AS (SELECT 1 FROM dm.sales_daily AS a "
            "JOIN dm.sales_daily AS b USING (region)) "
            f"SELECT {projection}"
        )
    else:
        sql = (
            "WITH dormant AS (SELECT 1 FROM dm.sales_daily AS a "
            "JOIN dm.sales_daily AS b USING (region)) "
            f"SELECT {projection} FROM dm.sales_daily"
        )

    detail = ungoverned_detail(tmp_path, sql)
    assert ungoverned_pairs(tmp_path / "pairs", sql) == [
        ("asset.query_fields_unchecked", "advisory")
    ]
    assert marker in detail
    assert "JOIN USING" not in detail


def test_shared_extractor_keeps_whole_ast_join_using_keys(tmp_path: Path) -> None:
    sql = (
        "WITH dormant AS (SELECT 1 FROM dm.sales_daily AS a "
        "JOIN dm.sales_daily AS b USING (region)) "
        "SELECT revenue FROM dm.sales_daily"
    )

    assert extract_column_refs(sql, dialect="clickhouse").join_using_keys == frozenset({"region"})
    assert ungoverned_codes(tmp_path, sql) == []


def test_postgres_keeps_the_whole_ast_join_using_remainder(tmp_path: Path) -> None:
    sql = (
        "WITH dormant AS (SELECT 1 FROM dm.sales_daily AS a "
        "JOIN dm.sales_daily AS b USING (region)) "
        "SELECT revenue FROM dm.sales_daily"
    )

    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_fields_unchecked", "advisory")
    ]
    assert "JOIN USING key region" in ungoverned_detail(tmp_path / "detail", sql, engine="postgres")
