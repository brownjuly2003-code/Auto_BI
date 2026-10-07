"""Assembling and re-verifying an evidence bundle (ARCHITECTURE §9.2, §9.3).

`BundleWriter` turns typed inputs into the layout of §9.2. Callers name artifacts; the
writer computes digests, decides paths, and refuses to write a bundle that does not
validate — so a claim whose evidence was never added fails *before* anything reaches disk
rather than leaving half a bundle behind.

Two properties the tests pin down:

* deterministic bytes — same inputs, same manifest, same digest, so two runs of one
  verification can be compared by their digests alone (invariant 9);
* data minimization — a restricted payload is written to the restricted store and the
  public manifest carries only its digest, size and classification (§10.2).

`verify_bundle` is the other half. Evidence read back from disk is only evidence if every
artifact is present and still hashes to what the manifest says.
"""

from __future__ import annotations

import json
from typing import Any

from auto_bi.evidence.canonical import canonical_bytes, digest_bytes
from auto_bi.evidence.model import (
    ArtifactKind,
    Claim,
    ClaimType,
    ComparisonSemantics,
    EvidenceArtifact,
    EvidenceBundle,
    EvidenceClassification,
    EvidenceIntegrity,
    EvidenceSignature,
)
from auto_bi.evidence.signing import (
    EvidenceSignatureError,
    EvidenceSigner,
    EvidenceVerifier,
)
from auto_bi.evidence.store import ArtifactRefusalError, ArtifactStore, ArtifactStoreError
from auto_bi.scalars import NonBlankText
from auto_bi.severity import Severity
from auto_bi.strict import StrictModel

MANIFEST_PATH = "manifest.json"

_BLOBS = "blobs/sha256"
_CHECKS = "checks"
_REPORTS = "reports"
_RESTRICTED = "restricted"


class WrittenBundle(StrictModel):
    run_id: NonBlankText
    digest: NonBlankText
    bundle: EvidenceBundle


class BundleProblem(StrictModel):
    code: NonBlankText
    severity: Severity
    detail: NonBlankText


class EvidenceConflictError(Exception):
    """The run id already holds a bundle with a different digest (see `BundleWriter.write`)."""


