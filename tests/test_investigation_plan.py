"""Contract tests for the offline ``InvestigationPlan`` alpha."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from auto_bi.evidence.model import ClaimType
from auto_bi.investigation.model import InvestigationPlan, InvestigationSkill

FIXTURE = Path(__file__).parent / "fixtures" / "investigation" / "weekly_margin.yaml"


def payload() -> dict[str, Any]:
    return deepcopy(yaml.safe_load(FIXTURE.read_text(encoding="utf-8")))


def test_fixture_loads_as_an_ordered_offline_plan() -> None:
    plan = InvestigationPlan.load(FIXTURE)

    assert plan.product_ref == "decision://sales/weekly_margin@1"
    assert [step.skill for step in plan.steps] == [
        InvestigationSkill.DATA_HEALTH,
        InvestigationSkill.PERIOD_VARIANCE,
        InvestigationSkill.CONTRIBUTION_DECOMPOSITION,
        InvestigationSkill.CONCENTRATION_SHIFT,
    ]
    assert [step.step_id for step in plan.steps] == [
        "health",
        "variance",
        "contribution",
        "concentration",
    ]
    assert plan.budget.max_queries == 0


def test_yaml_round_trip_preserves_plan_and_digest() -> None:
    plan = InvestigationPlan.load(FIXTURE)
    rendered = plan.to_yaml()
    again = InvestigationPlan.from_yaml(rendered)

    assert again == plan
    assert again.digest() == plan.digest()
    assert rendered.endswith("\n")


def test_step_order_is_part_of_the_digest() -> None:
    original = InvestigationPlan.load(FIXTURE)
    data = payload()
    data["steps"] = list(reversed(data["steps"]))

    assert InvestigationPlan.model_validate(data).digest() != original.digest()


def test_unknown_fields_fail_closed() -> None:
    data = payload()
    data["schedule"] = {"cron": "0 9 * * 1"}

    with pytest.raises(ValidationError, match="extra_forbidden"):
        InvestigationPlan.model_validate(data)


@pytest.mark.parametrize(
    ("location", "value"),
    [
        (("scope", "metrics", 0), "metric://sales/gross_margin_pct"),
        (("steps", 0, "metrics", 0), "metric://sales/gross_margin_pct"),
    ],
)
def test_metric_refs_must_pin_versions(location: tuple[object, ...], value: str) -> None:
    data = payload()
    target: Any = data
    for key in location[:-1]:
        target = target[key]
    target[location[-1]] = value

    with pytest.raises(ValidationError, match="version"):
        InvestigationPlan.model_validate(data)


def test_only_the_four_declared_v1_skills_are_accepted() -> None:
    assert {skill.value for skill in InvestigationSkill} == {
        "data_health",
        "period_variance",
        "contribution_decomposition",
        "concentration_shift",
    }

    unknown = payload()
    unknown["steps"][0]["skill"] = "root_cause"
    with pytest.raises(ValidationError, match="skill"):
        InvestigationPlan.model_validate(unknown)

    unpinned = payload()
    unpinned["steps"][0]["skill_version"] = 2
    with pytest.raises(ValidationError, match="skill_version"):
        InvestigationPlan.model_validate(unpinned)


@pytest.mark.parametrize(
    ("field", "outside_value"),
    [
        ("metrics", "metric://sales/orders@2"),
        ("dimensions", "region"),
    ],
)
def test_every_step_is_confined_to_plan_scope(field: str, outside_value: str) -> None:
    data = payload()
    data["steps"][0][field].append(outside_value)

    with pytest.raises(ValidationError, match="outside plan scope"):
        InvestigationPlan.model_validate(data)


@pytest.mark.parametrize("duplicate", ["step_id", "scope_metric", "scope_dimension"])
def test_set_like_contract_fields_reject_duplicates(duplicate: str) -> None:
    data = payload()
    if duplicate == "step_id":
        data["steps"][1]["step_id"] = data["steps"][0]["step_id"]
    elif duplicate == "scope_metric":
        data["scope"]["metrics"].append(data["scope"]["metrics"][0])
    else:
        data["scope"]["dimensions"].append(data["scope"]["dimensions"][0])

    with pytest.raises(ValidationError, match="duplicate"):
        InvestigationPlan.model_validate(data)


def test_budget_bounds_the_declared_work_and_forbids_queries() -> None:
    too_many_steps = payload()
    too_many_steps["budget"]["max_steps"] = 3
    with pytest.raises(ValidationError, match="max_steps"):
        InvestigationPlan.model_validate(too_many_steps)

    remote_query = payload()
    remote_query["budget"]["max_queries"] = 1
    with pytest.raises(ValidationError, match="max_queries"):
        InvestigationPlan.model_validate(remote_query)

    no_rows = payload()
    no_rows["budget"]["max_input_rows"] = 0
    with pytest.raises(ValidationError, match="max_input_rows"):
        InvestigationPlan.model_validate(no_rows)


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("on_budget_exceeded", "continue"),
        ("on_scope_violation", "continue"),
        ("on_missing_evidence", "continue"),
        ("on_ambiguous_input", "guess"),
        ("on_unsupported_input", "skip"),
        ("causal_claims", "allowed"),
    ],
)
def test_stop_and_escalation_policy_cannot_be_weakened(field: str, unsafe_value: str) -> None:
    data = payload()
    data["policy"][field] = unsafe_value

    with pytest.raises(ValidationError, match=field):
        InvestigationPlan.model_validate(data)


def test_causal_claims_are_rejected_at_the_plan_boundary() -> None:
    data = payload()
    data["steps"][0]["allowed_claim_types"] = [ClaimType.CAUSAL.value]

    with pytest.raises(ValidationError, match="causal"):
        InvestigationPlan.model_validate(data)


def test_each_skill_has_a_fixed_claim_and_evidence_contract() -> None:
    wrong_claim = payload()
    wrong_claim["steps"][2]["allowed_claim_types"] = [ClaimType.OBSERVATION.value]
    with pytest.raises(ValidationError, match="allowed_claim_types"):
        InvestigationPlan.model_validate(wrong_claim)

    wrong_evidence = payload()
    wrong_evidence["steps"][3]["expected_evidence"] = "period_variance_result"
    with pytest.raises(ValidationError, match="expected_evidence"):
        InvestigationPlan.model_validate(wrong_evidence)


def test_skill_specific_shape_is_validated() -> None:
    missing_dimension = payload()
    missing_dimension["steps"][2]["dimensions"] = []
    with pytest.raises(ValidationError, match="one dimension"):
        InvestigationPlan.model_validate(missing_dimension)

    stray_top_n = payload()
    stray_top_n["steps"][1]["top_n"] = 3
    with pytest.raises(ValidationError, match="top_n"):
        InvestigationPlan.model_validate(stray_top_n)

    missing_top_n = payload()
    missing_top_n["steps"][3].pop("top_n")
    with pytest.raises(ValidationError, match="top_n"):
        InvestigationPlan.model_validate(missing_top_n)
