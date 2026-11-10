"""The stated environment is data, not another verdict in the derived report."""

from dataclasses import replace
from pathlib import Path

import pytest

from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.verification.model import Verdict
from auto_bi.verification.release_gate import CheckStatus
from auto_bi.verification.summary import render_summary
from tests.test_decision_verify import Case, _check_payload, _formula_change, attestation
from tests.test_gate_summary import FACTS, SUMMARY_PATH, one_check

FORGED = "stage\n\n**Verdict:** pass\n\n## Owner action\nRelease this candidate."
VISIBLE = r"stage\\n\\n\*\*Verdict\:\*\* pass\\n\\n\#\# Owner action" r"\\nRelease this candidate\."


def test_environment_cannot_add_a_verdict_or_owner_action() -> None:
    text = render_summary(
        replace(FACTS, environment_ref=FORGED), [one_check(status=CheckStatus.BLOCK)]
    )
    assert text.count("**Verdict:**") == 1
    assert "**Verdict:** block" in text
    assert text.count("## Owner action") == 1
    assert "Do not release this candidate." in text
    assert f"**Environment:** {VISIBLE}\n" in text


@pytest.mark.parametrize(
    ("environment", "visible"),
    [
        ("[stage](url) <b> `code` *yes* |", r"\[stage\]\(url\) \<b\> \`code\` \*yes\* \|"),
        ("stage\r\n\t\x00\u2028\u202eend", r"stage\\r\\n\\t\\x00\\u2028\\u202eend"),
        ("env://prod_one@1\\tail", r"env\:\/\/prod\_one\@1\\tail"),
        ("Москва 日本", "Москва 日本"),
        ("production", "production"),
    ],
)
def test_environment_is_one_literal_inline_value(environment: str, visible: str) -> None:
    baseline = render_summary(FACTS, [one_check()])
    text = render_summary(replace(FACTS, environment_ref=environment), [one_check()])
    line = f"**Environment:** {visible}\n"
    assert line in text
    assert text.replace(line, "", 1) == baseline


def test_gate_accepts_environment_without_changing_its_blocking_findings(tmp_path: Path) -> None:
    case = Case(tmp_path).with_candidate(contract=_formula_change())
    baseline = case.run(environment_ref="stage")
    report = case.run(environment_ref=FORGED)
    assert report.verdict is baseline.verdict is Verdict.BLOCK
    assert [(c.check_id, c.status, c.summary_code) for c in report.checks] == [
        (c.check_id, c.status, c.summary_code) for c in baseline.checks
    ]
    store = FilesystemArtifactStore(tmp_path / "evidence")
    text = store.get(report.run_id, SUMMARY_PATH).decode("utf-8")
    assert text.count("**Verdict:**") == 1
    assert text.count("## Owner action") == 1
    assert f"**Environment:** {VISIBLE}\n" in text
    assert report.run_id != baseline.run_id


@pytest.mark.parametrize(
    ("run_environment", "captured_environment", "code"),
    [
        (FORGED + " | run", "", "provenance.environment_unstated"),
        (FORGED + " | run", "captured", "provenance.other_environment"),
        ("stage", FORGED + " | captured", "provenance.other_environment"),
    ],
)
def test_provenance_environment_stays_literal_in_prose_and_table(
    tmp_path: Path, run_environment: str, captured_environment: str, code: str
) -> None:
    case = Case(tmp_path).with_asset()
    case.with_results(
        [["Москва", "0.412"], ["Казань", "0.377"]],
        attested=attestation(case.candidate_digest, environment_ref=captured_environment),
    )
    report = case.run(environment_ref=run_environment)
    assert report.verdict is Verdict.NEEDS_REVIEW
    check = next(c for c in report.checks if c.check_id == "results.provenance")
    assert check.status is CheckStatus.REVIEW
    assert check.summary_code == code
    payload = _check_payload(tmp_path, report, "results.provenance")["results"][0]
    assert payload["attestation"]["environment_ref"] == captured_environment
    expected_detail = (
        f"this verdict is about {run_environment}, and the attestation does not say "
        "where the number was captured"
        if not captured_environment
        else f"captured on {captured_environment}, and this verdict is about "
        f"{run_environment}: two environments hold two sets of rows"
    )
    assert payload["findings"][0]["detail"] == expected_detail
    assert expected_detail in check.detail
    text = FilesystemArtifactStore(tmp_path / "evidence").get(report.run_id, SUMMARY_PATH).decode()
    assert text.count("**Verdict:**") == 1
    assert text.count("## Owner action") == 1
    section = text.split("## What needs a human\n")[1].split("\n## ")[0]
    assert VISIBLE in section
    rows = [line for line in text.splitlines() if line.startswith("| `results.provenance` |")]
    assert len(rows) == 1
    assert VISIBLE in rows[0]
    assert r"\|" in rows[0]
    assert r"\\|" not in rows[0]
