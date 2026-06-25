"""Auto-overview mode (autospec): curated dashboard built from the model alone.

The model here carries `physical.cardinality` and a joined dim table so the breakdown
logic is exercised (the shared `demo_model` fixture records no cardinality, so it would
yield only KPIs + a line). Every assertion ultimately leans on `validate_spec` returning
no errors — the auto spec must be a first-class citizen of the same pipeline.
"""

import pytest

from auto_bi.agent.autospec import build_auto_spec
from auto_bi.agent.normalize import apply_chart_defaults, apply_label_joins
from auto_bi.ir.spec import Viz
from auto_bi.ir.validate import validate_spec
from auto_bi.semantic.model import (
    Aggregation,
    Column,
    ColumnRole,
    Join,
    Physical,
    SemanticModel,
    Table,
)


@pytest.fixture
def model() -> SemanticModel:
    fact = Table(
        name="dm.sales_daily",
        description="Дневные продажи",
        grain=["date", "store_id", "product_id"],
        columns=[
            Column(name="date", type="Date", role=ColumnRole.TIME, description="День продажи"),
            Column(name="store_id", type="UInt32", role=ColumnRole.DIMENSION, fk="dm.stores.id"),
            Column(
                name="manager_id",  # high cardinality, no fk -> must never be a breakdown
                type="UInt32",
                role=ColumnRole.DIMENSION,
            ),
            Column(
                name="revenue",
                type="Decimal(18, 2)",
                role=ColumnRole.MEASURE,
                agg=Aggregation.SUM,
                description="Выручка, руб",
            ),
            Column(name="orders", type="UInt32", role=ColumnRole.MEASURE, agg=Aggregation.SUM),
        ],
        physical=Physical(
            engine="clickhouse",
            rows=20_000_000,
            cardinality={"store_id": 4200, "manager_id": 16825},
        ),
    )
    stores = Table(
        name="dm.stores",
        description="Справочник магазинов",
        grain=["id"],
        columns=[
            Column(name="id", type="UInt32", role=ColumnRole.DIMENSION),
            Column(name="name", type="String", role=ColumnRole.DIMENSION),
            Column(
                name="city",
                type="LowCardinality(String)",
                role=ColumnRole.DIMENSION,
                description="Город",
            ),
            Column(
                name="region",
                type="LowCardinality(String)",
                role=ColumnRole.DIMENSION,
                description="Регион",
            ),
            Column(
                name="format",
                type="LowCardinality(String)",
                role=ColumnRole.DIMENSION,
                description="Формат",
            ),
        ],
        physical=Physical(
            engine="clickhouse",
            rows=4200,
            cardinality={"id": 4200, "name": 4203, "city": 20, "region": 8, "format": 3},
        ),
    )
    return SemanticModel(
        tables=[fact, stores],
        joins=[Join(left="dm.sales_daily.store_id", right="dm.stores.id")],
    )


def _bare_dims(spec) -> set[str]:
    return {d for c in spec.charts for d in c.query.dimensions}


def test_builds_a_valid_spec(model) -> None:
    spec = build_auto_spec(model, "dm.sales_daily")
    assert validate_spec(spec, model) == []
    assert spec.charts
    ids = [c.id for c in spec.charts]
    assert len(ids) == len(set(ids))  # unique chart ids


def test_kpi_per_measure(model) -> None:
    spec = build_auto_spec(model, "dm.sales_daily")
    kpis = [c for c in spec.charts if c.viz == Viz.BIG_NUMBER]
    assert len(kpis) == 2  # revenue + orders
    for c in kpis:
        assert len(c.query.measures) == 1 and not c.query.dimensions


def test_dynamics_line_over_time_ordered_by_time(model) -> None:
    spec = build_auto_spec(model, "dm.sales_daily")
    lines = [c for c in spec.charts if c.viz == Viz.LINE]
    assert len(lines) == 1
    q = lines[0].query
    assert q.dimensions == ["date"]
    assert q.order_by and q.order_by[0].by == "date" and q.order_by[0].dir == "asc"


def test_breakdowns_use_joined_attributes_not_raw_ids(model) -> None:
    spec = build_auto_spec(model, "dm.sales_daily")
    dims = _bare_dims(spec)
    # low-card attributes of the joined dim table appear...
    assert {"dm.stores.city", "dm.stores.region", "dm.stores.format"} & dims
    # ...and the high-card no-fk id never does
    assert "manager_id" not in dims


def test_pie_and_bars_never_share_a_column(model) -> None:
    spec = build_auto_spec(model, "dm.sales_daily")
    bar_dims = {d for c in spec.charts if c.viz == Viz.BAR for d in c.query.dimensions}
    pie_dims = {d for c in spec.charts if c.viz == Viz.PIE for d in c.query.dimensions}
    assert not (bar_dims & pie_dims)
    # the pie is the lowest-cardinality breakdown (format=3)
    if pie_dims:
        assert pie_dims == {"dm.stores.format"}


def test_every_join_is_a_model_edge(model) -> None:
    spec = build_auto_spec(model, "dm.sales_daily")
    edges = {frozenset((j.left, j.right)) for j in model.joins}
    for chart in spec.charts:
        for j in chart.query.joins:
            assert frozenset((j.on_left, j.on_right)) in edges


def test_synthetic_count_for_table_without_measures(model) -> None:
    spec = build_auto_spec(model, "dm.stores")  # a reference dim: no role=measure columns
    assert validate_spec(spec, model) == []
    for chart in spec.charts:
        assert all(m.agg == Aggregation.COUNT for m in chart.query.measures)


def test_respects_max_charts(model) -> None:
    spec = build_auto_spec(model, "dm.sales_daily", max_charts=4)
    assert len(spec.charts) == 4
    assert validate_spec(spec, model) == []


def test_unknown_table_raises(model) -> None:
    with pytest.raises(ValueError, match="unknown table"):
        build_auto_spec(model, "dm.nope")


def test_title_drops_technical_grain_annotation() -> None:
    # a modeler's grain note is internal metadata, not a user-facing title (dashboard-craft)
    from auto_bi.agent.autospec import _clean_title

    assert _clean_title("Обзор: Продажи (грейн: date, store_id, product_id)") == "Обзор: Продажи"
    assert _clean_title("Обзор: Продажи (grain: date)") == "Обзор: Продажи"
    assert _clean_title("Обзор: Продажи (РФ)") == "Обзор: Продажи (РФ)"  # non-technical kept


def test_idempotent_and_valid_under_normalize(model) -> None:
    """The normalize pass compile_and_build runs must keep the spec valid and stable."""
    spec = build_auto_spec(model, "dm.sales_daily")
    once = apply_chart_defaults(apply_label_joins(spec, model), model)
    twice = apply_chart_defaults(apply_label_joins(once, model), model)
    assert validate_spec(once, model) == []
    assert once.model_dump() == twice.model_dump()  # idempotent


def test_default_time_filter_present_on_fact(model) -> None:
    spec = build_auto_spec(model, "dm.sales_daily")
    assert [f.column for f in spec.filters] == ["dm.sales_daily.date"]
