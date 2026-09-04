"""Canonical, offline-only input for deterministic investigation skills."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import Field, model_validator

from auto_bi.evidence.canonical import canonical_bytes, sha256_digest
from auto_bi.refs import MetricRef
from auto_bi.scalars import DecimalString, Duration, NonBlankText, Slug, Timestamp
from auto_bi.semantic.model import Additivity
from auto_bi.strict import StrictModel

API_VERSION = "auto-bi.io/v2alpha1"


class InvestigationPeriod(StrEnum):
    COMPARISON = "comparison"
    CURRENT = "current"


class MetricResultRow(StrictModel):
    period: InvestigationPeriod
    dimensions: dict[str, NonBlankText | None] = Field(default_factory=dict)
    value: DecimalString | None

    @model_validator(mode="after")
    def _check_dimension_names(self) -> MetricResultRow:
        bad = sorted(name for name in self.dimensions if not name.strip())
        if bad:
            raise ValueError("row dimension names must carry content")
        return self


class CanonicalMetricResult(StrictModel):
    """A complete aggregate result supplied by a trusted, external read-only step.

    Skills never receive a query executor. Rows carry decimal strings and two explicit
    periods; the declared dimension schema makes missing/extra grouping keys observable.
    Duplicate grains and NULL measurements remain representable so ``data_health`` can
    report them, while analytical skills refuse them with typed gaps.
    """

    api_version: str = Field(default=API_VERSION, pattern=r"^auto-bi\.io/v2alpha1$")
    kind: str = Field(default="CanonicalMetricResult", pattern=r"^CanonicalMetricResult$")
    result_id: Slug
    metric_ref: MetricRef
    unit: str = ""
    dimensions: list[NonBlankText] = Field(default_factory=list)
    additivity: Additivity
    complete: bool
    observed_at: Timestamp
    source_max_event_time: Timestamp | None = None
    freshness_max_age: Duration | None = None
    rows: list[MetricResultRow] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_shape(self) -> CanonicalMetricResult:
        if len(set(self.dimensions)) != len(self.dimensions):
            raise ValueError("result dimensions must be unique")
        expected = set(self.dimensions)
        for index, row in enumerate(self.rows):
            actual = set(row.dimensions)
            if actual != expected:
                missing = ", ".join(sorted(expected - actual)) or "none"
                extra = ", ".join(sorted(actual - expected)) or "none"
                raise ValueError(
                    f"row {index} dimension schema mismatch; missing: {missing}; extra: {extra}"
                )
        if (self.source_max_event_time is None) != (self.freshness_max_age is None):
            raise ValueError(
                "source_max_event_time and freshness_max_age must be supplied together"
            )
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.model_dump(mode="json"))

    def digest(self) -> str:
        return sha256_digest(self.model_dump(mode="json"))

    def to_yaml(self) -> str:
        return yaml.safe_dump(
            self.model_dump(mode="json"),
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
            line_break="\n",
        )

    @classmethod
    def from_yaml(cls, raw: str) -> CanonicalMetricResult:
        return cls.model_validate(yaml.safe_load(raw) or {})

    @classmethod
    def load(cls, path: str | Path) -> CanonicalMetricResult:
        return cls.from_yaml(Path(path).read_text(encoding="utf-8"))
