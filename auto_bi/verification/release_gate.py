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
* **A supplied number is not the same as a verified one.** Step 8 takes candidate results
  from a port rather than executing them, so the gate grades *what is known about where
  each number came from* as its own check (`provenance.py`). `results.reconcile` says
  whether two numbers agree; `results.provenance` says whether the second one is about this
  release at all. Neither answer stands in for the other.
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
from auto_bi.verification.provenance import (
    ProvenanceFinding,
    ResultAttestation,
    attest,
)
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

#: Part of the idempotency key: a different verifier can derive a different verdict or
#: evidence artifact from the same inputs, so its bundles must not be reused (§7.6).
#: 11 — `assets.invalidated` grades a reviewed query's governance down to the field, so the
#: same inputs can now yield `needs_review` where verifier 10 saw a clean asset.
#: 12 — a bare column the statement alone cannot pin is now attributed through the governed
#: model when exactly one source in its scope declares it. Verifier 11 left every such name
#: unchecked, so the same inputs can now yield `needs_review` where it saw only an advisory
#: — and, where the name is declared, a clean asset where it flagged a partial check.
VERIFIER_VERSION = "12"

#: Bumped when a check's meaning changes, so an old evidence bundle is not read as if it
#: had been graded by today's rule. Adding a *new* check is not such a change: no existing
#: `CheckResult` is re-graded by it, and the verifier version is what stops a bundle written
#: before it from being reused as a complete verification.
#: 7 — `assets.invalidated` answers a wider question: an asset stops being evidence not
#: only when semantics moved the number under it, but also when its reviewed query reads a
#: mart the model does not govern or a column it does not declare. An earlier `pass` on
#: that check did not ask either question.
#: 8 — `assets.invalidated` asks that field question of more of the query: a bare column the
#: SQL alone cannot attribute is now graded whenever the governed model leaves exactly one
#: source in its scope that declares it. A `pass` under rule 7 only means no column the SQL
#: itself could attribute was undeclared, and an advisory under it may cover a name rule 8
#: grades outright.
CHECK_VERSION = "8"

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


#: Severity order, for picking the finding that decided a check's status. A summary code is
#: the machine identity of an outcome, so it must name that finding rather than whichever one
#: was found first.
_FINDING_RANK = {Severity.ADVISORY: 0, Severity.REVIEW: 1, Severity.BLOCK: 2}


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


class RecordedResult(StrictModel):
    """One candidate answer, with what the capturing side says about where it came from.

    The attestation is optional because a result file written before provenance existed is
    still a usable input — it is graded as `provenance.unattested` rather than rejected. It
    is not optional in the sense of "unimportant": an unattested number is exactly the case
    §8.4's reconciliation cannot see through.
    """

    result: ResultSet
    attestation: ResultAttestation | None = None


class ResultSource(Protocol):
    """Where the gate gets the candidate answer to a verified question (§8.2 step 8).

    A live implementation would compile the asset's plan and execute it under the SQL guard.
    The offline gate is handed recorded results instead, which is what lets the whole gate
    run on fixtures in CI — and what keeps `verify` from ever reaching the DWH. Because the
    number arrives rather than being computed here, what it comes with — its attestation —
    is part of the port, not a decoration on it.
    """

    def result_for(self, asset: VerifiedAsset) -> RecordedResult | None: ...


class RecordedResults:
    """Candidate results captured outside the gate, keyed by verified-asset id."""

    def __init__(self, by_asset: Mapping[str, RecordedResult]) -> None:
        self._by_asset = dict(by_asset)

    def result_for(self, asset: VerifiedAsset) -> RecordedResult | None:
        return self._by_asset.get(asset.metadata.id)


