"""Where a candidate number came from (DECISIONOPS_ARCHITECTURE §8.4).

`results.reconcile` answers one question — do the expected and the candidate numbers
agree — and it answers it about whatever result it was handed. That leaves a hole the
comparison itself cannot see: a number captured last month, from another cluster, by a
query nobody reviewed, reconciles exactly as well as the right one. The gate does not
execute the asset's query (§8.2 step 8 takes results from a port), so the number's origin
arrives as a *declaration*; this module is what turns that declaration into something the
run can check rather than assume.

Three of the four things an attestation says are checked against facts the gate already
holds independently, so a wrong one is caught rather than believed:

* the **snapshot** the number was captured against, against the candidate digest this
  release is about — a number computed from other semantics is not evidence about these;
* the **environment**, against the environment ref the verdict is about — the run names
  one only when it has one, and comparing nothing to something would be theatre;
* the **query**, against the digest of the reviewed `reference_query` on the asset itself
  — this is the one that says the number came from the SQL a reviewer signed off, not
  from an ad-hoc rewrite of it.

The fourth — *that the runner actually ran that query* — is not checkable here and is not
claimed to be. An attestation can lie, and nothing short of a signature over the capture
would catch it (§9.3 defers the trust root). What this module removes is the silent case:
an unattested number now says `provenance.unattested` in the bundle instead of passing
through a reconciliation that reads like proof.

**Every finding is `review` or `advisory`, never `block`.** Provenance describes what is
known about a number, and "we cannot show where this came from" is a question for the
owner, not grounds for the gate to declare the release broken — the reconciliation itself
already blocks when numbers actually disagree. Grading it twice would give one fact two
weights in the verdict.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from auto_bi.scalars import Duration, NonBlankText, Sha256Digest, Timestamp, duration_seconds
from auto_bi.severity import Severity
from auto_bi.strict import StrictModel


class ResultOrigin(StrEnum):
    """How the candidate answer was obtained — the part no digest can establish."""

    #: A SQL query executed against the warehouse; the only origin that can carry a query
    #: digest comparable with the asset's reviewed one.
    QUERY_RUN = "query_run"
    #: Read off a BI surface. Proves what the surface showed, not what the mart computed.
    BI_EXPORT = "bi_export"
    #: Typed by a person. Never provenance for a governed number.
    MANUAL = "manual"


class ResultAttestation(StrictModel):
    """What the capturing side says about a candidate result.

    Optional fields are empty rather than absent-by-default-guess: an attestation that does
    not name its snapshot says so, and gets a finding for it, instead of inheriting the
    run's own snapshot and reading as if it had been checked.
    """

    origin: ResultOrigin
    captured_at: Timestamp
    #: The environment the capture ran against, in the same `host:port/database` shape the
    #: run's `environment_ref` uses.
    environment_ref: str = ""
    #: Canonical digest of the semantic snapshot the executed query was compiled from.
    semantic_digest: Sha256Digest | None = None
    #: Content address of the executed SQL — `sha256_digest({"engine": …, "sql": …})`,
    #: the same convention `ReferenceQuery.digest()` uses, so the two are comparable.
    query_digest: Sha256Digest | None = None
    #: The role or actor that ran the capture; recorded, never checked here.
    executed_by: str = ""


class ProvenanceFinding(StrictModel):
    code: str = Field(min_length=1, pattern=r"^provenance\.[a-z0-9_]+$")
    severity: Severity
    subject: NonBlankText
    detail: NonBlankText


def attest(
    *,
    subject: str,
    attestation: ResultAttestation | None,
    reviewed_query_digest: str | None,
    candidate_digest: str,
    environment_ref: str,
    max_age: Duration,
    created_at: Timestamp,
) -> list[ProvenanceFinding]:
    """Grade one candidate result's declared origin; an empty list means it is established.

    *subject* is the asset the result answers, *reviewed_query_digest* the digest of that
    asset's reviewed `reference_query` (``None`` when it declares none). Called only for
    assets a result was actually supplied for: an absent result is already `insufficient`
    in `results.reconcile`, and saying it twice would double its weight in the verdict.
    """
    if attestation is None:
        return [
            _finding(
                "provenance.unattested",
                Severity.REVIEW,
                subject,
                "the candidate result was supplied without an attestation, so nothing "
                "records which query produced it, against which semantics, or where",
            )
        ]

    findings = [
        *_origin_findings(subject, attestation),
        *_snapshot_findings(subject, attestation, candidate_digest),
        *_environment_findings(subject, attestation, environment_ref),
        *_query_findings(subject, attestation, reviewed_query_digest),
        *_timing_findings(subject, attestation, max_age, created_at),
    ]
    return findings


def _origin_findings(subject: str, attestation: ResultAttestation) -> list[ProvenanceFinding]:
    if attestation.origin is ResultOrigin.MANUAL:
        return [
            _finding(
                "provenance.hand_entered",
                Severity.REVIEW,
                subject,
                "the candidate result was entered by hand: it records a person's reading, "
                "which is not evidence that the mart computes this number",
            )
        ]
    if attestation.origin is ResultOrigin.BI_EXPORT:
        return [
            _finding(
                "provenance.surface_export",
                Severity.REVIEW,
                subject,
                "the candidate result was read off a BI surface, so it shows what the "
                "surface displayed — filters, caching and display rounding included — "
                "not what the governed query returns",
            )
        ]
    return []


def _snapshot_findings(
    subject: str, attestation: ResultAttestation, candidate_digest: str
) -> list[ProvenanceFinding]:
    if attestation.semantic_digest is None:
        return [
            _finding(
                "provenance.snapshot_unstated",
                Severity.REVIEW,
                subject,
                "the attestation names no semantic snapshot, so the number cannot be shown "
                f"to describe the candidate ({candidate_digest}) rather than an earlier one",
            )
        ]
    if attestation.semantic_digest != candidate_digest:
        return [
            _finding(
                "provenance.other_snapshot",
                Severity.REVIEW,
                subject,
                f"captured against snapshot {attestation.semantic_digest}, and this release "
                f"is about {candidate_digest}: the number answers other semantics",
            )
        ]
    return []


def _environment_findings(
    subject: str, attestation: ResultAttestation, environment_ref: str
) -> list[ProvenanceFinding]:
    # A run with no environment of its own has nothing to compare against, and the `dwh.*`
    # checks already report that run as unconfigured rather than clean.
    if not environment_ref:
        return []
    if not attestation.environment_ref:
        return [
            _finding(
                "provenance.environment_unstated",
                Severity.REVIEW,
                subject,
                f"this verdict is about {environment_ref}, and the attestation does not say "
                "where the number was captured",
            )
        ]
    if attestation.environment_ref != environment_ref:
        return [
            _finding(
                "provenance.other_environment",
                Severity.REVIEW,
                subject,
                f"captured on {attestation.environment_ref}, and this verdict is about "
                f"{environment_ref}: two environments hold two sets of rows",
            )
        ]
    return []


def _query_findings(
    subject: str, attestation: ResultAttestation, reviewed_query_digest: str | None
) -> list[ProvenanceFinding]:
    if attestation.origin is not ResultOrigin.QUERY_RUN:
        # Nothing was queried, and the origin finding already said so; asking a BI export
        # for a query digest would report one fact twice.
        return []
    if reviewed_query_digest is None:
        return [
            _finding(
                "provenance.query_unreviewed",
                Severity.ADVISORY,
                subject,
                "the asset declares no reference_query, so there is no reviewed SQL for the "
                "executed query to be compared against — the digest proves the capture is "
                "self-consistent and nothing about what it computed",
            )
        ]
    if attestation.query_digest is None:
        return [
            _finding(
                "provenance.query_unstated",
                Severity.REVIEW,
                subject,
                "the asset declares a reviewed reference_query and the attestation names no "
                f"executed query, so the number is not tied to {reviewed_query_digest}",
            )
        ]
    if attestation.query_digest != reviewed_query_digest:
        return [
            _finding(
                "provenance.other_query",
                Severity.REVIEW,
                subject,
                f"executed query {attestation.query_digest} is not the reviewed "
                f"reference_query {reviewed_query_digest}: the number answers a question "
                "no reviewer signed off",
            )
        ]
    return []


def _timing_findings(
    subject: str, attestation: ResultAttestation, max_age: Duration, created_at: Timestamp
) -> list[ProvenanceFinding]:
    age = _age_seconds(attestation.captured_at, created_at)
    if age < 0:
        return [
            _finding(
                "provenance.capture_after_run",
                Severity.REVIEW,
                subject,
                f"captured at {attestation.captured_at}, after the run's own {created_at}: "
                "a reading from the future means the two clocks or the two runs do not "
                "belong together",
            )
        ]
    window = duration_seconds(max_age)
    # A window measured in years or calendar months has no fixed length. `dwh.freshness`
    # already tells the owner that about this same declaration; repeating it here would
    # grade one contract defect twice.
    if window is None or age <= window:
        return []
    return [
        _finding(
            "provenance.stale_capture",
            Severity.REVIEW,
            subject,
            f"captured {_hours(age)} before this run, and the product declares its numbers "
            f"describe at most {max_age}: the reconciliation compares an old reading",
        )
    ]


def _finding(code: str, severity: Severity, subject: str, detail: str) -> ProvenanceFinding:
    return ProvenanceFinding(code=code, severity=severity, subject=subject, detail=detail)


def _age_seconds(captured_at: Timestamp, created_at: Timestamp) -> float:
    """Both are validated RFC 3339 UTC timestamps, so neither parse can fail here."""
    captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    now = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    return (now - captured).total_seconds()


def _hours(seconds: float) -> str:
    hours = seconds / 3600
    return f"{hours:.1f}h" if hours < 48 else f"{hours / 24:.1f}d"


__all__ = [
    "ProvenanceFinding",
    "ResultAttestation",
    "ResultOrigin",
    "attest",
]
