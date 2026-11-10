"""DecisionProductSpec — the external contract over dashboards (DECISIONOPS_ARCHITECTURE 7.2).

The decision product carries the lifecycle: owner, risk tier, verification policy, delivery.
`DashboardSpec` stays a presentation/query IR (invariant 1) and is only referenced by a
pinned ref, so the v1 compiler contract is untouched by anything in this module.
"""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from auto_bi.decision.spec import (
    ActionsMode,
    Cadence,
    DecisionProductSpec,
    RiskTier,
)
from auto_bi.ir.spec import DashboardSpec

REPO = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "decision" / "weekly_margin.yaml"
ARCHITECTURE = REPO / "docs" / "DECISIONOPS_ARCHITECTURE.md"


def payload(**overrides: Any) -> dict[str, Any]:
    data = deepcopy(yaml.safe_load(FIXTURE.read_text(encoding="utf-8")))
    data.update(overrides)
    return data


def test_fixture_product_loads() -> None:
    spec = DecisionProductSpec.load(FIXTURE)
    assert spec.metadata.id == "decision://sales/weekly_margin"
    assert spec.metadata.risk_tier is RiskTier.MEDIUM
    assert spec.decision.cadence is Cadence.WEEKLY
    assert spec.scope.metrics == ["metric://sales/gross_margin_pct@3"]
    assert spec.delivery.dashboards[0].ref == "dashboard://weekly_margin@7"
    assert spec.actions.mode is ActionsMode.DISABLED


def test_architecture_yaml_sketch_still_validates() -> None:
    """Docs-as-code: the v2alpha1 sketch in the architecture must parse as the real model."""
    text = ARCHITECTURE.read_text(encoding="utf-8")
    section = text.split("### 7.2 DecisionProductSpec", 1)[1]
    block = re.search(r"```yaml\n(.*?)```", section, re.S)
    assert block, "DecisionProductSpec sketch missing from DECISIONOPS_ARCHITECTURE 7.2"
    assert DecisionProductSpec.model_validate(yaml.safe_load(block.group(1))) == (
        DecisionProductSpec.load(FIXTURE)
    )


def test_self_ref_pins_the_domain_version() -> None:
    assert DecisionProductSpec.load(FIXTURE).self_ref() == "decision://sales/weekly_margin@1"


def test_round_trip_preserves_the_digest() -> None:
    spec = DecisionProductSpec.load(FIXTURE)
    again = DecisionProductSpec.model_validate(spec.model_dump(mode="json"))
    assert again == spec
    assert again.digest() == spec.digest()


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DecisionProductSpec.model_validate(payload(schedule={"cron": "0 9 * * 1"}))


def test_scope_metrics_must_be_present_and_version_pinned() -> None:
    with pytest.raises(ValidationError, match="at least 1"):
        DecisionProductSpec.model_validate(payload(scope={**payload()["scope"], "metrics": []}))
    with pytest.raises(ValidationError, match="version"):
        DecisionProductSpec.model_validate(
            payload(scope={**payload()["scope"], "metrics": ["metric://sales/gross_margin_pct"]})
        )


def test_delivery_dashboard_ref_must_be_version_pinned() -> None:
    with pytest.raises(ValidationError, match="version"):
        DecisionProductSpec.model_validate(
            payload(delivery={"dashboards": [{"ref": "dashboard://weekly_margin"}]})
        )


def test_actions_are_disabled_in_alpha() -> None:
    """Actions are an explicit non-goal until the release gate is proven (ADR 0003 9)."""
    with pytest.raises(ValidationError, match="not implemented"):
        DecisionProductSpec.model_validate(payload(actions={"mode": "proposal_only"}))


@pytest.mark.parametrize("bad", ["0.000001e0", "1e-6", "-0.0", "-0", "", "abc", "0,1", "+1"])
def test_cross_surface_tolerance_must_be_a_plain_decimal_string(bad: str) -> None:
    with pytest.raises(ValidationError, match="decimal"):
        DecisionProductSpec.model_validate(
            payload(verification={**payload()["verification"], "cross_surface_tolerance": bad})
        )


def test_cross_surface_tolerance_rejects_a_binary_float() -> None:
    with pytest.raises(ValidationError, match="decimal"):
        DecisionProductSpec.model_validate(
            payload(verification={**payload()["verification"], "cross_surface_tolerance": 0.000001})
        )


def test_question_must_carry_content() -> None:
    with pytest.raises(ValidationError, match="at least 1 character"):
        DecisionProductSpec.model_validate(
            payload(decision={**payload()["decision"], "question": "   "})
        )


def test_time_window_must_be_an_iso8601_duration() -> None:
    with pytest.raises(ValidationError, match="duration"):
        DecisionProductSpec.model_validate(
            payload(scope={**payload()["scope"], "time_window": "13 weeks"})
        )


def test_brief_format_is_a_closed_set() -> None:
    with pytest.raises(ValidationError, match="brief_formats"):
        DecisionProductSpec.model_validate(
            payload(delivery={**payload()["delivery"], "brief_formats": ["pptx"]})
        )


def test_dashboard_spec_stays_free_of_lifecycle_fields() -> None:
    """Invariant 1: lifecycle lives in the decision product, never in the presentation IR."""
    lifecycle = {"owner", "risk_tier", "approvals", "evidence", "lifecycle", "verification"}
    assert lifecycle.isdisjoint(DashboardSpec.model_fields)
