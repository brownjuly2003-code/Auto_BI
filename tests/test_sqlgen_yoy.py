"""SQL_GEN yoy_pct: year-over-year change vs the same period a year back.

yoy_pct reuses the period-over-period window machinery but lags by a full year of periods,
derived from the chart's time_grain (month=12, quarter=4, week=52, year=1) — so it requires a
non-day grain. Same dense-series assumption as pop (a row-based lag). Two checks: SQL *shape*
per dialect, and SQL *numbers* (Postgres SQL run in DuckDB vs a hand calculation).
"""

from __future__ import annotations

import pytest

from auto_bi.agent.sql_guard import guard_sql
from auto_bi.agent.sqlgen import generate_chart_sql
from auto_bi.ir.spec import ChartQuery, Measure, MeasureTransform, OrderBy, TimeGrain
from auto_bi.semantic.model import Aggregation


def _yoy_q(grain: TimeGrain = TimeGrain.MONTH, **kwargs) -> ChartQuery:
    defaults = dict(
        table="dm.sales_daily",
        dimensions=["date"],
        measures=[
            Measure(column="revenue", agg=Aggregation.SUM, transform=MeasureTransform.YOY_PCT)
        ],
        time_grain=grain,
    )
    defaults.update(kwargs)
    return ChartQuery(**defaults)


# --- SQL shape -------------------------------------------------------------


def test_yoy_month_clickhouse() -> None:
    sql = generate_chart_sql(_yoy_q())
    assert 'toStartOfMonth("date") AS "date"' in sql  # inner truncates to month
    assert 'lagInFrame(toNullable("__src_0"), 12)' in sql  # lag a full year of months
    assert "ROWS BETWEEN 12 PRECEDING AND CURRENT ROW" in sql
    assert 'AS "yoy_pct_sum_revenue"' in sql
    guard_sql(sql)


def test_yoy_month_postgres() -> None:
    sql = generate_chart_sql(_yoy_q(), dialect="postgres")
    assert 'LAG("__src_0", 12)' in sql
    assert "date_trunc('month', \"date\")" in sql.lower()
    guard_sql(sql, dialect="postgres")


def test_yoy_quarter_lags_four() -> None:
    assert 'lagInFrame(toNullable("__src_0"), 4)' in generate_chart_sql(_yoy_q(TimeGrain.QUARTER))


def test_yoy_week_lags_fifty_two_postgres() -> None:
    assert 'LAG("__src_0", 52)' in generate_chart_sql(_yoy_q(TimeGrain.WEEK), dialect="postgres")


def test_yoy_year_grain_lags_one_no_offset() -> None:
    # one period per year -> lag 1, the offset arg omitted (same shape as pop, byte-compatible)
    sql = generate_chart_sql(_yoy_q(TimeGrain.YEAR))
    assert 'lagInFrame(toNullable("__src_0")) OVER' in sql  # no ", 1"
    assert "ROWS BETWEEN 1 PRECEDING AND CURRENT ROW" in sql


def test_pop_pct_unchanged_by_yoy_addition() -> None:
    # the existing period-over-period output must stay byte-for-byte (lag 1, no offset)
    q = ChartQuery(
        table="dm.sales_daily",
        dimensions=["date"],
        measures=[
            Measure(column="revenue", agg=Aggregation.SUM, transform=MeasureTransform.POP_PCT)
        ],
    )
    sql = generate_chart_sql(q)
    assert 'lagInFrame(toNullable("__src_0")) OVER' in sql
    assert ", 1)" not in sql  # no explicit offset emitted for pop


# --- numeric verification in DuckDB (postgres window semantics) ------------


def _duckdb_yoy(rows: list[tuple[str, float]]) -> list[float | None]:
    duckdb = pytest.importorskip("duckdb")  # ephemeral dev dep, `--with duckdb` in CI
    con = duckdb.connect()
    con.execute("CREATE SCHEMA dm; CREATE TABLE dm.sales_daily (date DATE, revenue DOUBLE)")
    con.executemany("INSERT INTO dm.sales_daily VALUES (?, ?)", rows)
    sql = generate_chart_sql(_yoy_q(order_by=[OrderBy(by="date", dir="asc")]), dialect="postgres")
    return [r[1] for r in con.execute(sql).fetchall()]


def _two_years_monthly() -> tuple[list[tuple[str, float]], list[float]]:
    vals = [float(100 + 10 * i) for i in range(24)]  # 24 months, monotone
    rows: list[tuple[str, float]] = []
    year, month = 2023, 1
    for v in vals:
        rows.append((f"{year}-{month:02d}-01", v))
        month += 1
        if month == 13:
            month, year = 1, year + 1
    return rows, vals


def test_yoy_numbers_match_hand_calc() -> None:
    rows, vals = _two_years_monthly()
    got = _duckdb_yoy(rows)
    expected: list[float | None] = [None] * 12 + [
        (vals[i] - vals[i - 12]) / vals[i - 12] for i in range(12, 24)
    ]
    assert len(got) == len(expected)
    for g, e in zip(got, expected, strict=True):
        if e is None:
            assert g is None
        else:
            assert g is not None and abs(g - e) < 1e-9
