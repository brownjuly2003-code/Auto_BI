"""Auto-overview mode: a curated dashboard built deterministically from a datamart.

A THIRD entry into the pipeline next to free text and fields-first (invariant 6 / D8):
given just a table from the semantic model, assemble a *curated* overview dashboard —
not "every possible chart". The combinatorial "all measures × all dimensions × all viz"
is an anti-pattern (noise, and it negates the whole grounding-by-DM value); instead a
fixed, prioritised skeleton is filled from column roles and physical cardinality.

Fully deterministic — NO LLM (so it adds no GraceKelly dependency / prompt-eval gate;
S2 does not apply). It produces a plain ``DashboardSpec`` that flows through the exact
same validate → normalize → SQL-guard → adapter path; `apply_label_joins` /
`apply_chart_defaults` (run by `compile_and_build`) finish the job (raw FK ids → names,
top-N caps). Invariants 1-8 are untouched.

Recipe (truncated to `max_charts` by priority P1..P5):
  P1 KPI        — one big_number per measure, plus a scalar year-over-year KPI for the hero
                  measure when there is 2+ years of history (the latest period vs the same period
                  a year back, `Measure.compare` — a percent tile "Выручка, г/г: +12,4 %"). It
                  spends one dashboard slot, so the third breakdown bar is trimmed and the
                  structure (share) view still survives the max_charts cut.
  P2 dynamics   — primary measure as a line over the time column (if any), bucketed to a
                  readable grain (day/week/month) when the daily series is long
  P3 breakdowns — primary measure as a bar over each "good breakdown" (a dimension whose
                  cardinality is in [2..CARD_MAX], including attributes of adjacent dim
                  tables reached by a model-edge JOIN: city / region / format / ...)
  P4 structure  — each category's SHARE of the total (a sorted share-of-total bar) over the
                  lowest-cardinality breakdown — part-to-whole WITHOUT a pie (the dashboard
                  playbook bans pie/donut: angle/area read poorly, 31% vs 34% look equal)
  P5 detail     — primary measure as a table over the top breakdown

Hard stops that make it a dashboard, not a dump: aggregate only `role=measure` (or a
synthetic COUNT when the table has none); a breakdown must be genuinely categorical
(cardinality in range — manager_id=16825 is dropped); a JOIN is only ever a `model.joins`
edge (invariant 2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from auto_bi.ir.spec import (
    ChartQuery,
    ChartSpec,
    DashboardFilter,
    DashboardSpec,
    FilterOp,
    JoinSpec,
    LayoutHint,
    Measure,
    MeasureTransform,
    OrderBy,
    QueryFilter,
    ScalarCompare,
    ScalarCompareKind,
    TargetBI,
    TimeGrain,
    Viz,
    is_additive_agg,
    measure_alias,
)
from auto_bi.semantic.model import (
    Additivity,
    Aggregation,
    Column,
    ColumnRole,
    SemanticModel,
    Table,
)

# a breakdown is readable as a full categorical axis only in this cardinality band;
# below 2 it is constant, above CARD_MAX it is a "wall" better served by a top-N id chart
_CARD_MIN = 2
_CARD_MAX = 50
_STRUCTURE_CARD_MAX = 12  # a part-to-whole view with more than ~12 categories is unreadable

_DEFAULT_MAX_CHARTS = 8
_MAX_KPIS = 4
_MAX_BAR_BREAKDOWNS = 3

# the overview opens preset to this recent window (B5): recent enough to read as "now",
# but a full year so the year-over-year hero KPI has its comparison in view. Relative token
# parsed natively by the BI (see native_filters.superset_time_range); user widens to full
# history on the dashboard. Change here => tests/test_autospec.py::test_auto_overview_period.
_OVERVIEW_PERIOD = "last 12 months"


@dataclass(frozen=True)
class _Breakdown:
    """A categorical axis candidate: a model column plus the JOIN to reach it (if any)."""

    ref: str  # bare base column ("category") or qualified joined column ("dm.stores.city")
    join: JoinSpec | None
    card: int
    human: str  # short human label for the chart title ("Город")


def _clean_title(text: str) -> str:
    """Drop a trailing technical grain annotation a modeler left in the table description
    ('... (грейн: date, store_id, product_id)'): internal metadata, not a user-facing
    dashboard title. A non-technical parenthetical (e.g. '(РФ)') is kept."""
    return re.sub(r"\s*\((?:грейн|grain)\b[^)]*\)\s*$", "", text, flags=re.IGNORECASE).strip()


def _short(col: Column) -> str:
    """A short human label from a column's description (up to the first , ( : ), else name."""
    desc = col.description.strip()
    if not desc:
        return col.name
    for sep in (",", "(", ":", " —", " -"):
        idx = desc.find(sep)
        if idx > 0:
            desc = desc[:idx]
    return desc.strip()


