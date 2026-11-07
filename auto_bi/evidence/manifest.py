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
artifact is present and still hashes to what the manifest says — and if the closures of dbt
formulas it keeps are the ones its snapshots bind (ADR 0008, item 11).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, TypeGuard

from auto_bi.evidence.canonical import canonical_bytes, digest_bytes
from auto_bi.evidence.ids import ref_id
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


def verify_root_manifest(
    raw: bytes,
    run_id: str,
    *,
    verifier: EvidenceVerifier | None = None,
    parse_errors: tuple[type[Exception], ...] = (Exception,),
) -> tuple[EvidenceBundle | None, list[BundleProblem], Exception | None]:
    """Check the manifest model, run binding, canonical bytes and signature."""
    try:  # The caller selects which parse errors become findings.
        bundle = EvidenceBundle.model_validate(json.loads(raw.decode("utf-8")))
    except parse_errors as exc:  # json or schema failure
        return (
            None,
            [
                BundleProblem(
                    code="evidence.manifest_invalid",
                    severity=Severity.BLOCK,
                    detail=(
                        f"{MANIFEST_PATH} does not parse as an evidence bundle: "
                        f"{type(exc).__name__}"
                    ),
                ),
            ],
            exc,
        )

    problems: list[BundleProblem] = []
    # Copied evidence stays internally consistent — the manifest is canonical and every
    # artifact still hashes — so only its location lies. Ordinary inspection reports review:
    # the bundle is readable, but nothing proves it belongs to the run it was filed under.
    # An artifact read refuses the mismatch because the caller named the run.
    if bundle.run_id != run_id:
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
    return bundle, problems, None


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
    The store's `ArtifactRefusalError` is raised then, and nothing is graded. Of the
    payloads only the snapshots of the run are held past their hash: the closures the
    bundle keeps are checked against their bindings.
    """
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

    bundle, problems, _ = verify_root_manifest(raw, run_id, verifier=verifier)
    if bundle is None:
        return problems

    intact: dict[str, bytes] = {}
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
        elif artifact.artifact_id in _SNAPSHOT_IDS:
            intact[artifact.artifact_id] = payload
    problems.extend(_closure_problems(bundle, intact))
    return problems


#: The sides of a run whose snapshots a bundle keeps, each as `snapshot-<side>`.
CLOSURE_SIDES = ("base", "candidate")

#: The ids of those snapshots: the only payloads `verify_bundle` holds past their hash.
_SNAPSHOT_IDS = frozenset(f"snapshot-{side}" for side in CLOSURE_SIDES)


def closure_artifact_id(side: str, ref: str) -> str:
    """The id of the closure of dbt metric *ref* a run keeps for *side* (ADR 0008, item 11)."""
    return ref_id(f"closure-{side}", ref)


def _closure_problems(bundle: EvidenceBundle, intact: Mapping[str, bytes]) -> list[BundleProblem]:
    """The closures a bundle keeps, against the bindings of the snapshots it keeps.

    A side's closures are exactly the ones the bindings of its snapshot name a closure digest
    for, each under :func:`closure_artifact_id` and hashing to that digest — the bytes of a
    closure are the RFC 8785 form the digest addresses. *intact* holds the snapshots that hash
    to their address. The snapshot of a side that is not among them is a problem already, and
    the closures of that side are not checked; one that holds no graph of metrics leaves them
    unchecked, and says so. The snapshot is read as JSON, not rebuilt or validated: what it
    says beyond its bindings is not this check's.
    """
    problems: list[BundleProblem] = []
    listed = {artifact.artifact_id: artifact for artifact in bundle.artifacts}
    expected: dict[str, tuple[str, set[str]]] = {}
    unchecked: set[str] = set()
    for side in CLOSURE_SIDES:
        snapshot = listed.get(f"snapshot-{side}")
        if snapshot is None or snapshot.kind is not ArtifactKind.SEMANTIC_SNAPSHOT:
            continue
        raw = intact.get(snapshot.artifact_id)
        bindings = None if raw is None else _bindings(raw)
        if bindings is None:
            unchecked.add(side)
            if raw is not None:
                problems.append(
                    BundleProblem(
                        code="evidence.closure_unverified",
                        severity=Severity.BLOCK,
                        detail=(
                            f"{snapshot.artifact_id} holds no graph of metrics, so the "
                            f"closures of the {side} side are not checked"
                        ),
                    )
                )
            continue
        for ref, digest in bindings:
            expected.setdefault(closure_artifact_id(side, ref), (ref, set()))[1].add(digest)

    for artifact in bundle.artifacts:
        if artifact.kind is not ArtifactKind.FORMULA_CLOSURE:
            continue
        if any(artifact.artifact_id.startswith(f"closure-{side}-") for side in unchecked):
            continue
        found = expected.pop(artifact.artifact_id, None)
        if found is None:
            problems.append(
                BundleProblem(
                    code="evidence.closure_unbound",
                    severity=Severity.BLOCK,
                    detail=f"{artifact.artifact_id}: no binding of a snapshot names this closure",
                )
            )
        elif found[1] != {artifact.digest}:
            bound = ", ".join(sorted(found[1]))
            problems.append(
                BundleProblem(
                    code="evidence.closure_mismatch",
                    severity=Severity.BLOCK,
                    detail=(
                        f"{artifact.artifact_id}: the closure of {found[0]!r} hashes to "
                        f"{artifact.digest}, its bindings name {bound}"
                    ),
                )
            )
    for artifact_id, (ref, digests) in sorted(expected.items()):
        problems.append(
            BundleProblem(
                code="evidence.closure_missing",
                severity=Severity.BLOCK,
                detail=(
                    f"{artifact_id}: a binding names the closure {', '.join(sorted(digests))} "
                    f"of {ref!r}, and the bundle keeps none"
                ),
            )
        )
    return problems


def _bindings(raw: bytes) -> list[tuple[str, str]] | None:
    """`(ref, closure digest)` of each binding of a stored snapshot that names a closure.

    `None` when the payload is no JSON object with a list of metric objects under `graph`, or a
    binding there is no object with a text ref and a text or `null` closure digest. Text is a
    string with no lone surrogate: JSON reads `"\\ud800"` as one, and no id is derived from it.
    """
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    graph = data.get("graph") if isinstance(data, dict) else None
    metrics = graph.get("metrics") if isinstance(graph, dict) else None
    if not isinstance(metrics, list) or not all(isinstance(node, dict) for node in metrics):
        return None
    found: list[tuple[str, str]] = []
    for node in metrics:
        binding = node.get("binding")
        if binding is None:
            continue
        if not isinstance(binding, dict):
            return None
        ref, digest = binding.get("ref"), binding.get("closure_digest")
        if not _text(ref) or not (digest is None or _text(digest)):
            return None
        if digest is not None:
            found.append((ref, digest))
    return found


def _text(value: object) -> TypeGuard[str]:
    """Whether *value* is a string UTF-8 can encode, as RFC 8785 hashes one."""
    if not isinstance(value, str):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


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
    "CLOSURE_SIDES",
    "MANIFEST_PATH",
    "BundleProblem",
    "BundleWriter",
    "WrittenBundle",
    "closure_artifact_id",
    "read_bundle",
    "verify_bundle",
    "verify_bundle_signature",
    "verify_root_manifest",
]
