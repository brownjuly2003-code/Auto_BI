"""SQL_GEN + guard: deterministic SQL, quoting, SELECT-only enforcement."""

import pytest

from auto_bi.agent.sql_guard import LiveSQLValidator, SQLGuardError, guard_sql
from auto_bi.agent.sqlgen import generate_chart_sql
from auto_bi.ir.spec import ChartQuery, FilterOp, Measure, OrderBy, QueryFilter
from auto_bi.semantic.model import Aggregation


def make_query(**kwargs) -> ChartQuery:
    defaults = dict(
        table="dm.sales_daily",
        dimensions=["date"],
        measures=[Measure(column="revenue", agg=Aggregation.SUM, label="Выручка")],
    )
    defaults.update(kwargs)
    return ChartQuery(**defaults)


def test_line_sql() -> None:
    sql = generate_chart_sql(make_query())
    assert sql == (
        'SELECT "date", SUM("revenue") AS "Выручка" FROM "dm"."sales_daily" '
        'GROUP BY "date" LIMIT 5000'
    )


def test_big_number_sql_no_group_by() -> None:
    sql = generate_chart_sql(make_query(dimensions=[]))
    assert "GROUP BY" not in sql
    assert sql.startswith('SELECT SUM("revenue") AS "Выручка"')


def test_bar_with_order_and_limit() -> None:
    sql = generate_chart_sql(
        make_query(
            dimensions=["store_id"],
            order_by=[OrderBy(by="Выручка", dir="desc")],
            limit=10,
        )
    )
    assert 'ORDER BY "Выручка" DESC' in sql
    assert sql.endswith("LIMIT 10")


def test_filters_all_ops() -> None:
    sql = generate_chart_sql(
        make_query(
            filters=[
                QueryFilter(column="date", op=FilterOp.GTE, value="2026-01-01"),
                QueryFilter(column="store_id", op=FilterOp.IN, value=[1, 2, 3]),
                QueryFilter(column="city", op=FilterOp.NEQ, value="Москва"),
            ]
        )
    )
    assert "\"date\" >= '2026-01-01'" in sql
    assert '"store_id" IN (1, 2, 3)' in sql
    assert "\"city\" <> 'Москва'" in sql


def test_order_by_raw_measure_column_uses_alias() -> None:
    # measure without a label: ordering by the raw column must resolve to the SELECT
    # alias, never the bare column (which is not in GROUP BY -> ClickHouse error 215)
    sql = generate_chart_sql(
        make_query(
            dimensions=["store_id"],
            measures=[Measure(column="revenue", agg=Aggregation.SUM)],  # no label
            order_by=[OrderBy(by="revenue", dir="desc")],
        )
    )
    assert 'ORDER BY "sum_revenue" DESC' in sql
    assert 'ORDER BY "revenue"' not in sql


def test_order_by_computed_alias() -> None:
    sql = generate_chart_sql(
        make_query(
            dimensions=["store_id"],
            measures=[Measure(column="revenue", agg=Aggregation.SUM)],
            order_by=[OrderBy(by="sum_revenue", dir="desc")],
        )
    )
    assert 'ORDER BY "sum_revenue" DESC' in sql


def test_empty_in_filter_raises() -> None:
    with pytest.raises(ValueError, match="no values"):
        generate_chart_sql(
            make_query(filters=[QueryFilter(column="store_id", op=FilterOp.IN, value=[])])
        )


def test_pivot_groups_by_rows_and_columns() -> None:
    sql = generate_chart_sql(
        ChartQuery(
            table="dm.sales_daily",
            rows=["store_id"],
            columns=["date"],
            measures=[Measure(column="revenue", agg=Aggregation.SUM, label="Выручка")],
        )
    )
    assert '"store_id"' in sql and '"date"' in sql
    assert 'GROUP BY "store_id", "date"' in sql


def test_heatmap_groups_by_both_axes() -> None:
    sql = generate_chart_sql(
        ChartQuery(
            table="dm.sales_daily",
            dimensions=["store_id", "date"],
            measures=[Measure(column="revenue", agg=Aggregation.SUM)],
        )
    )
    assert 'GROUP BY "store_id", "date"' in sql


