"""Other products' validation issues are visible, without grading them here."""

from pathlib import Path

import pytest

from auto_bi.decision.validate import ValidationIssue
from auto_bi.severity import Severity
from auto_bi.verification.model import Verdict
from auto_bi.verification.summary import render_summary
from tests.test_decision_verify import PRODUCT_ID
from tests.test_gate_summary import FACTS, one_check
from tests.test_validation_issue_scope import (
    MOVED_PIN,
    OTHER,
    VERIFIED,
    WITH_STORE,
    _beside,
    _payload,
    _product,
    _two_products,
)

HEADING = "## Validation issues for other products"


def test_other_products_issues_are_visible_but_do_not_block(tmp_path: Path) -> None:
    case = _two_products(tmp_path, metrics=[MOVED_PIN])
    report = case.run(product_id=PRODUCT_ID)
    text = (tmp_path / "evidence" / report.run_id / "reports" / "summary.md").read_text(
        encoding="utf-8"
    )
    assert HEADING in text
    assert report.verdict is Verdict.PASS_WITH_ADVISORY
    assert report.blocking() == []
    assert "## What stops the release" not in text
    assert "## What needs a human" not in text
    assert text.index("## What was not done") < text.index(HEADING)
    assert text.index(HEADING) < text.index("## How to read this evidence")
    section = text.split(HEADING)[1].split("\n## ")[0]
    assert "They do not change this run's verdict." in section
    for issue in _payload(case, report)["other_product_issues"]:
        # Renderer escapes punctuation, retaining each literal value.
        for field in ("source", "code", "subject", "message"):
            assert issue[field] in section.replace("\\", "")
        assert f"reported severity: {issue['severity']}" in section


def test_no_other_issues_keeps_previous_summary_bytes() -> None:
    baseline = render_summary(FACTS, [one_check()])
    assert render_summary(FACTS, [one_check()], other_product_issues=()) == baseline
    assert HEADING not in baseline


def test_own_issues_remain_blocking_and_outside_new_section(tmp_path: Path) -> None:
    case = _two_products(tmp_path, metrics=[MOVED_PIN])
    _beside(case, _product(PRODUCT_ID, dimensions=WITH_STORE))
    report = case.run(product_id=PRODUCT_ID)
    text = (tmp_path / "evidence" / report.run_id / "reports" / "summary.md").read_text(
        encoding="utf-8"
    )
    payload = _payload(case, report)
    assert report.verdict is Verdict.BLOCK
    assert payload["issues"][0]["source"] == VERIFIED
    assert payload["other_product_issues"][0]["source"] == OTHER
    assert "## What stops the release" in text
    section = text.split(HEADING)[1].split("\n## ")[0].replace("\\", "")
    assert OTHER in section
    assert VERIFIED not in section
    assert "dimension.not_allowed" not in section


@pytest.mark.parametrize("field", ["source", "code", "subject", "message"])
def test_new_section_treats_free_text_as_literal_data(field: str) -> None:
    values = dict(
        code="issue.first", subject="metric://one", source="decision://other", message="x"
    )
    values[field] = "[link](url) <b> `code` | *bold*\n## injected\u2028end\\tail"
    issue = ValidationIssue(severity=Severity.BLOCK, **values)
    text = render_summary(FACTS, [one_check()], other_product_issues=[issue])
    section = text.split(HEADING)[1].split("\n## ")[0]
    assert r"\[link\]\(url\) \<b\> \`code\` \| \*bold\*\\n\#\# injected\\u2028end\\tail" in section
    assert "\n## injected" not in text
    assert "**Verdict:** pass" in text
    assert "## What stops the release" not in text


def test_issue_order_and_duplicates_are_preserved() -> None:
    first = ValidationIssue(
        code="first",
        severity=Severity.REVIEW,
        source="other",
        subject="one",
        message="first-message",
    )
    second = first.model_copy(update={"code": "second", "severity": Severity.BLOCK})
    text = render_summary(FACTS, [one_check()], other_product_issues=[second, first, second])
    section = text.split(HEADING)[1].split("\n## ")[0]
    assert section.index("second") < section.index("first")
    assert section.count("second") == 2
    assert section.count("reported severity: block") == 2
    assert section.count("reported severity: review") == 1
