"""D-1 dataset plan: expressibility classifier + shared source dataset shape."""

import sqlglot
from sqlglot import expressions as exp

from auto_bi.agent.dataset_plan import (
    DatasetRole,
    inexpressible_reason,
    plan_datasets,
    source_dataset_inputs,
)
from auto_bi.agent.sqlgen import generate_source_sql
from auto_bi.ir.spec import (
    ChartQuery,
    ChartSpec,
    DashboardSpec,
    JoinSpec,
    Measure,
    MeasureTransform,
    ScalarCompare,
    ScalarCompareKind,
    TimeGrain,
    Viz,
)
from auto_bi.semantic.model import Aggregation


def chart(chart_id: str = "c1", viz: Viz = Viz.LINE, **query_kwargs) -> ChartSpec:
    defaults = dict(
        table="dm.sales_daily",
        dimensions=["date"],
        measures=[Measure(column="revenue", agg=Aggregation.SUM)],
    )
    defaults.update(query_kwargs)
    return ChartSpec(id=chart_id, title=chart_id, viz=viz, query=ChartQuery(**defaults))


def spec(*charts: ChartSpec) -> DashboardSpec:
    return DashboardSpec(title="d", charts=list(charts))


# --- classifier ---------------------------------------------------------------


def test_plain_aggregate_is_expressible() -> None:
    assert inexpressible_reason(chart().query) is None


def test_ratio_measure_is_expressible() -> None:
    # a denominator becomes one adhoc SQL expression over source rows — no fallback
    q = chart(
        measures=[
            Measure(
                column="revenue",
                agg=Aggregation.SUM,
                denominator=Measure(column="orders", agg=Aggregation.SUM),
            )
        ]
    ).query
    assert inexpressible_reason(q) is None


def test_window_transform_falls_back() -> None:
    q = chart(
        measures=[
            Measure(
                column="revenue", agg=Aggregation.SUM, transform=MeasureTransform.SHARE_OF_TOTAL
            )
        ]
    ).query
    assert "share_of_total" in (inexpressible_reason(q) or "")


def test_scalar_compare_falls_back() -> None:
    q = chart(
        viz=Viz.BIG_NUMBER,
        dimensions=[],
        measures=[
            Measure(
                column="revenue",
                agg=Aggregation.SUM,
                compare=ScalarCompare(
                    kind=ScalarCompareKind.YOY, column="date", grain=TimeGrain.MONTH
                ),
            )
        ],
    ).query
    assert "yoy" in (inexpressible_reason(q) or "")


def test_raw_sql_falls_back() -> None:
    q = chart(viz=Viz.TABLE, dimensions=[], measures=[], raw_sql="SELECT 1 AS x", limit=100).query
    assert "raw_sql" in (inexpressible_reason(q) or "")


def test_histogram_bins_fall_back() -> None:
    q = chart(viz=Viz.HISTOGRAM, dimensions=["revenue"], bins=20).query
    assert "histogram" in (inexpressible_reason(q) or "")


def test_filter_preview_notes_for_own_charts_with_filters() -> None:
    from auto_bi.agent.dataset_plan import filter_preview_notes
    from auto_bi.ir.spec import DashboardFilter

    own = chart(
        "share",
        viz=Viz.BAR,
        dimensions=["store_id"],
        measures=[
            Measure(
                column="revenue", agg=Aggregation.SUM, transform=MeasureTransform.SHARE_OF_TOTAL
            )
        ],
    )
    own = own.model_copy(update={"title": "Доля магазинов"})
    plain = chart(
        "kpi",
        viz=Viz.BIG_NUMBER,
        dimensions=[],
        measures=[Measure(column="revenue", agg=Aggregation.SUM)],
    )
    # no filters -> no badges (nothing to warn about)
    assert filter_preview_notes(spec(own, plain)) == []
    with_filters = DashboardSpec(
        title="d",
        filters=[DashboardFilter(column="dm.sales_daily.date", type="time_range")],
        charts=[own, plain],
    )
    notes = filter_preview_notes(with_filters)
    assert len(notes) == 1
    assert notes[0].startswith("«Доля магазинов»: фильтр не влияет:")
    assert "share_of_total" in notes[0]


# --- plan ---------------------------------------------------------------------


