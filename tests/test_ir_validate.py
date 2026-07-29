"""validate_spec: spec vs semantic model (invariant 2)."""

from auto_bi.ir.spec import (
    ChartQuery,
    ChartSpec,
    DashboardFilter,
    DashboardSpec,
    Measure,
    OrderBy,
    TargetBI,
    Viz,
)
from auto_bi.ir.validate import validate_spec
from auto_bi.semantic.model import Aggregation


def chart(viz: Viz = Viz.LINE, **query_kwargs) -> ChartSpec:
    defaults = dict(
        table="dm.sales_daily",
        dimensions=["date"],
        measures=[Measure(column="revenue", agg=Aggregation.SUM, label="Выручка")],
    )
    defaults.update(query_kwargs)
    return ChartSpec(id="c1", title="t", viz=viz, query=ChartQuery(**defaults))


def spec(*charts: ChartSpec, **kwargs) -> DashboardSpec:
    return DashboardSpec(title="d", charts=list(charts), **kwargs)


def test_valid_spec_no_errors(demo_model) -> None:
    assert validate_spec(spec(chart()), demo_model) == []


def test_target_bi_supports_datalens(demo_model) -> None:
    # S4-1 (2026-06-13): the IR can target the second BI; validation is BI-agnostic,
    # so a datalens-targeted spec validates against the model exactly like superset
    assert TargetBI.DATALENS == "datalens"
    assert validate_spec(spec(chart(), target_bi=TargetBI.DATALENS), demo_model) == []


def test_unknown_table(demo_model) -> None:
    errors = validate_spec(spec(chart(table="dm.nope")), demo_model)
    assert len(errors) == 1
    assert "unknown table" in errors[0]
    assert "dm.sales_daily" in errors[0]  # known tables listed for the repair loop


def test_unknown_columns(demo_model) -> None:
    bad = chart(
        dimensions=["nope_dim"],
        measures=[Measure(column="nope_measure", agg=Aggregation.SUM)],
    )
    errors = validate_spec(spec(bad), demo_model)
    assert any("nope_dim" in e for e in errors)
    assert any("nope_measure" in e for e in errors)


def test_time_column_as_measure_rejected(demo_model) -> None:
    bad = chart(measures=[Measure(column="date", agg=Aggregation.MAX)])
    errors = validate_spec(spec(bad), demo_model)
    assert any("cannot be a measure" in e for e in errors)


def test_numeric_agg_over_dimension_rejected(demo_model) -> None:
    # sum(store_id) validates structurally but dies late on EXPLAIN: reject early
    # with an actionable error for the repair loop
    bad = chart(measures=[Measure(column="store_id", agg=Aggregation.SUM)])
    errors = validate_spec(spec(bad), demo_model)
    assert any("sum over dimension" in e and "store_id" in e for e in errors)


def test_count_over_dimension_allowed(demo_model) -> None:
    ok = chart(measures=[Measure(column="store_id", agg=Aggregation.COUNT_DISTINCT)])
    assert validate_spec(spec(ok), demo_model) == []


def test_order_by_must_reference_chart_fields(demo_model) -> None:
    bad = chart(order_by=[OrderBy(by="что-то левое", dir="desc")])
    errors = validate_spec(spec(bad), demo_model)
    assert any("order_by" in e for e in errors)


def test_order_by_measure_label_ok(demo_model) -> None:
    ok = chart(order_by=[OrderBy(by="Выручка", dir="desc")])
    assert validate_spec(spec(ok), demo_model) == []


def test_order_by_computed_alias_ok(demo_model) -> None:
    # measure without a label: ordering by its computed alias <agg>_<col> is valid
    ok = chart(
        measures=[Measure(column="revenue", agg=Aggregation.SUM)],
        order_by=[OrderBy(by="sum_revenue", dir="desc")],
    )
    assert validate_spec(spec(ok), demo_model) == []


def test_empty_in_filter_rejected(demo_model) -> None:
    from auto_bi.ir.spec import FilterOp, QueryFilter

    bad = chart(filters=[QueryFilter(column="store_id", op=FilterOp.IN, value=[])])
    errors = validate_spec(spec(bad), demo_model)
    assert any("empty value list" in e for e in errors)


def test_pivot_shape_ok(demo_model) -> None:
    ok = chart(viz=Viz.PIVOT, dimensions=[], rows=["store_id"], columns=["date"])
    assert validate_spec(spec(ok), demo_model) == []


def test_pivot_requires_rows_and_forbids_dimensions(demo_model) -> None:
    no_rows = chart(viz=Viz.PIVOT, dimensions=[], rows=[])
    assert any("needs at least one row" in e for e in validate_spec(spec(no_rows), demo_model))
    with_dims = chart(viz=Viz.PIVOT, dimensions=["date"], rows=["store_id"])
    assert any("must not set dimensions" in e for e in validate_spec(spec(with_dims), demo_model))


def test_heatmap_needs_two_dimensions(demo_model) -> None:
    bad = chart(viz=Viz.HEATMAP, dimensions=["date"])
    assert any("exactly two dimensions" in e for e in validate_spec(spec(bad), demo_model))
    ok = chart(viz=Viz.HEATMAP, dimensions=["store_id", "date"])
    assert validate_spec(spec(ok), demo_model) == []


def test_pie_needs_one_dim_one_measure(demo_model) -> None:
    ok = chart(viz=Viz.PIE, dimensions=["store_id"])
    assert validate_spec(spec(ok), demo_model) == []
    bad = chart(viz=Viz.PIE, dimensions=["store_id", "product_id"])
    assert any("pie needs exactly one dimension" in e for e in validate_spec(spec(bad), demo_model))


def test_stacked_bar_series_ok_but_line_forbids_pivot_roles(demo_model) -> None:
    ok = chart(viz=Viz.STACKED_BAR, dimensions=["date"], series=["store_id"])
    assert validate_spec(spec(ok), demo_model) == []
    bad = chart(viz=Viz.LINE, dimensions=["date"], rows=["store_id"])
    assert any("must not set rows" in e for e in validate_spec(spec(bad), demo_model))


def test_unknown_series_column_rejected(demo_model) -> None:
    bad = chart(viz=Viz.STACKED_BAR, dimensions=["date"], series=["nope_col"])
    assert any("unknown series column" in e for e in validate_spec(spec(bad), demo_model))


def test_big_number_shape(demo_model) -> None:
    bad = chart(viz=Viz.BIG_NUMBER)  # has a dimension
    errors = validate_spec(spec(bad), demo_model)
    assert any("big_number" in e for e in errors)

    ok = chart(viz=Viz.BIG_NUMBER, dimensions=[])
    assert validate_spec(spec(ok), demo_model) == []


def test_line_needs_dimension(demo_model) -> None:
    bad = chart(dimensions=[])
    errors = validate_spec(spec(bad), demo_model)
    assert any("at least one dimension" in e for e in errors)


def test_dashboard_filter_resolution(demo_model) -> None:
    ok = spec(chart(), filters=[DashboardFilter(column="dm.sales_daily.date")])
    assert validate_spec(ok, demo_model) == []

    bad = spec(chart(), filters=[DashboardFilter(column="dm.sales_daily.nope")])
    assert any("dashboard filter" in e for e in validate_spec(bad, demo_model))


def test_duplicate_chart_ids(demo_model) -> None:
    errors = validate_spec(spec(chart(), chart()), demo_model)
    assert any("not unique" in e for e in errors)


# --- joins (cross-table dimensions) ------------------------------------------------


