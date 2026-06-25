"""SQL_GEN windowed path: analytical transforms (PoP / share / running total).

Two complementary checks:
- SQL *shape* per dialect (ClickHouse `lagInFrame` vs Postgres `LAG`, the two-level
  inner-GROUP-BY / outer-window structure, parenthesized pop_pct numerator);
- SQL *numbers*: the generated Postgres SQL is executed in DuckDB (PG-compatible window
  semantics) over synthetic rows and asserted against an independent hand calculation.

The numeric pass covers the Greenplum/Greengage (postgres) path end-to-end. ClickHouse's
`lagInFrame` is frame-bounded, so its frame semantics still need a live-stand check — see
docs/plans/2026-06-25-derived-metrics-pop.md ("live-verify gate").
"""

from __future__ import annotations

import pytest

from auto_bi.agent.sql_guard import guard_sql
from auto_bi.agent.sqlgen import generate_chart_sql
from auto_bi.ir.spec import ChartQuery, JoinSpec, Measure, MeasureTransform, OrderBy
from auto_bi.semantic.model import Aggregation


def make_q(transform: MeasureTransform | None, **kwargs) -> ChartQuery:
    defaults = dict(
        table="dm.sales_daily",
        dimensions=["date"],
        measures=[Measure(column="revenue", agg=Aggregation.SUM, transform=transform)],
    )
    defaults.update(kwargs)
    return ChartQuery(**defaults)


# --- SQL shape -------------------------------------------------------------


def test_no_transform_stays_flat() -> None:
    # the windowed path is only taken when a measure carries a transform
    sql = generate_chart_sql(make_q(None))
    assert "__src_" not in sql
    assert "OVER" not in sql.upper()


def test_pop_abs_clickhouse_uses_laginframe_and_subquery() -> None:
    sql = generate_chart_sql(make_q(MeasureTransform.POP_ABS))
    # two-level: inner GROUP BY of the base aggregate under a private alias
    assert (
        'SELECT "date", SUM("revenue") AS "__src_0" FROM "dm"."sales_daily" GROUP BY "date"' in sql
    )
    # outer window: ClickHouse lagInFrame with an explicit ROWS frame, over the inner alias.
    # The source is wrapped in toNullable so the first (out-of-frame) row is NULL, not 0:
    # ClickHouse lagInFrame returns the type default (0) out-of-frame, where Postgres LAG
    # gives NULL. Verified live on the stand (docs/plans/2026-06-25-derived-metrics-pop.md §6).
    assert (
        'lagInFrame(toNullable("__src_0")) OVER '
        '(ORDER BY "date" ROWS BETWEEN 1 PRECEDING AND CURRENT ROW)'
    ) in sql
    assert 'AS "pop_abs_sum_revenue"' in sql
    guard_sql(sql)


def test_pop_abs_postgres_uses_plain_lag() -> None:
    sql = generate_chart_sql(make_q(MeasureTransform.POP_ABS), dialect="postgres")
    assert "lagInFrame" not in sql
    # Postgres LAG already returns NULL for the first row, so the source is NOT wrapped
    assert "toNullable" not in sql
    assert 'LAG("__src_0") OVER (ORDER BY "date" ROWS BETWEEN 1 PRECEDING AND CURRENT ROW)' in sql
    guard_sql(sql, dialect="postgres")


def test_pop_pct_numerator_is_parenthesized() -> None:
    # `/` binds tighter than `-`; without parens ClickHouse computes src - (lag/lag).
    # The numerator (src - lag) must be wrapped so it is (src - lag) / NULLIF(lag, 0).
    sql = generate_chart_sql(make_q(MeasureTransform.POP_PCT))
    assert '("__src_0" - lagInFrame(' in sql
    assert "nullIf(lagInFrame(" in sql  # guarded division (ClickHouse spelling)


def test_ratio_transforms_divide_in_float() -> None:
    # ClickHouse `Decimal / Decimal` keeps the dividend's scale (2dp) so a small ratio
    # truncates to 0.00 and category shares no longer sum to 1 — the numerator must be cast
    # to float. Both pop_pct and share_of_total go through the guarded division.
    pct = generate_chart_sql(make_q(MeasureTransform.POP_PCT))
    share = generate_chart_sql(make_q(MeasureTransform.SHARE_OF_TOTAL, dimensions=["store_id"]))
    assert "CAST((" in pct and "Float64)" in pct  # numerator (src - lag) cast to float
    assert 'CAST("__src_0" AS' in share and "Float64)" in share
    # Postgres renders the same cast as DOUBLE PRECISION (numeric division is already exact)
    pct_pg = generate_chart_sql(make_q(MeasureTransform.POP_PCT), dialect="postgres")
    assert "DOUBLE PRECISION" in pct_pg


