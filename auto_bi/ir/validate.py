"""Spec validation against the semantic model (invariant 2).

Runs BEFORE any BI/SQL work. Unknown table/column -> error list for the repair loop;
no silent fixes ever. Returns human-readable errors the LLM can act on.
"""

from auto_bi.ir.spec import (
    ChartSpec,
    DashboardSpec,
    FilterOp,
    MeasureTransform,
    Viz,
    column_alias,
    measure_alias,
)
from auto_bi.semantic.model import Aggregation, ColumnRole, SemanticModel, Table

# numeric aggregations make no sense over dimension columns; count/count_distinct
# are legal over anything. Rejecting here keeps the error actionable for the repair
# loop instead of a late EXPLAIN failure in compile_and_build.
_NUMERIC_AGGS = frozenset({Aggregation.SUM, Aggregation.AVG, Aggregation.MIN, Aggregation.MAX})

# transforms whose window orders by the chart's first dimension — that dimension must be a
# TIME column (a period-over-period / running total is only meaningful along a time axis)
_ORDERED_TRANSFORMS = frozenset(
    {MeasureTransform.POP_ABS, MeasureTransform.POP_PCT, MeasureTransform.RUNNING_TOTAL}
)
# viz with no single ordered category axis the SQL_GEN window can sort by
_TRANSFORM_UNSUPPORTED_VIZ = frozenset({Viz.BIG_NUMBER, Viz.PIVOT, Viz.HEATMAP})


def validate_spec(spec: DashboardSpec, model: SemanticModel) -> list[str]:
    errors: list[str] = []

    chart_ids = [c.id for c in spec.charts]
    if len(chart_ids) != len(set(chart_ids)):
        errors.append(f"chart ids are not unique: {chart_ids}")

    for f in spec.filters:
        if _resolve_qualified_column(f.column, model) is None:
            # common LLM slip: a bare column where the dashboard filter needs "schema.table.column"
            owners = [t.name for t in model.tables if t.column(f.column) is not None]
            hint = f" — укажи полное имя: {owners[0] + '.' + f.column!r}" if owners else ""
            errors.append(f"dashboard filter references unknown column: {f.column!r}{hint}")

    for chart in spec.charts:
        errors.extend(_validate_chart(chart, model))

    return errors


