"""Concentration cannot erase a violated input precondition by grouping rows."""

from pathlib import Path

import pytest

from auto_bi.cli import main
from auto_bi.investigation.application import InvestigationResults
from auto_bi.investigation.claims import BriefBlockKind, InvestigationBrief
from auto_bi.investigation.data import CanonicalMetricResult
from auto_bi.investigation.model import InvestigationPlan
from auto_bi.investigation.skills import (
    ConcentrationResult,
    ContributionResult,
    GapCode,
    InvestigationGap,
    PeriodVarianceResult,
    run_skill,
)

FIXTURES = Path(__file__).parent / "fixtures" / "investigation"


def _data(period: str, value: str) -> CanonicalMetricResult:
    raw = CanonicalMetricResult.load(FIXTURES / "revenue_results.yaml").model_dump(mode="json")
    raw["dimensions"] = ["category", "region"]
    raw["rows"] = [
        {
            "period": current_period,
            "dimensions": {"category": category, "region": region},
            "value": value if current_period == period and region == "west" else amount,
        }
        for current_period in ("comparison", "current")
        for category, region, amount in (
            ("A", "east", "100"),
            ("A", "west", "50"),
            ("B", "east", "50"),
        )
    ]
    return CanonicalMetricResult.model_validate(raw)


def _plan() -> InvestigationPlan:
    return InvestigationPlan.load(FIXTURES / "weekly_margin.yaml")


@pytest.mark.parametrize("period", ["comparison", "current"])
@pytest.mark.parametrize("value", ["-50", "-100", "-150", "-0.000000000000000000000001"])
@pytest.mark.parametrize("reverse", [False, True])
def test_concentration_rejects_negative_rows_even_when_group_total_is_nonnegative(
    period: str, value: str, reverse: bool
) -> None:
    data = _data(period, value)
    if reverse:
        data = data.model_copy(update={"rows": list(reversed(data.rows))})
    plan = _plan()
    result = run_skill(plan.steps[-1], data, plan.budget)

    assert isinstance(result, InvestigationGap)
    assert result.code is GapCode.METHOD_NEGATIVE_VALUE
    assert result.detail == "concentration requires non-negative additive values"
    assert result.step_id == "concentration"
    assert result.metric_ref == data.metric_ref
    assert result.skill_version == 2


@pytest.mark.parametrize("value", ["0", "50"])
def test_nonnegative_rows_still_match_preaggregated_concentration(value: str) -> None:
    data = _data("current", value)
    raw = data.model_dump(mode="json")
    raw["dimensions"] = ["category"]
    raw["rows"] = [
        {"period": period, "dimensions": {"category": category}, "value": amount}
        for period, total in (("comparison", "150"), ("current", str(100 + int(value))))
        for category, amount in (("A", total), ("B", "50"))
    ]
    plan = _plan()
    result = run_skill(plan.steps[-1], data, plan.budget)
    aggregated = run_skill(plan.steps[-1], CanonicalMetricResult.model_validate(raw), plan.budget)

    assert isinstance(result, ConcentrationResult)
    assert result.canonical_bytes() == aggregated.canonical_bytes()


def test_negative_rows_remain_valid_for_variance_and_contribution() -> None:
    plan = _plan()
    data = _data("current", "-50")
    variance = run_skill(plan.steps[1], data, plan.budget)
    contribution = run_skill(plan.steps[2], data, plan.budget)

    assert isinstance(variance, PeriodVarianceResult)
    assert variance.absolute_delta == "-100"
    assert isinstance(contribution, ContributionResult)
    assert contribution.total_delta == "-100"
    assert contribution.reconciled


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("incomplete", GapCode.INPUT_INCOMPLETE),
        ("null_value", GapCode.INPUT_NULL_VALUE),
        ("duplicate", GapCode.INPUT_DUPLICATE_GRAIN),
        ("non_additive", GapCode.METHOD_NON_ADDITIVE_METRIC),
        ("null_dimension", GapCode.INPUT_NULL_DIMENSION),
        ("group_budget", GapCode.BUDGET_GROUPS_EXCEEDED),
    ],
)
def test_existing_input_and_group_diagnostics_keep_precedence(
    mutation: str, expected: GapCode
) -> None:
    raw = _data("current", "-50").model_dump(mode="json")
    plan = _plan()
    budget = plan.budget
    if mutation == "incomplete":
        raw["complete"] = False
    elif mutation == "null_value":
        raw["rows"][0]["value"] = None
    elif mutation == "duplicate":
        raw["rows"].append(raw["rows"][0])
    elif mutation == "non_additive":
        raw["additivity"] = "non_additive"
    elif mutation == "null_dimension":
        raw["rows"][0]["dimensions"]["category"] = None
    else:
        budget = budget.model_copy(update={"max_groups_per_step": 1})

    result = run_skill(plan.steps[-1], CanonicalMetricResult.model_validate(raw), budget)
    assert isinstance(result, InvestigationGap)
    assert result.code is expected


@pytest.mark.parametrize("period", ["comparison", "current"])
def test_cli_publishes_evidence_backed_gap_without_concentration_claims(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], period: str
) -> None:
    raw_plan = _plan().model_dump(mode="json")
    raw_plan["steps"] = [raw_plan["steps"][-1]]
    plan = InvestigationPlan.model_validate(raw_plan)
    data = _data(period, "-50")
    plan_path, data_path, output = tmp_path / "plan.yaml", tmp_path / "data.yaml", tmp_path / "out"
    plan_path.write_text(plan.to_yaml(), encoding="utf-8")
    data_path.write_text(data.to_yaml(), encoding="utf-8")

    assert (
        main(
            [
                "decision",
                "investigate",
                str(plan_path),
                "--results",
                str(data_path),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert capsys.readouterr().err == ""
    results = InvestigationResults.model_validate_json((output / "results.json").read_bytes())
    brief = InvestigationBrief.model_validate_json((output / "brief.json").read_bytes())
    assert results.canonical_results == [data]
    gap = results.outputs[0]
    assert isinstance(gap, InvestigationGap)
    assert gap.code is GapCode.METHOD_NEGATIVE_VALUE
    assert not brief.claims
    assert len(brief.blocks) == len(brief.evidence) == 1
    assert brief.blocks[0].kind is BriefBlockKind.GAP
    assert brief.blocks[0].evidence_refs == [brief.evidence[0].artifact_id]
    assert brief.evidence[0].output == gap
    assert brief.evidence[0].digest == gap.digest()
    markdown = (output / "brief.md").read_text(encoding="utf-8")
    assert "method.negative_value" in markdown
    assert "HHI" not in markdown
