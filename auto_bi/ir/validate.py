"""Spec validation against the semantic model (invariant 2).

Runs BEFORE any BI/SQL work. Unknown table/column -> error list for the repair loop;
no silent fixes ever. Returns human-readable errors the LLM can act on.
"""

from auto_bi.ir.spec import (
    ChartSpec,
    DashboardSpec,
    FilterOp,
    MeasureTransform,
    TimeGrain,
    Viz,
    column_alias,
    measure_alias,
)
from auto_bi.semantic.model import Additivity, Aggregation, ColumnRole, SemanticModel, Table

# numeric aggregations make no sense over dimension columns; count/count_distinct
# are legal over anything. Rejecting here keeps the error actionable for the repair
# loop instead of a late EXPLAIN failure in compile_and_build.
_NUMERIC_AGGS = frozenset({Aggregation.SUM, Aggregation.AVG, Aggregation.MIN, Aggregation.MAX})

# transforms whose window orders by the chart's first dimension — that dimension must be a
# TIME column (a period-over-period / running total is only meaningful along a time axis)
_ORDERED_TRANSFORMS = frozenset(
    {
        MeasureTransform.POP_ABS,
        MeasureTransform.POP_PCT,
        MeasureTransform.YOY_PCT,
        MeasureTransform.RUNNING_TOTAL,
    }
)
# viz with no single ordered category axis the SQL_GEN window can sort by
_TRANSFORM_UNSUPPORTED_VIZ = frozenset({Viz.BIG_NUMBER, Viz.PIVOT, Viz.HEATMAP})


def validate_spec(
    spec: DashboardSpec,
    model: SemanticModel,
    *,
    allow_raw_sql: bool = True,
) -> list[str]:
    """Validate a DashboardSpec against the semantic model.

    `allow_raw_sql=False` is the LLM path (propose/patch): ChartQuery.raw_sql is an
    operator-only hatch (CLI `auto_bi raw`) and must never come from text/fields/repair.
    Default True keeps the operator/compile path working.
    """
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
        errors.extend(_validate_chart(chart, model, spec=spec, allow_raw_sql=allow_raw_sql))

    return errors


def _validate_raw_chart(
    chart: ChartSpec, prefix: str, *, target_bi: str | None = None
) -> list[str]:
    """Validate the X-5 raw_sql escape hatch: a manual SELECT that bypasses model validation.

    The columns can't be checked against the model (that IS the point — the SQL expresses what the
    IR cannot), so validation covers only: the SQL is a single plain SELECT (static guard_sql — the
    same SELECT-only parse the live gate repeats, surfaced now for an early, actionable error), viz
    is TABLE (the only shape a raw result maps to with no IR column->axis mapping), no
    aggregating IR query field is set alongside it (a raw chart carries its whole query in the SQL;
    `dimensions` are allowed — they name the display columns), and the BI target is Superset
    (v1 only — DataLens has no raw-table path). The live EXPLAIN + LIMIT trial still runs in
    compile_and_build. Table-level RBAC for the hatch is enforced separately via
    `auth.spec_tables` (AST walk), not here.
    """
    from auto_bi.agent.sql_guard import SQLGuardError, guard_sql
    from auto_bi.ir.spec import TargetBI

    errors: list[str] = []
    if chart.viz != Viz.TABLE:
        errors.append(f"{prefix}: raw_sql is supported only with viz=table, got {chart.viz.value}")
    if target_bi is not None and target_bi != TargetBI.SUPERSET.value:
        errors.append(
            f"{prefix}: raw_sql is supported only with target_bi=superset, got {target_bi!r}"
        )
    try:
        guard_sql(chart.query.raw_sql or "")
    except SQLGuardError as e:
        errors.append(f"{prefix}: raw_sql is not a single plain SELECT: {e}")
    q = chart.query
    populated = [
        name
        for name, val in (
            ("measures", q.measures),
            ("series", q.series),
            ("rows", q.rows),
            ("columns", q.columns),
            ("joins", q.joins),
            ("filters", q.filters),
            ("order_by", q.order_by),
        )
        if val
    ]
    if q.time_grain is not None:
        populated.append("time_grain")
    if q.bins is not None:
        populated.append("bins")
    if populated:
        errors.append(
            f"{prefix}: raw_sql cannot be combined with IR query fields {sorted(populated)} "
            "(a raw chart carries its whole query in the SQL; only `dimensions` — the display "
            "columns — may accompany it)"
        )
    return errors