def _validate_chart(chart: ChartSpec, model: SemanticModel) -> list[str]:
    prefix = f"chart {chart.id!r}"
    table = model.table(chart.query.table)
    if table is None:
        known = ", ".join(t.name for t in model.tables)
        return [f"{prefix}: unknown table {chart.query.table!r} (known tables: {known})"]

    errors: list[str] = []

    # --- joins (explicit, must mirror a semantic-model edge — invariant 2) -----
    model_edges = {frozenset((j.left, j.right)) for j in model.joins}
    joined: dict[str, Table] = {}
    for j in chart.query.joins:
        join_table = model.table(j.table)
        if join_table is None:
            errors.append(f"{prefix}: join references unknown table {j.table!r}")
            continue
        left_table, _, left_col = j.on_left.rpartition(".")
        right_table, _, right_col = j.on_right.rpartition(".")
        if left_table != table.name or table.column(left_col) is None:
            errors.append(
                f"{prefix}: join on_left {j.on_left!r} must be a column of the chart's "
                f"table {table.name}"
            )
        elif right_table != j.table or join_table.column(right_col) is None:
            errors.append(f"{prefix}: join on_right {j.on_right!r} must be a column of {j.table}")
        elif frozenset((j.on_left, j.on_right)) not in model_edges:
            known = "; ".join(f"{e.left} = {e.right}" for e in model.joins) or "нет"
            errors.append(
                f"{prefix}: join {j.on_left} = {j.on_right} is not an edge of the semantic "
                f"model (допустимые джойны: {known})"
            )
        else:
            joined[j.table] = join_table

    def _unknown(col: str, where: str) -> str:
        # common LLM slips, still rejected (no silent fixes) but with an exact hint:
        # a base-table-qualified name where a bare one is required, or a bare name
        # that actually lives in a joinable table and needs qualification + a join
        hint = ""
        if col.startswith(f"{table.name}.") and table.column(col.removeprefix(f"{table.name}.")):
            hint = f" — укажи имя без префикса таблицы: {col.removeprefix(f'{table.name}.')!r}"
        elif "." not in col:
            owners = [
                t.name for t in model.tables if t.name != table.name and t.column(col) is not None
            ]
            if owners:
                hint = (
                    f" — колонка есть в {owners[0]}: укажи {owners[0] + '.' + col!r} "
                    "и добавь соответствующий JOIN в query.joins"
                )
        return f"{prefix}: unknown {where} {col!r} in {table.name}{hint}"

    def _dim_ok(col: str, where: str) -> None:
        if "." in col:
            ref_table, _, ref_col = col.rpartition(".")
            if ref_table == table.name:
                errors.append(_unknown(col, where))  # base columns are written bare
            elif ref_table not in joined:
                errors.append(
                    f"{prefix}: {where} {col!r} references table {ref_table!r} without a "
                    "matching entry in query.joins"
                )
            elif joined[ref_table].column(ref_col) is None:
                errors.append(f"{prefix}: unknown {where} {col!r} in {ref_table}")
        elif table.column(col) is None:
            errors.append(_unknown(col, where))

    role_fields = (
        ("dimension", chart.query.dimensions),
        ("series", chart.query.series),
        ("pivot row", chart.query.rows),
        ("pivot column", chart.query.columns),
    )
    for role, cols in role_fields:
        for col in cols:
            _dim_ok(col, f"{role} column")

    group_cols = chart.query.group_columns()
    aliases = [column_alias(c) for c in group_cols]
    if len(aliases) != len(set(aliases)):
        dupes = sorted({a for a in aliases if aliases.count(a) > 1})
        errors.append(
            f"{prefix}: dimension columns collide by bare name {dupes} — "
            "одинаковые имена из разных таблиц в одном чарте не поддерживаются"
        )
    used_tables = {c.rpartition(".")[0] for c in group_cols if "." in c}
    used_tables.update(
        qf.column.rpartition(".")[0] for qf in chart.query.filters if "." in qf.column
    )
    for j in chart.query.joins:
        if j.table in joined and j.table not in used_tables:
            errors.append(f"{prefix}: join to {j.table} is declared but no column of it is used")

    def _check_measure_col(col_name: str, agg: Aggregation, what: str) -> None:
        # column/role rules shared by a measure and (for a ratio) its denominator
        col = table.column(col_name)
        if col is None:
            errors.append(_unknown(col_name, what))
        elif col.role == ColumnRole.TIME:
            errors.append(f"{prefix}: time column {col_name!r} cannot be a measure")
        elif agg in _NUMERIC_AGGS and col.role != ColumnRole.MEASURE:
            errors.append(
                f"{prefix}: {agg.value} over {col.role.value} column "
                f"{col_name!r} — для неё допустимы только count/count_distinct"
            )

    for measure in chart.query.measures:
        _check_measure_col(measure.column, measure.agg, "measure column")
        if measure.denominator is not None:
            _check_measure_col(
                measure.denominator.column, measure.denominator.agg, "denominator column"
            )

    for qf in chart.query.filters:
        _dim_ok(qf.column, "filter column")
        if qf.op == FilterOp.IN and isinstance(qf.value, list) and not qf.value:
            errors.append(f"{prefix}: filter on {qf.column!r} uses IN with an empty value list")

    orderable = set(group_cols)  # any selected dimension-like column
    orderable.update(aliases)  # joined dims are addressable by their bare SELECT alias
    for m in chart.query.measures:
        orderable.add(m.column)
        orderable.add(measure_alias(m))  # the SELECT alias SQL_GEN actually orders by
        if m.label:
            orderable.add(m.label)
    for ob in chart.query.order_by:
        if ob.by not in orderable:
            errors.append(
                f"{prefix}: order_by {ob.by!r} is neither a dimension nor a measure of the chart"
            )

    errors.extend(_validate_transforms(chart, model, prefix))
    errors.extend(_validate_ratios(chart, prefix))
    errors.extend(_validate_viz_shape(chart, prefix))
    return errors


def _first_dimension_is_time(chart: ChartSpec, model: SemanticModel) -> bool:
    """Whether the chart's first dimension resolves to a TIME column (the window x-axis).

    Defensive: an unknown table/column resolves to non-time, leaving the underlying
    unknown-column error (already emitted above) as the actionable message."""
    dims = chart.query.dimensions
    if not dims:
        return False
    ref = dims[0]
    table_name, _, col = ref.rpartition(".") if "." in ref else ("", "", ref)
    table = model.table(table_name) if table_name else model.table(chart.query.table)
    column = table.column(col) if table is not None else None
    return column is not None and column.role == ColumnRole.TIME