def test_series_dimension_enters_group_by() -> None:
    sql = generate_chart_sql(make_query(dimensions=["date"], series=["store_id"]))
    assert 'GROUP BY "date", "store_id"' in sql


def test_count_distinct() -> None:
    sql = generate_chart_sql(
        make_query(measures=[Measure(column="store_id", agg=Aggregation.COUNT_DISTINCT)])
    )
    assert 'COUNT(DISTINCT "store_id") AS "count_distinct_store_id"' in sql


def test_string_values_are_escaped() -> None:
    sql = generate_chart_sql(
        make_query(filters=[QueryFilter(column="city", op=FilterOp.EQ, value="О'Хара; DROP")])
    )
    assert "О''Хара; DROP" in sql  # quote doubled, stays inside the literal
    guard_sql(sql)  # and the result is still one valid SELECT


# --- guard ----------------------------------------------------------------


def test_guard_accepts_generated_sql() -> None:
    guard_sql(generate_chart_sql(make_query()))


@pytest.mark.parametrize(
    "bad",
    [
        "INSERT INTO t VALUES (1)",
        "DROP TABLE dm.sales_daily",
        "SELECT 1; SELECT 2",
        "CREATE TABLE x (a Int32) ENGINE = Memory",
        "totally not sql ((",
    ],
)
def test_guard_rejects(bad: str) -> None:
    with pytest.raises(SQLGuardError):
        guard_sql(bad)


# --- live validator (stubbed) ----------------------------------------------


def test_live_validator_explain_and_trial() -> None:
    seen: list[str] = []

    def run(sql: str) -> list[dict]:
        seen.append(sql)
        return [{"explain": "Expression"}]

    LiveSQLValidator(run).validate(generate_chart_sql(make_query()))
    assert seen[0].startswith("EXPLAIN ")
    assert "LIMIT 10" in seen[1]
    assert "max_execution_time" in seen[1]


def test_live_validator_wraps_engine_error() -> None:
    def run(sql: str) -> list[dict]:
        raise RuntimeError("Unknown column nope")

    with pytest.raises(SQLGuardError, match="EXPLAIN failed"):
        LiveSQLValidator(run).validate(generate_chart_sql(make_query()))


def test_join_query_qualifies_aliases_and_left_joins() -> None:
    from auto_bi.ir.spec import JoinSpec

    q = ChartQuery(
        table="dm.sales_daily",
        dimensions=["dm.stores.city"],
        measures=[Measure(column="revenue", agg=Aggregation.SUM, label="Выручка")],
        joins=[
            JoinSpec(
                table="dm.stores",
                on_left="dm.sales_daily.store_id",
                on_right="dm.stores.id",
            )
        ],
        filters=[QueryFilter(column="date", op=FilterOp.GTE, value="2026-06-01")],
        order_by=[OrderBy(by="Выручка", dir="desc")],
        limit=10,
    )
    sql = generate_chart_sql(q)
    assert 'LEFT JOIN "dm"."stores"' in sql
    assert '"dm"."sales_daily"."store_id" = "dm"."stores"."id"' in sql
    assert '"dm"."stores"."city" AS "city"' in sql  # bare alias for the dataset
    assert 'GROUP BY "dm"."stores"."city"' in sql
    # base-table references are qualified too: joined tables may share column names
    assert '"dm"."sales_daily"."date" >= ' in sql
    assert 'SUM("dm"."sales_daily"."revenue") AS "Выручка"' in sql
    assert 'ORDER BY "Выручка" DESC' in sql


def test_join_order_by_joined_dimension_uses_alias() -> None:
    from auto_bi.ir.spec import JoinSpec

    q = ChartQuery(
        table="dm.sales_daily",
        dimensions=["dm.stores.city"],
        measures=[Measure(column="revenue", agg=Aggregation.SUM)],
        joins=[
            JoinSpec(
                table="dm.stores",
                on_left="dm.sales_daily.store_id",
                on_right="dm.stores.id",
            )
        ],
        order_by=[OrderBy(by="dm.stores.city", dir="asc")],
    )
    sql = generate_chart_sql(q)
    assert 'ORDER BY "city"' in sql  # the SELECT alias, not a dotted identifier
