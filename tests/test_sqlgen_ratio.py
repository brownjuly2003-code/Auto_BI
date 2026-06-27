"""SQL_GEN ratio measures (Measure.denominator): `agg(num) / agg(den)`.

A ratio is a domain-neutral primitive (margin, conversion, defect rate, avg duration). It
reuses the two-level derived path: an inner GROUP BY computes BOTH aggregates, the outer
SELECT divides them in floating point with a divide-by-zero guard. Two checks:
- SQL *shape* per dialect (the inner __src_/__den_ aliases, the guarded float division);
- SQL *numbers*: the generated Postgres SQL runs in DuckDB over synthetic rows and is
  asserted against an independent hand calculation (covers the Greenplum/postgres path).
ClickHouse numbers still need a live-stand check (see derived-metrics live-verify gate).
"""

from __future__ import annotations

import pytest

from auto_bi.agent.sql_guard import guard_sql
from auto_bi.agent.sqlgen import generate_chart_sql
from auto_bi.ir.spec import (
    ChartQuery,
    Measure,
    OrderBy,
    is_compact_number,
    is_percent_measure,
    is_ratio_measure,
    measure_alias,
)
from auto_bi.semantic.model import Aggregation


def _ratio_q(**kwargs) -> ChartQuery:
    defaults = dict(
        table="dm.sales_daily",
        dimensions=["date"],
        measures=[
            Measure(
                column="revenue",
                agg=Aggregation.SUM,
                denominator=Measure(column="orders", agg=Aggregation.SUM),
            )
        ],
    )
    defaults.update(kwargs)
    return ChartQuery(**defaults)


# --- SQL shape -------------------------------------------------------------


def test_ratio_two_level_clickhouse() -> None:
    sql = generate_chart_sql(_ratio_q())
    # inner GROUP BY emits BOTH aggregates under private aliases
    assert 'SUM("revenue") AS "__src_0"' in sql
    assert 'SUM("orders") AS "__den_0"' in sql
    # outer: numerator cast to float (CH Decimal/Decimal keeps the dividend scale, see
    # _safe_div), divided by a zero-guarded denominator, aliased to the ratio name
    assert 'CAST("__src_0" AS' in sql and "Float64)" in sql  # numerator cast to float
    assert 'nullIf("__den_0", 0)' in sql  # ClickHouse NULLIF spelling
    assert 'AS "sum_revenue_per_sum_orders"' in sql
    guard_sql(sql)


def test_ratio_two_level_postgres() -> None:
    sql = generate_chart_sql(_ratio_q(), dialect="postgres")
    assert 'SUM("revenue") AS "__src_0"' in sql
    assert 'SUM("orders") AS "__den_0"' in sql
    assert "DOUBLE PRECISION" in sql  # postgres spelling of the float cast
    assert 'NULLIF("__den_0", 0)' in sql
    guard_sql(sql, dialect="postgres")


def test_ratio_big_number_has_no_group_by() -> None:
    # no dimension: a single ratio KPI — inner has no GROUP BY, still two aggregates divided
    sql = generate_chart_sql(_ratio_q(dimensions=[]))
    assert "GROUP BY" not in sql.upper()
    assert 'SUM("revenue") AS "__src_0"' in sql
    assert 'SUM("orders") AS "__den_0"' in sql
    guard_sql(sql)


def test_ratio_over_category_groups_in_inner() -> None:
    sql = generate_chart_sql(_ratio_q(dimensions=["store_id"]))
    assert 'GROUP BY "store_id"' in sql
    assert 'SUM("orders") AS "__den_0"' in sql
    guard_sql(sql)


def test_plain_measures_stay_flat() -> None:
    # no denominator and no transform -> flat single-level SELECT, no private aliases
    sql = generate_chart_sql(
        ChartQuery(
            table="dm.sales_daily",
            dimensions=["date"],
            measures=[Measure(column="revenue", agg=Aggregation.SUM)],
        )
    )
    assert "__den_" not in sql and "__src_" not in sql


