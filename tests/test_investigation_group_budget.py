"""Group budgets count distinct categories across both periods before publication."""

import json
from pathlib import Path

import pytest

from auto_bi.investigation.data import CanonicalMetricResult
from auto_bi.investigation.model import InvestigationPlan
from auto_bi.investigation.skills import (
    ConcentrationResult,
    ContributionResult,
    GapCode,
    InvestigationGap,
    run_skill,
)
from tests.test_investigation_cli import ARTIFACT_NAMES, PLAN_FIXTURE, RESULT_FIXTURE, invoke


@pytest.mark.parametrize("skill", ["contribution_decomposition", "concentration_shift"])
@pytest.mark.parametrize(
    ("current", "group_count"),
    [("AB", 2), ("BC", 3), ("CD", 4)],
    ids=["identical", "overlapping", "disjoint"],
)
@pytest.mark.parametrize("below_limit", [False, True], ids=["exact-budget", "over-budget"])
def test_group_union_controls_skill_result_and_publication(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    skill: str,
    current: str,
    group_count: int,
    below_limit: bool,
) -> None:
    limit = group_count - int(below_limit)
    raw_plan = InvestigationPlan.load(PLAN_FIXTURE).model_dump(mode="json")
    raw_plan["steps"] = [step for step in raw_plan["steps"] if step["skill"] == skill]
    raw_plan["budget"]["max_groups_per_step"] = limit
    plan = InvestigationPlan.model_validate(raw_plan)
    raw_data = CanonicalMetricResult.load(RESULT_FIXTURE).model_dump(mode="json")
    raw_data["rows"] = [
        {"period": period, "dimensions": {"category": category}, "value": "10"}
        for period, categories in [("comparison", "AB"), ("current", current)]
        for category in categories
    ]
    data = CanonicalMetricResult.model_validate(raw_data)
    result = run_skill(plan.steps[0], data, plan.budget)

    if below_limit:
        assert isinstance(result, InvestigationGap)
        assert result.code is GapCode.BUDGET_GROUPS_EXCEEDED
        assert result.detail == f"result has {group_count} groups; budget allows {limit}"
    else:
        expected_type = (
            ContributionResult if skill == "contribution_decomposition" else ConcentrationResult
        )
        assert isinstance(result, expected_type)

    plan_path = tmp_path / "plan.yaml"
    result_path = tmp_path / "result.yaml"
    plan_path.write_text(plan.to_yaml(), encoding="utf-8", newline="\n")
    result_path.write_text(data.to_yaml(), encoding="utf-8", newline="\n")
    output = tmp_path / "new-parent" / "output"

    code = invoke(output, plan_path, result_path)
    captured = capsys.readouterr()

    if below_limit:
        assert code == 2
        assert captured.out == ""
        assert "budget.groups_exceeded" in captured.err
        assert f"result has {group_count} groups; budget allows {limit}" in captured.err
        assert not output.parent.exists()
    else:
        assert code == 0
        assert captured.err == ""
        assert {path.name for path in output.iterdir()} == ARTIFACT_NAMES
        saved = json.loads((output / "results.json").read_text(encoding="utf-8"))
        assert saved["plan_digest"] == plan.digest()
        assert saved["outputs"] == [result.model_dump(mode="json")]