class NoResults:
    """No candidate answers available — every comparison is honestly `insufficient`."""

    def result_for(self, asset: VerifiedAsset) -> RecordedResult | None:
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
                ("results.provenance", CheckCategory.RESULT),
                # 6. the same reason: a metric whose contract did not parse is absent from
                # the graph, and compiling would report it as a metric with no definition.
                ("sql.compile", CheckCategory.EXECUTION),
            )
        )
    else:
        diff = diff_snapshots(inputs.base, inputs.candidate)
        checks.append(_check_diff(writer, diff, product_ref))
        impact = resolve_impact(
            diff,
            [inputs.product],
            dashboards=inputs.dashboards,
            # Both sides: the tile that computed the old definition is the one an owner
            # must look at when the definition moves (`decision/impact.py`).
            metrics=[*inputs.base.graph.metrics, *inputs.candidate.graph.metrics],
        )
        checks.append(_check_impact(writer, impact, product_ref))
        checks.append(_check_assets(writer, inputs, diff, product_ref))
        # 8. the numbers themselves, and where they came from. Provenance is graded first
        # only so the reconciliation's claims can cite its artifact; the report keeps the
        # order an owner reads them in — what was compared, then what is known about it.
        findings = _grade_provenance(inputs, created_at)
        provenance, provenance_ref = _check_provenance(writer, inputs, product_ref, findings)
        checks.append(_check_results(writer, inputs, product_ref, findings, provenance_ref))
        checks.append(provenance)
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
            impact.chart_gaps if impact is not None else (),
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
    # Counted over the reaching changes only, for the same reason the section is: a tile
    # named under somebody else's metric is in the payload, not in this product's count.
    tiles = {chart.ref for item in reaching for chart in item.charts}
    return CheckResult(
        check_id="impact.products",
        check_version=CHECK_VERSION,
        category=CheckCategory.IMPACT,
        subject_refs=[product_ref],
        status=status_for([item.severity for item in reaching]),
        summary_code="impact.none" if not reaching else "impact.reaches_product",
        detail=(
            f"{len(reaching)} of {len(impact.impacts)} change(s) reach this product; "
            f"{len(escalated)} escalated from review to block because it is governed; "
            f"{len(tiles)} delivered chart(s) named, {len(impact.chart_gaps)} the IR "
            "could not resolve"
        ),
        evidence_refs=[ref],
    )


def _check_assets(
    writer: BundleWriter, inputs: GateInputs, diff: SemanticDiff, product_ref: str
) -> CheckResult:
    # Two ways an asset stops being evidence, one question: a semantic change that moved
    # the number under it, and a reviewed query that reads a mart the model does not govern
    # (§8.4.1 anchors provenance on that query, so its attachment is worth checking).
    invalidations = [
        *inputs.assets.invalidate(diff),
        *inputs.assets.ungoverned(inputs.candidate),
    ]
    ref = writer.add_check(
        "assets.invalidated",
        {
            "assets": [item.self_ref() for item in inputs.assets.assets],
            "invalidations": [item.model_dump(mode="json") for item in invalidations],
        },
    )
    worst = max(invalidations, key=lambda item: _FINDING_RANK[item.severity], default=None)
    return CheckResult(
        check_id="assets.invalidated",
        check_version=CHECK_VERSION,
        category=CheckCategory.VERIFICATION,
        subject_refs=[product_ref, *(item.self_ref() for item in inputs.assets.assets)],
        status=status_for([item.severity for item in invalidations]),
        # Same convention as `results.provenance` and the environment checks: the summary
        # code names the reason that decided the status, and the detail is the reason
        # itself. A count cannot carry that here — a moved formula and a query over an
        # ungoverned mart are the same number of invalidated assets and two different fixes.
        summary_code="assets.valid" if worst is None else worst.code,
        detail=(
            f"{len(inputs.assets.assets)} verified asset(s) still stand as evidence"
            if worst is None
            else "; ".join(f"{item.asset}: {item.detail}" for item in invalidations)
        ),
        evidence_refs=[ref],
    )


def _grade_provenance(inputs: GateInputs, created_at: str) -> dict[str, list[ProvenanceFinding]]:
    """What is known about where each supplied candidate number came from (§8.4).

    Keyed by asset id, and only for assets a result was actually supplied for. An asset with
    no candidate answer is already `insufficient` in the reconciliation, and asking where a
    number that does not exist came from would let one missing result weigh twice.
    """
    graded: dict[str, list[ProvenanceFinding]] = {}
    for asset in inputs.assets.assets:
        if asset.expectation.kind is not ExpectationKind.RESULT:
            continue
        recorded = inputs.results.result_for(asset)
        if recorded is None:
            continue
        query = asset.reference_query
        graded[asset.metadata.id] = attest(
            subject=asset.self_ref(),
            attestation=recorded.attestation,
            reviewed_query_digest=query.digest() if query is not None else None,
            candidate_digest=inputs.candidate.canonical_digest,
            environment_ref=inputs.environment_ref,
            max_age=inputs.product.verification.freshness_max_age,
            created_at=created_at,
        )
    return graded


