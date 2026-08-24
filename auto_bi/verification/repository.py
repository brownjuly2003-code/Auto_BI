"""Verified Asset Repository (DECISIONOPS_ARCHITECTURE §7.5).

A verified asset is a reviewed question with a known-good answer: canonical question and
paraphrases, intent, the metrics it is built on, a reference plan, the expected result (or
the invariants that must hold, or the clarification/refusal a negative case must produce),
the tolerance the comparison runs under, and who reviewed it when.

Two rules keep an asset trustworthy rather than merely present:

* it records the semantic digest it was verified against, so an asset carried over from a
  different snapshot is *stale*, not evidence;
* a semantic change that alters meaning invalidates it (§8.2 step 5). An expectation nobody
  re-checked after a formula change is worse than no expectation — it looks like proof.

Assets are reviewed artifacts in Git. A production interaction only ever enters a redacted
review queue, and an expected result is never fed back into an LLM context (§7.5).
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import Field, model_validator

from auto_bi.decision.spec import RiskTier
from auto_bi.evidence.canonical import sha256_digest
from auto_bi.refs import AssetId, ContractRef, MetricRef
from auto_bi.scalars import MAX_SAFE_INT, NonBlankText, Sha256Digest, Timestamp
from auto_bi.semantic.diff import SemanticDiff
from auto_bi.severity import Severity
from auto_bi.strict import StrictModel
from auto_bi.verification.reconcile import ResultSet, ResultTolerance

API_VERSION = "auto-bi.io/v2alpha1"

_YAML_SUFFIXES = (".yaml", ".yml")
# Severities that mean "the number may have moved"; an advisory difference does not.
_INVALIDATING = (Severity.REVIEW, Severity.BLOCK)


class AssetIntent(StrEnum):
    METRIC_VALUE = "metric_value"
    BREAKDOWN = "breakdown"
    TREND = "trend"
    COMPARISON = "comparison"
    #: Negative cases: the right answer is a question back, or a refusal.
    CLARIFICATION = "clarification"
    REFUSAL = "refusal"


class ExpectationKind(StrEnum):
    RESULT = "result"
    INVARIANTS = "invariants"
    CLARIFICATION = "clarification"
    REFUSAL = "refusal"


class AssetExpectation(StrictModel):
    kind: ExpectationKind
    result: ResultSet | None = None
    invariants: list[str] = Field(default_factory=list)
    clarification: str = ""
    refusal_reason: str = ""

    @model_validator(mode="after")
    def _check_payload(self) -> AssetExpectation:
        if self.kind is ExpectationKind.RESULT and self.result is None:
            raise ValueError("expectation of kind result requires a `result`")
        if self.kind is ExpectationKind.INVARIANTS and not self.invariants:
            raise ValueError("expectation of kind invariants requires at least one invariant")
        if self.kind is ExpectationKind.CLARIFICATION and not self.clarification.strip():
            raise ValueError("expectation of kind clarification requires the expected question")
        if self.kind is ExpectationKind.REFUSAL and not self.refusal_reason.strip():
            raise ValueError("expectation of kind refusal requires the expected refusal reason")
        return self


class AssetReview(StrictModel):
    reviewer: NonBlankText
    reviewed_at: Timestamp
    rationale: NonBlankText


class AssetMetadata(StrictModel):
    id: AssetId
    version: int = Field(ge=1, le=MAX_SAFE_INT)
    owner: NonBlankText
    risk_tier: RiskTier


class VerifiedAsset(StrictModel):
    api_version: str = Field(default=API_VERSION, pattern=r"^auto-bi\.io/v2alpha1$")
    kind: str = Field(default="VerifiedAsset", pattern=r"^VerifiedAsset$")
    metadata: AssetMetadata
    question: NonBlankText
    paraphrases: list[str] = Field(default_factory=list)
    intent: AssetIntent
    metric_refs: list[MetricRef] = Field(default_factory=list)
    expectation: AssetExpectation
    tolerance: ResultTolerance = Field(default_factory=ResultTolerance)
    reference_plan: str = ""
    #: The canonical digest of the semantic snapshot this asset was verified against.
    semantic_digest: Sha256Digest
    review: AssetReview

    @model_validator(mode="after")
    def _check_shape(self) -> VerifiedAsset:
        negative = self.intent in (AssetIntent.CLARIFICATION, AssetIntent.REFUSAL)
        if not negative and not self.metric_refs:
            raise ValueError("a positive verified asset must name its metric_refs")
        if self.expectation.kind is ExpectationKind.RESULT and not self.tolerance.business_key:
            raise ValueError(
                "an expected result needs a tolerance.business_key: without one every "
                "comparison is `insufficient` by construction"
            )
        return self

    def metric_ids(self) -> list[str]:
        return [str(ContractRef.parse(ref).without_version()) for ref in self.metric_refs]

    def self_ref(self) -> str:
        return str(ContractRef.parse(self.metadata.id).with_version(self.metadata.version))

    def digest(self) -> str:
        return sha256_digest(self.model_dump(mode="json"))


class AssetInvalidation(StrictModel):
    asset: NonBlankText
    code: NonBlankText
    severity: Severity
    detail: NonBlankText


class VerifiedAssetRepository:
    """The reviewed corpus a release gate re-checks against."""

    def __init__(self, assets: list[VerifiedAsset]) -> None:
        seen: set[str] = set()
        for item in assets:
            if item.metadata.id in seen:
                raise ValueError(f"duplicate verified asset id: {item.metadata.id}")
            seen.add(item.metadata.id)
        self.assets = sorted(assets, key=lambda item: item.metadata.id)

    @classmethod
    def load(cls, path: str | Path) -> VerifiedAssetRepository:
        root = Path(path)
        files = (
            [root]
            if root.is_file()
            else sorted(
                child
                for child in root.rglob("*")
                if child.suffix in _YAML_SUFFIXES and child.is_file()
            )
        )
        assets = [
            VerifiedAsset.model_validate(yaml.safe_load(file.read_text(encoding="utf-8")) or {})
            for file in files
        ]
        return cls(assets)

    def get(self, asset_id: str) -> VerifiedAsset | None:
        return next((item for item in self.assets if item.metadata.id == asset_id), None)

    def invalidate(self, diff: SemanticDiff) -> list[AssetInvalidation]:
        """Assets a semantic change makes unusable as evidence, worst reason first."""
        invalidations: list[AssetInvalidation] = []
        for item in self.assets:
            if item.semantic_digest != diff.base_digest:
                invalidations.append(
                    AssetInvalidation(
                        asset=item.self_ref(),
                        code="asset.stale_snapshot",
                        severity=Severity.REVIEW,
                        detail=(
                            "verified against another snapshot "
                            f"({item.semantic_digest}), not {diff.base_digest}"
                        ),
                    )
                )
                continue
            invalidation = _invalidation_for(item, diff)
            if invalidation is not None:
                invalidations.append(invalidation)
        return invalidations


def _invalidation_for(item: VerifiedAsset, diff: SemanticDiff) -> AssetInvalidation | None:
    owned = set(item.metric_ids())
    removed: list[str] = []
    changed: list[str] = []
    for change in diff.changes:
        if change.severity not in _INVALIDATING:
            continue
        if not owned.intersection(change.impact.metrics):
            continue
        if change.code == "metric.removed":
            removed.append(change.subject)
        else:
            changed.append(f"{change.code} on {change.subject}")

    if removed:
        return AssetInvalidation(
            asset=item.self_ref(),
            code="asset.metric_removed",
            severity=Severity.BLOCK,
            detail=f"metric gone: {', '.join(sorted(removed))}",
        )
    if changed:
        return AssetInvalidation(
            asset=item.self_ref(),
            code="asset.metric_changed",
            severity=Severity.REVIEW,
            detail=f"re-review required: {', '.join(sorted(changed))}",
        )
    return None
