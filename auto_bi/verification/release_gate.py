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
  (`sql_contract.py`). The same applies downstream of a metric contract that failed to load,
  where the diff would call the unparsed contract a *removed metric*, and of a ref holding
  two contracts of one metric id, where it would compare whichever stands last: those checks
  are emitted as `check.not_run` instead of a guess (see `UNTRUSTWORTHY_GAPS` for why those
  are the only gaps that stop the sequence).

The evidence bundle carries every input and finding the verdict was computed from. The
`evidence.bundle` check itself is in the report but not in the bundle — it grades the
bundle's own re-verification, and a check cannot address the artifact it validates.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from pydantic import Field, ValidationError, model_validator

from auto_bi.decision.impact import ImpactReport, ProductImpact, resolve_impact
from auto_bi.decision.spec import DecisionProductSpec
from auto_bi.decision.validate import ValidationIssue, ValidationReport, product_rule_issues
from auto_bi.evidence.canonical import canonical_bytes, digest_bytes, sha256_digest
from auto_bi.evidence.ids import ref_id as _ref_id
from auto_bi.evidence.manifest import BundleWriter, closure_artifact_id, verify_bundle
from auto_bi.evidence.model import ArtifactKind, ClaimType, EvidenceClassification
from auto_bi.evidence.signing import EvidenceSigner
from auto_bi.evidence.store import ArtifactRefusalError, ArtifactStore
from auto_bi.ir.spec import DashboardSpec, TargetBI
from auto_bi.ir.validate import validate_spec
from auto_bi.refs import ContractRef
from auto_bi.scalars import NonBlankText, Sha256Digest, Slug, Timestamp
from auto_bi.semantic.diff import SemanticChange, SemanticDiff, diff_snapshots, metric_changes
from auto_bi.semantic.gateway import (
    MetricNode,
    SemanticGap,
    SemanticSnapshot,
    declared_node,
    metric_node,
)
from auto_bi.semantic.metric_contract import MetricContract
from auto_bi.semantic.metricflow import JsonObject
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
    CostBound,
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
    reading_for,
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
from auto_bi.yaml_document import schema_reason

API_VERSION = "auto-bi.io/v2alpha1"

#: Part of the idempotency key: a different verifier can derive a different verdict or
#: evidence artifact from the same inputs, so its bundles must not be reused (§7.6).
#: What each verifier version changed: docs/specs/evidence-format-versions.md, newest first.
#: A bump adds its entry there and here; here only the newest few stay (at most five,
#: tests/test_evidence_format_versions.py). The newest:
#: 127 — PostgreSQL mart questions preserve schema and table case in relation-name
#: arguments for privileges, ownership, columns and row security. Both engines use the
#: same two-part mart name for presence and COUNT/MAX. Collected facts and
#: resulting reports can change; check grading is unchanged and CHECK_VERSION stays 81.
#: 128 — native source version 4 (ADR 0008, slice S3): the node of a metric whose formula a dbt
#: semantic manifest defines carries its binding and, where the formula resolves from the
#: artifact the snapshot read, the typed formula, which impact and reference SQL then read;
#: without the artifact, or where it does not resolve, the formula stays `external_ref` and
#: the resolution's gaps join `gap.opaque_metric_formula`. Snapshots, the diff and every check
#: over a bound metric can change; a snapshot without one keeps its digest.
#: 129 — a bundle keeps, beside each snapshot, the canonical closure of every dbt metric a
#: binding of that snapshot names a closure digest for (ADR 0008, slice S4), and re-verifying
#: a bundle checks those closures against the bindings: `evidence.closure_missing`,
#: `evidence.closure_mismatch`, `evidence.closure_unbound`, `evidence.closure_unverified`. A run
#: without a bound metric keeps no closure.
#: 130 — a reference query the mart no longer fits is a fact about the mart, not a broken run
#: (ClickHouse, slice S7): where the catalog accounts for the code — a column the query names that
#: `dwh.physical` grades as gone for `47 UNKNOWN_IDENTIFIER`, or as retyped for
#: `43 ILLEGAL_TYPE_OF_ARGUMENT` — EXPLAIN ESTIMATE leaves the plan unpriced, naming those columns,
#: and its exact SELECT is not sent; `497 ACCESS_DENIED` from the exact SELECT is a refusal; a
#: `max()` of an event-time column whose drift accounts for the code leaves the rows counted and
#: undated. The evidence names the code alone, and a `43`/`47` the catalog does not account for
#: still ends the run. `freshness.unknown` over rows with no newest event time says the rows are
#: there. Verifier 129 let those answers end the run with no report and no bundle, so the drift
#: `dwh.physical` reports was never reported; check grading is unchanged and CHECK_VERSION stays 83.
VERIFIER_VERSION = "130"