def _join_chart(**query_overrides):
    from auto_bi.ir.spec import ChartQuery, ChartSpec, JoinSpec, Measure, Viz

    defaults = dict(
        table="dm.sales_daily",
        dimensions=["dm.stores.city"],
        measures=[Measure(column="revenue", agg="sum", label="Выручка")],
        joins=[
            JoinSpec(
                table="dm.stores",
                on_left="dm.sales_daily.store_id",
                on_right="dm.stores.id",
            )
        ],
    )
    defaults.update(query_overrides)
    return ChartSpec(id="j", title="j", viz=Viz.BAR, query=ChartQuery(**defaults))


def _spec_of(chart):
    from auto_bi.ir.spec import DashboardSpec

    return DashboardSpec(title="t", charts=[chart])


def test_join_matching_model_edge_is_valid(demo_model) -> None:
    assert validate_spec(_spec_of(_join_chart()), demo_model) == []


def test_join_not_in_model_is_rejected(demo_model) -> None:
    from auto_bi.ir.spec import JoinSpec

    chart = _join_chart(
        joins=[
            JoinSpec(
                table="dm.stores",
                on_left="dm.sales_daily.orders",  # invented condition
                on_right="dm.stores.id",
            )
        ]
    )
    errors = validate_spec(_spec_of(chart), demo_model)
    assert any("not an edge of the semantic model" in e for e in errors)


def test_joined_dimension_without_join_is_rejected_with_hint(demo_model) -> None:
    chart = _join_chart(joins=[])
    errors = validate_spec(_spec_of(chart), demo_model)
    assert any("without a matching entry in query.joins" in e for e in errors)


def test_bare_foreign_column_hints_qualification(demo_model) -> None:
    chart = _join_chart(dimensions=["city"], joins=[])
    errors = validate_spec(_spec_of(chart), demo_model)
    assert any("dm.stores.city" in e and "JOIN" in e for e in errors)


def test_unused_join_is_rejected(demo_model) -> None:
    chart = _join_chart(dimensions=["store_id"])
    errors = validate_spec(_spec_of(chart), demo_model)
    assert any("declared but no column of it is used" in e for e in errors)


def test_alias_collision_between_tables_is_rejected(demo_model) -> None:
    # dm.sales_daily has no "name", so collide via two refs with the same bare name
    chart = _join_chart(dimensions=["dm.stores.name", "name"])
    errors = validate_spec(_spec_of(chart), demo_model)
    assert errors  # bare "name" is unknown in the base table AND would collide


# --- analytical transforms (PoP / share / running total) --------------------


def _t_chart(transform, viz=Viz.LINE, **query_kwargs):
    from auto_bi.ir.spec import MeasureTransform  # noqa: F401 (imported for callers)

    defaults = dict(
        table="dm.sales_daily",
        dimensions=["date"],
        measures=[Measure(column="revenue", agg=Aggregation.SUM, transform=transform)],
    )
    defaults.update(query_kwargs)
    return ChartSpec(id="c1", title="t", viz=viz, query=ChartQuery(**defaults))


def test_pop_over_time_dimension_is_valid(demo_model) -> None:
    from auto_bi.ir.spec import MeasureTransform

    assert validate_spec(spec(_t_chart(MeasureTransform.POP_PCT)), demo_model) == []


def test_running_total_over_time_is_valid(demo_model) -> None:
    from auto_bi.ir.spec import MeasureTransform

    assert validate_spec(spec(_t_chart(MeasureTransform.RUNNING_TOTAL)), demo_model) == []


def test_pop_over_non_time_dimension_is_rejected(demo_model) -> None:
    from auto_bi.ir.spec import MeasureTransform

    # store_id is a dimension, not time -> a period-over-period has no order to walk
    bad = _t_chart(MeasureTransform.POP_ABS, viz=Viz.BAR, dimensions=["store_id"])
    errors = validate_spec(spec(bad), demo_model)
    assert any("колонкой времени" in e for e in errors)


def test_share_of_total_over_category_is_valid(demo_model) -> None:
    from auto_bi.ir.spec import MeasureTransform

    # share needs no time order; a categorical axis is fine
    ok = _t_chart(MeasureTransform.SHARE_OF_TOTAL, viz=Viz.PIE, dimensions=["store_id"])
    assert validate_spec(spec(ok), demo_model) == []


def test_share_of_total_without_dimension_is_rejected(demo_model) -> None:
    from auto_bi.ir.spec import MeasureTransform

    # big_number forbids dimensions, so a share there has nothing to be a share of
    bad = _t_chart(MeasureTransform.SHARE_OF_TOTAL, viz=Viz.BIG_NUMBER, dimensions=[])
    errors = validate_spec(spec(bad), demo_model)
    assert any("share_of_total" in e or "не поддерживаются" in e for e in errors)


def test_transform_on_big_number_is_rejected(demo_model) -> None:
    from auto_bi.ir.spec import MeasureTransform

    bad = _t_chart(MeasureTransform.RUNNING_TOTAL, viz=Viz.BIG_NUMBER, dimensions=[])
    errors = validate_spec(spec(bad), demo_model)
    assert any("не поддерживаются" in e and "big_number" in e for e in errors)


def test_transform_on_pivot_is_rejected(demo_model) -> None:
    from auto_bi.ir.spec import MeasureTransform

    bad = ChartSpec(
        id="c1",
        title="t",
        viz=Viz.PIVOT,
        query=ChartQuery(
            table="dm.sales_daily",
            rows=["store_id"],
            columns=["date"],
            measures=[
                Measure(column="revenue", agg=Aggregation.SUM, transform=MeasureTransform.POP_ABS)
            ],
        ),
    )
    errors = validate_spec(spec(bad), demo_model)
    assert any("не поддерживаются" in e for e in errors)


# --- ratio measures (Measure.denominator) -----------------------------------


def _ratio_measure(num="revenue", num_agg=Aggregation.SUM, den="orders", den_agg=Aggregation.SUM):
    return Measure(column=num, agg=num_agg, denominator=Measure(column=den, agg=den_agg))


def test_ratio_over_time_is_valid(demo_model) -> None:
    assert validate_spec(spec(chart(measures=[_ratio_measure()])), demo_model) == []


def test_ratio_on_big_number_is_valid(demo_model) -> None:
    # a ratio needs no ordered axis (unlike a window transform), so a single-KPI ratio is fine
    ok = chart(viz=Viz.BIG_NUMBER, dimensions=[], measures=[_ratio_measure()])
    assert validate_spec(spec(ok), demo_model) == []


def test_ratio_unknown_denominator_column_rejected(demo_model) -> None:
    bad = chart(measures=[_ratio_measure(den="nope_col")])
    errors = validate_spec(spec(bad), demo_model)
    assert any("nope_col" in e and "denominator" in e for e in errors)


def test_ratio_denominator_sum_over_dimension_rejected(demo_model) -> None:
    bad = chart(measures=[_ratio_measure(den="store_id", den_agg=Aggregation.SUM)])
    errors = validate_spec(spec(bad), demo_model)
    assert any("store_id" in e and "count" in e for e in errors)


def test_ratio_denominator_count_over_dimension_allowed(demo_model) -> None:
    ok = chart(measures=[_ratio_measure(den="store_id", den_agg=Aggregation.COUNT_DISTINCT)])
    assert validate_spec(spec(ok), demo_model) == []


def test_ratio_with_transform_rejected(demo_model) -> None:
    from auto_bi.ir.spec import MeasureTransform

    bad = chart(
        measures=[
            Measure(
                column="revenue",
                agg=Aggregation.SUM,
                transform=MeasureTransform.POP_PCT,
                denominator=Measure(column="orders", agg=Aggregation.SUM),
            )
        ]
    )
    errors = validate_spec(spec(bad), demo_model)
    assert any("не может одновременно иметь transform" in e for e in errors)


