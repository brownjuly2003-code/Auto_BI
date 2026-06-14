"""apply_chart_defaults — deterministic top-N on categorical charts (B1).

The normalizer only acts on bar/stacked_bar/pie over a non-time dimension that does not
already order by a measure; it sets `order_by = [first measure desc]` and tightens the
limit. Everything else is left byte-for-byte unchanged. It is pure and idempotent.
"""

from auto_bi.agent.normalize import apply_chart_defaults
from auto_bi.agent.sqlgen import generate_chart_sql
from auto_bi.ir.spec import (
    ChartQuery,
    ChartSpec,
    DashboardSpec,
    Measure,
    OrderBy,
    Viz,
)
from auto_bi.semantic.model import Aggregation

REVENUE = Measure(column="revenue", agg=Aggregation.SUM)  # alias -> "sum_revenue"


def _chart(viz: Viz, *, dimensions, order_by=None, limit=5000, measures=None) -> ChartSpec:
    return ChartSpec(
        id="c1",
        title="t",
        viz=viz,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=list(dimensions),
            measures=measures or [REVENUE],
            order_by=list(order_by or []),
            limit=limit,
        ),
    )


def _spec(chart: ChartSpec) -> DashboardSpec:
    return DashboardSpec(title="d", charts=[chart])


def _only(spec: DashboardSpec) -> ChartQuery:
    return spec.charts[0].query


# --- the core fix: an unordered categorical wall gets a top-N ----------------------


def test_bar_over_dimension_gets_topn(demo_model) -> None:
    out = apply_chart_defaults(_spec(_chart(Viz.BAR, dimensions=["store_id"])), demo_model)
    q = _only(out)
    assert q.order_by == [OrderBy(by="sum_revenue", dir="desc")]
    assert q.limit == 25


def test_pie_caps_limit_at_12(demo_model) -> None:
    out = apply_chart_defaults(_spec(_chart(Viz.PIE, dimensions=["store_id"])), demo_model)
    q = _only(out)
    assert q.order_by == [OrderBy(by="sum_revenue", dir="desc")]
    assert q.limit == 12


def test_stacked_bar_over_dimension_gets_topn(demo_model) -> None:
    out = apply_chart_defaults(_spec(_chart(Viz.STACKED_BAR, dimensions=["store_id"])), demo_model)
    assert _only(out).order_by == [OrderBy(by="sum_revenue", dir="desc")]
    assert _only(out).limit == 25


def test_dimension_only_order_is_replaced_by_measure_desc(demo_model) -> None:
    # ordering by the dimension itself is still a wall of 4000 sorted bars, not a top-N
    chart = _chart(Viz.BAR, dimensions=["store_id"], order_by=[OrderBy(by="store_id", dir="asc")])
    q = _only(apply_chart_defaults(_spec(chart), demo_model))
    assert q.order_by == [OrderBy(by="sum_revenue", dir="desc")]
    assert q.limit == 25


# --- skips: untouched specs --------------------------------------------------------


def test_explicit_measure_topn_is_untouched(demo_model) -> None:
    chart = _chart(
        Viz.BAR,
        dimensions=["store_id"],
        order_by=[OrderBy(by="sum_revenue", dir="desc")],
        limit=15,
    )
    out = apply_chart_defaults(_spec(chart), demo_model)
    assert out == _spec(chart)  # byte-for-byte unchanged, limit 15 preserved


def test_order_by_raw_measure_column_counts_as_topn(demo_model) -> None:
    # SQL_GEN treats the raw measure column as a measure order target; so must we
    chart = _chart(
        Viz.BAR, dimensions=["store_id"], order_by=[OrderBy(by="revenue", dir="desc")], limit=10
    )
    assert apply_chart_defaults(_spec(chart), demo_model) == _spec(chart)


def test_time_dimension_bar_is_untouched(demo_model) -> None:
    # a column time-series is ordered by time, not ranked by value
    chart = _chart(Viz.BAR, dimensions=["date"])
    assert apply_chart_defaults(_spec(chart), demo_model) == _spec(chart)


def test_line_is_untouched(demo_model) -> None:
    chart = _chart(Viz.LINE, dimensions=["date"])
    assert apply_chart_defaults(_spec(chart), demo_model) == _spec(chart)


def test_table_is_untouched(demo_model) -> None:
    chart = _chart(Viz.TABLE, dimensions=["store_id"])
    assert apply_chart_defaults(_spec(chart), demo_model) == _spec(chart)


def test_small_explicit_limit_is_not_widened(demo_model) -> None:
    chart = _chart(Viz.BAR, dimensions=["store_id"], limit=10)
    assert _only(apply_chart_defaults(_spec(chart), demo_model)).limit == 10


# --- properties --------------------------------------------------------------------


def test_idempotent(demo_model) -> None:
    once = apply_chart_defaults(_spec(_chart(Viz.BAR, dimensions=["store_id"])), demo_model)
    twice = apply_chart_defaults(once, demo_model)
    assert twice == once


def test_normalized_query_emits_order_by_and_limit(demo_model) -> None:
    out = apply_chart_defaults(_spec(_chart(Viz.BAR, dimensions=["store_id"])), demo_model)
    sql = generate_chart_sql(_only(out))
    assert 'ORDER BY "sum_revenue" DESC' in sql
    assert "LIMIT 25" in sql
