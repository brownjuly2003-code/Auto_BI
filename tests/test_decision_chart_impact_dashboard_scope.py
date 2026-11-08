"""`chart.metric_unread` answers the question this dashboard's deliverers asked.

The resolver scans the dashboards of every in-scope product in one list, and a change
that reaches product A raised `chart.metric_unread` on a tile of a dashboard only
product B delivers — a gap about a metric no deliverer of that dashboard declares. The
metric relation of a raw-SQL tile is now decided against the union of metric ids the
in-scope products delivering that exact pinned dashboard ref declare: a shared dashboard
asks the union of all its deliverers, a mixed-metric change is filtered independently
per dashboard, and dashboard versions are not merged. Field reads, the IR term match
behind `computes_metric`, and the tile-level gaps (`chart.opaque_sql`,
`chart.sql_partial`, `chart.dashboard_spec_missing`) stay whole-change.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from auto_bi.decision.impact import ChartRelation, ImpactReport, resolve_impact
from auto_bi.decision.spec import DecisionProductSpec
from auto_bi.ir.spec import DashboardSpec
from auto_bi.semantic.diff import diff_snapshots
from tests.test_decision_chart_impact import (
    _reformulated,
    _relabelled,
    dashboard,
    impact_of,
    operator_dashboard,
    snapshots,
)
from tests.test_decision_impact import product
from tests.test_semantic_gateway import model

#: Parseable operator SQL naming the ratio metric's term columns: the resolver cannot
#: claim `computes_metric` from it, so a dashboard whose deliverer declares that metric
#: raises `chart.metric_unread` for it.
METRIC_SQL = "SELECT region, sum(gross_margin)/sum(revenue) FROM dm.sales_daily GROUP BY region"
#: Parseable and fully attributed, and it names none of the revenue metric's term columns
#: (it does name the ratio metric's `gross_margin`): whether it is `chart.metric_unread`
#: depends only on which metric the dashboard's deliverer declares.
GROSS_MARGIN_SQL = "SELECT sum(gross_margin) FROM dm.sales_daily"

A_DASH = "dashboard://sales/margin_board@3"
B_DASH = "dashboard://ops/delivery_board@2"
SHARED = "dashboard://sales/shared_board@5"

A_PRODUCT = "decision://sales/margin_a"
B_PRODUCT = "decision://ops/delivery_b"
MARGIN_PIN = "metric://sales/gross_margin_pct@3"
REVENUE_PIN = "metric://sales/revenue@1"
ON_TIME_PIN = "metric://ops/on_time_rate@1"


def scoped_product(pid: str, metrics: list[str], dashboards: list[str]) -> DecisionProductSpec:
    """A product that delivers exactly *dashboards* and scopes exactly *metrics*."""
    return product(
        metadata={
            "id": pid,
            "version": 1,
            "owner": "commercial-analytics",
            "risk_tier": "medium",
        },
        scope={"metrics": metrics, "dimensions": ["region"], "time_window": "P13W"},
        delivery={"dashboards": [{"ref": ref} for ref in dashboards]},
    )


def resolve(
    tmp_path: Path,
    *,
    candidate: dict[str, Any],
    dashboards: dict[str, DashboardSpec],
    products: list[DecisionProductSpec],
) -> ImpactReport:
    base, head = snapshots(tmp_path, **candidate)
    return resolve_impact(
        diff_snapshots(base, head),
        products,
        dashboards=dashboards,
        metrics=[*base.graph.metrics, *head.graph.metrics],
    )


def gaps(report: ImpactReport) -> list[tuple[str, str]]:
    return [(gap.code, gap.subject) for gap in report.chart_gaps]


def test_metric_unread_fires_only_on_the_dashboard_whose_deliverer_declares_the_metric(
    tmp_path: Path,
) -> None:
    """The false positive this slice closes: B's tile carried A's question."""
    report = resolve(
        tmp_path,
        candidate=_reformulated(),
        dashboards={A_DASH: operator_dashboard(METRIC_SQL), B_DASH: operator_dashboard(METRIC_SQL)},
        products=[
            scoped_product(A_PRODUCT, [MARGIN_PIN], [A_DASH]),
            scoped_product(B_PRODUCT, [ON_TIME_PIN], [B_DASH]),
        ],
    )
    impact = impact_of(report, "metric.formula_changed")
    assert impact.products == [f"{A_PRODUCT}@1"]
    assert gaps(report) == [("chart.metric_unread", f"{A_DASH}#operator_table")]


def test_a_shared_dashboard_uses_the_union_of_all_its_delivering_products(
    tmp_path: Path,
) -> None:
    """B not declaring the metric must not suppress the gap A's declaration asks for."""
    report = resolve(
        tmp_path,
        candidate=_reformulated(),
        dashboards={SHARED: operator_dashboard(METRIC_SQL)},
        products=[
            scoped_product(A_PRODUCT, [MARGIN_PIN], [SHARED]),
            scoped_product(B_PRODUCT, [ON_TIME_PIN], [SHARED]),
        ],
    )
    impact = impact_of(report, "metric.formula_changed")
    assert impact.products == [f"{A_PRODUCT}@1"]
    assert gaps(report) == [("chart.metric_unread", f"{SHARED}#operator_table")]


