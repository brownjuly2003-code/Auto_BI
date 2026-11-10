"""HAVING and QUALIFY inputs are reads, not an ungraded remainder (T-19)."""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_bi.agent.sql_guard import extract_column_refs
from auto_bi.decision.impact import ChartRelation
from tests.test_decision_chart_impact_dictionary import charts, field_change
from tests.test_sql_guard_clickhouse_alias import (
    attributed,
    unattributed,
    ungoverned_codes,
    ungoverned_detail,
)


@pytest.mark.parametrize(
    ("dialect", "sql"),
    [
        (
            "postgres",
            "SELECT region FROM dm.sales_daily GROUP BY region HAVING sum(cost) > 0",
        ),
        (
            "clickhouse",
            "SELECT region FROM dm.sales_daily GROUP BY region HAVING max(cost) > 0",
        ),
        (
            "clickhouse",
            "SELECT region FROM dm.sales_daily GROUP BY region WITH TOTALS HAVING sum(cost) > 0",
        ),
    ],
)
def test_an_unqualified_having_input_is_attributed_to_its_only_source(
    dialect: str, sql: str
) -> None:
    assert attributed(sql, dialect=dialect) == ["dm.sales_daily.cost", "dm.sales_daily.region"]
    assert unattributed(sql, dialect=dialect) == []


def test_an_unqualified_qualify_window_input_is_attributed_to_its_only_source() -> None:
    sql = "SELECT region FROM dm.sales_daily " "QUALIFY row_number() OVER (PARTITION BY cost) = 1"
    assert attributed(sql) == ["dm.sales_daily.cost", "dm.sales_daily.region"]
    assert unattributed(sql) == []


def test_an_ordered_qualify_window_input_is_attributed_to_its_only_source() -> None:
    sql = "SELECT region FROM dm.sales_daily QUALIFY row_number() OVER (ORDER BY cost) = 1"
    assert attributed(sql) == ["dm.sales_daily.cost", "dm.sales_daily.region"]
    assert unattributed(sql) == []


@pytest.mark.parametrize(
    ("dialect", "sql"),
    [
        (
            "postgres",
            "SELECT s.region FROM dm.sales_daily AS s JOIN dm.stores AS t "
            "ON s.store_id = t.store_id GROUP BY s.region HAVING sum(cost) > 0",
        ),
        (
            "clickhouse",
            "SELECT s.region FROM dm.sales_daily AS s JOIN dm.stores AS t "
            "ON s.store_id = t.store_id GROUP BY s.region HAVING max(cost) > 0",
        ),
    ],
)
def test_an_unqualified_having_input_is_unattributed_in_a_join(dialect: str, sql: str) -> None:
    reads = extract_column_refs(sql, dialect=dialect)
    assert attributed(sql, dialect=dialect) == [
        "dm.sales_daily.region",
        "dm.sales_daily.store_id",
        "dm.stores.store_id",
    ]
    assert reads.unattributed == frozenset({"cost"})


def test_a_clickhouse_having_alias_is_not_reclassified_as_a_source_column() -> None:
    sql = "SELECT region, sum(revenue) AS rev FROM dm.sales_daily GROUP BY region HAVING rev > 0"
    assert attributed(sql) == ["dm.sales_daily.region", "dm.sales_daily.revenue"]
    assert unattributed(sql) == []


def test_a_qualified_having_input_stays_attributed() -> None:
    sql = (
        "SELECT s.region FROM dm.sales_daily AS s JOIN dm.stores AS t "
        "ON s.store_id = t.store_id GROUP BY s.region HAVING sum(s.cost) > 0"
    )
    assert attributed(sql, dialect="postgres") == [
        "dm.sales_daily.cost",
        "dm.sales_daily.region",
        "dm.sales_daily.store_id",
        "dm.stores.store_id",
    ]
    assert unattributed(sql, dialect="postgres") == []


def test_a_clickhouse_qualify_alias_reads_through_to_its_definition() -> None:
    sql = (
        "SELECT region, sum(revenue) AS rev, row_number() OVER () AS n "
        "FROM dm.sales_daily GROUP BY region QUALIFY rev > 0"
    )
    assert attributed(sql) == ["dm.sales_daily.region", "dm.sales_daily.revenue"]
    assert unattributed(sql) == []


@pytest.mark.parametrize(
    ("dialect", "sql"),
    [
        (
            "postgres",
            "SELECT region FROM dm.sales_daily GROUP BY region "
            "HAVING EXISTS (SELECT 1 FROM dm.stores AS t WHERE t.region = max(cost))",
        ),
        (
            "clickhouse",
            "SELECT region FROM dm.sales_daily GROUP BY region "
            "HAVING EXISTS (SELECT 1 FROM dm.stores AS t WHERE t.region = max(cost))",
        ),
    ],
)
def test_a_correlated_subquery_in_having_keeps_its_unattributed_input_local(
    dialect: str, sql: str
) -> None:
    assert attributed(sql, dialect=dialect) == ["dm.sales_daily.region", "dm.stores.region"]
    assert unattributed(sql, dialect=dialect) == ["cost"]


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            "SELECT region FROM dm.sales_daily GROUP BY region HAVING (sum(cost) AS s) > 0",
            ["dm.sales_daily.cost", "dm.sales_daily.region"],
        ),
        (
            "SELECT row_number() OVER () FROM dm.sales_daily "
            "QUALIFY (revenue AS y) > 1 ORDER BY y",
            ["dm.sales_daily.revenue"],
        ),
    ],
)
def test_an_inline_alias_definition_in_having_or_qualify_keeps_its_t18_read(
    sql: str, expected: list[str]
) -> None:
    assert attributed(sql) == expected
    assert unattributed(sql) == []


