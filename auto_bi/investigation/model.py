"""Fail-closed contract for the offline typed-investigation alpha.

The plan is executable data, not a prompt. It pins every metric and skill version,
confines each ordered step to the declared scope, carries finite work budgets, and makes
the stop/escalation policy part of the content digest. The alpha accepts precomputed
canonical results only: ``max_queries`` is fixed at zero and causal claims are forbidden.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, model_validator

from auto_bi.evidence.canonical import sha256_digest
from auto_bi.evidence.model import ClaimType
from auto_bi.refs import DecisionRef, MetricRef
from auto_bi.scalars import MAX_SAFE_INT, Duration, NonBlankText, Slug
from auto_bi.strict import StrictModel

API_VERSION = "auto-bi.io/v2alpha1"


class InvestigationSkill(StrEnum):
    DATA_HEALTH = "data_health"
    PERIOD_VARIANCE = "period_variance"
    CONTRIBUTION_DECOMPOSITION = "contribution_decomposition"
    CONCENTRATION_SHIFT = "concentration_shift"


class InvestigationPrecondition(StrEnum):
    COMPLETE_PERIODS = "complete_periods"
    NON_NULL_VALUES = "non_null_values"
    UNIQUE_GRAIN = "unique_grain"
    ADDITIVE_METRIC = "additive_metric"
    NON_NEGATIVE_VALUES = "non_negative_values"


class InvestigationEvidence(StrEnum):
    DATA_HEALTH_RESULT = "data_health_result"
    PERIOD_VARIANCE_RESULT = "period_variance_result"
    CONTRIBUTION_RESULT = "contribution_result"
    CONCENTRATION_RESULT = "concentration_result"


_EXPECTED_EVIDENCE = {
    InvestigationSkill.DATA_HEALTH: InvestigationEvidence.DATA_HEALTH_RESULT,
    InvestigationSkill.PERIOD_VARIANCE: InvestigationEvidence.PERIOD_VARIANCE_RESULT,
    InvestigationSkill.CONTRIBUTION_DECOMPOSITION: InvestigationEvidence.CONTRIBUTION_RESULT,
    InvestigationSkill.CONCENTRATION_SHIFT: InvestigationEvidence.CONCENTRATION_RESULT,
}

_SKILL_VERSIONS = {
    InvestigationSkill.DATA_HEALTH: 1,
    InvestigationSkill.PERIOD_VARIANCE: 2,
    InvestigationSkill.CONTRIBUTION_DECOMPOSITION: 2,
    InvestigationSkill.CONCENTRATION_SHIFT: 2,
}

_ALLOWED_CLAIMS = {
    InvestigationSkill.DATA_HEALTH: (ClaimType.OBSERVATION,),
    InvestigationSkill.PERIOD_VARIANCE: (ClaimType.OBSERVATION,),
    InvestigationSkill.CONTRIBUTION_DECOMPOSITION: (ClaimType.CONTRIBUTION,),
    InvestigationSkill.CONCENTRATION_SHIFT: (ClaimType.OBSERVATION,),
}


def _reject_duplicates(name: str, values: Sequence[object]) -> None:
    duplicates = sorted({str(value) for value in values if values.count(value) > 1})
    if duplicates:
        raise ValueError(f"duplicate entries in {name}: {', '.join(duplicates)}")


class InvestigationScope(StrictModel):
    metrics: list[MetricRef] = Field(min_length=1)
    dimensions: list[NonBlankText] = Field(default_factory=list)
    time_window: Duration

    @model_validator(mode="after")
    def _check_unique(self) -> InvestigationScope:
        _reject_duplicates("scope.metrics", self.metrics)
        _reject_duplicates("scope.dimensions", self.dimensions)
        return self


class InvestigationStep(StrictModel):
    step_id: Slug
    skill: InvestigationSkill
    skill_version: Literal[1, 2]
    metrics: list[MetricRef] = Field(min_length=1)
    dimensions: list[NonBlankText] = Field(default_factory=list)
    preconditions: list[InvestigationPrecondition] = Field(default_factory=list)
    expected_evidence: InvestigationEvidence
    allowed_claim_types: list[ClaimType] = Field(min_length=1)
    top_n: int | None = Field(default=None, ge=1, le=MAX_SAFE_INT)

    @model_validator(mode="after")
    def _check_skill_contract(self) -> InvestigationStep:
        for name, entries in (
            ("step.metrics", self.metrics),
            ("step.dimensions", self.dimensions),
            ("step.preconditions", self.preconditions),
            ("step.allowed_claim_types", self.allowed_claim_types),
        ):
            _reject_duplicates(name, entries)

        expected_version = _SKILL_VERSIONS[self.skill]
        if self.skill_version != expected_version:
            raise ValueError(f"skill_version for {self.skill.value} must be {expected_version}")

        if ClaimType.CAUSAL in self.allowed_claim_types:
            raise ValueError("causal claims are forbidden in the offline investigation alpha")

        expected_claims = _ALLOWED_CLAIMS[self.skill]
        if tuple(self.allowed_claim_types) != expected_claims:
            expected_values = ", ".join(claim.value for claim in expected_claims)
            raise ValueError(
                f"allowed_claim_types for {self.skill.value} must be exactly: {expected_values}"
            )

        expected_evidence = _EXPECTED_EVIDENCE[self.skill]
        if self.expected_evidence is not expected_evidence:
            raise ValueError(
                f"expected_evidence for {self.skill.value} must be " f"{expected_evidence.value}"
            )

        if len(self.metrics) != 1 and self.skill is not InvestigationSkill.DATA_HEALTH:
            raise ValueError(f"{self.skill.value} requires exactly one metric")

        needs_dimension = self.skill in {
            InvestigationSkill.CONTRIBUTION_DECOMPOSITION,
            InvestigationSkill.CONCENTRATION_SHIFT,
        }
        if needs_dimension and len(self.dimensions) != 1:
            raise ValueError(f"{self.skill.value} requires exactly one dimension")
        if self.skill is InvestigationSkill.PERIOD_VARIANCE and self.dimensions:
            raise ValueError("period_variance does not accept dimensions")

        needs_top_n = self.skill is InvestigationSkill.CONCENTRATION_SHIFT
        if needs_top_n and self.top_n is None:
            raise ValueError("concentration_shift requires top_n")
        if not needs_top_n and self.top_n is not None:
            raise ValueError(
                f"top_n is only valid for {InvestigationSkill.CONCENTRATION_SHIFT.value}"
            )
        return self


class InvestigationBudget(StrictModel):
    max_steps: int = Field(ge=1, le=MAX_SAFE_INT)
    max_input_rows: int = Field(ge=1, le=MAX_SAFE_INT)
    max_output_claims: int = Field(ge=1, le=MAX_SAFE_INT)
    max_groups_per_step: int = Field(ge=1, le=MAX_SAFE_INT)
    max_runtime_ms: int = Field(ge=1, le=MAX_SAFE_INT)
    max_queries: Literal[0]


class InvestigationPolicy(StrictModel):
    on_budget_exceeded: Literal["stop"]
    on_scope_violation: Literal["stop"]
    on_missing_evidence: Literal["stop"]
    on_ambiguous_input: Literal["escalate"]
    on_unsupported_input: Literal["escalate"]
    causal_claims: Literal["forbidden"]


class InvestigationPlan(StrictModel):
    api_version: str = Field(default=API_VERSION, pattern=r"^auto-bi\.io/v2alpha1$")
    kind: str = Field(default="InvestigationPlan", pattern=r"^InvestigationPlan$")
    plan_id: Slug
    product_ref: DecisionRef
    question: NonBlankText
    scope: InvestigationScope
    steps: list[InvestigationStep] = Field(min_length=1)
    budget: InvestigationBudget
    policy: InvestigationPolicy

    @model_validator(mode="after")
    def _check_plan_boundaries(self) -> InvestigationPlan:
        step_ids = [step.step_id for step in self.steps]
        _reject_duplicates("steps.step_id", step_ids)
        if len(self.steps) > self.budget.max_steps:
            raise ValueError(
                f"plan declares {len(self.steps)} steps, exceeding "
                f"budget.max_steps={self.budget.max_steps}"
            )

        metrics = set(self.scope.metrics)
        dimensions = set(self.scope.dimensions)
        for step in self.steps:
            extra_metrics = sorted(set(step.metrics) - metrics)
            extra_dimensions = sorted(set(step.dimensions) - dimensions)
            if extra_metrics:
                raise ValueError(
                    f"step {step.step_id} metrics are outside plan scope: "
                    f"{', '.join(extra_metrics)}"
                )
            if extra_dimensions:
                raise ValueError(
                    f"step {step.step_id} dimensions are outside plan scope: "
                    f"{', '.join(extra_dimensions)}"
                )
        return self

    def digest(self) -> str:
        """Content address of the ordered, fully validated plan."""
        return sha256_digest(self.model_dump(mode="json"))

    def to_yaml(self) -> str:
        """Stable UTF-8/LF YAML representation for an offline plan artifact."""
        return yaml.safe_dump(
            self.model_dump(mode="json"),
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
            line_break="\n",
        )

    @classmethod
    def from_yaml(cls, raw: str) -> InvestigationPlan:
        return cls.model_validate(yaml.safe_load(raw) or {})

    @classmethod
    def load(cls, path: str | Path) -> InvestigationPlan:
        return cls.from_yaml(Path(path).read_text(encoding="utf-8"))
