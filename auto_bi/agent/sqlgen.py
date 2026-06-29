"""SQL_GEN: deterministic ChartQuery -> ClickHouse SELECT via sqlglot AST.

No LLM here (D5): the spec's query block is declarative, SQL is assembled from it.
Identifiers are always quoted, values go through sqlglot literals.
"""

from sqlglot import expressions as exp

from auto_bi.ir.spec import (
    ChartQuery,
    FilterOp,
    Measure,
    MeasureTransform,
    QueryFilter,
    TimeGrain,
    column_alias,
    measure_alias,
)
from auto_bi.semantic.model import Aggregation

DIALECT = "clickhouse"

_AGG_FUNC = {
    Aggregation.SUM: "sum",
    Aggregation.AVG: "avg",
    Aggregation.MIN: "min",
    Aggregation.MAX: "max",
    Aggregation.COUNT: "count",
}


def _measure_expr(
    measure: Measure, col_ref: str | None = None, *, alias: str | None = None
) -> exp.Expression:
    # col_ref lets the caller qualify the column for joined queries; the SELECT
    # alias always comes from the measure itself, so it never grows a table prefix.
    # `alias` overrides the SELECT alias — the windowed path names the inner base
    # aggregate with a private alias the outer window then reads.
    col = _dim_column(col_ref or measure.column)
    if measure.agg == Aggregation.COUNT_DISTINCT:
        agg: exp.Expression = exp.Count(this=exp.Distinct(expressions=[col]))
    else:
        # sqlglot types func()/alias_ as Func/Expr, both Expression subclasses at runtime
        agg = exp.func(_AGG_FUNC[measure.agg], col)  # type: ignore[assignment]
    return exp.alias_(agg, alias or measure_alias(measure), quoted=True)  # type: ignore[return-value]


def _literal(value: str | int | float) -> exp.Expression:
    if isinstance(value, str):
        return exp.Literal.string(value)
    return exp.Literal.number(value)


def _dim_column(ref: str) -> exp.Expression:
    """Dimension-like reference -> column expr; 'dm.stores.city' becomes dm.stores.city."""
    if "." not in ref:
        return exp.column(ref)
    db_table, _, col = ref.rpartition(".")
    db, _, table = db_table.rpartition(".")
    return exp.column(col, table=table, db=db or None)


_CH_GRAIN_FUNC = {
    TimeGrain.MONTH: "toStartOfMonth",
    TimeGrain.QUARTER: "toStartOfQuarter",
    TimeGrain.YEAR: "toStartOfYear",
}


def _time_grain_expr(col: exp.Expression, grain: TimeGrain, *, dialect: str) -> exp.Expression:
    """Truncate a time column to `grain`. day -> unchanged; week starts Monday in both dialects.

    ClickHouse uses toStartOf* (toStartOfWeek mode 1 = Monday); other dialects use
    date_trunc(unit, col), whose Postgres week is already Monday-based. sqlglot renders the
    function name per output dialect; we pick the ClickHouse spelling explicitly for parity."""
    if grain == TimeGrain.DAY:
        return col
    if dialect == "clickhouse":
        if grain == TimeGrain.WEEK:
            # mode 1 => week starts Monday, matching Postgres date_trunc('week')
            return exp.func("toStartOfWeek", col, exp.Literal.number(1))  # type: ignore[return-value]
        return exp.func(_CH_GRAIN_FUNC[grain], col)  # type: ignore[return-value]
    return exp.func("date_trunc", exp.Literal.string(grain.value), col)  # type: ignore[return-value]


def _filter_expr(qf: QueryFilter) -> exp.Expression:
    col = _dim_column(qf.column)
    if qf.op == FilterOp.IN:
        values = qf.value if isinstance(qf.value, list) else [qf.value]
        if not values:
            raise ValueError(f"IN filter on {qf.column!r} has no values")
        return col.isin(*[_literal(v) for v in values])
    if isinstance(qf.value, list):
        raise ValueError(f"operator {qf.op.value!r} expects a scalar, got list: {qf.value}")
    rhs = _literal(qf.value)
    match qf.op:
        case FilterOp.EQ:
            return col.eq(rhs)
        case FilterOp.NEQ:
            return col.neq(rhs)
        case FilterOp.GTE:
            return exp.GTE(this=col, expression=rhs)
        case FilterOp.LTE:
            return exp.LTE(this=col, expression=rhs)
    raise ValueError(f"unsupported filter operator: {qf.op}")


