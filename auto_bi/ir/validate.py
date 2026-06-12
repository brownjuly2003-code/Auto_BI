"""Spec validation against the semantic model (invariant 2).

Runs BEFORE any BI/SQL work. Unknown table/column -> error list for the repair loop;
no silent fixes ever. Returns human-readable errors the LLM can act on.
"""

from auto_bi.ir.spec import ChartSpec, DashboardSpec, FilterOp, Viz, measure_alias
from auto_bi.semantic.model import Aggregation, ColumnRole, SemanticModel, Table

# numeric aggregations make no sense over dimension columns; count/count_distinct
# are legal over anything. Rejecting here keeps the error actionable for the repair
# loop instead of a late EXPLAIN failure in compile_and_build.
_NUMERIC_AGGS = frozenset({Aggregation.SUM, Aggregation.AVG, Aggregation.MIN, Aggregation.MAX})


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

    def _unknown(col: str, where: str) -> str:
        # the most common LLM slip: a table-qualified name where a bare one is required;
        # still rejected (no silent fixes), but the error says exactly how to fix it
        hint = ""
        if col.startswith(f"{table.name}.") and table.column(col.removeprefix(f"{table.name}.")):
            hint = f" — укажи имя без префикса таблицы: {col.removeprefix(f'{table.name}.')!r}"
        return f"{prefix}: unknown {where} {col!r} in {table.name}{hint}"

    role_fields = (
        ("dimension", chart.query.dimensions),
        ("series", chart.query.series),
        ("pivot row", chart.query.rows),
        ("pivot column", chart.query.columns),
    )
    for role, cols in role_fields:
        for col in cols:
            if table.column(col) is None:
                errors.append(_unknown(col, f"{role} column"))

    for measure in chart.query.measures:
        col = table.column(measure.column)
        if col is None:
            errors.append(_unknown(measure.column, "measure column"))
        elif col.role == ColumnRole.TIME:
            errors.append(f"{prefix}: time column {measure.column!r} cannot be a measure")
        elif measure.agg in _NUMERIC_AGGS and col.role != ColumnRole.MEASURE:
            errors.append(
                f"{prefix}: {measure.agg.value} over {col.role.value} column "
                f"{measure.column!r} — для неё допустимы только count/count_distinct"
            )

    for qf in chart.query.filters:
        if table.column(qf.column) is None:
            errors.append(_unknown(qf.column, "filter column"))
        if qf.op == FilterOp.IN and isinstance(qf.value, list) and not qf.value:
            errors.append(f"{prefix}: filter on {qf.column!r} uses IN with an empty value list")

    orderable = set(chart.query.group_columns())  # any selected dimension-like column
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

    errors.extend(_validate_viz_shape(chart, prefix))
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
