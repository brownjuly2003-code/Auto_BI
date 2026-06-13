"""Eval cases (task 1.11): 15 golden dialogue cases + seeded advisor anti-patterns.

Golden cases run against the live GraceKelly on the demo-DM model; advisor cases are
fully deterministic (metadata-driven rules, no LLM) — anti-patterns are SEEDED: the
case may transform the model (boosted cardinality, Collapsing engine) the same way
a real DM would expose them.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from pydantic import BaseModel, Field

from auto_bi.agent.seed import FieldsSeed, SeedGroup
from auto_bi.engine import GREENPLUM
from auto_bi.ir.spec import ChartQuery, ChartSpec, FilterOp, JoinSpec, Measure, QueryFilter, Viz
from auto_bi.semantic.model import Aggregation, SemanticModel


class CaseKind(StrEnum):
    CLEAR = "clear"  # unambiguous: zero questions + a sane spec
    AMBIGUOUS = "ambiguous"  # a clarifying question is REQUIRED
    INFEASIBLE = "infeasible"  # not in the DM: must be flagged, not hallucinated


class GoldenCase(BaseModel):
    id: str
    request: str = ""
    seed: FieldsSeed | None = None  # fields-first entry (task 2.3); request may be empty
    kind: CaseKind
    table: str = ""  # clear: every chart must use this table
    expect_columns: set[str] = Field(default_factory=set)  # clear: must appear in the spec
    # clear: per-group alternatives — at least ONE column of each group must appear
    # (a dimension may legitimately resolve to the fk id OR the joined human-readable
    # column, e.g. "магазин" -> store_id | dm.stores.name)
    expect_columns_any: list[set[str]] = Field(default_factory=list)
    expect_viz: set[Viz] = Field(default_factory=set)  # clear: at least one chart of these
    expect_phrase: str = ""  # ambiguous/infeasible: a question must mention this
    # iteration (task 2.8): a word edit applied AFTER the clear checks pass; the
    # patched spec is then checked against the edit_* expectations below
    edit: str = ""
    edit_expect_columns: set[str] = Field(default_factory=set)  # must appear after the edit
    edit_expect_gone: set[str] = Field(default_factory=set)  # must DISAPPEAR after the edit
    edit_expect_viz: set[Viz] = Field(default_factory=set)  # at least one chart of these


GOLDEN_CASES: list[GoldenCase] = [
    # --- clear: zero questions expected, spec checked mechanically -----------------
    GoldenCase(
        id="g1_revenue_by_day",
        request="Выручка по дням за июнь 2026",
        kind=CaseKind.CLEAR,
        table="dm.sales_daily",
        expect_columns={"date", "revenue"},
        expect_viz={Viz.LINE, Viz.AREA},
    ),
    GoldenCase(
        # cross-table dimension via an explicit join (city lives in dm.stores)
        id="g12_revenue_by_city_join",
        request="Топ-10 городов по выручке за июнь 2026",
        kind=CaseKind.CLEAR,
        table="dm.sales_daily",
        expect_columns={"dm.stores.city", "revenue"},
        expect_viz={Viz.BAR, Viz.TABLE},
    ),
    GoldenCase(
        id="g2_total_revenue_kpi",
        request="Общая выручка одним числом за июнь 2026",
        kind=CaseKind.CLEAR,
        table="dm.sales_daily",
        expect_columns={"revenue"},
        expect_viz={Viz.BIG_NUMBER},
    ),
    GoldenCase(
        id="g3_top_stores",
        request="Топ-10 магазинов по выручке за июнь 2026",
        kind=CaseKind.CLEAR,
        table="dm.sales_daily",
        expect_columns={"store_id", "revenue"},
        expect_viz={Viz.BAR, Viz.TABLE},
    ),
    GoldenCase(
        id="g4_orders_by_day",
        request="Число заказов по дням за июнь 2026",
        kind=CaseKind.CLEAR,
        table="dm.sales_daily",
        expect_columns={"date", "orders"},
        expect_viz={Viz.LINE, Viz.AREA, Viz.BAR},
    ),
    GoldenCase(
        id="g5_pivot_store_manager",
        request="Сводная таблица: выручка по магазинам (строки) и менеджерам (колонки) "
        "за 1-7 июня 2026",
        kind=CaseKind.CLEAR,
        table="dm.sales_daily",
        expect_columns={"store_id", "manager_id", "revenue"},
        expect_viz={Viz.PIVOT},
    ),
    GoldenCase(
        id="g6_heatmap_day_store",
        request="Теплокарта выручки: дни на одной оси, магазины на другой, июнь 2026, "
        "магазины 1-5",
        kind=CaseKind.CLEAR,
        table="dm.sales_daily",
        expect_columns={"date", "store_id", "revenue"},
        expect_viz={Viz.HEATMAP},
    ),
    GoldenCase(
        id="g7_table_recent",
        request="Таблица: дата, магазин, выручка и заказы за 20-27 июня 2026",
        kind=CaseKind.CLEAR,
        table="dm.sales_daily",
        expect_columns={"date", "revenue", "orders"},
        # «магазин»: с joins LLM законно выбирает читаемое имя вместо id
        expect_columns_any=[{"store_id", "dm.stores.name"}],
        expect_viz={Viz.TABLE},
    ),
    GoldenCase(
        id="g8_stacked_by_store",
        request="Динамика выручки по дням с разбивкой по магазинам 1, 2, 3 за июнь 2026 "
        "(стопкой)",
        kind=CaseKind.CLEAR,
        table="dm.sales_daily",
        expect_columns={"date", "store_id", "revenue"},
        expect_viz={Viz.STACKED_BAR, Viz.AREA},
    ),
    GoldenCase(
        id="g9_city_share",
        request="Доли городов по числу магазинов",
        kind=CaseKind.CLEAR,
        table="dm.stores",
        expect_columns={"city"},
        expect_viz={Viz.PIE, Viz.BAR},
    ),
    GoldenCase(
        id="g10_avg_price_by_category",
        request="Средняя цена товаров по категориям",
        kind=CaseKind.CLEAR,
        table="dm.products",
        expect_columns={"category", "price"},
        expect_viz={Viz.BAR, Viz.TABLE},
    ),
    GoldenCase(
        id="g11_items_by_day",
        request="Число позиций (items) по дням за июнь 2026",
        kind=CaseKind.CLEAR,
        table="dm.sales_daily",
        expect_columns={"date", "items"},
        expect_viz={Viz.LINE, Viz.AREA, Viz.BAR},
    ),
    # --- iterations (task 2.8 / 2.4): clear request -> word edit -> patched spec ----
    GoldenCase(
        id="it1_add_orders",
        request="Выручка по дням за июнь 2026",
        kind=CaseKind.CLEAR,
        table="dm.sales_daily",
        expect_columns={"date", "revenue"},
        edit="Добавь на дашборд число заказов по дням",
        edit_expect_columns={"orders"},
    ),
    GoldenCase(
        id="it2_remove_chart",
        request="Выручка по дням и топ-10 магазинов по выручке за июнь 2026",
        kind=CaseKind.CLEAR,
        table="dm.sales_daily",
        expect_columns={"date", "revenue"},
        # «магазин»: с joins LLM законно выбирает читаемое имя вместо id
        expect_columns_any=[{"store_id", "dm.stores.name"}],
        # «чарт» в ед. числе ловился на спеке с ДВУМЯ топами магазинов (bar+table):
        # LLM честно убирал один — scope правки должен быть явным
        edit="Убери все чарты с разбивкой по магазинам",
        edit_expect_gone={"store_id", "dm.stores.name"},
    ),
    GoldenCase(
        id="it3_change_viz",
        request="Выручка по дням за июнь 2026 линией",
        kind=CaseKind.CLEAR,
        table="dm.sales_daily",
        expect_columns={"date", "revenue"},
        expect_viz={Viz.LINE},
        edit="Замени линию на area (с заливкой)",
        edit_expect_viz={Viz.AREA},
    ),
    # --- fields-first (task 2.3): the seed is the request, same checks as clear -----
    GoldenCase(
        id="f1_fields_revenue_trend",
        seed=FieldsSeed(
            groups=[
                SeedGroup(
                    label="Тренд выручки",
                    fields=["dm.sales_daily.date", "dm.sales_daily.revenue"],
                )
            ],
            comment="июнь 2026",
        ),
        kind=CaseKind.CLEAR,
        table="dm.sales_daily",
        expect_columns={"date", "revenue"},
        expect_viz={Viz.LINE, Viz.AREA},
    ),
    GoldenCase(
        id="f2_fields_two_groups",
        seed=FieldsSeed(
            groups=[
                SeedGroup(fields=["dm.sales_daily.date", "dm.sales_daily.revenue"]),
                SeedGroup(fields=["dm.sales_daily.store_id", "dm.sales_daily.orders"]),
            ],
            comment="июнь 2026",
        ),
        kind=CaseKind.CLEAR,
        table="dm.sales_daily",
        expect_columns={"date", "revenue", "store_id", "orders"},
    ),
    GoldenCase(
        id="f3_fields_then_edit",
        seed=FieldsSeed(
            groups=[SeedGroup(fields=["dm.sales_daily.date", "dm.sales_daily.revenue"])],
            comment="июнь 2026",
        ),
        kind=CaseKind.CLEAR,
        table="dm.sales_daily",
        expect_columns={"date", "revenue"},
        edit="Добавь чарт: число заказов по дням",
        edit_expect_columns={"orders"},
    ),
    # --- ambiguous: the request has >=2 real readings -> a question is required ----
    GoldenCase(
        id="a1_quantity",
        request="Количество по магазинам за июнь 2026",
        kind=CaseKind.AMBIGUOUS,
        expect_phrase="количество",
    ),
    GoldenCase(
        id="a2_name",
        request="Выручка по названию за июнь 2026",
        kind=CaseKind.AMBIGUOUS,
        expect_phrase="назван",
    ),
    GoldenCase(
        id="a3_avg_ticket",
        request="Средний чек по дням за июнь 2026",
        kind=CaseKind.AMBIGUOUS,
        expect_phrase="чек",
    ),
    GoldenCase(
        id="a4_quantity_dynamics",
        request="Динамика количества за июнь 2026",
        kind=CaseKind.AMBIGUOUS,
        expect_phrase="количеств",
    ),
    # --- infeasible: not in the DM at all -> flagged with an explanation -----------
    GoldenCase(
        id="i1_salaries",
        request="Зарплата сотрудников по месяцам за 2026 год",
        kind=CaseKind.INFEASIBLE,
        expect_phrase="зарплат",
    ),
    GoldenCase(
        id="i2_returns",
        request="Динамика возвратов по дням за июнь 2026",
        kind=CaseKind.INFEASIBLE,
        expect_phrase="возврат",
    ),
    GoldenCase(
        id="i3_conversion",
        request="Конверсия сайта по неделям за июнь 2026",
        kind=CaseKind.INFEASIBLE,
        expect_phrase="конверси",
    ),
    GoldenCase(
        id="i4_weather",
        request="Прогноз погоды по дням за июнь 2026",
        kind=CaseKind.INFEASIBLE,
        expect_phrase="погод",
    ),
]


# --- advisor anti-pattern cases ----------------------------------------------------

REVENUE = Measure(column="revenue", agg=Aggregation.SUM, label="Выручка")


class AdvisorCase(BaseModel):
    id: str
    description: str
    chart: ChartSpec
    expect_rules: set[str] = Field(default_factory=set)  # must ALL be found
    expect_clean: bool = False  # clean case: ZERO findings expected
    # optional model seeding (e.g. boosted cardinality); applied to a deep copy
    seed: Callable[[SemanticModel], None] | None = Field(default=None, exclude=True)

    model_config = {"arbitrary_types_allowed": True}


def _seed_high_cardinality(model: SemanticModel) -> None:
    """Seed: a real DM would have a 100k+ dimension; demo tops out at ~17k."""
    model.table("dm.sales_daily").physical.cardinality["manager_id"] = 150_000


def _seed_collapsing_engine(model: SemanticModel) -> None:
    model.table("dm.sales_daily").physical.table_engine = "ReplacingMergeTree"


def _chart(cid: str, viz: Viz, **query) -> ChartSpec:
    query.setdefault("table", "dm.sales_daily")
    query.setdefault("measures", [REVENUE])
    return ChartSpec(id=cid, title=cid, viz=viz, query=ChartQuery(**query))


JUNE = QueryFilter(column="date", op=FilterOp.GTE, value="2026-06-01")

ADVISOR_CASES: list[AdvisorCase] = [
    AdvisorCase(
        id="ap1_no_filter_large_fact",
        description="bar по 20M-факту вообще без фильтров",
        chart=_chart("ap1", Viz.BAR, dimensions=["store_id"]),
        expect_rules={"no_filter_on_large_fact"},
    ),
    AdvisorCase(
        id="ap2_miss_leading_key",
        description="фильтр по store_id (в ключе, но мимо ведущей колонки date)",
        chart=_chart(
            "ap2",
            Viz.BAR,
            dimensions=["product_id"],
            filters=[QueryFilter(column="store_id", op=FilterOp.EQ, value=5)],
        ),
        expect_rules={"filter_not_in_sorting_key_prefix"},
    ),
    AdvisorCase(
        id="ap3_filter_outside_key",
        description="фильтр по manager_id — колонки нет в ключе сортировки вовсе "
        "(dm_change_request)",
        chart=_chart(
            "ap3",
            Viz.LINE,
            dimensions=["date"],
            filters=[QueryFilter(column="manager_id", op=FilterOp.EQ, value=42)],
        ),
        expect_rules={"filter_not_in_sorting_key_prefix"},
    ),
    AdvisorCase(
        id="ap4_partition_misaligned",
        description="фильтры есть, но ни одного по партиционной колонке date",
        chart=_chart(
            "ap4",
            Viz.BAR,
            dimensions=["store_id"],
            filters=[QueryFilter(column="store_id", op=FilterOp.IN, value=[1, 2, 3])],
        ),
        expect_rules={"partition_misaligned_filter"},
    ),
    AdvisorCase(
        id="ap5_high_cardinality_groupby",
        description="GROUP BY manager_id при подсаженной кардинальности 150k",
        chart=_chart("ap5", Viz.TABLE, dimensions=["manager_id"], filters=[JUNE]),
        expect_rules={"group_by_high_cardinality"},
        seed=_seed_high_cardinality,
    ),
    AdvisorCase(
        id="ap6_collapsing_engine",
        description="подсаженный ReplacingMergeTree: агрегаты без FINAL могут задвоить",
        chart=_chart("ap6", Viz.LINE, dimensions=["date"], filters=[JUNE]),
        expect_rules={"collapsing_engine_needs_final"},
        seed=_seed_collapsing_engine,
    ),
    # --- clean cases: the advisor must stay SILENT (0 false positives) -------------
    AdvisorCase(
        id="clean1_dated_trend",
        description="line по date с фильтром по date — чистый паттерн витрины",
        chart=_chart("c1", Viz.LINE, dimensions=["date"], filters=[JUNE]),
        expect_clean=True,
    ),
    AdvisorCase(
        id="clean2_dated_kpi",
        description="big_number с фильтром по date",
        chart=_chart("c2", Viz.BIG_NUMBER, filters=[JUNE]),
        expect_clean=True,
    ),
    AdvisorCase(
        id="clean3_small_dim_table",
        description="pie по городам на маленьком справочнике без партиций",
        chart=_chart(
            "c3",
            Viz.PIE,
            table="dm.stores",
            dimensions=["city"],
            measures=[Measure(column="id", agg=Aggregation.COUNT, label="Магазинов")],
        ),
        expect_clean=True,
    ),
]


# --- Greenplum advisor anti-pattern cases (Phase 3.5) ------------------------------
# Deterministic, like the ClickHouse pack above: metadata-driven rules, no LLM. GP
# reasons about the distribution key / partition column instead of the sorting key,
# so the cases seed the GP demo model (dm.sales: DISTRIBUTED BY store_id, RANGE(date)).
# The two at-scale rules gate on rows >= 10M, so those cases seed a large fact (the
# committed model_gp.yaml is the 300k demo) exactly as the CH cases seed cardinality.
# `partition_not_pruned` is EXPLAIN-measured (live-only) and has no offline case here.


def _seed_gp_large_fact(model: SemanticModel) -> None:
    """A real GP fact crosses the 10M threshold; the demo model ships at 300k."""
    model.table("dm.sales").physical.rows = 20_000_000


def _seed_gp_high_cardinality(model: SemanticModel) -> None:
    """A real product dimension is far larger than the demo's 50 rows."""
    model.table("dm.sales").physical.cardinality["product_id"] = 150_000


