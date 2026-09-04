"""Synthetic truth sets for deterministic investigation skills."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from auto_bi.investigation.data import CanonicalMetricResult
from auto_bi.investigation.model import InvestigationPlan, InvestigationSkill
from auto_bi.investigation.skills import (
    ConcentrationResult,
    ContributionResult,
    DataHealthResult,
    FreshnessStatus,
    GapCode,
    InvestigationGap,
    PeriodVarianceResult,
    concentration_shift,
    contribution_decomposition,
    data_health,
    period_variance,
)

FIXTURES = Path(__file__).parent / "fixtures" / "investigation"
PLAN_FIXTURE = FIXTURES / "weekly_margin.yaml"
RESULT_FIXTURE = FIXTURES / "revenue_results.yaml"


def raw_result() -> dict[str, Any]:
    return deepcopy(yaml.safe_load(RESULT_FIXTURE.read_text(encoding="utf-8")))


def plan() -> InvestigationPlan:
    return InvestigationPlan.load(PLAN_FIXTURE)


def step(skill: InvestigationSkill):
    return next(item for item in plan().steps if item.skill is skill)


def test_canonical_metric_result_is_strict_and_content_addressed() -> None:
    result = CanonicalMetricResult.load(RESULT_FIXTURE)
    again = CanonicalMetricResult.from_yaml(result.to_yaml())

    assert again == result
    assert again.digest() == result.digest()
    assert [row.value for row in result.rows[:2]] == ["100", "50"]

    unknown = raw_result()
    unknown["query"] = "SELECT *"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CanonicalMetricResult.model_validate(unknown)

    binary_float = raw_result()
    binary_float["rows"][0]["value"] = 100.0
    with pytest.raises(ValidationError, match="decimal string"):
        CanonicalMetricResult.model_validate(binary_float)


def test_data_health_reports_counts_grain_and_freshness() -> None:
    result = data_health(
        step(InvestigationSkill.DATA_HEALTH),
        CanonicalMetricResult.load(RESULT_FIXTURE),
        plan().budget,
    )

    assert isinstance(result, DataHealthResult)
    assert result.current_rows == 3
    assert result.comparison_rows == 3
    assert result.null_values == 0
    assert result.duplicate_grains == 0
    assert result.freshness is FreshnessStatus.PASS
    assert result.freshness_age_seconds == "7200"


def test_data_health_surfaces_bad_input_without_guessing() -> None:
    data = raw_result()
    data["complete"] = False
    data["source_max_event_time"] = "2026-09-03T00:00:00Z"
    data["rows"][4]["value"] = None
    data["rows"].append(deepcopy(data["rows"][3]))

    result = data_health(
        step(InvestigationSkill.DATA_HEALTH),
        CanonicalMetricResult.model_validate(data),
        plan().budget,
    )

    assert isinstance(result, DataHealthResult)
    assert not result.complete
    assert result.null_values == 1
    assert result.duplicate_grains == 1
    assert result.freshness is FreshnessStatus.STALE


def test_period_variance_matches_the_truth_set_exactly() -> None:
    result = period_variance(
        step(InvestigationSkill.PERIOD_VARIANCE),
        CanonicalMetricResult.load(RESULT_FIXTURE),
        plan().budget,
    )

    assert isinstance(result, PeriodVarianceResult)
    assert result.comparison_value == "200"
    assert result.current_value == "180"
    assert result.absolute_delta == "-20"
    assert result.relative_delta == "-0.1"


def test_contribution_decomposition_reconciles_to_the_total_delta() -> None:
    result = contribution_decomposition(
        step(InvestigationSkill.CONTRIBUTION_DECOMPOSITION),
        CanonicalMetricResult.load(RESULT_FIXTURE),
        plan().budget,
    )

    assert isinstance(result, ContributionResult)
    assert result.total_delta == "-20"
    assert [item.dimension_value for item in result.contributions] == ["A", "B", "C"]
    assert [item.delta for item in result.contributions] == ["-30", "10", "0"]
    assert [item.share_of_total_delta for item in result.contributions] == ["1.5", "-0.5", "0"]
    assert result.reconciled


def test_decomposition_is_independent_of_input_row_order() -> None:
    original = CanonicalMetricResult.load(RESULT_FIXTURE)
    shuffled = raw_result()
    shuffled["rows"] = list(reversed(shuffled["rows"]))

    first = contribution_decomposition(
        step(InvestigationSkill.CONTRIBUTION_DECOMPOSITION), original, plan().budget
    )
    second = contribution_decomposition(
        step(InvestigationSkill.CONTRIBUTION_DECOMPOSITION),
        CanonicalMetricResult.model_validate(shuffled),
        plan().budget,
    )

    assert isinstance(first, ContributionResult)
    assert isinstance(second, ContributionResult)
    assert first.canonical_bytes() == second.canonical_bytes()


def test_unsupported_dimension_returns_a_typed_gap() -> None:
    raw_step = step(InvestigationSkill.CONTRIBUTION_DECOMPOSITION).model_dump(mode="json")
    raw_step["dimensions"] = ["region"]
    outside = type(step(InvestigationSkill.CONTRIBUTION_DECOMPOSITION)).model_validate(raw_step)

    result = contribution_decomposition(
        outside,
        CanonicalMetricResult.load(RESULT_FIXTURE),
        plan().budget,
    )

    assert isinstance(result, InvestigationGap)
    assert result.code is GapCode.SCOPE_UNSUPPORTED_DIMENSION
    assert "region" in result.detail


def test_non_additive_metric_returns_a_typed_gap() -> None:
    data = raw_result()
    data["additivity"] = "non_additive"

    result = contribution_decomposition(
        step(InvestigationSkill.CONTRIBUTION_DECOMPOSITION),
        CanonicalMetricResult.model_validate(data),
        plan().budget,
    )

    assert isinstance(result, InvestigationGap)
    assert result.code is GapCode.METHOD_NON_ADDITIVE_METRIC


def test_group_budget_fails_closed() -> None:
    budget = plan().budget.model_dump(mode="json")
    budget["max_groups_per_step"] = 2

    result = contribution_decomposition(
        step(InvestigationSkill.CONTRIBUTION_DECOMPOSITION),
        CanonicalMetricResult.load(RESULT_FIXTURE),
        type(plan().budget).model_validate(budget),
    )

    assert isinstance(result, InvestigationGap)
    assert result.code is GapCode.BUDGET_GROUPS_EXCEEDED


def test_concentration_shift_matches_hhi_and_top_n_truth() -> None:
    result = concentration_shift(
        step(InvestigationSkill.CONCENTRATION_SHIFT),
        CanonicalMetricResult.load(RESULT_FIXTURE),
        plan().budget,
    )

    assert isinstance(result, ConcentrationResult)
    assert result.comparison.hhi == "0.375"
    assert result.current.hhi == "0.339506"
    assert result.hhi_delta == "-0.035494"
    assert result.comparison.top_n_share == "0.75"
    assert result.current.top_n_share == "0.722222"
    assert result.top_n_share_delta == "-0.027778"
    assert [item.dimension_value for item in result.current.leaders] == ["A", "B"]


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("incomplete", GapCode.INPUT_INCOMPLETE),
        ("null", GapCode.INPUT_NULL_VALUE),
        ("duplicate", GapCode.INPUT_DUPLICATE_GRAIN),
    ],
)
def test_analytic_skills_refuse_unreliable_inputs(mutation: str, expected: GapCode) -> None:
    data = raw_result()
    if mutation == "incomplete":
        data["complete"] = False
    elif mutation == "null":
        data["rows"][0]["value"] = None
    else:
        data["rows"].append(deepcopy(data["rows"][0]))

    result = period_variance(
        step(InvestigationSkill.PERIOD_VARIANCE),
        CanonicalMetricResult.model_validate(data),
        plan().budget,
    )

    assert isinstance(result, InvestigationGap)
    assert result.code is expected
