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
