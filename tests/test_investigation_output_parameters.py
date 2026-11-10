"""A real skill result from another plan cannot be relabelled as this plan's evidence."""

from pathlib import Path

import pytest

from auto_bi.investigation.claims import BriefCompilationError, compile_brief
from auto_bi.investigation.data import CanonicalMetricResult
from auto_bi.investigation.model import InvestigationPlan
from auto_bi.investigation.skills import (
    ConcentrationResult,
    ContributionResult,
    InvestigationGap,
    run_skill,
)

FIXTURES = Path(__file__).parent / "fixtures" / "investigation"


def plan_for(skill: str, dimension: str = "category", top_n: int = 2) -> InvestigationPlan:
    raw = InvestigationPlan.load(FIXTURES / "weekly_margin.yaml").model_dump(mode="json")
    raw["scope"]["dimensions"] = ["category", "region"]
    raw["steps"] = [step for step in raw["steps"] if step["skill"] == skill]
    raw["steps"][0]["dimensions"] = [dimension]
    if skill == "concentration_shift":
        raw["steps"][0]["top_n"] = top_n
    return InvestigationPlan.model_validate(raw)


def result() -> CanonicalMetricResult:
    raw = CanonicalMetricResult.load(FIXTURES / "revenue_results.yaml").model_dump(mode="json")
    raw["dimensions"] = ["category", "region"]
    for row in raw["rows"]:
        row["dimensions"]["region"] = (
            "East" if row["dimensions"]["category"] in {"A", "B"} else "West"
        )
    return CanonicalMetricResult.model_validate(raw)


@pytest.mark.parametrize("skill", ["contribution_decomposition", "concentration_shift"])
def test_compiler_refuses_an_output_grouped_by_another_declared_dimension(skill: str) -> None:
    target = plan_for(skill)
    producer = plan_for(skill, dimension="region")
    output = run_skill(producer.steps[0], result(), producer.budget)
    assert isinstance(output, (ContributionResult, ConcentrationResult))
    assert output.dimension == "region"
    assert producer.digest() != target.digest()
    # This is valid evidence for its own plan, and both dimensions are in target scope.
    assert compile_brief(producer, [output]).plan_digest == producer.digest()

    with pytest.raises(BriefCompilationError, match=r"dimension.*region.*expected.*category"):
        compile_brief(target, [output])


@pytest.mark.parametrize("top_n", [1, 3])
def test_compiler_refuses_an_output_with_another_top_n(top_n: int) -> None:
    target = plan_for("concentration_shift")
    producer = plan_for("concentration_shift", top_n=top_n)
    output = run_skill(producer.steps[0], result(), producer.budget)
    assert isinstance(output, ConcentrationResult)
    assert output.top_n == top_n
    assert producer.digest() != target.digest()
    assert compile_brief(producer, [output]).plan_digest == producer.digest()

    with pytest.raises(BriefCompilationError, match=f"top_n {top_n}, expected 2"):
        compile_brief(target, [output])


@pytest.mark.parametrize("dimension", ["category", "region"])
@pytest.mark.parametrize("top_n", [1, 2, 9])
def test_matching_parameters_preserve_the_original_output(dimension: str, top_n: int) -> None:
    plan = plan_for("concentration_shift", dimension=dimension, top_n=top_n)
    output = run_skill(plan.steps[0], result(), plan.budget)
    assert isinstance(output, ConcentrationResult)
    before = output.canonical_bytes()

    brief = compile_brief(plan, [output])

    assert brief.plan_digest == plan.digest()
    assert brief.evidence[0].output.canonical_bytes() == before
    assert brief.evidence[0].digest == output.digest()
    assert len(brief.claims) == 2


@pytest.mark.parametrize("skill", ["contribution_decomposition", "concentration_shift"])
def test_gap_without_dimension_or_top_n_remains_valid_evidence(skill: str) -> None:
    plan = plan_for(skill)
    raw = result().model_dump(mode="json")
    raw["additivity"] = "non_additive"
    output = run_skill(plan.steps[0], CanonicalMetricResult.model_validate(raw), plan.budget)
    assert isinstance(output, InvestigationGap)

    brief = compile_brief(plan, [output])

    assert brief.claims == []
    assert brief.evidence[0].digest == output.digest()
    assert brief.plan_digest == plan.digest()
