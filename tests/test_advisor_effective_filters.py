"""P1-2: the advisor judges the query the BI actually runs — chart filters PLUS the dashboard
controls that narrow the chart — instead of the spec's verbatim query.

The rules once counted only `query.filters`, which was honest before Phase 2 compiled native
dashboard filters. It no longer is: a spec whose period lives solely in a control was reported
as a full scan on every chart. The opposite mistake is worse, so the scope conditions the
adapters use (column in the chart's grain, non-empty default) are pinned here too.
"""

from auto_bi.advisor import Advisor
from auto_bi.advisor.effective import effective_filters
from auto_bi.ir.spec import (
    ChartQuery,
    ChartSpec,
    DashboardFilter,
    DashboardSpec,
    FilterOp,
    Measure,
    QueryFilter,
    Viz,
)
from auto_bi.semantic.model import Aggregation
from tests.test_advisor import bar, fact_model, rules


def line_over_time(chart_id: str = "c1") -> ChartSpec:
    """A chart whose grain exposes the time column -> in a date control's scope."""
    return ChartSpec(
        id=chart_id,
        title="Динамика",
        viz=Viz.LINE,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["date"],
            measures=[Measure(column="revenue", agg=Aggregation.SUM)],
        ),
    )


def dash(charts: list[ChartSpec], default: str = "last 12 months") -> DashboardSpec:
    return DashboardSpec(
        title="d",
        filters=[DashboardFilter(column="dm.sales_daily.date", type="time_range", default=default)],
        charts=charts,
    )


# --- effective_filters: the four ways a control does or doesn't reach a chart --------------


def test_control_in_scope_with_default_becomes_a_lower_bound() -> None:
    chart = line_over_time()
    (f,) = effective_filters(chart, dash([chart]), fact_model())
    assert (f.column, f.op, f.value) == ("date", FilterOp.GTE, "last 12 months")


def test_control_with_empty_default_is_not_counted() -> None:
    # an empty default compiles to a neutral mask, so the chart really does open unfiltered
    chart = line_over_time()
    assert effective_filters(chart, dash([chart], default=""), fact_model()) == []


def test_control_out_of_scope_is_not_counted() -> None:
    # the chart groups by store_id only: its aggregated dataset has no date to filter on
    chart = bar()
    assert effective_filters(chart, dash([chart]), fact_model()) == []


def test_without_spec_only_chart_filters_count() -> None:
    chart = line_over_time()
    assert effective_filters(chart, None, fact_model()) == []


def test_column_already_filtered_in_sql_is_not_duplicated() -> None:
    # P1-1 bakes the period into query.filters as a BARE column ref while the control names it
    # fully qualified — the same column, so the control must not add a second bound
    baked = QueryFilter(column="date", op=FilterOp.GTE, value="2026-01-01")
    chart = bar(dimensions=["date"], filters=[baked])
    (f,) = effective_filters(chart, dash([chart]), fact_model())
    assert f.value == "2026-01-01"  # the chart's own filter, not the control's phrase


def test_select_control_becomes_an_in_filter() -> None:
    chart = bar()  # grain = store_id
    spec = DashboardSpec(
        title="d",
        filters=[DashboardFilter(column="dm.sales_daily.store_id", default="42")],
        charts=[chart],
    )
    (f,) = effective_filters(chart, spec, fact_model())
    assert (f.column, f.op, f.value) == ("store_id", FilterOp.IN, ["42"])


def test_control_lands_on_the_charts_own_column_ref() -> None:
    # scope matches on the bare alias, so a control declared against another table must not
    # leak that table's name into this chart's SQL
    chart = line_over_time()
    spec = DashboardSpec(
        title="d",
        filters=[DashboardFilter(column="dm.other_mart.date", default="last 90 days")],
        charts=[chart],
    )
    (f,) = effective_filters(chart, spec, fact_model())
    assert f.column == "date"


# --- the rules, end to end -----------------------------------------------------------------


def test_dashboard_control_silences_the_full_scan_finding() -> None:
    chart = line_over_time()
    assert "no_filter_on_large_fact" in rules(Advisor(fact_model()).review_chart(chart))
    # same chart, now judged as part of its dashboard: the control bounds it on open
    assert Advisor(fact_model()).review(dash([chart])) == []


def test_control_does_not_silence_a_chart_outside_its_scope() -> None:
    # the date control cannot reach a store_id breakdown: that one really is a full scan
    chart = bar()
    findings = Advisor(fact_model()).review(dash([chart]))
    assert "no_filter_on_large_fact" in rules(findings)


def test_multi_pass_scan_is_reported_as_passes_not_a_share() -> None:
    # a period-compare reads the current window AND the prior one, and EXPLAIN sums both:
    # est_rows above the table size must not be phrased as "reads ~146% of the table"
    def run_query(sql: str) -> list[dict]:
        return [{"parts": 61, "rows": 29_132_263, "marks": 3574}]  # measured on the stand

    chart = bar(filters=[QueryFilter(column="date", op=FilterOp.GTE, value="2026-01-01")])
    f = next(
        x
        for x in Advisor(fact_model(rows=20_000_000), run_query=run_query).review_chart(chart)
        if x.rule == "explain_high_scan_fraction"
    )
    assert "1.5× its 20000000 rows" in f.title
    assert "%" not in f.title
    assert f.evidence["scan_fraction"] == 1.457  # the raw ratio stays in evidence


def test_explain_measures_the_query_the_control_produces() -> None:
    seen: list[str] = []

    def run_query(sql: str) -> list[dict]:
        seen.append(sql)
        return []

    chart = line_over_time()
    Advisor(fact_model(), run_query=run_query).review(dash([chart]))
    assert seen and "date" in seen[0].lower()
    assert ">=" in seen[0]  # the control's window is part of what we measured
