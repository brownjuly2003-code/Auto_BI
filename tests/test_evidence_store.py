"""Writing and re-verifying an evidence bundle (ARCHITECTURE §9.2–§9.3, bootstrap task 6).

The bundle is the artifact a release decision is defended with, so writing it is not just
serialization. Paths are built by the store, never by a caller; the same inputs must
produce the same bytes; a claim without its evidence never reaches disk; and a restricted
payload lives in the restricted store while the public manifest carries only its digest.

Re-verification is the other half: a bundle read back from disk must still hash to what it
claims, or it is not evidence.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from auto_bi.evidence.canonical import sha256_digest
from auto_bi.evidence.manifest import (
    BundleWriter,
    read_bundle,
    verify_bundle,
)
from auto_bi.evidence.model import ClaimType, EvidenceClassification, EvidenceIntegrity
from auto_bi.evidence.store import ArtifactStoreError, FilesystemArtifactStore

RUN = "run-2026-08-24-01"
PRODUCT = "decision://sales/weekly_margin@1"
CREATED = "2026-08-24T09:15:00Z"


def writer(root: Path) -> BundleWriter:
    return BundleWriter(
        store=FilesystemArtifactStore(root),
        run_id=RUN,
        product_ref=PRODUCT,
        verifier_version="0.5.0+decisionops.1",
        created_at=CREATED,
    )


def full_bundle(root: Path) -> BundleWriter:
    builder = writer(root)
    builder.add_check("freshness", {"status": "pass", "summary_code": "freshness.within_max_age"})
    result = builder.add_blob(
        "result-candidate",
        kind="query_result",
        payload={"rows": [["north", "0.412"]]},
        media_type="application/json",
    )
    builder.add_claim(
        claim_id="margin-drop",
        claim_type=ClaimType.OBSERVATION,
        summary_code="margin.candidate_differs",
        evidence_refs=[result, "check-freshness"],
        value="-0.0031",
        unit="ratio",
    )
    builder.add_report("summary.md", "# Weekly margin\n\nNo blocking findings.\n")
    return builder


def test_bundle_is_written_into_the_documented_layout(tmp_path: Path) -> None:
    written = full_bundle(tmp_path).write()
    root = tmp_path / RUN
    assert (root / "manifest.json").is_file()
    assert (root / "checks" / "check-freshness.json").is_file()
    assert (root / "reports" / "summary.md").is_file()
    blobs = list((root / "blobs" / "sha256").iterdir())
    assert [blob.name for blob in blobs] == [
        written.bundle.artifacts[1].digest.removeprefix("sha256:")
    ]
    assert written.digest.startswith("sha256:")


def test_manifest_on_disk_is_the_canonical_bytes(tmp_path: Path) -> None:
    written = full_bundle(tmp_path).write()
    raw = (tmp_path / RUN / "manifest.json").read_bytes()
    assert raw == written.bundle.canonical_bytes()
    assert sha256_digest(json.loads(raw.decode("utf-8"))) == written.digest


def test_writing_the_same_inputs_twice_is_byte_identical(tmp_path: Path) -> None:
    first = full_bundle(tmp_path / "a").write()
    second = full_bundle(tmp_path / "b").write()
    assert first.digest == second.digest
    assert (tmp_path / "a" / RUN / "manifest.json").read_bytes() == (
        tmp_path / "b" / RUN / "manifest.json"
    ).read_bytes()


def test_claim_without_its_evidence_never_reaches_disk(tmp_path: Path) -> None:
    builder = writer(tmp_path)
    builder.add_claim(
        claim_id="unsupported",
        claim_type=ClaimType.OBSERVATION,
        summary_code="margin.candidate_differs",
        evidence_refs=["result-that-was-never-added"],
    )
    with pytest.raises(ValidationError, match="result-that-was-never-added"):
        builder.write()
    assert not (tmp_path / RUN).exists()


def test_causal_claim_is_refused_by_the_writer(tmp_path: Path) -> None:
    builder = full_bundle(tmp_path)
    with pytest.raises(ValidationError, match="causal"):
        builder.add_claim(
            claim_id="because",
            claim_type=ClaimType.CAUSAL,
            summary_code="margin.caused_by_price",
            evidence_refs=["result-candidate"],
        )


def test_restricted_payload_stays_out_of_the_public_manifest(tmp_path: Path) -> None:
    """§10.2: the manifest carries the digest and the class, not the rows."""
    secret = "customer-4471 paid 918213.55"
    builder = full_bundle(tmp_path)
    builder.add_blob(
        "sample-rows",
        kind="raw_payload",
        payload={"rows": [[secret]]},
        media_type="application/json",
        classification=EvidenceClassification.RESTRICTED,
    )
    written = builder.write()

    manifest = (tmp_path / RUN / "manifest.json").read_bytes().decode("utf-8")
    assert secret not in manifest
    restricted = next((tmp_path / RUN / "restricted").iterdir())
    assert secret in restricted.read_text(encoding="utf-8")
    artifact = next(item for item in written.bundle.artifacts if item.artifact_id == "sample-rows")
    assert artifact.classification is EvidenceClassification.RESTRICTED
    assert artifact.path.startswith("restricted/")


def test_written_bundle_re_verifies(tmp_path: Path) -> None:
    full_bundle(tmp_path).write()
    assert verify_bundle(FilesystemArtifactStore(tmp_path), RUN) == []
    bundle = read_bundle(FilesystemArtifactStore(tmp_path), RUN)
    assert bundle.run_id == RUN
    assert bundle.integrity is EvidenceIntegrity.CONTENT_HASHED


def test_tampered_payload_is_caught_by_its_digest(tmp_path: Path) -> None:
    written = full_bundle(tmp_path).write()
    blob = tmp_path / RUN / written.bundle.artifacts[1].path
    blob.write_bytes(b'{"rows":[["north","0.999"]]}')
    problems = verify_bundle(FilesystemArtifactStore(tmp_path), RUN)
    assert [problem.code for problem in problems] == ["evidence.digest_mismatch"]
    assert "result-candidate" in problems[0].detail


def test_missing_artifact_is_reported(tmp_path: Path) -> None:
    full_bundle(tmp_path).write()
    (tmp_path / RUN / "checks" / "check-freshness.json").unlink()
    problems = verify_bundle(FilesystemArtifactStore(tmp_path), RUN)
    assert [problem.code for problem in problems] == ["evidence.artifact_missing"]


def test_missing_manifest_is_reported(tmp_path: Path) -> None:
    problems = verify_bundle(FilesystemArtifactStore(tmp_path), "run-that-never-ran")
    assert [problem.code for problem in problems] == ["evidence.manifest_missing"]


def test_corrupted_manifest_is_reported(tmp_path: Path) -> None:
    full_bundle(tmp_path).write()
    (tmp_path / RUN / "manifest.json").write_bytes(b"{not json")
    problems = verify_bundle(FilesystemArtifactStore(tmp_path), RUN)
    assert [problem.code for problem in problems] == ["evidence.manifest_invalid"]


# --- the store owns the paths --------------------------------------------------------


@pytest.mark.parametrize("path", ["../escape.json", "/etc/passwd", "checks/../../out.json", ""])
def test_store_refuses_a_path_that_leaves_the_run(tmp_path: Path, path: str) -> None:
    store = FilesystemArtifactStore(tmp_path)
    with pytest.raises(ArtifactStoreError, match=r"confin|path"):
        store.put(RUN, path, b"x")


def test_store_refuses_a_symlinked_parent(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path)
    store.put(RUN, "checks/real.json", b"{}")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / RUN / "reports"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - needs privileges on Windows
        pytest.skip("creating a symlink requires privileges on this machine")
    with pytest.raises(ArtifactStoreError, match="symlink"):
        store.put(RUN, "reports/summary.md", b"# report")


def test_store_round_trips_bytes(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path)
    store.put(RUN, "checks/one.json", b'{"status":"pass"}')
    assert store.get(RUN, "checks/one.json") == b'{"status":"pass"}'
    assert store.exists(RUN, "checks/one.json")
    assert not store.exists(RUN, "checks/two.json")
