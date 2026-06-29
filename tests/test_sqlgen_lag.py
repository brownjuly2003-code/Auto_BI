"""SQL_GEN lag_periods: period-over-period change vs N periods back (generalised yoy lag).

pop_abs/pop_pct lag the adjacent period by default; `Measure.lag_periods=N` lags by N rows
instead, reusing the same row-based window machinery as yoy (which lags a full year). Two
checks: SQL *shape* per dialect, and SQL *numbers* (Postgres SQL run in DuckDB vs a hand calc).
"""

from __future__ import annotations

import pytest

from auto_bi.agent.sql_guard import guard_sql
from auto_bi.agent.sqlgen import generate_chart_sql
from auto_bi.ir.spec import ChartQuery, Measure, MeasureTransform, OrderBy
from auto_bi.semantic.model import Aggregation


def _lag_q(transform: MeasureTransform, lag_periods: int | None, **kwargs) -> ChartQuery:
    defaults = dict(
        table="dm.sales_daily",
        dimensions=["date"],
        measures=[
            Measure(
                column="revenue",
                agg=Aggregation.SUM,
                transform=transform,
                lag_periods=lag_periods,
            )
        ],
    )
    defaults.update(kwargs)
    return ChartQuery(**defaults)


# --- SQL shape -------------------------------------------------------------


def test_pop_pct_lag3_clickhouse() -> None:
    sql = generate_chart_sql(_lag_q(MeasureTransform.POP_PCT, 3))
    assert 'lagInFrame(toNullable("__src_0"), 3)' in sql  # lag 3 rows back
    assert "ROWS BETWEEN 3 PRECEDING AND CURRENT ROW" in sql
    assert 'AS "pop_pct_sum_revenue_lag3"' in sql  # lag suffix keeps the alias distinct
    guard_sql(sql)


def test_pop_pct_lag3_postgres() -> None:
    sql = generate_chart_sql(_lag_q(MeasureTransform.POP_PCT, 3), dialect="postgres")
    assert 'LAG("__src_0", 3)' in sql
    guard_sql(sql, dialect="postgres")


def test_pop_abs_lag2_clickhouse() -> None:
    sql = generate_chart_sql(_lag_q(MeasureTransform.POP_ABS, 2))
    assert 'lagInFrame(toNullable("__src_0"), 2)' in sql
    assert "ROWS BETWEEN 2 PRECEDING AND CURRENT ROW" in sql
    assert 'AS "pop_abs_sum_revenue_lag2"' in sql
    guard_sql(sql)


def test_lag1_window_is_byte_identical_to_adjacent_pop() -> None:
    # lag_periods=1 == adjacent period: the offset arg is omitted, so the window SQL matches
    # the no-lag form exactly (only the alias carries the explicit _lag1 marker).
    sql = generate_chart_sql(_lag_q(MeasureTransform.POP_PCT, 1))
    assert 'lagInFrame(toNullable("__src_0")) OVER' in sql
    assert ", 1)" not in sql  # no explicit offset emitted at k=1
    assert 'AS "pop_pct_sum_revenue_lag1"' in sql


def test_no_lag_periods_path_unchanged() -> None:
    # without lag_periods the alias and window are the pre-existing pop form (regression guard)
    sql = generate_chart_sql(_lag_q(MeasureTransform.POP_PCT, None))
    assert 'lagInFrame(toNullable("__src_0")) OVER' in sql
    assert "_lag" not in sql
    assert 'AS "pop_pct_sum_revenue"' in sql


# --- numeric verification in DuckDB (postgres window semantics) ------------


def _duckdb_pop_pct_lag(rows: list[tuple[str, float]], lag_periods: int) -> list[float | None]:
    duckdb = pytest.importorskip("duckdb")  # ephemeral dev dep, `--with duckdb` in CI
    con = duckdb.connect()
    con.execute("CREATE SCHEMA dm; CREATE TABLE dm.sales_daily (date DATE, revenue DOUBLE)")
    con.executemany("INSERT INTO dm.sales_daily VALUES (?, ?)", rows)
    q = _lag_q(MeasureTransform.POP_PCT, lag_periods, order_by=[OrderBy(by="date", dir="asc")])
    sql = generate_chart_sql(q, dialect="postgres")
    return [r[1] for r in con.execute(sql).fetchall()]


def test_lag3_numbers_match_hand_calc() -> None:
    vals = [float(100 + 7 * i) for i in range(12)]  # 12 monthly points, monotone
    rows = [(f"2025-{m + 1:02d}-01", vals[m]) for m in range(12)]
    got = _duckdb_pop_pct_lag(rows, 3)
    expected: list[float | None] = [None] * 3 + [
        (vals[i] - vals[i - 3]) / vals[i - 3] for i in range(3, 12)
    ]
    assert len(got) == len(expected)
    for g, e in zip(got, expected, strict=True):
        if e is None:
            assert g is None
        else:
            assert g is not None and abs(g - e) < 1e-9
