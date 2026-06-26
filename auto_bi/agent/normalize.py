"""Deterministic chart-default normalization (dashboard adequacy B1).

After the LLM proposes a spec, a categorical chart (bar / stacked_bar / pie) over a
high-cardinality dimension with no measure ordering renders as a "wall of bars": the
SQL default limit is 5000 *unordered* rows. The propose prompt (rule 5) *asks* the LLM
to add ``order_by desc + limit 10-50``, but that is advisory — a stubborn LLM, a
hand-built spec, or a fields-first seed can omit it, leaving the wall.

This module enforces a sane top-N deterministically at the IR layer, so BOTH BI
adapters (Superset, DataLens) inherit the fix from one place (BI-agnostic — see
``docs/plans/2026-06-14-dashboard-adequacy-fixes.md`` B1). It is pure and idempotent,
and only acts where the author did *not* already express top-N intent.

Skips (left untouched):
- non-categorical viz (line/area/table/pivot/heatmap/big_number);
- a chart whose primary dimension is a TIME column — a column time-series is ordered by
  time, not ranked by value (forcing measure-desc + a tight limit would silently drop
  the tail of a chronological chart). The semantic model resolves the role, which is why
  this needs `model`, unlike the plan's bare `apply_chart_defaults(spec)` sketch;
- a chart that already orders by a measure (the author's explicit top-N).
"""

from __future__ import annotations

from auto_bi.ir.spec import (
    Aggregation,
    ChartQuery,
    ChartSpec,
    DashboardSpec,
    JoinSpec,
    Measure,
    OrderBy,
    Viz,
    column_alias,
    measure_alias,
)
from auto_bi.semantic.model import Column, ColumnRole, SemanticModel, Table

# viz whose x-axis / slices are categorical and degrade into a "wall" without top-N
_CATEGORICAL_VIZ = frozenset({Viz.BAR, Viz.STACKED_BAR, Viz.PIE})
# top-N caps: a pie with >12 slices is unreadable; bars tolerate a few more rows
_DEFAULT_TOP_N = 25
_PIE_TOP_N = 12


def _orders_by_measure(query: ChartQuery) -> bool:
    """True if the author already ordered by a measure (their explicit top-N intent).

    Mirrors SQL_GEN's order-target resolution exactly: a measure is addressable by its
    raw column, its canonical SELECT alias, or its label.
    """
    measure_refs: set[str] = set()
    for m in query.measures:
        measure_refs.add(m.column)
        measure_refs.add(measure_alias(m))
        if m.label:
            measure_refs.add(m.label)
    return any(ob.by in measure_refs for ob in query.order_by)


def _is_time_dimension(ref: str, base_table: str, model: SemanticModel) -> bool:
    """Role of a dimension reference, resolved against the model; False if unresolvable.

    `ref` is either bare (a column of the chart's base table) or fully qualified
    ('dm.stores.city'). Defensive: an unknown table/column resolves to non-time so an
    invalid spec is left for `validate_spec` to reject, not crashed here.
    """
    if "." in ref:
        table_name, _, col = ref.rpartition(".")
    else:
        table_name, col = base_table, ref
    table = model.table(table_name)
    if table is None:
        return False
    column = table.column(col)
    return column is not None and column.role == ColumnRole.TIME


def _normalize_chart(chart: ChartSpec, model: SemanticModel) -> ChartSpec:
    query = chart.query
    if chart.viz not in _CATEGORICAL_VIZ:
        return chart
    if not query.dimensions:  # no categorical axis to rank
        return chart
    if _is_time_dimension(query.dimensions[0], query.table, model):
        return chart  # column time-series: ordered by time, never ranked by value
    if _orders_by_measure(query):  # author already set an explicit top-N
        return chart

    cap = _PIE_TOP_N if chart.viz == Viz.PIE else _DEFAULT_TOP_N
    new_query = query.model_copy(
        update={
            "order_by": [OrderBy(by=measure_alias(query.measures[0]), dir="desc")],
            "limit": min(query.limit, cap),  # tighten only; never widen an explicit small limit
        }
    )
    return chart.model_copy(update={"query": new_query})


def apply_chart_defaults(spec: DashboardSpec, model: SemanticModel) -> DashboardSpec:
    """Return a copy of `spec` with deterministic top-N defaults on categorical charts.

    Pure and idempotent: a second pass is a no-op (after the first, the chart orders by
    a measure, so `_orders_by_measure` short-circuits it).
    """
    charts = [_normalize_chart(chart, model) for chart in spec.charts]
    return spec.model_copy(update={"charts": charts})


