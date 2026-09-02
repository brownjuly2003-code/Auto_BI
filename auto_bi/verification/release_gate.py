"""Analytics Release & Evidence Gate — orchestration only (DECISIONOPS_ARCHITECTURE §8.2).

One decision product, two semantic snapshots, a fixed sequence of typed checks, one
content-addressed evidence bundle, one verdict. Every step is a call into a module that
already exists and is tested on its own; this file decides the order, grades the findings
and writes the evidence. It reads no files and opens no sockets — loading inputs is
`decision/use_cases.py`, and what a live engine had to say arrives as facts a read-only probe
already collected (`environment.py`), never as a connection this module holds.

Two properties this module exists to keep:

* **`operational_status` and `verdict` are different answers** (invariant 8). A run that
  completes and blocks is a working gate; a run that fails is a broken one. They are
  separate fields and a report that mixes them does not validate.
* **A step that did not run says so.** §8.2 step 7 needs a running engine, and all five of
  its checks — access, freshness, the physical layer, security and cost — grade *facts* a
  read-only probe collected outside this module (`environment.py`), so a run without a
  configured engine emits them as `check.not_configured` instead of passing, and a check the
  engine answered nothing for says `not_run` rather than clean. Neither may leave a bundle
  that reads as a full verification. Step 6 compiles the reference query of each
  in-scope metric where the semantics are exact offline and names what it will not guess
  (`sql_contract.py`). The same applies downstream of a metric contract that failed to load:
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

from auto_bi.decision.impact import ImpactReport, ProductImpact, resolve_impact
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
from auto_bi.verification.environment import (
    EnvironmentFacts,
    EnvironmentFinding,
    EnvironmentQuestions,
    check_access,
    check_cost,
    check_freshness,
    check_physical,
    check_security,
    denies_all_rows,
    questions_for,
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
from auto_bi.verification.sql_contract import compile_metric_queries
from auto_bi.verification.summary import SummaryFacts, render_summary

API_VERSION = "auto-bi.io/v2alpha1"

#: Part of the idempotency key: a different verifier can reach a different verdict from the
#: same inputs, so its bundles must not be reused (§7.6).
#: 5 — aggregate arithmetic is exact beyond the Decimal context's 28-digit precision.
VERIFIER_VERSION = "5"

#: Bumped when a check's meaning changes, so an old evidence bundle is not read as if it
#: had been graded by today's rule.
#: 5 — aggregate invariants no longer round intermediate sums, products or ratios.
CHECK_VERSION = "5"

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
    #: What a read-only probe of that environment answered (§8.2 step 7). Absent means the
    #: run had no engine to ask — the checks say so rather than passing. The readings
    #: themselves stay out of the idempotency key: they are observations of a moment, and
    #: keying on them would make every run a new run.
    environment: EnvironmentFacts | None = None


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
    impact: ImpactReport | None = None
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
                # 6. the same reason: a metric whose contract did not parse is absent from
                # the graph, and compiling would report it as a metric with no definition.
                ("sql.compile", CheckCategory.EXECUTION),
            )
        )
    else:
        diff = diff_snapshots(inputs.base, inputs.candidate)
        checks.append(_check_diff(writer, diff, product_ref))
        impact = resolve_impact(diff, [inputs.product])
        checks.append(_check_impact(writer, impact, product_ref))
        checks.append(_check_assets(writer, inputs, diff, product_ref))
        checks.append(_check_results(writer, inputs, product_ref))
        # 6. reference SQL for both snapshots — compiled, never executed (no DWH here)
        checks.append(_check_sql(writer, inputs, product_ref))

    # 7. ask the engine itself: who we are on it, and how old what it holds is
    checks.extend(_check_environment(writer, inputs, product_ref, created_at))

    # 9. BI compile contracts — payloads only, never a remote create
    checks.append(_check_bi_contracts(writer, inputs, product_ref))

    # 10. assemble the bundle, then re-verify what was written. The owner-facing summary is
    # written last and from the graded checks only: it is derived evidence (§9.2), so it can
    # restate findings but must never be the first place one appears.
    writer.add_report(
        "summary.md",
        render_summary(
            SummaryFacts(
                product_ref=product_ref,
                run_id=run_id,
                base_digest=inputs.base.canonical_digest,
                candidate_digest=inputs.candidate.canonical_digest,
                created_at=created_at,
                verifier_version=VERIFIER_VERSION,
                environment_ref=inputs.environment_ref,
            ),
            checks,
            _reaching(impact) if impact is not None else (),
        ),
    )
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


def _reaching(impact: ImpactReport) -> list[ProductImpact]:
    """The changes that reach the product under verification.

    `resolve_impact` keeps every change of the diff, including those no in-scope product
    depends on, so the payload says what it saw. The run verifies one product, so a change
    whose `products` is empty did not reach it — grading it here, or listing it under "whose
    numbers this reaches", would report a change to somebody else's metric as this product's.
    The diff check still carries it: that is the snapshot's finding, not the product's.
    """
    return [item for item in impact.impacts if item.products]


def _check_impact(writer: BundleWriter, impact: ImpactReport, product_ref: str) -> CheckResult:
    ref = writer.add_check("impact.products", impact.model_dump(mode="json"))
    reaching = _reaching(impact)
    escalated = [
        item
        for item in reaching
        if item.severity is Severity.BLOCK and item.classified_severity is Severity.REVIEW
    ]
    return CheckResult(
        check_id="impact.products",
        check_version=CHECK_VERSION,
        category=CheckCategory.IMPACT,
        subject_refs=[product_ref],
        status=status_for([item.severity for item in reaching]),
        summary_code="impact.none" if not reaching else "impact.reaches_product",
        detail=(
            f"{len(reaching)} of {len(impact.impacts)} change(s) reach this product; "
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


def _check_sql(writer: BundleWriter, inputs: GateInputs, product_ref: str) -> CheckResult:
    """§8.2 step 6: the reference query of every in-scope metric, on both snapshots.

    The check reports; it does not grade. *Why* a query moved was already decided by the
    diff and the impact graph, and grading it twice would let one change count twice toward
    a verdict. A moved query is still worth saying out loud on its own: the dialect comes
    from the mart's declared engine, so an engine swap moves the SQL without touching a
    single metric contract — a change nothing else in the sequence would mention.
    """
    refs = list(inputs.product.scope.metrics)
    base = compile_metric_queries(inputs.base, refs)
    candidate = compile_metric_queries(inputs.candidate, refs)
    on_both = base.by_metric().keys() & candidate.by_metric().keys()
    moved = sorted(
        ref for ref in on_both if base.by_metric()[ref].digest != candidate.by_metric()[ref].digest
    )
    findings = [*base.findings, *candidate.findings]

    ref_id = writer.add_check(
        "sql.compile",
        {
            "base": base.model_dump(mode="json"),
            "candidate": candidate.model_dump(mode="json"),
            "moved": moved,
        },
    )
    if not candidate.queries:
        summary_code = "sql.not_compiled"
    elif moved:
        summary_code = "sql.changed"
    elif findings:
        summary_code = "sql.partially_compiled"
    else:
        summary_code = "sql.compiled"
    severities = [finding.severity for finding in findings]
    if moved:
        severities.append(Severity.ADVISORY)
    return CheckResult(
        check_id="sql.compile",
        check_version=CHECK_VERSION,
        category=CheckCategory.EXECUTION,
        subject_refs=[product_ref, *refs],
        status=status_for(severities),
        summary_code=summary_code,
        detail=(
            f"{len(candidate.queries)} of {len(refs)} metric(s) compiled to a reference query; "
            f"{len(moved)} moved between the snapshots, {len(findings)} not compiled offline"
        ),
        evidence_refs=[ref_id],
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


#: Ranking of finding severities, for picking the one a check's summary code names.
_FINDING_RANK = {Severity.ADVISORY: 0, Severity.REVIEW: 1, Severity.BLOCK: 2}


def _check_environment(
    writer: BundleWriter, inputs: GateInputs, product_ref: str, created_at: str
) -> list[CheckResult]:
    """§8.2 step 7: what a read-only look at the engine says about this release.

    The gate grades facts rather than holding the connection (`environment.py` explains why
    each engine is asked its own question). Without a probe there is nothing to grade, and
    all five checks say `check.not_configured` — an answer, not a pass.
    """
    facts = inputs.environment
    if facts is None:
        reason = (
            "no engine was configured for this run, so the release was not verified against "
            "a live environment (§8.2 step 7)"
        )
        return [
            _not_configured("dwh.access", product_ref, reason),
            _not_configured("dwh.freshness", product_ref, reason),
            _not_configured("dwh.physical", product_ref, reason),
            _not_configured("dwh.security", product_ref, reason),
            _not_configured("dwh.cost", product_ref, reason),
        ]

    questions = questions_for(inputs.product, inputs.candidate)
    access = check_access(questions, facts)
    freshness = check_freshness(
        questions,
        facts,
        max_age=inputs.product.verification.freshness_max_age,
        created_at=created_at,
    )
    return [
        _environment_check(
            writer,
            "dwh.access",
            questions,
            facts,
            access,
            product_ref,
            clean=f"{facts.role.name} reads every in-scope mart on {facts.target}, and only reads",
            summary_clean="access.read_only",
        ),
        _environment_check(
            writer,
            "dwh.freshness",
            questions,
            facts,
            freshness,
            product_ref,
            # Nothing readable to date is not a fresh source: `check_access` already blocked
            # on the mart itself, and calling this half clean would read as a second pass.
            clean=(
                f"every in-scope mart on {facts.target} is inside the "
                f"{inputs.product.verification.freshness_max_age} this product declares"
                if _dated(questions, facts)
                else f"no in-scope mart could be dated on {facts.target}, so nothing was aged"
            ),
            summary_clean=("freshness.fresh" if _dated(questions, facts) else "freshness.not_run"),
        ),
        _environment_check(
            writer,
            "dwh.physical",
            questions,
            facts,
            check_physical(questions, facts),
            product_ref,
            clean=(
                _physical_clean(questions, facts)
                if _readable(questions, facts)
                else f"no in-scope mart could be read on {facts.target}, so nothing was compared"
            ),
            summary_clean=(
                "physical.as_declared" if _readable(questions, facts) else "physical.not_run"
            ),
        ),
        _environment_check(
            writer,
            "dwh.security",
            questions,
            facts,
            check_security(questions, facts),
            product_ref,
            clean=(
                f"nothing narrows what {facts.role.name} reads on {facts.target}: no row "
                "policy applies to it and it may read every column the in-scope metrics are "
                "made of"
                if _readable(questions, facts)
                else f"no in-scope mart could be read on {facts.target}, so nothing was asked "
                "about what narrows it"
            ),
            summary_clean=(
                "security.unfiltered" if _readable(questions, facts) else "security.not_run"
            ),
        ),
        _environment_check(
            writer,
            "dwh.cost",
            questions,
            facts,
            check_cost(questions, facts),
            product_ref,
            clean=_cost_clean(questions, facts),
            summary_clean=_cost_summary(questions, facts),
        ),
    ]


def _cost_summary(questions: EnvironmentQuestions, facts: EnvironmentFacts) -> str:
    """Three different clean answers, because they mean three different things: nothing was
    declared to check, nothing could be measured, or the ceilings held."""
    if not questions.plans:
        return "cost.no_budget_declared"
    return "cost.within_budget" if _priced(questions, facts) else "cost.not_run"


def _priced(questions: EnvironmentQuestions, facts: EnvironmentFacts) -> bool:
    """Whether any plan was actually estimated against a declared ceiling.

    A product whose metrics declare no budget, and one whose every estimate came back
    silent, must not read the same as one the engine priced — the first has nothing to
    check, and the second is the sentence this report must never contain.
    """
    return any(
        (plan := facts.plan(question.metric_ref)) is not None and plan.scanned_rows is not None
        for question in questions.plans
        if question.budgets.max_scanned_rows is not None
    )


def _cost_clean(questions: EnvironmentQuestions, facts: EnvironmentFacts) -> str:
    if not questions.plans:
        return "no in-scope metric declares a cost ceiling, so no plan was priced"
    if not _priced(questions, facts):
        return f"no plan could be priced on {facts.target}, so no ceiling was compared"
    return f"every governed query stays inside the ceiling its metric declares on {facts.target}"


def _physical_clean(questions: EnvironmentQuestions, facts: EnvironmentFacts) -> str:
    """Say what was actually compared: an engine that reports no layout was not agreed with."""
    compared = [
        table for item in questions.tables if (table := facts.table(item.table)) is not None
    ]
    columns = f"every column the in-scope metrics read is on {facts.target} with the declared type"
    if any(table.layout is not None for table in compared):
        return f"{columns}, and the layout matches what the model claims"
    return f"{columns}; this engine reports no layout to compare"


def _readable(questions: EnvironmentQuestions, facts: EnvironmentFacts) -> bool:
    """Whether the probe could actually read any mart the product asks about."""
    return any(
        (table := facts.table(item.table)) is not None and table.readable
        for item in questions.tables
    )


def _dated(questions: EnvironmentQuestions, facts: EnvironmentFacts) -> bool:
    """Whether any mart could be aged at all.

    A mart row security empties is readable and still says nothing about how fresh its source
    is — `check_security` blocks on that, and `check_freshness` stays quiet so one fact is not
    graded twice. Reusing `_readable` here would print "every mart is inside the window" about
    marts nothing was read from, which is the one sentence this report must never contain.
    """
    return any(
        (table := facts.table(item.table)) is not None
        and table.readable
        and not denies_all_rows(table, facts.role)
        for item in questions.tables
    )


def _environment_check(
    writer: BundleWriter,
    check_id: str,
    questions: EnvironmentQuestions,
    facts: EnvironmentFacts,
    findings: list[EnvironmentFinding],
    product_ref: str,
    *,
    clean: str,
    summary_clean: str,
) -> CheckResult:
    """One graded look at the environment, with the readings it stood on in the bundle."""
    ref = writer.add_check(
        check_id,
        {
            "questions": questions.model_dump(mode="json"),
            "facts": facts.model_dump(mode="json"),
            "findings": [item.model_dump(mode="json") for item in findings],
        },
    )
    severities = [item.severity for item in findings]
    # The summary code is the machine identity of the outcome, so it names the finding that
    # decided the status — not whichever one happened to be found first.
    worst = max(findings, key=lambda item: _FINDING_RANK[item.severity], default=None)
    summary_code = summary_clean if worst is None else worst.code
    return CheckResult(
        check_id=check_id,
        check_version=CHECK_VERSION,
        category=CheckCategory.EXECUTION,
        subject_refs=[product_ref, *sorted({item.subject for item in findings})],
        status=status_for(severities),
        summary_code=summary_code,
        detail=clean if not findings else "; ".join(item.detail for item in findings),
        evidence_refs=[ref],
    )


def _not_configured(check_id: str, product_ref: str, detail: str) -> CheckResult:
    """A check with an implementation that this run had no engine to point it at."""
    return CheckResult(
        check_id=check_id,
        check_version=CHECK_VERSION,
        category=CheckCategory.EXECUTION,
        subject_refs=[product_ref],
        status=CheckStatus.ADVISORY,
        summary_code="check.not_configured",
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
