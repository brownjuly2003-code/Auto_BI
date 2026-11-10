"""DecisionProductSpec — the external contract over dashboards (ARCHITECTURE §7.2).

A decision product answers one question for one consumer, from pinned metrics, verified
under one policy, delivered through pinned dashboards. It is deliberately *not* an
extension of ``DashboardSpec``: the presentation IR stays free of lifecycle, approvals and
evidence (invariant 1), and this contract references it (``dashboard://weekly_margin@7``)
instead of absorbing it. That is what keeps the v1 compiler and its tests untouched while
the lifecycle grows here.

Everything that decides a verdict is pinned to a version: metric refs, the verification
policy and the delivered dashboards. An unpinned ref would let a verified product drift to
a changed definition without a new run — the exact failure this gate exists to catch.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import Field, model_validator

from auto_bi.evidence.canonical import sha256_digest
from auto_bi.refs import ContractRef, DashboardRef, DecisionId, MetricRef, PolicyRef
from auto_bi.scalars import MAX_SAFE_INT, DecimalString, Duration, NonBlankText
from auto_bi.strict import StrictModel

API_VERSION = "auto-bi.io/v2alpha1"


class RiskTier(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Cadence(StrEnum):
    """Describes the product. The scheduler that would act on it is not in the MVP."""

    AD_HOC = "ad_hoc"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


class BriefFormat(StrEnum):
    MARKDOWN = "markdown"


class ActionsMode(StrEnum):
    DISABLED = "disabled"
    PROPOSAL_ONLY = "proposal_only"


class DecisionMetadata(StrictModel):
    id: DecisionId
    version: int = Field(ge=1, le=MAX_SAFE_INT)
    owner: NonBlankText
    risk_tier: RiskTier


class DecisionStatement(StrictModel):
    question: NonBlankText
    consumer: NonBlankText
    cadence: Cadence


class DecisionScope(StrictModel):
    metrics: list[MetricRef] = Field(min_length=1)
    dimensions: list[str] = Field(default_factory=list)
    time_window: Duration

    @model_validator(mode="after")
    def _check_unique(self) -> DecisionScope:
        """Both lists are sets by meaning, and a repeated entry changes the digest without
        changing the scope — the same rule `MetricContract` applies to its own lists."""
        for name, values in (("metrics", self.metrics), ("dimensions", self.dimensions)):
            duplicates = sorted({value for value in values if values.count(value) > 1})
            if duplicates:
                raise ValueError(f"duplicate entries in scope.{name}: {', '.join(duplicates)}")
        return self


class VerificationSpec(StrictModel):
    policy: PolicyRef
    freshness_max_age: Duration
    # Decimal string, not a float: the tolerance is compared against exact decimals (§8.4).
    cross_surface_tolerance: DecimalString


class DashboardDelivery(StrictModel):
    ref: DashboardRef


class DeliverySpec(StrictModel):
    dashboards: list[DashboardDelivery] = Field(min_length=1)
    brief_formats: list[BriefFormat] = Field(default_factory=lambda: [BriefFormat.MARKDOWN])

    @model_validator(mode="after")
    def _check_unique(self) -> DeliverySpec:
        """Both lists are sets by meaning, as the lists of `DecisionScope` are: a repeated
        entry changes the digest without changing the delivery. The gate also takes each entry
        of `dashboards`: a dashboard delivered twice would be named twice in its evidence, and
        if its spec compiles, its payloads would be sealed under one id twice."""
        refs = [item.ref for item in self.dashboards]
        formats = [item.value for item in self.brief_formats]
        for name, values in (("dashboards", refs), ("brief_formats", formats)):
            duplicates = sorted({value for value in values if values.count(value) > 1})
            if duplicates:
                raise ValueError(f"duplicate entries in delivery.{name}: {', '.join(duplicates)}")
        return self


class ActionsSpec(StrictModel):
    mode: ActionsMode = ActionsMode.DISABLED

    @model_validator(mode="after")
    def _check_mode(self) -> ActionsSpec:
        if self.mode is not ActionsMode.DISABLED:
            raise ValueError(
                f"actions mode {self.mode.value!r} is not implemented; "
                "write-actions stay an explicit non-goal until the release gate is proven"
            )
        return self


class DecisionProductSpec(StrictModel):
    api_version: str = Field(default=API_VERSION, pattern=r"^auto-bi\.io/v2alpha1$")
    kind: str = Field(default="DecisionProduct", pattern=r"^DecisionProduct$")
    metadata: DecisionMetadata
    decision: DecisionStatement
    scope: DecisionScope
    verification: VerificationSpec
    delivery: DeliverySpec
    actions: ActionsSpec = Field(default_factory=ActionsSpec)

    def self_ref(self) -> str:
        """This product's version-pinned ref (``decision://sales/weekly_margin@1``)."""
        return str(ContractRef.parse(self.metadata.id).with_version(self.metadata.version))

    def digest(self) -> str:
        """Content address of the whole product spec, canonicalized per RFC 8785."""
        return sha256_digest(self.model_dump(mode="json"))

    @classmethod
    def load(cls, path: str | Path) -> DecisionProductSpec:
        with open(path, encoding="utf-8") as handle:
            return cls.model_validate(yaml.safe_load(handle) or {})
