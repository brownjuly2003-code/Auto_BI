"""D-1 dataset plan: expressibility classifier + shared source dataset shape."""

from collections import Counter

import pytest
import sqlglot
from sqlglot import expressions as exp

from auto_bi.agent.dataset_plan import (
    DatasetRole,
    SourceAliasCollisionError,
    collect_source_aliases,
    filter_binding_alias,
    grain_exposes_column,
    inexpressible_reason,
    plan_datasets,
    source_column_alias,
    source_dataset_inputs,
    source_exposes_column,
)
from auto_bi.agent.sqlgen import generate_source_sql
from auto_bi.ir.spec import (
    ChartQuery,
    ChartSpec,
    DashboardFilter,
    DashboardSpec,
    JoinSpec,
    Measure,
    MeasureTransform,
    ScalarCompare,
    ScalarCompareKind,
    TimeGrain,
    Viz,
)
from auto_bi.semantic.model import Aggregation, Column, ColumnRole


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
    assert inexpressible_reason(q) == "оконная мера share_of_total: окно считается в SQL"


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
    # IR enum value is lower-case "yoy"; SQL reason is pinned in the same string
    assert inexpressible_reason(q) == "period-compare yoy: скаляр считается в SQL"


def test_raw_sql_falls_back() -> None:
    q = chart(viz=Viz.TABLE, dimensions=[], measures=[], raw_sql="SELECT 1 AS x", limit=100).query
    assert inexpressible_reason(q) == "raw_sql: запрос написан вручную, вне IR"


def test_histogram_bins_fall_back() -> None:
    q = chart(viz=Viz.HISTOGRAM, dimensions=["revenue"], bins=20).query
    assert inexpressible_reason(q) == "histogram: биннинг считается в SQL"


def test_filter_preview_notes_for_own_charts_with_filters() -> None:
    from auto_bi.agent.dataset_plan import filter_preview_notes

    own_share = chart(
        "share",
        viz=Viz.BAR,
        dimensions=["store_id"],
        measures=[
            Measure(
                column="revenue", agg=Aggregation.SUM, transform=MeasureTransform.SHARE_OF_TOTAL
            )
        ],
    )
    own_share = own_share.model_copy(update={"title": "Доля магазинов"})
    own_run = chart(
        "run",
        measures=[
            Measure(column="revenue", agg=Aggregation.SUM, transform=MeasureTransform.RUNNING_TOTAL)
        ],
    )
    own_run = own_run.model_copy(update={"title": "Накопительный"})
    plain = chart(
        "kpi",
        viz=Viz.BIG_NUMBER,
        dimensions=[],
        measures=[Measure(column="revenue", agg=Aggregation.SUM)],
    )
    # no filters -> no badges (nothing to warn about)
    assert filter_preview_notes(spec(own_share, own_run, plain)) == []
    with_filters = DashboardSpec(
        title="d",
        filters=[DashboardFilter(column="dm.sales_daily.date", type="time_range")],
        charts=[own_share, own_run, plain],
    )
    # no model: continue (not break) walks every OWN chart and pins exact reason copy
    assert filter_preview_notes(with_filters) == [
        "«Доля магазинов»: фильтр не влияет: оконная мера share_of_total: окно считается в SQL",
        "«Накопительный»: фильтр не влияет: оконная мера running_total: окно считается в SQL",
    ]


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
    own = plan.chart("cwin")
    assert own.role is DatasetRole.OWN
    assert own.chart_id == "cwin"
    assert own.table == "dm.sales_daily"
    assert own.fallback_reason == "оконная мера running_total: окно считается в SQL"
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
    # joined refs get deterministic unique aliases (not bare "name")
    assert [c.alias_or_name for c in parsed.selects] == ["date", "revenue", "stores_name"]
    joins = parsed.args.get("joins") or []
    assert len(joins) == 1
    assert joins[0].side == "LEFT" or joins[0].kind == "LEFT"
    # base columns are qualified against the mart so joined bare names cannot collide
    assert '"dm"."sales_daily"."date"' in sql or '"dm"."sales_daily".date' in sql