def test_nested_ratio_rejected(demo_model) -> None:
    bad = chart(
        measures=[
            Measure(
                column="revenue",
                agg=Aggregation.SUM,
                denominator=Measure(
                    column="orders",
                    agg=Aggregation.SUM,
                    denominator=Measure(column="orders", agg=Aggregation.SUM),
                ),
            )
        ]
    )
    errors = validate_spec(spec(bad), demo_model)
    assert any("вложенные отношения" in e for e in errors)


# --- time_grain (truncated time x-axis) -------------------------------------


def test_time_grain_over_time_is_valid(demo_model) -> None:
    from auto_bi.ir.spec import TimeGrain

    assert validate_spec(spec(chart(time_grain=TimeGrain.MONTH)), demo_model) == []


def test_time_grain_on_non_time_first_dim_rejected(demo_model) -> None:
    from auto_bi.ir.spec import TimeGrain

    bad = chart(viz=Viz.BAR, dimensions=["store_id"], time_grain=TimeGrain.MONTH)
    errors = validate_spec(spec(bad), demo_model)
    assert any("time_grain" in e and "колонкой времени" in e for e in errors)


def test_time_grain_on_big_number_without_time_axis_rejected(demo_model) -> None:
    from auto_bi.ir.spec import TimeGrain

    # big_number has no dimensions -> there is no time x-axis to truncate
    bad = chart(viz=Viz.BIG_NUMBER, dimensions=[], time_grain=TimeGrain.YEAR)
    errors = validate_spec(spec(bad), demo_model)
    assert any("time_grain" in e for e in errors)


# --- yoy_pct (year-over-year, needs a grain to size the lag) -----------------


def test_yoy_with_month_grain_is_valid(demo_model) -> None:
    from auto_bi.ir.spec import MeasureTransform, TimeGrain

    ok = _t_chart(MeasureTransform.YOY_PCT, time_grain=TimeGrain.MONTH)
    assert validate_spec(spec(ok), demo_model) == []


def test_yoy_without_grain_is_rejected(demo_model) -> None:
    from auto_bi.ir.spec import MeasureTransform

    bad = _t_chart(MeasureTransform.YOY_PCT)  # no time_grain -> lag size undefined
    errors = validate_spec(spec(bad), demo_model)
    assert any("yoy_pct" in e and "time_grain" in e for e in errors)


def test_yoy_with_day_grain_is_rejected(demo_model) -> None:
    from auto_bi.ir.spec import MeasureTransform, TimeGrain

    bad = _t_chart(MeasureTransform.YOY_PCT, time_grain=TimeGrain.DAY)
    errors = validate_spec(spec(bad), demo_model)
    assert any("yoy_pct" in e and "time_grain" in e for e in errors)


# --- lag_periods (period-over-period offset; only pop_abs/pop_pct) -----------


def _lag_chart(transform, lag_periods, **query_kwargs):
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
    defaults.update(query_kwargs)
    return ChartSpec(id="c1", title="t", viz=Viz.LINE, query=ChartQuery(**defaults))


def test_lag_periods_with_pop_pct_is_valid(demo_model) -> None:
    from auto_bi.ir.spec import MeasureTransform

    assert validate_spec(spec(_lag_chart(MeasureTransform.POP_PCT, 3)), demo_model) == []


def test_lag_periods_with_pop_abs_is_valid(demo_model) -> None:
    from auto_bi.ir.spec import MeasureTransform

    assert validate_spec(spec(_lag_chart(MeasureTransform.POP_ABS, 2)), demo_model) == []


def test_lag_periods_on_yoy_is_rejected(demo_model) -> None:
    from auto_bi.ir.spec import MeasureTransform, TimeGrain

    # yoy derives its own year lag from the grain; an explicit lag_periods is a contradiction
    bad = _lag_chart(MeasureTransform.YOY_PCT, 3, time_grain=TimeGrain.MONTH)
    errors = validate_spec(spec(bad), demo_model)
    assert any("lag_periods" in e and "pop_abs/pop_pct" in e for e in errors)


def test_lag_periods_on_share_is_rejected(demo_model) -> None:
    from auto_bi.ir.spec import MeasureTransform

    # viz lives on ChartSpec, not ChartQuery (extra=forbid); pass only query kwargs
    bad = _lag_chart(MeasureTransform.SHARE_OF_TOTAL, 2, dimensions=["store_id"])
    bad = bad.model_copy(update={"viz": Viz.PIE})
    errors = validate_spec(spec(bad), demo_model)
    assert any("lag_periods" in e and "pop_abs/pop_pct" in e for e in errors)


def test_lag_periods_on_plain_measure_is_rejected(demo_model) -> None:
    # a measure with no transform has no period to lag against
    bad = ChartSpec(
        id="c1",
        title="t",
        viz=Viz.LINE,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["date"],
            measures=[Measure(column="revenue", agg=Aggregation.SUM, lag_periods=2)],
        ),
    )
    errors = validate_spec(spec(bad), demo_model)
    assert any("lag_periods" in e and "обычной меры" in e for e in errors)


# --- running_share (Pareto / ABC cumulative share; ranked by measure, no time axis) ---------


def test_running_share_over_category_is_valid(demo_model) -> None:
    from auto_bi.ir.spec import MeasureTransform

    # ranked by the measure, not time -> a categorical bar is fine, no time axis required
    ok = _t_chart(MeasureTransform.RUNNING_SHARE, viz=Viz.BAR, dimensions=["store_id"])
    assert validate_spec(spec(ok), demo_model) == []


def test_running_share_without_dimension_is_rejected(demo_model) -> None:
    from auto_bi.ir.spec import MeasureTransform

    bad = _t_chart(MeasureTransform.RUNNING_SHARE, viz=Viz.BIG_NUMBER, dimensions=[])
    errors = validate_spec(spec(bad), demo_model)
    assert any("running_share" in e or "не поддерживаются" in e for e in errors)


def test_running_share_does_not_require_time_axis(demo_model) -> None:
    from auto_bi.ir.spec import MeasureTransform

    # a non-time first dimension must NOT trigger the time-axis error (unlike pop/yoy/running_total)
    ok = _t_chart(MeasureTransform.RUNNING_SHARE, viz=Viz.BAR, dimensions=["store_id"])
    errors = validate_spec(spec(ok), demo_model)
    assert not any("колонкой времени" in e for e in errors)


# --- histogram (equal-width binning of a numeric measure column) ------------------------------


def _hist_chart(bins=5, dim="revenue", agg=Aggregation.COUNT, viz=Viz.HISTOGRAM, **q):
    defaults = dict(
        table="dm.sales_daily",
        dimensions=[dim],
        measures=[Measure(column="revenue", agg=agg)],
        bins=bins,
    )
    defaults.update(q)
    return ChartSpec(id="c1", title="t", viz=viz, query=ChartQuery(**defaults))


def test_histogram_over_numeric_measure_is_valid(demo_model) -> None:
    # revenue is a numeric measure column in dm.sales_daily -> a histogram of it is valid
    assert validate_spec(spec(_hist_chart()), demo_model) == []


def test_histogram_without_bins_is_rejected(demo_model) -> None:
    bad = _hist_chart(bins=None)
    errors = validate_spec(spec(bad), demo_model)
    assert any("histogram требует bins" in e for e in errors)


def test_bins_without_histogram_viz_is_rejected(demo_model) -> None:
    bad = _hist_chart(viz=Viz.BAR)
    errors = validate_spec(spec(bad), demo_model)
    assert any("bins задаётся только для viz=histogram" in e for e in errors)


def test_histogram_over_non_measure_dimension_is_rejected(demo_model) -> None:
    # store_id is a role=dimension column -> not a numeric quantity to bin
    bad = _hist_chart(dim="store_id")
    errors = validate_spec(spec(bad), demo_model)
    assert any("role=measure" in e for e in errors)


