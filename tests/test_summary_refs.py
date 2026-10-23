"""Product, metric and dashboard refs in the summary read as the refs the evidence names."""

from dataclasses import replace
from pathlib import Path

import pytest
from markdown_it import MarkdownIt

from auto_bi.decision.impact import ProductImpact
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.refs import ContractRef
from auto_bi.severity import Severity
from auto_bi.verification.model import Verdict
from auto_bi.verification.release_gate import GateReport
from auto_bi.verification.summary import render_summary
from tests.test_decision_verify import (
    Case,
    _check_payload,
    _write_semantic,
    _write_yaml,
    dashboard_spec,
    metric_contract,
    product_contract,
    semantic_model,
)
from tests.test_gate_summary import FACTS, SUMMARY_PATH, one_check

# Valid refs whose underscores CommonMark reads as emphasis when they are left raw.
PRODUCT = "decision://sales/margin-_weekly_-view"
METRIC = "metric://sales/gross-_margin_-pct"
DASHBOARD = "dashboard://weekly-_margin_-board@7"
PLAIN = ("decision://sales/weekly_margin", "metric://sales/gross_margin_pct")


def _visible(ref: str) -> str:
    return "".join(f"\\{char}" if char in ":/_-@" else char for char in ref)


def _run(case: Case, product: str, metric: str, dashboard: str) -> tuple[GateReport, str]:
    contract = metric_contract()
    contract["metadata"]["id"] = metric
    spec = product_contract()
    spec["metadata"]["id"] = product
    spec["scope"]["metrics"] = [f"{metric}@3"]
    spec["delivery"]["dashboards"] = [{"ref": dashboard}]
    _write_yaml(case.contracts / "weekly_margin.yaml", spec)
    _write_yaml(case.contracts / "gross_margin_pct.yaml", contract)
    _write_yaml(
        case.dashboards / f"{ContractRef.parse(dashboard).path[-1]}@7.yaml", dashboard_spec()
    )
    _write_semantic(case.base, semantic_model(), contract)
    candidate = semantic_model()
    next(c for c in candidate.tables[0].columns if c.name == "region").type = "UInt32"
    report = case.with_candidate(model=candidate, contract=contract).run()
    text = FilesystemArtifactStore(case.root / "evidence").get(report.run_id, SUMMARY_PATH)
    return report, text.decode()


def test_gate_run_summary_keeps_every_ref_without_changing_evidence(tmp_path: Path) -> None:
    baseline, _ = _run(Case(tmp_path / "plain"), *PLAIN, "dashboard://weekly_margin@7")
    report, text = _run(Case(tmp_path / "emphasis"), PRODUCT, METRIC, DASHBOARD)

    assert report.verdict is baseline.verdict is Verdict.BLOCK
    assert [(c.check_id, c.status, c.summary_code) for c in report.checks] == [
        (c.check_id, c.status, c.summary_code) for c in baseline.checks
    ]
    payload = _check_payload(tmp_path / "emphasis", report, "impact.products")
    item = next(i for i in payload["impacts"] if i["code"] == "field.type_changed")
    assert item["subject"] == "dm.sales_daily.region"
    assert item["products"] == [f"{PRODUCT}@1"]
    assert item["metrics"] == [METRIC]
    assert item["dashboards"] == [DASHBOARD]

    assert text.startswith(f"# Release gate — {_visible(PRODUCT)}\\@1\n")
    assert f"  - metrics: {_visible(METRIC)}\n" in text
    assert f"  - dashboards: {_visible(DASHBOARD)}\n" in text
    html = MarkdownIt("commonmark").render(text)
    assert f"<h1>Release gate — {PRODUCT}@1</h1>" in html
    assert f"<li>metrics: {METRIC}</li>" in html
    assert f"<li>dashboards: {DASHBOARD}</li>" in html
    assert "<em>" not in html


@pytest.mark.parametrize(
    "ref",
    [PRODUCT + "@1", "decision://sales/a-_b/c_-d@1", "decision://sales/weekly_margin@1"],
)
def test_product_ref_in_heading_is_literal(ref: str) -> None:
    text = render_summary(replace(FACTS, product_ref=ref), [one_check()])
    baseline = render_summary(replace(FACTS, product_ref="baseline"), [one_check()])

    heading = f"# Release gate — {_visible(ref)}\n"
    assert text.startswith(heading)
    assert text.replace(heading, "# Release gate — baseline\n", 1) == baseline
    assert f"<h1>Release gate — {ref}</h1>" in MarkdownIt("commonmark").render(text)


def test_metric_and_dashboard_refs_are_literal_in_impact_rows() -> None:
    metrics = ["metric://sales/gross-_margin", "metric://sales/net_-pct"]
    dashboards = [DASHBOARD, "dashboard://weekly_margin@7"]
    impact = ProductImpact(
        code="field.type_changed",
        subject="dm.sales_daily.region",
        severity=Severity.BLOCK,
        classified_severity=Severity.BLOCK,
        detail="type changed",
        metrics=metrics,
        dashboards=dashboards,
    )
    text = render_summary(FACTS, [one_check()], [impact])
    baseline = render_summary(
        FACTS, [one_check()], [impact.model_copy(update={"metrics": ["m"], "dashboards": ["d"]})]
    )

    metric_line = f"  - metrics: {', '.join(_visible(ref) for ref in metrics)}\n"
    dashboard_line = f"  - dashboards: {', '.join(_visible(ref) for ref in dashboards)}\n"
    assert metric_line in text
    assert dashboard_line in text
    restored = text.replace(metric_line, "  - metrics: m\n", 1)
    assert restored.replace(dashboard_line, "  - dashboards: d\n", 1) == baseline
    html = MarkdownIt("commonmark").render(text)
    assert f"<li>metrics: {', '.join(metrics)}</li>" in html
    assert f"<li>dashboards: {', '.join(dashboards)}</li>" in html
    assert impact.metrics == metrics
    assert impact.dashboards == dashboards


def test_a_metric_change_still_names_its_metric_once() -> None:
    impact = ProductImpact(
        code="metric.formula_changed",
        subject=METRIC,
        severity=Severity.BLOCK,
        classified_severity=Severity.BLOCK,
        detail="formula changed",
        metrics=[METRIC],
    )
    text = render_summary(FACTS, [one_check()], [impact])

    assert f"- **block** `metric.formula_changed` — {_visible(METRIC)}\n" in text
    assert "metrics:" not in text
