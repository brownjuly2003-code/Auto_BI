"""apply_label_joins — deterministic id -> human-readable name via a safe join (B3).

The transform swaps a raw FK id dimension (store_id) for the joined name column, but ONLY
where the semantic model proves the name is ~unique per id (cardinality guard) — so it can
never silently merge distinct ids. It adds the matching LEFT JOIN, remaps order_by, and is
pure + idempotent. Measures, filters, non-FK dimensions and ids without unique names are
left untouched. Composes with B1 (top-N) and produces specs that validate + generate SQL.
"""

import pytest

from auto_bi.agent.normalize import apply_chart_defaults, apply_label_joins
from auto_bi.agent.sqlgen import generate_chart_sql
from auto_bi.ir.spec import (
    ChartQuery,
    ChartSpec,
    DashboardSpec,
    JoinSpec,
    Measure,
    OrderBy,
    QueryFilter,
    Viz,
)
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

REVENUE = Measure(column="revenue", agg=Aggregation.SUM)  # alias -> "sum_revenue"


@pytest.fixture
def model() -> SemanticModel:
    """Star with cardinality recorded: stores/products names are ~unique per id (swap is
    lossless), regions names collide heavily (guard must skip)."""
    return SemanticModel(
        tables=[
            Table(
                name="dm.sales_daily",
                grain=["date", "store_id", "product_id"],
                columns=[
                    Column(name="date", type="Date", role=ColumnRole.TIME),
                    Column(
                        name="store_id", type="UInt32", role=ColumnRole.DIMENSION, fk="dm.stores.id"
                    ),
                    Column(
                        name="product_id",
                        type="UInt32",
                        role=ColumnRole.DIMENSION,
                        fk="dm.products.id",
                    ),
                    Column(
                        name="region_id",
                        type="UInt32",
                        role=ColumnRole.DIMENSION,
                        fk="dm.regions.id",
                    ),
                    Column(
                        name="revenue",
                        type="Decimal(18, 2)",
                        role=ColumnRole.MEASURE,
                        agg=Aggregation.SUM,
                    ),
                    Column(
                        name="orders", type="UInt32", role=ColumnRole.MEASURE, agg=Aggregation.SUM
                    ),
                ],
                physical=Physical(engine="clickhouse", rows=20_000_000),
            ),
            Table(
                name="dm.stores",
                grain=["id"],
                columns=[
                    Column(name="id", type="UInt32", role=ColumnRole.DIMENSION),
                    Column(name="name", type="String", role=ColumnRole.DIMENSION),
                    Column(name="city", type="LowCardinality(String)", role=ColumnRole.DIMENSION),
                ],
                physical=Physical(
                    engine="clickhouse",
                    rows=4200,
                    cardinality={"id": 4200, "name": 4203, "city": 20},
                ),
            ),
            Table(
                name="dm.products",
                grain=["id"],
                columns=[
                    Column(name="id", type="UInt32", role=ColumnRole.DIMENSION),
                    Column(name="name", type="String", role=ColumnRole.DIMENSION),
                ],
                physical=Physical(
                    engine="clickhouse", rows=2000, cardinality={"id": 2000, "name": 2000}
                ),
            ),
            Table(
                name="dm.regions",
                grain=["id"],
                columns=[
                    Column(name="id", type="UInt32", role=ColumnRole.DIMENSION),
                    Column(name="name", type="String", role=ColumnRole.DIMENSION),
                ],
                # name collides heavily (8 distinct names over 80 ids) -> NOT unique per id
                physical=Physical(engine="clickhouse", rows=80, cardinality={"id": 80, "name": 8}),
            ),
        ],
        joins=[
            Join(left="dm.sales_daily.store_id", right="dm.stores.id"),
            Join(left="dm.sales_daily.product_id", right="dm.products.id"),
            Join(left="dm.sales_daily.region_id", right="dm.regions.id"),
        ],
    )


def _chart(
    viz: Viz,
    *,
    dimensions=(),
    series=(),
    rows=(),
    columns=(),
    order_by=(),
    filters=(),
    measures=None,
) -> ChartSpec:
    return ChartSpec(
        id="c1",
        title="t",
        viz=viz,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=list(dimensions),
            series=list(series),
            rows=list(rows),
            columns=list(columns),
            measures=measures or [REVENUE],
            order_by=list(order_by),
            filters=list(filters),
        ),
    )


def _spec(chart: ChartSpec) -> DashboardSpec:
    return DashboardSpec(title="d", charts=[chart])


def _q(spec: DashboardSpec) -> ChartQuery:
    return spec.charts[0].query


# --- the core swap -----------------------------------------------------------------


def test_store_id_dimension_swapped_for_name(model) -> None:
    q = _q(apply_label_joins(_spec(_chart(Viz.BAR, dimensions=["store_id"])), model))
    assert q.dimensions == ["dm.stores.name"]
    assert q.joins == [
        JoinSpec(table="dm.stores", on_left="dm.sales_daily.store_id", on_right="dm.stores.id")
    ]


def test_swap_emits_join_and_groups_by_name(model) -> None:
    q = _q(apply_label_joins(_spec(_chart(Viz.BAR, dimensions=["store_id"])), model))
    sql = generate_chart_sql(q)
    assert "LEFT JOIN" in sql and '"dm"."stores"' in sql
    assert '"name"' in sql  # grouped/selected by the human-readable name, not store_id


