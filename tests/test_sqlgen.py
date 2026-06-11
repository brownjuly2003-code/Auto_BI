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
