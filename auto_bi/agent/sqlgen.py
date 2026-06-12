"""SQL_GEN: deterministic ChartQuery -> ClickHouse SELECT via sqlglot AST.

No LLM here (D5): the spec's query block is declarative, SQL is assembled from it.
Identifiers are always quoted, values go through sqlglot literals.
"""

from sqlglot import expressions as exp

from auto_bi.ir.spec import ChartQuery, FilterOp, Measure, QueryFilter, measure_alias
from auto_bi.semantic.model import Aggregation

DIALECT = "clickhouse"

_AGG_FUNC = {
    Aggregation.SUM: "sum",
    Aggregation.AVG: "avg",
    Aggregation.MIN: "min",
    Aggregation.MAX: "max",
    Aggregation.COUNT: "count",
}


def _measure_expr(measure: Measure) -> exp.Expression:
    col = exp.column(measure.column)
    if measure.agg == Aggregation.COUNT_DISTINCT:
        agg: exp.Expression = exp.Count(this=exp.Distinct(expressions=[col]))
    else:
        agg = exp.func(_AGG_FUNC[measure.agg], col)
    return exp.alias_(agg, measure_alias(measure), quoted=True)


def _literal(value: str | int | float) -> exp.Expression:
    if isinstance(value, str):
        return exp.Literal.string(value)
    return exp.Literal.number(value)


def _filter_expr(qf: QueryFilter) -> exp.Expression:
    col = exp.column(qf.column)
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


def generate_chart_sql(query: ChartQuery) -> str:
    dims = [exp.column(d) for d in query.dimensions]
    select = exp.select(*dims, *[_measure_expr(m) for m in query.measures]).from_(
        exp.to_table(query.table)
    )
    for qf in query.filters:
        select = select.where(_filter_expr(qf))
    if dims:
        select = select.group_by(*dims)
    # any reference to a measure (raw column, label, or computed alias) must order by
    # the SELECT alias, never the raw column: a bare measure column is not in GROUP BY
    # and ClickHouse rejects it (error 215, NOT_AN_AGGREGATE).
    measure_targets: dict[str, str] = {}
    for m in query.measures:
        alias = measure_alias(m)
        measure_targets[m.column] = alias
        measure_targets[alias] = alias
        if m.label:
            measure_targets[m.label] = alias
    for ob in query.order_by:
        target = measure_targets.get(ob.by, ob.by)
        select = select.order_by(
            exp.Ordered(this=exp.column(target, quoted=True), desc=ob.dir == "desc")
        )
    select = select.limit(query.limit)
    return select.sql(dialect=DIALECT, identify=True)