def test_histogram_non_count_measure_is_rejected(demo_model) -> None:
    bad = _hist_chart(agg=Aggregation.SUM)
    errors = validate_spec(spec(bad), demo_model)
    assert any("простым count" in e for e in errors)


def test_histogram_two_dimensions_is_rejected(demo_model) -> None:
    bad = _hist_chart(dimensions=["price", "category"])
    errors = validate_spec(spec(bad), demo_model)
    assert any("exactly one dimension" in e for e in errors)


def test_histogram_with_join_is_rejected(demo_model) -> None:
    from auto_bi.ir.spec import JoinSpec

    # the generator bins a base-table column and never emits query.joins, so a join would
    # silently produce broken SQL — reject at validation with a clear hint (audit LOW)
    bad = _hist_chart(
        joins=[
            JoinSpec(table="dm.stores", on_left="dm.sales_daily.store_id", on_right="dm.stores.id")
        ]
    )
    errors = validate_spec(spec(bad), demo_model)
    assert any("histogram не поддерживает join" in e for e in errors)


# --- measure alias uniqueness (two measures must not share a SELECT alias) ---------------------


def test_measures_colliding_by_alias_rejected(demo_model) -> None:
    # two unlabeled sum(revenue) both resolve to "sum_revenue" -> a duplicate SELECT alias ->
    # a ClickHouse "duplicate alias" at EXPLAIN; reject early like the dimension check (audit LOW)
    bad = chart(
        measures=[
            Measure(column="revenue", agg=Aggregation.SUM),
            Measure(column="revenue", agg=Aggregation.SUM),
        ]
    )
    errors = validate_spec(spec(bad), demo_model)
    assert any("collide by alias" in e for e in errors)


def test_distinct_measure_aliases_ok(demo_model) -> None:
    # the check must not false-positive on two measures with different aliases
    ok = chart(
        measures=[
            Measure(column="revenue", agg=Aggregation.SUM),
            Measure(column="orders", agg=Aggregation.SUM),
        ]
    )
    assert validate_spec(spec(ok), demo_model) == []


def test_measure_alias_colliding_with_dimension_rejected(demo_model) -> None:
    # a measure label equal to a dimension's bare name emits two SELECT columns under one
    # alias; which one the BI's aggregate reads is undefined -> silently wrong numbers
    # (audit 2026-07-19 finding 2)
    bad = chart(
        dimensions=["store_id"],
        measures=[Measure(column="revenue", agg=Aggregation.SUM, label="store_id")],
    )
    errors = validate_spec(spec(bad), demo_model)
    assert any("measure alias collides with dimension column" in e for e in errors)


def test_measure_over_dimension_column_without_label_ok(demo_model) -> None:
    # no false positive on the same column used both ways: the unlabeled measure's default
    # alias carries the agg prefix (count_distinct_store_id), so it never shadows the dimension
    ok = chart(
        dimensions=["store_id"],
        measures=[Measure(column="store_id", agg=Aggregation.COUNT_DISTINCT)],
    )
    assert validate_spec(spec(ok), demo_model) == []


# --- scalar period-compare KPI (Measure.compare) ---------------------------


def _cmp_chart(viz=Viz.BIG_NUMBER, **compare_kwargs):
    from auto_bi.ir.spec import ScalarCompare

    ck = dict(column="date", grain="month", kind="yoy", output="pct")
    ck.update(compare_kwargs)
    m = Measure(column="revenue", agg=Aggregation.SUM, compare=ScalarCompare(**ck))
    return ChartSpec(
        id="c1", title="t", viz=viz, query=ChartQuery(table="dm.sales_daily", measures=[m])
    )


def test_compare_on_big_number_is_valid(demo_model) -> None:
    assert validate_spec(spec(_cmp_chart()), demo_model) == []


def test_compare_abs_on_big_number_is_valid(demo_model) -> None:
    assert validate_spec(spec(_cmp_chart(output="abs")), demo_model) == []


def test_compare_pop_on_big_number_is_valid(demo_model) -> None:
    assert validate_spec(spec(_cmp_chart(kind="pop")), demo_model) == []


def test_compare_on_non_big_number_is_rejected(demo_model) -> None:
    # a scalar compare needs a scalar tile; a line has a time axis and would use yoy_pct instead
    bad = _cmp_chart(viz=Viz.LINE)
    errors = validate_spec(spec(bad), demo_model)
    assert any("только для big_number" in e for e in errors)


def test_compare_day_grain_is_rejected(demo_model) -> None:
    errors = validate_spec(spec(_cmp_chart(grain="day")), demo_model)
    assert any("не day" in e for e in errors)


def test_compare_over_non_time_column_is_rejected(demo_model) -> None:
    errors = validate_spec(spec(_cmp_chart(column="store_id")), demo_model)
    assert any("колонкой времени" in e for e in errors)


def test_compare_over_unknown_column_is_rejected(demo_model) -> None:
    errors = validate_spec(spec(_cmp_chart(column="nope_col")), demo_model)
    assert any("неизвестная колонка" in e for e in errors)


def test_compare_with_transform_is_mutually_exclusive(demo_model) -> None:
    from auto_bi.ir.spec import MeasureTransform, ScalarCompare

    m = Measure(
        column="revenue",
        agg=Aggregation.SUM,
        transform=MeasureTransform.YOY_PCT,
        compare=ScalarCompare(column="date", grain="month"),
    )
    bad = ChartSpec(
        id="c1",
        title="t",
        viz=Viz.BIG_NUMBER,
        query=ChartQuery(table="dm.sales_daily", measures=[m]),
    )
    errors = validate_spec(spec(bad), demo_model)
    assert any("не может одновременно" in e for e in errors)