def _revenue_retyped() -> dict[str, Any]:
    """A type change on `dm.sales_daily.revenue`: one change both metrics carry."""
    typed = model()
    typed.tables[0].columns[4].type = "Decimal(20, 2)"
    return {"semantic": typed}


def test_a_mixed_metric_change_is_filtered_independently_per_dashboard(tmp_path: Path) -> None:
    """Both products are reached; each dashboard is asked only its own deliverer's half."""
    report = resolve(
        tmp_path,
        candidate=_revenue_retyped(),
        dashboards={
            A_DASH: operator_dashboard(GROSS_MARGIN_SQL),
            B_DASH: operator_dashboard(GROSS_MARGIN_SQL),
        },
        products=[
            scoped_product(A_PRODUCT, [MARGIN_PIN], [A_DASH]),
            scoped_product(B_PRODUCT, [REVENUE_PIN], [B_DASH]),
        ],
    )
    impact = impact_of(report, "field.type_changed")
    # The change itself is untouched: both metrics and both products stay on it.
    assert impact.metrics == ["metric://sales/gross_margin_pct", "metric://sales/revenue"]
    assert impact.products == [f"{B_PRODUCT}@1", f"{A_PRODUCT}@1"]
    # A's deliverer declares the ratio metric, whose terms include gross_margin — the
    # tile cannot be ruled out. B's deliverer declares only the revenue metric, whose
    # terms the fully-read tile does not name: that is an answer, not a hole.
    assert gaps(report) == [("chart.metric_unread", f"{A_DASH}#operator_table")]


def test_field_reads_stay_whole_change_on_a_dashboard_that_declares_nothing_changed(
    tmp_path: Path,
) -> None:
    """`reads_field` follows the change's fields, not any product's declaration."""
    report = resolve(
        tmp_path,
        candidate={
            "contracts": _reformulated()["contracts"],
            "semantic": _relabelled(0, 4, "Revenue, net of VAT"),
        },
        dashboards={
            A_DASH: operator_dashboard(METRIC_SQL),
            B_DASH: operator_dashboard("SELECT sum(revenue) FROM dm.sales_daily"),
        },
        products=[
            scoped_product(A_PRODUCT, [MARGIN_PIN], [A_DASH]),
            scoped_product(B_PRODUCT, [ON_TIME_PIN], [B_DASH]),
        ],
    )
    relabel = impact_of(report, "field.description_changed")
    # Both tiles read `revenue` — A's METRIC_SQL names it too: field reads follow the
    # change's fields on every dashboard, whatever its deliverer declares.
    assert [(chart.ref, chart.relation) for chart in relabel.charts] == [
        (f"{B_DASH}#operator_table", ChartRelation.READS_FIELD),
        (f"{A_DASH}#operator_table", ChartRelation.READS_FIELD),
    ]
    assert "dm.sales_daily.revenue" in relabel.charts[0].detail
    # The formula change still reaches A; B's tile, which A's change used to mark
    # `chart.metric_unread`, is not asked about a metric B does not declare.
    assert gaps(report) == [("chart.metric_unread", f"{A_DASH}#operator_table")]


