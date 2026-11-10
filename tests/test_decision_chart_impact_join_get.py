"""A ClickHouse joinGet call in an operator tile: the read it names, or a named hole.

`joinGet('dm.rev', 'revenue', key)` reads `dm.rev.revenue` the way `dictGet` reads a
dictionary attribute, while `extract_column_refs` sees only the key. The dictionary
family does not cover it because the name does not begin with `dict`. A Join-table
call now either names its read — put through the same reconciliation as a table
read — or leaves the tile read in part. Silence is the one outcome that is not
allowed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_bi.decision.impact import ChartRelation
from tests.test_decision_chart_impact import (
    DASHBOARD,
    _reformulated,
    operator_dashboard,
    resolved,
)
from tests.test_decision_chart_impact_dictionary import TILE, charts, field_change, gaps

UNNAMED_CLAUSE = "whose Join table read this run could not name"


# --- resolved: a literal Join table and column name the read --------------------------------


@pytest.mark.parametrize(
    "call",
    [
        "joinGet('dm.rev', 'revenue', toUInt64(1))",
        "joinGetOrNull('dm.rev', 'revenue', toUInt64(1))",
        "JOINGET('dm.rev', 'revenue', toUInt64(1))",
    ],
)
def test_a_literal_joinget_resolves_into_table_dot_column(tmp_path: Path, call: str) -> None:
    sql = f"SELECT {call} AS r, sum(revenue) FROM dm.sales_daily"
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    assert charts(report) == [
        ("operator_table", ChartRelation.READS_FIELD, "query reads dm.rev.revenue")
    ]
    assert report.chart_gaps == []


def test_a_resolved_join_get_of_another_field_is_not_this_tile(tmp_path: Path) -> None:
    """The read is `dm.rev.revenue`, not the Join table as a whole."""
    sql = "SELECT joinGet('dm.rev', 'revenue', toUInt64(1)) AS r FROM dm.sales_daily"
    _, report = field_change(tmp_path, sql, "dm.rev.cost")
    assert charts(report) == []
    assert report.chart_gaps == []


def test_a_bare_join_table_that_cannot_be_reconciled_is_named_as_unread(
    tmp_path: Path,
) -> None:
    """A bare `rev` is not the tile's table: same rule as a dictionary name."""
    sql = "SELECT joinGet('rev', 'revenue', toUInt64(1)) AS r FROM dm.sales_daily"
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    assert charts(report) == []
    assert gaps(report) == [("chart.sql_partial", TILE)]
    assert "rev could not be reconciled" in report.chart_gaps[0].detail


def test_a_join_get_of_a_metric_term_keeps_the_metric_from_being_ruled_out(
    tmp_path: Path,
) -> None:
    """Read through joinGet, `revenue` is a term column: the tile cannot be ruled out."""
    sql = "SELECT sum(store_id), joinGet('dm.sales_daily', 'revenue', store_id) FROM dm.sales_daily"
    _, report = resolved(
        tmp_path, candidate=_reformulated(), dashboards={DASHBOARD: operator_dashboard(sql)}
    )
    assert gaps(report) == [("chart.metric_unread", TILE)]


# --- every other Join-table call leaves the tile read in part -------------------------------


@pytest.mark.parametrize(
    ("call", "name"),
    [
        ("joinGet(dm.rev, 'revenue', toUInt64(1))", "joinGet"),
        ("joinGet(concat('dm.', 'rev'), 'revenue', toUInt64(1))", "joinGet"),
        ("joinGet('dm.rev', revenue, toUInt64(1))", "joinGet"),
        ("joinGet('dm.rev', ('revenue', 'cost'), toUInt64(1))", "joinGet"),
        ("joinGetSomething('dm.rev', 'revenue', toUInt64(1))", "joinGetSomething"),
    ],
)
def test_a_join_get_call_this_run_cannot_name_leaves_the_tile_read_in_part(
    tmp_path: Path, call: str, name: str
) -> None:
    sql = f"SELECT {call} AS r, sum(revenue) FROM dm.sales_daily"
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    assert charts(report) == []
    assert gaps(report) == [("chart.sql_partial", TILE)]
    assert f"it calls {name}(...), {UNNAMED_CLAUSE}" in report.chart_gaps[0].detail


def test_a_tuple_column_is_not_a_join_get_read(tmp_path: Path) -> None:
    """ClickHouse's joinGet takes one value column; a tuple is the unread clause, not two reads."""
    sql = "SELECT joinGet('dm.rev', ('revenue', 'cost'), toUInt64(1)) AS r FROM dm.sales_daily"
    _, report = field_change(tmp_path, sql, "dm.rev.revenue", "dm.rev.cost")
    assert charts(report) == []
    assert gaps(report) == [("chart.sql_partial", TILE)]
    assert f"it calls joinGet(...), {UNNAMED_CLAUSE}" in report.chart_gaps[0].detail


# --- scope: only a call the statement runs --------------------------------------------------


def test_a_join_get_in_a_cte_the_from_never_names_changes_nothing(tmp_path: Path) -> None:
    sql = (
        "WITH x AS (SELECT joinGet('dm.rev', 'revenue', toUInt64(1)) AS r FROM dm.sales_daily) "
        "SELECT sum(store_id) FROM dm.sales_daily"
    )
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    assert charts(report) == []
    assert report.chart_gaps == []


def test_a_join_get_in_an_unreferenced_scalar_subquery_with_changes_nothing(
    tmp_path: Path,
) -> None:
    sql = (
        "WITH (SELECT joinGet('dm.rev', 'revenue', toUInt64(1))) AS r "
        "SELECT sum(revenue) FROM dm.sales_daily"
    )
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    assert charts(report) == []
    assert report.chart_gaps == []


def test_a_join_get_in_a_referenced_scalar_subquery_with_is_read(tmp_path: Path) -> None:
    sql = (
        "WITH (SELECT joinGet('dm.rev', 'revenue', toUInt64(1))) AS r "
        "SELECT sum(revenue) * r FROM dm.sales_daily"
    )
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    assert charts(report) == [
        ("operator_table", ChartRelation.READS_FIELD, "query reads dm.rev.revenue")
    ]
    assert report.chart_gaps == []
