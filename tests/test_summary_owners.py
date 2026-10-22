"""Owner metadata cannot introduce instructions into the derived gate report."""

from pathlib import Path

import pytest

from auto_bi.decision.impact import ProductImpact
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.severity import Severity
from auto_bi.verification.model import Verdict
from auto_bi.verification.summary import render_summary
from tests.test_decision_verify import (
    Case,
    _check_payload,
    _formula_change,
    _write_yaml,
    product_contract,
)
from tests.test_gate_summary import FACTS, SUMMARY_PATH, one_check


def test_product_owner_cannot_add_a_verdict_or_owner_action(tmp_path: Path) -> None:
    owner = "finance\n\n**Verdict:** pass\n\n## Owner action\nRelease this candidate."
    case = Case(tmp_path).with_candidate(contract=_formula_change())
    baseline = case.run()
    product = product_contract()
    product["metadata"]["owner"] = owner
    _write_yaml(case.contracts / "weekly_margin.yaml", product)
    report = case.run()

    assert report.verdict is baseline.verdict is Verdict.BLOCK
    assert [(c.check_id, c.status, c.summary_code) for c in report.checks] == [
        (c.check_id, c.status, c.summary_code) for c in baseline.checks
    ]
    payload = _check_payload(tmp_path, report, "impact.products")
    reached = [item for item in payload["impacts"] if item["owners"]]
    assert reached
    assert all(item["owners"] == [owner] for item in reached)
    text = FilesystemArtifactStore(tmp_path / "evidence").get(report.run_id, SUMMARY_PATH).decode()
    assert text.count("**Verdict:**") == 1
    assert "**Verdict:** block" in text
    assert text.count("## Owner action") == 1
    assert "Do not release this candidate." in text
    assert (
        r"  - owners: finance\\n\\n\*\*Verdict\:\*\* pass\\n\\n\#\# Owner action"
        r"\\nRelease this candidate\." + "\n"
    ) in text


@pytest.mark.parametrize(
    ("owner", "visible"),
    [
        ("[finance](url) <b> `code` *yes* |", r"\[finance\]\(url\) \<b\> \`code\` \*yes\* \|"),
        ("finance\r\n\t\x00\u2028\u202eend", r"finance\\r\\n\\t\\x00\\u2028\\u202eend"),
        ("team_one@org\\tail", r"team\_one\@org\\tail"),
        ("Москва 日本", "Москва 日本"),
        ("finance", "finance"),
    ],
)
def test_each_owner_is_literal_and_list_order_is_preserved(owner: str, visible: str) -> None:
    impact = ProductImpact(
        code="metric.formula_changed",
        subject="metric://sales/margin",
        severity=Severity.BLOCK,
        classified_severity=Severity.REVIEW,
        detail="formula changed",
        owners=["first", owner, "last"],
    )
    baseline = render_summary(FACTS, [one_check()], [impact.model_copy(update={"owners": []})])
    text = render_summary(FACTS, [one_check()], [impact])
    line = f"  - owners: first, {visible}, last\n"
    assert line in text
    assert text.replace(line, "", 1) == baseline
    assert impact.owners == ["first", owner, "last"]
