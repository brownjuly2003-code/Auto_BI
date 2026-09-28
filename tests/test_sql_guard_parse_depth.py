"""Parser depth is a guard rejection, not an interpreter crash (T-26)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from auto_bi.agent.sql_guard import (
    SQLGuardError,
    extract_column_refs,
    extract_table_refs,
    guard_sql,
)
from auto_bi.verification.repository import ReferenceQuery
from tests.test_decision_chart_impact import (
    DASHBOARD,
    _reformulated,
    impact_of,
    operator_dashboard,
    resolved,
)
from tests.test_verified_assets import asset, query, repository


def nested_parentheses(depth: int) -> str:
    return "SELECT " + "(" * depth + "revenue" + " + 1)" * depth + " FROM dm.sales_daily"


def control_query() -> str:
    return nested_parentheses(10)


@pytest.mark.parametrize("dialect", ["clickhouse", "postgres"])
def test_parser_depth_is_rejected_by_all_sql_guard_entry_points(dialect: str) -> None:
    deep = nested_parentheses(120)
    for parse_or_read in (guard_sql, extract_table_refs, extract_column_refs):
        with pytest.raises(SQLGuardError, match="nest"):
            parse_or_read(deep, dialect=dialect)

    control = control_query()
    guard_sql(control, dialect=dialect)
    assert {ref.name for ref in extract_table_refs(control, dialect=dialect)} == {"dm.sales_daily"}
    reads = extract_column_refs(control, dialect=dialect)
    assert {f"{ref.table.name}.{ref.name}" for ref in reads.attributed} == {
        "dm.sales_daily.revenue"
    }


@pytest.mark.parametrize("engine", ["clickhouse", "postgresql"])
def test_reference_query_reports_parser_depth_as_validation_error(engine: str) -> None:
    with pytest.raises(ValidationError, match="reference query is not a plain SELECT") as error:
        ReferenceQuery(engine=engine, sql=nested_parentheses(120))
    assert "nest" in str(error.value)


@pytest.mark.parametrize("engine", ["clickhouse", "postgresql"])
def test_verified_asset_registry_reports_parser_depth_as_validation_error(
    tmp_path: Path, engine: str
) -> None:
    with pytest.raises(ValidationError, match="reference query is not a plain SELECT") as error:
        repository(
            tmp_path / engine,
            asset(reference_query=query(nested_parentheses(120), engine=engine)),
        )
    assert "nest" in str(error.value)
    assert "RecursionError" not in str(error.value)


def test_chart_impact_keeps_a_deep_raw_sql_tile_opaque(tmp_path: Path) -> None:
    _, report = resolved(
        tmp_path,
        candidate=_reformulated(),
        dashboards={DASHBOARD: operator_dashboard(nested_parentheses(120))},
    )
    assert impact_of(report, "metric.formula_changed").charts == []
    assert [(gap.code, gap.subject) for gap in report.chart_gaps] == [
        ("chart.opaque_sql", f"{DASHBOARD}#operator_table")
    ]


@pytest.mark.parametrize("dialect", ["clickhouse", "postgres"])
def test_parser_remains_usable_after_depth_refusal(dialect: str) -> None:
    with pytest.raises(SQLGuardError, match="nest"):
        guard_sql(nested_parentheses(120), dialect=dialect)
    reads = extract_column_refs("SELECT revenue FROM dm.sales_daily", dialect=dialect)
    assert {f"{ref.table.name}.{ref.name}" for ref in reads.attributed} == {
        "dm.sales_daily.revenue"
    }