class BundleWriter:
    """Collect artifacts and claims, then write one bundle atomically enough to trust."""

    def __init__(
        self,
        *,
        store: ArtifactStore,
        run_id: str,
        product_ref: str,
        verifier_version: str,
        created_at: str,
        signer: EvidenceSigner | None = None,
    ) -> None:
        self._store = store
        self._run_id = run_id
        self._product_ref = product_ref
        self._verifier_version = verifier_version
        self._created_at = created_at
        self._signer = signer
        self._artifacts: list[EvidenceArtifact] = []
        self._claims: list[Claim] = []
        self._payloads: dict[str, bytes] = {}

    def add_check(
        self,
        check_id: str,
        payload: dict[str, Any],
        *,
        classification: EvidenceClassification = EvidenceClassification.INTERNAL,
    ) -> str:
        """Store one typed check result under ``checks/`` and return its artifact id."""
        return self._add(
            artifact_id=f"check-{check_id}",
            kind=ArtifactKind.CHECK_RESULT,
            payload=canonical_bytes(payload),
            media_type="application/json",
            classification=classification,
            path=f"{_CHECKS}/check-{check_id}.json",
        )

    def add_blob(
        self,
        artifact_id: str,
        *,
        kind: ArtifactKind | str,
        payload: bytes | dict[str, Any],
        media_type: str,
        classification: EvidenceClassification = EvidenceClassification.INTERNAL,
    ) -> str:
        """Store a content-addressed payload; restricted ones go to the restricted store."""
        raw = payload if isinstance(payload, bytes) else canonical_bytes(payload)
        digest = digest_bytes(raw)
        if classification is EvidenceClassification.RESTRICTED:
            path = f"{_RESTRICTED}/{digest.removeprefix('sha256:')}"
        else:
            path = f"{_BLOBS}/{digest.removeprefix('sha256:')}"
        return self._add(
            artifact_id=artifact_id,
            kind=ArtifactKind(kind),
            payload=raw,
            media_type=media_type,
            classification=classification,
            path=path,
            digest=digest,
        )

    def add_report(self, name: str, text: str) -> str:
        """Derived, human-facing summary — never the source of truth (§9.2)."""
        return self._add(
            artifact_id=f"report-{name.replace('.', '-')}",
            kind=ArtifactKind.REPORT,
            payload=text.encode("utf-8"),
            media_type="text/markdown",
            classification=EvidenceClassification.INTERNAL,
            path=f"{_REPORTS}/{name}",
        )

    def add_claim(
        self,
        *,
        claim_id: str,
        claim_type: ClaimType,
        summary_code: str,
        evidence_refs: list[str],
        value: str | None = None,
        unit: str = "",
        comparison: ComparisonSemantics | None = None,
        caveats: list[str] | None = None,
    ) -> None:
        self._claims.append(
            Claim(
                claim_id=claim_id,
                claim_type=claim_type,
                summary_code=summary_code,
                value=value,
                unit=unit,
                comparison=comparison,
                evidence_refs=evidence_refs,
                caveats=caveats or [],
            )
        )

    def build(self) -> EvidenceBundle:
        """The bundle as it stands — validated, not yet written."""
        bundle = EvidenceBundle(
            run_id=self._run_id,
            product_ref=self._product_ref,
            created_at=self._created_at,
            verifier_version=self._verifier_version,
            artifacts=list(self._artifacts),
            claims=list(self._claims),
        )
        if self._signer is None:
            return bundle

        signature = EvidenceSignature(
            algorithm=self._signer.algorithm,
            key_id=self._signer.key_id,
            value=self._signer.sign(
                bundle.signing_bytes(
                    algorithm=self._signer.algorithm,
                    key_id=self._signer.key_id,
                    signed_at=self._created_at,
                )
            ),
            signed_at=self._created_at,
        )
        payload = bundle.model_dump(mode="json")
        payload.update(
            integrity=EvidenceIntegrity.SIGNED.value,
            signature=signature.model_dump(mode="json"),
        )
        signed = EvidenceBundle.model_validate(payload)
        # `signed` is a claim, not merely an envelope: authenticate it with the public half
        # before allowing those bytes to reach the evidence store.
        self._signer.verify(signed.signing_bytes(), signature)
        return signed

    def write(self) -> WrittenBundle:
        """Validate, then write payloads and the canonical manifest.

        A run id is the idempotency key (§7.6), so the same verification lands on the same
        path and rewriting it is a no-op. A *different* bundle under that id — the same
        inputs with other recorded results, or the same environment read at another moment
        — is refused before a byte is written: a sealed bundle is evidence, and the store
        does not replace evidence silently. Whoever needs both keeps them in separate stores.
        """
        bundle = self.build()
        manifest = bundle.canonical_bytes()  # re-validates; raises before anything is written
        if self._store.exists(self._run_id, MANIFEST_PATH):
            existing = digest_bytes(self._store.get(self._run_id, MANIFEST_PATH))
            if existing != digest_bytes(manifest):
                raise EvidenceConflictError(
                    f"run {self._run_id!r} already holds bundle {existing}; refusing to "
                    f"overwrite it with {digest_bytes(manifest)} "
                    "(same idempotency key, different evidence)"
                )
        for artifact in bundle.artifacts:
            self._store.put(self._run_id, artifact.path, self._payloads[artifact.artifact_id])
        self._store.put(self._run_id, MANIFEST_PATH, manifest)
        return WrittenBundle(run_id=self._run_id, digest=digest_bytes(manifest), bundle=bundle)

    def _add(
        self,
        *,
        artifact_id: str,
        kind: ArtifactKind,
        payload: bytes,
        media_type: str,
        classification: EvidenceClassification,
        path: str,
        digest: str | None = None,
    ) -> str:
        artifact = EvidenceArtifact(
            artifact_id=artifact_id,
            kind=kind,
            media_type=media_type,
            digest=digest or digest_bytes(payload),
            size_bytes=len(payload),
            classification=classification,
            path=path,
        )
        self._artifacts.append(artifact)
        self._payloads[artifact_id] = payload
        return artifact_id


def read_bundle(store: ArtifactStore, run_id: str, *, raw: bytes | None = None) -> EvidenceBundle:
    """Load and validate the manifest of *run_id*."""
    if raw is None:
        raw = store.get(run_id, MANIFEST_PATH)
    return EvidenceBundle.model_validate(json.loads(raw.decode("utf-8")))


