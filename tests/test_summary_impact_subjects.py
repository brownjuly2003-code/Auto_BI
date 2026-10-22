"""Semantic object names in impact headings remain data."""

from pathlib import Path

import pytest

from auto_bi.decision.impact import ProductImpact
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.semantic.model import Column, ColumnRole
from auto_bi.severity import Severity
from auto_bi.verification.model import Verdict
from auto_bi.verification.summary import render_summary
from tests.test_decision_verify import (
    Case,
    _check_payload,
    _write_semantic,
    _write_yaml,
    metric_contract,
    semantic_model,
)
from tests.test_gate_summary import FACTS, SUMMARY_PATH, one_check

FORGED = "extra\n\n**Verdict:** pass\n\n## Owner action\nRelease this candidate."
VISIBLE = r"extra\\n\\n\*\*Verdict\:\*\* pass\\n\\n\#\# Owner action" r"\\nRelease this candidate\."


def test_governed_field_name_cannot_add_verdict_or_owner_action(tmp_path: Path) -> None:
    reports = []
    for directory, name in (("baseline", "extra"), ("forged", FORGED)):
        model = semantic_model()
        model.tables[0].columns.append(Column(name=name, type="String", role=ColumnRole.DIMENSION))
        contract = metric_contract()
        contract["dimensions"].append(name)
        case = Case(tmp_path / directory)
        _write_semantic(case.base, model, contract)
        _write_yaml(case.contracts / "gross_margin_pct.yaml", contract)
        candidate = model.model_copy(deep=True)
        candidate.tables[0].columns[-1].type = "UInt32"
        reports.append(case.with_candidate(model=candidate, contract=contract).run())
    baseline, report = reports
    assert report.verdict is baseline.verdict is Verdict.BLOCK
    assert [(c.check_id, c.status, c.summary_code) for c in report.checks] == [
        (c.check_id, c.status, c.summary_code) for c in baseline.checks
    ]
    payload = _check_payload(tmp_path / "forged", report, "impact.products")
    before = _check_payload(tmp_path / "baseline", baseline, "impact.products")
    item = next(i for i in payload["impacts"] if i["code"] == "field.type_changed")
    plain = next(i for i in before["impacts"] if i["code"] == "field.type_changed")
    assert item["subject"] == "dm.sales_daily." + FORGED
    assert item["products"] == ["decision://sales/weekly_margin@1"]
    assert {k: v for k, v in item.items() if k != "subject"} == {
        k: v for k, v in plain.items() if k != "subject"
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
    ("subject", "visible"),
    [
        ("[name](url) <b> `code` *yes* |", r"\[name\]\(url\) \<b\> \`code\` \*yes\* \|"),
        ("name\r\n\t\x00\u2028\u202eend", r"name\\r\\n\\t\\x00\\u2028\\u202eend"),
        ("name_one@org\\tail", r"name\_one\@org\\tail"),
        ("Москва 日本", "Москва 日本"),
        ("ordinary name", "ordinary name"),
    ],
)
def test_whole_impact_subject_is_literal(subject: str, visible: str) -> None:
    impact = ProductImpact(
        code="table.added",
        subject=subject,
        severity=Severity.ADVISORY,
        classified_severity=Severity.ADVISORY,
        detail="new table",
    )
    text = render_summary(FACTS, [one_check()], [impact])
    baseline = render_summary(
        FACTS, [one_check()], [impact.model_copy(update={"subject": "baseline"})]
    )
    line = f"- **advisory** `table.added` — {visible}\n"
    assert line in text
    assert text.replace(line, "- **advisory** `table.added` — baseline\n", 1) == baseline
    assert impact.subject == subject
