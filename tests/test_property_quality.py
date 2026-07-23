"""plan_sol step 12: property / metamorphic tests for critical pure logic.

No hypothesis dependency — stdlib random with fixed seeds keeps CI deterministic
and under the Windows memory budget. Targets: SQL guard, IR validation shapes,
normalize idempotence.
"""

from __future__ import annotations

import random

import pytest

from auto_bi.agent.normalize import apply_chart_defaults, apply_label_joins
from auto_bi.agent.sql_guard import SQLGuardError, extract_table_names, guard_sql
from auto_bi.ir.spec import (
    ChartQuery,
    ChartSpec,
    DashboardSpec,
    Measure,
    TargetBI,
    Viz,
)
from auto_bi.ir.validate import validate_spec
from auto_bi.semantic.model import (
    Aggregation,
    Column,
    ColumnRole,
    Physical,
    SemanticModel,
    Table,
)


@pytest.fixture
def tiny_model() -> SemanticModel:
    return SemanticModel(
        tables=[
            Table(
                name="dm.sales",
                description="sales",
                grain=["date", "store_id"],
                columns=[
                    Column(name="date", type="Date", role=ColumnRole.TIME),
                    Column(name="store_id", type="UInt32", role=ColumnRole.DIMENSION),
                    Column(
                        name="revenue",
                        type="Float64",
                        role=ColumnRole.MEASURE,
                        agg=Aggregation.SUM,
                    ),
                ],
                physical=Physical(engine="MergeTree", cardinality={"store_id": 10}),
            )
        ]
    )


# --- SQL guard ----------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "SELECT revenue FROM dm.sales",
        "SELECT sum(revenue) AS r FROM dm.sales WHERE date >= '2024-01-01' GROUP BY store_id",
        "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
        "SELECT * FROM dm.sales UNION ALL SELECT * FROM dm.sales",
    ],
)
def test_guard_allows_plain_selects(sql: str) -> None:
    guard_sql(sql)  # must not raise


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO dm.sales VALUES (1)",
        "UPDATE dm.sales SET revenue = 0",
        "DELETE FROM dm.sales",
        "DROP TABLE dm.sales",
        "CREATE TABLE t (x Int)",
        "SELECT 1; SELECT 2",
        "SELECT * FROM url('http://evil')",
        "SELECT * FROM s3('bucket', 'key')",
        "TRUNCATE TABLE dm.sales",
    ],
)
def test_guard_rejects_writes_and_remote_sources(sql: str) -> None:
    with pytest.raises(SQLGuardError):
        guard_sql(sql)


def test_guard_metamorphic_append_write_always_fails() -> None:
    """Any accepted SELECT + trailing write must become multi-statement fail."""
    bases = [
        "SELECT 1",
        "SELECT revenue FROM dm.sales",
        "WITH t AS (SELECT store_id FROM dm.sales) SELECT * FROM t",
    ]
    tails = [
        "; DELETE FROM dm.sales",
        "; DROP TABLE dm.sales",
        "; INSERT INTO dm.sales SELECT * FROM dm.sales",
    ]
    for base in bases:
        guard_sql(base)
        for tail in tails:
            with pytest.raises(SQLGuardError):
                guard_sql(base + tail)


def test_extract_table_names_ignores_cte_aliases() -> None:
    names = extract_table_names("WITH cte AS (SELECT 1 AS x FROM dm.sales) SELECT x FROM cte")
    assert "dm.sales" in names or "sales" in {n.split(".")[-1] for n in names}
    assert "cte" not in {n.lower() for n in names}


def test_guard_fuzz_random_identifier_selects_seeded() -> None:
    """Randomized benign SELECTs stay accepted (seed fixed)."""
    rng = random.Random(42)
    cols = ["a", "b", "revenue", "store_id", "date"]
    for _ in range(40):
        col = rng.choice(cols)
        table = rng.choice(["dm.sales", "dm.orders", "t"])
        limit = rng.randint(1, 1000)
        sql = f"SELECT {col} FROM {table} LIMIT {limit}"
        guard_sql(sql)