def _validate_transforms(chart: ChartSpec, model: SemanticModel, prefix: str) -> list[str]:
    """Shape rules for analytical transforms (PoP / share / running total).

    A period-over-period or running total is computed as a window ordered by the chart's
    first dimension, so that dimension must be a TIME column and the viz must have a single
    ordered axis (not a KPI / pivot / heatmap). share_of_total needs at least one dimension
    to be a share *of* — without grouping the share is a trivial 100%.
    """
    transformed = [m for m in chart.query.measures if m.transform is not None]
    if not transformed:
        return []
    if chart.viz in _TRANSFORM_UNSUPPORTED_VIZ:
        names = ", ".join(sorted({m.transform.value for m in transformed if m.transform}))
        return [
            f"{prefix}: преобразования мер ({names}) не поддерживаются для {chart.viz.value} — "
            "нужен график с одной упорядоченной осью (line/area/bar/pie/table)"
        ]
    errors: list[str] = []
    first_is_time = _first_dimension_is_time(chart, model)
    for m in transformed:
        if m.transform in _ORDERED_TRANSFORMS and not first_is_time:
            errors.append(
                f"{prefix}: преобразование {m.transform.value!r} требует, чтобы первое "
                "измерение было колонкой времени (ось x по времени)"
            )
        elif m.transform == MeasureTransform.SHARE_OF_TOTAL and not chart.query.dimensions:
            errors.append(
                f"{prefix}: преобразование 'share_of_total' требует хотя бы одно измерение"
            )
    return errors


def _validate_ratios(chart: ChartSpec, prefix: str) -> list[str]:
    """Structural rules for ratio measures (`Measure.denominator`).

    A ratio is `agg(num) / agg(den)`, both aggregated in the same GROUP BY (SQL_GEN divides
    them in floating point). It is mutually exclusive with a window transform, and the
    denominator is a plain aggregate — no nested ratio and no transform of its own (neither
    has a defined meaning here). Column/role of the denominator is checked with the numerator
    in `_validate_chart`; ratio is allowed on every viz (it is just a measure value).
    """
    errors: list[str] = []
    for m in chart.query.measures:
        if m.denominator is None:
            continue
        if m.transform is not None:
            errors.append(
                f"{prefix}: мера-отношение не может одновременно иметь transform "
                f"({m.transform.value}) и denominator"
            )
        if m.denominator.denominator is not None:
            errors.append(
                f"{prefix}: вложенные отношения не поддерживаются (denominator у denominator)"
            )
        if m.denominator.transform is not None:
            errors.append(f"{prefix}: знаменатель отношения не может иметь transform")
    return errors


def _validate_viz_shape(chart: ChartSpec, prefix: str) -> list[str]:
    """Compile-level shape rules so adapters never meet impossible charts.

    Each viz declares which dimension-like roles it uses; roles it does not use must
    be empty so the LLM cannot smuggle structure an adapter would silently ignore.
    """
    q = chart.query
    errors: list[str] = []

    def forbid(*roles: tuple[str, list[str]]) -> None:
        for name, cols in roles:
            if cols:
                errors.append(f"{prefix}: {chart.viz.value} must not set {name} (got {cols})")

    dims, series = ("dimensions", q.dimensions), ("series", q.series)
    rows, cols = ("rows", q.rows), ("columns", q.columns)

    if chart.viz == Viz.BIG_NUMBER:
        forbid(dims, series, rows, cols)
        if len(q.measures) != 1:
            errors.append(f"{prefix}: big_number needs exactly one measure (got {len(q.measures)})")
    elif chart.viz in (Viz.LINE, Viz.AREA, Viz.BAR, Viz.STACKED_BAR):
        if not q.dimensions:
            errors.append(f"{prefix}: {chart.viz.value} needs at least one dimension (x-axis)")
        forbid(rows, cols)
    elif chart.viz == Viz.PIE:
        if len(q.dimensions) != 1:
            errors.append(f"{prefix}: pie needs exactly one dimension (got {len(q.dimensions)})")
        if len(q.measures) != 1:
            errors.append(f"{prefix}: pie needs exactly one measure (got {len(q.measures)})")
        forbid(series, rows, cols)
    elif chart.viz == Viz.TABLE:
        if not q.dimensions and not q.measures:
            errors.append(f"{prefix}: table needs at least one dimension or measure")
        forbid(series, rows, cols)
    elif chart.viz == Viz.PIVOT:
        if not q.rows:
            errors.append(f"{prefix}: pivot needs at least one row dimension")
        forbid(dims, series)
    elif chart.viz == Viz.HEATMAP:
        if len(q.dimensions) != 2:
            errors.append(
                f"{prefix}: heatmap needs exactly two dimensions x,y (got {len(q.dimensions)})"
            )
        if len(q.measures) != 1:
            errors.append(f"{prefix}: heatmap needs exactly one measure (got {len(q.measures)})")
        forbid(series, rows, cols)

    return errors


def _resolve_qualified_column(qualified: str, model: SemanticModel) -> Table | None:
    """'dm.sales_daily.date' -> its table, if both table and column exist."""
    table_name, _, column = qualified.rpartition(".")
    if not table_name:
        return None
    table = model.table(table_name)
    if table is None or table.column(column) is None:
        return None
    return table
