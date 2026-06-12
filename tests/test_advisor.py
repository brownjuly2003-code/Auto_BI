"""Feasibility Advisor: rule pack fires on anti-patterns, stays silent on clean queries."""

from auto_bi.advisor import Advisor, Severity, VerdictClass
from auto_bi.ir.spec import ChartQuery, ChartSpec, FilterOp, Measure, QueryFilter, Viz
from auto_bi.semantic.model import (
    Aggregation,
    Column,
    ColumnRole,
    Physical,
    SemanticModel,
    Table,
)


def fact_model(
    *,
    engine: str = "MergeTree",
    sorting_key: list[str] | None = None,
    partition_key: str = "toYYYYMM(date)",
    rows: int = 100_000_000,
    cardinality: dict[str, int] | None = None,
) -> SemanticModel:
    cols = [
        Column(name="date", type="Date", role=ColumnRole.TIME),
        Column(name="store_id", type="UInt32", role=ColumnRole.DIMENSION),
        Column(name="manager_id", type="UInt32", role=ColumnRole.DIMENSION),
        Column(name="revenue", type="Decimal(18, 2)", role=ColumnRole.MEASURE, agg=Aggregation.SUM),
    ]
    physical = Physical(
        engine="clickhouse",
        table_engine=engine,
        sorting_key=sorting_key if sorting_key is not None else ["date", "store_id"],
        partition_key=partition_key,
        rows=rows,
        cardinality=cardinality or {},
    )
    return SemanticModel(tables=[Table(name="dm.sales_daily", columns=cols, physical=physical)])


def bar(**query_kwargs) -> ChartSpec:
    defaults = dict(
        table="dm.sales_daily",
        dimensions=["store_id"],
        measures=[Measure(column="revenue", agg=Aggregation.SUM)],
    )
    defaults.update(query_kwargs)
    return ChartSpec(id="c1", title="t", viz=Viz.BAR, query=ChartQuery(**defaults))


def date_filter() -> QueryFilter:
    return QueryFilter(column="date", op=FilterOp.GTE, value="2026-01-01")


def rules(findings) -> set[str]:
    return {f.rule for f in findings}


def test_clean_query_has_no_findings() -> None:
    # filters on the leading sorting/partition key, low-cardinality group by -> silent
    chart = bar(dimensions=["store_id"], filters=[date_filter()])
    assert Advisor(fact_model()).review_chart(chart) == []


def test_filter_off_sorting_key_prefix_is_spec_adjustment() -> None:
    # filter on store_id (in the key, but not the leading column date)
    chart = bar(filters=[QueryFilter(column="store_id", op=FilterOp.EQ, value=1)])
    findings = Advisor(fact_model()).review_chart(chart)
    f = next(x for x in findings if x.rule == "filter_not_in_sorting_key_prefix")
    assert f.verdict_class == VerdictClass.SPEC_ADJUSTMENT
    assert f.severity == Severity.CRITICAL  # 100M-row fact
    assert f.evidence["leading_key"] == "date"


def test_filter_off_key_entirely_is_dm_change_request() -> None:
    # manager_id is nowhere in the sorting key -> no spec tweak helps, DM mismatch
    chart = bar(filters=[QueryFilter(column="manager_id", op=FilterOp.EQ, value=7)])
    findings = Advisor(fact_model()).review_chart(chart)
    f = next(x for x in findings if x.rule == "filter_not_in_sorting_key_prefix")
    assert f.verdict_class == VerdictClass.DM_CHANGE_REQUEST


def test_partition_misaligned_filter_fires() -> None:
    chart = bar(filters=[QueryFilter(column="store_id", op=FilterOp.EQ, value=1)])
    assert "partition_misaligned_filter" in rules(Advisor(fact_model()).review_chart(chart))


def test_no_filter_on_large_fact_fires_and_partition_rule_does_not() -> None:
    chart = bar(filters=[])
    found = rules(Advisor(fact_model()).review_chart(chart))
    assert "no_filter_on_large_fact" in found
    assert "partition_misaligned_filter" not in found  # no overlap when there are no filters
    assert "filter_not_in_sorting_key_prefix" not in found


def test_small_table_no_filter_is_silent() -> None:
    chart = bar(filters=[])
    assert Advisor(fact_model(rows=4200, partition_key="")).review_chart(chart) == []


def test_group_by_high_cardinality_fires() -> None:
    model = fact_model(cardinality={"manager_id": 2_000_000})
    chart = bar(dimensions=["manager_id"], filters=[date_filter()])
    f = next(x for x in Advisor(model).review_chart(chart) if x.rule == "group_by_high_cardinality")
    assert f.evidence["cardinality"] == 2_000_000
    assert f.severity == Severity.WARN


def test_collapsing_engine_needs_final() -> None:
    chart = bar(filters=[date_filter()])
    findings = Advisor(fact_model(engine="ReplacingMergeTree")).review_chart(chart)
    assert "collapsing_engine_needs_final" in rules(findings)


def test_explain_high_scan_fraction_uses_measured_evidence() -> None:
    def run_query(sql: str) -> list[dict]:
        assert sql.startswith("EXPLAIN ESTIMATE ")
        return [
            {"database": "dm", "table": "sales_daily", "parts": 10, "rows": 95_000_000, "marks": 1}
        ]

    chart = bar(filters=[date_filter()])  # otherwise clean -> only the EXPLAIN rule fires
    f = next(
        x
        for x in Advisor(fact_model(), run_query=run_query).review_chart(chart)
        if x.rule == "explain_high_scan_fraction"
    )
    assert f.severity == Severity.CRITICAL
    assert f.evidence["scan_fraction"] == 0.95


def test_explain_failure_degrades_to_no_evidence() -> None:
    def run_query(sql: str) -> list[dict]:
        raise RuntimeError("clickhouse unreachable")

    chart = bar(filters=[date_filter()])
    # advisor is advisory-only: a failed EXPLAIN must not raise, just no measured finding
    findings = Advisor(fact_model(), run_query=run_query).review_chart(chart)
    assert "explain_high_scan_fraction" not in rules(findings)


def test_no_physical_metadata_no_findings() -> None:
    model = SemanticModel(
        tables=[
            Table(
                name="dm.sales_daily",
                columns=[Column(name="revenue", type="Float64", role=ColumnRole.MEASURE)],
                physical=None,
            )
        ]
    )
    assert Advisor(model).review_chart(bar(dimensions=[], filters=[])) == []


def test_review_aggregates_over_charts() -> None:
    spec_charts = [
        bar(filters=[]),  # no_filter_on_large_fact
        bar(filters=[QueryFilter(column="manager_id", op=FilterOp.EQ, value=7)]),  # dm_change
    ]
    from auto_bi.ir.spec import DashboardSpec

    spec = DashboardSpec(
        title="d",
        charts=[
            ChartSpec(id=f"c{i}", title="t", viz=Viz.BAR, query=c.query)
            for i, c in enumerate(spec_charts)
        ],
    )
    findings = Advisor(fact_model()).review(spec)
    assert {f.chart_id for f in findings} == {"c0", "c1"}