# --- IR validate --------------------------------------------------------------


def _valid_spec(model: SemanticModel) -> DashboardSpec:
    t = model.tables[0].name
    return DashboardSpec(
        title="t",
        target_bi=TargetBI.SUPERSET,
        charts=[
            ChartSpec(
                id="c1",
                title="Revenue",
                viz=Viz.BIG_NUMBER,
                query=ChartQuery(
                    table=t,
                    measures=[Measure(column="revenue", agg="sum")],
                ),
            )
        ],
    )


def test_validate_accepts_minimal_valid_spec(tiny_model: SemanticModel) -> None:
    assert validate_spec(_valid_spec(tiny_model), tiny_model) == []


def test_validate_unknown_table_fails(tiny_model: SemanticModel) -> None:
    spec = _valid_spec(tiny_model)
    spec.charts[0].query.table = "dm.nope"
    errs = validate_spec(spec, tiny_model)
    assert errs


def test_validate_unknown_measure_fails(tiny_model: SemanticModel) -> None:
    spec = _valid_spec(tiny_model)
    spec.charts[0].query.measures = [Measure(column="not_a_col", agg="sum")]
    errs = validate_spec(spec, tiny_model)
    assert errs


def test_validate_duplicate_chart_ids_fail(tiny_model: SemanticModel) -> None:
    spec = _valid_spec(tiny_model)
    c2 = spec.charts[0].model_copy(deep=True)
    c2.id = "c1"
    spec.charts.append(c2)
    errs = validate_spec(spec, tiny_model)
    assert any("unique" in e for e in errs)


def test_validate_metamorphic_extra_bad_chart_never_clears_errors(
    tiny_model: SemanticModel,
) -> None:
    """If a chart is invalid, appending another bad chart cannot make the list empty."""
    bad = _valid_spec(tiny_model)
    bad.charts[0].query.measures = [Measure(column="ghost", agg="sum")]
    base_errs = validate_spec(bad, tiny_model)
    assert base_errs
    extra = ChartSpec(
        id="c_bad2",
        title="x",
        viz=Viz.TABLE,
        query=ChartQuery(
            table="dm.missing",
            measures=[Measure(column="ghost2", agg="sum")],
        ),
    )
    bad.charts.append(extra)
    more = validate_spec(bad, tiny_model)
    assert len(more) >= len(base_errs)


def test_llm_path_rejects_raw_sql(tiny_model: SemanticModel) -> None:
    spec = DashboardSpec(
        title="raw",
        target_bi=TargetBI.SUPERSET,
        charts=[
            ChartSpec(
                id="r1",
                title="raw",
                viz=Viz.TABLE,
                query=ChartQuery(table="dm.sales", raw_sql="SELECT 1"),
            )
        ],
    )
    errs = validate_spec(spec, tiny_model, allow_raw_sql=False)
    assert errs
    # operator path may accept raw_sql table shape
    op_errs = validate_spec(spec, tiny_model, allow_raw_sql=True)
    # raw still needs to pass guard; SELECT 1 is fine
    assert not any("not a single plain SELECT" in e for e in op_errs)


# --- normalize ----------------------------------------------------------------


def test_apply_chart_defaults_idempotent(tiny_model: SemanticModel) -> None:
    spec = _valid_spec(tiny_model)
    once = apply_chart_defaults(spec, tiny_model)
    twice = apply_chart_defaults(once, tiny_model)
    assert once.model_dump() == twice.model_dump()


def test_label_joins_idempotent_on_valid_spec(tiny_model: SemanticModel) -> None:
    spec = apply_chart_defaults(_valid_spec(tiny_model), tiny_model)
    once = apply_label_joins(spec, tiny_model)
    twice = apply_label_joins(once, tiny_model)
    assert once.model_dump() == twice.model_dump()


def test_normalize_then_validate_stays_clean(tiny_model: SemanticModel) -> None:
    spec = apply_label_joins(apply_chart_defaults(_valid_spec(tiny_model), tiny_model), tiny_model)
    assert validate_spec(spec, tiny_model) == []