def verify_bundle(
    store: ArtifactStore,
    run_id: str,
    *,
    verifier: EvidenceVerifier | None = None,
    raw: bytes | None = None,
) -> list[BundleProblem]:
    """Re-check a written bundle: manifest, presence, and every content address.

    A problem is a finding about the bundle. What the filesystem refuses the store is none:
    a manifest or an artifact that may not be looked at or read is not one that is missing.
    The store's `ArtifactRefusalError` is raised then, and nothing is graded.
    """
    problems: list[BundleProblem] = []
    if raw is None:
        try:
            raw = store.get(run_id, MANIFEST_PATH)
        except ArtifactRefusalError:
            raise
        except ArtifactStoreError:
            return [
                BundleProblem(
                    code="evidence.manifest_missing",
                    severity=Severity.BLOCK,
                    detail=f"run {run_id!r} has no {MANIFEST_PATH}",
                )
            ]

    try:
        bundle = read_bundle(store, run_id, raw=raw)
    except Exception as exc:  # json or schema failure — both mean "not a bundle"
        return [
            BundleProblem(
                code="evidence.manifest_invalid",
                severity=Severity.BLOCK,
                detail=(
                    f"{MANIFEST_PATH} does not parse as an evidence bundle: "
                    f"{type(exc).__name__}"
                ),
            )
        ]

    if bundle.run_id != run_id:
        # Copied evidence stays internally consistent — the manifest is canonical and every
        # artifact still hashes — so only its location lies. Review, not block: the bundle is
        # readable, but nothing here proves it belongs to the run it was filed under.
        problems.append(
            BundleProblem(
                code="evidence.run_id_mismatch",
                severity=Severity.REVIEW,
                detail=f"the manifest names run {bundle.run_id!r}, but it is stored as {run_id!r}",
            )
        )

    if bundle.canonical_bytes() != raw:
        problems.append(
            BundleProblem(
                code="evidence.manifest_not_canonical",
                severity=Severity.BLOCK,
                detail="stored manifest is not the canonical form of its own content",
            )
        )

    signature_problem = verify_bundle_signature(bundle, verifier)
    if signature_problem is not None:
        problems.append(signature_problem)

    for artifact in bundle.artifacts:
        try:
            payload = store.get(run_id, artifact.path)
        except ArtifactRefusalError:
            raise
        except ArtifactStoreError:
            problems.append(
                BundleProblem(
                    code="evidence.artifact_missing",
                    severity=Severity.BLOCK,
                    detail=f"{artifact.artifact_id}: {artifact.path} is not in the store",
                )
            )
            continue
        actual = digest_bytes(payload)
        if actual != artifact.digest:
            problems.append(
                BundleProblem(
                    code="evidence.digest_mismatch",
                    severity=Severity.BLOCK,
                    detail=(
                        f"{artifact.artifact_id}: stored payload hashes to {actual}, "
                        f"the manifest says {artifact.digest}"
                    ),
                )
            )
        elif len(payload) != artifact.size_bytes:  # pragma: no cover - implied by the digest
            problems.append(
                BundleProblem(
                    code="evidence.size_mismatch",
                    severity=Severity.REVIEW,
                    detail=f"{artifact.artifact_id}: size {len(payload)} != {artifact.size_bytes}",
                )
            )
    return problems


def verify_bundle_signature(
    bundle: EvidenceBundle, verifier: EvidenceVerifier | None
) -> BundleProblem | None:
    """Authenticate a signed manifest against an explicitly supplied trust root."""
    if bundle.integrity is EvidenceIntegrity.CONTENT_HASHED:
        return None
    if verifier is None:
        return BundleProblem(
            code="evidence.signature_untrusted",
            severity=Severity.BLOCK,
            detail="signed evidence requires an explicitly configured public trust root",
        )
    signature = bundle.signature
    if signature is None:  # pragma: no cover - enforced by EvidenceBundle
        return BundleProblem(
            code="evidence.signature_invalid",
            severity=Severity.BLOCK,
            detail="signed evidence has no signature envelope",
        )
    try:
        verifier.verify(bundle.signing_bytes(), signature)
    except EvidenceSignatureError as exc:
        return BundleProblem(code=exc.code, severity=Severity.BLOCK, detail=exc.detail)
    return None


__all__ = [
    "MANIFEST_PATH",
    "BundleProblem",
    "BundleWriter",
    "WrittenBundle",
    "read_bundle",
    "verify_bundle",
    "verify_bundle_signature",
]
