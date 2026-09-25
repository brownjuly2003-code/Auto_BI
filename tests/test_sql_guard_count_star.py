"""COUNT(*) is a row count; every other star still reads columns (T-21)."""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_bi.agent.sql_guard import extract_column_refs
from auto_bi.decision.impact import ChartRelation
from tests.test_decision_chart_impact_dictionary import charts, field_change, gaps
from tests.test_sql_guard_clickhouse_alias import ungoverned_codes, ungoverned_detail

TILE = "dashboard://weekly_margin@7#operator_table"


@pytest.mark.parametrize(
    ("sql", "dialects", "starred"),
    [
        (
            "SELECT region, count(*) FROM dm.sales_daily GROUP BY region",
            ("postgres", "clickhouse"),
            False,
        ),
        (
            "SELECT region, count(*) OVER () FROM dm.sales_daily",
            ("postgres", "clickhouse"),
            False,
        ),
        (
            "SELECT region, count(*) FILTER (WHERE region = 'x') FROM dm.sales_daily",
            ("postgres", "clickhouse"),
            False,
        ),
        ("SELECT * FROM dm.sales_daily", ("postgres", "clickhouse"), True),
        ("SELECT t.* FROM dm.sales_daily AS t", ("postgres", "clickhouse"), True),
        ("SELECT count(t.*) FROM dm.sales_daily AS t", ("postgres", "clickhouse"), True),
        ("SELECT count(DISTINCT *) FROM dm.sales_daily", ("postgres", "clickhouse"), True),
        ("SELECT uniq(*) FROM dm.sales_daily", ("clickhouse",), True),
        ("SELECT cityHash64(*) FROM dm.sales_daily", ("clickhouse",), True),
        (
            "SELECT count(*) FROM (SELECT * FROM dm.sales_daily) AS d",
            ("postgres", "clickhouse"),
            True,
        ),
    ],
)
def test_starred_only_when_a_star_reads_columns(
    sql: str, dialects: tuple[str, ...], starred: bool
) -> None:
    for dialect in dialects:
        assert extract_column_refs(sql, dialect=dialect).starred is starred


@pytest.mark.parametrize("engine", ("postgres", "clickhouse"))
def test_count_star_is_a_valid_fully_governed_query(tmp_path: Path, engine: str) -> None:
    """FALSE-REJECT CONTROL: the row count must not make a valid asset partial."""
    sql = "SELECT region, count(*) FROM dm.sales_daily GROUP BY region"
    assert ungoverned_codes(tmp_path, sql, engine=engine) == []


@pytest.mark.parametrize("engine", ("postgres", "clickhouse"))
def test_select_star_keeps_the_partial_governance_advisory(tmp_path: Path, engine: str) -> None:
    sql = "SELECT * FROM dm.sales_daily"
    assert ungoverned_codes(tmp_path, sql, engine=engine) == ["asset.query_fields_unchecked"]
    assert "it selects *" in ungoverned_detail(tmp_path / "detail", sql, engine=engine)


def test_count_distinct_star_keeps_the_partial_governance_advisory(tmp_path: Path) -> None:
    sql = "SELECT count(DISTINCT *) FROM dm.sales_daily"
    assert ungoverned_codes(tmp_path, sql) == ["asset.query_fields_unchecked"]
    assert "it selects *" in ungoverned_detail(tmp_path / "detail", sql)


def test_count_distinct_star_is_a_partial_chart_read(tmp_path: Path) -> None:
    _, report = field_change(
        tmp_path,
        "SELECT count(DISTINCT *) FROM dm.sales_daily",
        "dm.sales_daily.cost",
    )
    assert charts(report) == []
    assert gaps(report) == [("chart.sql_partial", TILE)]
    assert "it selects *" in report.chart_gaps[0].detail


def test_count_star_beside_a_named_read_stays_resolved_in_whole(tmp_path: Path) -> None:
    _, report = field_change(
        tmp_path,
        "SELECT count(*), sum(revenue) FROM dm.sales_daily",
        "dm.sales_daily.revenue",
    )
    assert [(chart, relation) for chart, relation, _ in charts(report)] == [
        ("operator_table", ChartRelation.READS_FIELD)
    ]
    assert report.chart_gaps == []