def _order_targets(query: ChartQuery) -> dict[str, str]:
    """Map every legal `order_by.by` reference to the SELECT alias it must sort on.

    A measure is addressable by raw column / canonical alias / label, a qualified
    dimension by its bare alias. Ordering by a raw measure column would reference a
    non-grouped column (ClickHouse error 215); the SELECT alias is always safe.
    """
    targets: dict[str, str] = {}
    for m in query.measures:
        alias = measure_alias(m)
        targets[m.column] = alias
        targets[alias] = alias
        if m.label:
            targets[m.label] = alias
    for c in query.group_columns():
        if "." in c:
            targets[c] = column_alias(c)
    return targets


def _apply_order_and_limit(
    select: exp.Select,
    query: ChartQuery,
    *,
    apply_limit: bool,
    order_exprs: dict[str, exp.Expression] | None = None,
) -> exp.Select:
    targets = _order_targets(query)
    order_exprs = order_exprs or {}
    for ob in query.order_by:
        target = targets.get(ob.by, ob.by)
        # a grain-truncated time dim sorts by its truncation expression, not the alias
        # (see _grain_order_exprs); everything else sorts by the SELECT alias column
        override = order_exprs.get(target)
        sort_on = override.copy() if override is not None else exp.column(target, quoted=True)
        select = select.order_by(exp.Ordered(this=sort_on, desc=ob.dir == "desc"))
    if apply_limit:
        select = select.limit(query.limit)
    return select


def _grain_order_exprs(query: ChartQuery, dialect: str) -> dict[str, exp.Expression]:
    """Flat path only: ORDER BY a grain-truncated time dim by its truncation EXPRESSION.

    The grained dimension is aliased back to its bare name (`toStartOfMonth("date") AS "date"`)
    so the dataset column is stable. But on the flat path the SELECT reads from the physical
    table, where a column named `date` also exists: ClickHouse binds `ORDER BY "date"` to that
    physical column (not the grouped expression) and fails with NOT_AN_AGGREGATE (live-verified
    2026-06-28). Ordering by the truncation expression — itself a GROUP BY key — is unambiguous
    in ClickHouse, Postgres and DuckDB alike. The windowed path is unaffected: it selects from a
    subquery where `date` is an output column with no physical shadow, so it keeps the alias."""
    grain = query.time_grain if query.time_grain != TimeGrain.DAY else None
    if grain is None or not query.dimensions:
        return {}
    grained = query.dimensions[0]
    target = column_alias(grained) if "." in grained else grained
    return {target: _time_grain_expr(_grained_source(query, grained), grain, dialect=dialect)}


def _is_derived(measure: Measure) -> bool:
    """A measure needing the two-level SELECT: a window transform or a ratio denominator."""
    return measure.transform is not None or measure.denominator is not None


def generate_chart_sql(
    query: ChartQuery, *, dialect: str = DIALECT, apply_limit: bool = True
) -> str:
    """Deterministic SELECT for a chart's virtual dataset.

    The AST is dialect-agnostic; `dialect` (a sqlglot dialect, e.g. "clickhouse" v1 or
    "postgres" for Greenplum/Greengage v2 — see auto_bi.engine.sqlglot_dialect) only
    changes identifier quoting and function rendering on output (e.g. a window LAG renders
    as ClickHouse `lagInFrame` vs Postgres `LAG`).

    `apply_limit=False` drops the trailing top-N LIMIT: used for charts in a native
    dashboard filter's scope, where the limit moves to form_data so re-ranking happens
    AFTER the viewer's filter (a pre-truncated top-N would filter the wrong rows, and a
    select filter's options would be capped to that pre-filter top-N). The dataset is
    already aggregated, so the row count stays small regardless.

    When any measure is *derived* — carries a `transform` (period-over-period, share, running
    total) or a `denominator` (a ratio num/den) — the query becomes two-level: an inner GROUP BY
    computes the base aggregates and an outer SELECT applies the window / division over them
    (see `_generate_windowed_sql`).
    """
    if query.bins is not None:
        return _generate_histogram_sql(query, dialect=dialect, apply_limit=apply_limit)
    if any(_is_derived(m) for m in query.measures):
        return _generate_windowed_sql(query, dialect=dialect, apply_limit=apply_limit)
    return _generate_flat_sql(query, dialect=dialect, apply_limit=apply_limit)