def _measures(table: Table) -> list[Column]:
    return [c for c in table.columns if c.role == ColumnRole.MEASURE]


def _time_column(table: Table) -> Column | None:
    return next((c for c in table.columns if c.role == ColumnRole.TIME), None)


def _cardinality(table: Table, col: str) -> int | None:
    if table.physical is None:
        return None
    return table.physical.cardinality.get(col)


# a line chart reads as a trend with roughly 12-60 points, not 730 noisy days. The dynamics
# line is bucketed to a coarser grain when the time axis carries many distinct values, chosen
# from the time column's recorded distinct-count (model cardinality): a short series stays raw.
_GRAIN_WEEK_MIN = 62  # > ~2 months of daily points -> weekly buckets read better
_GRAIN_MONTH_MIN = 365  # > ~a year of daily points -> monthly buckets

_DYNAMICS_TITLE = {
    TimeGrain.DAY: "Динамика",
    TimeGrain.WEEK: "Динамика по неделям",
    TimeGrain.MONTH: "Динамика по месяцам",
}


def _auto_time_grain(table: Table, time_col: Column) -> TimeGrain:
    """Pick a readable bucket for the dynamics line from the time column's distinct-value count.

    A long daily series (e.g. 730 days) reads as noise on a line; bucketing to week/month makes
    it a trend (time_grain, deterministic — no LLM). The count comes from the model's recorded
    cardinality (an introspected distinct count), so this stays model-driven. Idempotent on an
    already-coarse axis (toStartOfMonth of monthly dates is a no-op) and falls back to raw day
    when the cardinality is unknown — never guesses a grain it cannot justify."""
    card = _cardinality(table, time_col.name)
    if card is None or card <= _GRAIN_WEEK_MIN:
        return TimeGrain.DAY
    if card <= _GRAIN_MONTH_MIN:
        return TimeGrain.WEEK
    return TimeGrain.MONTH


# a year-over-year line lags a full year of periods, so it needs a non-day grain (to know how many
# periods make a year) and more than a year of them — the first year is the null baseline. Autospec
# buckets to month at most, so yoy fires only at month grain with >= ~13 months: 13 guarantees one
# real point, the demo's 24 months give a full year of them.
_YOY_MIN_PERIODS = 13


def _yoy_applicable(grain: TimeGrain, time_card: int | None) -> bool:
    """Whether a year-over-year dynamics line is worth adding for this time axis.

    Needs the month grain autospec picks for long series and enough of those months that the
    year-back lag yields real comparison points (not an all-null first year). The month count is
    estimated from the time column's distinct-day count — the same model-recorded cardinality the
    grain itself is chosen from — so this stays deterministic and model-driven (no LLM)."""
    if time_card is None or grain != TimeGrain.MONTH:
        return False
    est_months = time_card * 12 // 365
    return est_months >= _YOY_MIN_PERIODS


def _to_measure(col: Column) -> Measure:
    # empty label => SQL alias is "<agg>_<column>" (measure_alias); chart titles are human.
    # A non-additive column (rate/price) with no modeled agg must not fall back to SUM —
    # validation would reject the spec it lands in (P1-6), so the fallback is AVG.
    fallback = Aggregation.AVG if col.additivity == Additivity.NON_ADDITIVE else Aggregation.SUM
    return Measure(column=col.name, agg=col.agg or fallback, label="")