@pytest.mark.parametrize(
    ("engine", "sql", "expected_codes"),
    [
        (
            "postgres",
            "SELECT region FROM dm.sales_daily GROUP BY region HAVING sum(cost) > 0",
            ["asset.query_ungoverned_field"],
        ),
        (
            "clickhouse",
            "SELECT region FROM dm.sales_daily GROUP BY region HAVING max(cost) > 0",
            ["asset.query_ungoverned_field"],
        ),
        (
            "clickhouse",
            "SELECT region FROM dm.sales_daily "
            "QUALIFY row_number() OVER (PARTITION BY cost) = 1",
            ["asset.query_ungoverned_field"],
        ),
        (
            "clickhouse",
            "SELECT region FROM dm.sales_daily GROUP BY region WITH TOTALS HAVING sum(cost) > 0",
            ["asset.query_ungoverned_field"],
        ),
        (
            "clickhouse",
            "SELECT region, sum(revenue) AS rev FROM dm.sales_daily GROUP BY region HAVING rev > 0",
            [],
        ),
    ],
)
def test_having_and_qualify_reads_are_checked_by_governance(
    tmp_path: Path, engine: str, sql: str, expected_codes: list[str]
) -> None:
    assert ungoverned_codes(tmp_path, sql, engine=engine) == expected_codes


@pytest.mark.parametrize(
    ("engine", "sql"),
    [
        (
            "postgres",
            "SELECT s.region FROM dm.sales_daily AS s JOIN dm.stores AS t "
            "ON s.store_id = t.store_id GROUP BY s.region HAVING sum(cost) > 0",
        ),
        (
            "clickhouse",
            "SELECT s.region FROM dm.sales_daily AS s JOIN dm.stores AS t "
            "ON s.store_id = t.store_id GROUP BY s.region HAVING max(cost) > 0",
        ),
    ],
)
def test_an_unqualified_having_input_in_a_join_reports_the_unattributed_field_review(
    tmp_path: Path, engine: str, sql: str
) -> None:
    assert ungoverned_codes(tmp_path, sql, engine=engine) == ["asset.query_ungoverned_field"]
    detail = ungoverned_detail(tmp_path / "detail", sql, engine=engine)
    assert "cost (read from one of dm.sales_daily, dm.stores)" in detail


@pytest.mark.parametrize(
    ("engine", "sql"),
    [
        (
            "postgres",
            "SELECT s.region FROM dm.sales_daily AS s JOIN (SELECT store_id FROM dm.stores) AS t "
            "ON s.store_id = t.store_id GROUP BY s.region HAVING sum(cost) > 0",
        ),
        (
            "clickhouse",
            "SELECT s.region FROM dm.sales_daily AS s JOIN (SELECT store_id FROM dm.stores) AS t "
            "ON s.store_id = t.store_id GROUP BY s.region HAVING max(cost) > 0",
        ),
    ],
)
def test_an_unqualified_having_input_with_a_derived_source_reports_unchecked_fields(
    tmp_path: Path, engine: str, sql: str
) -> None:
    assert ungoverned_codes(tmp_path, sql, engine=engine) == [
        "asset.query_ungoverned_field",
        "asset.query_fields_unchecked",
    ]
    detail = ungoverned_detail(tmp_path / "detail", sql, engine=engine)
    assert "cost could not be attributed to one table without guessing" in detail


@pytest.mark.parametrize(
    ("engine", "sql", "expected_codes"),
    [
        (
            "postgres",
            "SELECT s.region FROM dm.sales_daily AS s JOIN dm.stores AS t "
            "ON s.store_id = t.store_id GROUP BY s.region HAVING sum(s.cost) > 0",
            ["asset.query_ungoverned_field"],
        ),
        (
            "clickhouse",
            "SELECT region, sum(revenue) AS rev, row_number() OVER () AS n "
            "FROM dm.sales_daily GROUP BY region QUALIFY rev > 0",
            [],
        ),
        (
            "postgres",
            "SELECT region FROM dm.sales_daily GROUP BY region "
            "HAVING EXISTS (SELECT 1 FROM dm.stores AS t WHERE t.region = max(cost))",
            ["asset.query_ungoverned_field", "asset.query_fields_unchecked"],
        ),
        (
            "clickhouse",
            "SELECT region FROM dm.sales_daily GROUP BY region HAVING (sum(cost) AS s) > 0",
            ["asset.query_ungoverned_field"],
        ),
        (
            "clickhouse",
            "SELECT row_number() OVER () FROM dm.sales_daily QUALIFY (cost AS s) > 0",
            ["asset.query_ungoverned_field"],
        ),
    ],
)
def test_already_graded_having_and_qualify_cases_keep_their_governance_result(
    tmp_path: Path, engine: str, sql: str, expected_codes: list[str]
) -> None:
    assert ungoverned_codes(tmp_path, sql, engine=engine) == expected_codes


def test_a_valid_reviewed_having_query_over_declared_fields_stays_clean(tmp_path: Path) -> None:
    """FALSE-REJECT CONTROL: a declared HAVING input is still accepted."""
    sql = "SELECT region FROM dm.sales_daily GROUP BY region HAVING sum(revenue) > 0"
    assert ungoverned_codes(tmp_path, sql) == []


def test_a_raw_sql_tile_names_cost_read_only_in_having(tmp_path: Path) -> None:
    sql = "SELECT region FROM dm.sales_daily GROUP BY region HAVING max(cost) > 0"
    _, report = field_change(tmp_path, sql, "dm.sales_daily.cost")
    assert charts(report) == [
        ("operator_table", ChartRelation.READS_FIELD, "query reads dm.sales_daily.cost")
    ]
    assert report.chart_gaps == []
