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
  P1 KPI        — one big_number per measure
  P2 dynamics   — primary measure as a line over the time column (if any)
  P3 breakdowns — primary measure as a bar over each "good breakdown" (a dimension whose
                  cardinality is in [2..CARD_MAX], including attributes of adjacent dim
                  tables reached by a model-edge JOIN: city / region / format / ...)
  P4 structure  — primary measure as a pie over the lowest-cardinality breakdown
  P5 detail     — primary measure as a table over the top breakdown

Hard stops that make it a dashboard, not a dump: aggregate only `role=measure` (or a
synthetic COUNT when the table has none); a breakdown must be genuinely categorical
(cardinality in range — manager_id=16825 is dropped); a JOIN is only ever a `model.joins`
edge (invariant 2).
"""

from __future__ import annotations

from dataclasses import dataclass

from auto_bi.ir.spec import (
    ChartQuery,
    ChartSpec,
    DashboardFilter,
    DashboardSpec,
    JoinSpec,
    LayoutHint,
    Measure,
    OrderBy,
    TargetBI,
    Viz,
    measure_alias,
)
from auto_bi.semantic.model import (
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
_PIE_CARD_MAX = 12  # a pie with more than ~12 slices is unreadable

_DEFAULT_MAX_CHARTS = 8
_MAX_KPIS = 4
_MAX_BAR_BREAKDOWNS = 3


@dataclass(frozen=True)
class _Breakdown:
    """A categorical axis candidate: a model column plus the JOIN to reach it (if any)."""

    ref: str  # bare base column ("category") or qualified joined column ("dm.stores.city")
    join: JoinSpec | None
    card: int
    human: str  # short human label for the chart title ("Город")


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


def _to_measure(col: Column) -> Measure:
    # empty label => SQL alias is "<agg>_<column>" (measure_alias); chart titles are human
    return Measure(column=col.name, agg=col.agg or Aggregation.SUM, label="")


def _synthetic_count(table: Table) -> Measure:
    """A COUNT measure for a table with no `role=measure` columns (e.g. a reference dim)."""
    anchor = table.grain[0] if table.grain else table.columns[0].name
    return Measure(column=anchor, agg=Aggregation.COUNT, label="cnt")


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
    breakdowns = _good_breakdowns(table, model)

    charts: list[ChartSpec] = []

    # P1 — KPI per measure
    for col, m in zip(measures or [None], measure_objs, strict=True):
        title = _short(col) if col is not None else "Количество"
        charts.append(
            ChartSpec(
                id="",
                title=title,
                viz=Viz.BIG_NUMBER,
                query=ChartQuery(table=table_name, measures=[m]),
                layout_hint=LayoutHint(w=3, h=4),
            )
        )
        if len(charts) >= _MAX_KPIS:
            break

    primary_title = _short(measures[0]) if measures else "Количество"

    # the lowest-cardinality breakdown is shown as a pie (structure), the rest as bars —
    # so the same column is never both a bar and a pie (`breakdowns` is sorted card asc)
    pie_break = breakdowns[0] if breakdowns and breakdowns[0].card <= _PIE_CARD_MAX else None
    bar_breaks = [b for b in breakdowns if b is not pie_break][:_MAX_BAR_BREAKDOWNS]

    # P2 — dynamics over time (ordered by time, never top-N'd)
    if time_col is not None:
        charts.append(
            ChartSpec(
                id="",
                title=f"Динамика: {primary_title}",
                viz=Viz.LINE,
                query=ChartQuery(
                    table=table_name,
                    dimensions=[time_col.name],
                    measures=[primary],
                    order_by=[OrderBy(by=time_col.name, dir="asc")],
                ),
                layout_hint=LayoutHint(w=12, h=6),
            )
        )

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

    # P4 — structure (pie over the single lowest-card breakdown)
    if pie_break is not None:
        charts.append(
            ChartSpec(
                id="",
                title=f"Доля: {pie_break.human}",
                viz=Viz.PIE,
                query=ChartQuery(
                    table=table_name,
                    dimensions=[pie_break.ref],
                    measures=[primary],
                    joins=[pie_break.join] if pie_break.join else [],
                    order_by=[OrderBy(by=measure_alias(primary), dir="desc")],
                    limit=min(pie_break.card, _PIE_CARD_MAX),
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
    for i, chart in enumerate(charts, start=1):
        chart.id = f"auto{i}"

    # an interactive period control for the overview: the Superset/DataLens adapters compile
    # spec.filters into a native time filter scoped to the charts that expose the time column
    # (KPIs/breakdowns over the fact). No preset range — the adapter applies no default value,
    # so claiming one here would be misleading; the user picks the period on the dashboard.
    filters: list[DashboardFilter] = []
    if time_col is not None:
        filters.append(DashboardFilter(column=f"{table_name}.{time_col.name}", type="time_range"))

    return DashboardSpec(
        title=f"Обзор: {table.description or table_name}",
        target_bi=target_bi,
        filters=filters,
        charts=charts,
    )
