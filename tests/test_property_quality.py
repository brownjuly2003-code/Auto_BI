"""plan_sol step 12: property / metamorphic tests for critical pure logic.

No hypothesis dependency — stdlib random with fixed seeds keeps CI deterministic
and under the Windows memory budget. Targets: SQL guard, IR validation shapes,
normalize idempotence, RBAC schema filters.
"""

from __future__ import annotations

import random

import pytest

from auto_bi.adapters.superset.native_filters import (
    build_native_filter_configuration,
    participating_chart_ids,
)
from auto_bi.agent.dataset_plan import chart_accepts_filter, plan_datasets
from auto_bi.agent.normalize import apply_chart_defaults, apply_label_joins
from auto_bi.agent.sql_guard import SQLGuardError, extract_table_names, guard_sql
from auto_bi.auth import (
    filter_model_by_schemas,
    forbidden_tables,
    is_table_allowed,
    schema_of,
    spec_tables,
)
from auto_bi.ir.spec import (
    ChartQuery,
    ChartSpec,
    DashboardFilter,
    DashboardSpec,
    JoinSpec,
    Measure,
    MeasureTransform,
    TargetBI,
    Viz,
)
from auto_bi.ir.validate import validate_spec
from auto_bi.semantic.model import (
    Aggregation,
    Column,
    ColumnRole,
    Join,
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


# --- RBAC schema filters (plan_sol step 12 residual) --------------------------


def _multi_schema_model() -> SemanticModel:
    """Three schemas with one cross-schema join (dm ↔ ext) and an isolated finance table."""
    return SemanticModel(
        tables=[
            Table(
                name="dm.sales",
                columns=[
                    Column(name="k", type="String", role=ColumnRole.DIMENSION),
                    Column(
                        name="revenue",
                        type="Float64",
                        role=ColumnRole.MEASURE,
                        agg=Aggregation.SUM,
                    ),
                ],
            ),
            Table(
                name="dm.stores",
                columns=[Column(name="id", type="UInt32", role=ColumnRole.DIMENSION)],
            ),
            Table(
                name="ext.rates",
                columns=[Column(name="k", type="String", role=ColumnRole.DIMENSION)],
            ),
            Table(
                name="finance.ledger",
                columns=[
                    Column(
                        name="amount",
                        type="Float64",
                        role=ColumnRole.MEASURE,
                        agg=Aggregation.SUM,
                    )
                ],
            ),
        ],
        joins=[Join(left="dm.sales.k", right="ext.rates.k")],
    )


def _spec_touching(*tables: str) -> DashboardSpec:
    """One chart per table; first chart may join the second when ≥2 tables."""
    charts: list[ChartSpec] = []
    for i, table in enumerate(tables):
        joins: list[JoinSpec] = []
        if i == 0 and len(tables) > 1:
            other = tables[1]
            joins = [
                JoinSpec(
                    table=other,
                    on_left=f"{table}.k",
                    on_right=f"{other}.k",
                )
            ]
        charts.append(
            ChartSpec(
                id=f"c{i}",
                title=table,
                viz=Viz.TABLE,
                query=ChartQuery(
                    table=table,
                    measures=[Measure(column="x", agg="sum")],
                    joins=joins,
                ),
            )
        )
    return DashboardSpec(title="rbac", charts=charts, target_bi=TargetBI.SUPERSET)


def test_is_table_allowed_matches_schema_membership() -> None:
    rng = random.Random(42)
    schemas = ["dm", "ext", "finance", "ops"]
    for _ in range(40):
        table_schema = rng.choice(schemas)
        table = f"{table_schema}.t{rng.randint(0, 9)}"
        allowed = list({rng.choice(schemas) for _ in range(rng.randint(0, 3))})
        if rng.random() < 0.15:
            allowed = ["*"]
        expected = "*" in allowed or table_schema in set(allowed)
        assert is_table_allowed(table, allowed) is expected
        assert schema_of(table) == table_schema


def test_filter_model_only_keeps_allowed_tables_and_internal_joins() -> None:
    model = _multi_schema_model()
    rng = random.Random(7)
    candidates = [["dm"], ["ext"], ["finance"], ["dm", "ext"], ["dm", "finance"], ["*"], []]
    for allowed in candidates:
        scoped = filter_model_by_schemas(model, allowed)
        if "*" in allowed:
            assert scoped is model
            continue
        for t in scoped.tables:
            assert is_table_allowed(t.name, allowed)
        kept = {t.name for t in scoped.tables}
        for j in scoped.joins:
            left_table = ".".join(j.left.split(".")[:2])
            right_table = ".".join(j.right.split(".")[:2])
            assert left_table in kept
            assert right_table in kept
        # idempotent
        again = filter_model_by_schemas(scoped, allowed)
        assert [t.name for t in again.tables] == [t.name for t in scoped.tables]
        assert len(again.joins) == len(scoped.joins)
    # random subsets stay consistent with membership
    all_schemas = ["dm", "ext", "finance"]
    for _ in range(20):
        k = rng.randint(0, len(all_schemas))
        allowed = rng.sample(all_schemas, k)
        scoped = filter_model_by_schemas(model, allowed)
        assert all(is_table_allowed(t.name, allowed) for t in scoped.tables)


def test_forbidden_tables_subset_and_wildcard_empty() -> None:
    specs = [
        _spec_touching("dm.sales"),
        _spec_touching("dm.sales", "ext.rates"),
        _spec_touching("finance.ledger"),
        _spec_touching("dm.sales", "finance.ledger"),
    ]
    for spec in specs:
        tables = spec_tables(spec)
        assert forbidden_tables(spec, ["*"]) == []
        # allow exactly the schemas present → empty forbidden
        present = sorted({schema_of(t) for t in tables})
        assert forbidden_tables(spec, present) == []
        # deny all → every touched table is forbidden (sorted)
        assert forbidden_tables(spec, []) == sorted(tables)
        # allow only dm → any non-dm table is forbidden
        denied = forbidden_tables(spec, ["dm"])
        assert denied == sorted(t for t in tables if schema_of(t) != "dm")
        assert set(denied).issubset(tables)


def test_forbidden_metamorphic_wider_allowlist_never_adds_denials() -> None:
    """Expanding allowed schemas is monotonic: forbidden set can only shrink."""
    rng = random.Random(99)
    model_tables = ["dm.sales", "ext.rates", "finance.ledger", "ops.metrics"]
    for _ in range(30):
        n = rng.randint(1, 3)
        touched = rng.sample(model_tables, n)
        spec = _spec_touching(*touched)
        base = list({schema_of(t) for t in touched})
        # start from a random subset of base schemas (possibly empty)
        k = rng.randint(0, len(base))
        narrow = rng.sample(base, k)
        wide = list(set(narrow) | {rng.choice(base)})
        denied_n = set(forbidden_tables(spec, narrow))
        denied_w = set(forbidden_tables(spec, wide))
        assert denied_w.issubset(denied_n)


def test_filter_model_drops_tables_forbidden_in_specs() -> None:
    """Any table remaining after filter is allowed for a synthetic all-tables spec."""
    model = _multi_schema_model()
    for allowed in (["dm"], ["ext"], ["dm", "ext"], ["finance"], []):
        scoped = filter_model_by_schemas(model, allowed)
        if not scoped.tables:
            continue
        # spec over every remaining table — RBAC must report nothing forbidden
        names = [t.name for t in scoped.tables]
        spec = _spec_touching(*names[:2] if len(names) > 1 else names)
        assert forbidden_tables(spec, allowed) == []
        # original model tables outside allowlist stay forbidden if used
        outside = [t.name for t in model.tables if not is_table_allowed(t.name, allowed)]
        if outside:
            bad = _spec_touching(outside[0])
            assert outside[0] in forbidden_tables(bad, allowed)


def test_raw_sql_label_cannot_smuggle_other_schema() -> None:
    """Property: raw_sql AST tables participate in RBAC regardless of query.table label."""
    rng = random.Random(3)
    labels = ["dm.allowed", "dm.sales", "public.view"]
    secrets = ["finance.secret", "hr.salary", "audit.trail"]
    for _ in range(15):
        label = rng.choice(labels)
        secret = rng.choice(secrets)
        spec = DashboardSpec(
            title="hatch",
            charts=[
                ChartSpec(
                    id="raw",
                    title="raw",
                    viz=Viz.TABLE,
                    query=ChartQuery(
                        table=label,
                        dimensions=["id"],
                        raw_sql=f"SELECT id FROM {secret}",
                    ),
                )
            ],
        )
        # label schema only → secret always forbidden
        label_schema = schema_of(label)
        denied = forbidden_tables(spec, [label_schema])
        assert secret in denied
        # wildcard still empty
        assert forbidden_tables(spec, ["*"]) == []


# --- Superset native-filter scope --------------------------------------------


def _native_filter_model() -> SemanticModel:
    return SemanticModel(
        tables=[
            Table(
                name="dm.sales",
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
            ),
            Table(
                name="dm.stores",
                grain=["id"],
                columns=[
                    Column(name="id", type="UInt32", role=ColumnRole.DIMENSION),
                    Column(name="name", type="String", role=ColumnRole.DIMENSION),
                ],
            ),
        ],
        joins=[Join(left="dm.sales.store_id", right="dm.stores.id")],
    )


def _native_filter_charts() -> list[ChartSpec]:
    joined = JoinSpec(
        table="dm.stores",
        on_left="dm.sales.store_id",
        on_right="dm.stores.id",
    )
    return [
        ChartSpec(
            id="source_kpi",
            title="Revenue",
            viz=Viz.BIG_NUMBER,
            query=ChartQuery(
                table="dm.sales",
                measures=[Measure(column="revenue", agg=Aggregation.SUM)],
            ),
        ),
        ChartSpec(
            id="source_joined",
            title="Revenue by store",
            viz=Viz.BAR,
            query=ChartQuery(
                table="dm.sales",
                dimensions=["dm.stores.name"],
                measures=[Measure(column="revenue", agg=Aggregation.SUM)],
                joins=[joined],
            ),
        ),
        ChartSpec(
            id="own_store",
            title="Share by store id",
            viz=Viz.BAR,
            query=ChartQuery(
                table="dm.sales",
                dimensions=["store_id"],
                measures=[
                    Measure(
                        column="revenue",
                        agg=Aggregation.SUM,
                        transform=MeasureTransform.SHARE_OF_TOTAL,
                    )
                ],
            ),
        ),
        ChartSpec(
            id="own_joined",
            title="Share by store name",
            viz=Viz.BAR,
            query=ChartQuery(
                table="dm.sales",
                dimensions=["dm.stores.name"],
                measures=[
                    Measure(
                        column="revenue",
                        agg=Aggregation.SUM,
                        transform=MeasureTransform.SHARE_OF_TOTAL,
                    )
                ],
                joins=[joined],
            ),
        ),
    ]


def test_native_filter_scope_is_order_invariant_and_partitions_placements() -> None:
    """Spec-side scope and compiled Superset scope stay equivalent under reordering."""
    model = _native_filter_model()
    charts = _native_filter_charts()
    filters = [
        DashboardFilter(column="dm.sales.store_id", type="value"),
        DashboardFilter(column="dm.sales.date", type="time_range"),
        DashboardFilter(column="dm.stores.name", type="value"),
        DashboardFilter(column="dm.missing.ghost", type="value"),
    ]
    rng = random.Random(20260723)
    expected_participants = {"source_kpi", "source_joined", "own_store"}

    for _ in range(24):
        spec = DashboardSpec(
            title="native filter properties",
            target_bi=TargetBI.SUPERSET,
            charts=rng.sample(charts, len(charts)),
            filters=rng.sample(filters, len(filters)),
        )
        placements = [(chart, 100 + index, 500 + index) for index, chart in enumerate(spec.charts)]
        plan = plan_datasets(spec)
        config, applied = build_native_filter_configuration(spec, placements, model, plan)
        all_slice_ids = [slice_id for _, slice_id, _ in placements]
        slice_by_chart = {chart.id: slice_id for chart, slice_id, _ in placements}

        wired_participants: set[str] = set()
        assert len(config) == len(applied)
        for compiled, (filter_, in_scope, excluded) in zip(config, applied, strict=True):
            expected_ids = {
                chart.id
                for chart in spec.charts
                if chart_accepts_filter(chart, filter_, spec, plan, model)
            }
            expected_slices = [
                slice_by_chart[chart.id] for chart in spec.charts if chart.id in expected_ids
            ]
            assert in_scope == expected_slices
            assert compiled["chartsInScope"] == expected_slices
            assert excluded == [sid for sid in all_slice_ids if sid not in expected_slices]
            assert compiled["scope"]["excluded"] == excluded
            assert set(in_scope).isdisjoint(excluded)
            assert set(in_scope) | set(excluded) == set(all_slice_ids)
            wired_participants.update(expected_ids)

        assert participating_chart_ids(spec, model) == expected_participants
        assert wired_participants == expected_participants
