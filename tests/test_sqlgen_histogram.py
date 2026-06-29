"""SQL_GEN histogram: equal-width binning of a numeric column + per-bucket count.

`ChartQuery.bins` takes the histogram path: the x-dimension is replaced by each bucket's lower
bound (`mn + idx*w`, idx clamped to bins-1) and the measure becomes the per-bucket row count.
Two checks: SQL *shape* per dialect, and SQL *numbers* (Postgres SQL run in DuckDB vs a hand calc).
"""

from __future__ import annotations

import pytest

from auto_bi.agent.sql_guard import guard_sql
from auto_bi.agent.sqlgen import generate_chart_sql
from auto_bi.ir.spec import ChartQuery, Measure
from auto_bi.semantic.model import Aggregation


def _hist_q(bins: int = 5, **kwargs) -> ChartQuery:
    defaults = dict(
        table="dm.products",
        dimensions=["price"],
        measures=[Measure(column="price", agg=Aggregation.COUNT)],
        bins=bins,
    )
    defaults.update(kwargs)
    return ChartQuery(**defaults)


# --- SQL shape -------------------------------------------------------------


def test_histogram_clickhouse_shape() -> None:
    sql = generate_chart_sql(_hist_q(5))
    # one-row subquery b: min + bucket width, both over the column CAST to float (Decimal binning
    # mis-buckets boundary rows — see _safe_div; CH renders DOUBLE as Nullable(Float64))
    assert 'AS "mn"' in sql and 'AS "w"' in sql
    assert "/ 5 AS" in sql  # width = (max - min) / bins
    assert "CAST" in sql  # binned column cast to float for the bucket arithmetic
    assert "CROSS JOIN" in sql
    # bucket = mn + LEAST(FLOOR((cast(qualified_col) - mn) / NULLIF(w, 0)), bins-1) * w
    assert "LEAST(FLOOR" in sql
    assert ", 4)" in sql  # clamp to bins-1
    # binned column qualified with the base table inside the bucket expr (anti alias-shadow)
    assert '"dm"."products"."price"' in sql
    assert 'AS "price"' in sql  # bucket aliased back to the bare dimension name
    assert 'COUNT("price") AS "count_price"' in sql
    guard_sql(sql)


def test_histogram_postgres_shape() -> None:
    sql = generate_chart_sql(_hist_q(5), dialect="postgres")
    assert "CROSS JOIN" in sql
    assert "LEAST(FLOOR" in sql
    guard_sql(sql, dialect="postgres")


def test_histogram_not_taken_without_bins() -> None:
    # a plain query (no bins) must NOT take the histogram path
    q = ChartQuery(
        table="dm.products",
        dimensions=["category"],
        measures=[Measure(column="price", agg=Aggregation.SUM)],
    )
    assert "CROSS JOIN" not in generate_chart_sql(q)


# --- numeric verification in DuckDB (postgres window semantics) ------------


def test_histogram_numbers_match_hand_calc() -> None:
    duckdb = pytest.importorskip("duckdb")  # ephemeral dev dep, `--with duckdb` in CI
    con = duckdb.connect()
    con.execute("CREATE SCHEMA dm; CREATE TABLE dm.products (price DOUBLE)")
    con.executemany("INSERT INTO dm.products VALUES (?)", [(float(i),) for i in range(100)])
    # min 0, max 99, 5 bins -> width 19.8, lower bounds 0/19.8/39.6/59.4/79.2, 20 rows each
    rows = con.execute(generate_chart_sql(_hist_q(5), dialect="postgres")).fetchall()
    lbs = [round(r[0], 2) for r in rows]
    counts = [r[1] for r in rows]
    assert lbs == [0.0, 19.8, 39.6, 59.4, 79.2]
    assert counts == [20, 20, 20, 20, 20]
    assert sum(counts) == 100  # every row lands in exactly one bucket (max clamped into the last)


def test_histogram_all_equal_values_one_bucket() -> None:
    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect()
    con.execute("CREATE SCHEMA dm; CREATE TABLE dm.products (price DOUBLE)")
    con.executemany("INSERT INTO dm.products VALUES (?)", [(5.0,)] * 10)
    # zero width (max == min): NULLIF guards the divide-by-zero -> a single bucket, no crash
    rows = con.execute(generate_chart_sql(_hist_q(4), dialect="postgres")).fetchall()
    assert sum(r[1] for r in rows) == 10
    assert len(rows) == 1


# --- NULL handling (a NULL belongs to no bucket; dialect-stable exclusion) --------------


def test_histogram_excludes_null_both_dialects() -> None:
    # a NOT NULL guard on BOTH the width subquery and the outer count, in each dialect
    for dialect in ("clickhouse", "postgres"):
        sql = generate_chart_sql(_hist_q(5), dialect=dialect)
        assert sql.count("IS NULL") == 2


def test_histogram_null_values_excluded_not_folded_into_top_bucket() -> None:
    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect()
    con.execute("CREATE SCHEMA dm; CREATE TABLE dm.products (price DOUBLE)")
    con.executemany("INSERT INTO dm.products VALUES (?)", [(float(i),) for i in range(100)])
    con.executemany("INSERT INTO dm.products VALUES (?)", [(None,)] * 7)  # NULLs -> no bucket
    rows = con.execute(generate_chart_sql(_hist_q(5), dialect="postgres")).fetchall()
    counts = [r[1] for r in rows]
    # Postgres LEAST() ignores NULL and would otherwise fold the 7 NULLs into the top bucket;
    # the guard excludes them, so each bucket keeps exactly 20 and the total stays 100
    assert counts == [20, 20, 20, 20, 20]
    assert sum(counts) == 100
    assert all(r[0] is not None for r in rows)  # no NULL-bound bucket
