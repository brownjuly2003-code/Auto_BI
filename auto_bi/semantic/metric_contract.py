"""MetricContract — the governed definition of one metric (DECISIONOPS_ARCHITECTURE §7.3).

This is the object a release gate diffs, so everything that can silently change a number is
explicit and part of the contract digest: formula, grain, additivity, event time and
timezone, unit and precision, null/zero semantics, freshness, policy and budgets.

What the kernel refuses to do is guess. An external formula without a pinned revision, a
semi-additive metric that does not name its non-additive axis, or a bare SQL string with no
recorded review are rejected at parse time (invariants 6 and 11) — a metric whose meaning
is ambiguous cannot be the basis of a pass verdict.

``semantic/model.py`` stays the native model.yaml schema; this module is the portable
contract layered above it and does not change it.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import Field, model_validator

from auto_bi.evidence.canonical import sha256_digest
from auto_bi.refs import AssetRef, ContractRef, MetricId, PolicyRef
from auto_bi.scalars import Duration, Timestamp
from auto_bi.semantic.model import Additivity, Aggregation, DataClassification, Join
from auto_bi.strict import StrictModel

API_VERSION = "auto-bi.io/v2alpha1"


class MetricLifecycle(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class FormulaKind(StrEnum):
    """Resolution order of §7.3: a governed external definition first, raw SQL last."""

    EXTERNAL_REF = "external_ref"
    TYPED_AGGREGATE = "typed_aggregate"
    TYPED_RATIO = "typed_ratio"
    REVIEWED_SQL = "reviewed_sql"


class NullSemantics(StrEnum):
    """Whether a missing measurement is absent or a zero — §8.4 keeps these apart."""

    NULL_IS_MISSING = "null_is_missing"
    NULL_IS_ZERO = "null_is_zero"


class ZeroSemantics(StrEnum):
    ZERO_IS_VALUE = "zero_is_value"
    ZERO_IS_MISSING = "zero_is_missing"


class MetricTerm(StrictModel):
    table: str = Field(min_length=1, pattern=r"^[a-z0-9_]+\.[a-z0-9_]+$")
    column: str = Field(min_length=1)
    agg: Aggregation


class ExternalFormula(StrictModel):
    """Governed definition owned elsewhere (dbt/MetricFlow, Apache Ossie, …)."""

    source: str = Field(min_length=1)
    ref: str = Field(min_length=1)
    # Invariant 11: an external standard is only usable pinned — tag, commit or version.
    source_revision: str = Field(min_length=1)


class FormulaReview(StrictModel):
    reviewer: str = Field(min_length=1)
    reviewed_at: Timestamp
    rationale: str = Field(min_length=1)


class MetricFormula(StrictModel):
    kind: FormulaKind
    external: ExternalFormula | None = None
    aggregate: MetricTerm | None = None
    numerator: MetricTerm | None = None
    denominator: MetricTerm | None = None
    sql: str = ""
    review: FormulaReview | None = None

    @model_validator(mode="after")
    def _check_shape(self) -> MetricFormula:
        if self.kind is FormulaKind.EXTERNAL_REF:
            if self.external is None:
                raise ValueError("external_ref formula requires an `external` reference")
            self._reject(aggregate=True, terms=True, sql=True, review=True)
        elif self.kind is FormulaKind.TYPED_AGGREGATE:
            if self.aggregate is None:
                raise ValueError("typed_aggregate formula requires an `aggregate` term")
            self._reject(external=True, terms=True, sql=True, review=True)
        elif self.kind is FormulaKind.TYPED_RATIO:
            if self.numerator is None or self.denominator is None:
                raise ValueError("typed_ratio formula requires both numerator and denominator")
            self._reject(external=True, aggregate=True, sql=True, review=True)
        else:
            if not self.sql.strip():
                raise ValueError("reviewed_sql formula requires `sql`")
            if self.review is None:
                raise ValueError("reviewed_sql formula requires a recorded `review`")
            self._reject(external=True, aggregate=True, terms=True)
        return self

    def _reject(
        self,
        *,
        external: bool = False,
        aggregate: bool = False,
        terms: bool = False,
        sql: bool = False,
        review: bool = False,
    ) -> None:
        """Fail on payload that does not belong to this formula kind (no silent ignoring)."""
        unexpected: list[str] = []
        if external and self.external is not None:
            unexpected.append("external")
        if aggregate and self.aggregate is not None:
            unexpected.append("aggregate")
        if terms and (self.numerator is not None or self.denominator is not None):
            unexpected.append("numerator/denominator")
        if sql and self.sql:
            unexpected.append("sql")
        if review and self.review is not None:
            unexpected.append("review")
        if unexpected:
            raise ValueError(f"{self.kind.value} formula must not carry {', '.join(unexpected)}")


class MetricGrain(StrictModel):
    table: str = Field(min_length=1)
    keys: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_keys(self) -> MetricGrain:
        if len(set(self.keys)) != len(self.keys):
            raise ValueError("grain keys must be unique")
        return self


class MetricTime(StrictModel):
    event_time_column: str = Field(min_length=1)
    # Business timezone and calendar stay explicit: a silent UTC assumption moves day
    # boundaries and quietly changes every daily number (§8.3 treats a change here as block).
    timezone: str = Field(min_length=1)
    calendar: str = "gregorian"


class MetricUnit(StrictModel):
    unit: str = Field(min_length=1)
    precision: int = Field(ge=0, le=18)
    null_semantics: NullSemantics
    zero_semantics: ZeroSemantics


class MetricFreshness(StrictModel):
    max_age: Duration
    quality_preconditions: list[str] = Field(default_factory=list)


class MetricBudget(StrictModel):
    max_scanned_rows: int | None = Field(default=None, ge=0)
    max_scanned_bytes: int | None = Field(default=None, ge=0)
    timeout_ms: int | None = Field(default=None, ge=0)


class MetricMetadata(StrictModel):
    id: MetricId
    version: int = Field(ge=1)
    owner: str = Field(min_length=1)
    status: MetricLifecycle
    description: str = ""


class MetricContract(StrictModel):
    api_version: str = Field(default=API_VERSION, pattern=r"^auto-bi\.io/v2alpha1$")
    kind: str = Field(default="MetricContract", pattern=r"^MetricContract$")
    metadata: MetricMetadata
    formula: MetricFormula
    grain: MetricGrain
    dimensions: list[str] = Field(default_factory=list)
    join_paths: list[Join] = Field(default_factory=list)
    additivity: Additivity
    non_additive_axes: list[str] = Field(default_factory=list)
    time: MetricTime
    unit: MetricUnit
    freshness: MetricFreshness
    classification: DataClassification
    policy_refs: list[PolicyRef] = Field(default_factory=list)
    lineage_refs: list[str] = Field(default_factory=list)
    verified_asset_refs: list[AssetRef] = Field(default_factory=list)
    budgets: MetricBudget | None = None

    @model_validator(mode="after")
    def _check_semantics(self) -> MetricContract:
        if self.additivity is Additivity.SEMI_ADDITIVE and not self.non_additive_axes:
            raise ValueError(
                "semi_additive metric must list its non_additive_axes; "
                "an unnamed axis is what makes a wrong SUM look right"
            )
        if self.additivity is not Additivity.SEMI_ADDITIVE and self.non_additive_axes:
            raise ValueError("non_additive_axes only apply to a semi_additive metric")
        if len(set(self.dimensions)) != len(self.dimensions):
            raise ValueError("dimensions must be unique")
        return self

    def self_ref(self) -> str:
        """This contract's version-pinned ref (``metric://sales/gross_margin_pct@3``)."""
        return str(ContractRef.parse(self.metadata.id).with_version(self.metadata.version))

    def digest(self) -> str:
        """Content address of the whole contract, canonicalized per RFC 8785."""
        return sha256_digest(self.model_dump(mode="json"))

    @classmethod
    def load(cls, path: str | Path) -> MetricContract:
        with open(path, encoding="utf-8") as handle:
            return cls.model_validate(yaml.safe_load(handle) or {})
