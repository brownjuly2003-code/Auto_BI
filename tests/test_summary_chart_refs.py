"""Chart identifiers stay data in impact rows and unresolved-chart subjects."""

from pathlib import Path

import pytest
import yaml

from auto_bi.decision.impact import ChartGap, ChartImpact, ChartRelation, ProductImpact
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.severity import Severity
from auto_bi.verification.model import Verdict
from auto_bi.verification.summary import render_summary
from tests.test_decision_verify import (
    Case,
    _check_payload,
    _formula_change,
    _with_margin_tile,
    _with_operator_tile,
    _write_yaml,
)
from tests.test_gate_summary import FACTS, SUMMARY_PATH, one_check

FORGED = "tile`\n\n**Verdict:** pass\n\n## Owner action\nRelease this candidate.\n\n`tail"
PREFIX = r"dashboard\:\/\/weekly\_margin\@7\#"
VISIBLE = (
    r"tile\`\\n\\n\*\*Verdict\:\*\* pass\\n\\n\#\# Owner action"
    r"\\nRelease this candidate\.\\n\\n\`tail"
)


@pytest.mark.parametrize("operator", [False, True], ids=["matched-chart", "chart-gap"])
def test_chart_id_cannot_add_verdict_or_owner_action(tmp_path: Path, operator: bool) -> None:
    case = Case(tmp_path).with_candidate(contract=_formula_change())
    if operator:
        _with_operator_tile(case, "SELECT sum(gross_margin) FROM dm.sales_daily")
    else:
        _with_margin_tile(case)
    baseline = case.run()
    path = case.dashboards / "weekly_margin@7.yaml"
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    spec["charts"][-1]["id"] = FORGED
    _write_yaml(path, spec)
    report = case.run()

    assert report.verdict is baseline.verdict is Verdict.BLOCK
    assert [(c.check_id, c.status, c.summary_code) for c in report.checks] == [
        (c.check_id, c.status, c.summary_code) for c in baseline.checks
    ]
    payload = _check_payload(tmp_path, report, "impact.products")
    ref = f"dashboard://weekly_margin@7#{FORGED}"
    if operator:
        assert [(gap["code"], gap["subject"]) for gap in payload["chart_gaps"]] == [
            ("chart.metric_unread", ref)
        ]
    else:
        charts = [chart for item in payload["impacts"] for chart in item["charts"]]
        assert [(chart["ref"], chart["chart"], chart["relation"]) for chart in charts] == [
            (ref, FORGED, "computes_metric")
        ]
        assert payload["chart_gaps"] == []
    text = FilesystemArtifactStore(tmp_path / "evidence").get(report.run_id, SUMMARY_PATH).decode()
    assert text.count("**Verdict:**") == 1
    assert "**Verdict:** block" in text
    assert text.count("## Owner action") == 1
    assert "Do not release this candidate." in text
    assert PREFIX + VISIBLE in text
    assert f"`{PREFIX}" not in text


@pytest.mark.parametrize(
    ("chart_id", "visible"),
    [
        ("[tile](url) <b> `code` *yes* |", r"\[tile\]\(url\) \<b\> \`code\` \*yes\* \|"),
        ("tile\r\n\t\x00\u2028\u202eend", r"tile\\r\\n\\t\\x00\\u2028\\u202eend"),
        ("tile_one@org\\tail", r"tile\_one\@org\\tail"),
        ("Москва 日本", "Москва 日本"),
        ("tile", "tile"),
    ],
)
def test_chart_refs_are_literal_in_both_impact_locations(chart_id: str, visible: str) -> None:
    ref = f"dashboard://weekly_margin@7#{chart_id}"
    chart = ChartImpact(
        ref=ref,
        dashboard="dashboard://weekly_margin@7",
        chart=chart_id,
        relation=ChartRelation.READS_FIELD,
        detail="query reads a field",
    )
    impact = ProductImpact(
        code="metric.formula_changed",
        subject="metric://sales/margin",
        severity=Severity.BLOCK,
        classified_severity=Severity.REVIEW,
        detail="formula changed",
        charts=[chart],
    )
    gap = ChartGap(code="chart.metric_unread", subject=ref, detail="metric not read")
    text = render_summary(FACTS, [one_check()], [impact], [gap])
    assert f"  - chart {PREFIX}{visible} — reads_field: query reads a field\n" in text
    assert f"- `chart.metric_unread` {PREFIX}{visible} — metric not read\n" in text
    assert chart.ref == gap.subject == ref
    assert chart.chart == chart_id
