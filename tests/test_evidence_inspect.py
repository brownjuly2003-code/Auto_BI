"""Reading a written bundle back (ARCHITECTURE §9.3, §12.1 `auto_bi decision evidence`).

Writing evidence and reading it are different jobs with different failure modes. The writer
trusts its own inputs; the reader trusts nothing — the bundle it is handed may have been
copied, edited or truncated since the run that produced it, and saying so is the whole
point of a content address.

Three properties are pinned here:

* an inspection is a *verdict about integrity*, computed from problems the same way every
  other verdict in the control plane is computed — an intact bundle passes, a tampered one
  blocks, and a report whose verdict disagrees with its problems does not validate;
* a manifest that does not parse leaves no bundle to show, and that state must still name
  the bytes on disk — "there is a file here and it is not evidence" is an answer;
* a payload can only be read out of a bundle if it still hashes to what the manifest says,
  and a restricted payload is never printed at all (§10.2).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from auto_bi.evidence.canonical import digest_bytes
from auto_bi.evidence.inspect import (
    BundleInspection,
    EvidenceDigestError,
    EvidenceReadError,
    inspect_bundle,
    read_artifact,
)
from auto_bi.evidence.model import EvidenceClassification
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.severity import Severity
from auto_bi.verification.model import Verdict
from tests.test_evidence_store import RUN, full_bundle

SECRET = "customer-4471 paid 918213.55"


def store_of(root: Path) -> FilesystemArtifactStore:
    return FilesystemArtifactStore(root)


def with_restricted(root: Path) -> str:
    """A bundle that also carries a restricted payload; returns its artifact id."""
    builder = full_bundle(root)
    builder.add_blob(
        "sample-rows",
        kind="raw_payload",
        payload={"rows": [[SECRET]]},
        media_type="application/json",
        classification=EvidenceClassification.RESTRICTED,
    )
    builder.write()
    return "sample-rows"


# --- an intact bundle ---------------------------------------------------------


def test_a_clean_bundle_reads_back_as_a_pass(tmp_path: Path) -> None:
    written = full_bundle(tmp_path).write()

    inspection = inspect_bundle(store_of(tmp_path), RUN)

    assert inspection.verdict is Verdict.PASS
    assert inspection.problems == []
    assert inspection.bundle is not None
    assert inspection.bundle.product_ref == written.bundle.product_ref
    assert [claim.claim_id for claim in inspection.bundle.claims] == ["margin-drop"]
    assert len(inspection.bundle.artifacts) == len(written.bundle.artifacts)


def test_the_reported_digest_is_the_manifest_on_disk(tmp_path: Path) -> None:
    """The address a run is quoted by must be recomputed from the bytes, not restated."""
    written = full_bundle(tmp_path).write()
    raw = (tmp_path / RUN / "manifest.json").read_bytes()

    inspection = inspect_bundle(store_of(tmp_path), RUN)

    assert inspection.manifest_digest == written.digest == digest_bytes(raw)


def test_an_expected_digest_that_does_not_match_blocks(tmp_path: Path) -> None:
    """Handed a bundle and told which one it should be, the reader checks (§9.1)."""
    written = full_bundle(tmp_path).write()
    other = digest_bytes(b"another run entirely")

    assert inspect_bundle(store_of(tmp_path), RUN, expected_digest=written.digest).problems == []

    inspection = inspect_bundle(store_of(tmp_path), RUN, expected_digest=other)
    assert [problem.code for problem in inspection.problems] == ["evidence.digest_unexpected"]
    assert inspection.verdict is Verdict.BLOCK


# --- a bundle that is no longer what it claims --------------------------------


def test_a_tampered_payload_cannot_be_read_as_evidence(tmp_path: Path) -> None:
    written = full_bundle(tmp_path).write()
    blob = tmp_path / RUN / written.bundle.artifacts[1].path
    blob.write_bytes(b'{"rows":[["north","0.999"]]}')

    inspection = inspect_bundle(store_of(tmp_path), RUN)

    assert inspection.verdict is Verdict.BLOCK
    assert [problem.code for problem in inspection.problems] == ["evidence.digest_mismatch"]
    assert inspection.bundle is not None  # the manifest is still readable; the payload is not

    with pytest.raises(EvidenceReadError, match="does not hash"):
        read_artifact(store_of(tmp_path), RUN, "result-candidate", expected_digest=written.digest)


def test_a_pinned_artifact_rejects_an_unexpected_manifest_digest(tmp_path: Path) -> None:
    full_bundle(tmp_path).write()

    with pytest.raises(EvidenceDigestError, match=r"evidence\.digest_unexpected"):
        read_artifact(
            store_of(tmp_path),
            RUN,
            "check-freshness",
            expected_digest=digest_bytes(b"another bundle"),
        )


def test_a_pinned_artifact_does_not_verify_unselected_damaged_payloads(tmp_path: Path) -> None:
    written = full_bundle(tmp_path).write()
    (tmp_path / RUN / "reports" / "summary.md").write_bytes(b"damaged unrelated report")

    artifact, payload = read_artifact(
        store_of(tmp_path), RUN, "check-freshness", expected_digest=written.digest
    )

    assert artifact.artifact_id == "check-freshness"
    assert b"freshness.within_max_age" in payload


def test_a_manifest_that_does_not_parse_leaves_no_bundle_to_show(tmp_path: Path) -> None:
    full_bundle(tmp_path).write()
    (tmp_path / RUN / "manifest.json").write_bytes(b"{not json")

    inspection = inspect_bundle(store_of(tmp_path), RUN)

    assert inspection.bundle is None
    assert inspection.verdict is Verdict.BLOCK
    assert [problem.code for problem in inspection.problems] == ["evidence.manifest_invalid"]
    assert inspection.manifest_digest == digest_bytes(b"{not json")


def test_nothing_can_be_read_out_of_a_manifest_that_does_not_parse(tmp_path: Path) -> None:
    full_bundle(tmp_path).write()
    (tmp_path / RUN / "manifest.json").write_bytes(b"{not json")

    assert inspect_bundle(store_of(tmp_path), RUN).restricted() == []
    with pytest.raises(EvidenceReadError, match="no readable"):
        read_artifact(store_of(tmp_path), RUN, "check-freshness")


def test_a_bundle_stored_under_another_run_id_is_reported(tmp_path: Path) -> None:
    """Copied evidence stays internally consistent; only its location lies (§9.2)."""
    full_bundle(tmp_path).write()
    (tmp_path / RUN).rename(tmp_path / "run-copied-elsewhere")

    inspection = inspect_bundle(store_of(tmp_path), "run-copied-elsewhere")

    assert [problem.code for problem in inspection.problems] == ["evidence.run_id_mismatch"]
    assert inspection.verdict is Verdict.NEEDS_REVIEW
    assert inspection.bundle is not None


def test_a_run_without_a_manifest_is_not_a_bundle(tmp_path: Path) -> None:
    with pytest.raises(EvidenceReadError, match="no manifest"):
        inspect_bundle(store_of(tmp_path), "run-that-never-ran")


# --- the inspection model itself ----------------------------------------------


def test_an_inspection_without_a_bundle_must_say_what_stopped_it(tmp_path: Path) -> None:
    full_bundle(tmp_path).write()
    payload = inspect_bundle(store_of(tmp_path), RUN).model_dump(mode="json")
    payload["bundle"] = None

    with pytest.raises(ValidationError, match="blocking problem"):
        BundleInspection.model_validate(payload)


def test_a_verdict_that_contradicts_its_problems_is_rejected(tmp_path: Path) -> None:
    full_bundle(tmp_path).write()
    (tmp_path / RUN / "checks" / "check-freshness.json").unlink()
    payload = inspect_bundle(store_of(tmp_path), RUN).model_dump(mode="json")

    assert payload["verdict"] == Verdict.BLOCK.value
    payload["verdict"] = Verdict.PASS.value
    with pytest.raises(ValidationError, match="contradicts"):
        BundleInspection.model_validate(payload)


def test_the_inspection_round_trips(tmp_path: Path) -> None:
    full_bundle(tmp_path).write()
    inspection = inspect_bundle(store_of(tmp_path), RUN)

    assert BundleInspection.model_validate(inspection.model_dump(mode="json")) == inspection
    assert inspection.model_dump(mode="json")["kind"] == "EvidenceInspection"


# --- reading one artifact ------------------------------------------------------


def test_reading_an_artifact_returns_the_bytes_the_manifest_addresses(tmp_path: Path) -> None:
    written = full_bundle(tmp_path).write()

    artifact, payload = read_artifact(store_of(tmp_path), RUN, "check-freshness")

    assert digest_bytes(payload) == artifact.digest
    assert b"freshness.within_max_age" in payload
    assert artifact.artifact_id in {item.artifact_id for item in written.bundle.artifacts}


def test_a_restricted_payload_is_named_but_never_printed(tmp_path: Path) -> None:
    """§10.2: the manifest lists it, the reader refuses to show it."""
    artifact_id = with_restricted(tmp_path)

    inspection = inspect_bundle(store_of(tmp_path), RUN)
    assert inspection.bundle is not None
    listed = next(item for item in inspection.bundle.artifacts if item.artifact_id == artifact_id)
    assert listed.classification is EvidenceClassification.RESTRICTED
    assert inspection.restricted() == [listed]
    assert SECRET not in inspection.model_dump_json()

    with pytest.raises(EvidenceReadError, match="restricted"):
        read_artifact(
            store_of(tmp_path), RUN, artifact_id, expected_digest=inspection.manifest_digest
        )


def test_an_artifact_the_store_no_longer_holds_is_reported(tmp_path: Path) -> None:
    full_bundle(tmp_path).write()
    (tmp_path / RUN / "checks" / "check-freshness.json").unlink()

    with pytest.raises(EvidenceReadError, match="not in the store"):
        read_artifact(
            store_of(tmp_path),
            RUN,
            "check-freshness",
            expected_digest=digest_bytes((tmp_path / RUN / "manifest.json").read_bytes()),
        )


def test_reading_an_artifact_the_bundle_does_not_list_is_an_error(tmp_path: Path) -> None:
    full_bundle(tmp_path).write()
    with pytest.raises(EvidenceReadError, match="check-freshness"):  # names what is there
        read_artifact(store_of(tmp_path), RUN, "check-nonexistent")


def test_problem_severities_drive_the_verdict(tmp_path: Path) -> None:
    full_bundle(tmp_path).write()
    (tmp_path / RUN / "reports" / "summary.md").unlink()

    inspection = inspect_bundle(store_of(tmp_path), RUN)

    assert [problem.severity for problem in inspection.problems] == [Severity.BLOCK]
    assert inspection.verdict is Verdict.BLOCK
