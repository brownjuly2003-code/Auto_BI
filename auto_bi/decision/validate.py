"""Cross-contract validation of decision products (ARCHITECTURE §6, bootstrap task 3).

Contracts live in Git as YAML; this module reads the working tree and answers one question
offline — do they agree with each other? No DWH, BI or LLM is involved, which is what makes
it usable as a pre-merge check.

The value is in what a pinned ref promises. `metric://sales/gross_margin_pct@3` means "the
version 3 I was verified against"; if the repository now holds version 4, or no such metric
at all, every later claim built on that product is unsupported. That is a block, not a
warning. Lifecycle and policy questions a human should look at are `needs_review`, and a
deprecation that changes nothing today is advisory — the severity is a property of the
finding, not of who reads it.

Issue codes are stable identifiers meant for CI and machine consumers; the human sentence
is the message, and the two are not interchangeable (§7.7).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, ValidationError, model_validator

from auto_bi.decision.spec import DecisionProductSpec
from auto_bi.refs import ContractRef
from auto_bi.semantic.metric_contract import MetricContract, MetricLifecycle
from auto_bi.strict import StrictModel
from auto_bi.verification.model import Severity, Verdict, verdict_for

API_VERSION = "auto-bi.io/v2alpha1"

_YAML_SUFFIXES = (".yaml", ".yml")
_MAX_SCHEMA_ERRORS = 3

_LIFECYCLE_ISSUES: dict[MetricLifecycle, tuple[str, Severity, str]] = {
    MetricLifecycle.RETIRED: (
        "metric.retired",
        Severity.BLOCK,
        "metric contract is retired and must not back a decision product",
    ),
    MetricLifecycle.DRAFT: (
        "metric.draft",
        Severity.REVIEW,
        "metric contract is still a draft: its definition is not agreed yet",
    ),
    MetricLifecycle.DEPRECATED: (
        "metric.deprecated",
        Severity.ADVISORY,
        "metric contract is deprecated: plan a replacement before it is retired",
    ),
}


class ValidationIssue(StrictModel):
    code: str = Field(min_length=1)
    severity: Severity
    subject: str
    source: str
    message: str = Field(min_length=1)


class ValidationReport(StrictModel):
    api_version: str = Field(default=API_VERSION, pattern=r"^auto-bi\.io/v2alpha1$")
    kind: str = Field(default="DecisionValidationReport", pattern=r"^DecisionValidationReport$")
    verdict: Verdict
    products: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_verdict(self) -> ValidationReport:
        expected = verdict_for(issue.severity for issue in self.issues)
        if self.verdict is not expected:
            raise ValueError(
                f"verdict {self.verdict.value} contradicts the findings (expected {expected.value})"
            )
        return self


@dataclass(frozen=True)
class LoadedContracts:
    """The parsed contracts behind a :class:`ValidationReport`.

    The release gate needs both halves — the report to grade step 1 of §8.2, and the specs
    themselves to run the rest — and loading the working tree twice would let the two
    disagree about what was on disk.
    """

    products: dict[str, DecisionProductSpec]
    metrics: dict[str, MetricContract]
    report: ValidationReport


def load_contracts(paths: Iterable[str | Path]) -> LoadedContracts:
    """Load every contract under *paths* and validate the products against the metrics."""
    issues: list[ValidationIssue] = []
    products: dict[str, DecisionProductSpec] = {}
    metrics: dict[str, MetricContract] = {}

    for path in _iter_contract_files(paths, issues):
        _load_contract(path, products, metrics, issues)

    for _, product in sorted(products.items()):
        issues.extend(_validate_product(product, metrics))

    return LoadedContracts(
        products=products,
        metrics=metrics,
        report=ValidationReport(
            verdict=verdict_for(issue.severity for issue in issues),
            products=sorted(product.self_ref() for product in products.values()),
            metrics=sorted(metric.self_ref() for metric in metrics.values()),
            issues=issues,
        ),
    )


def validate_paths(paths: Iterable[str | Path]) -> ValidationReport:
    """Cross-contract report for *paths* (the `decision validate` surface)."""
    return load_contracts(paths).report


def _iter_contract_files(paths: Iterable[str | Path], issues: list[ValidationIssue]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            files.extend(
                sorted(child for child in path.rglob("*") if child.suffix in _YAML_SUFFIXES)
            )
        elif path.is_file():
            files.append(path)
        else:
            issues.append(
                ValidationIssue(
                    code="contract.unreadable",
                    severity=Severity.BLOCK,
                    subject=str(path),
                    source=str(path),
                    message="path does not exist",
                )
            )
    return files


def _load_contract(
    path: Path,
    products: dict[str, DecisionProductSpec],
    metrics: dict[str, MetricContract],
    issues: list[ValidationIssue],
) -> None:
    source = str(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        issues.append(
            ValidationIssue(
                code="contract.unreadable",
                severity=Severity.BLOCK,
                subject=source,
                source=source,
                message=f"cannot read contract: {type(exc).__name__}",
            )
        )
        return
    if not isinstance(raw, dict):
        issues.append(
            ValidationIssue(
                code="contract.unreadable",
                severity=Severity.BLOCK,
                subject=source,
                source=source,
                message="contract file must contain a YAML mapping",
            )
        )
        return

    kind = raw.get("kind")
    if kind == "DecisionProduct":
        _store(DecisionProductSpec, raw, path, products, issues)
    elif kind == "MetricContract":
        _store(MetricContract, raw, path, metrics, issues)
    else:
        issues.append(
            ValidationIssue(
                code="contract.unknown_kind",
                severity=Severity.BLOCK,
                subject=str(kind),
                source=source,
                message="kind must be DecisionProduct or MetricContract",
            )
        )


def _store(
    model: type[DecisionProductSpec] | type[MetricContract],
    raw: dict[str, Any],
    path: Path,
    into: dict[str, Any],
    issues: list[ValidationIssue],
) -> None:
    source = str(path)
    try:
        contract = model.model_validate(raw)
    except ValidationError as exc:
        issues.append(
            ValidationIssue(
                code="contract.invalid",
                severity=Severity.BLOCK,
                subject=str(raw.get("metadata", {}).get("id", path.name)),
                source=source,
                message=_schema_errors(exc),
            )
        )
        return
    identifier = contract.metadata.id
    if identifier in into:
        issues.append(
            ValidationIssue(
                code="contract.duplicate_id",
                severity=Severity.BLOCK,
                subject=identifier,
                source=source,
                message="the same contract id is defined in more than one file",
            )
        )
        return
    into[identifier] = contract


def _schema_errors(exc: ValidationError) -> str:
    """Compact, quotable schema errors — locations and reasons, never the file content."""
    parts: list[str] = []
    for error in exc.errors()[:_MAX_SCHEMA_ERRORS]:
        location = ".".join(str(item) for item in error["loc"]) or "<root>"
        parts.append(f"{location}: {error['msg']}")
    remaining = len(exc.errors()) - len(parts)
    if remaining > 0:
        parts.append(f"(+{remaining} more)")
    return "; ".join(parts)


def _validate_product(
    product: DecisionProductSpec, metrics: dict[str, MetricContract]
) -> list[ValidationIssue]:
    source = product.self_ref()
    issues: list[ValidationIssue] = []
    resolved: list[MetricContract] = []

    # `DecisionScope` already rejects a repeated pin at parse time, so every ref here is
    # distinct and the only open question is whether it resolves.
    for ref in product.scope.metrics:
        parsed = ContractRef.parse(ref)
        identifier = str(parsed.without_version())
        contract = metrics.get(identifier)
        if contract is None:
            issues.append(
                ValidationIssue(
                    code="metric_ref.unknown",
                    severity=Severity.BLOCK,
                    subject=ref,
                    source=source,
                    message="no metric contract with this id in the validated set",
                )
            )
            continue
        if contract.metadata.version != parsed.version:
            issues.append(
                ValidationIssue(
                    code="metric_ref.version_mismatch",
                    severity=Severity.BLOCK,
                    subject=ref,
                    source=source,
                    message=(
                        f"the repository holds version {contract.metadata.version} of "
                        f"{identifier}; the pinned version is gone"
                    ),
                )
            )
            continue
        resolved.append(contract)

    issues.extend(_check_dimensions(product, resolved, source))
    issues.extend(_check_lifecycle(resolved, source))
    issues.extend(_check_policy(product, resolved, source))
    return issues


def _check_dimensions(
    product: DecisionProductSpec, resolved: Sequence[MetricContract], source: str
) -> list[ValidationIssue]:
    """Every scope dimension must be declared by every metric it will be split by."""
    issues: list[ValidationIssue] = []
    for dimension in sorted(product.scope.dimensions):
        missing = [metric.self_ref() for metric in resolved if dimension not in metric.dimensions]
        if missing:
            issues.append(
                ValidationIssue(
                    code="dimension.not_allowed",
                    severity=Severity.BLOCK,
                    subject=dimension,
                    source=source,
                    message=(f"dimension {dimension!r} is not declared by {', '.join(missing)}"),
                )
            )
    return issues


def _check_lifecycle(resolved: Sequence[MetricContract], source: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for metric in resolved:
        entry = _LIFECYCLE_ISSUES.get(metric.metadata.status)
        if entry is None:
            continue
        code, severity, message = entry
        issues.append(
            ValidationIssue(
                code=code,
                severity=severity,
                subject=metric.self_ref(),
                source=source,
                message=message,
            )
        )
    return issues


def _check_policy(
    product: DecisionProductSpec, resolved: Sequence[MetricContract], source: str
) -> list[ValidationIssue]:
    """A metric governed by a different policy than the product verifies under."""
    issues: list[ValidationIssue] = []
    policy = product.verification.policy
    for metric in resolved:
        if metric.policy_refs and policy not in metric.policy_refs:
            issues.append(
                ValidationIssue(
                    code="policy.mismatch",
                    severity=Severity.REVIEW,
                    subject=metric.self_ref(),
                    source=source,
                    message=(
                        f"metric is governed by {', '.join(metric.policy_refs)}, "
                        f"the product verifies under {policy}"
                    ),
                )
            )
    return issues