def test_ir_computes_metric_keeps_the_whole_change_on_an_undeclaring_dashboard(
    tmp_path: Path,
) -> None:
    """The IR term match is not filtered by any dashboard's declaration."""
    report = resolve(
        tmp_path,
        candidate=_reformulated(),
        dashboards={A_DASH: operator_dashboard(METRIC_SQL), B_DASH: dashboard(with_raw_sql=True)},
        products=[
            scoped_product(A_PRODUCT, [MARGIN_PIN], [A_DASH]),
            scoped_product(B_PRODUCT, [ON_TIME_PIN], [B_DASH]),
        ],
    )
    impact = impact_of(report, "metric.formula_changed")
    # B declares nothing of this change, and the tile that reproduces the metric's terms
    # is still named: an undeclared dependency is evidence, not a gap.
    assert [(chart.ref, chart.relation) for chart in impact.charts] == [
        (f"{B_DASH}#margin_by_region", ChartRelation.COMPUTES_METRIC)
    ]
    # The operator tile on B's dashboard used to carry A's `chart.metric_unread` too.
    assert gaps(report) == [("chart.metric_unread", f"{A_DASH}#operator_table")]


def test_product_order_does_not_change_the_report(tmp_path: Path) -> None:
    """The declared union is a set union; nothing depends on the products' order."""
    a = scoped_product(A_PRODUCT, [MARGIN_PIN], [A_DASH])
    b = scoped_product(B_PRODUCT, [ON_TIME_PIN], [B_DASH])
    dashboards = {A_DASH: operator_dashboard(METRIC_SQL), B_DASH: operator_dashboard(METRIC_SQL)}
    first = resolve(
        tmp_path / "one", candidate=_reformulated(), dashboards=dashboards, products=[a, b]
    )
    second = resolve(
        tmp_path / "two", candidate=_reformulated(), dashboards=dashboards, products=[b, a]
    )
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert gaps(first) == [("chart.metric_unread", f"{A_DASH}#operator_table")]


def test_dashboard_versions_are_not_merged(tmp_path: Path) -> None:
    """`@7` and `@6` are different pinned refs; the older one keeps its own deliverers."""
    v7 = "dashboard://sales/versioned@7"
    v6 = "dashboard://sales/versioned@6"
    report = resolve(
        tmp_path,
        candidate=_reformulated(),
        dashboards={v7: operator_dashboard(METRIC_SQL), v6: operator_dashboard(METRIC_SQL)},
        products=[
            scoped_product(A_PRODUCT, [MARGIN_PIN], [v7]),
            scoped_product(B_PRODUCT, [ON_TIME_PIN], [v6]),
        ],
    )
    impact = impact_of(report, "metric.formula_changed")
    assert impact.dashboards == [v7]
    assert gaps(report) == [("chart.metric_unread", f"{v7}#operator_table")]


def test_structural_gaps_stay_regardless_of_declaration(tmp_path: Path) -> None:
    """Opaque, partial and missing specs are properties of the inputs, not of the metric."""
    opaque = "dashboard://ops/opaque@1"
    partial = "dashboard://ops/partial@1"
    missing = "dashboard://ops/missing@1"
    report = resolve(
        tmp_path,
        candidate=_reformulated(),
        dashboards={
            A_DASH: operator_dashboard(METRIC_SQL),
            opaque: operator_dashboard("this is not SQL"),
            partial: operator_dashboard("SELECT * FROM dm.sales_daily"),
        },
        products=[
            scoped_product(A_PRODUCT, [MARGIN_PIN], [A_DASH]),
            scoped_product(B_PRODUCT, [ON_TIME_PIN], [opaque, partial, missing]),
        ],
    )
    assert impact_of(report, "metric.formula_changed").products == [f"{A_PRODUCT}@1"]
    assert gaps(report) == [
        ("chart.dashboard_spec_missing", missing),
        ("chart.metric_unread", f"{A_DASH}#operator_table"),
        ("chart.opaque_sql", f"{opaque}#operator_table"),
        ("chart.sql_partial", f"{partial}#operator_table"),
    ]
