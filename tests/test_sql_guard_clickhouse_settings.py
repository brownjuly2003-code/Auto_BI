"""ClickHouse SETTINGS are query options, not reads of table columns (T-20)."""

from __future__ import annotations

from pathlib import Path

import sqlglot

from auto_bi.agent.sql_guard import extract_column_refs
from auto_bi.decision.impact import _open_cte_gates
from tests.test_decision_chart_impact_dictionary import charts, field_change, gaps
from tests.test_sql_guard_clickhouse_alias import (
    attributed,
    ungoverned_codes,
    ungoverned_detail,
)


def _attributed(sql: str) -> list[str]:
    reads = extract_column_refs(sql, dialect="clickhouse")
    return sorted(f"{ref.table.name}.{ref.name}" for ref in reads.attributed)


def _unattributed(sql: str) -> list[str]:
    return sorted(extract_column_refs(sql, dialect="clickhouse").unattributed)


def test_plain_settings_items_are_not_column_reads() -> None:
    """The setting name and identifier value stay outside the column reader."""
    assert _attributed("SELECT region FROM dm.sales_daily SETTINGS max_threads = 8") == [
        "dm.sales_daily.region"
    ]
    assert _unattributed("SELECT region FROM dm.sales_daily SETTINGS max_threads = 8") == []
    assert _attributed("SELECT region FROM dm.sales_daily SETTINGS load_balancing = random") == [
        "dm.sales_daily.region"
    ]
    assert _unattributed("SELECT region FROM dm.sales_daily SETTINGS load_balancing = random") == []


def test_reviewed_query_with_plain_settings_stays_clean(tmp_path: Path) -> None:
    """FALSE-REJECT CONTROL: declared reads plus query settings remain admissible."""
    sql = "SELECT region, sum(revenue) FROM dm.sales_daily GROUP BY region SETTINGS max_threads = 8"
    assert attributed(sql) == ["dm.sales_daily.region", "dm.sales_daily.revenue"]
    assert ungoverned_codes(tmp_path, sql) == []


def test_multiple_plain_settings_do_not_create_review_findings(tmp_path: Path) -> None:
    sql = (
        "SELECT region, sum(revenue) FROM dm.sales_daily GROUP BY region "
        "SETTINGS join_use_nulls = 1, max_threads = 4"
    )
    assert attributed(sql) == ["dm.sales_daily.region", "dm.sales_daily.revenue"]
    assert ungoverned_codes(tmp_path, sql) == []


def test_settings_on_each_query_level_match_the_statement_without_settings() -> None:
    cases = [
        (
            "SELECT region FROM dm.sales_daily UNION ALL "
            "SELECT region FROM dm.stores SETTINGS max_threads = 8",
            "SELECT region FROM dm.sales_daily UNION ALL SELECT region FROM dm.stores",
        ),
        (
            "SELECT region FROM dm.sales_daily WHERE region IN "
            "(SELECT region FROM dm.stores SETTINGS max_threads = 1)",
            "SELECT region FROM dm.sales_daily WHERE region IN (SELECT region FROM dm.stores)",
        ),
        (
            "WITH w AS (SELECT region FROM dm.sales_daily SETTINGS max_threads = 2) "
            "SELECT region FROM w",
            "WITH w AS (SELECT region FROM dm.sales_daily) SELECT region FROM w",
        ),
    ]
    for with_settings, without_settings in cases:
        actual = extract_column_refs(with_settings, dialect="clickhouse")
        expected = extract_column_refs(without_settings, dialect="clickhouse")
        assert actual.attributed == expected.attributed
        assert actual.unattributed == expected.unattributed
        assert actual.ambiguous == expected.ambiguous


def test_format_remains_outside_column_reads() -> None:
    assert _attributed("SELECT region FROM dm.sales_daily FORMAT JSONEachRow") == [
        "dm.sales_daily.region"
    ]
    assert _unattributed("SELECT region FROM dm.sales_daily FORMAT JSONEachRow") == []


def test_additional_table_filters_is_case_insensitive_and_not_a_column_read() -> None:
    sql = "SELECT region FROM dm.sales_daily SETTINGS ADDITIONAL_TABLE_FILTERS = 1"
    reads = extract_column_refs(sql, dialect="clickhouse")
    assert _attributed(sql) == ["dm.sales_daily.region"]
    assert reads.unattributed == frozenset()
    assert reads.opaque_settings == frozenset({"ADDITIONAL_TABLE_FILTERS"})


