"""EvidenceBundle, Claim and EvidenceArtifact (DECISIONOPS_ARCHITECTURE §7.7, §9).

The bundle is the audit surface of a verification run: typed claims, each backed by
content-addressed artifacts. Two rules are enforced at parse time rather than at write
time, because a bundle that fails them is worse than no bundle at all:

* a claim with a dangling ``evidence_ref`` never assembles (invariant 5) — an unsupported
  number in a governed output is exactly the failure this product exists to prevent;
* an artifact path is a location inside the fixed bundle layout (§9.2), built by the store,
  never a caller-chosen filesystem path — path confinement is a security boundary.

``integrity=signed`` stays unreachable without a verified signature (§9.3): a local content
hash is an integrity check, not a signature, and must never be reported as one.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from auto_bi.evidence.canonical import canonical_bytes, digest_bytes
from auto_bi.refs import DecisionRef
from auto_bi.scalars import DecimalString, Sha256Digest, Slug, Timestamp
from auto_bi.strict import StrictModel

API_VERSION = "auto-bi.io/v2alpha1"

# Bundle-relative layout (§9.2). `manifest.json` is the root document, never an artifact.
_CHECKS = "checks/"
_REPORTS = "reports/"
_BLOBS = "blobs/sha256/"
_RESTRICTED = "restricted/"
_ALLOWED_PREFIXES = (_CHECKS, _REPORTS, _BLOBS, _RESTRICTED)


class EvidenceClassification(StrEnum):
    """Sensitivity of an artifact.

    Mirrors ``semantic.model.DataClassification`` on purpose: evidence carries results of
    DWH data, so the two vocabularies must stay comparable for redaction and retention
    rules. A test pins the value sets together — the layers share the words, not the type.
    """

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class ArtifactKind(StrEnum):
    CHECK_RESULT = "check_result"
    QUERY_RESULT = "query_result"
    QUERY_PLAN = "query_plan"
    SEMANTIC_SNAPSHOT = "semantic_snapshot"
    BI_CONTRACT = "bi_contract"
    REPORT = "report"
    RAW_PAYLOAD = "raw_payload"


class ClaimType(StrEnum):
    """Claim taxonomy (invariant 12).

    ``causal`` is listed because the distinction is the point — an observation is not a
    contribution and neither is a cause — but emitting one is refused until a verified
    causal module exists.
    """

    OBSERVATION = "observation"
    CONTRIBUTION = "contribution"
    ASSOCIATION = "association"
    HYPOTHESIS = "hypothesis"
    CAUSAL = "causal"


class ComparisonSemantics(StrEnum):
    ABSOLUTE = "absolute"
    RELATIVE = "relative"
    SHARE = "share"
    RANK = "rank"


class EvidenceIntegrity(StrEnum):
    CONTENT_HASHED = "content_hashed"
    SIGNED = "signed"


class EvidenceSignature(StrictModel):
    """Recorded only after a signature verified — see :class:`EvidenceIntegrity`."""

    algorithm: str = Field(min_length=1)
    key_id: str = Field(min_length=1)
    value: str = Field(min_length=1)
    verified_at: Timestamp


class EvidenceArtifact(StrictModel):
    artifact_id: Slug
    kind: ArtifactKind
    media_type: str = Field(min_length=1)
    digest: Sha256Digest
    size_bytes: int = Field(ge=0)
    classification: EvidenceClassification
    path: str = Field(min_length=1)
    redacted: bool = False

    @model_validator(mode="after")
    def _check_path(self) -> EvidenceArtifact:
        path = self.path
        if "\\" in path or path.startswith("/") or (len(path) > 1 and path[1] == ":"):
            raise ValueError(f"artifact path must be bundle-relative, got {path!r}")
        segments = path.split("/")
        if any(segment in ("", ".", "..") for segment in segments):
            raise ValueError(f"artifact path must not contain empty or relative segments: {path!r}")
        if not path.startswith(_ALLOWED_PREFIXES):
            allowed = ", ".join(_ALLOWED_PREFIXES)
            raise ValueError(f"artifact path {path!r} is outside the bundle layout ({allowed})")
        if path.startswith(_BLOBS):
            expected = self.digest.removeprefix("sha256:")
            if path != f"{_BLOBS}{expected}":
                raise ValueError(f"blob path must address its own digest: expected {expected}")
        under_restricted = path.startswith(_RESTRICTED)
        is_restricted = self.classification is EvidenceClassification.RESTRICTED
        if is_restricted and not under_restricted:
            raise ValueError(f"restricted payload must live under {_RESTRICTED}, got {path!r}")
        if under_restricted and not is_restricted:
            raise ValueError(
                f"{_RESTRICTED} holds restricted payloads only, got {self.classification.value}"
            )
        return self


class Claim(StrictModel):
    claim_id: Slug
    claim_type: ClaimType
    summary_code: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
    value: DecimalString | None = None
    unit: str = ""
    comparison: ComparisonSemantics | None = None
    evidence_refs: list[Slug] = Field(min_length=1)
    caveats: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _refuse_causal(self) -> Claim:
        if self.claim_type is ClaimType.CAUSAL:
            raise ValueError(
                "causal claims are refused until a verified causal module exists (invariant 12)"
            )
        return self


class EvidenceBundle(StrictModel):
    api_version: str = Field(default=API_VERSION, pattern=r"^auto-bi\.io/v2alpha1$")
    kind: str = Field(default="EvidenceBundle", pattern=r"^EvidenceBundle$")
    run_id: Slug
    product_ref: DecisionRef
    created_at: Timestamp
    verifier_version: str = Field(min_length=1)
    integrity: EvidenceIntegrity = EvidenceIntegrity.CONTENT_HASHED
    signature: EvidenceSignature | None = None
    artifacts: list[EvidenceArtifact] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_referential_integrity(self) -> EvidenceBundle:
        known: set[str] = set()
        for artifact in self.artifacts:
            if artifact.artifact_id in known:
                raise ValueError(f"duplicate artifact id: {artifact.artifact_id}")
            known.add(artifact.artifact_id)

        seen_claims: set[str] = set()
        for claim in self.claims:
            if claim.claim_id in seen_claims:
                raise ValueError(f"duplicate claim id: {claim.claim_id}")
            seen_claims.add(claim.claim_id)
            for ref in claim.evidence_refs:
                if ref not in known:
                    raise ValueError(
                        f"claim {claim.claim_id} references missing evidence artifact: {ref}"
                    )

        if self.integrity is EvidenceIntegrity.SIGNED and self.signature is None:
            raise ValueError(
                "integrity=signed requires a verified signature; a content hash is not one"
            )
        if self.integrity is EvidenceIntegrity.CONTENT_HASHED and self.signature is not None:
            raise ValueError("a signature is only recorded together with integrity=signed")
        return self

    def canonical_bytes(self) -> bytes:
        """Canonical manifest bytes — the thing that gets hashed and, later, signed."""
        return canonical_bytes(self.model_dump(mode="json"))

    def digest(self) -> str:
        return digest_bytes(self.canonical_bytes())
