"""Metric-derived chart gaps fire only for a change that reaches an in-scope product.

`chart.metric_unread`, `chart.metric_unknown` and `chart.formula_untyped` answer a
question an in-scope product asked. A change that reaches none does not ask them.
Field reads and tile-level gaps (`chart.opaque_sql`, `chart.sql_partial`,
`chart.dashboard_spec_missing`) are not metric-derived and still fire.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from auto_bi.decision.impact import (
    ChartRelation,
    ImpactReport,
    _ChartResolver,
    resolve_impact,
)
from auto_bi.decision.spec import DecisionProductSpec
from auto_bi.ir.spec import ChartQuery
from auto_bi.semantic.diff import diff_snapshots
from auto_bi.semantic.metric_contract import MetricTerm
from tests.test_decision_chart_impact import (
    DASHBOARD,
    REVENUE_CONTRACT,
    _reformulated,
    _relabelled,
    both_products,
    dashboard,
    impact_of,
    operator_dashboard,
    resolved,
    snapshots,
)
from tests.test_decision_impact import product
from tests.test_semantic_gateway import contract

#: Parseable operator SQL that names the metric's columns: the resolver cannot claim
#: `computes_metric` from it, so a reaching change raises `chart.metric_unread`.
METRIC_SQL = "SELECT region, sum(gross_margin)/sum(revenue) FROM dm.sales_daily GROUP BY region"
TILE = f"{DASHBOARD}#operator_table"


def product_out_of_scope() -> list[DecisionProductSpec]:
    """Delivers the same dashboard; depends on a metric this diff never touches."""
    scope = product().scope.model_dump()
    scope["metrics"] = ["metric://ops/on_time_rate@1"]
    return [product(scope=scope)]


def gaps(report: ImpactReport) -> list[tuple[str, str]]:
    return [(gap.code, gap.subject) for gap in report.chart_gaps]


def test_typed_metric_change_that_reaches_no_product_does_not_raise_metric_unread(
    tmp_path: Path,
) -> None:
    _, report = resolved(
        tmp_path,
        candidate=_reformulated(),
        dashboards={DASHBOARD: operator_dashboard(METRIC_SQL)},
        products=product_out_of_scope(),
    )
    impact = impact_of(report, "metric.formula_changed")
    assert impact.products == []
    assert "chart.metric_unread" not in {gap.code for gap in report.chart_gaps}


def test_typed_metric_change_that_reaches_a_product_still_raises_metric_unread(
    tmp_path: Path,
) -> None:
    _, report = resolved(
        tmp_path,
        candidate=_reformulated(),
        dashboards={DASHBOARD: operator_dashboard(METRIC_SQL)},
        products=both_products(),
    )
    impact = impact_of(report, "metric.formula_changed")
    assert impact.products == ["decision://sales/weekly_margin@1"]
    assert gaps(report) == [("chart.metric_unread", TILE)]


def test_opaque_sql_is_still_named_when_the_change_reaches_nobody(tmp_path: Path) -> None:
    _, report = resolved(
        tmp_path,
        candidate=_reformulated(),
        dashboards={DASHBOARD: operator_dashboard("this is not SQL")},
        products=product_out_of_scope(),
    )
    impact = impact_of(report, "metric.formula_changed")
    assert impact.products == []
    assert gaps(report) == [("chart.opaque_sql", TILE)]


def test_field_change_that_carries_no_metric_still_resolves_tile_reads(tmp_path: Path) -> None:
    """`dm.stores.id` is not a formula/grain/time field of the scoped metrics."""
    _, report = resolved(
        tmp_path,
        candidate={"semantic": _relabelled(1, 0, "Store surrogate key")},
        dashboards={DASHBOARD: dashboard()},
        products=both_products(),
    )
    impact = impact_of(report, "field.description_changed")
    assert impact.metrics == []
    assert impact.products == []
    assert [chart.chart for chart in impact.charts] == ["stores_covered"]
    assert impact.charts[0].relation is ChartRelation.READS_FIELD


def test_untyped_formula_does_not_raise_when_the_change_reaches_nobody(tmp_path: Path) -> None:
    external = contract(
        formula={
            "kind": "external_ref",
            "external": {
                "source": "dbt",
                "ref": "metric.sales.gross_margin_pct",
                "source_revision": "3f1c2b9a4d5e6f708192a3b4c5d6e7f809a1b2c3",
            },
        }
    )
    _, report = resolved(
        tmp_path,
        candidate={"contracts": {"m.yaml": external, "revenue.yaml": REVENUE_CONTRACT}},
        products=product_out_of_scope(),
    )
    impact = impact_of(report, "metric.formula_changed")
    assert impact.products == []
    assert "chart.formula_untyped" not in {gap.code for gap in report.chart_gaps}


def test_unknown_metric_does_not_raise_when_the_change_reaches_nobody(tmp_path: Path) -> None:
    base, head = snapshots(tmp_path, **_reformulated())
    report = resolve_impact(
        diff_snapshots(base, head),
        product_out_of_scope(),
        dashboards={DASHBOARD: dashboard()},
        metrics=[],
    )
    impact = impact_of(report, "metric.formula_changed")
    assert impact.products == []
    assert "chart.metric_unknown" not in {gap.code for gap in report.chart_gaps}


def test_partial_sql_is_still_named_when_the_change_reaches_nobody(tmp_path: Path) -> None:
    _, report = resolved(
        tmp_path,
        candidate=_reformulated(),
        dashboards={DASHBOARD: operator_dashboard("SELECT * FROM dm.sales_daily")},
        products=product_out_of_scope(),
    )
    impact = impact_of(report, "metric.formula_changed")
    assert impact.products == []
    assert gaps(report) == [("chart.sql_partial", TILE)]


def test_missing_dashboard_spec_is_still_named_when_the_change_reaches_nobody(
    tmp_path: Path,
) -> None:
    _, report = resolved(
        tmp_path,
        candidate=_reformulated(),
        dashboards={},
        products=product_out_of_scope(),
    )
    impact = impact_of(report, "metric.formula_changed")
    assert impact.products == []
    assert gaps(report) == [("chart.dashboard_spec_missing", DASHBOARD)]


_REPO = Path(__file__).resolve().parents[1]


def test_match_raw_guards_metric_unread_with_reaches_product() -> None:
    """One mechanism: `_match_raw` sees the real `computed` mapping and the flag."""
    params = inspect.signature(_ChartResolver._match_raw).parameters
    assert params["reaches_product"].kind is inspect.Parameter.KEYWORD_ONLY
    doc = (inspect.getdoc(_ChartResolver._match_raw) or "").replace("\n", " ")
    assert "reaches an in-scope product" in doc
    query = ChartQuery(table="dm.sales_daily", raw_sql=METRIC_SQL)
    term = MetricTerm(table="dm.sales_daily", column="gross_margin", agg="sum")
    computed = {"metric://sales/gross_margin_pct": (term,)}
    withheld = _ChartResolver([], {}, [])
    withheld._match_raw("d", "c", query, computed, set(), reaches_product=False)
    assert "chart.metric_unread" not in {gap.code for gap in withheld.gaps()}
    raised = _ChartResolver([], {}, [])
    raised._match_raw("d", "c", query, computed, set(), reaches_product=True)
    assert "chart.metric_unread" in {gap.code for gap in raised.gaps()}


def test_current_state_residual_records_metric_gaps_only_for_a_reaching_change() -> None:
    """The refuted per-change recording is gone; the codes stay named.

    Wording is not pinned: T-09 rewrites the residual prose (the rule moves into
    «Коротко») and may still write «открытым решением» for the half that is open —
    per-product scoping of a gap on a reaching change. `chart.metric_unknown` is not
    pinned either: the document names it in that bullet only, so pinning it would fail
    a rewrite the docs slice is chartered to make and, being a docs slice, may not repair.
    """
    text = (_REPO / "docs" / "DECISIONOPS_CURRENT_STATE.md").read_text(encoding="utf-8")
    assert "записывается для каждого изменения" not in text
    assert "chart.metric_unread" in text
    assert "chart.formula_untyped" in text
