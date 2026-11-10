"""EvidenceBundle, Claim and EvidenceArtifact (DECISIONOPS_ARCHITECTURE §7.7, §9).

The bundle is the audit surface of a verification run: typed claims, each backed by
content-addressed artifacts. Two rules are enforced at parse time rather than at write
time, because a bundle that fails them is worse than no bundle at all:

* a claim with a dangling ``evidence_ref`` never assembles (invariant 5) — an unsupported
  number in a governed output is exactly the failure this product exists to prevent;
* an artifact path is a location inside the fixed bundle layout (§9.2), built by the store,
  never a caller-chosen filesystem path — path confinement is a security boundary.

``integrity=signed`` stays unreachable without a signature envelope (§9.3): a local content
hash is an integrity check, not a signature.  Independent authentication happens against
an out-of-band trust root when the bundle is read.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import Field, model_validator

from auto_bi.evidence.canonical import canonical_bytes, digest_bytes
from auto_bi.refs import DecisionRef
from auto_bi.scalars import (
    MAX_SAFE_INT,
    DecimalString,
    NonBlankText,
    Sha256Digest,
    Slug,
    Timestamp,
)
from auto_bi.strict import StrictModel

API_VERSION = "auto-bi.io/v2alpha1"

# Domain separation prevents a valid signature over the same canonical bytes in another
# protocol from being replayed as an Auto_BI evidence-manifest signature.
_SIGNING_DOMAIN = b"auto-bi.io/evidence-manifest/v1\x00"

# Bundle-relative layout (§9.2). `manifest.json` is the root document, never an artifact.
_CHECKS = "checks/"
_REPORTS = "reports/"
_BLOBS = "blobs/sha256/"
_RESTRICTED = "restricted/"
_ALLOWED_PREFIXES = (_CHECKS, _REPORTS, _BLOBS, _RESTRICTED)

# A path segment is whitelisted, not blacklisted: a prefix check alone still admits NUL,
# `name:stream` (an NTFS alternate data stream — invisible in a listing but written),
# trailing dots/spaces (Windows strips them, so two artifacts collide on one file) and
# bidi overrides in a display name.
_SEGMENT_RE = re.compile(r"^(?![.\s])[A-Za-z0-9._-]+(?<![.\s])\Z")
_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)


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
    #: The canonical closure a dbt-bound formula was resolved from (ADR 0008, item 11).
    FORMULA_CLOSURE = "formula_closure"
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
    """Detached signature envelope; ``value`` is excluded from its own signed payload."""

    algorithm: NonBlankText
    key_id: Sha256Digest
    value: NonBlankText
    signed_at: Timestamp


class EvidenceArtifact(StrictModel):
    artifact_id: Slug
    kind: ArtifactKind
    media_type: NonBlankText
    digest: Sha256Digest
    size_bytes: int = Field(ge=0, le=MAX_SAFE_INT)
    classification: EvidenceClassification
    path: str = Field(min_length=1)
    redacted: bool = False

    @model_validator(mode="after")
    def _check_path(self) -> EvidenceArtifact:
        path = self.path
        if "\\" in path or path.startswith("/") or (len(path) > 1 and path[1] == ":"):
            raise ValueError(f"artifact path must be bundle-relative, got {path!r}")
        segments = path.split("/")
        for segment in segments:
            if not _SEGMENT_RE.match(segment):
                raise ValueError(
                    f"artifact path segment {segment!r} is not a plain name "
                    "([A-Za-z0-9._-], no leading/trailing dot or space)"
                )
            if segment.split(".")[0].lower() in _RESERVED_NAMES:
                raise ValueError(f"artifact path segment {segment!r} is a reserved device name")
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

    @model_validator(mode="after")
    def _check_unicode_text(self) -> Claim:
        # JSON escapes can carry lone surrogates through plain str validation, but
        # RFC 8785 cannot represent them. Reject before inspection or signing.
        try:
            self.unit.encode("utf-8")
            for caveat in self.caveats:
                caveat.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("claim text must contain only Unicode scalar values") from None
        return self


class EvidenceBundle(StrictModel):
    api_version: str = Field(default=API_VERSION, pattern=r"^auto-bi\.io/v2alpha1$")
    kind: str = Field(default="EvidenceBundle", pattern=r"^EvidenceBundle$")
    run_id: Slug
    product_ref: DecisionRef
    created_at: Timestamp
    verifier_version: NonBlankText
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
        """Canonical stored manifest bytes, including its signature envelope.

        The bundle is re-validated first. A bundle is *assembled*, not merely parsed:
        `bundle.claims.append(...)` or a field assignment goes around every validator
        above, and hashing an unchecked manifest is exactly how a dangling evidence ref or
        an escaped path would reach a signature.
        """
        payload = self.model_dump(mode="json")
        type(self).model_validate(payload)
        return canonical_bytes(payload)

    def signing_bytes(
        self,
        *,
        algorithm: str | None = None,
        key_id: str | None = None,
        signed_at: str | None = None,
    ) -> bytes:
        """Domain-separated canonical bytes authenticated by a detached signature.

        The signature value itself cannot sign itself.  Its algorithm, key id and timestamp
        remain covered by replacing only ``value`` with the empty-string marker.  A writer
        supplies that metadata while the bundle is still ``content_hashed``; a reader takes
        it from the stored envelope.
        """
        payload = self.model_dump(mode="json")
        type(self).model_validate(payload)
        existing = self.signature
        if existing is not None:
            metadata = (existing.algorithm, existing.key_id, existing.signed_at)
            supplied = (algorithm, key_id, signed_at)
            if any(value is not None for value in supplied) and supplied != metadata:
                raise ValueError("supplied signature metadata contradicts the bundle envelope")
            algorithm, key_id, signed_at = metadata
        if algorithm is None or key_id is None or signed_at is None:
            raise ValueError("signature metadata is required to build signing bytes")
        payload["integrity"] = EvidenceIntegrity.SIGNED.value
        payload["signature"] = {
            "algorithm": algorithm,
            "key_id": key_id,
            "value": "",
            "signed_at": signed_at,
        }
        return _SIGNING_DOMAIN + canonical_bytes(payload)

    def digest(self) -> str:
        return digest_bytes(self.canonical_bytes())
