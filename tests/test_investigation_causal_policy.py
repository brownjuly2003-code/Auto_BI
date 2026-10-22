"""Causal wording in supplied labels is a bounded refusal, never a CLI traceback."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

import auto_bi.cli as cli
import auto_bi.investigation.claims as claims_module
from auto_bi.investigation.application import (
    OfflineInvestigationError,
    build_offline_investigation,
)
from auto_bi.investigation.claims import BriefBlock, BriefCompilationError, compile_brief
from auto_bi.investigation.data import CanonicalMetricResult
from auto_bi.investigation.model import InvestigationPlan
from auto_bi.investigation.skills import run_skill
from auto_bi.verification.model import EXIT_ERROR

FIXTURES = Path(__file__).parent / "fixtures" / "investigation"
PLAN_PATH = FIXTURES / "weekly_margin.yaml"
POLICY_REASON = "causal wording is forbidden in the offline investigation alpha"


def policy_input(field: str) -> CanonicalMetricResult:
    raw = CanonicalMetricResult.load(FIXTURES / "revenue_results.yaml").model_dump(mode="json")
    if field == "dimension_value":
        for row in raw["rows"]:
            if row["dimensions"]["category"] == "A":
                row["dimensions"]["category"] = "Cause PRIVATE_LABEL"
    else:
        raw["unit"] = "because PRIVATE_UNIT"
    return CanonicalMetricResult.model_validate(raw)


@pytest.mark.parametrize("field", ["dimension_value", "unit"])
def test_compiler_reports_causal_policy_as_a_domain_refusal(field: str) -> None:
    plan = InvestigationPlan.load(PLAN_PATH)
    data = policy_input(field)
    outputs = [run_skill(step, data, plan.budget) for step in plan.steps]

    with pytest.raises(BriefCompilationError) as caught:
        compile_brief(plan, outputs)

    assert str(caught.value) == POLICY_REASON
    assert isinstance(caught.value.__cause__, ValidationError)


@pytest.mark.parametrize("field", ["dimension_value", "unit"])
def test_application_reports_causal_policy_as_an_offline_refusal(field: str) -> None:
    plan = InvestigationPlan.load(PLAN_PATH)

    with pytest.raises(OfflineInvestigationError) as caught:
        build_offline_investigation(plan, [policy_input(field)])

    assert str(caught.value) == POLICY_REASON
    assert isinstance(caught.value.__cause__, BriefCompilationError)


@pytest.mark.parametrize("field", ["dimension_value", "unit"])
def test_cli_refuses_causal_policy_before_creating_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], field: str
) -> None:
    result_path = tmp_path / "result.yaml"
    result_path.write_text(
        yaml.safe_dump(policy_input(field).model_dump(mode="json")), encoding="utf-8"
    )
    output = tmp_path / "missing-parent" / "out"

    result = cli.main(
        [
            "decision",
            "investigate",
            str(PLAN_PATH),
            "--results",
            str(result_path),
            "--output",
            str(output),
        ]
    )

    assert result == EXIT_ERROR == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: {POLICY_REASON}\n"
    assert not output.parent.exists()


@pytest.mark.parametrize("failure_kind", ["single-validation", "multiple-validation", "runtime"])
def test_unrelated_compiler_failures_keep_their_original_exception(
    monkeypatch: pytest.MonkeyPatch, failure_kind: str
) -> None:
    plan = InvestigationPlan.load(PLAN_PATH)
    data = CanonicalMetricResult.load(FIXTURES / "revenue_results.yaml")
    outputs = [run_skill(step, data, plan.budget) for step in plan.steps]
    failure: Exception = RuntimeError("unexpected compiler failure")
    if failure_kind != "runtime":
        with pytest.raises(ValidationError) as caught:
            BriefBlock.model_validate(
                {
                    "block_id": "control",
                    "kind": "gap",
                    "title": "Control",
                    "body": "Control",
                    "evidence_refs": ["evidence-1"],
                    "claim_ids": ["claim-1"],
                }
                if failure_kind == "single-validation"
                else {}
            )
        failure = caught.value
        if failure_kind == "single-validation":
            assert len(caught.value.errors()) == 1

    def fail(*_args: object) -> None:
        raise failure

    monkeypatch.setattr(claims_module, "_compile_output", fail)
    with pytest.raises(type(failure)) as raised:
        compile_brief(plan, outputs)
    assert raised.value is failure