def test_plan_splits_roles_and_orders_source_tables() -> None:
    windowed = chart(
        "cwin",
        measures=[
            Measure(column="revenue", agg=Aggregation.SUM, transform=MeasureTransform.RUNNING_TOTAL)
        ],
    )
    plain1 = chart("cp1")
    plain2 = chart("cp2", table="dm.orders", measures=[Measure(column="qty", agg=Aggregation.SUM)])
    plan = plan_datasets(spec(windowed, plain1, plain2))
    assert plan.chart("cwin").role is DatasetRole.OWN
    assert plan.chart("cwin").fallback_reason
    assert plan.chart("cp1").role is DatasetRole.SOURCE
    assert plan.chart("cp1").fallback_reason is None
    # spec order, deduped; the OWN chart's table appears only because cp1 shares it
    assert plan.source_tables == ("dm.sales_daily", "dm.orders")
    assert plan.source_chart_ids() == {"cp1", "cp2"}


def test_plan_all_fallback_has_no_source_tables() -> None:
    only = chart(
        "c1",
        measures=[
            Measure(column="revenue", agg=Aggregation.SUM, transform=MeasureTransform.POP_PCT)
        ],
    )
    plan = plan_datasets(spec(only))
    assert plan.source_tables == ()
    assert plan.source_chart_ids() == set()


# --- source dataset inputs ----------------------------------------------------


def _joined_chart(chart_id: str) -> ChartSpec:
    return chart(
        chart_id,
        viz=Viz.BAR,
        dimensions=["dm.stores.name"],
        joins=[
            JoinSpec(table="dm.stores", on_left="dm.sales_daily.store_id", on_right="dm.stores.id")
        ],
    )


def test_source_inputs_collect_model_columns_and_joins(demo_model) -> None:
    s = spec(_joined_chart("cj1"), _joined_chart("cj2"), chart("cp"))
    plan = plan_datasets(s)
    inputs = source_dataset_inputs(s, plan, demo_model, "dm.sales_daily")
    assert inputs.table == "dm.sales_daily"
    # every mart column from the model, bare names
    model_cols = tuple(c.name for c in demo_model.table("dm.sales_daily").columns)
    assert inputs.columns == model_cols
    # the identical join from two charts collapses to one
    assert len(inputs.joins) == 1
    assert inputs.joins[0].table == "dm.stores"
    assert inputs.joined_refs == ("dm.stores.name",)


def test_source_inputs_skip_own_charts_and_mart_qualified_refs(demo_model) -> None:
    own = chart(
        "cwin",
        dimensions=["dm.stores.name"],
        joins=[
            JoinSpec(table="dm.stores", on_left="dm.sales_daily.store_id", on_right="dm.stores.id")
        ],
        measures=[
            Measure(column="revenue", agg=Aggregation.SUM, transform=MeasureTransform.RUNNING_TOTAL)
        ],
    )
    base_qualified = chart("cq", dimensions=["dm.sales_daily.date"])
    s = spec(own, base_qualified)
    plan = plan_datasets(s)
    inputs = source_dataset_inputs(s, plan, demo_model, "dm.sales_daily")
    # the OWN chart's join must not leak into the shared dataset
    assert inputs.joins == ()
    # a mart-qualified ref is already covered by the bare mart column
    assert inputs.joined_refs == ()


# --- source SQL ---------------------------------------------------------------


def test_source_sql_no_joins_is_flat_select() -> None:
    sql = generate_source_sql("dm.sales_daily", ["date", "store_id", "revenue"], [])
    parsed = sqlglot.parse_one(sql, read="clickhouse")
    assert isinstance(parsed, exp.Select)
    assert parsed.args.get("group") is None
    assert parsed.args.get("where") is None
    assert parsed.args.get("limit") is None
    assert [c.alias_or_name for c in parsed.selects] == ["date", "store_id", "revenue"]


def test_source_sql_with_joins_qualifies_and_aliases() -> None:
    join = JoinSpec(table="dm.stores", on_left="dm.sales_daily.store_id", on_right="dm.stores.id")
    sql = generate_source_sql(
        "dm.sales_daily", ["date", "revenue"], [join], joined_refs=["dm.stores.name"]
    )
    parsed = sqlglot.parse_one(sql, read="clickhouse")
    assert [c.alias_or_name for c in parsed.selects] == ["date", "revenue", "name"]
    joins = parsed.args.get("joins") or []
    assert len(joins) == 1
    assert joins[0].side == "LEFT" or joins[0].kind == "LEFT"
    # base columns are qualified against the mart so joined bare names cannot collide
    assert '"dm"."sales_daily"."date"' in sql or '"dm"."sales_daily".date' in sql