def _resolve_for(query: ChartQuery):
    def resolve(ref: str) -> str:
        # with joins in play every bare reference is qualified with the base table:
        # joined tables can share column names (stores.name vs products.name) and an
        # unqualified identifier would be ambiguous to ClickHouse
        if query.joins and "." not in ref:
            return f"{query.table}.{ref}"
        return ref

    return resolve


def _grained_source(query: ChartQuery, col: str) -> exp.Expression:
    """Base-table-qualified column for a grain-truncated time dim (e.g. dm.sales_daily.date).

    Qualifying stops the truncation's inner reference from binding to the SELECT alias of the
    same bare name: ClickHouse otherwise raises NOT_AN_AGGREGATE on `toStartOfMonth(date) AS
    date ... GROUP BY toStartOfMonth(date)` (live-verified 2026-06-28). Joined queries already
    qualify every bare reference with the base table, so this only changes the no-join case; the
    qualified reference is valid in ClickHouse, Postgres and DuckDB alike."""
    ref = col if "." in col else f"{query.table}.{col}"
    return _dim_column(ref)


def _grouped_select(
    query: ChartQuery, *, measure_exprs: list[exp.Expression], dialect: str
) -> exp.Select:
    """FROM + JOINs + WHERE + GROUP BY with the given measure SELECT expressions.

    Shared by the flat path (measures aliased to their final names) and the windowed
    path's inner query (measures aliased to private `__src_i` names). When `time_grain`
    is set, the time x-axis (first dimension) is truncated to that grain in BOTH the
    SELECT and the GROUP BY, aliased back to its bare name so the dataset column is stable.
    """
    resolve = _resolve_for(query)
    group_cols = query.group_columns()  # dimensions + series + rows + columns, deduped
    # the time x-axis (first dimension) is bucketed when time_grain is set (day = raw)
    grain = query.time_grain if query.time_grain != TimeGrain.DAY else None
    grained_col = query.dimensions[0] if grain is not None and query.dimensions else None
    group_exprs: list[exp.Expression] = []
    # qualified columns (and a truncated time column) get their bare name as the SELECT alias,
    # so the dataset exposes plain column names regardless of source table / truncation
    select_dims: list[exp.Expression] = []
    for c in group_cols:
        if grain is not None and c == grained_col:
            # qualify the truncated time column with the base table so neither GROUP BY nor
            # ORDER BY can bind its bare name to the SELECT alias of the same name (ClickHouse
            # raises NOT_AN_AGGREGATE on `toStartOfMonth(date) AS date GROUP BY toStartOfMonth(
            # date)`; live-verified 2026-06-28). See _grained_source.
            e: exp.Expression = _time_grain_expr(_grained_source(query, c), grain, dialect=dialect)
        else:
            e = _dim_column(resolve(c))
        group_exprs.append(e)
        if "." in resolve(c) or c == grained_col:
            # sqlglot types alias_ as Expr (an Expression subclass at runtime), as elsewhere
            select_dims.append(exp.alias_(e, column_alias(c), quoted=True))  # type: ignore[arg-type]
        else:
            select_dims.append(e)
    select = exp.select(*select_dims, *measure_exprs).from_(exp.to_table(query.table))
    for j in query.joins:
        select = select.join(
            exp.to_table(j.table),
            on=_dim_column(j.on_left).eq(_dim_column(j.on_right)),
            join_type="left",
        )
    for qf in query.filters:
        select = select.where(_filter_expr(qf.model_copy(update={"column": resolve(qf.column)})))
    if group_exprs:
        select = select.group_by(*group_exprs)
    return select


def _generate_flat_sql(query: ChartQuery, *, dialect: str, apply_limit: bool) -> str:
    resolve = _resolve_for(query)
    measure_exprs = [_measure_expr(m, resolve(m.column)) for m in query.measures]
    select = _grouped_select(query, measure_exprs=measure_exprs, dialect=dialect)
    select = _apply_order_and_limit(
        select, query, apply_limit=apply_limit, order_exprs=_grain_order_exprs(query, dialect)
    )
    return select.sql(dialect=dialect, identify=True)


def _safe_div(num: exp.Expression, den: exp.Expression) -> exp.Expression:
    """num / NULLIF(den, 0) in floating point — a zero/NULL denominator yields NULL.

    The numerator is cast to double so the division is floating point in BOTH dialects.
    ClickHouse keeps the dividend's scale on `Decimal / Decimal` (a ratio like 0.0084 over
    a Decimal(18,2) numerator truncates to 0.00, and category shares lose their fraction so
    they no longer sum to 1) — a ratio measure must divide in Float64. Postgres numeric
    division is already exact; the cast only normalises the result type. Verified live on the
    ClickHouse stand (docs/plans/2026-06-25-derived-metrics-pop.md §6)."""
    return exp.Div(
        this=exp.cast(num, "DOUBLE"),
        expression=exp.Nullif(this=den, expression=exp.Literal.number(0)),
    )


