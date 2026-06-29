"""Auto-overview mode (autospec): curated dashboard built from the model alone.

The model here carries `physical.cardinality` and a joined dim table so the breakdown
logic is exercised (the shared `demo_model` fixture records no cardinality, so it would
yield only KPIs + a line). Every assertion ultimately leans on `validate_spec` returning
no errors — the auto spec must be a first-class citizen of the same pipeline.
"""

from pathlib import Path

import pytest

from auto_bi.agent.autospec import build_auto_spec
from auto_bi.agent.normalize import apply_chart_defaults, apply_label_joins
from auto_bi.ir.spec import MeasureTransform, TimeGrain, Viz
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


def test_kpi_cards_are_identical_and_fill_their_row(model) -> None:
    # dashboard-craft §3: KPI cards are identical (same width AND height) and their row spans
    # one full aligned width (2 cards -> 6+6 = 12), not a ragged partial row
    spec = build_auto_spec(model, "dm.sales_daily")
    kpis = [c for c in spec.charts if c.viz == Viz.BIG_NUMBER]
    assert len({c.layout_hint.w for c in kpis}) == 1  # one width
    assert len({c.layout_hint.h for c in kpis}) == 1  # one height
    assert sum(c.layout_hint.w for c in kpis) == 12  # the row is exactly filled


def test_dynamics_line_over_time_ordered_by_time(model) -> None:
    spec = build_auto_spec(model, "dm.sales_daily")
    lines = [c for c in spec.charts if c.viz == Viz.LINE]
    assert len(lines) == 1
    q = lines[0].query
    assert q.dimensions == ["date"]
    assert q.order_by and q.order_by[0].by == "date" and q.order_by[0].dir == "asc"
    # the fixture records no `date` cardinality -> the line stays at raw day (unset grain)
    assert q.time_grain is None


def _with_date_card(model: SemanticModel, card: int | None) -> SemanticModel:
    """Return the model with the fact's `date` cardinality overridden (None removes it)."""
    fact = model.table("dm.sales_daily")
    assert fact is not None and fact.physical is not None
    card_map = dict(fact.physical.cardinality)
    if card is None:
        card_map.pop("date", None)
    else:
        card_map["date"] = card
    new_fact = fact.model_copy(
        update={"physical": fact.physical.model_copy(update={"cardinality": card_map})}
    )
    others = [t for t in model.tables if t.name != "dm.sales_daily"]
    return model.model_copy(update={"tables": [new_fact, *others]})


@pytest.mark.parametrize(
    "card,grain,title_hint",
    [
        (730, TimeGrain.MONTH, "по месяцам"),  # ~2 years of days -> monthly
        (300, TimeGrain.WEEK, "по неделям"),  # ~a year of days -> weekly
        (40, TimeGrain.DAY, None),  # a short series stays raw day
        (None, TimeGrain.DAY, None),  # unknown cardinality -> raw day
    ],
)
def test_dynamics_grain_from_time_cardinality(model, card, grain, title_hint) -> None:
    m = _with_date_card(model, card)
    spec = build_auto_spec(m, "dm.sales_daily")
    line = next(c for c in spec.charts if c.viz == Viz.LINE)
    if grain == TimeGrain.DAY:
        assert line.query.time_grain is None  # DAY left unset -> SQL unchanged for short series
        assert line.title.startswith("Динамика:")
    else:
        assert line.query.time_grain == grain
        assert title_hint in line.title
    assert validate_spec(spec, m) == []  # still a first-class, valid spec


def test_breakdowns_use_joined_attributes_not_raw_ids(model) -> None:
    spec = build_auto_spec(model, "dm.sales_daily")
    dims = _bare_dims(spec)
    # low-card attributes of the joined dim table appear...
    assert {"dm.stores.city", "dm.stores.region", "dm.stores.format"} & dims
    # ...and the high-card no-fk id never does
    assert "manager_id" not in dims


