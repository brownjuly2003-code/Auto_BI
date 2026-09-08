"""Verified Asset Repository (DECISIONOPS_ARCHITECTURE §7.5).

A verified asset is a reviewed question with a known-good answer: canonical question and
paraphrases, intent, the metrics it is built on, a reference plan, the expected result (or
the invariants that must hold, or the clarification/refusal a negative case must produce),
the tolerance the comparison runs under, and who reviewed it when.

Three rules keep an asset trustworthy rather than merely present:

* it records the semantic digest it was verified against, so an asset carried over from a
  different snapshot is *stale*, not evidence;
* a semantic change that alters meaning invalidates it (§8.2 step 5). An expectation nobody
  re-checked after a formula change is worse than no expectation — it looks like proof;
* the reviewed query is part of the asset, not a note beside it (`ReferenceQuery`). The
  gate is handed candidate numbers rather than executing them, so the reviewed query's
  digest is the only thing an attestation can be compared against — an asset without one
  can hold an expectation nobody can tie a number to.

Assets are reviewed artifacts in Git. A production interaction only ever enters a redacted
review queue, and an expected result is never fed back into an LLM context (§7.5).
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import Field, model_validator

from auto_bi.agent.sql_guard import SQLGuardError, extract_table_refs, guard_sql
from auto_bi.decision.spec import RiskTier
from auto_bi.engine import is_known_engine, sqlglot_dialect
from auto_bi.evidence.canonical import sha256_digest
from auto_bi.refs import AssetId, ContractRef, MetricRef
from auto_bi.scalars import MAX_SAFE_INT, NonBlankText, Sha256Digest, Timestamp
from auto_bi.semantic.diff import SemanticDiff
from auto_bi.semantic.gateway import SemanticSnapshot
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


class ReferenceQuery(StrictModel):
    """The reviewed SQL that answers this asset's question.

    Free-text `reference_plan` says *how* the answer is meant to be obtained; this says it
    exactly enough to be a content address. That is the whole point of it: a candidate
    result attests to the query that produced it, and without a reviewed query on the asset
    there is nothing for that attestation to be compared against (`provenance.py`).

    The SQL is guarded here, at the contract boundary, rather than wherever it is later
    read: a verified asset carrying an `INSERT` is a broken reviewed artifact, and invariant
    7 does not become optional because nobody has executed it yet. The engine is required
    and must be one the seam knows — `sqlglot_dialect` substitutes the v1 engine for an
    unfamiliar name, and guarding PostgreSQL text with the ClickHouse grammar would either
    reject valid SQL or, worse, accept what it failed to parse as a write.
    """

    engine: NonBlankText
    sql: NonBlankText

    @model_validator(mode="after")
    def _check_sql(self) -> ReferenceQuery:
        if not is_known_engine(self.engine):
            raise ValueError(
                f"unknown engine {self.engine!r}: a reference query must name an engine the "
                "seam knows, so its SQL is guarded with the grammar it was written in"
            )
        try:
            guard_sql(self.sql, dialect=sqlglot_dialect(self.engine))
        except SQLGuardError as exc:
            raise ValueError(f"reference query is not a plain SELECT: {exc}") from None
        return self

    def digest(self) -> str:
        """Content address of the reviewed query — what an attestation is compared against.

        Over the engine as well as the text, because one statement means two things on two
        engines. The canonical form is RFC 8785 over `{"engine": …, "sql": …}` so that a
        capture runner outside this codebase can compute the same digest from the same two
        strings (§9.1).
        """
        return sha256_digest({"engine": self.engine, "sql": self.sql})


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
    #: The reviewed SQL a candidate result must attest to. Optional, because an asset whose
    #: expectation is a clarification or a refusal has no query at all — but an expected
    #: *result* without one leaves the number's origin uncheckable (`provenance.py`).
    reference_query: ReferenceQuery | None = None
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

    def ungoverned(self, snapshot: SemanticSnapshot) -> list[AssetInvalidation]:
        """Assets whose reviewed query reads a mart this snapshot does not govern.

        The provenance chain anchors on the reviewed query (§8.4.1): a candidate number is
        tied to the SQL a reviewer signed off. That anchor is worth exactly as much as its
        attachment to the governed model — a reviewed query reading `dm.sales_daily_v2`
        produces an attested, reconciled number computed from a mart nobody governs, which
        is the failure this product exists to prevent.

        **Tables only, never columns.** Attributing an unqualified column to one of several
        tables in scope, across joins, CTEs and subqueries, is a guess, and a gate that
        guesses about governance has stopped being evidence (invariant 6). A table name is
        unambiguous, and it is the level at which a mart is governed.

        Case is read the way the asset's engine reads it: ClickHouse distinguishes
        `Sales_Daily` from `sales_daily`, the PostgreSQL family folds an *unquoted*
        identifier to lower case, and grading one engine's rule on the other would either
        miss a real mismatch or invent one. Quoting is part of that question and travels
        with the name: `"Sales_Daily"` on PostgreSQL is a table `sales_daily` is not, so a
        quoted reference is compared exactly on every engine.
        """
        findings: list[AssetInvalidation] = []
        governed = {node.id for node in snapshot.graph.tables}
        folded = {name.lower() for name in governed}
        for item in self.assets:
            query = item.reference_query
            if query is None:
                continue
            dialect = sqlglot_dialect(query.engine)
            folds = dialect != "clickhouse"
            unknown = sorted(
                ref.name
                for ref in extract_table_refs(query.sql, dialect=dialect)
                if ref.name not in governed
                and not (folds and not ref.quoted and ref.name.lower() in folded)
            )
            if unknown:
                findings.append(
                    AssetInvalidation(
                        asset=item.self_ref(),
                        code="asset.query_ungoverned",
                        severity=Severity.REVIEW,
                        detail=(
                            "the reviewed reference_query reads "
                            f"{', '.join(unknown)}, which this snapshot does not govern: "
                            "a number from it is not a number about this model"
                        ),
                    )
                )
        return findings

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