def test_source_column_alias_mart_bare_joined_unique() -> None:
    assert source_column_alias("date", "dm.sales_daily") == "date"
    assert source_column_alias("dm.sales_daily.store_id", "dm.sales_daily") == "store_id"
    assert source_column_alias("dm.stores.name", "dm.sales_daily") == "stores_name"
    assert source_column_alias("dm.products.name", "dm.sales_daily") == "products_name"


def test_two_joined_name_refs_have_unique_source_aliases(demo_model) -> None:
    """Finding 2 repro: two SOURCE charts on stores.name and products.name must not
    both emit AS \"name\" on the shared source dataset."""
    stores_join = JoinSpec(
        table="dm.stores", on_left="dm.sales_daily.store_id", on_right="dm.stores.id"
    )
    products_join = JoinSpec(
        table="dm.products", on_left="dm.sales_daily.product_id", on_right="dm.products.id"
    )
    s = spec(
        chart(
            "by_store",
            viz=Viz.BAR,
            dimensions=["dm.stores.name"],
            joins=[stores_join],
        ),
        chart(
            "by_product",
            viz=Viz.BAR,
            dimensions=["dm.products.name"],
            joins=[products_join],
        ),
    )
    plan = plan_datasets(s)
    inputs = source_dataset_inputs(s, plan, demo_model, "dm.sales_daily")
    assert set(inputs.joined_refs) == {"dm.stores.name", "dm.products.name"}
    sql = generate_source_sql(
        inputs.table, list(inputs.columns), list(inputs.joins), inputs.joined_refs
    )
    aliases = [c.alias_or_name for c in sqlglot.parse_one(sql, read="clickhouse").selects]
    dupes = [a for a, n in Counter(aliases).items() if n > 1]
    assert dupes == [], f"duplicate aliases in source SQL: {dupes}"
    assert "stores_name" in aliases and "products_name" in aliases
    assert aliases.count("name") == 0


def test_source_alias_collision_raises_at_plan_time() -> None:
    # happy path: two bare mart columns + one joined ref map to deterministic aliases
    assert collect_source_aliases(
        ["date", "store_id"],
        ["dm.stores.name"],
        "dm.sales_daily",
    ) == {
        "date": "date",
        "store_id": "store_id",
        "dm.stores.name": "stores_name",
    }

    with pytest.raises(SourceAliasCollisionError) as dup_bare:
        collect_source_aliases(["date", "date"], [], "dm.sales_daily")
    assert str(dup_bare.value) == (
        "source dataset alias 'date' collides between 'date' and mart column 'date' "
        "on dm.sales_daily"
    )

    with pytest.raises(SourceAliasCollisionError) as mart_vs_joined:
        collect_source_aliases(
            ["date", "stores_name"],
            ["dm.stores.name"],
            "dm.sales_daily",
        )
    assert str(mart_vs_joined.value) == (
        "source dataset alias 'stores_name' collides between 'stores_name' and "
        "joined ref 'dm.stores.name' on dm.sales_daily"
    )

    with pytest.raises(SourceAliasCollisionError) as dup_joined:
        collect_source_aliases([], ["dm.stores.name", "dm.stores.name"], "dm.sales_daily")
    assert str(dup_joined.value) == (
        "source dataset alias 'stores_name' collides between 'dm.stores.name' and "
        "joined ref 'dm.stores.name' on dm.sales_daily"
    )


