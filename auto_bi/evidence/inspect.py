"""Reading a written bundle back (DECISIONOPS_ARCHITECTURE §9.3, §12.1).

`manifest.py` assembles evidence and re-verifies what it just wrote. This module is the
other side of that: it opens a bundle nobody here produced — copied out of CI, pulled from
a colleague, sitting in a directory since last month — and answers two questions the owner
of a release actually asks. *Is this still the bundle it says it is?* and *what does it
claim?*

The answer to the first is a verdict, computed from problems exactly the way every other
verdict in the control plane is computed (`verification/model.py`), so a broken bundle
fails a pipeline with the same exit code vocabulary as a blocked release. The verdict is
about integrity only: an intact bundle recording a `block` verdict is a perfectly good
bundle, and this command says so.

Two refusals are deliberate:

* a payload is handed over only if it still hashes to the address the manifest gives it —
  otherwise the bytes on disk are not the evidence anything was decided from;
* a `restricted` payload is never printed, whatever the caller asks (§10.2). The manifest
  names it, carries its digest, size and class, and that is the whole public surface.
"""

from __future__ import annotations

import json

from pydantic import Field, model_validator

from auto_bi.evidence.canonical import digest_bytes
from auto_bi.evidence.manifest import (
    MANIFEST_PATH,
    BundleProblem,
    read_bundle,
    verify_bundle,
    verify_bundle_signature,
)
from auto_bi.evidence.model import (
    API_VERSION,
    EvidenceArtifact,
    EvidenceBundle,
    EvidenceClassification,
    EvidenceIntegrity,
)
from auto_bi.evidence.signing import EvidenceVerifier
from auto_bi.evidence.store import ArtifactStore, ArtifactStoreError
from auto_bi.scalars import Sha256Digest, Slug
from auto_bi.severity import Severity
from auto_bi.strict import StrictModel
from auto_bi.verification.model import Verdict, verdict_for

#: Codes that mean there is no manifest to show — every other problem still leaves a
#: readable bundle beside it, which is what lets a report say *what* was tampered with.
_UNREADABLE = frozenset({"evidence.manifest_missing", "evidence.manifest_invalid"})


class EvidenceReadError(Exception):
    """The bundle could not be opened or the payload could not be trusted (exit 2).

    Distinct from a blocking problem on purpose: a problem is a graded finding *about* a
    bundle, while this says the caller never got one to grade.
    """


class EvidenceDigestError(EvidenceReadError):
    """The caller pinned a different manifest (exit 4, not a reader failure)."""

    code = "evidence.digest_unexpected"

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"{self.code}: {detail}")