#: Bumped when a check's meaning changes, so an old evidence bundle is not read as if it
#: had been graded by today's rule. Adding a *new* check is not such a change: no existing
#: `CheckResult` is re-graded by it, and the verifier version is what stops a bundle written
#: before it from being reused as a complete verification.
#: What each check version changed: docs/specs/evidence-format-versions.md, newest first.
#: A bump adds its entry there and here; here only the newest few stay (at most five,
#: tests/test_evidence_format_versions.py). The newest:
#: 81 — row-security findings and policy applicability respect the per-mart owner
#: bypass: an owner without FORCE is neither filtered nor denied all rows by policies.
#: A false row-security switch makes reported policies inapplicable to every session,
#: including classification checks, and suppresses bypass findings. Bypass details name
#: the established exemptions without claiming that consumers receive different rows.
#: 82 — semantic.gaps grades the gaps of a formula a dbt semantic manifest defines (block where
#: the artifact is missing or refused, the metric is missing from it, the pin, the relation or
#: the time contradicts it; review where the match is unproven or the construct unsupported),
#: where rule 81 graded every such formula `gap.opaque_metric_formula` alone; semantic.diff
#: names a changed binding `metric.binding_changed` (review), which reaches the metric's
#: products and verified assets; contracts.validate compares a bound metric's declaration and
#: pin with the validated contract, not the formula resolved from the artifact.
#: 83 — evidence.bundle also checks the closures of dbt formulas a bundle keeps against the
#: bindings of its snapshots (ADR 0008, item 11): a closure missing, mismatched, unbound or
#: left unverified blocks, where rule 82 checked the presence, hash and size of artifacts.
CHECK_VERSION = "83"

#: Gaps over which a diff would be fiction. A metric contract that failed to parse is simply
#: absent from the candidate graph, so the graph does not represent its source *at all*, and
#: the diff would call that a removed metric. Of a metric two contracts define, the diff would
#: compare the one that stands last in the graph, where a lookup of the metric by its id reads
#: the first. Every other blocking gap (a dangling join, a metric field the model does not declare)
#: describes a truthful graph with a named hole — the diff and the impact over it are still
#: worth reading, and the gap's own `block` severity already stops the release.
UNTRUSTWORTHY_GAPS = frozenset({"gap.contract_invalid", "gap.metric_duplicate"})

#: Of those, the gap that can leave a metric out of the graph: a pin the candidate does not
#: hold is not compared when a contract of the candidate did not load, for that contract may
#: be of the pinned metric. Two contracts of another metric say nothing of this one.
UNLOADED_CONTRACT_GAPS = frozenset({"gap.contract_invalid"})

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
    """Everything §8.1 lists as gate input, already loaded and parsed.

    Every field is part of the run identity (`idempotency_key`) except the two that hold
    what was observed when the run was made, `results` and `environment`, and the closures,
    which the snapshots address by digest.
    """

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
    #: Metric id (without the version) -> the contract the validation report was graded on;
    #: empty means the gate was given nothing to compare a pin's contents with.
    validated_metrics: Mapping[str, MetricContract] = field(default_factory=dict)
    #: dbt ref -> the closure the artifact of each side gave for it (`SnapshotRead.closures`),
    #: kept in evidence beside the snapshot (ADR 0008, item 11). Exactly the refs whose closure
    #: digest the snapshot's bindings carry, each hashing to it; the snapshot addresses them,
    #: so they are no part of the run identity of their own.
    base_closures: Mapping[str, JsonObject] = field(default_factory=dict)
    candidate_closures: Mapping[str, JsonObject] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for side, snapshot, closures in (
            ("base", self.base, self.base_closures),
            ("candidate", self.candidate, self.candidate_closures),
        ):
            bound: dict[str, set[str]] = {}
            for node in snapshot.graph.metrics:
                if node.binding is not None and node.binding.closure_digest is not None:
                    bound.setdefault(node.binding.ref, set()).add(node.binding.closure_digest)
            if set(closures) != set(bound):
                raise ValueError(
                    f"the {side} closures are not those of the refs its snapshot binds: "
                    f"given {sorted(closures)}, bound {sorted(bound)}"
                )
            for ref, closure in closures.items():
                if bound[ref] != {sha256_digest(closure, allow_float=True)}:
                    raise ValueError(f"the {side} closure of {ref!r} is not the one bound")


