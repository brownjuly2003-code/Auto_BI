"""SQL_GEN: deterministic ChartQuery -> ClickHouse SELECT via sqlglot AST.

No LLM here (D5): the spec's query block is declarative, SQL is assembled from it.
Identifiers are always quoted, values go through sqlglot literals.
"""

from sqlglot import expressions as exp

from auto_bi.ir.spec import (
    ChartQuery,
    FilterOp,
    Measure,
    QueryFilter,
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


def _measure_expr(measure: Measure, col_ref: str | None = None) -> exp.Expression:
    # col_ref lets the caller qualify the column for joined queries; the SELECT
    # alias always comes from the measure itself, so it never grows a table prefix
    col = _dim_column(col_ref or measure.column)
    if measure.agg == Aggregation.COUNT_DISTINCT:
        agg: exp.Expression = exp.Count(this=exp.Distinct(expressions=[col]))
    else:
        agg = exp.func(_AGG_FUNC[measure.agg], col)
    return exp.alias_(agg, measure_alias(measure), quoted=True)


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


def generate_chart_sql(query: ChartQuery, *, apply_limit: bool = True) -> str:
    """Deterministic SELECT for a chart's virtual dataset.

    `apply_limit=False` drops the trailing top-N LIMIT: used for charts in a native
    dashboard filter's scope, where the limit moves to form_data so re-ranking happens
    AFTER the viewer's filter (a pre-truncated top-N would filter the wrong rows, and a
    select filter's options would be capped to that pre-filter top-N). The dataset is
    already aggregated, so the row count stays small regardless.
    """

    def resolve(ref: str) -> str:
        # with joins in play every bare reference is qualified with the base table:
        # joined tables can share column names (stores.name vs products.name) and an
        # unqualified identifier would be ambiguous to ClickHouse
        if query.joins and "." not in ref:
            return f"{query.table}.{ref}"
        return ref

    group_cols = query.group_columns()  # dimensions + series + rows + columns, deduped
    group_exprs = [_dim_column(resolve(c)) for c in group_cols]
    # qualified columns get their bare name as the SELECT alias, so the dataset exposes
    # plain column names regardless of the source table (validation rejects collisions)
    select_dims = [
        exp.alias_(e, column_alias(c), quoted=True) if "." in resolve(c) else e
        for c, e in zip(group_cols, group_exprs, strict=True)
    ]
    measure_exprs = [_measure_expr(m, resolve(m.column)) for m in query.measures]
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
    # any reference to a measure (raw column, label, or computed alias) must order by
    # the SELECT alias, never the raw column: a bare measure column is not in GROUP BY
    # and ClickHouse rejects it (error 215, NOT_AN_AGGREGATE). Qualified dimensions
    # likewise map to their bare SELECT alias.
    order_targets: dict[str, str] = {}
    for m in query.measures:
        alias = measure_alias(m)
        order_targets[m.column] = alias
        order_targets[alias] = alias
        if m.label:
            order_targets[m.label] = alias
    for c in group_cols:
        if "." in c:
            order_targets[c] = column_alias(c)
    for ob in query.order_by:
        target = order_targets.get(ob.by, ob.by)
        select = select.order_by(
            exp.Ordered(this=exp.column(target, quoted=True), desc=ob.dir == "desc")
        )
    if apply_limit:
        select = select.limit(query.limit)
    return select.sql(dialect=DIALECT, identify=True)
