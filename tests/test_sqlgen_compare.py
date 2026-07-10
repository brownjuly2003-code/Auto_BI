"""SQL_GEN scalar period-compare KPI (`Measure.compare`): one number = the latest period vs a
period back (yoy/pop), as a percent or absolute change.

Unlike the yoy_pct transform (a windowed SERIES), this reduces to a SINGLE row by conditional
aggregation over two time buckets, so a big_number stays a true scalar. Two checks: SQL *shape*
per dialect, and SQL *numbers* (Postgres SQL run in DuckDB vs a hand calculation).
"""

from __future__ import annotations

import pytest

from auto_bi.agent.sql_guard import guard_sql
from auto_bi.agent.sqlgen import generate_chart_sql
from auto_bi.ir.spec import (
    ChartQuery,
    Measure,
    ScalarCompare,
    ScalarCompareKind,
    ScalarCompareOutput,
    TimeGrain,
    Viz,
)
from auto_bi.semantic.model import Aggregation


def _cmp_q(
    grain: TimeGrain = TimeGrain.MONTH,
    kind: ScalarCompareKind = ScalarCompareKind.YOY,
    output: ScalarCompareOutput = ScalarCompareOutput.PCT,
    agg: Aggregation = Aggregation.SUM,
    column: str = "revenue",
    **kwargs,
) -> ChartQuery:
    defaults = dict(
        table="dm.sales_daily",
        measures=[
            Measure(
                column=column,
                agg=agg,
                compare=ScalarCompare(column="date", grain=grain, kind=kind, output=output),
            )
        ],
    )
    defaults.update(kwargs)
    return ChartQuery(**defaults)


# the truncated time column is qualified with its base table (ClickHouse alias-shadow fix, see
# sqlgen._grained_source) — inside both the bucket subquery and the outer CASE conditions
_DATE = '"dm"."sales_daily"."date"'


# --- SQL shape -------------------------------------------------------------


def test_compare_yoy_month_clickhouse() -> None:
    sql = generate_chart_sql(_cmp_q())
    # one-row bucket subquery: latest month present and the matching month a year back
    assert f'MAX(toStartOfMonth({_DATE})) AS "p_cur"' in sql
    assert f'MAX(toStartOfMonth({_DATE})) - INTERVAL 12 MONTH AS "p_prev"' in sql
    assert "CROSS JOIN (SELECT" in sql
    # conditional aggregation over each bucket, (cur - prev) / prev in Float64
    assert f'SUM(CASE WHEN toStartOfMonth({_DATE}) = "b"."p_cur" THEN "revenue" END)' in sql
    assert "AS Nullable(Float64)) / nullIf(" in sql
    assert 'AS "yoy_sum_revenue"' in sql
    # a true scalar: no GROUP BY, no displayed dimension
    assert "GROUP BY" not in sql
    guard_sql(sql)


def test_compare_yoy_month_postgres() -> None:
    sql = generate_chart_sql(_cmp_q(), dialect="postgres")
    assert "date_trunc('month'" in sql.lower()
    assert "INTERVAL '12 MONTH'" in sql
    assert "AS DOUBLE PRECISION) / NULLIF(" in sql
    guard_sql(sql, dialect="postgres")


def test_compare_yoy_quarter_lags_full_year_in_months() -> None:
    # a quarter compare shifts a full year = 4 quarters, expressed as 12 months so the shifted
    # bucket lands exactly on a toStartOfQuarter boundary
    sql = generate_chart_sql(_cmp_q(TimeGrain.QUARTER))
    assert f'MAX(toStartOfQuarter({_DATE})) - INTERVAL 12 MONTH AS "p_prev"' in sql


def test_compare_yoy_week_lags_364_days() -> None:
    # a week compare shifts 52 weeks = 364 days so the shifted bucket stays a Monday
    sql = generate_chart_sql(_cmp_q(TimeGrain.WEEK))
    assert '- INTERVAL 364 DAY AS "p_prev"' in sql


def test_compare_pop_month_shifts_one_period() -> None:
    sql = generate_chart_sql(_cmp_q(kind=ScalarCompareKind.POP))
    assert f'MAX(toStartOfMonth({_DATE})) - INTERVAL 1 MONTH AS "p_prev"' in sql
    assert 'AS "pop_sum_revenue"' in sql


def test_compare_abs_is_plain_difference_no_ratio() -> None:
    # output=abs => cur - prev, no division / Float64 cast
    sql = generate_chart_sql(_cmp_q(output=ScalarCompareOutput.ABS))
    assert "nullIf(" not in sql
    assert "Nullable(Float64)" not in sql
    assert 'AS "yoy_sum_revenue"' in sql


def test_compare_count_uses_conditional_count() -> None:
    sql = generate_chart_sql(_cmp_q(agg=Aggregation.COUNT, column="order_id"))
    assert f'COUNT(CASE WHEN toStartOfMonth({_DATE}) = "b"."p_cur" THEN "order_id" END)' in sql
    assert 'AS "yoy_count_order_id"' in sql


def test_compare_filters_apply_to_both_bucket_and_aggregate() -> None:
    from auto_bi.ir.spec import FilterOp, QueryFilter

    q = _cmp_q(filters=[QueryFilter(column="region", op=FilterOp.EQ, value="RF")])
    sql = generate_chart_sql(q)
    # the WHERE appears twice: once in the bucket subquery, once in the outer aggregate
    assert sql.count("\"region\" = 'RF'") == 2