def _check_provenance(
    writer: BundleWriter,
    inputs: GateInputs,
    product_ref: str,
    graded: Mapping[str, list[ProvenanceFinding]],
) -> tuple[CheckResult, str]:
    """§8.4: can this run show where each candidate number came from?

    Separate from `results.reconcile` because it answers a different question. The
    reconciliation asks whether two numbers agree; this asks whether the second number is
    about this release at all — the same semantics, the same environment, the reviewed
    query. A result captured last month on another cluster reconciles perfectly and proves
    nothing, and only one of the two checks can see that.

    The attestation itself is evidence and goes into the bundle whether or not it is clean:
    a run that could not establish provenance must record what it was told, not only that it
    was unconvinced.
    """
    subjects: list[str] = []
    payload: list[dict[str, Any]] = []
    found: list[ProvenanceFinding] = []
    for asset in inputs.assets.assets:
        findings = graded.get(asset.metadata.id)
        if findings is None:
            continue
        recorded = inputs.results.result_for(asset)
        assert recorded is not None  # `graded` holds exactly the assets with a result
        query = asset.reference_query
        subjects.append(asset.self_ref())
        found.extend(findings)
        payload.append(
            {
                "asset": asset.self_ref(),
                "attestation": (
                    recorded.attestation.model_dump(mode="json")
                    if recorded.attestation is not None
                    else None
                ),
                "reviewed_query_digest": query.digest() if query is not None else None,
                "findings": [finding.model_dump(mode="json") for finding in findings],
            }
        )

    ref = writer.add_check("results.provenance", {"results": payload})
    if not payload:
        return (
            CheckResult(
                check_id="results.provenance",
                check_version=CHECK_VERSION,
                category=CheckCategory.RESULT,
                subject_refs=[product_ref],
                status=CheckStatus.ADVISORY,
                summary_code="provenance.no_results",
                detail="no candidate result was supplied, so there is no origin to establish",
                evidence_refs=[ref],
            ),
            ref,
        )
    established = sum(1 for item in payload if not item["findings"])
    # Same convention as the environment checks: the summary code names the finding that
    # decided the status, and the detail is what an owner has to act on — not a count that
    # sends them into the artifact to learn which of a dozen things went wrong.
    worst = max(found, key=lambda finding: _FINDING_RANK[finding.severity], default=None)
    return (
        CheckResult(
            check_id="results.provenance",
            check_version=CHECK_VERSION,
            category=CheckCategory.RESULT,
            subject_refs=[product_ref, *subjects],
            status=status_for([finding.severity for finding in found]),
            summary_code="provenance.established" if worst is None else worst.code,
            detail=(
                f"{established} of {len(payload)} candidate result(s) traced to the reviewed "
                "query, snapshot and environment of this run"
                if worst is None
                else "; ".join(f"{finding.subject}: {finding.detail}" for finding in found)
            ),
            evidence_refs=[ref],
        ),
        ref,
    )


def _check_results(
    writer: BundleWriter,
    inputs: GateInputs,
    product_ref: str,
    graded: Mapping[str, list[ProvenanceFinding]],
    provenance_ref: str,
) -> CheckResult:
    """Reconcile every asset that declares an expected result (§8.4).

    An asset the source has no answer for is `insufficient`, never a pass: a comparison that
    did not happen is not evidence that nothing moved.

    An equivalent comparison emits a claim, and the claim carries the provenance findings as
    caveats. Grading stays with `results.provenance` — the same fact must not weigh twice in
    the verdict — but a `result.equivalent` observation read on its own, out of the bundle,
    must not be able to hide that nobody could say where its number came from (invariant 5).
    """
    severities: list[Severity] = []
    comparisons: list[dict[str, Any]] = []
    equivalent: list[VerifiedAsset] = []

    for asset in inputs.assets.assets:
        if asset.expectation.kind is not ExpectationKind.RESULT:
            continue
        expected = asset.expectation.result
        assert expected is not None  # AssetExpectation validates this pairing
        recorded = inputs.results.result_for(asset)
        candidate = recorded.result if recorded is not None else None
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
        # A factual claim needs its evidence (invariant 5) — here, the comparison itself and
        # the attestation of the number it compared.
        writer.add_claim(
            claim_id=f"claim-{_slugify(asset.metadata.id)}",
            claim_type=ClaimType.OBSERVATION,
            summary_code="result.equivalent",
            evidence_refs=[ref, provenance_ref],
            caveats=[
                f"verified against snapshot {asset.semantic_digest}",
                *(
                    f"{finding.code}: {finding.detail}"
                    for finding in graded.get(asset.metadata.id, ())
                ),
            ],
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