def test_additional_table_filters_is_an_opaque_partial_read(tmp_path: Path) -> None:
    sql = (
        "SELECT region FROM dm.sales_daily SETTINGS "
        "additional_table_filters = {'dm.sales_daily': 'cost > 0'}"
    )
    reads = extract_column_refs(sql, dialect="clickhouse")
    assert _attributed(sql) == ["dm.sales_daily.region"]
    assert reads.unattributed == frozenset()
    assert reads.opaque_settings == frozenset({"additional_table_filters"})
    assert ungoverned_codes(tmp_path, sql) == ["asset.query_fields_unchecked"]
    assert "additional_table_filters" in ungoverned_detail(tmp_path / "detail", sql)


def test_additional_result_filter_does_not_read_a_table_column(tmp_path: Path) -> None:
    sql = "SELECT region FROM dm.sales_daily SETTINGS additional_result_filter = 'region != ''x'''"
    reads = extract_column_refs(sql, dialect="clickhouse")
    assert _attributed(sql) == ["dm.sales_daily.region"]
    assert reads.unattributed == frozenset()
    assert reads.opaque_settings == frozenset()
    assert ungoverned_codes(tmp_path, sql) == []


def test_a_setting_named_like_a_visible_alias_does_not_read_through_it() -> None:
    sql = "WITH cost AS max_threads SELECT region FROM dm.sales_daily SETTINGS max_threads = 8"
    assert _attributed(sql) == ["dm.sales_daily.region"]
    assert _unattributed(sql) == []


def test_settings_do_not_open_scalar_with_gates() -> None:
    """Only a running query expression, never a setting name, can open a WITH gate."""
    queries = [
        "WITH dictGet('dm.sales_daily', 'cost', region) AS max_threads "
        "SELECT region FROM dm.sales_daily SETTINGS max_threads = 8",
        "WITH (SELECT * FROM dm.sales_daily) AS max_threads "
        "SELECT region FROM dm.sales_daily SETTINGS max_threads = 8",
    ]
    for sql in queries:
        root = sqlglot.parse_one(sql, read="clickhouse")
        assert _open_cte_gates(root) == set()


def test_additional_table_filters_keeps_chart_impact_partial(tmp_path: Path) -> None:
    sql = (
        "SELECT region FROM dm.sales_daily SETTINGS "
        "additional_table_filters = {'dm.sales_daily': 'cost > 0'}"
    )
    _, report = field_change(tmp_path, sql, "dm.sales_daily.cost")
    assert charts(report) == []
    assert gaps(report) == [("chart.sql_partial", "dashboard://weekly_margin@7#operator_table")]
    assert "additional_table_filters" in report.chart_gaps[0].detail


def test_subquery_settings_preserves_resolved_chart_reads(tmp_path: Path) -> None:
    with_settings = (
        "SELECT region FROM dm.sales_daily WHERE region IN "
        "(SELECT t.region FROM dm.stores AS t SETTINGS max_threads = 1)"
    )
    without_settings = (
        "SELECT region FROM dm.sales_daily WHERE region IN " "(SELECT t.region FROM dm.stores AS t)"
    )
    _, actual = field_change(tmp_path / "with", with_settings, "dm.sales_daily.revenue")
    _, expected = field_change(tmp_path / "without", without_settings, "dm.sales_daily.revenue")
    assert charts(actual) == charts(expected)
    assert gaps(actual) == gaps(expected) == []


def test_alias_named_setting_is_not_a_chart_field_read(tmp_path: Path) -> None:
    sql = "WITH cost AS max_threads SELECT region FROM dm.sales_daily SETTINGS max_threads = 8"
    _, report = field_change(tmp_path, sql, "dm.sales_daily.cost")
    assert [(chart, relation) for chart, relation, _ in charts(report)] == []
    assert gaps(report) == []


def test_scalar_with_named_like_a_setting_is_not_a_chart_field_read(tmp_path: Path) -> None:
    sql = (
        "WITH dictGet('dm.sales_daily', 'cost', region) AS max_threads "
        "SELECT region FROM dm.sales_daily SETTINGS max_threads = 8"
    )
    _, report = field_change(tmp_path, sql, "dm.sales_daily.cost")
    assert [(chart, relation) for chart, relation, _ in charts(report)] == []
    assert gaps(report) == []