def _validate_chart(
    chart: ChartSpec,
    model: SemanticModel,
    *,
    spec: DashboardSpec | None = None,
    allow_raw_sql: bool = True,
) -> list[str]:
    prefix = f"chart {chart.id!r}"
    if chart.query.raw_sql is not None:
        if not allow_raw_sql:
            return [
                f"{prefix}: raw_sql is an operator-only hatch (CLI `auto_bi raw`); "
                "LLM/text/fields paths must use IR measures and dimensions only"
            ]
        target = spec.target_bi.value if spec is not None else None
        return _validate_raw_chart(chart, prefix, target_bi=target)

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
    # measures must also resolve to distinct SELECT aliases: two measures sharing a
    # measure_alias (e.g. two unlabeled count(id), or a label colliding with another's
    # default alias) emit duplicate aliases in both the flat and windowed SQL → a ClickHouse
    # "duplicate alias" at EXPLAIN. Mirror the dimension collision check for measures.
    m_aliases = [measure_alias(m) for m in chart.query.measures]
    if len(m_aliases) != len(set(m_aliases)):
        m_dupes = sorted({a for a in m_aliases if m_aliases.count(a) > 1})
        errors.append(
            f"{prefix}: measures collide by alias {m_dupes} — "
            "две меры дают одинаковый SELECT-алиас (задайте label одной из них)"
        )
    # ...and the two sets must not collide with EACH OTHER: a measure label equal to a
    # dimension's bare name (label="store_id" next to dimensions=["store_id"]) emits two
    # SELECT columns under one alias; which one the BI's aggregate picks up is undefined,
    # so the chart silently shows wrong numbers instead of failing.
    cross = sorted(set(aliases) & set(m_aliases))
    if cross:
        errors.append(
            f"{prefix}: measure alias collides with dimension column {cross} — "
            "мера и размерность дают одинаковый SELECT-алиас (задайте мере другой label)"
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
        elif agg == Aggregation.SUM and col.additivity == Additivity.NON_ADDITIVE:
            # semantic governance (P1-6): a rate/ratio/price summed over rows is
            # business-meaningless; the model says so, the spec must not do it
            errors.append(
                f"{prefix}: sum над неаддитивной колонкой {col_name!r} (rate/ratio) "
                "бессмыслен — используйте avg или ratio из numerator/denominator"
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
    errors.extend(_validate_compare(chart, model, prefix))
    errors.extend(_validate_time_grain(chart, model, prefix))
    errors.extend(_validate_histogram(chart, model, prefix))
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
    errors: list[str] = []
    # lag_periods is a period-over-period offset, only meaningful for pop_abs/pop_pct: yoy_pct
    # derives its own year lag, share_of_total/running_total have no period offset, and a plain
    # measure has no lag. Checked across ALL measures (a lag on any other measure is a spec slip).
    for m in chart.query.measures:
        if m.lag_periods is not None and m.transform not in (
            MeasureTransform.POP_ABS,
            MeasureTransform.POP_PCT,
        ):
            kind = m.transform.value if m.transform is not None else "обычной меры (без transform)"
            errors.append(f"{prefix}: lag_periods применим только к pop_abs/pop_pct, не к {kind}")
    transformed = [m for m in chart.query.measures if m.transform is not None]
    if not transformed:
        return errors
    if chart.viz in _TRANSFORM_UNSUPPORTED_VIZ:
        names = ", ".join(sorted({m.transform.value for m in transformed if m.transform}))
        errors.append(
            f"{prefix}: преобразования мер ({names}) не поддерживаются для {chart.viz.value} — "
            "нужен график с одной упорядоченной осью (line/area/bar/pie/table)"
        )
        return errors
    first_is_time = _first_dimension_is_time(chart, model)
    for m in transformed:
        if m.transform in _ORDERED_TRANSFORMS and not first_is_time:
            errors.append(
                f"{prefix}: преобразование {m.transform.value!r} требует, чтобы первое "
                "измерение было колонкой времени (ось x по времени)"
            )
        elif (
            m.transform in (MeasureTransform.SHARE_OF_TOTAL, MeasureTransform.RUNNING_SHARE)
            and not chart.query.dimensions
        ):
            # both are a share *of* something — without a grouping dimension a share is a
            # trivial 100% (and a Pareto cumulative share has nothing to rank)
            errors.append(
                f"{prefix}: преобразование {m.transform.value!r} требует хотя бы одно измерение"
            )
    grain = chart.query.time_grain
    if any(m.transform == MeasureTransform.YOY_PCT for m in transformed) and (
        grain is None or grain == TimeGrain.DAY
    ):
        errors.append(
            f"{prefix}: преобразование 'yoy_pct' требует time_grain "
            "(week/month/quarter/year), чтобы определить сдвиг на год"
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


def _validate_compare(chart: ChartSpec, model: SemanticModel, prefix: str) -> list[str]:
    """Rules for a scalar period-compare KPI (`Measure.compare`).

    Only on big_number (a scalar tile — SQL_GEN reduces it to one row by conditional aggregation);
    the compare column must be a TIME column of the chart's table and the grain a real period (not
    day); mutually exclusive with a window transform or a ratio denominator (a compare IS the
    derived value). The one-measure / no-dimension shape is enforced by `_validate_viz_shape`.
    """
    compared = [m for m in chart.query.measures if m.compare is not None]
    if not compared:
        return []
    errors: list[str] = []
    if chart.viz != Viz.BIG_NUMBER:
        errors.append(
            f"{prefix}: сравнение периодов (compare) поддерживается только для big_number "
            f"(получено {chart.viz.value})"
        )
    base_table = model.table(chart.query.table)
    for m in compared:
        c = m.compare
        assert c is not None  # filtered above; keeps mypy happy on c.column/c.grain
        if m.transform is not None or m.denominator is not None:
            errors.append(
                f"{prefix}: мера со сравнением периодов (compare) не может одновременно иметь "
                "transform или denominator"
            )
        if c.grain == TimeGrain.DAY:
            errors.append(
                f"{prefix}: compare.grain должен задавать период "
                "(week/month/quarter/year), не day"
            )
        col_name = c.column.rpartition(".")[2] if "." in c.column else c.column
        ref_table = model.table(c.column.rpartition(".")[0]) if "." in c.column else base_table
        column = ref_table.column(col_name) if ref_table is not None else None
        if column is None:
            errors.append(f"{prefix}: compare.column {c.column!r} — неизвестная колонка")
        elif column.role != ColumnRole.TIME:
            errors.append(
                f"{prefix}: compare.column {c.column!r} должна быть колонкой времени (role=time)"
            )
    return errors


def _validate_time_grain(chart: ChartSpec, model: SemanticModel, prefix: str) -> list[str]:
    """time_grain truncates the time x-axis (the first dimension), so that dimension must be a
    TIME column — bucketing a categorical or absent axis to a period is meaningless."""
    if chart.query.time_grain is None:
        return []
    if not _first_dimension_is_time(chart, model):
        return [
            f"{prefix}: time_grain ({chart.query.time_grain.value}) требует, чтобы первое "
            "измерение было колонкой времени (ось x по времени)"
        ]
    return []


def _validate_histogram(chart: ChartSpec, model: SemanticModel, prefix: str) -> list[str]:
    """A histogram (`bins` set) bins the numeric x-dimension into equal-width buckets and counts
    rows per bucket. `bins` and viz=HISTOGRAM imply each other; the binned dimension must be a
    numeric MEASURE column (a quantity to distribute, not an id/category); the single measure is
    a plain COUNT (count of rows per bucket — no transform/ratio/other aggregate). Shape (exactly
    one dimension + one measure, no other roles) is enforced in `_validate_viz_shape`."""
    q = chart.query
    is_hist = chart.viz == Viz.HISTOGRAM
    if (q.bins is not None) != is_hist:
        if is_hist:
            return [f"{prefix}: histogram требует bins (число корзин)"]
        return [f"{prefix}: bins задаётся только для viz=histogram (получено {chart.viz.value})"]
    if not is_hist:
        return []
    errors: list[str] = []
    table = model.table(q.table)
    if q.dimensions and table is not None:
        ref = q.dimensions[0]
        col = table.column(ref.rpartition(".")[2] if "." in ref else ref)
        if col is not None and col.role != ColumnRole.MEASURE:
            errors.append(
                f"{prefix}: histogram бинирует числовую меру — измерение {ref!r} имеет роль "
                f"{col.role.value}, нужна колонка role=measure (количественная)"
            )
    for m in q.measures:
        if m.agg != Aggregation.COUNT or m.transform is not None or m.denominator is not None:
            errors.append(
                f"{prefix}: мера гистограммы должна быть простым count (число строк в корзине), "
                "без transform/denominator"
            )
    if q.time_grain is not None:
        errors.append(f"{prefix}: histogram несовместима с time_grain")
    if q.joins:
        # _generate_histogram_sql bins a column of the BASE table (FROM table CROSS JOIN <width>);
        # it never emits query.joins, so any joined-table reference would produce broken SQL.
        # Reject at validation (a clear repair-loop error) instead of a late EXPLAIN failure.
        errors.append(
            f"{prefix}: histogram не поддерживает join — бинирование идёт по колонке базовой "
            "таблицы (вынесите join-измерение в отдельный чарт)"
        )
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
    elif chart.viz == Viz.HISTOGRAM:
        if len(q.dimensions) != 1:
            errors.append(
                f"{prefix}: histogram needs exactly one dimension to bin (got {len(q.dimensions)})"
            )
        if len(q.measures) != 1:
            errors.append(f"{prefix}: histogram needs exactly one measure (got {len(q.measures)})")
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
