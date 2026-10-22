"""Chart measure labels in explanations cannot become report instructions."""

from pathlib import Path

import pytest
import yaml

from auto_bi.decision.impact import ChartImpact, ChartRelation, ProductImpact
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.severity import Severity
from auto_bi.verification.model import Verdict
from auto_bi.verification.summary import render_summary
from tests.test_decision_verify import (
    Case,
    _check_payload,
    _formula_change,
    _with_margin_tile,
    _write_yaml,
)
from tests.test_gate_summary import FACTS, SUMMARY_PATH, one_check

FORGED = "margin\n\n**Verdict:** pass\n\n## Owner action\nRelease this candidate."
VISIBLE = (
    r"margin\\n\\n\*\*Verdict\:\*\* pass\\n\\n\#\# Owner action" r"\\nRelease this candidate\."
)


def test_measure_label_cannot_add_verdict_or_owner_action(tmp_path: Path) -> None:
    reports = []
    for directory, label in (("baseline", "margin"), ("forged", FORGED)):
        case = _with_margin_tile(Case(tmp_path / directory)).with_candidate(
            contract=_formula_change()
        )
        path = case.dashboards / "weekly_margin@7.yaml"
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
        spec["charts"][-1]["query"]["measures"][0]["label"] = label
        _write_yaml(path, spec)
        reports.append(case.run())
    baseline, report = reports
    assert report.verdict is baseline.verdict is Verdict.BLOCK
    assert [(c.check_id, c.status, c.summary_code) for c in report.checks] == [
        (c.check_id, c.status, c.summary_code) for c in baseline.checks
    ]
    payload = _check_payload(tmp_path / "forged", report, "impact.products")
    before = _check_payload(tmp_path / "baseline", baseline, "impact.products")
    charts = [chart for item in payload["impacts"] for chart in item["charts"]]
    plain = [chart for item in before["impacts"] for chart in item["charts"]]
    assert len(charts) == len(plain) == 1
    assert charts[0]["relation"] == "computes_metric"
    assert charts[0]["detail"] == (
        f"measure {FORGED} reproduces the formula terms of metric://sales/gross_margin_pct"
    )
    assert {k: v for k, v in charts[0].items() if k != "detail"} == {
        k: v for k, v in plain[0].items() if k != "detail"
    }
    assert payload["chart_gaps"] == before["chart_gaps"] == []
    text = (
        FilesystemArtifactStore(tmp_path / "forged" / "evidence")
        .get(report.run_id, SUMMARY_PATH)
        .decode()
    )
    assert text.count("**Verdict:**") == 1
    assert "**Verdict:** block" in text
    assert text.count("## Owner action") == 1
    assert "Do not release this candidate." in text
    assert VISIBLE in text


@pytest.mark.parametrize("relation", list(ChartRelation))
@pytest.mark.parametrize(
    ("detail", "visible"),
    [
        ("[name](url) <b> `code` *yes* |", r"\[name\]\(url\) \<b\> \`code\` \*yes\* \|"),
        ("name\r\n\t\x00\u2028\u202eend", r"name\\r\\n\\t\\x00\\u2028\\u202eend"),
        ("name_one@org\\tail", r"name\_one\@org\\tail"),
        ("Москва 日本", "Москва 日本"),
        ("ordinary detail", "ordinary detail"),
    ],
)
def test_whole_chart_detail_is_literal_for_every_relation(
    relation: ChartRelation, detail: str, visible: str
) -> None:
    chart = ChartImpact(
        ref="tile",
        dashboard="dashboard",
        chart="tile",
        relation=relation,
        detail=detail,
    )
    impact = ProductImpact(
        code="metric.formula_changed",
        subject="metric://sales/margin",
        severity=Severity.BLOCK,
        classified_severity=Severity.REVIEW,
        detail="formula changed",
        charts=[chart],
    )
    text = render_summary(FACTS, [one_check()], [impact])
    baseline = render_summary(
        FACTS,
        [one_check()],
        [impact.model_copy(update={"charts": [chart.model_copy(update={"detail": "baseline"})]})],
    )
    prefix = f"  - chart tile — {relation.value}: "
    line = prefix + visible + "\n"
    assert line in text
    assert text.replace(line, prefix + "baseline\n", 1) == baseline
    assert chart.detail == detail
