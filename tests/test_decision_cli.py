"""`auto_bi decision validate` — CLI surface and the exit-code contract (§12.1).

Exit codes are a contract with CI, so they are pinned here rather than left to whatever the
implementation happens to return: a gate whose "block" is indistinguishable from a crash
cannot be wired into a pipeline. Per §12.1 this test exists before the CLI does.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from auto_bi.cli import main
from auto_bi.verification.model import EXIT_ERROR, Verdict, exit_code_for
from tests.test_decision_validate import write_contracts


def test_exit_code_contract_is_stable() -> None:
    assert exit_code_for(Verdict.PASS) == 0
    assert exit_code_for(Verdict.PASS_WITH_ADVISORY) == 0
    assert exit_code_for(Verdict.NEEDS_REVIEW) == 3
    assert exit_code_for(Verdict.BLOCK) == 4
    assert EXIT_ERROR == 2
    assert {verdict.value for verdict in Verdict} == {
        "pass",
        "pass_with_advisory",
        "needs_review",
        "block",
    }


def test_valid_set_exits_zero_and_reports_the_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["decision", "validate", str(write_contracts(tmp_path))]) == 0
    out = capsys.readouterr().out
    assert "pass" in out
    assert "decision://sales/weekly_margin@1" in out


def test_unresolvable_ref_exits_block(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def repoint(data: dict[str, Any]) -> None:
        data["scope"]["metrics"] = ["metric://sales/net_margin_pct@1"]

    assert main(["decision", "validate", str(write_contracts(tmp_path, product=repoint))]) == 4
    out = capsys.readouterr().out
    assert "metric_ref.unknown" in out
    assert "block" in out


def test_lifecycle_question_exits_needs_review(tmp_path: Path) -> None:
    def relabel(data: dict[str, Any]) -> None:
        data["metadata"]["status"] = "draft"

    assert main(["decision", "validate", str(write_contracts(tmp_path, metric=relabel))]) == 3


def test_advisory_still_exits_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def relabel(data: dict[str, Any]) -> None:
        data["metadata"]["status"] = "deprecated"

    assert main(["decision", "validate", str(write_contracts(tmp_path, metric=relabel))]) == 0
    assert "metric.deprecated" in capsys.readouterr().out


def test_json_output_is_the_only_thing_on_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["decision", "validate", str(write_contracts(tmp_path)), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["kind"] == "DecisionValidationReport"
    assert payload["verdict"] == "pass"
    assert payload["products"] == ["decision://sales/weekly_margin@1"]
    assert payload["issues"] == []


def test_json_output_carries_stable_issue_codes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def repoint(data: dict[str, Any]) -> None:
        data["scope"]["metrics"] = ["metric://sales/net_margin_pct@1"]

    main(["decision", "validate", str(write_contracts(tmp_path, product=repoint)), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert [issue["code"] for issue in payload["issues"]] == ["metric_ref.unknown"]
    assert payload["issues"][0]["severity"] == "block"


def test_missing_path_exits_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["decision", "validate", str(tmp_path / "nope")]) == EXIT_ERROR
    assert "contract.unreadable" in capsys.readouterr().out


def test_directory_without_products_exits_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A run that validated nothing must not look like a pass."""
    metric = yaml.safe_load(
        (Path(__file__).parent / "fixtures" / "decision" / "gross_margin_pct.yaml").read_text(
            encoding="utf-8"
        )
    )
    (tmp_path / "gross_margin_pct.yaml").write_text(
        yaml.safe_dump(metric, allow_unicode=True), encoding="utf-8"
    )
    assert main(["decision", "validate", str(tmp_path)]) == EXIT_ERROR
    assert "no decision product" in capsys.readouterr().out.lower()


def test_decision_command_requires_a_subcommand() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["decision"])
    assert exc.value.code == 2