def test_compare_time_lower_bound_widens_in_outer_scan_only() -> None:
    # A "last quarter" period filter clips the prior quarter's rows: without widening the outer
    # scan, the prior conditional aggregate is NULL and the KPI tile renders empty (live bug,
    # dashboard /uif1gsbulluid 2026-07-10). The outer >= must shift back one compare interval;
    # the bucket subquery keeps the original bound so p_cur anchors in the REQUESTED window.
    from auto_bi.ir.spec import FilterOp, QueryFilter

    q = _cmp_q(
        TimeGrain.QUARTER,
        kind=ScalarCompareKind.POP,
        filters=[
            QueryFilter(column="date", op=FilterOp.GTE, value="2026-04-01"),
            QueryFilter(column="date", op=FilterOp.LTE, value="2026-06-30"),
        ],
    )
    sql = generate_chart_sql(q)
    # subquery: original bound; outer: widened bound (cast to date, minus one quarter;
    # sqlglot's clickhouse dialect renders the cast target as Nullable(DATE))
    assert "\"date\" >= '2026-04-01'" in sql
    assert "CAST('2026-04-01' AS Nullable(DATE)) - INTERVAL 3 MONTH" in sql
    # the upper bound stays as-is in both scans
    assert sql.count("\"date\" <= '2026-06-30'") == 2
    guard_sql(sql)

    pg = generate_chart_sql(q, dialect="postgres")
    assert "CAST('2026-04-01' AS DATE) - INTERVAL '3 MONTH'" in pg
    guard_sql(pg, dialect="postgres")


def test_compare_non_time_gte_filter_is_not_widened() -> None:
    # widening applies only to the compare time column: a numeric lower bound on another column
    # must pass through untouched in both scans
    from auto_bi.ir.spec import FilterOp, QueryFilter

    q = _cmp_q(filters=[QueryFilter(column="revenue", op=FilterOp.GTE, value=100)])
    sql = generate_chart_sql(q)
    assert sql.count('"revenue" >= 100') == 2
    assert "AS Nullable(DATE)" not in sql  # no widened date bound anywhere


# --- numeric verification in DuckDB (postgres semantics) -------------------


def _duckdb_scalar(
    rows: list[tuple[str, float]],
    *,
    query: ChartQuery | None = None,
    kind: ScalarCompareKind = ScalarCompareKind.YOY,
    output: ScalarCompareOutput = ScalarCompareOutput.PCT,
) -> float | None:
    duckdb = pytest.importorskip("duckdb")  # ephemeral dev dep, `--with duckdb` in CI
    con = duckdb.connect()
    con.execute("CREATE SCHEMA dm; CREATE TABLE dm.sales_daily (date DATE, revenue DOUBLE)")
    con.executemany("INSERT INTO dm.sales_daily VALUES (?, ?)", rows)
    sql = generate_chart_sql(query or _cmp_q(kind=kind, output=output), dialect="postgres")
    result = con.execute(sql).fetchall()
    assert len(result) == 1  # a scalar KPI is exactly one row
    return result[0][0]


def _monthly(n: int, start_year: int = 2023) -> tuple[list[tuple[str, float]], list[float]]:
    """n consecutive months of monotone values from start_year-01."""
    vals = [float(100 + 10 * i) for i in range(n)]
    rows: list[tuple[str, float]] = []
    year, month = start_year, 1
    for v in vals:
        rows.append((f"{year}-{month:02d}-01", v))
        month += 1
        if month == 13:
            month, year = 1, year + 1
    return rows, vals


def test_compare_yoy_pct_numbers_match_hand_calc() -> None:
    rows, vals = _monthly(24)  # 2023-01 .. 2024-12
    got = _duckdb_scalar(rows)
    # latest present month = 2024-12 (index 23), year back = 2023-12 (index 11)
    expected = (vals[23] - vals[11]) / vals[11]
    assert got is not None and abs(got - expected) < 1e-9


def test_compare_pop_abs_numbers_match_hand_calc() -> None:
    rows, vals = _monthly(24)
    got = _duckdb_scalar(rows, kind=ScalarCompareKind.POP, output=ScalarCompareOutput.ABS)
    # latest = index 23, previous month = index 22
    assert got is not None and abs(got - (vals[23] - vals[22])) < 1e-9


def test_compare_missing_prior_bucket_is_null() -> None:
    # only 2024-01..2024-06: the latest month's year-back bucket (2023-06) is absent -> prior sum
    # is NULL over zero rows -> the ratio is NULL, never a crash
    rows, _ = _monthly(6, start_year=2024)
    assert _duckdb_scalar(rows) is None


def test_compare_pop_pct_survives_period_filter_numbers() -> None:
    # the live bug reproduced numerically: a "last quarter" filter (Q2) with a QoQ compare —
    # the widened outer scan lets Q1 rows feed p_prev, so the KPI is (Q2 - Q1) / Q1, not NULL
    from auto_bi.ir.spec import FilterOp, QueryFilter

    rows, vals = _monthly(6, start_year=2024)  # 2024-01 .. 2024-06
    q = _cmp_q(
        TimeGrain.QUARTER,
        kind=ScalarCompareKind.POP,
        filters=[
            QueryFilter(column="date", op=FilterOp.GTE, value="2024-04-01"),
            QueryFilter(column="date", op=FilterOp.LTE, value="2024-06-30"),
        ],
    )
    got = _duckdb_scalar(rows, query=q)
    q1, q2 = sum(vals[0:3]), sum(vals[3:6])
    assert got is not None and abs(got - (q2 - q1) / q1) < 1e-9


# --- routing: a compare measure never touches the windowed/flat paths ------


def test_big_number_compare_shape_is_scalar() -> None:
    # sanity: the compare query has zero dimensions (validate enforces this for big_number)
    q = _cmp_q()
    assert q.dimensions == []
    assert q.measures[0].compare is not None
    # and the spec-level viz would be big_number (not asserted here — see test_ir_validate)
    _ = Viz.BIG_NUMBER