_PERIODS_PER_YEAR = {
    TimeGrain.WEEK: 52,
    TimeGrain.MONTH: 12,
    TimeGrain.QUARTER: 4,
    TimeGrain.YEAR: 1,
}


def _periods_per_year(grain: TimeGrain | None) -> int:
    """Rows to lag for a year-over-year comparison at `grain` (validation guarantees a non-day
    grain when yoy_pct is used; a 1 fallback degrades yoy to period-over-period, never crashes)."""
    return _PERIODS_PER_YEAR.get(grain, 1) if grain is not None else 1


def _window_expr(
    transform: MeasureTransform,
    src: exp.Column,
    order_col: exp.Column | None,
    *,
    dialect: str,
    yoy_lag: int = 1,
    pop_lag: int = 1,
) -> exp.Expression:
    """Window expression over the inner base aggregate referenced by `src` (its alias).

    Dialect rendering is handled by sqlglot on output: `exp.Lag` becomes ClickHouse
    `lagInFrame` and Postgres `LAG`; `SUM(...) OVER (...)` is identical in both. Every
    leaf is `.copy()`d so reusing `src`/`order` across the AST never aliases a node.

    `dialect` is needed for one ClickHouse-specific accommodation: its `lagInFrame` returns
    the source type's default (0) for an out-of-frame row, while Postgres `LAG` returns NULL.
    The lag source is wrapped in `toNullable` on ClickHouse so the first PoP row is NULL,
    matching the live-verified Postgres path.

    `pop_lag` is the row offset for pop_abs/pop_pct (a measure's `lag_periods`, default 1 =
    adjacent period); `yoy_lag` is the year offset for yoy_pct. Both flow into the same `lag(k)`.
    """

    def lag_source() -> exp.Expression:
        s = src.copy()
        if dialect != "clickhouse":
            return s
        # sqlglot types func() as Func (an Expression subclass at runtime), as in _measure_expr
        return exp.func("toNullable", s)  # type: ignore[return-value]

    if transform == MeasureTransform.SHARE_OF_TOTAL:
        total = exp.Window(this=exp.func("sum", src.copy()))  # SUM(src) OVER ()
        return _safe_div(src.copy(), total)

    if transform == MeasureTransform.RUNNING_SHARE:
        # Pareto / ABC: categories ranked by the measure DESCENDING, the cumulative share of the
        # grand total — SUM(src) OVER (ORDER BY src DESC ROWS UNBOUNDED PRECEDING) / SUM(src) OVER
        # (). Orders by the aggregate value itself, not a time axis, so it ignores `order_col`.
        # Each row's value is its rank-based cumulative share regardless of the final display
        # order; the last (smallest) category reaches 1.0. (Exact ties order arbitrarily within
        # the ROWS frame — immaterial for a ranking view; rare for real aggregates.)
        order_desc = exp.Order(expressions=[exp.Ordered(this=src.copy(), desc=True)])
        spec = exp.WindowSpec(
            kind="ROWS", start="UNBOUNDED", start_side="PRECEDING", end="CURRENT ROW"
        )
        running = exp.Window(this=exp.func("sum", src.copy()), order=order_desc, spec=spec)
        total = exp.Window(this=exp.func("sum", src.copy()))  # SUM(src) OVER ()
        return _safe_div(running, total)

    # ordered transforms (pop_*, running_total) sort by the chart's time x-axis, which
    # validate guarantees exists; defensive guard keeps the failure local and clear
    if order_col is None:
        raise ValueError(f"transform {transform.value!r} requires a time dimension to order by")
    order = exp.Order(expressions=[exp.Ordered(this=order_col.copy())])

    if transform == MeasureTransform.RUNNING_TOTAL:
        spec = exp.WindowSpec(
            kind="ROWS", start="UNBOUNDED", start_side="PRECEDING", end="CURRENT ROW"
        )
        return exp.Window(this=exp.func("sum", src.copy()), order=order, spec=spec)

    # pop_* / yoy_pct: lag by k rows with an explicit ROWS frame so ClickHouse's frame-bounded
    # lagInFrame reads exactly the k-th previous row (Postgres LAG ignores the frame clause).
    # k = pop_lag for period-over-period (1 = adjacent, or a measure's lag_periods for "vs N
    # periods ago"); k = periods-per-year for yoy. At k = 1 the offset arg is omitted so the
    # adjacent-period pop SQL is byte-for-byte unchanged.
    def lag(k: int) -> exp.Expression:
        spec = exp.WindowSpec(kind="ROWS", start=str(k), start_side="PRECEDING", end="CURRENT ROW")
        call = (
            exp.Lag(this=lag_source())
            if k == 1
            else exp.Lag(this=lag_source(), offset=exp.Literal.number(k))
        )
        return exp.Window(this=call, order=order.copy(), spec=spec)

    if transform == MeasureTransform.POP_ABS:
        return exp.Sub(this=src.copy(), expression=lag(pop_lag))
    # pop_pct / yoy_pct: (src - lag) / lag — the numerator MUST be parenthesized, else `/` binds
    # tighter than `-` and ClickHouse computes `src - (lag / lag)` (Postgres is saved only
    # by an incidental CAST wrapper, so don't rely on the dialect adding parens). yoy lags a
    # full year of periods; pop lags pop_lag (1 by default).
    k = yoy_lag if transform == MeasureTransform.YOY_PCT else pop_lag
    return _safe_div(exp.paren(exp.Sub(this=src.copy(), expression=lag(k))), lag(k))