def test_validate_repair_loop_error_contract(demo_model) -> None:
    """Exact repair-loop errors are a stable behavioral contract, including their order."""
    from auto_bi.ir.spec import (
        FilterOp,
        JoinSpec,
        MeasureTransform,
        QueryFilter,
        TimeGrain,
    )
    from auto_bi.ir.validate import _validate_chart

    # Dashboard-level validation and the qualification hint.
    assert validate_spec(spec(chart(), filters=[DashboardFilter(column="date")]), demo_model) == [
        "dashboard filter references unknown column: 'date' — укажи полное имя: "
        "'dm.sales_daily.date'"
    ]
    assert validate_spec(
        spec(chart(), filters=[DashboardFilter(column="dm.sales_daily.nope")]),
        demo_model,
    ) == ["dashboard filter references unknown column: 'dm.sales_daily.nope'"]
    assert validate_spec(spec(chart(), chart()), demo_model) == [
        "chart ids are not unique: ['c1', 'c1']"
    ]

    # Table/column resolution and actionable repair hints.
    assert validate_spec(spec(chart(table="dm.nope")), demo_model) == [
        "chart 'c1': unknown table 'dm.nope' (known tables: dm.sales_daily, dm.stores)"
    ]
    assert validate_spec(
        spec(
            chart(
                dimensions=["nope_dim"],
                measures=[Measure(column="nope_measure", agg=Aggregation.SUM)],
            )
        ),
        demo_model,
    ) == [
        "chart 'c1': unknown dimension column 'nope_dim' in dm.sales_daily",
        "chart 'c1': unknown measure column 'nope_measure' in dm.sales_daily",
    ]
    assert validate_spec(spec(chart(dimensions=["city"])), demo_model) == [
        "chart 'c1': unknown dimension column 'city' in dm.sales_daily — колонка есть в "
        "dm.stores: укажи 'dm.stores.city' и добавь соответствующий JOIN в query.joins"
    ]
    assert validate_spec(spec(chart(dimensions=["dm.stores.city"])), demo_model) == [
        "chart 'c1': dimension column 'dm.stores.city' references table 'dm.stores' "
        "without a matching entry in query.joins"
    ]
    assert validate_spec(spec(chart(dimensions=["dm.sales_daily.date"])), demo_model) == [
        "chart 'c1': unknown dimension column 'dm.sales_daily.date' in dm.sales_daily — "
        "укажи имя без префикса таблицы: 'date'"
    ]

    # Every join failure mode is distinct and must retain its exact repair text.
    unknown_join = JoinSpec(
        table="dm.nope",
        on_left="dm.sales_daily.store_id",
        on_right="dm.nope.id",
    )
    assert validate_spec(spec(chart(joins=[unknown_join])), demo_model) == [
        "chart 'c1': join references unknown table 'dm.nope'"
    ]
    bad_left = JoinSpec(
        table="dm.stores",
        on_left="dm.sales_daily.nope",
        on_right="dm.stores.id",
    )
    assert validate_spec(spec(chart(joins=[bad_left])), demo_model) == [
        "chart 'c1': join on_left 'dm.sales_daily.nope' must be a column of the chart's "
        "table dm.sales_daily"
    ]
    bad_right = JoinSpec(
        table="dm.stores",
        on_left="dm.sales_daily.store_id",
        on_right="dm.stores.nope",
    )
    assert validate_spec(spec(chart(joins=[bad_right])), demo_model) == [
        "chart 'c1': join on_right 'dm.stores.nope' must be a column of dm.stores"
    ]
    non_edge = JoinSpec(
        table="dm.stores",
        on_left="dm.sales_daily.orders",
        on_right="dm.stores.id",
    )
    assert validate_spec(spec(chart(joins=[non_edge])), demo_model) == [
        "chart 'c1': join dm.sales_daily.orders = dm.stores.id is not an edge of the "
        "semantic model (допустимые джойны: dm.sales_daily.store_id = dm.stores.id)"
    ]
    assert validate_spec(spec(_join_chart(dimensions=["store_id"])), demo_model) == [
        "chart 'j': join to dm.stores is declared but no column of it is used"
    ]

    # Alias, measure, filter, and ordering contracts.
    duplicate_measures = chart(
        measures=[
            Measure(column="revenue", agg=Aggregation.SUM),
            Measure(column="revenue", agg=Aggregation.SUM),
        ]
    )
    assert validate_spec(spec(duplicate_measures), demo_model) == [
        "chart 'c1': measures collide by alias ['sum_revenue'] — две меры дают одинаковый "
        "SELECT-алиас (задайте label одной из них)"
    ]
    cross_alias = chart(
        dimensions=["store_id"],
        measures=[Measure(column="revenue", agg=Aggregation.SUM, label="store_id")],
    )
    assert validate_spec(spec(cross_alias), demo_model) == [
        "chart 'c1': measure alias collides with dimension column ['store_id'] — мера и "
        "размерность дают одинаковый SELECT-алиас (задайте мере другой label)"
    ]
    assert validate_spec(
        spec(chart(measures=[Measure(column="date", agg=Aggregation.MAX)])), demo_model
    ) == ["chart 'c1': time column 'date' cannot be a measure"]
    assert validate_spec(
        spec(chart(measures=[Measure(column="store_id", agg=Aggregation.SUM)])),
        demo_model,
    ) == [
        "chart 'c1': sum over dimension column 'store_id' — для неё допустимы только "
        "count/count_distinct"
    ]
    assert validate_spec(
        spec(chart(filters=[QueryFilter(column="store_id", op=FilterOp.IN, value=[])])),
        demo_model,
    ) == ["chart 'c1': filter on 'store_id' uses IN with an empty value list"]
    assert validate_spec(spec(chart(order_by=[OrderBy(by="unknown", dir="desc")])), demo_model) == [
        "chart 'c1': order_by 'unknown' is neither a dimension nor a measure of the chart"
    ]

    # The public and private defaults both keep the operator raw-SQL path enabled.
    raw_sql = "SELECT store_id FROM dm.sales_daily"

    def raw_chart(viz: Viz = Viz.TABLE, **query_overrides) -> ChartSpec:
        query = dict(table="dm.sales_daily", dimensions=["store_id"], raw_sql=raw_sql)
        query.update(query_overrides)
        return ChartSpec(
            id="raw",
            title="Raw",
            viz=viz,
            query=ChartQuery(**query),
        )

    raw = raw_chart()
    raw_spec = spec(raw)
    assert validate_spec(raw_spec, demo_model) == []
    assert _validate_chart(raw, demo_model, spec=raw_spec) == []
    assert validate_spec(raw_spec, demo_model, allow_raw_sql=False) == [
        "chart 'raw': raw_sql is an operator-only hatch (CLI `auto_bi raw`); "
        "LLM/text/fields paths must use IR measures and dimensions only"
    ]
    assert validate_spec(spec(raw_chart(viz=Viz.BAR)), demo_model) == [
        "chart 'raw': raw_sql is supported only with viz=table, got bar"
    ]
    assert validate_spec(
        spec(raw, target_bi=TargetBI.DATALENS),
        demo_model,
    ) == ["chart 'raw': raw_sql is supported only with target_bi=superset, got 'datalens'"]
    assert validate_spec(
        spec(
            raw_chart(
                dimensions=[],
                measures=[Measure(column="revenue", agg=Aggregation.SUM)],
            )
        ),
        demo_model,
    ) == [
        "chart 'raw': raw_sql cannot be combined with IR query fields ['measures'] "
        "(a raw chart carries its whole query in the SQL; only `dimensions` — the display "
        "columns — may accompany it)"
    ]
    assert validate_spec(
        spec(
            raw_chart(
                dimensions=[],
                measures=[Measure(column="revenue", agg=Aggregation.SUM)],
                series=["store_id"],
                time_grain=TimeGrain.MONTH,
                bins=5,
            )
        ),
        demo_model,
    ) == [
        "chart 'raw': raw_sql cannot be combined with IR query fields "
        "['bins', 'measures', 'series', 'time_grain'] (a raw chart carries its whole query "
        "in the SQL; only `dimensions` — the display columns — may accompany it)"
    ]
    assert validate_spec(
        spec(raw_chart(dimensions=[], raw_sql="DELETE FROM dm.sales_daily")),
        demo_model,
    ) == ["chart 'raw': raw_sql is not a single plain SELECT: only SELECT is allowed, got Delete"]

    # Transform, ratio, and compare errors are ordered repair-loop output.
    assert validate_spec(
        spec(_t_chart(MeasureTransform.RUNNING_TOTAL, viz=Viz.BIG_NUMBER, dimensions=[])),
        demo_model,
    ) == [
        "chart 'c1': преобразования мер (running_total) не поддерживаются для big_number — "
        "нужен график с одной упорядоченной осью (line/area/bar/pie/table)"
    ]
    assert validate_spec(
        spec(_t_chart(MeasureTransform.POP_ABS, viz=Viz.BAR, dimensions=["store_id"])),
        demo_model,
    ) == [
        "chart 'c1': преобразование 'pop_abs' требует, чтобы первое измерение было колонкой "
        "времени (ось x по времени)"
    ]
    assert validate_spec(spec(_t_chart(MeasureTransform.YOY_PCT)), demo_model) == [
        "chart 'c1': преобразование 'yoy_pct' требует time_grain "
        "(week/month/quarter/year), чтобы определить сдвиг на год"
    ]
    plain_lag = ChartSpec(
        id="c1",
        title="t",
        viz=Viz.LINE,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["date"],
            measures=[Measure(column="revenue", agg=Aggregation.SUM, lag_periods=2)],
        ),
    )
    assert validate_spec(spec(plain_lag), demo_model) == [
        "chart 'c1': lag_periods применим только к pop_abs/pop_pct, не к обычной меры "
        "(без transform)"
    ]

    bad_ratio = Measure(
        column="orders",
        agg=Aggregation.SUM,
        label="ratio",
        transform=MeasureTransform.POP_PCT,
        denominator=Measure(column="revenue", agg=Aggregation.SUM),
    )
    ratio_after_plain = chart(
        measures=[
            Measure(column="revenue", agg=Aggregation.SUM, label="plain"),
            bad_ratio,
        ]
    )
    assert validate_spec(spec(ratio_after_plain), demo_model) == [
        "chart 'c1': мера-отношение не может одновременно иметь transform (pop_pct) "
        "и denominator"
    ]
    assert validate_spec(
        spec(
            chart(
                measures=[
                    Measure(
                        column="revenue",
                        agg=Aggregation.SUM,
                        denominator=Measure(
                            column="orders",
                            agg=Aggregation.SUM,
                            denominator=Measure(column="orders", agg=Aggregation.SUM),
                        ),
                    )
                ]
            )
        ),
        demo_model,
    ) == ["chart 'c1': вложенные отношения не поддерживаются (denominator у denominator)"]

    assert validate_spec(spec(_cmp_chart(viz=Viz.LINE)), demo_model) == [
        "chart 'c1': сравнение периодов (compare) поддерживается только для big_number "
        "(получено line)",
        "chart 'c1': line needs at least one dimension (x-axis)",
    ]
    assert validate_spec(spec(_cmp_chart(grain="day")), demo_model) == [
        "chart 'c1': compare.grain должен задавать период (week/month/quarter/year), не day"
    ]
    assert validate_spec(spec(_cmp_chart(column="store_id")), demo_model) == [
        "chart 'c1': compare.column 'store_id' должна быть колонкой времени (role=time)"
    ]
    assert validate_spec(spec(_cmp_chart(column="nope_col")), demo_model) == [
        "chart 'c1': compare.column 'nope_col' — неизвестная колонка"
    ]

    # Time-axis and histogram contracts, including multi-error ordering.
    assert validate_spec(
        spec(chart(viz=Viz.BAR, dimensions=["store_id"], time_grain=TimeGrain.MONTH)),
        demo_model,
    ) == [
        "chart 'c1': time_grain (month) требует, чтобы первое измерение было колонкой "
        "времени (ось x по времени)"
    ]
    assert validate_spec(spec(_hist_chart(bins=None)), demo_model) == [
        "chart 'c1': histogram требует bins (число корзин)"
    ]
    assert validate_spec(spec(_hist_chart(viz=Viz.BAR)), demo_model) == [
        "chart 'c1': bins задаётся только для viz=histogram (получено bar)"
    ]
    assert validate_spec(spec(_hist_chart(dim="store_id")), demo_model) == [
        "chart 'c1': histogram бинирует числовую меру — измерение 'store_id' имеет роль "
        "dimension, нужна колонка role=measure (количественная)"
    ]
    assert validate_spec(spec(_hist_chart(agg=Aggregation.SUM)), demo_model) == [
        "chart 'c1': мера гистограммы должна быть простым count (число строк в корзине), "
        "без transform/denominator"
    ]
    assert validate_spec(
        spec(_hist_chart(time_grain=TimeGrain.MONTH)),
        demo_model,
    ) == [
        "chart 'c1': time_grain (month) требует, чтобы первое измерение было колонкой "
        "времени (ось x по времени)",
        "chart 'c1': histogram несовместима с time_grain",
    ]
    assert validate_spec(
        spec(
            _hist_chart(
                joins=[
                    JoinSpec(
                        table="dm.stores",
                        on_left="dm.sales_daily.store_id",
                        on_right="dm.stores.id",
                    )
                ]
            )
        ),
        demo_model,
    ) == [
        "chart 'c1': join to dm.stores is declared but no column of it is used",
        "chart 'c1': histogram не поддерживает join — бинирование идёт по колонке базовой "
        "таблицы (вынесите join-измерение в отдельный чарт)",
    ]
    assert validate_spec(spec(_hist_chart(dimensions=[])), demo_model) == [
        "chart 'c1': histogram needs exactly one dimension to bin (got 0)"
    ]

    # Compact viz-shape matrix with exact role names and cardinalities.
    assert validate_spec(spec(chart(viz=Viz.BIG_NUMBER)), demo_model) == [
        "chart 'c1': big_number must not set dimensions (got ['date'])"
    ]
    assert validate_spec(spec(chart(viz=Viz.LINE, dimensions=[])), demo_model) == [
        "chart 'c1': line needs at least one dimension (x-axis)"
    ]
    assert validate_spec(
        spec(chart(viz=Viz.LINE, rows=["store_id"])),
        demo_model,
    ) == ["chart 'c1': line must not set rows (got ['store_id'])"]
    assert validate_spec(
        spec(chart(viz=Viz.PIE, dimensions=["store_id", "product_id"])),
        demo_model,
    ) == ["chart 'c1': pie needs exactly one dimension (got 2)"]
    assert validate_spec(
        spec(chart(viz=Viz.PIVOT, dimensions=[], rows=[])),
        demo_model,
    ) == ["chart 'c1': pivot needs at least one row dimension"]
    assert validate_spec(
        spec(chart(viz=Viz.HEATMAP, dimensions=["date"])),
        demo_model,
    ) == ["chart 'c1': heatmap needs exactly two dimensions x,y (got 1)"]

    # The chart loop must keep validating after the first invalid chart.
    first = chart(table="dm.first")
    second = chart(table="dm.second").model_copy(update={"id": "c2"})
    assert validate_spec(spec(first, second), demo_model) == [
        "chart 'c1': unknown table 'dm.first' (known tables: dm.sales_daily, dm.stores)",
        "chart 'c2': unknown table 'dm.second' (known tables: dm.sales_daily, dm.stores)",
    ]