def test_ratio_alongside_plain_measure() -> None:
    # a plain measure passes through the outer SELECT; the ratio divides its own inner pair,
    # indexed by the measure position so __den follows the ratio measure's index
    q = ChartQuery(
        table="dm.sales_daily",
        dimensions=["date"],
        measures=[
            Measure(column="revenue", agg=Aggregation.SUM, label="Выручка"),
            Measure(
                column="revenue",
                agg=Aggregation.SUM,
                denominator=Measure(column="orders", agg=Aggregation.SUM),
            ),
        ],
    )
    sql = generate_chart_sql(q)
    assert 'SUM("revenue") AS "__src_0"' in sql  # plain measure base aggregate
    assert 'SUM("revenue") AS "__src_1"' in sql  # ratio numerator
    assert 'SUM("orders") AS "__den_1"' in sql  # ratio denominator at measure index 1
    assert '"__src_0" AS "Выручка"' in sql  # plain measure passthrough


# --- alias / format --------------------------------------------------------


def test_ratio_alias_distinct_from_base() -> None:
    ratio = Measure(
        column="revenue",
        agg=Aggregation.SUM,
        denominator=Measure(column="orders", agg=Aggregation.SUM),
    )
    # distinct from the plain numerator's alias so both can live in one chart
    assert measure_alias(ratio) == "sum_revenue_per_sum_orders"
    assert measure_alias(Measure(column="revenue", agg=Aggregation.SUM)) == "sum_revenue"


def test_ratio_label_wins() -> None:
    ratio = Measure(
        column="revenue",
        agg=Aggregation.SUM,
        label="Маржа",
        denominator=Measure(column="orders", agg=Aggregation.SUM),
    )
    assert measure_alias(ratio) == "Маржа"


def test_ratio_is_exact_number_not_compact_or_percent() -> None:
    ratio = Measure(
        column="revenue",
        agg=Aggregation.SUM,
        denominator=Measure(column="orders", agg=Aggregation.SUM),
    )
    assert is_ratio_measure(ratio) is True
    assert is_compact_number(ratio) is False  # a rate/average is small and exact, not "236K"
    assert is_percent_measure(ratio) is False


# --- numeric verification in DuckDB (postgres window semantics = Greenplum path) ----


def _duckdb_ratio_values(rows: list[tuple[str, float, int]]) -> list[float | None]:
    duckdb = pytest.importorskip("duckdb")  # ephemeral dev dep, `--with duckdb` in CI
    con = duckdb.connect()
    con.execute(
        "CREATE SCHEMA dm; "
        "CREATE TABLE dm.sales_daily (date DATE, revenue DOUBLE, orders INTEGER)"
    )
    con.executemany("INSERT INTO dm.sales_daily VALUES (?, ?, ?)", rows)
    sql = generate_chart_sql(_ratio_q(order_by=[OrderBy(by="date", dir="asc")]), dialect="postgres")
    return [r[1] for r in con.execute(sql).fetchall()]


# two source rows on 2024-01-01 prove the inner GROUP BY runs before the division;
# a zero-orders day proves the divide-by-zero guard (NULLIF) yields NULL, not an error
_ROWS = [
    ("2024-01-01", 60.0, 2),
    ("2024-01-01", 40.0, 3),  # date total: revenue 100 / orders 5 -> 20.0
    ("2024-02-01", 150.0, 5),  # 30.0
    ("2024-03-01", 120.0, 4),  # 30.0
    ("2024-04-01", 50.0, 0),  # orders 0 -> NULL
]


def test_ratio_numbers_match_hand_calc() -> None:
    got = _duckdb_ratio_values(_ROWS)
    expected: list[float | None] = [20.0, 30.0, 30.0, None]
    assert len(got) == len(expected)
    for g, e in zip(got, expected, strict=True):
        if e is None:
            assert g is None
        else:
            assert g is not None and abs(g - e) < 1e-9