def is_horizontal_bar(chart: ChartSpec, model: SemanticModel) -> bool:
    """Whether a bar chart should render horizontally (categorical ranking) vs vertically.

    A ``bar``/``stacked_bar`` over a categorical (non-time) primary dimension is the
    canonical horizontal-bar case: long category labels (RU product/category names) get the
    full row width instead of truncating/rotating on a vertical x-axis. Convention
    (Cleveland/Few): a ranked *bar* chart is horizontal, a time *column* chart is vertical.

    Display-only and deterministic — no IR field (invariant 1: the LLM still emits only IR,
    orientation is decided here), no data probe. Both adapters call this with their model to
    choose orientation (Superset ``orientation``, DataLens ``bar`` vs ``column`` viz id),
    mirroring how B1/B2/B3 inherit a normalization from one place.

    Vertical is kept for a time x-axis (a column time-series reads left-to-right) and for any
    non-bar viz.
    """
    if chart.viz not in (Viz.BAR, Viz.STACKED_BAR):
        return False
    if not chart.query.dimensions:
        return False
    return not _is_time_dimension(chart.query.dimensions[0], chart.query.table, model)


def compact_decimals(measure: Measure, base_table: str, model: SemanticModel) -> int:
    """How many decimals a compact-formatted measure should display (0 or 1).

    An integer-typed aggregate is a count of rows/items/orders — its fractional digit is
    always ``,0`` noise (``115M``, not ``115,0M``). A money-like decimal/float sum keeps one
    decimal, where it can be meaningful (``236,1B``). ``COUNT``/``COUNT_DISTINCT`` count rows
    and are integer regardless of the underlying column.

    Display-only and deterministic (resolves the column type from the model, like
    ``is_horizontal_bar``/``_is_time_dimension``). Superset needs no equivalent: its d3
    ``.3~s`` already trims insignificant trailing zeros (``115M``); DataLens compact
    ``formatting`` has no trim mode, only a fixed ``precision``, so the adapter passes this.

    Safe default of 1 (keep a decimal) when the column type is unknown — never strips a
    digit on an unresolvable spec.
    """
    if measure.agg in (Aggregation.COUNT, Aggregation.COUNT_DISTINCT):
        return 0
    table = model.table(base_table)
    column = table.column(measure.column) if table is not None else None
    if column is None:
        return 1
    # ClickHouse/GP integer families never carry a meaningful fraction: Int8/16/32/64,
    # UInt*, "integer". Decimal/Numeric/Float*/Double/Real keep one decimal.
    return 0 if "int" in column.type.lower() else 1


# --- B3: label joins (raw FK id dimension -> human-readable name) -------------------
#
# A dimension that is a raw foreign key (`store_id`) renders as opaque integers. When the
# semantic model says that id points (via `Column.fk`) at a table holding a human-readable
# name AND that name is ~unique per id, rewrite the dimension to the joined name column and
# add the LEFT JOIN. Deterministic half of dashboard-adequacy B3, done at the IR layer so
# BOTH adapters inherit it — they already render joined dimensions (Phase 3 contract tests).
#
# Lossless by construction: the cardinality guard (`_label_column`) only swaps when the
# model records the name's distinct count as ~equal to the id's, so grouping by the name
# cannot merge distinct ids. Where names collide (or no cardinality evidence exists) the id
# is left in place — never a silent row merge (the correctness tradeoff B3 was gated on).

# column-name hints for a human-readable label of an entity (matched case-insensitively)
_LABEL_NAME_HINTS = ("name", "title", "label", "наименование", "название", "имя")
# the joined name must identify the id near-1:1; recorded cardinalities are approximate
# (ClickHouse uniqCombined), so allow a little slack rather than demanding exact equality
_LABEL_UNIQUENESS = 0.99
# dimension-like roles a label swap applies to (measures/filters keep the raw id)
_DIM_ROLES = ("dimensions", "series", "rows", "columns")