def test_validate_branch_and_shape_contract(demo_model) -> None:
    """Exercise multi-item, qualified-reference, and forbidden-role branches exactly."""
    from auto_bi.ir.spec import (
        FilterOp,
        JoinSpec,
        MeasureTransform,
        QueryFilter,
        ScalarCompare,
        TimeGrain,
    )
    from auto_bi.semantic.model import Additivity, Join

    valid_join = JoinSpec(
        table="dm.stores",
        on_left="dm.sales_daily.store_id",
        on_right="dm.stores.id",
    )
    unknown_join = JoinSpec(
        table="dm.nope",
        on_left="dm.sales_daily.store_id",
        on_right="dm.nope.id",
    )
    bad_left = JoinSpec(
        table="dm.stores",
        on_left="dm.sales_daily.nope",
        on_right="dm.stores.id",
    )
    non_edge = JoinSpec(
        table="dm.stores",
        on_left="dm.sales_daily.orders",
        on_right="dm.stores.id",
    )

    # Loops must continue after an invalid item, and edge-list rendering is deterministic.
    assert validate_spec(spec(chart(joins=[unknown_join, bad_left])), demo_model) == [
        "chart 'c1': join references unknown table 'dm.nope'",
        "chart 'c1': join on_left 'dm.sales_daily.nope' must be a column of the chart's "
        "table dm.sales_daily",
    ]
    two_edge_model = demo_model.model_copy(
        update={
            "joins": [
                *demo_model.joins,
                Join(left="dm.sales_daily.product_id", right="dm.stores.id"),
            ]
        }
    )
    assert validate_spec(spec(chart(joins=[non_edge])), two_edge_model) == [
        "chart 'c1': join dm.sales_daily.orders = dm.stores.id is not an edge of the "
        "semantic model (допустимые джойны: dm.sales_daily.store_id = dm.stores.id; "
        "dm.sales_daily.product_id = dm.stores.id)"
    ]
    no_edge_model = demo_model.model_copy(update={"joins": []})
    assert validate_spec(spec(chart(joins=[non_edge])), no_edge_model) == [
        "chart 'c1': join dm.sales_daily.orders = dm.stores.id is not an edge of the "
        "semantic model (допустимые джойны: нет)"
    ]
    assert validate_spec(
        spec(chart(dimensions=["dm.stores.nope"], joins=[valid_join])),
        demo_model,
    ) == ["chart 'c1': unknown dimension column 'dm.stores.nope' in dm.stores"]
    assert validate_spec(
        spec(
            chart(
                viz=Viz.PIVOT,
                dimensions=[],
                rows=["nope_row"],
                columns=["nope_col"],
            )
        ),
        demo_model,
    ) == [
        "chart 'c1': unknown pivot row column 'nope_row' in dm.sales_daily",
        "chart 'c1': unknown pivot column column 'nope_col' in dm.sales_daily",
    ]

    # Duplicate sets contain one repeated and one unique alias.
    dim_collision = _join_chart(dimensions=["dm.stores.id", "id", "store_id"])
    assert validate_spec(spec(dim_collision), demo_model) == [
        "chart 'j': unknown dimension column 'id' in dm.sales_daily — колонка есть в "
        "dm.stores: укажи 'dm.stores.id' и добавь соответствующий JOIN в query.joins",
        "chart 'j': dimension columns collide by bare name ['id'] — одинаковые имена из "
        "разных таблиц в одном чарте не поддерживаются",
    ]
    measure_collision = chart(
        measures=[
            Measure(column="revenue", agg=Aggregation.SUM),
            Measure(column="revenue", agg=Aggregation.SUM),
            Measure(column="orders", agg=Aggregation.SUM),
        ]
    )
    assert validate_spec(spec(measure_collision), demo_model) == [
        "chart 'c1': measures collide by alias ['sum_revenue'] — две меры дают одинаковый "
        "SELECT-алиас (задайте label одной из них)"
    ]

    # A joined table used only by a filter is not an orphaned join.
    joined_filter = _join_chart(
        dimensions=["store_id"],
        filters=[
            QueryFilter(
                column="dm.stores.city",
                op=FilterOp.EQ,
                value="Москва",
            )
        ],
    )
    assert validate_spec(spec(joined_filter), demo_model) == []

    # Non-additive governance, denominator labels, filter labels, and orderable columns.
    sales = demo_model.table("dm.sales_daily")
    assert sales is not None
    non_additive_sales = sales.model_copy(
        update={
            "columns": [
                (
                    column.model_copy(update={"additivity": Additivity.NON_ADDITIVE})
                    if column.name == "revenue"
                    else column
                )
                for column in sales.columns
            ]
        }
    )
    non_additive_model = demo_model.model_copy(
        update={
            "tables": [
                non_additive_sales if table.name == sales.name else table
                for table in demo_model.tables
            ]
        }
    )
    assert validate_spec(spec(chart()), non_additive_model) == [
        "chart 'c1': sum над неаддитивной колонкой 'revenue' (rate/ratio) бессмыслен — "
        "используйте avg или ratio из numerator/denominator"
    ]
    assert validate_spec(
        spec(chart(measures=[_ratio_measure(den="nope_col")])),
        demo_model,
    ) == ["chart 'c1': unknown denominator column 'nope_col' in dm.sales_daily"]
    assert validate_spec(
        spec(
            chart(
                filters=[
                    QueryFilter(
                        column="nope_col",
                        op=FilterOp.EQ,
                        value="x",
                    )
                ]
            )
        ),
        demo_model,
    ) == ["chart 'c1': unknown filter column 'nope_col' in dm.sales_daily"]
    assert (
        validate_spec(
            spec(
                chart(
                    filters=[
                        QueryFilter(
                            column="store_id",
                            op=FilterOp.EQ,
                            value=[],
                        )
                    ]
                )
            ),
            demo_model,
        )
        == []
    )
    assert (
        validate_spec(
            spec(chart(order_by=[OrderBy(by="revenue", dir="desc")])),
            demo_model,
        )
        == []
    )

    # Qualified time references must be split at the final dot.
    assert validate_spec(
        spec(
            chart(
                dimensions=["dm.sales_daily.date"],
                time_grain=TimeGrain.MONTH,
            )
        ),
        demo_model,
    ) == [
        "chart 'c1': unknown dimension column 'dm.sales_daily.date' in dm.sales_daily — "
        "укажи имя без префикса таблицы: 'date'"
    ]
    assert validate_spec(
        spec(chart(dimensions=["dm.sales_daily.nope"])),
        demo_model,
    ) == ["chart 'c1': unknown dimension column 'dm.sales_daily.nope' in dm.sales_daily"]

    # Multiple transform names, no-dimension share, and denominator transforms.
    unsupported_transforms = chart(
        viz=Viz.BIG_NUMBER,
        dimensions=[],
        measures=[
            Measure(
                column="revenue",
                agg=Aggregation.SUM,
                label="rt",
                transform=MeasureTransform.RUNNING_TOTAL,
            ),
            Measure(
                column="orders",
                agg=Aggregation.SUM,
                label="share",
                transform=MeasureTransform.SHARE_OF_TOTAL,
            ),
        ],
    )
    assert validate_spec(spec(unsupported_transforms), demo_model) == [
        "chart 'c1': преобразования мер (running_total, share_of_total) не поддерживаются "
        "для big_number — нужен график с одной упорядоченной осью "
        "(line/area/bar/pie/table)",
        "chart 'c1': big_number needs exactly one measure (got 2)",
    ]
    share_without_dimension = chart(
        viz=Viz.TABLE,
        dimensions=[],
        measures=[
            Measure(
                column="revenue",
                agg=Aggregation.SUM,
                transform=MeasureTransform.SHARE_OF_TOTAL,
            )
        ],
    )
    assert validate_spec(spec(share_without_dimension), demo_model) == [
        "chart 'c1': преобразование 'share_of_total' требует хотя бы одно измерение"
    ]
    denominator_transform = Measure(
        column="revenue",
        agg=Aggregation.SUM,
        denominator=Measure(
            column="orders",
            agg=Aggregation.SUM,
            transform=MeasureTransform.POP_PCT,
        ),
    )
    assert validate_spec(
        spec(chart(measures=[denominator_transform])),
        demo_model,
    ) == ["chart 'c1': знаменатель отношения не может иметь transform"]

    compare_ratio = Measure(
        column="revenue",
        agg=Aggregation.SUM,
        denominator=Measure(column="orders", agg=Aggregation.SUM),
        compare=ScalarCompare(column="date", grain=TimeGrain.MONTH),
    )
    assert validate_spec(
        spec(
            ChartSpec(
                id="c1",
                title="t",
                viz=Viz.BIG_NUMBER,
                query=ChartQuery(table="dm.sales_daily", measures=[compare_ratio]),
            )
        ),
        demo_model,
    ) == [
        "chart 'c1': мера со сравнением периодов (compare) не может одновременно иметь "
        "transform или denominator"
    ]
    assert (
        validate_spec(
            spec(_cmp_chart(column="dm.sales_daily.date")),
            demo_model,
        )
        == []
    )
    assert validate_spec(spec(_cmp_chart(column="dm.nope.date")), demo_model) == [
        "chart 'c1': compare.column 'dm.nope.date' — неизвестная колонка"
    ]

    # Raw populated-field labels are all part of the operator repair contract.
    raw_all_fields = ChartSpec(
        id="raw",
        title="Raw",
        viz=Viz.TABLE,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["display"],
            measures=[Measure(column="revenue", agg=Aggregation.SUM)],
            series=["series"],
            rows=["row"],
            columns=["column"],
            joins=[valid_join],
            filters=[
                QueryFilter(
                    column="store_id",
                    op=FilterOp.EQ,
                    value=1,
                )
            ],
            order_by=[OrderBy(by="display", dir="asc")],
            time_grain=TimeGrain.MONTH,
            bins=5,
            raw_sql="SELECT 1 AS display",
        ),
    )
    assert validate_spec(spec(raw_all_fields), demo_model) == [
        "chart 'raw': raw_sql cannot be combined with IR query fields "
        "['bins', 'columns', 'filters', 'joins', 'measures', 'order_by', 'rows', 'series', "
        "'time_grain'] (a raw chart carries its whole query in the SQL; only `dimensions` — "
        "the display columns — may accompany it)"
    ]

    # Histogram qualified-column parsing and each invalid measure component.
    qualified_histogram = _hist_chart(dim="dm.sales_daily.store_id")
    assert validate_spec(spec(qualified_histogram), demo_model) == [
        "chart 'c1': unknown dimension column 'dm.sales_daily.store_id' in dm.sales_daily — "
        "укажи имя без префикса таблицы: 'store_id'",
        "chart 'c1': histogram бинирует числовую меру — измерение "
        "'dm.sales_daily.store_id' имеет роль dimension, нужна колонка role=measure "
        "(количественная)",
    ]
    histogram_transform = _hist_chart(
        measures=[
            Measure(
                column="revenue",
                agg=Aggregation.COUNT,
                transform=MeasureTransform.SHARE_OF_TOTAL,
            )
        ]
    )
    assert validate_spec(spec(histogram_transform), demo_model) == [
        "chart 'c1': мера гистограммы должна быть простым count (число строк в корзине), "
        "без transform/denominator"
    ]
    histogram_ratio = _hist_chart(
        measures=[
            Measure(
                column="revenue",
                agg=Aggregation.COUNT,
                denominator=Measure(column="orders", agg=Aggregation.COUNT),
            )
        ]
    )
    assert validate_spec(spec(histogram_ratio), demo_model) == [
        "chart 'c1': мера гистограммы должна быть простым count (число строк в корзине), "
        "без transform/denominator"
    ]

    # Viz-shape contracts cover every forbidden role and cardinality branch.
    big_roles = chart(
        viz=Viz.BIG_NUMBER,
        dimensions=[],
        series=["store_id"],
        rows=["product_id"],
        columns=["date"],
    )
    assert validate_spec(spec(big_roles), demo_model) == [
        "chart 'c1': big_number must not set series (got ['store_id'])",
        "chart 'c1': big_number must not set rows (got ['product_id'])",
        "chart 'c1': big_number must not set columns (got ['date'])",
    ]
    big_two_measures = chart(
        viz=Viz.BIG_NUMBER,
        dimensions=[],
        measures=[
            Measure(column="revenue", agg=Aggregation.SUM, label="revenue_sum"),
            Measure(column="orders", agg=Aggregation.SUM, label="orders_sum"),
        ],
    )
    assert validate_spec(spec(big_two_measures), demo_model) == [
        "chart 'c1': big_number needs exactly one measure (got 2)"
    ]
    assert validate_spec(
        spec(chart(viz=Viz.LINE, columns=["store_id"])),
        demo_model,
    ) == ["chart 'c1': line must not set columns (got ['store_id'])"]

    pie_two_measures = chart(
        viz=Viz.PIE,
        dimensions=["store_id"],
        measures=[
            Measure(column="revenue", agg=Aggregation.SUM, label="revenue_sum"),
            Measure(column="orders", agg=Aggregation.SUM, label="orders_sum"),
        ],
    )
    assert validate_spec(spec(pie_two_measures), demo_model) == [
        "chart 'c1': pie needs exactly one measure (got 2)"
    ]
    pie_roles = chart(
        viz=Viz.PIE,
        dimensions=["store_id"],
        series=["product_id"],
        rows=["date"],
        columns=["revenue"],
    )
    assert validate_spec(spec(pie_roles), demo_model) == [
        "chart 'c1': pie must not set series (got ['product_id'])",
        "chart 'c1': pie must not set rows (got ['date'])",
        "chart 'c1': pie must not set columns (got ['revenue'])",
    ]

    table_template = chart(viz=Viz.TABLE)
    empty_table = table_template.model_copy(
        update={
            "query": table_template.query.model_copy(
                update={
                    "dimensions": [],
                    "measures": [],
                }
            )
        }
    )
    assert validate_spec(spec(empty_table), demo_model) == [
        "chart 'c1': table needs at least one dimension or measure"
    ]
    dimensions_only_table = table_template.model_copy(
        update={
            "query": table_template.query.model_copy(
                update={
                    "measures": [],
                }
            )
        }
    )
    assert validate_spec(spec(dimensions_only_table), demo_model) == []
    assert (
        validate_spec(
            spec(chart(viz=Viz.TABLE, dimensions=[])),
            demo_model,
        )
        == []
    )
    table_roles = chart(
        viz=Viz.TABLE,
        series=["store_id"],
        rows=["product_id"],
        columns=["revenue"],
    )
    assert validate_spec(spec(table_roles), demo_model) == [
        "chart 'c1': table must not set series (got ['store_id'])",
        "chart 'c1': table must not set rows (got ['product_id'])",
        "chart 'c1': table must not set columns (got ['revenue'])",
    ]

    pivot_series = chart(
        viz=Viz.PIVOT,
        dimensions=[],
        rows=["store_id"],
        series=["product_id"],
    )
    assert validate_spec(spec(pivot_series), demo_model) == [
        "chart 'c1': pivot must not set series (got ['product_id'])"
    ]

    heatmap_two_measures = chart(
        viz=Viz.HEATMAP,
        dimensions=["store_id", "date"],
        measures=[
            Measure(column="revenue", agg=Aggregation.SUM, label="revenue_sum"),
            Measure(column="orders", agg=Aggregation.SUM, label="orders_sum"),
        ],
    )
    assert validate_spec(spec(heatmap_two_measures), demo_model) == [
        "chart 'c1': heatmap needs exactly one measure (got 2)"
    ]
    heatmap_roles = chart(
        viz=Viz.HEATMAP,
        dimensions=["store_id", "product_id"],
        series=["date"],
        rows=["revenue"],
        columns=["orders"],
        measures=[Measure(column="revenue", agg=Aggregation.SUM, label="metric")],
    )
    assert validate_spec(spec(heatmap_roles), demo_model) == [
        "chart 'c1': heatmap must not set series (got ['date'])",
        "chart 'c1': heatmap must not set rows (got ['revenue'])",
        "chart 'c1': heatmap must not set columns (got ['orders'])",
    ]

    histogram_two_measures = _hist_chart(
        measures=[
            Measure(column="revenue", agg=Aggregation.COUNT, label="revenue_count"),
            Measure(column="orders", agg=Aggregation.COUNT, label="orders_count"),
        ]
    )
    assert validate_spec(spec(histogram_two_measures), demo_model) == [
        "chart 'c1': histogram needs exactly one measure (got 2)"
    ]
    histogram_roles = _hist_chart(
        series=["store_id"],
        rows=["product_id"],
        columns=["date"],
    )
    assert validate_spec(spec(histogram_roles), demo_model) == [
        "chart 'c1': histogram must not set series (got ['store_id'])",
        "chart 'c1': histogram must not set rows (got ['product_id'])",
        "chart 'c1': histogram must not set columns (got ['date'])",
    ]
