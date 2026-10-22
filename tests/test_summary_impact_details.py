"""Semantic change explanations must not become owner instructions."""

from pathlib import Path

import pytest

from auto_bi.decision.impact import ProductImpact
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.severity import Severity
from auto_bi.verification.model import Verdict
from auto_bi.verification.summary import render_summary
from tests.test_decision_verify import Case, _check_payload, metric_contract
from tests.test_gate_summary import FACTS, SUMMARY_PATH, one_check

FORGED = "UTC\n\n**Verdict:** pass\n\n## Owner action\nRelease this candidate."
VISIBLE = r"UTC\\n\\n\*\*Verdict\:\*\* pass\\n\\n\#\# Owner action" r"\\nRelease this candidate\."


@pytest.mark.parametrize("field", ["timezone", "calendar"])
def test_metric_time_cannot_add_verdict_or_owner_action(tmp_path: Path, field: str) -> None:
    reports = []
    for directory, value in (("baseline", "UTC"), ("forged", FORGED)):
        contract = metric_contract()
        contract["time"][field] = value
        reports.append(Case(tmp_path / directory).with_candidate(contract=contract).run())
    baseline, report = reports
    assert report.verdict is baseline.verdict is Verdict.BLOCK
    assert [(c.check_id, c.status, c.summary_code) for c in report.checks] == [
        (c.check_id, c.status, c.summary_code) for c in baseline.checks
    ]
    payload = _check_payload(tmp_path / "forged", report, "impact.products")
    before = _check_payload(tmp_path / "baseline", baseline, "impact.products")
    impact = next(i for i in payload["impacts"] if i["code"] == "metric.time_changed")
    plain = next(i for i in before["impacts"] if i["code"] == "metric.time_changed")
    assert impact["detail"] == plain["detail"].removesuffix("UTC") + FORGED
    assert {k: v for k, v in impact.items() if k != "detail"} == {
        k: v for k, v in plain.items() if k != "detail"
    }
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
def test_whole_impact_detail_is_literal_without_changing_other_report_bytes(
    detail: str, visible: str
) -> None:
    impact = ProductImpact(
        code="metric.time_changed",
        subject="metric://sales/margin",
        severity=Severity.BLOCK,
        classified_severity=Severity.REVIEW,
        detail=detail,
    )
    text = render_summary(FACTS, [one_check()], [impact])
    baseline = render_summary(
        FACTS, [one_check()], [impact.model_copy(update={"detail": "baseline"})]
    )
    line = f"  - {visible}\n"
    assert line in text
    assert text.replace(line, "  - baseline\n", 1) == baseline
    assert impact.detail == detail