def test_grain_source_and_filter_binding_exposure(demo_model) -> None:
    """Behavioral coverage for grain/source exposure and filter binding fallback."""
    stores_join = JoinSpec(
        table="dm.stores", on_left="dm.sales_daily.store_id", on_right="dm.stores.id"
    )
    bare = chart("gbare", viz=Viz.BAR, dimensions=["store_id"])
    assert grain_exposes_column(bare, "store_id") is True
    assert grain_exposes_column(bare, "unknown_col") is False

    joined_source = chart("js", viz=Viz.BAR, dimensions=["dm.stores.name"], joins=[stores_join])
    plain = chart("p1")
    s = spec(joined_source, plain)
    plan = plan_datasets(s)
    table = "dm.sales_daily"
    assert source_exposes_column(s, plan, demo_model, table, "dm.sales_daily.date") is True
    # same bare tail on a wrong table must not match the mart's date column
    assert source_exposes_column(s, plan, demo_model, table, "dm.other_fact.date") is False
    assert source_exposes_column(s, plan, demo_model, table, "date") is True
    assert source_exposes_column(s, plan, demo_model, table, "nope") is False
    assert source_exposes_column(s, plan, demo_model, table, "dm.stores.name") is True

    # joined ref is contributed only by SOURCE charts — OWN joins must not leak
    own_joined = chart(
        "ownj",
        viz=Viz.BAR,
        dimensions=["dm.stores.name"],
        joins=[stores_join],
        measures=[
            Measure(column="revenue", agg=Aggregation.SUM, transform=MeasureTransform.RUNNING_TOTAL)
        ],
    )
    s_own = spec(own_joined, plain)
    plan_own = plan_datasets(s_own)
    assert source_exposes_column(s_own, plan_own, demo_model, table, "dm.stores.name") is False

    # SOURCE chart that does not expose dm.products.name -> bare "name", not products_name
    products_filt = DashboardFilter(column="dm.products.name", type="value")
    s_bind = spec(plain)
    plan_bind = plan_datasets(s_bind)
    assert filter_binding_alias(s_bind, plan_bind, demo_model, products_filt) == "name"


def test_source_inputs_distinct_joins_and_alias_collision(demo_model) -> None:
    """Two distinct joins keep order; mart bare stores_name collides with joined stores.name."""
    stores_join = JoinSpec(
        table="dm.stores", on_left="dm.sales_daily.store_id", on_right="dm.stores.id"
    )
    products_join = JoinSpec(
        table="dm.products", on_left="dm.sales_daily.product_id", on_right="dm.products.id"
    )
    s = spec(
        chart("by_store", viz=Viz.BAR, dimensions=["dm.stores.name"], joins=[stores_join]),
        chart("by_product", viz=Viz.BAR, dimensions=["dm.products.name"], joins=[products_join]),
    )
    plan = plan_datasets(s)
    inputs = source_dataset_inputs(s, plan, demo_model, "dm.sales_daily")
    assert [j.table for j in inputs.joins] == ["dm.stores", "dm.products"]
    assert inputs.joined_refs == ("dm.stores.name", "dm.products.name")

    fact = demo_model.table("dm.sales_daily")
    assert fact is not None
    extended = demo_model.model_copy(
        update={
            "tables": [
                fact.model_copy(
                    update={
                        "columns": [
                            *fact.columns,
                            Column(
                                name="stores_name",
                                type="String",
                                role=ColumnRole.DIMENSION,
                            ),
                        ]
                    }
                ),
                *[t for t in demo_model.tables if t.name != "dm.sales_daily"],
            ]
        }
    )
    collide = spec(
        chart("by_store", viz=Viz.BAR, dimensions=["dm.stores.name"], joins=[stores_join])
    )
    collide_plan = plan_datasets(collide)
    with pytest.raises(SourceAliasCollisionError) as err:
        source_dataset_inputs(collide, collide_plan, extended, "dm.sales_daily")
    assert str(err.value) == (
        "source dataset alias 'stores_name' collides between 'stores_name' and "
        "joined ref 'dm.stores.name' on dm.sales_daily"
    )
    assert str(err.value).endswith("on dm.sales_daily")
