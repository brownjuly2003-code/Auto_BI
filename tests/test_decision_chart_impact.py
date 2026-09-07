"""Chart-level impact: which chart, not just which dashboard (ARCHITECTURE §8.3).

§8.3 draws the chain `source field -> metric -> verified asset -> chart -> decision
product -> owner`, and the gate used to stop it at the dashboard ref. A dashboard is a
folder of numbers; an owner asked to review a change wants the tile.

Two relations are kept apart on purpose, because they are different claims:

* `reads_field` — the chart's query touches a column the change touches. Read straight off
  the IR, no inference.
* `computes_metric` — the chart's measure reproduces the metric's formula terms, so the
  tile *is* the governed number. A chart is never annotated with a metric ref in the IR, so
  this one is an inference, and it is only made on an exact term match.

Anything the IR cannot answer (operator SQL, a dashboard spec that was not loaded, a
formula with no typed terms) leaves a named gap instead of an empty list.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from auto_bi.decision.impact import ChartRelation, ImpactReport, resolve_impact
from auto_bi.decision.spec import DecisionProductSpec
from auto_bi.ir.spec import DashboardSpec
from auto_bi.semantic.diff import SemanticDiff, diff_snapshots
from auto_bi.semantic.gateway import SemanticSnapshot
from auto_bi.semantic.model import SemanticModel
from auto_bi.severity import Severity
from tests.test_decision_impact import product
from tests.test_semantic_diff import snap
from tests.test_semantic_gateway import contract, model

DASHBOARD = "dashboard://weekly_margin@7"

REVENUE_METRIC = "metric://sales/revenue"

#: A second, typed_aggregate contract beside the ratio one: `computes_metric` has to hold
#: for a plain aggregate too, and a window over that aggregate is still that number.
REVENUE_CONTRACT: dict[str, Any] = contract(
    metadata={
        "id": REVENUE_METRIC,
        "version": 1,
        "owner": "commercial-analytics",
        "status": "active",
    },
    formula={
        "kind": "typed_aggregate",
        "aggregate": {"table": "dm.sales_daily", "column": "revenue", "agg": "sum"},
    },
    unit={
        "unit": "RUB",
        "precision": 2,
        "null_semantics": "null_is_missing",
        "zero_semantics": "zero_is_value",
    },
)


def dashboard(*, with_raw_sql: bool = False) -> DashboardSpec:
    charts: list[dict[str, Any]] = [
        {
            # the ratio metric, term for term: numerator on the measure, denominator on its
            # `denominator` — this tile IS metric://sales/gross_margin_pct
            "id": "margin_by_region",
            "title": "Маржа по регионам",
            "viz": "bar",
            "query": {
                "table": "dm.sales_daily",
                "dimensions": ["region"],
                "measures": [
                    {
                        "column": "gross_margin",
                        "agg": "sum",
                        "denominator": {"column": "revenue", "agg": "sum"},
                    }
                ],
            },
        },
        {
            # a window over sum(revenue): the same base aggregate, so it moves with the
            # metric — and it is only half of the ratio, so it does not compute that one
            "id": "revenue_trend",
            "title": "Выручка, к прошлому периоду",
            "viz": "line",
            "query": {
                "table": "dm.sales_daily",
                "dimensions": ["date"],
                "measures": [{"column": "revenue", "agg": "sum", "transform": "pop_pct"}],
            },
        },
        {
            # reads gross_margin, computes nothing governed: avg is not the metric's agg
            "id": "margin_avg_by_category",
            "title": "Средняя маржа по категориям",
            "viz": "bar",
            "query": {
                "table": "dm.sales_daily",
                "dimensions": ["category"],
                "measures": [{"column": "gross_margin", "agg": "avg"}],
            },
        },
        {
            # touches neither measure: a joined dimension and a count over a key column
            "id": "stores_covered",
            "title": "Магазины",
            "viz": "big_number",
            "query": {
                "table": "dm.sales_daily",
                "dimensions": ["dm.stores.id"],
                "measures": [{"column": "store_id", "agg": "count_distinct"}],
                "joins": [
                    {
                        "table": "dm.stores",
                        "on_left": "dm.sales_daily.store_id",
                        "on_right": "dm.stores.id",
                    }
                ],
            },
        },
    ]
    if with_raw_sql:
        charts.append(
            {
                "id": "operator_table",
                "title": "Операторский срез",
                "viz": "table",
                "query": {
                    "table": "dm.sales_daily",
                    "raw_sql": "SELECT region, sum(revenue) FROM dm.sales_daily GROUP BY region",
                },
            }
        )
    return DashboardSpec.model_validate(
        {"title": "Недельная маржа", "target_bi": "superset", "charts": charts}
    )


def both_products() -> list[DecisionProductSpec]:
    """One product, scoped to both contracts, so both metrics reach the same dashboard."""
    scope = product().scope.model_dump()
    scope["metrics"] = ["metric://sales/gross_margin_pct@3", f"{REVENUE_METRIC}@1"]
    return [product(scope=scope)]


def snapshots(tmp_path: Path, **candidate: Any) -> tuple[SemanticSnapshot, SemanticSnapshot]:
    contracts = {"m.yaml": contract(), "revenue.yaml": REVENUE_CONTRACT}
    base = snap(tmp_path, "base", contracts=contracts)
    candidate.setdefault("contracts", contracts)
    return base, snap(tmp_path, "candidate", **candidate)


def resolved(
    tmp_path: Path,
    *,
    candidate: dict[str, Any] | None = None,
    dashboards: dict[str, DashboardSpec] | None = None,
    products: list[DecisionProductSpec] | None = None,
) -> tuple[SemanticDiff, ImpactReport]:
    base, head = snapshots(tmp_path, **(candidate or {}))
    diff = diff_snapshots(base, head)
    report = resolve_impact(
        diff,
        products if products is not None else both_products(),
        dashboards={DASHBOARD: dashboard()} if dashboards is None else dashboards,
        metrics=[*base.graph.metrics, *head.graph.metrics],
    )
    return diff, report


def impact_of(report: ImpactReport, code: str) -> Any:
    matches = [item for item in report.impacts if item.code == code]
    assert matches, f"{code} not in {[item.code for item in report.impacts]}"
    assert len(matches) == 1
    return matches[0]


def _reformulated() -> dict[str, Any]:
    changed = contract()
    changed["formula"]["denominator"]["column"] = "gross_margin"
    return {"contracts": {"m.yaml": changed, "revenue.yaml": REVENUE_CONTRACT}}


def _relabelled(table: int, column: int, text: str) -> SemanticModel:
    changed = model()
    changed.tables[table].columns[column].description = text
    return changed


def test_a_ratio_metric_resolves_to_the_tile_that_computes_it(tmp_path: Path) -> None:
    _, report = resolved(tmp_path, candidate=_reformulated())
    impact = impact_of(report, "metric.formula_changed")

    assert [(chart.ref, chart.relation) for chart in impact.charts] == [
        (f"{DASHBOARD}#margin_by_region", ChartRelation.COMPUTES_METRIC)
    ]
    assert impact.charts[0].dashboard == DASHBOARD
    assert impact.charts[0].chart == "margin_by_region"
    assert report.chart_gaps == []


def test_half_a_ratio_is_not_the_metric(tmp_path: Path) -> None:
    """`sum(revenue)` alone is the denominator, not the margin — naming it would be a claim."""
    _, report = resolved(tmp_path, candidate=_reformulated())
    impact = impact_of(report, "metric.formula_changed")
    assert [chart.chart for chart in impact.charts] == ["margin_by_region"]


def test_a_window_over_the_base_aggregate_still_computes_the_metric(tmp_path: Path) -> None:
    """`pop_pct` is a window over `sum(revenue)`: it moves exactly when the metric moves."""
    changed = dict(REVENUE_CONTRACT)
    changed["unit"] = {**REVENUE_CONTRACT["unit"], "precision": 4}
    _, report = resolved(
        tmp_path, candidate={"contracts": {"m.yaml": contract(), "revenue.yaml": changed}}
    )
    impact = impact_of(report, "metric.unit_changed")
    assert [chart.chart for chart in impact.charts] == ["revenue_trend"]
    assert impact.charts[0].relation is ChartRelation.COMPUTES_METRIC
    assert "pop_pct" in impact.charts[0].detail


def test_a_field_change_names_every_chart_that_reads_it(tmp_path: Path) -> None:
    """A removed column is read straight off the IR — no inference, both roles counted."""
    trimmed = model()
    trimmed.tables[0].columns = [
        column for column in trimmed.tables[0].columns if column.name != "gross_margin"
    ]
    _, report = resolved(tmp_path, candidate={"semantic": trimmed})
    impact = impact_of(report, "field.removed")

    by_chart = {chart.chart: chart.relation for chart in impact.charts}
    assert by_chart["margin_avg_by_category"] is ChartRelation.READS_FIELD
    # it reads the field too, but it also computes the metric — the stronger claim wins,
    # and one tile listed twice would read as two findings
    assert by_chart["margin_by_region"] is ChartRelation.COMPUTES_METRIC
    assert "stores_covered" not in by_chart


def test_a_joined_dimension_is_a_column_the_chart_reads(tmp_path: Path) -> None:
    _, report = resolved(tmp_path, candidate={"semantic": _relabelled(1, 0, "Store surrogate key")})
    impact = impact_of(report, "field.description_changed")
    assert [chart.chart for chart in impact.charts] == ["stores_covered"]
    assert impact.charts[0].relation is ChartRelation.READS_FIELD


def test_operator_sql_is_a_named_gap_not_an_absent_chart(tmp_path: Path) -> None:
    _, report = resolved(
        tmp_path,
        candidate=_reformulated(),
        dashboards={DASHBOARD: dashboard(with_raw_sql=True)},
    )
    assert [(gap.code, gap.subject) for gap in report.chart_gaps] == [
        ("chart.opaque_sql", f"{DASHBOARD}#operator_table")
    ]
    assert [chart.chart for chart in impact_of(report, "metric.formula_changed").charts] == [
        "margin_by_region"
    ]


def test_a_dashboard_spec_that_was_not_loaded_is_a_named_gap(tmp_path: Path) -> None:
    _, report = resolved(tmp_path, candidate=_reformulated(), dashboards={})
    impact = impact_of(report, "metric.formula_changed")
    assert impact.dashboards == [DASHBOARD]
    assert impact.charts == []
    assert [(gap.code, gap.subject) for gap in report.chart_gaps] == [
        ("chart.dashboard_spec_missing", DASHBOARD)
    ]


def test_an_untyped_formula_cannot_be_resolved_to_a_tile(tmp_path: Path) -> None:
    """external_ref carries no terms: the IR cannot say which tile computes it."""
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
    )
    impact = impact_of(report, "metric.formula_changed")
    # the typed side still names the tile that computed yesterday's definition; the
    # external side is a hole, and rounding it into that answer would hide it
    assert [chart.chart for chart in impact.charts] == ["margin_by_region"]
    assert [(gap.code, gap.subject) for gap in report.chart_gaps] == [
        ("chart.formula_untyped", "metric://sales/gross_margin_pct")
    ]


def test_a_metric_whose_definition_was_not_supplied_is_a_named_gap(tmp_path: Path) -> None:
    base, head = snapshots(tmp_path, **_reformulated())
    report = resolve_impact(
        diff_snapshots(base, head),
        both_products(),
        dashboards={DASHBOARD: dashboard()},
        metrics=[],
    )
    impact = impact_of(report, "metric.formula_changed")
    assert impact.charts == []
    assert [(gap.code, gap.subject) for gap in report.chart_gaps] == [
        ("chart.metric_unknown", "metric://sales/gross_margin_pct")
    ]


def test_a_tile_is_named_even_when_no_product_declares_the_metric(tmp_path: Path) -> None:
    """An undeclared dependency: the product does not scope the metric, the tile computes it.

    Grading is untouched — `products` stays empty, so the verdict is what it was. Saying
    nothing here would let a delivered tile move on a change the report called out of scope.
    """
    scope = product().scope.model_dump()
    scope["metrics"] = ["metric://ops/on_time_rate@1"]
    _, report = resolved(
        tmp_path,
        candidate=_reformulated(),
        dashboards={DASHBOARD: dashboard()},
        products=[product(scope=scope)],
    )
    impact = impact_of(report, "metric.formula_changed")
    assert impact.products == []
    assert impact.severity is Severity.REVIEW
    assert [chart.chart for chart in impact.charts] == ["margin_by_region"]


def test_a_change_no_tile_touches_names_no_tile(tmp_path: Path) -> None:
    """The resolver answers "none" only when it actually read the charts."""
    _, report = resolved(tmp_path, candidate={"semantic": _relabelled(0, 1, "Store key")})
    impact = impact_of(report, "field.description_changed")
    assert [chart.chart for chart in impact.charts] == ["stores_covered"]
    assert impact.charts[0].relation is ChartRelation.READS_FIELD


def test_charts_and_gaps_are_ordered_and_round_trip(tmp_path: Path) -> None:
    _, report = resolved(
        tmp_path,
        candidate={"semantic": _relabelled(0, 4, "Revenue, net of VAT")},
        dashboards={DASHBOARD: dashboard(with_raw_sql=True)},
    )
    impact = impact_of(report, "field.description_changed")
    refs = [chart.ref for chart in impact.charts]
    assert refs == sorted(refs)
    assert len(refs) > 1
    assert ImpactReport.model_validate(report.model_dump(mode="json")) == report


def test_an_empty_diff_reports_no_chart_gap(tmp_path: Path) -> None:
    """Nothing was resolved, so there is no hole in a resolution to report."""
    _, report = resolved(tmp_path, dashboards={})
    assert report.impacts == []
    assert report.chart_gaps == []


def test_resolution_is_deterministic(tmp_path: Path) -> None:
    first = resolved(tmp_path / "a", candidate=_reformulated())[1]
    second = resolved(tmp_path / "b", candidate=_reformulated())[1]
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
