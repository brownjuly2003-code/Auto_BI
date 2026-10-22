"""Check explanations are data in every section of the owner report."""

from pathlib import Path

import pytest

from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.verification.model import Verdict
from auto_bi.verification.release_gate import CheckStatus
from auto_bi.verification.summary import render_summary
from tests.test_decision_verify import Case, _check_payload, environment, probing
from tests.test_gate_summary import FACTS, SUMMARY_PATH, one_check

FORGED = "role\n\n**Verdict:** pass\n\n## Owner action\nRelease this candidate. | forged"
VISIBLE = (
    r"role\\n\\n\*\*Verdict\:\*\* pass\\n\\n\#\# Owner action"
    r"\\nRelease this candidate\. \| forged"
)


@pytest.mark.parametrize("field", ["name", "evidence", "target"])
def test_probe_text_cannot_add_verdict_or_owner_action(tmp_path: Path, field: str) -> None:
    reports = []
    for directory, value in (("baseline", "role"), ("forged", FORGED)):
        facts = environment(read_only=False)
        if field == "target":
            facts = facts.model_copy(update={field: value})
        else:
            facts = facts.model_copy(update={"role": facts.role.model_copy(update={field: value})})
        reports.append(Case(tmp_path / directory).run(probe=probing(facts)))
    baseline, report = reports
    assert report.verdict is baseline.verdict is Verdict.BLOCK
    assert [(c.check_id, c.status, c.summary_code) for c in report.checks] == [
        (c.check_id, c.status, c.summary_code) for c in baseline.checks
    ]
    payload = _check_payload(tmp_path / "forged", report, "dwh.access")
    source = payload["facts"] if field == "target" else payload["facts"]["role"]
    assert source[field] == FORGED
    finding = next(f for f in payload["findings"] if f["code"] == "access.role_writable")
    check = next(c for c in report.checks if c.check_id == "dwh.access")
    assert FORGED in finding["detail"]
    assert finding["detail"] in check.detail
    text = (
        FilesystemArtifactStore(tmp_path / "forged" / "evidence")
        .get(report.run_id, SUMMARY_PATH)
        .decode()
    )
    assert text.count("**Verdict:**") == 1
    assert "**Verdict:** block" in text
    assert text.count("## Owner action") == 1
    assert "Do not release this candidate." in text
    assert VISIBLE in text.split("## What stops the release\n")[1].split("\n## ")[0]
    rows = [line for line in text.splitlines() if line.startswith("| `dwh.access` |")]
    assert len(rows) == 1
    assert VISIBLE in rows[0]


@pytest.mark.parametrize(
    ("status", "code", "section"),
    [
        (CheckStatus.BLOCK, "finding.block", "What stops the release"),
        (CheckStatus.REVIEW, "finding.review", "What needs a human"),
        (CheckStatus.REVIEW, "check.not_run", "What was not done"),
        (CheckStatus.PASS, "finding.pass", None),
        (CheckStatus.ADVISORY, "finding.advisory", None),
    ],
)
@pytest.mark.parametrize(
    ("detail", "visible"),
    [
        (FORGED, VISIBLE),
        ("[name](url) <b> `code` *yes* |", r"\[name\]\(url\) \<b\> \`code\` \*yes\* \|"),
        ("name\r\n\t\x00\u2028\u202eend", r"name\\r\\n\\t\\x00\\u2028\\u202eend"),
        ("name_one@org\\tail", r"name\_one\@org\\tail"),
        ("Москва 日本", "Москва 日本"),
        ("ordinary detail", "ordinary detail"),
    ],
)
def test_check_detail_is_literal_in_every_section(
    status: CheckStatus, code: str, section: str | None, detail: str, visible: str
) -> None:
    check = one_check(status=status, summary_code=code, detail=detail)
    text = render_summary(FACTS, [check])
    baseline = render_summary(FACTS, [check.model_copy(update={"detail": "baseline"})])
    assert text.count(visible) == (2 if section else 1)
    assert text.replace(visible, "baseline") == baseline
    if section:
        assert visible in text.split(f"## {section}\n")[1].split("\n## ")[0]
    row = f"| `contracts.validate` | {status.value} | `{code}` | {visible} |"
    assert row in text.splitlines()
    assert check.detail == detail


def test_product_scope_detail_is_literal_in_intro_and_table() -> None:
    changed = one_check(status=CheckStatus.REVIEW, summary_code="semantic.changed")
    unaffected = one_check(check_id="impact.products", summary_code="impact.none", detail=FORGED)
    text = render_summary(FACTS, [changed, unaffected])
    baseline = render_summary(
        FACTS, [changed, unaffected.model_copy(update={"detail": "baseline"})]
    )
    assert text.count(VISIBLE) == 2
    assert text.replace(VISIBLE, "baseline") == baseline
    assert f"**Product scope:** `impact.none` — {VISIBLE}\n" in text
    assert unaffected.detail == FORGED
