"""Impact of semantic changes on decision products (ARCHITECTURE §8.3, bootstrap task 4).

The impact chain ends at a person: source field → metric → chart/dashboard → decision
product → owner. This is also where a `review` classification becomes a `block`: a formula
or grain change is a question in the abstract and a release stopper the moment a governed
product depends on it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from auto_bi.decision.impact import ImpactReport, resolve_impact
from auto_bi.decision.spec import DecisionProductSpec
from auto_bi.semantic.diff import diff_snapshots
from auto_bi.severity import Severity
from tests.test_semantic_diff import snap
from tests.test_semantic_gateway import contract, model

FIXTURE = Path(__file__).parent / "fixtures" / "decision" / "weekly_margin.yaml"


def product(**overrides: Any) -> DecisionProductSpec:
    data = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    data.update(overrides)
    return DecisionProductSpec.model_validate(data)


def other_product() -> DecisionProductSpec:
    return product(
        metadata={
            "id": "decision://finance/margin_watch",
            "version": 2,
            "owner": "finance-controlling",
            "risk_tier": "high",
        },
        delivery={"dashboards": [{"ref": "dashboard://margin_watch@1"}]},
    )


def unrelated_product() -> DecisionProductSpec:
    return product(
        metadata={
            "id": "decision://ops/delivery_sla",
            "version": 1,
            "owner": "operations",
            "risk_tier": "low",
        },
        scope={
            "metrics": ["metric://ops/on_time_rate@1"],
            "dimensions": [],
            "time_window": "P13W",
        },
    )


def review_diff(tmp_path: Path) -> Any:
    """A formula change: `review` by classification, escalated only by impact."""
    reformulated = contract()
    reformulated["formula"]["denominator"]["column"] = "gross_margin"
    return diff_snapshots(
        snap(tmp_path, "base"), snap(tmp_path, "candidate", contracts={"m.yaml": reformulated})
    )


def test_review_becomes_block_when_a_product_depends_on_the_metric(tmp_path: Path) -> None:
    report = resolve_impact(review_diff(tmp_path), [product()])
    impact = next(item for item in report.impacts if item.code == "metric.formula_changed")
    assert impact.classified_severity is Severity.REVIEW
    assert impact.severity is Severity.BLOCK
    assert impact.products == ["decision://sales/weekly_margin@1"]
    assert impact.owners == ["commercial-analytics"]
    assert impact.dashboards == ["dashboard://weekly_margin@7"]


def test_review_stays_review_when_nothing_depends_on_the_metric(tmp_path: Path) -> None:
    report = resolve_impact(review_diff(tmp_path), [unrelated_product()])
    impact = next(item for item in report.impacts if item.code == "metric.formula_changed")
    assert impact.severity is Severity.REVIEW
    assert impact.products == []
    assert impact.owners == []


def test_pin_version_does_not_hide_a_change(tmp_path: Path) -> None:
    """The product pins @3; a change to that metric reaches it whatever the new version is."""
    report = resolve_impact(
        review_diff(tmp_path),
        [
            product(
                scope={
                    **product().scope.model_dump(),
                    "metrics": ["metric://sales/gross_margin_pct@9"],
                }
            )
        ],
    )
    impact = next(item for item in report.impacts if item.code == "metric.formula_changed")
    assert impact.products == ["decision://sales/weekly_margin@1"]


def test_advisory_never_escalates(tmp_path: Path) -> None:
    relabelled = model()
    relabelled.tables[0].columns[4].description = "Revenue, net of VAT"
    diff = diff_snapshots(snap(tmp_path, "base"), snap(tmp_path, "candidate", semantic=relabelled))
    report = resolve_impact(diff, [product()])
    impact = next(item for item in report.impacts if item.code == "field.description_changed")
    assert impact.severity is Severity.ADVISORY


def test_block_stays_block_and_still_names_the_owner(tmp_path: Path) -> None:
    trimmed = model()
    trimmed.tables[0].columns = [
        column for column in trimmed.tables[0].columns if column.name != "gross_margin"
    ]
    diff = diff_snapshots(snap(tmp_path, "base"), snap(tmp_path, "candidate", semantic=trimmed))
    impact = next(
        item for item in resolve_impact(diff, [product()]).impacts if item.code == "field.removed"
    )
    assert impact.severity is Severity.BLOCK
    assert impact.metrics == ["metric://sales/gross_margin_pct"]
    assert impact.owners == ["commercial-analytics"]


def test_every_dependent_product_and_owner_is_listed(tmp_path: Path) -> None:
    report = resolve_impact(
        review_diff(tmp_path), [other_product(), product(), unrelated_product()]
    )
    impact = next(item for item in report.impacts if item.code == "metric.formula_changed")
    assert impact.products == [
        "decision://finance/margin_watch@2",
        "decision://sales/weekly_margin@1",
    ]
    assert impact.owners == ["commercial-analytics", "finance-controlling"]
    assert impact.dashboards == ["dashboard://margin_watch@1", "dashboard://weekly_margin@7"]


def test_report_keeps_diff_order_and_round_trips(tmp_path: Path) -> None:
    diff = review_diff(tmp_path)
    report = resolve_impact(diff, [product()])
    assert [item.code for item in report.impacts] == [change.code for change in diff.changes]
    assert report.base_digest == diff.base_digest
    assert report.products_in_scope == ["decision://sales/weekly_margin@1"]
    assert ImpactReport.model_validate(report.model_dump(mode="json")) == report


def test_empty_diff_yields_an_empty_report(tmp_path: Path) -> None:
    diff = diff_snapshots(snap(tmp_path, "base"), snap(tmp_path, "candidate"))
    report = resolve_impact(diff, [product()])
    assert report.impacts == []