class BundleInspection(StrictModel):
    """One bundle as it stands on disk, re-verified."""

    api_version: str = Field(default=API_VERSION, pattern=r"^auto-bi\.io/v2alpha1$")
    kind: str = Field(default="EvidenceInspection", pattern=r"^EvidenceInspection$")
    run_id: Slug
    #: Recomputed from the stored bytes, never copied out of the manifest.
    manifest_digest: Sha256Digest
    verdict: Verdict
    #: True only after the stored signature passed an out-of-band public trust root.
    signature_verified: bool = False
    #: Absent only when the manifest does not parse — then the problems say why.
    bundle: EvidenceBundle | None = None
    problems: list[BundleProblem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_consistency(self) -> BundleInspection:
        expected = verdict_for(problem.severity for problem in self.problems)
        if self.verdict is not expected:
            raise ValueError(
                f"verdict {self.verdict.value} contradicts the problems "
                f"(expected {expected.value})"
            )
        if self.bundle is None and not any(
            problem.severity is Severity.BLOCK for problem in self.problems
        ):
            raise ValueError(
                "an inspection with no bundle must carry the blocking problem that stopped it"
            )
        signature_problem = any(
            problem.code.startswith("evidence.signature_") for problem in self.problems
        )
        expected_signature = bool(
            self.bundle is not None
            and self.bundle.integrity is EvidenceIntegrity.SIGNED
            and not signature_problem
        )
        if self.signature_verified is not expected_signature:
            raise ValueError(
                "signature_verified contradicts the bundle and signature verification problems"
            )
        return self

    def restricted(self) -> list[EvidenceArtifact]:
        """Artifacts the manifest names and this reader will not print (§10.2)."""
        if self.bundle is None:
            return []
        return [
            artifact
            for artifact in self.bundle.artifacts
            if artifact.classification is EvidenceClassification.RESTRICTED
        ]


def inspect_bundle(
    store: ArtifactStore,
    run_id: str,
    *,
    expected_digest: str | None = None,
    verifier: EvidenceVerifier | None = None,
) -> BundleInspection:
    """Re-verify the bundle of *run_id* and report it.

    *expected_digest* is the address the caller was told to expect. Checking it here is the
    point of content addressing: without it, "the bundle verifies" only means the directory
    is internally consistent, not that it is the one the verdict was published under.
    """
    try:
        raw = store.get(run_id, MANIFEST_PATH)
    except ArtifactStoreError as exc:
        raise EvidenceReadError(
            f"run {run_id!r} has no {MANIFEST_PATH}: this is not an evidence bundle"
        ) from exc

    problems = list(verify_bundle(store, run_id, verifier=verifier))
    digest = digest_bytes(raw)
    if expected_digest and expected_digest != digest:
        problems.append(
            BundleProblem(
                code="evidence.digest_unexpected",
                severity=Severity.BLOCK,
                detail=f"manifest hashes to {digest}, the caller expected {expected_digest}",
            )
        )

    unreadable = any(problem.code in _UNREADABLE for problem in problems)
    bundle = None if unreadable else read_bundle(store, run_id)
    signature_verified = bool(
        bundle is not None
        and bundle.integrity is EvidenceIntegrity.SIGNED
        and not any(problem.code.startswith("evidence.signature_") for problem in problems)
    )
    return BundleInspection(
        run_id=run_id,
        manifest_digest=digest,
        verdict=verdict_for(problem.severity for problem in problems),
        signature_verified=signature_verified,
        bundle=bundle,
        problems=problems,
    )


def read_artifact(
    store: ArtifactStore,
    run_id: str,
    artifact_id: str,
    *,
    expected_digest: str | None = None,
    verifier: EvidenceVerifier | None = None,
) -> tuple[EvidenceArtifact, bytes]:
    """One payload out of a bundle — only if it is still what the manifest addresses."""
    try:
        raw = store.get(run_id, MANIFEST_PATH)
    except (ArtifactStoreError, ValueError) as exc:
        raise EvidenceReadError(
            f"run {run_id!r} has no readable {MANIFEST_PATH}, so nothing can be read out of it"
        ) from exc
    manifest_digest = digest_bytes(raw)
    if expected_digest and expected_digest != manifest_digest:
        raise EvidenceDigestError(
            f"manifest hashes to {manifest_digest}, the caller expected {expected_digest}"
        )
    try:
        bundle = EvidenceBundle.model_validate(json.loads(raw.decode("utf-8")))
    except ValueError as exc:
        raise EvidenceReadError(
            f"run {run_id!r} has no readable {MANIFEST_PATH}, so nothing can be read out of it"
        ) from exc

    signature_problem = verify_bundle_signature(bundle, verifier)
    if signature_problem is not None:
        raise EvidenceReadError(signature_problem.detail)

    artifact = next(
        (item for item in bundle.artifacts if item.artifact_id == artifact_id),
        None,
    )
    if artifact is None:
        known = ", ".join(sorted(item.artifact_id for item in bundle.artifacts)) or "none"
        raise EvidenceReadError(
            f"run {run_id!r} has no artifact {artifact_id!r}; it holds: {known}"
        )
    if artifact.classification is EvidenceClassification.RESTRICTED:
        raise EvidenceReadError(
            f"{artifact_id} is classified restricted and is not printed (§10.2); the manifest "
            f"carries its digest, size and class, and the payload stays in {artifact.path}"
        )

    try:
        payload = store.get(run_id, artifact.path)
    except ArtifactStoreError as exc:
        raise EvidenceReadError(f"{artifact_id}: {artifact.path} is not in the store") from exc
    actual = digest_bytes(payload)
    if actual != artifact.digest:
        raise EvidenceReadError(
            f"{artifact_id} does not hash to the address the manifest gives it "
            f"({actual} != {artifact.digest}), so it is not the evidence it claims to be"
        )
    return artifact, payload


__all__ = [
    "BundleInspection",
    "EvidenceDigestError",
    "EvidenceReadError",
    "inspect_bundle",
    "read_artifact",
]
