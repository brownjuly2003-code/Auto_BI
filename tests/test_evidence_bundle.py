"""EvidenceBundle — content-addressed evidence and typed claims (ARCHITECTURE 7.7, 9).

Two properties are load-bearing and therefore enforced at parse time, not at write time:
a factual claim without a resolvable evidence ref never assembles (invariant 5), and an
artifact path is a bundle-relative location inside the fixed layout, never a caller-chosen
filesystem path (9.2). `integrity=signed` stays unreachable until a signature is verified,
so a local hash is never presented as a signature (9.3).
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from auto_bi.evidence.canonical import sha256_digest
from auto_bi.evidence.model import (
    ArtifactKind,
    ClaimType,
    EvidenceBundle,
    EvidenceClassification,
    EvidenceIntegrity,
)
from auto_bi.semantic.model import DataClassification

RESULT_DIGEST = sha256_digest({"rows": [["north", "0.412"]]})
PLAN_DIGEST = sha256_digest({"plan": "candidate"})


def artifact(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "artifact_id": "result-candidate",
        "kind": "query_result",
        "media_type": "application/json",
        "digest": RESULT_DIGEST,
        "size_bytes": 128,
        "classification": "internal",
        "path": f"blobs/sha256/{RESULT_DIGEST.removeprefix('sha256:')}",
    }
    data.update(overrides)
    return data


def claim(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "claim_id": "margin-drop",
        "claim_type": "observation",
        "summary_code": "margin.candidate_differs",
        "value": "-0.0031",
        "unit": "ratio",
        "evidence_refs": ["result-candidate"],
    }
    data.update(overrides)
    return data


def bundle(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "api_version": "auto-bi.io/v2alpha1",
        "kind": "EvidenceBundle",
        "run_id": "run-2026-08-24-01",
        "product_ref": "decision://sales/weekly_margin@1",
        "created_at": "2026-08-24T09:15:00Z",
        "verifier_version": "0.5.0+decisionops.1",
        "integrity": "content_hashed",
        "artifacts": [artifact()],
        "claims": [claim()],
    }
    data.update(deepcopy(overrides))
    return data


def test_valid_bundle_parses_and_round_trips() -> None:
    parsed = EvidenceBundle.model_validate(bundle())
    assert parsed.integrity is EvidenceIntegrity.CONTENT_HASHED
    assert parsed.artifacts[0].kind is ArtifactKind.QUERY_RESULT
    assert parsed.artifacts[0].classification is EvidenceClassification.INTERNAL
    assert parsed.claims[0].claim_type is ClaimType.OBSERVATION
    again = EvidenceBundle.model_validate(parsed.model_dump(mode="json"))
    assert again == parsed
    assert again.digest() == parsed.digest()


def test_digest_is_content_addressed() -> None:
    base = EvidenceBundle.model_validate(bundle())
    changed = EvidenceBundle.model_validate(
        bundle(claims=[claim(value="-0.0032")]),
    )
    assert base.digest() != changed.digest()


def test_dangling_evidence_ref_is_rejected() -> None:
    with pytest.raises(ValidationError, match="missing-artifact"):
        EvidenceBundle.model_validate(bundle(claims=[claim(evidence_refs=["missing-artifact"])]))


def test_claim_without_evidence_is_rejected() -> None:
    """Invariant 5: a factual claim without evidence never reaches governed output."""
    with pytest.raises(ValidationError, match="at least 1"):
        EvidenceBundle.model_validate(bundle(claims=[claim(evidence_refs=[])]))


def test_causal_claim_is_refused_until_a_verified_module_exists() -> None:
    """Invariant 12: the type exists in the taxonomy, the gate must not emit it yet."""
    with pytest.raises(ValidationError, match="causal"):
        EvidenceBundle.model_validate(bundle(claims=[claim(claim_type="causal")]))


def test_duplicate_ids_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate artifact"):
        EvidenceBundle.model_validate(bundle(artifacts=[artifact(), artifact()]))
    with pytest.raises(ValidationError, match="duplicate claim"):
        EvidenceBundle.model_validate(bundle(claims=[claim(), claim()]))


@pytest.mark.parametrize(
    "path",
    [
        "../secrets.json",
        "/etc/passwd",
        "C:/keys.json",
        "checks/../../escape.json",
        "checks//double.json",
        "outside/x.json",
        "manifest.json",
        "checks/",
        "",
    ],
)
def test_artifact_path_stays_inside_the_bundle_layout(path: str) -> None:
    with pytest.raises(ValidationError, match="path"):
        EvidenceBundle.model_validate(bundle(artifacts=[artifact(path=path)]))


def test_blob_path_must_address_its_own_digest() -> None:
    with pytest.raises(ValidationError, match="digest"):
        EvidenceBundle.model_validate(
            bundle(artifacts=[artifact(path=f"blobs/sha256/{PLAN_DIGEST.removeprefix('sha256:')}")])
        )


def test_check_result_artifact_may_live_under_checks() -> None:
    parsed = EvidenceBundle.model_validate(
        bundle(
            artifacts=[
                artifact(
                    artifact_id="check-freshness",
                    kind="check_result",
                    path="checks/check-freshness.json",
                )
            ],
            claims=[claim(evidence_refs=["check-freshness"])],
        )
    )
    assert parsed.artifacts[0].path == "checks/check-freshness.json"


def test_restricted_payload_must_live_in_the_restricted_store() -> None:
    with pytest.raises(ValidationError, match="restricted"):
        EvidenceBundle.model_validate(bundle(artifacts=[artifact(classification="restricted")]))


def test_restricted_store_is_only_for_restricted_payloads() -> None:
    with pytest.raises(ValidationError, match="restricted"):
        EvidenceBundle.model_validate(
            bundle(artifacts=[artifact(path="restricted/rows.json", classification="internal")])
        )


def test_malformed_digest_is_rejected() -> None:
    with pytest.raises(ValidationError, match="sha256"):
        EvidenceBundle.model_validate(bundle(artifacts=[artifact(digest="deadbeef")]))


def test_signed_integrity_requires_a_verified_signature() -> None:
    """9.3: a local content hash is never reported as a signature."""
    with pytest.raises(ValidationError, match="signature"):
        EvidenceBundle.model_validate(bundle(integrity="signed"))


def test_created_at_must_be_rfc3339_utc() -> None:
    for bad in ["2026-08-24 09:15:00", "2026-08-24T09:15:00+03:00", "2026-08-24"]:
        with pytest.raises(ValidationError, match="UTC"):
            EvidenceBundle.model_validate(bundle(created_at=bad))


def test_evidence_classification_mirrors_the_dwh_classes() -> None:
    """Two layers, one vocabulary: drift here would make redaction rules incomparable."""
    assert {c.value for c in EvidenceClassification} == {c.value for c in DataClassification}