def _share_of(measure: Measure) -> Measure:
    """Part-to-whole variant of a measure: each category's share of the column total.

    Computed deterministically as a window over the base aggregate (invariant 1) and rendered
    as a percent (`is_percent_measure`). This is the structure view that replaces a pie — the
    dashboard playbook bans pie/donut (angle/area read poorly), so part-to-whole is a sorted
    share bar instead. The label is cleared so the alias reflects the transform.
    """
    return measure.model_copy(update={"transform": MeasureTransform.SHARE_OF_TOTAL, "label": ""})


def _synthetic_count(table: Table) -> Measure:
    """A COUNT measure for a table with no `role=measure` columns (e.g. a reference dim)."""
    anchor = table.grain[0] if table.grain else table.columns[0].name
    return Measure(column=anchor, agg=Aggregation.COUNT, label="cnt")


_GRID_COLS = 12  # dashboard grid width (mirrors form_data.GRID_WIDTH)


def _fill_trailing_row(charts: list[ChartSpec]) -> None:
    """Widen a chart left ALONE on the final row to full width, so the overview has no ragged
    right edge (dashboard-craft §5 "единая ширина рядов"). Common after the max_charts cut drops
    the detail table and leaves a lone share bar. Mirrors the adapters' left-to-right 12-column
    packing; autospec sets no row hints, so packing is purely by width."""
    row_start, used = 0, 0
    for i, chart in enumerate(charts):
        w = chart.layout_hint.w
        if used and used + w > _GRID_COLS:
            row_start, used = i, w
        else:
            used += w
    if len(charts) - row_start == 1 and charts[-1].layout_hint.w < _GRID_COLS:
        charts[-1].layout_hint.w = _GRID_COLS


def _good_breakdowns(table: Table, model: SemanticModel) -> list[_Breakdown]:
    """Categorical axes worth charting: base low-card dims + joined dim-table attributes.

    Sorted by cardinality ascending so truncation keeps the most aggregated, diverse
    set. Joins are only emitted for FK edges that exist in `model.joins` (invariant 2).
    """
    edges = {frozenset((j.left, j.right)) for j in model.joins}
    out: list[_Breakdown] = []

    # base-table dimension columns that are themselves low-cardinality (not FK ids)
    for c in table.columns:
        if c.role != ColumnRole.DIMENSION or c.fk:
            continue
        card = _cardinality(table, c.name)
        if card is not None and _CARD_MIN <= card <= _CARD_MAX:
            out.append(_Breakdown(ref=c.name, join=None, card=card, human=_short(c)))

    # attributes of adjacent dimension tables reached via a foreign key
    for fk_col in table.columns:
        if fk_col.role != ColumnRole.DIMENSION or not fk_col.fk:
            continue
        on_left = f"{table.name}.{fk_col.name}"
        if frozenset((on_left, fk_col.fk)) not in edges:
            continue
        target_name = fk_col.fk.rpartition(".")[0]
        target = model.table(target_name)
        if target is None:
            continue
        join = JoinSpec(table=target_name, on_left=on_left, on_right=fk_col.fk)
        for tc in target.columns:
            if tc.role != ColumnRole.DIMENSION or tc.name in target.grain:
                continue  # skip the id/grain; high-card name columns fall out by CARD_MAX
            card = _cardinality(target, tc.name)
            if card is not None and _CARD_MIN <= card <= _CARD_MAX:
                out.append(
                    _Breakdown(
                        ref=f"{target_name}.{tc.name}", join=join, card=card, human=_short(tc)
                    )
                )

    out.sort(key=lambda b: (b.card, b.ref))
    return out


def _bar(table: str, measure: Measure, b: _Breakdown, title: str) -> ChartQuery:
    return ChartQuery(
        table=table,
        dimensions=[b.ref],
        measures=[measure],
        joins=[b.join] if b.join else [],
        order_by=[OrderBy(by=measure_alias(measure), dir="desc")],
        limit=min(b.card, 25),
    )