def identity_text(value: object) -> str:
    """Exact text of a dumped gate input, written for the run identity alone.

    Evidence is stored as RFC 8785, and that form cannot serve here. The contract layer
    refuses a float, an integer past the safe range and a lone surrogate; the foreign layer
    writes two integers past the safe range as one double. The gate grades inputs that hold
    such values — a dashboard filter, a mart's row count, a synonym — so its key must
    neither stop a run on one nor give two different inputs one text. Integers and floats
    are written in hexadecimal, which has a text for every one of them and none in common;
    strings as ASCII literals, one per sequence of code points — a JSON text would spell a
    character outside the BMP as the two surrogates a string can also hold on their own;
    objects by sorted key.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):  # before int: bool is an int subclass
        return "true" if value else "false"
    if isinstance(value, int):
        return hex(value)
    if isinstance(value, float):
        return value.hex()
    if isinstance(value, str):
        return ascii(value)
    if isinstance(value, list):
        return "[" + ",".join(identity_text(item) for item in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("a dumped object has string keys")
        members = (f"{key!a}:{identity_text(value[key])}" for key in sorted(value))
        return "{" + ",".join(members) + "}"
    raise TypeError(f"not a dumped JSON value: {type(value).__name__}")


def _static_inputs(inputs: GateInputs) -> dict[str, str]:
    """Content address of every input a run is handed whole, apart from the product.

    Whole, not the part a check reads: the validation report, both snapshots and every
    payload graded from the rest are sealed, so an input that differs is another bundle.
    """
    dumped: dict[str, object] = {
        "validation": inputs.validation.model_dump(mode="json"),
        "validated_metrics": {
            metric_id: contract.model_dump(mode="json")
            for metric_id, contract in inputs.validated_metrics.items()
        },
        "base": inputs.base.model_dump(mode="json"),
        "candidate": inputs.candidate.model_dump(mode="json"),
        "candidate_model": inputs.candidate_model.model_dump(mode="json"),
        "dashboards": {
            ref: spec.model_dump(mode="json") for ref, spec in inputs.dashboards.items()
        },
        "assets": [asset.model_dump(mode="json") for asset in inputs.assets.assets],
    }
    return {
        name: digest_bytes(identity_text(value).encode("ascii")) for name, value in dumped.items()
    }


def idempotency_key(inputs: GateInputs, *, signer: EvidenceSigner | None = None) -> str:
    """§7.6: every static verification input, evidence-signing identity and verifier version.

    What was observed when the run was made — the recorded results, the engine's readings,
    `created_at` — is left out: keying on it would make every run a new one.
    """
    signing_identity = (
        None if signer is None else {"algorithm": signer.algorithm, "key_id": signer.key_id}
    )
    return sha256_digest(
        {
            "product_digest": inputs.product.digest(),
            "base_digest": inputs.base.canonical_digest,
            "candidate_digest": inputs.candidate.canonical_digest,
            "policy": inputs.product.verification.policy,
            "environment_ref": inputs.environment_ref,
            "evidence_signing": signing_identity,
            "verifier_version": VERIFIER_VERSION,
            "inputs": _static_inputs(inputs),
        }
    )


def run_id_for(key: str) -> str:
    """Deterministic run id, so the same verification writes the same bundle path."""
    return f"run-{key.removeprefix('sha256:')[:16]}"


class EvidenceReadBackError(Exception):
    """The gate wrote the bundle, and the filesystem refused it the read of what it wrote.

    `evidence.bundle` says what a second reading of the bundle found. A reading that was
    refused found nothing: the bundle is not verified and is not shown to be invalid, so
    the run has no report to give. What was written stays where it was written.
    """


def run_gate(
    inputs: GateInputs,
    *,
    store: ArtifactStore,
    created_at: str,
    signer: EvidenceSigner | None = None,
) -> GateReport:
    """Run §8.2 end to end and return the verdict with its evidence.

    `created_at` is supplied rather than read from a clock: with a fixed timestamp the same
    inputs produce a byte-identical bundle, which is what makes a verification reproducible.
    """
    key = idempotency_key(inputs, signer=signer)
    run_id = run_id_for(key)
    product_ref = inputs.product.self_ref()
    writer = BundleWriter(
        store=store,
        run_id=run_id,
        product_ref=product_ref,
        verifier_version=VERIFIER_VERSION,
        created_at=created_at,
        signer=signer,
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
        codes = {gap.code for gap in untrustworthy}
        reasons = []
        if codes & UNLOADED_CONTRACT_GAPS:
            reasons.append(
                "a metric contract did not load, so the snapshot does not represent its source; "
                "a diff would report the parse failure as a removed metric"
            )
        if "gap.metric_duplicate" in codes:
            reasons.append(
                "a semantic ref holds more than one contract of a metric id, so it does not say "
                "which of them a diff should compare"
            )
        reason = "; ".join(reasons)
        checks.extend(
            _skipped(check_id, category, product_ref, reason)
            for check_id, category in (
                ("semantic.diff", CheckCategory.SEMANTIC),
                ("impact.products", CheckCategory.IMPACT),
                ("assets.invalidated", CheckCategory.VERIFICATION),
                ("results.reconcile", CheckCategory.RESULT),
                ("results.provenance", CheckCategory.RESULT),
                # 6. the same reason: a metric whose contract did not parse is absent from
                # the graph, and compiling would report it as a metric with no definition;
                # of a metric two contracts define, it would compile the first.
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
            other_product_issues=_issues_by_product(inputs.validation, product_ref)[1],
        ),
    )
    written = writer.write()
    try:
        problems = verify_bundle(store, run_id, verifier=signer)
    except ArtifactRefusalError as exc:
        raise EvidenceReadBackError(str(exc)) from exc
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
    """Validate the paths and resolve pins by version, then compare their contents.

    Validation reads only the validated paths, not the snapshot. Snapshot lookups used
    elsewhere drop the version and select the first node, so pins must be graded here.
    The base is read only to ask whether it holds the validated definition; it is not graded.
    The product's rules that read a metric contract are answered on the candidate's
    definition of every compared pin as well: the validation report answers them on the
    validated copy alone.
    The report answers for every product of the validated paths, and the run verifies one:
    what the rules found for another product is sealed apart and not graded.
    """
    report = inputs.validation
    own_issues, other_issues = _issues_by_product(report, product_ref)
    candidate_pins, pin_issues, clauses = _candidate_metric_pins(inputs, product_ref)
    rule_issues, rule_clauses = _candidate_rule_issues(inputs)
    issues = [*own_issues, *pin_issues, *rule_issues]
    severities = [issue.severity for issue in issues]
    payload = report.model_dump(mode="json")
    payload["issues"] = [issue.model_dump(mode="json") for issue in issues]
    payload["other_product_issues"] = [issue.model_dump(mode="json") for issue in other_issues]
    payload["verdict"] = verdict_for(severities).value
    payload["candidate_pins"] = candidate_pins
    ref = writer.add_check("contracts.validate", payload)
    matches = sum(pin["outcome"] == "match" for pin in candidate_pins)
    detail = f"{len(report.metrics)} metric contract(s) resolved, {len(issues)} issue(s)"
    if other_issues:
        products = len({issue.source for issue in other_issues})
        detail += f", {len(other_issues)} issue(s) of {products} other product(s) not graded"
    detail += f"; {matches} of {len(candidate_pins)} metric pin(s) match the candidate snapshot"
    if clauses:
        detail += ": " + "; ".join(clauses)
    for clause in rule_clauses:
        detail += "; " + clause
    return CheckResult(
        check_id="contracts.validate",
        check_version=CHECK_VERSION,
        category=CheckCategory.CONTRACT,
        subject_refs=[product_ref, *inputs.product.scope.metrics],
        status=status_for(severities),
        summary_code="contracts.clean" if not issues else "contracts.issues",
        detail=detail,
        evidence_refs=[ref],
    )


def _issues_by_product(
    report: ValidationReport, product_ref: str
) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
    """The report's issues this run grades, and those the rules gave for another product.

    The validation rules answer for one product at a time and write its ref as the source of
    what they find, so an issue whose source is another product the report lists says
    nothing of this one. An issue of loading a file carries the file's path and is graded
    on every run: what an unread file would have held is not known. A report that does not
    list this product was not made for it, and all of it is graded.
    """
    others: set[str] = set()
    if product_ref in report.products:
        others = set(report.products) - {product_ref}
    graded = [issue for issue in report.issues if issue.source not in others]
    set_aside = [issue for issue in report.issues if issue.source in others]
    return graded, set_aside


def _candidate_metric_pins(
    inputs: GateInputs, product_ref: str
) -> tuple[list[dict[str, Any]], list[ValidationIssue], list[str]]:
    pins: list[dict[str, Any]] = []
    issues: list[ValidationIssue] = []
    clauses: list[str] = []
    unloaded = any(gap.code in UNLOADED_CONTRACT_GAPS for gap in inputs.candidate.gaps)
    for ref in inputs.product.scope.metrics:
        parsed = ContractRef.parse(ref)
        metric_id = str(parsed.without_version())
        pinned = parsed.version
        versions = sorted(
            node.version for node in inputs.candidate.graph.metrics if node.id == metric_id
        )
        message = ""
        severity = Severity.BLOCK
        differences: list[SemanticChange] = []
        if len(versions) > 1:
            outcome = "ambiguous"
            held = ", ".join(str(version) for version in versions)
            message = (
                f"the candidate snapshot holds {len(versions)} contracts of {metric_id} "
                f"(versions {held}); the pin to version {pinned} does not resolve to one of them"
            )
            clauses.append(
                f"it holds {len(versions)} contracts of {metric_id} "
                f"(versions {held}), pinned at version {pinned}"
            )
        elif versions:
            if versions[0] == pinned:
                outcome = "match"
                differences = _content_differences(inputs, metric_id, pinned)
                if differences:
                    outcome = "content_mismatch"
                    codes = ", ".join(change.code for change in differences)
                    severity = (
                        Severity.ADVISORY
                        if all(change.severity is Severity.ADVISORY for change in differences)
                        else Severity.BLOCK
                    )
                    message = (
                        f"the candidate snapshot holds another definition of {metric_id} "
                        f"at version {pinned} than the validated contract ({codes}), "
                        "and the base snapshot does not hold the validated definition"
                    )
                    clauses.append(
                        f"it holds another definition of {metric_id} at version {pinned} "
                        f"than the validated contract ({codes})"
                    )
            else:
                outcome = "version_mismatch"
                message = (
                    f"the candidate snapshot holds version {versions[0]} of {metric_id}; "
                    f"the product pins version {pinned}"
                )
                clauses.append(
                    f"it holds version {versions[0]} of {metric_id}, pinned at version {pinned}"
                )
        elif unloaded:
            outcome = "not_compared"
            clauses.append(
                f"{ref} was not compared, because a metric contract of the candidate did not load"
            )
        else:
            outcome = "absent"
            message = (
                f"the candidate snapshot holds no metric {metric_id}; "
                f"the product pins version {pinned}"
            )
            clauses.append(f"it holds no {metric_id}, pinned at version {pinned}")
        pins.append({"ref": ref, "candidate_versions": versions, "outcome": outcome})
        if differences:
            pins[-1]["differences"] = [change.code for change in differences]
        if message:
            issues.append(
                ValidationIssue(
                    code=f"metric_ref.candidate_{outcome}",
                    severity=severity,
                    subject=ref,
                    source=product_ref,
                    message=message,
                )
            )
    return pins, issues, clauses


def _candidate_rule_issues(inputs: GateInputs) -> tuple[list[ValidationIssue], list[str]]:
    """The product's rules on the candidate's definition of each pinned metric.

    Answered for a pin whose contents are compared: one candidate node at the pinned version
    and a validated contract at that version. An issue the rules give on the validated
    definition as well is the validation report's own and is not repeated.
    """
    issues: list[ValidationIssue] = []
    clauses: list[str] = []
    for ref in inputs.product.scope.metrics:
        parsed = ContractRef.parse(ref)
        metric_id = str(parsed.without_version())
        contract = inputs.validated_metrics.get(metric_id)
        nodes = [node for node in inputs.candidate.graph.metrics if node.id == metric_id]
        if contract is None or contract.metadata.version != parsed.version:
            continue
        if len(nodes) != 1 or nodes[0].version != parsed.version:
            continue
        own = product_rule_issues(inputs.product, [_defined_as(contract, metric_node(contract))])
        found = [
            issue.model_copy(
                update={
                    "code": issue.code.replace(".", ".candidate_", 1),
                    "message": f"in the candidate snapshot, {issue.message}",
                }
            )
            for issue in product_rule_issues(inputs.product, [_defined_as(contract, nodes[0])])
            if issue not in own
        ]
        if found:
            codes = ", ".join(dict.fromkeys(issue.code for issue in found))
            clauses.append(
                f"the product's rules give {codes} on the candidate's definition of {ref}"
            )
            issues.extend(found)
    return issues, clauses


def _defined_as(contract: MetricContract, node: MetricNode) -> MetricContract:
    """*contract* with what the product's rules read taken from *node*.

    Governing policies are a set: one set written in another order or with a repeat reads
    the same, so the two definitions of a pin are compared in one form.
    """
    return contract.model_copy(
        update={
            "metadata": contract.metadata.model_copy(update={"status": node.status}),
            "dimensions": list(node.dimensions),
            "policy_refs": sorted(set(node.policy_refs)),
        }
    )


def _content_differences(
    inputs: GateInputs, metric_id: str, pinned: int | None
) -> list[SemanticChange]:
    """What the diff names between the validated contract and the candidate's definition.

    Empty when there is nothing to compare (the validated set holds no contract of this id
    at the pinned version), when the two are one definition, and when the base holds the
    validated definition: the difference is then the change under review, which
    `semantic.diff` names and `impact.products` grades.

    A snapshot's node is read as its contract declares it (`declared_node`): of a metric whose
    formula a dbt semantic manifest defines, the declaration and its pin are compared, not the
    formula resolved from the artifact, which the contract does not write (ADR 0008, item 10).
    """
    contract = inputs.validated_metrics.get(metric_id)
    if contract is None or contract.metadata.version != pinned:
        return []
    validated = metric_node(contract)
    candidate = declared_node(
        next(node for node in inputs.candidate.graph.metrics if node.id == metric_id)
    )
    changes = metric_changes(validated, candidate)
    if not changes:
        return []
    base = [declared_node(node) for node in inputs.base.graph.metrics if node.id == metric_id]
    if len(base) == 1 and not metric_changes(validated, base[0]):
        return []
    return changes


def _check_gaps(
    writer: BundleWriter, inputs: GateInputs, product_ref: str
) -> tuple[CheckResult, list[SemanticGap]]:
    """Freeze both snapshots into evidence and grade what they could not represent.

    Beside each snapshot go the closures its bindings name, in the order of refs: the bytes
    are the RFC 8785 form the closure digest addresses, so each blob's digest is that digest.
    """
    for name, snapshot, closures in (
        ("base", inputs.base, inputs.base_closures),
        ("candidate", inputs.candidate, inputs.candidate_closures),
    ):
        writer.add_blob(
            f"snapshot-{name}",
            kind=ArtifactKind.SEMANTIC_SNAPSHOT,
            payload=snapshot.model_dump(mode="json"),
            media_type="application/json",
        )
        for ref in sorted(closures):
            writer.add_blob(
                closure_artifact_id(name, ref),
                kind=ArtifactKind.FORMULA_CLOSURE,
                payload=canonical_bytes(closures[ref], allow_float=True),
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
    # Bucket by code: `chart.metric_unread` is a withheld relation on a tile that may
    # have been read in whole, `chart.sql_partial` is a partial read, `chart.opaque_sql`
    # is unread SQL. Set-difference of every dashboard-scoped gap from `tiles` called
    # the first two "not resolved at all". Metric-id and dashboard-id gaps stay out.
    # Gap subjects are scoped to dashboards of reaching changes, matching `tiles`.
    reaching_dashboards = {dashboard for item in reaching for dashboard in item.dashboards}

    def _tile_gap_subjects(code: str) -> set[str]:
        return {
            gap.subject
            for gap in impact.chart_gaps
            if gap.code == code
            and any(gap.subject.startswith(f"{dashboard}#") for dashboard in reaching_dashboards)
        }

    partial = _tile_gap_subjects("chart.sql_partial")
    opaque = _tile_gap_subjects("chart.opaque_sql")
    metric_unread = _tile_gap_subjects("chart.metric_unread")
    # The four numbers partition the tiles this product's changes touched: a tile is
    # named, or it is not named and then falls under the worst thing missing from it.
    # Without the third bucket a tile read only in part and named by no impact would be
    # in the summary's gap list and in none of these counts.
    unnamed_opaque = opaque - tiles
    unnamed_partial = partial - tiles - unnamed_opaque
    unnamed_metric = metric_unread - tiles - unnamed_opaque - unnamed_partial
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
            f"{len(tiles)} delivered chart(s) named ({len(tiles & partial)} read only in part); "
            f"of the tiles not named: {len(unnamed_opaque)} whose SQL could not be read, "
            f"{len(unnamed_partial)} read only in part, "
            f"{len(unnamed_metric)} read in whole but not settled against a changed metric"
        ),
        evidence_refs=[ref],
    )


def _check_assets(
    writer: BundleWriter, inputs: GateInputs, diff: SemanticDiff, product_ref: str
) -> CheckResult:
    # Three ways an asset stops being evidence: a semantic change that moved the number
    # under it, a metric pin that does not resolve in the candidate, and a reviewed query
    # that reads a mart the model does not govern
    # (§8.4.1 anchors provenance on that query, so its attachment is worth checking).
    metric_pins, pin_findings = inputs.assets.resolve_metric_pins(inputs.candidate)
    invalidations = [
        *inputs.assets.invalidate(diff),
        *pin_findings,
        *inputs.assets.ungoverned(inputs.candidate),
    ]
    ref = writer.add_check(
        "assets.invalidated",
        {
            "assets": [item.self_ref() for item in inputs.assets.assets],
            "invalidations": [item.model_dump(mode="json") for item in invalidations],
            "metric_pins": metric_pins,
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
            + (
                f"; {len(metric_pins)} of {len(metric_pins)} metric pin(s) "
                "match the candidate snapshot"
                if metric_pins
                else ""
            )
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
            claim_id=_ref_id("claim", asset.metadata.id),
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
    """Validate every delivered spec against the candidate model before compiling (§8.5).

    As the build path does before any BI call (invariant 2), an invalid spec is named,
    not compiled. A spec that validates and that the compile then refuses is named too, with
    what the compile said of it: the compile runs the build path's own builders, so the
    build path would not build that dashboard either. Specs that compile produce payloads
    only.
    """
    severities: list[Severity] = []
    compiled: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    refused: list[dict[str, str]] = []
    missing: list[str] = []

    for delivery in inputs.product.delivery.dashboards:
        spec = inputs.dashboards.get(delivery.ref)
        if spec is None:
            missing.append(delivery.ref)
            severities.append(Severity.REVIEW)
            continue
        errors = validate_spec(spec, inputs.candidate_model)
        if errors:
            invalid.append({"dashboard": delivery.ref, "errors": errors})
            severities.append(Severity.BLOCK)
            continue
        try:
            contract = _compile(spec, inputs.candidate_model)
        except ValueError as exc:
            refused.append({"dashboard": delivery.ref, "refusal": _compile_refusal(exc)})
            severities.append(Severity.BLOCK)
            continue
        severities.extend(finding.severity for finding in contract.findings)
        compiled.append({"dashboard": delivery.ref, **contract.model_dump(mode="json")})
        _write_bi_payloads(writer, delivery.ref, contract)

    ref = writer.add_check(
        "bi.contract",
        {"compiled": compiled, "invalid": invalid, "refused": refused, "missing": missing},
    )
    if invalid:
        summary_code = "bi.spec_invalid"
    elif refused:
        summary_code = "bi.compile_refused"
    elif missing:
        summary_code = "bi.spec_missing"
    else:
        summary_code = "bi.compiled"
    return CheckResult(
        check_id="bi.contract",
        check_version=CHECK_VERSION,
        category=CheckCategory.DELIVERY,
        subject_refs=[product_ref, *(item.ref for item in inputs.product.delivery.dashboards)],
        status=status_for(severities),
        summary_code=summary_code,
        detail=(
            f"{len(compiled)} dashboard(s) compiled without touching the BI"
            + (
                "; not compiled, the spec does not validate against the candidate semantic model: "
                + ", ".join(item["dashboard"] for item in invalid)
                if invalid
                else ""
            )
            + (
                "; not compiled, the compiler refuses the spec: "
                + ", ".join(item["dashboard"] for item in refused)
                if refused
                else ""
            )
            + (f"; no spec supplied for {', '.join(missing)}" if missing else "")
        ),
        evidence_refs=[ref],
    )


def _compile(spec: DashboardSpec, model: SemanticModel) -> CompiledBIContract:
    compiler = _COMPILERS[spec.target_bi]()
    return compiler.compile_contract(spec, model)


def _compile_refusal(exc: ValueError) -> str:
    """What the compile said of a spec it does not compile, in the one line the check keeps.

    The class of what was raised, then its words with each run of whitespace as one space;
    the class alone when it said nothing. Of a model of the contract that does not take what
    the compile hands it, the words are the model and, for its first refusals, where and why
    — the manner a schema's refusal of a run input is said in, without the value.
    """
    if isinstance(exc, ValidationError):
        said = f"{exc.title}: {schema_reason(exc)}"
    else:
        said = " ".join(str(exc).split())
    name = type(exc).__name__
    return f"{name}: {said}" if said else name


def _write_bi_payloads(
    writer: BundleWriter, dashboard_ref: str, contract: CompiledBIContract
) -> None:
    """Store each compiled payload under its own content address.

    `allow_float=True`: these are foreign JSON documents (Superset form_data, DataLens
    configs) whose numbers are IEEE-754 by construction — the strict decimal-string rule
    governs our contracts, not theirs.
    """
    for artifact in contract.artifacts:
        writer.add_blob(
            _ref_id("bi", dashboard_ref, artifact.artifact_id),
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
                _freshness_clean(questions, facts, inputs.product.verification.freshness_max_age)
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
                "physical.as_declared"
                if any(_physical_coverage(questions, facts)[:2])
                else "physical.not_run"
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
                _security_clean(questions, facts)
                if _readable(questions, facts)
                else f"no in-scope mart could be read on {facts.target}, so nothing was asked "
                "about what narrows it"
            ),
            summary_clean=(
                "security.unfiltered"
                if any(_security_coverage(questions, facts)[:2])
                else "security.not_run"
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
    """Whether any plan or exact runtime query was checked against a declared ceiling.

    A product whose metrics declare no budget, and one whose every estimate came back
    silent, must not read the same as one the engine priced — the first has nothing to check,
    and the second is the sentence this report must never contain.
    """
    for question in questions.plans:
        table = reading_for(question, questions, facts.tables)
        if (
            table is None
            or not table.exists
            or not table.readable
            or denies_all_rows(table, facts.role)
            or not question.sql
        ):
            continue
        plan = facts.plan(question.metric_ref)
        if (
            question.budgets.max_scanned_rows is not None
            and plan is not None
            and plan.scanned_rows is not None
        ):
            return True
        execution = facts.execution(question.metric_ref)
        if execution is None:
            continue
        if question.budgets.max_scanned_bytes is not None and (
            execution.scanned_bytes is not None
            or CostBound.SCANNED_BYTES in execution.checked_bounds
            or execution.exceeded_bound is CostBound.SCANNED_BYTES
        ):
            return True
        if question.budgets.timeout_ms is not None and (
            execution.elapsed_ms is not None
            or CostBound.TIMEOUT in execution.checked_bounds
            or execution.exceeded_bound is CostBound.TIMEOUT
        ):
            return True
    return False


def _cost_clean(questions: EnvironmentQuestions, facts: EnvironmentFacts) -> str:
    if not questions.plans:
        return "no in-scope metric declares a cost ceiling, so no query was priced"
    totals = {"max_scanned_rows": 0, "max_scanned_bytes": 0, "timeout_ms": 0}
    checked = dict.fromkeys(totals, 0)
    for question in questions.plans:
        for name in totals:
            totals[name] += getattr(question.budgets, name) is not None
        table = reading_for(question, questions, facts.tables)
        if (
            table is None
            or not table.exists
            or not table.readable
            or denies_all_rows(table, facts.role)
            or not question.sql
        ):
            continue
        plan = facts.plan(question.metric_ref)
        if question.budgets.max_scanned_rows is not None and plan is not None:
            checked["max_scanned_rows"] += plan.scanned_rows is not None
        execution = facts.execution(question.metric_ref)
        if execution is None:
            continue
        for bound, measured in (
            (CostBound.SCANNED_BYTES, execution.scanned_bytes),
            (CostBound.TIMEOUT, execution.elapsed_ms),
        ):
            if getattr(question.budgets, bound.value) is not None:
                checked[bound.value] += (
                    measured is not None
                    or bound in execution.checked_bounds
                    or execution.exceeded_bound is bound
                )
    coverage = "complete" if checked == totals else "incomplete"
    result = (
        f"cost comparison on {facts.target}: {checked['max_scanned_rows']} of "
        f"{totals['max_scanned_rows']} declared scanned-row ceilings checked from plans; "
        f"{checked['max_scanned_bytes']} of {totals['max_scanned_bytes']} declared scanned-byte "
        f"ceilings and {checked['timeout_ms']} of {totals['timeout_ms']} declared timeout "
        f"ceilings checked from exact-query runtime evidence; coverage is {coverage}"
    )
    if not any(checked.values()):
        return f"{result}; no cost comparison was made"
    return f"{result}; checked bounds are within their declared ceilings"


def _physical_coverage(
    questions: EnvironmentQuestions, facts: EnvironmentFacts
) -> tuple[int, int, int, int]:
    """Return compared column/layout counts, then their declared totals, per question."""
    columns_total = columns_compared = layout_total = layout_compared = 0
    for question in questions.tables:
        columns_total += len(question.columns)
        declared_layout = [
            name
            for name, declared in (
                ("table_engine", question.layout.table_engine),
                ("sorting_key", ", ".join(question.layout.sorting_key)),
                ("partition_key", question.layout.partition_key),
                ("distribution_key", ", ".join(question.layout.distribution_key)),
            )
            if declared
        ]
        layout_total += len(declared_layout)
        table = reading_for(question, questions, facts.tables)
        if table is None or not table.exists or not table.readable:
            continue
        if table.columns is not None:
            columns_compared += sum(
                table.columns.get(column) is not None for column in question.columns
            )
        if table.layout is not None:
            layout_compared += sum(
                getattr(table.layout, name) is not None for name in declared_layout
            )
    return columns_compared, layout_compared, columns_total, layout_total


def _physical_clean(questions: EnvironmentQuestions, facts: EnvironmentFacts) -> str:
    columns_compared, layout_compared, columns_total, layout_total = _physical_coverage(
        questions, facts
    )
    coverage = (
        "complete"
        if columns_compared == columns_total and layout_compared == layout_total
        else "incomplete"
    )
    result = (
        f"physical comparison on {facts.target}: {columns_compared} of {columns_total} "
        f"declared column types and {layout_compared} of {layout_total} declared layout "
        f"fields compared; coverage is {coverage}"
    )
    if not columns_compared and not layout_compared:
        return f"{result}; no physical comparison was made"
    return f"{result}; no differences found in the compared facts"


def _security_coverage(
    questions: EnvironmentQuestions, facts: EnvironmentFacts
) -> tuple[int, int, int, int]:
    """Return confirmed column/policy counts, then their requested totals, per question."""
    columns_total = columns_confirmed = policies_observed = 0
    for question in questions.tables:
        columns_total += len(question.columns)
        table = reading_for(question, questions, facts.tables)
        if table is None or not table.exists or not table.readable:
            continue
        if table.column_reads is not None:
            columns_confirmed += sum(
                table.column_reads.get(column) is True for column in question.columns
            )
        policies_observed += table.row_policies is not None
    return columns_confirmed, policies_observed, columns_total, len(questions.tables)


def _security_clean(questions: EnvironmentQuestions, facts: EnvironmentFacts) -> str:
    """Limit clean evidence to the permissions and policy reports actually observed."""
    columns_confirmed, policies_observed, columns_total, policies_total = _security_coverage(
        questions, facts
    )
    coverage = (
        "complete"
        if columns_confirmed == columns_total and policies_observed == policies_total
        else "incomplete"
    )
    result = (
        f"security observation on {facts.target}: {columns_confirmed} of {columns_total} "
        f"requested column reads confirmed and {policies_observed} of {policies_total} "
        "row-policy questions answered per in-scope mart/event-time question; "
        f"coverage is {coverage}"
    )
    if facts.predates_row_security():
        result += (
            f"; {facts.target} reports server_version_num {facts.server_version_num}, "
            "a kernel without row-level security, so no row-policy catalog was read"
        )
    if not columns_confirmed and not policies_observed:
        return f"{result}; no security observation was made"
    return f"{result}; no restrictions found in the observed facts for {facts.role.name}"


def _readable(questions: EnvironmentQuestions, facts: EnvironmentFacts) -> bool:
    """Whether the probe could actually read any mart the product asks about."""
    return any(
        (table := reading_for(item, questions, facts.tables)) is not None and table.readable
        for item in questions.tables
    )


def _freshness_clean(questions: EnvironmentQuestions, facts: EnvironmentFacts, max_age: str) -> str:
    """Used only without freshness findings, so each eligible question's age was compared."""
    compared = sum(
        (table := reading_for(item, questions, facts.tables)) is not None
        and table.exists
        and table.readable
        and not denies_all_rows(table, facts.role)
        for item in questions.tables
    )
    total = len(questions.tables)
    coverage = "complete" if compared == total else "incomplete"
    result = (
        f"freshness comparison on {facts.target}: {compared} of {total} in-scope "
        f"mart/event-time questions compared; coverage is {coverage}"
    )
    if not compared:
        return f"{result}; no freshness comparison was made"
    return f"{result}; compared ages are inside the {max_age} this product declares"


def _dated(questions: EnvironmentQuestions, facts: EnvironmentFacts) -> bool:
    """Whether any mart could be aged at all.

    A mart row security empties is readable and still says nothing about how fresh its source
    is — `check_security` blocks on that, and `check_freshness` stays quiet so one fact is not
    graded twice. Reusing `_readable` here would print "every mart is inside the window" about
    marts nothing was read from, which is the one sentence this report must never contain.
    """
    return any(
        (table := reading_for(item, questions, facts.tables)) is not None
        and table.exists
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
