"""Spec validation against the semantic model (invariant 2).

Runs BEFORE any BI/SQL work. Unknown table/column -> error list for the repair loop;
no silent fixes ever. Returns human-readable errors the LLM can act on.
"""

from auto_bi.ir.spec import ChartSpec, DashboardSpec, Viz
from auto_bi.semantic.model import ColumnRole, SemanticModel, Table


def validate_spec(spec: DashboardSpec, model: SemanticModel) -> list[str]:
    errors: list[str] = []

    chart_ids = [c.id for c in spec.charts]
    if len(chart_ids) != len(set(chart_ids)):
        errors.append(f"chart ids are not unique: {chart_ids}")

    for f in spec.filters:
        if _resolve_qualified_column(f.column, model) is None:
            errors.append(f"dashboard filter references unknown column: {f.column!r}")

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

    for dim in chart.query.dimensions:
        if table.column(dim) is None:
            errors.append(f"{prefix}: unknown dimension column {dim!r} in {table.name}")

    for measure in chart.query.measures:
        col = table.column(measure.column)
        if col is None:
            errors.append(f"{prefix}: unknown measure column {measure.column!r} in {table.name}")
        elif col.role == ColumnRole.TIME:
            errors.append(f"{prefix}: time column {measure.column!r} cannot be a measure")

    for qf in chart.query.filters:
        if table.column(qf.column) is None:
            errors.append(f"{prefix}: filter references unknown column {qf.column!r}")

    orderable = set(chart.query.dimensions)
    for m in chart.query.measures:
        orderable.add(m.column)
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
    """Compile-level shape rules so adapters never meet impossible charts."""
    q = chart.query
    if chart.viz == Viz.BIG_NUMBER:
        if q.dimensions:
            return [f"{prefix}: big_number must not have dimensions (got {q.dimensions})"]
        if len(q.measures) != 1:
            return [f"{prefix}: big_number needs exactly one measure (got {len(q.measures)})"]
    if chart.viz in (Viz.LINE, Viz.BAR) and not q.dimensions:
        return [f"{prefix}: {chart.viz} needs at least one dimension"]
    return []


def _resolve_qualified_column(qualified: str, model: SemanticModel) -> Table | None:
    """'dm.sales_daily.date' -> its table, if both table and column exist."""
    table_name, _, column = qualified.rpartition(".")
    if not table_name:
        return None
    table = model.table(table_name)
    if table is None or table.column(column) is None:
        return None
    return table
