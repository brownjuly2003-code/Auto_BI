"""Cross-contract validation of decision products (ARCHITECTURE §6, bootstrap task 3).

Contracts live in Git as YAML/JSON; this module reads the working tree and answers one question
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

from auto_bi import filesystem
from auto_bi.decision.spec import DecisionProductSpec
from auto_bi.refs import ContractRef
from auto_bi.semantic.metric_contract import MetricContract, MetricLifecycle
from auto_bi.strict import StrictModel
from auto_bi.verification.model import Severity, Verdict, verdict_for
from auto_bi.yaml_document import load_yaml_document, schema_reason

API_VERSION = "auto-bi.io/v2alpha1"

_CONTRACT_SUFFIXES = (".yaml", ".yml", ".json")
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
class RefusedProduct:
    """A file of kind ``DecisionProduct`` that fails its schema: no product, but one written.

    *declared* is the id its metadata declares, spelled as the subject of its
    ``contract.invalid`` issue — ``None`` when it declares none, where the issue names the
    file — and *source* is its file, spelled as the source of that issue. *reason* is what
    the schema refused, in the one line of a usage error: it is printed by a run that cannot
    find the product, and never kept in a report or a bundle.
    """

    declared: str | None
    source: str
    reason: str


@dataclass(frozen=True)
class LoadedContracts:
    """The parsed contracts behind a :class:`ValidationReport`.

    The release gate needs both halves — the report to grade step 1 of §8.2, and the specs
    themselves to run the rest — and loading the working tree twice would let the two
    disagree about what was on disk. The decision products the schema refused are kept
    apart, in the order their files were read, so that a run asked for one can say why it
    is not there: the report's issue does not say of what kind its file was.
    """

    products: dict[str, DecisionProductSpec]
    metrics: dict[str, MetricContract]
    report: ValidationReport
    refused_products: tuple[RefusedProduct, ...] = ()


def load_contracts(paths: Iterable[str | Path]) -> LoadedContracts:
    """Load every contract under *paths* and validate the products against the metrics."""
    issues: list[ValidationIssue] = []
    products: dict[str, DecisionProductSpec] = {}
    metrics: dict[str, MetricContract] = {}
    refused: list[RefusedProduct] = []

    for path in _iter_contract_files(paths, issues):
        _load_contract(path, products, metrics, issues, refused)

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
        refused_products=tuple(refused),
    )


def validate_paths(paths: Iterable[str | Path]) -> ValidationReport:
    """Cross-contract report for *paths* (the `decision validate` surface)."""
    return load_contracts(paths).report


def _iter_contract_files(paths: Iterable[str | Path], issues: list[ValidationIssue]) -> list[Path]:
    """The contract files under *paths*, and an issue for each path that was not gone through.

    The report answers for every file under its paths. A directory the filesystem refuses
    to list is therefore no directory without contracts, and a path it refuses to look at
    — as one under a directory that may not be searched — is no path that does not exist:
    each is an issue of its own, and everything that could be listed is still loaded.

    The paths are gone through in the order they are given, and what a directory holds in
    the order of its paths (`_path_order`). A file is read once, however many of the paths
    reach it (`_each_file_once`).
    """
    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        try:
            is_dir = filesystem.is_dir(path)
            is_file = not is_dir and filesystem.is_file(path)
        except OSError as exc:
            issues.append(_unread_path(path, f"cannot look at path: {type(exc).__name__}"))
            continue
        if is_dir:
            refused: list[OSError] = []
            files.extend(
                sorted(
                    (
                        child
                        for child in filesystem.paths_under(path, refused)
                        if _names_a_contract(child)
                    ),
                    key=_path_order,
                )
            )
            unlisted = sorted(
                (
                    (Path(error.filename) if error.filename else path, type(error).__name__)
                    for error in refused
                ),
                key=lambda unread: (_path_order(unread[0]), unread[1]),
            )
            issues.extend(
                _unread_path(directory, f"cannot list directory: {refusal}")
                for directory, refusal in unlisted
            )
        elif is_file:
            files.append(path)
        else:
            issues.append(_unread_path(path, "path does not exist"))
    return _each_file_once(files)


def _each_file_once(files: list[Path]) -> list[Path]:
    """*files* with each file once, under the first of its paths, in the order they were found.

    Two of the validated paths can reach one file: a directory and a file in it, one directory
    given twice, one file spelled two ways. Read twice, the file would be the contract of its
    id "defined in more than one file", and a contract set that holds every id once would
    block. A file is told by the filesystem's identity of it (`filesystem.file_identity`), not
    by the text of its path: text that differs can name one file, and text made alike by
    folding `..` can name two. Only one text is sure to name one file: the path's own, as
    `pathlib` writes it — `.` and doubled separators dropped, case kept. Two paths are one file
    when their identities are one or their texts are; a path without an identity — not there,
    refused a look, on a filesystem that numbers no file, a named stream — is one file with a
    path of its text only, and one path is one file whether a look gives it an identity or not.
    Two files of equal bytes stay two files, and their id is still defined twice.
    """
    seen: set[tuple[str, object]] = set()
    once: list[Path] = []
    for path in files:
        keys: set[tuple[str, object]] = {("path", str(path))}
        identity = filesystem.file_identity(path)
        if identity is not None:
            keys.add(("file", identity))
        if keys.isdisjoint(seen):
            once.append(path)
        seen |= keys
    return once


def _path_order(path: Path) -> tuple[str, ...]:
    """Where *path* stands among the paths found under one validated directory.

    Name by name, from the directory down, each name compared by code point with its case, on
    every system. `sorted` folds case where the system does, on Windows: `B.yaml` stood after
    `a.yaml` there and before it on POSIX, the issues of the two files stood in the other
    order, and of an id the two files define the run was handed the other contract.
    """
    return path.parts


def _spelled(path: Path) -> str:
    """*path* as the report names it: its names joined by `/`, on every system.

    `str` joins them by the separator of the system, so the same files read on Windows and on
    POSIX were named by two texts, and gave two reports and two runs.
    """
    return path.as_posix()


def _names_a_contract(path: Path) -> bool:
    """Whether a path found under a validated directory is read as a contract.

    One with a contract suffix is, unless it is a directory: `archive.json/` is walked, and
    the contracts it holds are found on their own. A path the filesystem refuses to look at
    is kept: reading it is what says it could not be read.
    """
    if path.suffix not in _CONTRACT_SUFFIXES:
        return False
    try:
        return not filesystem.is_dir(path)
    except OSError:
        return True


def _unread_path(path: Path, message: str) -> ValidationIssue:
    return ValidationIssue(
        code="contract.unreadable",
        severity=Severity.BLOCK,
        subject=_spelled(path),
        source=_spelled(path),
        message=message,
    )


def _load_contract(
    path: Path,
    products: dict[str, DecisionProductSpec],
    metrics: dict[str, MetricContract],
    issues: list[ValidationIssue],
    refused: list[RefusedProduct],
) -> None:
    source = _spelled(path)
    try:
        raw = load_yaml_document(path.read_text(encoding="utf-8"))
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
        refusal = _store(DecisionProductSpec, raw, path, products, issues)
        if refusal is not None:
            refused.append(
                RefusedProduct(
                    declared=_declared(raw), source=source, reason=schema_reason(refusal)
                )
            )
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
) -> ValidationError | None:
    """Keep the contract of *raw* under its id; what its schema refused, if it did."""
    source = _spelled(path)
    try:
        contract = model.model_validate(raw)
    except ValidationError as exc:
        issues.append(
            ValidationIssue(
                code="contract.invalid",
                severity=Severity.BLOCK,
                subject=_declared_id(raw, path),
                source=source,
                message=_schema_errors(exc),
            )
        )
        return exc
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
        return None
    into[identifier] = contract
    return None


def _declared_id(raw: dict[str, Any], path: Path) -> str:
    """What names a contract that fails its schema: the id it declares, or its file."""
    declared = _declared(raw)
    return path.name if declared is None else declared


def _declared(raw: dict[str, Any]) -> str | None:
    """The id a contract declares, as text, whatever its schema says of it — if it declares one."""
    metadata = raw.get("metadata")
    if isinstance(metadata, dict) and "id" in metadata:
        return str(metadata["id"])
    return None


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

    issues.extend(product_rule_issues(product, resolved))
    return issues


def product_rule_issues(
    product: DecisionProductSpec, metrics: Sequence[MetricContract]
) -> list[ValidationIssue]:
    """What the rules that read a metric contract say of *product* over *metrics*.

    Dimensions, status and governing policies, in that order. The release gate asks them a
    second time, about the candidate's definition of a pinned metric.
    """
    source = product.self_ref()
    return [
        *_check_dimensions(product, metrics, source),
        *_check_lifecycle(metrics, source),
        *_check_policy(product, metrics, source),
    ]


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