def _gp_chart(cid: str, viz: Viz, **query) -> ChartSpec:
    query.setdefault("table", "dm.sales")
    query.setdefault("measures", [REVENUE])
    return ChartSpec(id=cid, title=cid, viz=viz, query=ChartQuery(**query))


GP_JUNE = QueryFilter(column="date", op=FilterOp.GTE, value="2026-06-01")

GP_ADVISOR_CASES: list[AdvisorCase] = [
    AdvisorCase(
        id="gp_ap1_no_filter_large_fact",
        description="bar по 20M GP-факту без фильтров (full scan на каждом обновлении)",
        chart=_gp_chart("gp_ap1", Viz.BAR, dimensions=["store_id"]),
        expect_rules={"no_filter_on_large_fact"},
        seed=_seed_gp_large_fact,
    ),
    AdvisorCase(
        id="gp_ap2_distribution_skew",
        description="dist key store_id (~20 значений) на 20M-факте -> перекос по сегментам",
        chart=_gp_chart("gp_ap2", Viz.BAR, dimensions=["store_id"], filters=[GP_JUNE]),
        expect_rules={"distribution_skew"},
        seed=_seed_gp_large_fact,
    ),
    AdvisorCase(
        id="gp_ap3_non_colocated_join",
        description="join sales x products по product_id мимо dist key store_id -> motion",
        chart=_gp_chart(
            "gp_ap3",
            Viz.BAR,
            dimensions=["dm.products.category"],
            filters=[GP_JUNE],
            joins=[
                JoinSpec(
                    table="dm.products",
                    on_left="dm.sales.product_id",
                    on_right="dm.products.product_id",
                )
            ],
        ),
        expect_rules={"non_colocated_join"},
    ),
    AdvisorCase(
        id="gp_ap4_high_cardinality_groupby",
        description="GROUP BY product_id при подсаженной кардинальности 150k",
        chart=_gp_chart("gp_ap4", Viz.TABLE, dimensions=["product_id"], filters=[GP_JUNE]),
        expect_rules={"group_by_high_cardinality"},
        seed=_seed_gp_high_cardinality,
    ),
    # --- clean cases: the GP pack must stay SILENT (0 false positives) --------------
    AdvisorCase(
        id="gp_clean1_dated_colocated_join",
        description="date-фильтр + co-located join по store_id (dist key) на 300k -> чисто",
        chart=_gp_chart(
            "gp_c1",
            Viz.BAR,
            dimensions=["dm.stores.city"],
            filters=[GP_JUNE],
            joins=[
                JoinSpec(
                    table="dm.stores",
                    on_left="dm.sales.store_id",
                    on_right="dm.stores.store_id",
                )
            ],
        ),
        expect_clean=True,
    ),
    AdvisorCase(
        id="gp_clean2_dated_kpi",
        description="big_number с фильтром по date на 300k-факте -> чисто",
        chart=_gp_chart("gp_c2", Viz.BIG_NUMBER, filters=[GP_JUNE]),
        expect_clean=True,
    ),
]


# GP golden dialogue cases require designing prompts/expectations against the GP demo
# schema and a live GraceKelly run — that is eval-design (stopper S2), handed off to a
# Fable/manual session (see docs/plans/2026-06-13-phase3.5-gp-golden-cases-handoff.md).
GP_GOLDEN_CASES: list[GoldenCase] = []


def advisor_cases_for_engine(engine: str) -> list[AdvisorCase]:
    """Pick the advisor anti-pattern set by DM engine (CH sorting-key vs GP distribution)."""
    return GP_ADVISOR_CASES if engine == GREENPLUM else ADVISOR_CASES


def golden_cases_for_engine(engine: str) -> list[GoldenCase]:
    return GP_GOLDEN_CASES if engine == GREENPLUM else GOLDEN_CASES
