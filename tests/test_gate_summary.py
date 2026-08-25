"""The derived summary report inside a bundle (ARCHITECTURE §9.2, bootstrap task 8).

`reports/summary.md` is the one artifact in the layout written for a person rather than for
a machine — the owner of a governed product opening the evidence for a change they did not
make. Being derived is the whole contract: it restates findings the checks already carry,
in the order an owner needs them, and never introduces one of its own.

Two boundaries are pinned here. The report lists every check that was graded *into* the
bundle, and it does not grade `evidence.bundle` — the re-verification of the artifact this
report lives inside is decided after the bundle is sealed, and a report claiming that
verdict would be claiming something nobody had checked yet. And it stays byte-identical for
identical inputs, because it travels inside a content-addressed bundle (§9.1).
"""

from __future__ import annotations

from pathlib import Path

from auto_bi.evidence.manifest import read_bundle
from auto_bi.evidence.model import ArtifactKind
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.verification.release_gate import (
    CheckCategory,
    CheckResult,
    CheckStatus,
    GateReport,
)
from auto_bi.verification.summary import SummaryFacts, render_summary
from tests.test_decision_verify import PRODUCT_ID, Case, _formula_change

SUMMARY_PATH = "reports/summary.md"

FACTS = SummaryFacts(
    product_ref="decision://sales/weekly_margin@1",
    run_id="run-0123456789abcdef",
    base_digest="sha256:" + "a" * 64,
    candidate_digest="sha256:" + "b" * 64,
    created_at="2026-08-25T09:00:00Z",
    verifier_version="1",
)


def one_check(**overrides: object) -> CheckResult:
    data: dict[str, object] = {
        "check_id": "contracts.validate",
        "check_version": "1",
        "category": CheckCategory.CONTRACT,
        "status": CheckStatus.PASS,
        "summary_code": "contracts.clean",
        "detail": "1 metric contract(s) resolved, 0 issue(s)",
    }
    data.update(overrides)
    return CheckResult.model_validate(data)


def run_with_summary(case: Case) -> tuple[GateReport, str]:
    report = case.run()
    store = FilesystemArtifactStore(case.root / "evidence")
    return report, store.get(report.run_id, SUMMARY_PATH).decode("utf-8")


def test_the_bundle_carries_a_derived_summary(tmp_path: Path) -> None:
    report, text = run_with_summary(Case(tmp_path).with_candidate())
    bundle = read_bundle(FilesystemArtifactStore(tmp_path / "evidence"), report.run_id)

    artifact = next(item for item in bundle.artifacts if item.path == SUMMARY_PATH)
    assert artifact.artifact_id == "report-summary-md"
    assert artifact.kind is ArtifactKind.REPORT
    assert artifact.media_type == "text/markdown"
    assert text.startswith("# ")


def test_the_summary_names_the_verdict_the_product_and_both_snapshots(tmp_path: Path) -> None:
    report, text = run_with_summary(Case(tmp_path).with_candidate())

    assert f"{PRODUCT_ID}@1" in text
    assert report.verdict.value in text
    assert report.base_digest in text
    assert report.candidate_digest in text
    assert report.created_at in text


def test_a_blocking_run_names_what_stops_it_and_whom_it_reaches(tmp_path: Path) -> None:
    """The owner's question is not "did it block" but "what of mine moved" (§8.3)."""
    report, text = run_with_summary(Case(tmp_path).with_candidate(contract=_formula_change()))

    assert report.verdict.value == "block"
    assert "metric://sales/gross_margin_pct" in text
    assert "dashboard://weekly_margin@7" in text
    assert "commercial-analytics" in text
    for check in report.blocking():
        assert check.check_id in text


def test_the_summary_says_what_the_run_did_not_do(tmp_path: Path) -> None:
    """A clean report that hides its own holes is worse than no report (§8.2)."""
    _, text = run_with_summary(Case(tmp_path).with_candidate())

    assert "sql.compile" in text
    assert "execution.checks" in text
    assert "check.not_implemented" in text


def test_the_summary_lists_every_check_that_went_into_the_bundle(tmp_path: Path) -> None:
    report, text = run_with_summary(Case(tmp_path).with_candidate())

    for check in report.checks:
        if check.check_id == "evidence.bundle":
            continue
        assert check.check_id in text, f"summary omits {check.check_id}"
        assert check.summary_code in text


def test_the_summary_does_not_grade_the_bundle_it_lives_in(tmp_path: Path) -> None:
    report, text = run_with_summary(Case(tmp_path).with_candidate())

    assert any(check.check_id == "evidence.bundle" for check in report.checks)
    assert "evidence.bundle" not in text


def test_a_clean_run_does_not_invent_a_blocking_section(tmp_path: Path) -> None:
    report, text = run_with_summary(
        Case(tmp_path).with_asset().with_results([["Москва", "0.412"], ["Казань", "0.377"]])
    )

    assert report.blocking() == []
    assert "## What stops the release" not in text
    assert "## What was not done" in text  # the two unimplemented steps are always there


def test_the_summary_is_byte_identical_for_identical_input(tmp_path: Path) -> None:
    _, first = run_with_summary(Case(tmp_path / "a").with_candidate())
    _, second = run_with_summary(Case(tmp_path / "b").with_candidate())

    assert first == second


def test_a_run_with_no_holes_still_carries_the_section(tmp_path: Path) -> None:
    """Completeness must be stated, not inferred from a heading that is simply absent."""
    text = render_summary(FACTS, [one_check()])

    assert "## What was not done" in text
    assert "every step of §8.2 produced evidence" in text
    assert "## What stops the release" not in text


def test_a_finding_is_data_not_markup() -> None:
    """A pipe inside a detail must not become a table column."""
    text = render_summary(FACTS, [one_check(detail="rows a | b differ")])

    assert "| rows a \\| b differ |" in text


def test_a_step_that_did_not_run_is_not_also_listed_as_a_human_decision() -> None:
    """One finding, one section: repeated under two headings it reads as two problems."""
    skipped = one_check(
        check_id="semantic.diff",
        status=CheckStatus.REVIEW,
        summary_code="check.not_run",
        detail="a metric contract did not load",
    )
    text = render_summary(FACTS, [skipped])

    assert "## What needs a human" not in text
    assert text.count("semantic.diff") == 2  # the undone section and the table


def test_a_stopped_sequence_reports_the_checks_that_did_not_run(tmp_path: Path) -> None:
    """A contract that did not parse leaves four checks ungraded; the report says which."""
    case = Case(tmp_path).with_candidate()
    (case.candidate / "metrics" / "gross_margin_pct.yaml").write_text(
        "kind: MetricContract\nnot: a contract\n", encoding="utf-8"
    )
    report, text = run_with_summary(case)

    not_run = [check for check in report.checks if check.summary_code == "check.not_run"]
    assert len(not_run) == 4
    assert all(check.status is CheckStatus.REVIEW for check in not_run)
    for check in not_run:
        assert check.check_id in text
