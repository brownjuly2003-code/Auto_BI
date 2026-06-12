"""validate_spec: spec vs semantic model (invariant 2)."""

from auto_bi.ir.spec import (
    ChartQuery,
    ChartSpec,
    DashboardFilter,
    DashboardSpec,
    Measure,
    OrderBy,
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