def build_auto_spec(
    model: SemanticModel,
    table_name: str,
    *,
    max_charts: int = _DEFAULT_MAX_CHARTS,
    target_bi: TargetBI = TargetBI.SUPERSET,
) -> DashboardSpec:
    """Curated overview dashboard for one datamart, from the semantic model alone.

    Raises ValueError for an unknown table or a table with no chartable columns.
    The returned spec passes `validate_spec` and is idempotent under the normalize pass.
    """
    table = model.table(table_name)
    if table is None:
        known = ", ".join(t.name for t in model.tables)
        raise ValueError(f"unknown table {table_name!r} (known: {known})")

    measures = _measures(table)
    measure_objs = [_to_measure(c) for c in measures] or [_synthetic_count(table)]
    primary = measure_objs[0]
    time_col = _time_column(table)
    time_grain = _auto_time_grain(table, time_col) if time_col is not None else TimeGrain.DAY
    time_card = _cardinality(table, time_col.name) if time_col is not None else None
    yoy_on = time_col is not None and _yoy_applicable(time_grain, time_card)
    breakdowns = _good_breakdowns(table, model)

    charts: list[ChartSpec] = []
    primary_title = _short(measures[0]) if measures else "Количество"

    # P1 — KPI per measure (when there are no real measures, a single synthetic COUNT pairs with a
    # None column -> the "Количество" title), plus a year-over-year KPI for the hero measure when
    # there is 2+ years of history (S14): the hero's yoy % tile sits right after its level so the
    # pair reads "Выручка: 236 млрд" + "Выручка, г/г: +12,4 %" (a scalar period-compare — the
    # latest month vs the same month a year back, `Measure.compare`; display-only, no LLM). The KPI
    # cards are identical (same viz, same height) and fill their row evenly: a uniform width 12 // n
    # so the cards span the grid without a ragged gap (dashboard-craft §3). n in 1..4 all divide 12.
    kpi_cells: list[tuple[str, Measure]] = []
    cols: list[Column | None] = list(measures) if measures else [None]
    for col, m in zip(cols, measure_objs, strict=True):
        kpi_cells.append((_short(col) if col is not None else "Количество", m))
    if yoy_on and time_col is not None:
        hero_yoy = primary.model_copy(
            update={
                "compare": ScalarCompare(
                    column=time_col.name, grain=time_grain, kind=ScalarCompareKind.YOY
                ),
                "label": "",
            }
        )
        kpi_cells.insert(1, (f"{primary_title}, г/г", hero_yoy))
    kpi_cells = kpi_cells[:_MAX_KPIS]
    kpi_w = 12 // len(kpi_cells)
    for title, m in kpi_cells:
        charts.append(
            ChartSpec(
                id="",
                title=title,
                viz=Viz.BIG_NUMBER,
                query=ChartQuery(table=table_name, measures=[m]),
                layout_hint=LayoutHint(w=kpi_w, h=4),
            )
        )

    # the lowest-cardinality breakdown becomes a part-to-whole SHARE bar (structure), the rest
    # absolute bars — so the same column is never both (`breakdowns` is sorted card asc)
    share_break = (
        breakdowns[0] if breakdowns and breakdowns[0].card <= _STRUCTURE_CARD_MAX else None
    )
    # the year-over-year view spends one dashboard slot (the hero yoy KPI in P1) — trade the third
    # breakdown bar for it so the structure (share) view still survives the max_charts cut
    max_bars = _MAX_BAR_BREAKDOWNS - 1 if yoy_on else _MAX_BAR_BREAKDOWNS
    bar_breaks = [b for b in breakdowns if b is not share_break][:max_bars]

    # P2 — dynamics over time (ordered by time, never top-N'd). A long daily series is bucketed
    # to a readable grain so the line shows a trend, not 730 noisy points (time_grain). DAY is
    # left unset so a short series' SQL is unchanged.
    if time_col is not None:
        dyn_query = ChartQuery(
            table=table_name,
            dimensions=[time_col.name],
            measures=[primary],
            order_by=[OrderBy(by=time_col.name, dir="asc")],
        )
        if time_grain != TimeGrain.DAY:
            dyn_query = dyn_query.model_copy(update={"time_grain": time_grain})
        charts.append(
            ChartSpec(
                id="",
                title=f"{_DYNAMICS_TITLE[time_grain]}: {primary_title}",
                viz=Viz.LINE,
                query=dyn_query,
                layout_hint=LayoutHint(w=12, h=6),
            )
        )
        # NB: the year-over-year view is now the compact hero yoy KPI in P1 (S14), not a second
        # full-width line — it delivers the same headline vs-a-year-back insight in one tile and
        # frees the slot for the structure (share) view. The yoy_pct LINE remains available via
        # fields-first / an explicit spec; the curated overview just no longer spends a full row on
        # it (dashboard-not-presentation: a curated overview, not every chartable view).

    # P3 — bar breakdowns (low-card categorical axes)
    for b in bar_breaks:
        charts.append(
            ChartSpec(
                id="",
                title=f"{primary_title} — {b.human}",
                viz=Viz.BAR,
                query=_bar(table_name, primary, b, b.human),
                layout_hint=LayoutHint(w=6, h=6),
            )
        )

    # P4 — structure: each category's share of total as a sorted bar (part-to-whole, no pie).
    # The share is a window over the base aggregate; ordering by it equals ordering by the base
    # measure (the total is constant), so the bar still reads top-down by contribution.
    # Only for an additive primary: a share of avg(price) divides by a sum of averages,
    # which is not a part-to-whole of anything (P1-6).
    if share_break is not None and is_additive_agg(primary.agg):
        share = _share_of(primary)
        charts.append(
            ChartSpec(
                id="",
                title=f"Доля: {share_break.human}",
                viz=Viz.BAR,
                query=ChartQuery(
                    table=table_name,
                    dimensions=[share_break.ref],
                    measures=[share],
                    joins=[share_break.join] if share_break.join else [],
                    order_by=[OrderBy(by=measure_alias(share), dir="desc")],
                    limit=min(share_break.card, _STRUCTURE_CARD_MAX),
                ),
                layout_hint=LayoutHint(w=6, h=6),
            )
        )

    # P5 — detail table over the widest (highest-card) good breakdown
    if breakdowns:
        b = breakdowns[-1]
        charts.append(
            ChartSpec(
                id="",
                title=f"Детализация: {b.human}",
                viz=Viz.TABLE,
                query=_bar(table_name, primary, b, b.human),
                layout_hint=LayoutHint(w=12, h=8),
            )
        )

    if not charts:
        raise ValueError(f"table {table_name!r} has no chartable columns")

    charts = charts[:max_charts]
    _fill_trailing_row(charts)  # no ragged right edge on the last row (§5)
    for i, chart in enumerate(charts, start=1):
        chart.id = f"auto{i}"

    # P1-1: bake the overview period into EACH chart's query.filters (SQL WHERE), not only into
    # the native dashboard control. A native time filter can only re-scope charts whose grain
    # exposes the time column (typically the dynamics line) — KPIs and categorical breakdowns
    # stay all-time, so the user sees contradictory numbers. Baking a GTE "last N …" filter
    # (compiled by SQL_GEN to a dialect-native bound) makes every chart honour the same window.
    # Exception: a yoy_pct *series* needs a full year of prior buckets for every point — baking
    # the short window would null out the lag; those charts keep full history (the interactive
    # native control still covers them when the grain includes time).
    if time_col is not None:
        period_filter = QueryFilter(column=time_col.name, op=FilterOp.GTE, value=_OVERVIEW_PERIOD)
        baked: list[ChartSpec] = []
        for chart in charts:
            if any(m.transform == MeasureTransform.YOY_PCT for m in chart.query.measures):
                baked.append(chart)
                continue
            q = chart.query.model_copy(update={"filters": [*chart.query.filters, period_filter]})
            baked.append(chart.model_copy(update={"query": q}))
        charts = baked

    # Interactive period control (B5): adapters compile this into a native time filter scoped
    # to charts that expose the time column. Opens preset to _OVERVIEW_PERIOD so the user can
    # widen/narrow on the dashboard; the baked query.filters above already align the default
    # numbers across the whole board.
    filters: list[DashboardFilter] = []
    if time_col is not None:
        filters.append(
            DashboardFilter(
                column=f"{table_name}.{time_col.name}",
                type="time_range",
                default=_OVERVIEW_PERIOD,
            )
        )

    return DashboardSpec(
        title=_clean_title(f"Обзор: {table.description or table_name}"),
        target_bi=target_bi,
        filters=filters,
        charts=charts,
    )
