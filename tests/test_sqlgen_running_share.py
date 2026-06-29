"""SQL_GEN running_share: Pareto / ABC cumulative share (ranked by the measure descending).

Unlike the time-ordered transforms, running_share orders its window by the AGGREGATE VALUE
descending — each category's value is the cumulative share of the grand total it and every
larger category make up (the smallest reaches 1.0). Two checks: SQL *shape* per dialect, and
SQL *numbers* (Postgres SQL run in DuckDB vs a hand calc).
"""

from __future__ import annotations

import pytest

from auto_bi.agent.sql_guard import guard_sql
from auto_bi.agent.sqlgen import generate_chart_sql
from auto_bi.ir.spec import ChartQuery, Measure, MeasureTransform
from auto_bi.semantic.model import Aggregation


def _share_q(**kwargs) -> ChartQuery:
    defaults = dict(
        table="dm.sales_daily",
        dimensions=["category"],
        measures=[
            Measure(column="revenue", agg=Aggregation.SUM, transform=MeasureTransform.RUNNING_SHARE)
        ],
    )
    defaults.update(kwargs)
    return ChartQuery(**defaults)


# --- SQL shape -------------------------------------------------------------


def test_running_share_clickhouse() -> None:
    sql = generate_chart_sql(_share_q())
    # cumulative SUM ordered by the aggregate value descending (NOT a time axis)
    assert 'SUM("__src_0") OVER (ORDER BY "__src_0" DESC ROWS BETWEEN UNBOUNDED PRECEDING' in sql
    # divided by the grand total SUM(...) OVER ()
    assert 'SUM("__src_0") OVER ()' in sql
    assert 'AS "running_share_sum_revenue"' in sql
    # two-level: inner GROUP BY of the base aggregate under a private alias
    assert 'SUM("revenue") AS "__src_0" FROM "dm"."sales_daily" GROUP BY "category"' in sql
    guard_sql(sql)


def test_running_share_postgres() -> None:
    sql = generate_chart_sql(_share_q(), dialect="postgres")
    assert 'SUM("__src_0") OVER (ORDER BY "__src_0" DESC' in sql
    assert 'SUM("__src_0") OVER ()' in sql
    guard_sql(sql, dialect="postgres")


def test_running_share_needs_no_time_axis() -> None:
    # ordered by the measure, so a categorical-only chart is fine (no ORDER BY a time column)
    sql = generate_chart_sql(_share_q())
    assert '"date"' not in sql  # no time dimension involved at all


# --- numeric verification in DuckDB (postgres window semantics) ------------


def test_running_share_numbers_match_hand_calc() -> None:
    duckdb = pytest.importorskip("duckdb")  # ephemeral dev dep, `--with duckdb` in CI
    con = duckdb.connect()
    con.execute("CREATE SCHEMA dm; CREATE TABLE dm.sales_daily (category VARCHAR, revenue DOUBLE)")
    con.executemany(
        "INSERT INTO dm.sales_daily VALUES (?, ?)",
        [("A", 50.0), ("B", 30.0), ("C", 15.0), ("D", 5.0)],  # total 100, distinct (no ties)
    )
    sql = generate_chart_sql(_share_q(), dialect="postgres")
    got = {row[0]: row[1] for row in con.execute(sql).fetchall()}
    # ranked desc: A 50/100=.5, +B .8, +C .95, +D 1.0 (the smallest category closes at 100%)
    expected = {"A": 0.50, "B": 0.80, "C": 0.95, "D": 1.00}
    assert got.keys() == expected.keys()
    for cat, exp in expected.items():
        assert abs(got[cat] - exp) < 1e-9
    assert abs(max(got.values()) - 1.0) < 1e-9  # cumulative share closes at exactly 1.0
