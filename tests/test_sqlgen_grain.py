"""SQL_GEN time_grain (ChartQuery.time_grain): truncate the time x-axis to a period.

Domain-neutral and deterministic: a long daily series buckets to week/month/quarter/year so
it reads as a trend. ClickHouse uses toStartOf* (week mode 1 = Monday); other dialects use
date_trunc (Postgres week is Monday-based). Two checks: SQL *shape* per dialect, and SQL
*numbers* (Postgres SQL run in DuckDB, asserted against a hand calculation).
"""

from __future__ import annotations

import pytest

from auto_bi.agent.sql_guard import guard_sql
from auto_bi.agent.sqlgen import generate_chart_sql
from auto_bi.ir.spec import ChartQuery, Measure, MeasureTransform, OrderBy, TimeGrain
from auto_bi.semantic.model import Aggregation


def _grain_q(grain: TimeGrain, **kwargs) -> ChartQuery:
    defaults = dict(
        table="dm.sales_daily",
        dimensions=["date"],
        measures=[Measure(column="revenue", agg=Aggregation.SUM)],
        time_grain=grain,
    )
    defaults.update(kwargs)
    return ChartQuery(**defaults)


# the grain-truncated time column is qualified with its base table to avoid the ClickHouse
# alias-shadow: `toStartOfMonth(date) AS date ... GROUP BY toStartOfMonth(date)` makes CH bind
# the inner `date` to the alias and raise NOT_AN_AGGREGATE (live-verified 2026-06-28).
_DATE = '"dm"."sales_daily"."date"'


# --- SQL shape -------------------------------------------------------------


def test_month_grain_clickhouse() -> None:
    sql = generate_chart_sql(_grain_q(TimeGrain.MONTH))
    # truncated in BOTH the SELECT (aliased back to the bare name) and the GROUP BY
    assert f'toStartOfMonth({_DATE}) AS "date"' in sql
    assert f"GROUP BY toStartOfMonth({_DATE})" in sql
    guard_sql(sql)


def test_month_grain_postgres() -> None:
    sql = generate_chart_sql(_grain_q(TimeGrain.MONTH), dialect="postgres")
    low = sql.lower()
    assert f"date_trunc('month', {_DATE})" in low
    assert f"group by date_trunc('month', {_DATE})" in low
    guard_sql(sql, dialect="postgres")


def test_quarter_and_year_grain_clickhouse() -> None:
    assert f"toStartOfQuarter({_DATE})" in generate_chart_sql(_grain_q(TimeGrain.QUARTER))
    assert f"toStartOfYear({_DATE})" in generate_chart_sql(_grain_q(TimeGrain.YEAR))


def test_week_grain_starts_monday_both_dialects() -> None:
    ch = generate_chart_sql(_grain_q(TimeGrain.WEEK))
    assert f"toStartOfWeek({_DATE}, 1)" in ch  # mode 1 => Monday, matching Postgres
    pg = generate_chart_sql(_grain_q(TimeGrain.WEEK), dialect="postgres").lower()
    assert f"date_trunc('week', {_DATE})" in pg


def test_day_grain_is_raw_no_truncation() -> None:
    sql = generate_chart_sql(_grain_q(TimeGrain.DAY))
    assert "toStartOf" not in sql and "date_trunc" not in sql.lower()
    assert '"date"' in sql  # the raw date dimension is still selected


def test_grain_orders_by_expression_not_alias_clickhouse() -> None:
    # the grained dim is aliased back to its bare name (toStartOfMonth(...) AS "date"). The column
    # is qualified so the flat path orders by the grouped expression; ordering by the bare alias
    # "date" would bind to the PHYSICAL column and fail NOT_AN_AGGREGATE on the live stand.
    sql = generate_chart_sql(_grain_q(TimeGrain.MONTH, order_by=[OrderBy(by="date", dir="asc")]))
    assert f"ORDER BY toStartOfMonth({_DATE})" in sql
    assert 'ORDER BY "date"' not in sql
    guard_sql(sql)


def test_grain_orders_by_expression_postgres() -> None:
    sql = generate_chart_sql(
        _grain_q(TimeGrain.MONTH, order_by=[OrderBy(by="date", dir="asc")]), dialect="postgres"
    )
    assert f"order by date_trunc('month', {_DATE})" in sql.lower()
    guard_sql(sql, dialect="postgres")


def test_windowed_grain_keeps_alias_order() -> None:
    # the windowed path selects FROM the subquery, where "date" is an output column with no
    # physical shadow -> it MUST keep ordering by the alias (the truncation expression isn't in
    # the outer scope). Guards against the flat-path fix leaking into the windowed path.
    q = _grain_q(TimeGrain.MONTH, order_by=[OrderBy(by="date", dir="asc")])
    q = q.model_copy(
        update={
            "measures": [
                Measure(column="revenue", agg=Aggregation.SUM, transform=MeasureTransform.YOY_PCT)
            ]
        }
    )
    sql = generate_chart_sql(q)
    assert 'ORDER BY "date"' in sql  # outer orders by the subquery's date column
    assert "ORDER BY toStartOfMonth" not in sql
    guard_sql(sql)


def test_grain_composes_with_transform_month_over_month() -> None:
    # month grain + pop_pct = month-over-month: inner truncates to month, the window walks it
    q = _grain_q(TimeGrain.MONTH)
    q = q.model_copy(
        update={
            "measures": [
                Measure(column="revenue", agg=Aggregation.SUM, transform=MeasureTransform.POP_PCT)
            ]
        }
    )
    sql = generate_chart_sql(q)
    assert f'toStartOfMonth({_DATE}) AS "date"' in sql  # inner truncates
    assert "lagInFrame(" in sql  # window over the monthly series
    guard_sql(sql)


def test_grain_composes_with_ratio() -> None:
    # month grain + ratio: inner groups by month and emits both aggregates, outer divides
    q = _grain_q(
        TimeGrain.MONTH,
        measures=[
            Measure(
                column="revenue",
                agg=Aggregation.SUM,
                denominator=Measure(column="orders", agg=Aggregation.SUM),
            )
        ],
    )
    sql = generate_chart_sql(q)
    assert f"GROUP BY toStartOfMonth({_DATE})" in sql
    assert 'SUM("orders") AS "__den_0"' in sql
    guard_sql(sql)


# --- numeric verification in DuckDB (postgres date_trunc) ------------------


def _duckdb_grain_rows(grain: TimeGrain, rows: list[tuple[str, float]]) -> list[tuple]:
    duckdb = pytest.importorskip("duckdb")  # ephemeral dev dep, `--with duckdb` in CI
    con = duckdb.connect()
    con.execute("CREATE SCHEMA dm; CREATE TABLE dm.sales_daily (date DATE, revenue DOUBLE)")
    con.executemany("INSERT INTO dm.sales_daily VALUES (?, ?)", rows)
    q = _grain_q(grain, order_by=[OrderBy(by="date", dir="asc")])
    sql = generate_chart_sql(q, dialect="postgres")
    return con.execute(sql).fetchall()


def test_month_grain_aggregates_by_month() -> None:
    rows = [
        ("2024-01-05", 10.0),
        ("2024-01-20", 20.0),  # Jan -> 30
        ("2024-02-10", 5.0),  # Feb -> 5
        ("2024-03-01", 7.0),
        ("2024-03-31", 3.0),  # Mar -> 10
    ]
    out = _duckdb_grain_rows(TimeGrain.MONTH, rows)
    assert [r[1] for r in out] == [30.0, 5.0, 10.0]
    assert str(out[0][0]).startswith("2024-01-01")  # bucket truncated to first of month