def test_share_of_total_is_window_sum_over_empty() -> None:
    sql = generate_chart_sql(make_q(MeasureTransform.SHARE_OF_TOTAL))
    assert 'SUM("__src_0") OVER ()' in sql
    assert "nullIf(SUM(" in sql  # divide-by-zero guard
    guard_sql(sql)


def test_share_of_total_allows_non_time_dimension() -> None:
    # share needs no ordering -> works over a categorical axis (store_id), no ORDER BY in OVER
    sql = generate_chart_sql(make_q(MeasureTransform.SHARE_OF_TOTAL, dimensions=["store_id"]))
    assert 'SUM("__src_0") OVER ()' in sql
    assert "OVER (ORDER BY" not in sql


def test_running_total_orders_with_cumulative_frame() -> None:
    sql = generate_chart_sql(make_q(MeasureTransform.RUNNING_TOTAL))
    assert (
        'SUM("__src_0") OVER (ORDER BY "date" ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)'
        in sql
    )
    guard_sql(sql)


def test_mixed_base_and_transform_share_inner_aggregates() -> None:
    # a base measure passes through the outer SELECT; a transform wraps its own inner alias
    q = make_q(
        None,
        measures=[
            Measure(column="revenue", agg=Aggregation.SUM, label="Выручка"),
            Measure(column="revenue", agg=Aggregation.SUM, transform=MeasureTransform.POP_PCT),
        ],
    )
    sql = generate_chart_sql(q)
    assert 'SUM("revenue") AS "__src_0", SUM("revenue") AS "__src_1"' in sql
    assert '"__src_0" AS "Выручка"' in sql  # base measure passthrough
    assert 'lagInFrame(toNullable("__src_1"))' in sql  # transform over its own source


def test_transform_order_and_limit_apply_to_outer() -> None:
    sql = generate_chart_sql(
        make_q(
            MeasureTransform.RUNNING_TOTAL,
            order_by=[OrderBy(by="date", dir="asc")],
            limit=12,
        )
    )
    # the trailing ORDER BY / LIMIT belong to the outer query (after the subquery alias "t")
    outer = sql.rsplit('AS "t"', 1)[1]
    assert 'ORDER BY "date" ASC' in outer
    assert outer.strip().endswith("LIMIT 12")


def test_share_over_joined_dimension() -> None:
    # share of total across a joined (named) category — the JOIN/GROUP BY live in the inner
    q = ChartQuery(
        table="dm.sales_daily",
        dimensions=["dm.stores.city"],
        measures=[
            Measure(
                column="revenue", agg=Aggregation.SUM, transform=MeasureTransform.SHARE_OF_TOTAL
            )
        ],
        joins=[
            JoinSpec(table="dm.stores", on_left="dm.sales_daily.store_id", on_right="dm.stores.id")
        ],
    )
    sql = generate_chart_sql(q)
    assert 'LEFT JOIN "dm"."stores"' in sql
    assert 'SUM("__src_0") OVER ()' in sql
    guard_sql(sql)


# --- numeric verification in DuckDB (postgres window semantics = Greenplum path) ----


def _duckdb_values(
    transform: MeasureTransform, rows: list[tuple[str, float]]
) -> list[float | None]:
    duckdb = pytest.importorskip("duckdb")  # ephemeral dev dep, `--with duckdb` in CI
    con = duckdb.connect()
    con.execute("CREATE SCHEMA dm; CREATE TABLE dm.sales_daily (date DATE, revenue DOUBLE)")
    con.executemany("INSERT INTO dm.sales_daily VALUES (?, ?)", rows)
    q = make_q(transform, order_by=[OrderBy(by="date", dir="asc")])
    sql = generate_chart_sql(q, dialect="postgres")
    return [r[1] for r in con.execute(sql).fetchall()]


# two source rows on 2024-01-01 prove the inner GROUP BY runs before the window
_ROWS = [
    ("2024-01-01", 60.0),
    ("2024-01-01", 40.0),  # date total -> 100
    ("2024-02-01", 150.0),
    ("2024-03-01", 120.0),
    ("2024-04-01", 200.0),
]


@pytest.mark.parametrize(
    "transform, expected",
    [
        (MeasureTransform.POP_ABS, [None, 50.0, -30.0, 80.0]),
        (MeasureTransform.POP_PCT, [None, 0.5, -0.2, 2 / 3]),
        (MeasureTransform.SHARE_OF_TOTAL, [100 / 570, 150 / 570, 120 / 570, 200 / 570]),
        (MeasureTransform.RUNNING_TOTAL, [100.0, 250.0, 370.0, 570.0]),
    ],
)
def test_transform_numbers_match_hand_calc(
    transform: MeasureTransform, expected: list[float | None]
) -> None:
    got = _duckdb_values(transform, _ROWS)
    assert len(got) == len(expected)
    for g, e in zip(got, expected, strict=True):
        if e is None:
            assert g is None
        else:
            assert g is not None and abs(g - e) < 1e-9