def _label_column(target: Table, id_col: str) -> Column | None:
    """A near-unique, human-readable name column of `target`, or None.

    Heuristic (a dimension column whose name looks like a label) gated by cardinality:
    the name's recorded distinct count must be ~equal to the id's, so grouping by the name
    does not merge distinct ids. No cardinality evidence -> no swap (conservative).

    Only `role=DIMENSION` columns are considered label candidates: a measure/time column
    is never a sensible axis label, and skipping one only forgoes readability (never wrong).
    When `id_col` itself has no recorded cardinality the guard falls back to `phys.rows`,
    which is >= any column's distinct count — so the >=0.99 ratio can only get *stricter*,
    never looser; the fallback can never cause an unsafe swap. Ties among label candidates
    are broken `name`-first then by column order (deterministic, but incidental).
    """
    phys = target.physical
    if phys is None or not phys.cardinality:
        return None
    id_card = phys.cardinality.get(id_col) or phys.rows
    if not id_card:
        return None
    candidates = [
        c
        for c in target.columns
        if c.name != id_col
        and c.role == ColumnRole.DIMENSION
        and any(h in c.name.lower() for h in _LABEL_NAME_HINTS)
    ]
    candidates.sort(key=lambda c: c.name.lower() != "name")  # prefer an exact "name"
    for c in candidates:
        label_card = phys.cardinality.get(c.name)
        if label_card and label_card >= _LABEL_UNIQUENESS * id_card:
            return c
    return None


def _label_join_for(ref: str, base_table: str, model: SemanticModel) -> tuple[str, JoinSpec] | None:
    """If bare dimension `ref` is a labelable FK id, return (qualified label ref, join).

    Only base-table id columns with an `fk` whose edge exists in the model are eligible
    (invariant 2 — we never invent a join). Already-qualified refs (a joined column) and
    non-FK dimensions are left alone.
    """
    if "." in ref:
        return None
    base = model.table(base_table)
    if base is None:
        return None
    col = base.column(ref)
    if col is None or col.role != ColumnRole.DIMENSION or not col.fk:
        return None
    target_name, _, target_id = col.fk.rpartition(".")
    target = model.table(target_name)
    if target is None:
        return None
    label = _label_column(target, target_id)
    if label is None:
        return None
    on_left = f"{base_table}.{ref}"
    edges = {frozenset((j.left, j.right)) for j in model.joins}
    if frozenset((on_left, col.fk)) not in edges:
        return None  # only joins validate_spec will accept
    join = JoinSpec(table=target_name, on_left=on_left, on_right=col.fk)
    return f"{target_name}.{label.name}", join


def _label_joins_chart(chart: ChartSpec, model: SemanticModel) -> ChartSpec:
    q = chart.query
    swaps: dict[str, str] = {}  # bare id ref -> qualified label ref
    added: dict[frozenset[str], JoinSpec] = {}
    for role in _DIM_ROLES:
        for ref in getattr(q, role):
            if ref in swaps:
                continue
            result = _label_join_for(ref, q.table, model)
            if result is None:
                continue
            label_ref, join = result
            swaps[ref] = label_ref
            added[frozenset((join.on_left, join.on_right))] = join
    if not swaps:
        return chart

    def rewrite(refs: list[str]) -> list[str]:
        return [swaps.get(r, r) for r in refs]

    new_roles = {role: rewrite(getattr(q, role)) for role in _DIM_ROLES}
    # bail if a swap would collide bare aliases (two columns sharing a bare name in one
    # chart is rejected by validate_spec) — keep the id rather than emit an invalid spec
    group_cols = [c for role in _DIM_ROLES for c in new_roles[role]]
    bare = [column_alias(c) for c in dict.fromkeys(group_cols)]
    if len(bare) != len(set(bare)):
        return chart

    existing = {frozenset((j.on_left, j.on_right)): j for j in q.joins}
    merged_joins = list({**existing, **added}.values())
    new_order_by = [
        ob.model_copy(update={"by": swaps[ob.by]}) if ob.by in swaps else ob for ob in q.order_by
    ]
    new_query = q.model_copy(update={**new_roles, "joins": merged_joins, "order_by": new_order_by})
    return chart.model_copy(update={"query": new_query})


def apply_label_joins(spec: DashboardSpec, model: SemanticModel) -> DashboardSpec:
    """Return a copy of `spec` with raw FK id dimensions replaced by their human-readable
    name (via a LEFT JOIN), where the model proves the name is ~unique per id (B3).

    Pure and idempotent: after a swap the dimension is a qualified joined ref, which
    `_label_join_for` skips, so a second pass is a no-op.
    """
    charts = [_label_joins_chart(chart, model) for chart in spec.charts]
    return spec.model_copy(update={"charts": charts})