def _generate_windowed_sql(query: ChartQuery, *, dialect: str, apply_limit: bool) -> str:
    """Two-level SELECT for derived measures: inner GROUP BY of base aggregates, outer
    window functions / ratio divisions over them. Plain measures pass through the outer
    SELECT unchanged. A ratio measure also emits its denominator aggregate in the inner
    query (private `__den_i` alias) and divides `__src_i / __den_i` in the outer."""
    resolve = _resolve_for(query)
    # inner: every measure's base aggregate under a private alias (measure order preserved);
    # a ratio measure additionally contributes its denominator aggregate under __den_i
    src_aliases = [f"__src_{i}" for i in range(len(query.measures))]
    den_aliases = [f"__den_{i}" for i in range(len(query.measures))]
    inner_measures: list[exp.Expression] = []
    for i, m in enumerate(query.measures):
        inner_measures.append(_measure_expr(m, resolve(m.column), alias=src_aliases[i]))
        if m.denominator is not None:
            d = m.denominator
            inner_measures.append(_measure_expr(d, resolve(d.column), alias=den_aliases[i]))
    inner = _grouped_select(query, measure_exprs=inner_measures, dialect=dialect)

    # window order column = the chart's first dimension (validate proves it is TIME for the
    # ordered transforms); None when the chart has no dimension (share_of_total only)
    order_col = (
        exp.column(column_alias(query.dimensions[0]), quoted=True) if query.dimensions else None
    )
    yoy_lag = _periods_per_year(query.time_grain)  # rows to lag for a yoy_pct measure

    outer_dims: list[exp.Expression] = [
        exp.column(column_alias(c), quoted=True) for c in query.group_columns()
    ]
    outer_measures: list[exp.Expression] = []
    for i, m in enumerate(query.measures):
        src = exp.column(src_aliases[i], quoted=True)
        body: exp.Expression
        if m.denominator is not None:
            body = _safe_div(src, exp.column(den_aliases[i], quoted=True))
        elif m.transform is not None:
            body = _window_expr(
                m.transform,
                src,
                order_col,
                dialect=dialect,
                yoy_lag=yoy_lag,
                pop_lag=m.lag_periods or 1,
            )
        else:
            body = src
        # sqlglot types alias_ as Expr (an Expression subclass at runtime), as in _measure_expr
        outer_measures.append(exp.alias_(body, measure_alias(m), quoted=True))  # type: ignore[arg-type]

    outer = exp.select(*outer_dims, *outer_measures).from_(inner.subquery(alias="t"))
    outer = _apply_order_and_limit(outer, query, apply_limit=apply_limit)
    return outer.sql(dialect=dialect, identify=True)