def test_swap_in_series_role(model) -> None:
    q = _q(
        apply_label_joins(
            _spec(_chart(Viz.STACKED_BAR, dimensions=["date"], series=["store_id"])), model
        )
    )
    assert q.series == ["dm.stores.name"]
    assert q.dimensions == ["date"]  # the time axis is untouched


def test_swap_in_pivot_rows_keeps_shape(model) -> None:
    out = apply_label_joins(_spec(_chart(Viz.PIVOT, rows=["store_id"])), model)
    assert _q(out).rows == ["dm.stores.name"]
    assert validate_spec(out, model) == []


def test_order_by_on_id_is_remapped(model) -> None:
    chart = _chart(Viz.TABLE, dimensions=["store_id"], order_by=[OrderBy(by="store_id", dir="asc")])
    q = _q(apply_label_joins(_spec(chart), model))
    assert q.dimensions == ["dm.stores.name"]
    assert q.order_by == [OrderBy(by="dm.stores.name", dir="asc")]


# --- guards: untouched -------------------------------------------------------------


def test_non_unique_name_is_not_swapped(model) -> None:
    # region names collide (8 names / 80 ids) -> grouping by name would merge regions
    out = apply_label_joins(_spec(_chart(Viz.BAR, dimensions=["region_id"])), model)
    assert _q(out).dimensions == ["region_id"] and _q(out).joins == []


def test_no_cardinality_evidence_is_not_swapped(demo_model) -> None:
    # the conftest demo_model records no per-column cardinality -> conservative no-op
    out = apply_label_joins(_spec(_chart(Viz.BAR, dimensions=["store_id"])), demo_model)
    assert _q(out).dimensions == ["store_id"] and _q(out).joins == []


def test_non_fk_dimension_is_not_swapped(model) -> None:
    out = apply_label_joins(_spec(_chart(Viz.BAR, dimensions=["date"])), model)
    assert _q(out).dimensions == ["date"] and _q(out).joins == []


def test_already_qualified_dimension_is_not_swapped(model) -> None:
    out = apply_label_joins(_spec(_chart(Viz.BAR, dimensions=["dm.stores.city"])), model)
    assert _q(out).dimensions == ["dm.stores.city"]


def test_filter_on_id_is_preserved(model) -> None:
    # filtering by the raw id stays valid (the id is not displayed, so not swapped)
    chart = _chart(
        Viz.BAR, dimensions=["store_id"], filters=[QueryFilter(column="store_id", op="=", value=7)]
    )
    q = _q(apply_label_joins(_spec(chart), model))
    assert q.dimensions == ["dm.stores.name"]
    assert q.filters == [QueryFilter(column="store_id", op="=", value=7)]


def test_measures_untouched(model) -> None:
    q = _q(
        apply_label_joins(
            _spec(_chart(Viz.BAR, dimensions=["store_id"], measures=[REVENUE])), model
        )
    )
    assert q.measures == [REVENUE]


def test_bare_alias_collision_bails_whole_chart(model) -> None:
    # store_id -> dm.stores.name and product_id -> dm.products.name both alias to "name";
    # rather than emit a colliding (invalid) spec, the chart is left with its ids
    chart = _chart(Viz.TABLE, dimensions=["store_id", "product_id"])
    q = _q(apply_label_joins(_spec(chart), model))
    assert q.dimensions == ["store_id", "product_id"] and q.joins == []


# --- properties + integration ------------------------------------------------------


def test_idempotent(model) -> None:
    once = apply_label_joins(_spec(_chart(Viz.BAR, dimensions=["store_id"])), model)
    assert apply_label_joins(once, model) == once


def test_swapped_spec_validates(model) -> None:
    out = apply_label_joins(_spec(_chart(Viz.BAR, dimensions=["store_id"])), model)
    assert validate_spec(out, model) == []


def test_pie_swap_keeps_single_dimension(model) -> None:
    out = apply_label_joins(_spec(_chart(Viz.PIE, dimensions=["store_id"])), model)
    assert _q(out).dimensions == ["dm.stores.name"]
    assert validate_spec(out, model) == []  # pie still has exactly one dimension


def test_heatmap_two_dims_one_swapped_keeps_shape(model) -> None:
    # in-place replacement must preserve the exact dimension count heatmap requires (2)
    out = apply_label_joins(_spec(_chart(Viz.HEATMAP, dimensions=["store_id", "date"])), model)
    assert _q(out).dimensions == ["dm.stores.name", "date"]
    assert validate_spec(out, model) == []  # heatmap still has exactly two dimensions


def test_mixed_unique_and_non_unique_ids_swaps_only_safe_one(model) -> None:
    # store name is unique (swap), region name collides (kept) -> partial, still valid
    out = apply_label_joins(_spec(_chart(Viz.TABLE, dimensions=["store_id", "region_id"])), model)
    assert _q(out).dimensions == ["dm.stores.name", "region_id"]
    assert [j.table for j in _q(out).joins] == ["dm.stores"]
    assert validate_spec(out, model) == []


def test_composes_with_topn(model) -> None:
    # B3 swaps id->name, then B1 ranks the named categorical axis by the measure
    labeled = apply_label_joins(_spec(_chart(Viz.BAR, dimensions=["store_id"])), model)
    normalized = apply_chart_defaults(labeled, model)
    q = _q(normalized)
    assert q.dimensions == ["dm.stores.name"]
    assert q.order_by == [OrderBy(by="sum_revenue", dir="desc")] and q.limit == 25
    sql = generate_chart_sql(q)
    assert "LEFT JOIN" in sql and '"name"' in sql
    assert 'ORDER BY "sum_revenue" DESC' in sql and "LIMIT 25" in sql