def test_structure_view_is_a_share_bar_not_a_pie(model) -> None:
    # the dashboard playbook bans pie/donut (angle/area read poorly) — the structure / part-to-
    # whole view is a sorted share-of-total bar instead
    spec = build_auto_spec(model, "dm.sales_daily")
    assert all(c.viz != Viz.PIE for c in spec.charts)

    share_bars = [
        c
        for c in spec.charts
        if c.viz == Viz.BAR
        and any(m.transform == MeasureTransform.SHARE_OF_TOTAL for m in c.query.measures)
    ]
    assert len(share_bars) == 1
    structure = share_bars[0]
    # it is the lowest-cardinality breakdown (format=3), sorted by the share descending
    assert structure.query.dimensions == ["dm.stores.format"]
    assert structure.query.order_by and structure.query.order_by[0].dir == "desc"

    # absolute (non-transformed) bars never reuse the structure column
    abs_bar_dims = {
        d
        for c in spec.charts
        if c.viz == Viz.BAR and not any(m.transform for m in c.query.measures)
        for d in c.query.dimensions
    }
    assert "dm.stores.format" not in abs_bar_dims


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


def _yoy_charts(spec) -> list:
    return [
        c
        for c in spec.charts
        if any(m.transform == MeasureTransform.YOY_PCT for m in c.query.measures)
    ]


def test_yoy_line_added_for_two_plus_years_of_history(model) -> None:
    # with 2+ years of daily history the dynamics grain is monthly, and a SECOND line shows the
    # hero measure's year-over-year change — a percent, ordered by time, at a non-day grain
    m = _with_date_card(model, 730)
    spec = build_auto_spec(m, "dm.sales_daily")
    yoy = _yoy_charts(spec)
    assert len(yoy) == 1
    chart = yoy[0]
    assert chart.viz == Viz.LINE
    assert chart.query.dimensions == ["date"]
    assert chart.query.time_grain == TimeGrain.MONTH  # yoy_pct requires a non-day grain
    assert chart.query.order_by and chart.query.order_by[0].by == "date"
    assert chart.title.startswith("Динамика г/г")
    assert validate_spec(spec, m) == []  # the yoy chart is a first-class, valid spec member


@pytest.mark.parametrize("card", [366, 300, 40, None])
def test_no_yoy_line_without_two_years(model, card) -> None:
    # a non-day grain alone is not enough: yoy lags a full year, so it needs MORE than ~12 months
    # or every point is the null baseline. 366 days buckets monthly but is only ~12 months (no
    # yoy); 300 is weekly, 40 daily, None unknown — none qualify.
    m = _with_date_card(model, card)
    spec = build_auto_spec(m, "dm.sales_daily")
    assert _yoy_charts(spec) == []
    assert validate_spec(spec, m) == []


def test_yoy_keeps_share_view_within_budget_on_real_model() -> None:
    # on the committed demo model (2 years of daily sales, 3 breakdowns + a format share) the
    # year-over-year line is prioritised above the third breakdown bar, so the structure (share)
    # view still fits the default 8-chart budget instead of being truncated away
    real = SemanticModel.load(Path(__file__).resolve().parents[1] / "semantic" / "model.yaml")
    spec = build_auto_spec(real, "dm.sales_daily")  # default max_charts == 8
    assert len(spec.charts) == 8
    share = [
        c
        for c in spec.charts
        if c.viz == Viz.BAR
        and any(m.transform == MeasureTransform.SHARE_OF_TOTAL for m in c.query.measures)
    ]
    abs_bars = [
        c
        for c in spec.charts
        if c.viz == Viz.BAR and not any(m.transform for m in c.query.measures)
    ]
    assert len(_yoy_charts(spec)) == 1  # the yoy line is present...
    assert len(share) == 1  # ...and it did not evict the structure / share view
    assert len(abs_bars) == 2  # trimmed from 3 breakdown bars to make room for yoy
    assert validate_spec(spec, real) == []
