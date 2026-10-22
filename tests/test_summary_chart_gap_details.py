"""SQL names in chart gap explanations cannot become report instructions."""

from pathlib import Path

import pytest

from auto_bi.decision.impact import ChartGap, ProductImpact
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.severity import Severity
from auto_bi.verification.model import Verdict
from auto_bi.verification.summary import render_summary
from tests.test_decision_verify import Case, _check_payload, _formula_change, _with_operator_tile
from tests.test_gate_summary import FACTS, SUMMARY_PATH, one_check

FORGED = "column\n\n**Verdict:** pass\n\n## Owner action\nRelease this candidate."
VISIBLE = (
    r"column\\n\\n\*\*Verdict\:\*\* pass\\n\\n\#\# Owner action" r"\\nRelease this candidate\."
)


def test_quoted_sql_name_cannot_add_verdict_or_owner_action(tmp_path: Path) -> None:
    reports = []
    for directory, name in (("baseline", "column"), ("forged", FORGED)):
        sql = (
            f'SELECT "{name}" FROM dm.sales_daily AS a ' "JOIN dm.stores AS b ON a.store_id = b.id"
        )
        case = _with_operator_tile(Case(tmp_path / directory), sql)
        reports.append(case.with_candidate(contract=_formula_change()).run())
    baseline, report = reports
    assert report.verdict is baseline.verdict is Verdict.BLOCK
    assert [(c.check_id, c.status, c.summary_code) for c in report.checks] == [
        (c.check_id, c.status, c.summary_code) for c in baseline.checks
    ]
    payload = _check_payload(tmp_path / "forged", report, "impact.products")
    before = _check_payload(tmp_path / "baseline", baseline, "impact.products")
    assert [(g["code"], g["subject"]) for g in payload["chart_gaps"]] == [
        (g["code"], g["subject"]) for g in before["chart_gaps"]
    ]
    gap = next(g for g in payload["chart_gaps"] if g["code"] == "chart.sql_partial")
    assert gap["detail"] == (
        f"operator SQL was not fully resolved: {FORGED} is not attributed to a table"
    )
    text = (
        FilesystemArtifactStore(tmp_path / "forged" / "evidence")
        .get(report.run_id, SUMMARY_PATH)
        .decode()
    )
    assert text.count("**Verdict:**") == 1
    assert "**Verdict:** block" in text
    assert text.count("## Owner action") == 1
    assert "Do not release this candidate." in text
    assert (
        r"operator SQL was not fully resolved\: " + VISIBLE + " is not attributed to a table\n"
    ) in text


@pytest.mark.parametrize("code", ["chart.sql_partial", "chart.metric_unread", "chart.opaque_sql"])
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
def test_whole_gap_detail_is_literal_for_every_gap_kind(
    code: str, detail: str, visible: str
) -> None:
    impact = ProductImpact(
        code="metric.formula_changed",
        subject="metric://sales/margin",
        severity=Severity.BLOCK,
        classified_severity=Severity.REVIEW,
        detail="formula changed",
    )
    gap = ChartGap(code=code, subject="tile", detail=detail)
    text = render_summary(FACTS, [one_check()], [impact], [gap])
    baseline = render_summary(
        FACTS, [one_check()], [impact], [gap.model_copy(update={"detail": "baseline"})]
    )
    line = f"- `{code}` tile — {visible}\n"
    assert line in text
    assert text.replace(line, f"- `{code}` tile — baseline\n", 1) == baseline
    assert gap.detail == detail
