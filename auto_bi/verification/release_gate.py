"""Analytics Release & Evidence Gate — orchestration only (DECISIONOPS_ARCHITECTURE §8.2).

One decision product, two semantic snapshots, a fixed sequence of typed checks, one
content-addressed evidence bundle, one verdict. Every step is a call into a module that
already exists and is tested on its own; this file decides the order, grades the findings
and writes the evidence. It reads no files and opens no sockets — loading inputs is
`decision/use_cases.py`, and executing SQL is not implemented (see below).

Two properties this module exists to keep:

* **`operational_status` and `verdict` are different answers** (invariant 8). A run that
  completes and blocks is a working gate; a run that fails is a broken one. They are
  separate fields and a report that mixes them does not validate.
* **A step that did not run says so.** Compiling and executing SQL against the DWH
  (§8.2 steps 6–7) has no offline implementation yet, so the gate emits those checks as
  `check.not_implemented` advisories rather than leaving a clean bundle that reads as a
  full verification. The same applies downstream of a metric contract that failed to load:
  the diff would call the unparsed contract a *removed metric*, so those checks are emitted
  as `check.not_run` instead of a guess (see `UNTRUSTWORTHY_GAPS` for why that is the only
  gap that stops the sequence).

The evidence bundle carries every input and finding the verdict was computed from. The
`evidence.bundle` check itself is in the report but not in the bundle — it grades the
bundle's own re-verification, and a check cannot address the artifact it validates.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from pydantic import Field, model_validator

from auto_bi.decision.impact import ImpactReport, resolve_impact
from auto_bi.decision.spec import DecisionProductSpec
from auto_bi.decision.validate import ValidationReport
from auto_bi.evidence.canonical import canonical_bytes, sha256_digest
from auto_bi.evidence.manifest import BundleWriter, verify_bundle
from auto_bi.evidence.model import ArtifactKind, ClaimType, EvidenceClassification
from auto_bi.evidence.store import ArtifactStore
from auto_bi.ir.spec import DashboardSpec, TargetBI
from auto_bi.scalars import NonBlankText, Sha256Digest, Slug, Timestamp
from auto_bi.semantic.diff import SemanticDiff, diff_snapshots
from auto_bi.semantic.gateway import SemanticGap, SemanticSnapshot
from auto_bi.semantic.model import SemanticModel
from auto_bi.severity import Severity
from auto_bi.strict import StrictModel
from auto_bi.verification.bi_contract import (
    BIContractCompiler,
    CompiledBIContract,
    DataLensContractCompiler,
    SupersetContractCompiler,
)
from auto_bi.verification.model import Verdict, verdict_for
from auto_bi.verification.reconcile import (
    ComparisonOutcome,
    ComparisonReport,
    ResultSet,
    compare_results,
)
from auto_bi.verification.repository import (
    ExpectationKind,
    VerifiedAsset,
    VerifiedAssetRepository,
)

API_VERSION = "auto-bi.io/v2alpha1"

#: Part of the idempotency key: a different verifier can reach a different verdict from the
#: same inputs, so its bundles must not be reused (§7.6).
VERIFIER_VERSION = "1"

#: Bumped when a check's meaning changes, so an old evidence bundle is not read as if it
#: had been graded by today's rule.
CHECK_VERSION = "1"

#: Gaps that mean the canonical graph does not represent its source *at all*, so a diff over
#: it would be fiction: a metric contract that failed to parse is simply absent from the
#: candidate graph, and the diff would call that a removed metric. Every other blocking gap
#: (a dangling join, a metric field the model does not declare) describes a truthful graph
#: with a named hole — the diff and the impact over it are still worth reading, and the gap's
#: own `block` severity already stops the release.
UNTRUSTWORTHY_GAPS = frozenset({"gap.contract_invalid"})

_COMPILERS: dict[TargetBI, Callable[[], BIContractCompiler]] = {
    TargetBI.SUPERSET: SupersetContractCompiler,
    TargetBI.DATALENS: DataLensContractCompiler,
}


class RunStatus(StrEnum):
    """Operational lifecycle (§7.6) — never the verdict."""

    COMPLETED = "completed"
    FAILED = "failed"


class CheckStatus(StrEnum):
    PASS = "pass"
    ADVISORY = "advisory"
    REVIEW = "review"
    BLOCK = "block"
    #: The check could not be evaluated — a runtime fault, not a graded outcome.
    ERROR = "error"


class CheckCategory(StrEnum):
    CONTRACT = "contract"
    SEMANTIC = "semantic"
    IMPACT = "impact"
    VERIFICATION = "verification"
    RESULT = "result"
    EXECUTION = "execution"
    DELIVERY = "delivery"
    EVIDENCE = "evidence"


_STATUS_FOR_SEVERITY: dict[Severity, CheckStatus] = {
    Severity.ADVISORY: CheckStatus.ADVISORY,
    Severity.REVIEW: CheckStatus.REVIEW,
    Severity.BLOCK: CheckStatus.BLOCK,
}
_SEVERITY_FOR_STATUS: dict[CheckStatus, Severity] = {
    CheckStatus.ADVISORY: Severity.ADVISORY,
    CheckStatus.REVIEW: Severity.REVIEW,
    CheckStatus.BLOCK: Severity.BLOCK,
}


def status_for(severities: Sequence[Severity]) -> CheckStatus:
    """Worst severity present, as a check status; nothing found is a pass."""
    worst = CheckStatus.PASS
    order = [CheckStatus.PASS, CheckStatus.ADVISORY, CheckStatus.REVIEW, CheckStatus.BLOCK]
    for severity in severities:
        candidate = _STATUS_FOR_SEVERITY[severity]
        if order.index(candidate) > order.index(worst):
            worst = candidate
    return worst


class CheckResult(StrictModel):
    """One typed check (§7.7).

    `summary_code` is the stable machine identity of the outcome; `detail` is the sentence a
    human reads, and the two are not interchangeable. Timing is deliberately absent: a wall
    clock inside a content-addressed artifact makes the same inputs produce a different
    bundle every run, and observability belongs to the trace, not to the evidence (§13).
    """

    check_id: Slug
    check_version: NonBlankText
    category: CheckCategory
    subject_refs: list[str] = Field(default_factory=list)
    status: CheckStatus
    summary_code: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
    detail: NonBlankText
    evidence_refs: list[Slug] = Field(default_factory=list)

    def severity(self) -> Severity | None:
        return _SEVERITY_FOR_STATUS.get(self.status)


class GateReport(StrictModel):
    api_version: str = Field(default=API_VERSION, pattern=r"^auto-bi\.io/v2alpha1$")
    kind: str = Field(default="DecisionGateReport", pattern=r"^DecisionGateReport$")
    run_id: Slug
    product_ref: NonBlankText
    #: §7.6: same inputs, same verifier, same key — the only condition under which a prior
    #: terminal bundle may be reused.
    idempotency_key: Sha256Digest
    base_digest: Sha256Digest
    candidate_digest: Sha256Digest
    operational_status: RunStatus
    verdict: Verdict
    checks: list[CheckResult] = Field(default_factory=list)
    evidence_digest: Sha256Digest
    created_at: Timestamp

    @model_validator(mode="after")
    def _check_consistency(self) -> GateReport:
        if any(check.status is CheckStatus.ERROR for check in self.checks):
            if self.operational_status is not RunStatus.FAILED:
                raise ValueError(
                    "a check that errored means the run failed; a verdict computed from an "
                    "incomplete check set would not be one (invariant 8)"
                )
            return self
        severities = [
            severity for check in self.checks if (severity := check.severity()) is not None
        ]
        expected = verdict_for(severities)
        if self.verdict is not expected:
            raise ValueError(
                f"verdict {self.verdict.value} contradicts the checks (expected {expected.value})"
            )
        return self

    def blocking(self) -> list[CheckResult]:
        return [check for check in self.checks if check.status is CheckStatus.BLOCK]


class ResultSource(Protocol):
    """Where the gate gets the candidate answer to a verified question (§8.2 step 8).

    A live implementation would compile the asset's plan and execute it under the SQL guard.
    The offline gate is handed recorded results instead, which is what lets the whole gate
    run on fixtures in CI — and what keeps `verify` from ever reaching the DWH.
    """

    def result_for(self, asset: VerifiedAsset) -> ResultSet | None: ...


class RecordedResults:
    """Candidate results captured outside the gate, keyed by verified-asset id."""

    def __init__(self, by_asset: Mapping[str, ResultSet]) -> None:
        self._by_asset = dict(by_asset)

    def result_for(self, asset: VerifiedAsset) -> ResultSet | None:
        return self._by_asset.get(asset.metadata.id)


class NoResults:
    """No candidate answers available — every comparison is honestly `insufficient`."""

    def result_for(self, asset: VerifiedAsset) -> ResultSet | None:
        return None


@dataclass(frozen=True)
class GateInputs:
    """Everything §8.1 lists as gate input, already loaded and parsed."""

    product: DecisionProductSpec
    validation: ValidationReport
    base: SemanticSnapshot
    candidate: SemanticSnapshot
    #: The candidate `model.yaml` itself: the BI compilers bind charts to columns, which the
    #: canonical graph deliberately abstracts away.
    candidate_model: SemanticModel
    #: Pinned dashboard ref -> its presentation IR. A ref with no spec is a finding.
    dashboards: Mapping[str, DashboardSpec] = field(default_factory=dict)
    assets: VerifiedAssetRepository = field(default_factory=lambda: VerifiedAssetRepository([]))
    results: ResultSource = field(default_factory=NoResults)
    #: The DWH/BI environment this verdict is about; part of the idempotency key.
    environment_ref: str = ""


def idempotency_key(inputs: GateInputs) -> str:
    """§7.6: product spec, both snapshots, policy, environment and verifier version."""
    return sha256_digest(
        {
            "product_digest": inputs.product.digest(),
            "base_digest": inputs.base.canonical_digest,
            "candidate_digest": inputs.candidate.canonical_digest,
            "policy": inputs.product.verification.policy,
            "environment_ref": inputs.environment_ref,
            "verifier_version": VERIFIER_VERSION,
        }
    )


def run_id_for(key: str) -> str:
    """Deterministic run id, so the same verification writes the same bundle path."""
    return f"run-{key.removeprefix('sha256:')[:16]}"


def run_gate(inputs: GateInputs, *, store: ArtifactStore, created_at: str) -> GateReport:
    """Run §8.2 end to end and return the verdict with its evidence.

    `created_at` is supplied rather than read from a clock: with a fixed timestamp the same
    inputs produce a byte-identical bundle, which is what makes a verification reproducible.
    """
    key = idempotency_key(inputs)
    run_id = run_id_for(key)
    product_ref = inputs.product.self_ref()
    writer = BundleWriter(
        store=store,
        run_id=run_id,
        product_ref=product_ref,
        verifier_version=VERIFIER_VERSION,
        created_at=created_at,
    )
    checks: list[CheckResult] = []

    # 1. validate input contracts
    checks.append(_check_contracts(writer, inputs, product_ref))

    # 2-3. freeze snapshots and reject blocking semantic gaps
    gaps_check, untrustworthy = _check_gaps(writer, inputs, product_ref)
    checks.append(gaps_check)

    # 4-5. semantic diff, product impact, verified-asset invalidation
    if untrustworthy:
        reason = (
            "a metric contract did not load, so the snapshot does not represent its source; "
            "a diff would report the parse failure as a removed metric"
        )
        checks.extend(
            _skipped(check_id, category, product_ref, reason)
            for check_id, category in (
                ("semantic.diff", CheckCategory.SEMANTIC),
                ("impact.products", CheckCategory.IMPACT),
                ("assets.invalidated", CheckCategory.VERIFICATION),
                ("results.reconcile", CheckCategory.RESULT),
            )
        )
    else:
        diff = diff_snapshots(inputs.base, inputs.candidate)
        checks.append(_check_diff(writer, diff, product_ref))
        impact = resolve_impact(diff, [inputs.product])
        checks.append(_check_impact(writer, impact, product_ref))
        checks.append(_check_assets(writer, inputs, diff, product_ref))
        checks.append(_check_results(writer, inputs, product_ref))

    # 6-7. compile and execute SQL — no offline implementation yet, so say so
    checks.append(
        _not_implemented(
            "sql.compile",
            CheckCategory.EXECUTION,
            product_ref,
            "base/candidate SQL is not compiled by this gate yet (§8.2 step 6)",
        )
    )
    checks.append(
        _not_implemented(
            "execution.checks",
            CheckCategory.EXECUTION,
            product_ref,
            "security, access, freshness and physical checks need a DWH (§8.2 step 7)",
        )
    )

    # 9. BI compile contracts — payloads only, never a remote create
    checks.append(_check_bi_contracts(writer, inputs, product_ref))

    # 10. assemble the bundle, then re-verify what was written
    written = writer.write()
    problems = verify_bundle(store, run_id)
    checks.append(
        CheckResult(
            check_id="evidence.bundle",
            check_version=CHECK_VERSION,
            category=CheckCategory.EVIDENCE,
            subject_refs=[product_ref],
            status=status_for([problem.severity for problem in problems]),
            summary_code="evidence.verified" if not problems else "evidence.invalid",
            detail=(
                f"bundle {written.digest} re-verified: "
                f"{len(written.bundle.artifacts)} artifact(s), {len(problems)} problem(s)"
            ),
        )
    )

    severities = [severity for check in checks if (severity := check.severity()) is not None]
    return GateReport(
        run_id=run_id,
        product_ref=product_ref,
        idempotency_key=key,
        base_digest=inputs.base.canonical_digest,
        candidate_digest=inputs.candidate.canonical_digest,
        operational_status=RunStatus.COMPLETED,
        verdict=verdict_for(severities),
        checks=checks,
        evidence_digest=written.digest,
        created_at=created_at,
    )


# --- individual steps ---------------------------------------------------------


def _check_contracts(writer: BundleWriter, inputs: GateInputs, product_ref: str) -> CheckResult:
    report = inputs.validation
    ref = writer.add_check("contracts.validate", report.model_dump(mode="json"))
    return CheckResult(
        check_id="contracts.validate",
        check_version=CHECK_VERSION,
        category=CheckCategory.CONTRACT,
        subject_refs=[product_ref, *inputs.product.scope.metrics],
        status=status_for([issue.severity for issue in report.issues]),
        summary_code="contracts.clean" if not report.issues else "contracts.issues",
        detail=(
            f"{len(report.metrics)} metric contract(s) resolved, {len(report.issues)} issue(s)"
        ),
        evidence_refs=[ref],
    )


def _check_gaps(
    writer: BundleWriter, inputs: GateInputs, product_ref: str
) -> tuple[CheckResult, list[SemanticGap]]:
    """Freeze both snapshots into evidence and grade what they could not represent."""
    for name, snapshot in (("base", inputs.base), ("candidate", inputs.candidate)):
        writer.add_blob(
            f"snapshot-{name}",
            kind=ArtifactKind.SEMANTIC_SNAPSHOT,
            payload=snapshot.model_dump(mode="json"),
            media_type="application/json",
        )
    gaps = [*inputs.base.gaps, *inputs.candidate.gaps]
    blocking = [gap for gap in gaps if gap.severity is Severity.BLOCK]
    untrustworthy = [gap for gap in gaps if gap.code in UNTRUSTWORTHY_GAPS]
    ref = writer.add_check(
        "semantic.gaps",
        {
            "base": [gap.model_dump(mode="json") for gap in inputs.base.gaps],
            "candidate": [gap.model_dump(mode="json") for gap in inputs.candidate.gaps],
        },
    )
    return (
        CheckResult(
            check_id="semantic.gaps",
            check_version=CHECK_VERSION,
            category=CheckCategory.SEMANTIC,
            subject_refs=[product_ref],
            status=status_for([gap.severity for gap in gaps]),
            summary_code="semantic.representable" if not gaps else "semantic.gaps_present",
            detail=(
                f"{len(gaps)} gap(s) across both snapshots, {len(blocking)} blocking"
                if gaps
                else "both snapshots represent their source completely"
            ),
            evidence_refs=[ref],
        ),
        untrustworthy,
    )


def _check_diff(writer: BundleWriter, diff: SemanticDiff, product_ref: str) -> CheckResult:
    ref = writer.add_check("semantic.diff", diff.model_dump(mode="json"))
    return CheckResult(
        check_id="semantic.diff",
        check_version=CHECK_VERSION,
        category=CheckCategory.SEMANTIC,
        subject_refs=[product_ref],
        status=status_for([change.severity for change in diff.changes]),
        summary_code="semantic.unchanged" if not diff.changes else "semantic.changed",
        detail=f"{len(diff.changes)} semantic change(s) between the snapshots",
        evidence_refs=[ref],
    )


def _check_impact(writer: BundleWriter, impact: ImpactReport, product_ref: str) -> CheckResult:
    ref = writer.add_check("impact.products", impact.model_dump(mode="json"))
    escalated = [
        item
        for item in impact.impacts
        if item.severity is Severity.BLOCK and item.classified_severity is Severity.REVIEW
    ]
    return CheckResult(
        check_id="impact.products",
        check_version=CHECK_VERSION,
        category=CheckCategory.IMPACT,
        subject_refs=[product_ref],
        status=status_for([item.severity for item in impact.impacts]),
        summary_code="impact.none" if not impact.impacts else "impact.reaches_product",
        detail=(
            f"{len(impact.impacts)} change(s) reach this product; "
            f"{len(escalated)} escalated from review to block because it is governed"
        ),
        evidence_refs=[ref],
    )


def _check_assets(
    writer: BundleWriter, inputs: GateInputs, diff: SemanticDiff, product_ref: str
) -> CheckResult:
    invalidations = inputs.assets.invalidate(diff)
    ref = writer.add_check(
        "assets.invalidated",
        {
            "assets": [item.self_ref() for item in inputs.assets.assets],
            "invalidations": [item.model_dump(mode="json") for item in invalidations],
        },
    )
    return CheckResult(
        check_id="assets.invalidated",
        check_version=CHECK_VERSION,
        category=CheckCategory.VERIFICATION,
        subject_refs=[product_ref, *(item.self_ref() for item in inputs.assets.assets)],
        status=status_for([item.severity for item in invalidations]),
        summary_code="assets.valid" if not invalidations else "assets.invalidated",
        detail=(
            f"{len(invalidations)} of {len(inputs.assets.assets)} verified asset(s) "
            "can no longer stand as evidence"
        ),
        evidence_refs=[ref],
    )


def _check_results(writer: BundleWriter, inputs: GateInputs, product_ref: str) -> CheckResult:
    """Reconcile every asset that declares an expected result (§8.4).

    An asset the source has no answer for is `insufficient`, never a pass: a comparison that
    did not happen is not evidence that nothing moved.
    """
    severities: list[Severity] = []
    comparisons: list[dict[str, Any]] = []
    equivalent: list[VerifiedAsset] = []

    for asset in inputs.assets.assets:
        if asset.expectation.kind is not ExpectationKind.RESULT:
            continue
        expected = asset.expectation.result
        assert expected is not None  # AssetExpectation validates this pairing
        candidate = inputs.results.result_for(asset)
        if candidate is None:
            severities.append(Severity.REVIEW)
            comparisons.append(
                {
                    "asset": asset.self_ref(),
                    "outcome": ComparisonOutcome.INSUFFICIENT.value,
                    "differences": [
                        {
                            "code": "result.unavailable",
                            "detail": "no candidate result was supplied for this asset",
                        }
                    ],
                }
            )
            continue
        report: ComparisonReport = compare_results(expected, candidate, asset.tolerance)
        comparisons.append({"asset": asset.self_ref(), **report.model_dump(mode="json")})
        if report.outcome is ComparisonOutcome.DIFFERENT:
            severities.append(Severity.BLOCK)
        elif report.outcome is ComparisonOutcome.INSUFFICIENT:
            severities.append(Severity.REVIEW)
        else:
            equivalent.append(asset)

    ref = writer.add_check("results.reconcile", {"comparisons": comparisons})
    for asset in equivalent:
        # A factual claim needs its evidence (invariant 5) — here, the comparison itself.
        writer.add_claim(
            claim_id=f"claim-{_slugify(asset.metadata.id)}",
            claim_type=ClaimType.OBSERVATION,
            summary_code="result.equivalent",
            evidence_refs=[ref],
            caveats=[f"verified against snapshot {asset.semantic_digest}"],
        )
    if not comparisons:
        # Nothing was compared, so nothing was proven. Reporting that as a pass is how a
        # governed product ends up with an evidence bundle that verifies none of its numbers.
        return CheckResult(
            check_id="results.reconcile",
            check_version=CHECK_VERSION,
            category=CheckCategory.RESULT,
            subject_refs=[product_ref],
            status=CheckStatus.ADVISORY,
            summary_code="result.no_expectations",
            detail="no verified asset declares an expected result for this product",
            evidence_refs=[ref],
        )
    return CheckResult(
        check_id="results.reconcile",
        check_version=CHECK_VERSION,
        category=CheckCategory.RESULT,
        subject_refs=[product_ref],
        status=status_for(severities),
        summary_code="result.equivalent" if not severities else "result.not_equivalent",
        detail=f"{len(equivalent)} of {len(comparisons)} expected result(s) reconciled",
        evidence_refs=[ref],
    )


def _check_bi_contracts(writer: BundleWriter, inputs: GateInputs, product_ref: str) -> CheckResult:
    """Compile every delivered dashboard for its target — payloads only (§8.5)."""
    severities: list[Severity] = []
    compiled: list[dict[str, Any]] = []
    missing: list[str] = []

    for delivery in inputs.product.delivery.dashboards:
        spec = inputs.dashboards.get(delivery.ref)
        if spec is None:
            missing.append(delivery.ref)
            severities.append(Severity.REVIEW)
            continue
        contract = _compile(spec, inputs.candidate_model)
        severities.extend(finding.severity for finding in contract.findings)
        compiled.append({"dashboard": delivery.ref, **contract.model_dump(mode="json")})
        _write_bi_payloads(writer, delivery.ref, contract)

    ref = writer.add_check(
        "bi.contract",
        {"compiled": compiled, "missing": missing},
    )
    return CheckResult(
        check_id="bi.contract",
        check_version=CHECK_VERSION,
        category=CheckCategory.DELIVERY,
        subject_refs=[product_ref, *(item.ref for item in inputs.product.delivery.dashboards)],
        status=status_for(severities),
        summary_code="bi.compiled" if not missing else "bi.spec_missing",
        detail=(
            f"{len(compiled)} dashboard(s) compiled without touching the BI"
            + (f"; no spec supplied for {', '.join(missing)}" if missing else "")
        ),
        evidence_refs=[ref],
    )


def _compile(spec: DashboardSpec, model: SemanticModel) -> CompiledBIContract:
    compiler = _COMPILERS[spec.target_bi]()
    return compiler.compile_contract(spec, model)


def _write_bi_payloads(
    writer: BundleWriter, dashboard_ref: str, contract: CompiledBIContract
) -> None:
    """Store each compiled payload under its own content address.

    `allow_float=True`: these are foreign JSON documents (Superset form_data, DataLens
    configs) whose numbers are IEEE-754 by construction — the strict decimal-string rule
    governs our contracts, not theirs.
    """
    prefix = _slugify(dashboard_ref)
    for artifact in contract.artifacts:
        writer.add_blob(
            f"bi-{prefix}-{artifact.artifact_id}",
            kind=ArtifactKind.BI_CONTRACT,
            payload=canonical_bytes(artifact.payload, allow_float=True),
            media_type="application/json",
            classification=EvidenceClassification.INTERNAL,
        )


def _not_implemented(
    check_id: str, category: CheckCategory, product_ref: str, detail: str
) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        check_version=CHECK_VERSION,
        category=category,
        subject_refs=[product_ref],
        status=CheckStatus.ADVISORY,
        summary_code="check.not_implemented",
        detail=detail,
    )


def _skipped(check_id: str, category: CheckCategory, product_ref: str, reason: str) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        check_version=CHECK_VERSION,
        category=category,
        subject_refs=[product_ref],
        status=CheckStatus.REVIEW,
        summary_code="check.not_run",
        detail=reason,
    )


def _slugify(ref: str) -> str:
    """A ref (`decision://sales/weekly_margin@1`) as an artifact-id slug."""
    out = "".join(char if char.isalnum() else "-" for char in ref.lower())
    return "-".join(part for part in out.split("-") if part) or "ref"