def _generate_histogram_sql(query: ChartQuery, *, dialect: str, apply_limit: bool) -> str:
    """Equal-width histogram of the numeric x-dimension (validate guarantees exactly one + bins).

    Bins `query.dimensions[0]` into `query.bins` equal-width buckets and counts rows per bucket.
    A one-row subquery `b` computes the min and the bucket width over the (filtered) table; the
    outer query maps each row to its bucket's lower bound `mn + idx*w` (idx = floor((x-mn)/w),
    clamped to bins-1 so the maximum value joins the last bucket instead of a singleton one past
    it; a zero width — all values equal — yields a single NULL bucket via NULLIF). The bucket is
    aliased back to the dimension's bare name so adapters address it like any x-axis (a histogram
    renders as a bar over ordered buckets). The binned column is qualified with the base table
    inside the bucket expression so its inner reference cannot bind to the SELECT alias of the
    same bare name (ClickHouse alias-shadow; see _grained_source). Filters apply to BOTH the
    bucket-width subquery and the outer query so the bins reflect exactly the filtered range.
    Rows whose binned value is NULL are excluded from both: a NULL belongs to no bucket, and the
    dialects otherwise disagree on where it lands — ClickHouse isolates it in a NULL bucket
    (least propagates NULL) while Postgres/Greenplum `least` ignores NULL and folds it into the
    top bucket (silent skew). Excluding it up front makes the histogram dialect-stable.
    """
    assert query.bins is not None  # validate guarantees this on the histogram path

    def _not_null(col: exp.Expression) -> exp.Expression:
        return exp.Not(this=exp.Is(this=col, expression=exp.Null()))

    dim = query.dimensions[0]
    bare = column_alias(dim) if "." in dim else dim
    col_q = _grained_source(query, dim)  # base-table-qualified binned column (anti alias-shadow)
    col_bare = _dim_column(bare)

    # The binned column is cast to double for all bucket arithmetic: ClickHouse does Decimal
    # division/floor at the dividend's scale (a Decimal price would bucket at boundaries
    # differently than float), so equal-width binning must run in floating point — the same
    # Decimal-vs-float lesson as _safe_div (live-verified: a Decimal-path histogram mis-binned
    # boundary rows vs the float hand calc). Postgres double is exact; the cast only normalises.
    col_bare_d = exp.cast(col_bare.copy(), "DOUBLE")
    col_q_d = exp.cast(col_q, "DOUBLE")

    # one-row subquery b: min + equal-bucket width over the (filtered) table
    span = exp.Sub(
        this=exp.func("max", col_bare_d.copy()), expression=exp.func("min", col_bare_d.copy())
    )
    width = exp.Div(this=exp.paren(span), expression=exp.Literal.number(query.bins))
    sub = exp.select(
        exp.alias_(exp.func("min", col_bare_d.copy()), "mn", quoted=True),  # type: ignore[arg-type]
        exp.alias_(width, "w", quoted=True),  # type: ignore[arg-type]
    ).from_(exp.to_table(query.table))
    for qf in query.filters:
        sub = sub.where(_filter_expr(qf))
    sub = sub.where(_not_null(col_bare.copy()))  # min/width over non-NULL values only

    mn = exp.column("mn", table="b", quoted=True)
    w = exp.column("w", table="b", quoted=True)
    idx = exp.func(
        "floor",
        exp.Div(
            this=exp.paren(exp.Sub(this=col_q_d, expression=mn.copy())),
            expression=exp.Nullif(this=w.copy(), expression=exp.Literal.number(0)),
        ),
    )
    idx_clamped = exp.func("least", idx, exp.Literal.number(query.bins - 1))
    bucket = exp.Add(
        this=mn.copy(), expression=exp.Mul(this=exp.paren(idx_clamped), expression=w.copy())
    )

    measure_expr = _measure_expr(query.measures[0], query.measures[0].column)
    select = (
        exp.select(exp.alias_(bucket, bare, quoted=True), measure_expr)  # type: ignore[arg-type]
        .from_(exp.to_table(query.table))
        .join(sub.subquery(alias="b"), join_type="cross")
    )
    for qf in query.filters:
        select = select.where(_filter_expr(qf))
    # drop NULL-valued rows: a NULL is in no bucket and the dialects disagree on where it lands
    # (CH → NULL bucket, Postgres least() → top bucket). Qualified by the base table so it cannot
    # bind to the bucket SELECT alias of the same bare name (alias-shadow; see _grained_source).
    select = select.where(_not_null(_grained_source(query, dim)))
    select = select.group_by(bucket.copy())
    # order by the bucket EXPRESSION (not the alias) so it cannot bind to the physical column
    select = select.order_by(exp.Ordered(this=bucket.copy()))
    if apply_limit:
        select = select.limit(query.limit)
    return select.sql(dialect=dialect, identify=True)
