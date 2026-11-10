"""A payload belongs to the run named by its checked root manifest."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from auto_bi.cli import main
from auto_bi.decision.use_cases import EvidenceLocation
from auto_bi.evidence.canonical import digest_bytes
from auto_bi.evidence.inspect import EvidenceReadError, inspect_bundle, read_artifact
from auto_bi.evidence.manifest import MANIFEST_PATH, BundleWriter, WrittenBundle
from auto_bi.evidence.signing import Ed25519Signer, Ed25519TrustStore
from auto_bi.evidence.store import ArtifactStore, ArtifactStoreError, FilesystemArtifactStore
from auto_bi.severity import Severity
from auto_bi.verification.model import EXIT_ERROR, Verdict

ORIGINAL = "run-original"
SWAPPED = "run-swapped"
PAYLOAD = b"artifact of run-original"


class MemoryStore:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str], bytes] = {}
        self.reads: list[tuple[str, str]] = []

    def put(self, run_id: str, path: str, payload: bytes) -> None:
        self.data[run_id, path] = payload

    def get(self, run_id: str, path: str) -> bytes:
        self.reads.append((run_id, path))
        try:
            return self.data[run_id, path]
        except KeyError as exc:
            raise ArtifactStoreError(path) from exc

    def exists(self, run_id: str, path: str) -> bool:
        return (run_id, path) in self.data


def signed_runs(
    store: ArtifactStore, *, signed: bool = True
) -> tuple[WrittenBundle, Ed25519TrustStore | None, bytes]:
    key = Ed25519PrivateKey.generate()
    writer = BundleWriter(
        store=store,
        run_id=ORIGINAL,
        product_ref="decision://sales/weekly_margin@1",
        verifier_version="test.1",
        created_at="2026-11-01T09:00:00Z",
        signer=Ed25519Signer(key) if signed else None,
    )
    writer.add_blob("answer", kind="raw_payload", payload=PAYLOAD, media_type="text/plain")
    written = writer.write()
    for path in [MANIFEST_PATH, *(item.path for item in written.bundle.artifacts)]:
        store.put(SWAPPED, path, store.get(ORIGINAL, path))
    return (
        written,
        Ed25519TrustStore([key.public_key()]) if signed else None,
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ),
    )


@pytest.mark.parametrize("signed", [False, True])
@pytest.mark.parametrize("pinned", [False, True])
def test_read_artifact_refuses_manifest_of_another_run(signed: bool, pinned: bool) -> None:
    store = MemoryStore()
    written, trust, _ = signed_runs(store, signed=signed)
    store.reads.clear()

    with pytest.raises(EvidenceReadError) as caught:
        read_artifact(
            store,
            SWAPPED,
            "answer",
            expected_digest=written.digest if pinned else None,
            verifier=trust,
        )

    assert "evidence.run_id_mismatch" in str(caught.value)
    assert f"names run {ORIGINAL!r}" in str(caught.value)
    assert f"stored as {SWAPPED!r}" in str(caught.value)
    assert store.reads == [(SWAPPED, MANIFEST_PATH)]


def test_matching_signed_run_returns_its_artifact() -> None:
    store = MemoryStore()
    written, trust, _ = signed_runs(store)

    artifact, payload = read_artifact(
        store, ORIGINAL, "answer", expected_digest=written.digest, verifier=trust
    )

    assert artifact == written.bundle.artifacts[0]
    assert payload == PAYLOAD


@pytest.mark.parametrize("signed", [False, True])
def test_inspection_still_reports_the_run_mismatch(signed: bool) -> None:
    store = MemoryStore()
    written, trust, _ = signed_runs(store, signed=signed)

    report = inspect_bundle(store, SWAPPED, expected_digest=written.digest, verifier=trust)

    assert report.verdict is Verdict.NEEDS_REVIEW
    assert report.signature_verified is signed
    assert report.bundle == written.bundle
    assert len(report.problems) == 1
    problem = report.problems[0]
    assert problem.code == "evidence.run_id_mismatch"
    assert problem.severity is Severity.REVIEW
    assert problem.detail == f"the manifest names run {ORIGINAL!r}, but it is stored as {SWAPPED!r}"


@pytest.mark.parametrize("run_id", [SWAPPED, ORIGINAL])
@pytest.mark.parametrize("signed", [False, True])
@pytest.mark.parametrize("pinned", [False, True])
def test_cli_artifact_uses_the_run_binding(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_id: str,
    signed: bool,
    pinned: bool,
) -> None:
    store = MemoryStore()
    written, trust, _ = signed_runs(store, signed=signed)
    monkeypatch.setattr(
        "auto_bi.decision.use_cases.locate_evidence",
        lambda path, run: EvidenceLocation(store=store, run_id=run),
    )
    monkeypatch.setattr("auto_bi.evidence.signing.load_ed25519_trust_store", lambda paths: trust)
    store.reads.clear()

    code = main(
        [
            "decision",
            "evidence",
            "evidence",
            "--run",
            run_id,
            "--artifact",
            "answer",
            *(["--expect-digest", written.digest] if pinned else []),
            *(["--trust-key", "public.pem"] if signed else []),
        ]
    )

    captured = capsys.readouterr()
    if run_id == SWAPPED:
        assert code == EXIT_ERROR == 2
        assert captured.out == ""
        assert captured.err == (
            f"error: evidence.run_id_mismatch: the manifest names run {ORIGINAL!r}, "
            f"but it is stored as {SWAPPED!r}\n"
        )
        assert store.reads == [(SWAPPED, MANIFEST_PATH)]
    else:
        assert code == 0
        assert captured.out == PAYLOAD.decode() + "\n"
        assert captured.err == ""


@pytest.mark.parametrize("run_id", [SWAPPED, ORIGINAL])
def test_filesystem_cli_artifact_checks_the_signed_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], run_id: str
) -> None:
    store = FilesystemArtifactStore(tmp_path / "evidence")
    written, _, public_pem = signed_runs(store)
    public_path = tmp_path / "public.pem"
    public_path.write_bytes(public_pem)

    code = main(
        [
            "decision",
            "evidence",
            str(tmp_path / "evidence"),
            "--run",
            run_id,
            "--artifact",
            "answer",
            "--expect-digest",
            written.digest,
            "--trust-key",
            str(public_path),
        ]
    )

    captured = capsys.readouterr()
    if run_id == SWAPPED:
        assert code == EXIT_ERROR
        assert captured.out == ""
        assert "evidence.run_id_mismatch" in captured.err
        assert ORIGINAL in captured.err and SWAPPED in captured.err
        assert (
            main(
                [
                    "decision",
                    "evidence",
                    str(tmp_path / "evidence"),
                    "--run",
                    run_id,
                    "--trust-key",
                    str(public_path),
                    "--json",
                ]
            )
            == 3
        )
        report = json.loads(capsys.readouterr().out)
        assert [item["code"] for item in report["problems"]] == ["evidence.run_id_mismatch"]
        assert report["signature_verified"] is True
    else:
        assert code == 0
        assert captured.out == PAYLOAD.decode() + "\n"
        assert captured.err == ""


@pytest.mark.parametrize("signed", [False, True])
@pytest.mark.parametrize("pinned", [False, True], ids=["unpinned", "pinned"])
def test_artifact_accepts_a_noncanonical_root_while_inspection_reports_it(
    signed: bool, pinned: bool
) -> None:
    store = MemoryStore()
    written, trust, _ = signed_runs(store, signed=signed)
    raw = b" " + written.bundle.canonical_bytes()
    store.put(ORIGINAL, MANIFEST_PATH, raw)
    report = inspect_bundle(store, ORIGINAL, verifier=trust)
    assert [item.code for item in report.problems] == ["evidence.manifest_not_canonical"]
    assert report.signature_verified is signed
    store.reads.clear()

    artifact, payload = read_artifact(
        store,
        ORIGINAL,
        "answer",
        expected_digest=digest_bytes(raw) if pinned else None,
        verifier=trust,
    )

    assert artifact == written.bundle.artifacts[0]
    assert payload == PAYLOAD
    assert store.reads == [(ORIGINAL, MANIFEST_PATH), (ORIGINAL, artifact.path)]


@pytest.mark.parametrize(
    "fault,signed",
    [("run", False), ("run", True), ("signature", True)],
    ids=["unsigned-run", "signed-run", "signature"],
)
@pytest.mark.parametrize("pinned", [False, True], ids=["unpinned", "pinned"])
def test_noncanonical_root_preserves_other_refusals_for_api_and_cli(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fault: str,
    signed: bool,
    pinned: bool,
) -> None:
    from auto_bi.evidence.canonical import canonical_bytes

    store = MemoryStore()
    written, trust, _ = signed_runs(store, signed=signed)
    run_id = SWAPPED if fault == "run" else ORIGINAL
    document = written.bundle.model_dump(mode="json")
    if fault == "run":
        root_code = "evidence.run_id_mismatch"
        detail = f"the manifest names run {ORIGINAL!r}, but it is stored as {SWAPPED!r}"
    else:
        assert written.bundle.signature is not None
        key_id = written.bundle.signature.key_id
        document["product_ref"] = "decision://sales/other_product@1"
        root_code = "evidence.signature_invalid"
        detail = f"signature from key {key_id!r} does not authenticate the manifest"
    raw = b" " + canonical_bytes(document)
    store.put(run_id, MANIFEST_PATH, raw)
    expected_digest = digest_bytes(raw) if pinned else None
    message = f"{root_code}: {detail}"
    store.reads.clear()

    with pytest.raises(EvidenceReadError) as caught:
        read_artifact(store, run_id, "answer", expected_digest=expected_digest, verifier=trust)

    assert str(caught.value) == message
    assert store.reads == [(run_id, MANIFEST_PATH)]

    monkeypatch.setattr(
        "auto_bi.decision.use_cases.locate_evidence",
        lambda path, run: EvidenceLocation(store=store, run_id=run),
    )
    monkeypatch.setattr("auto_bi.evidence.signing.load_ed25519_trust_store", lambda paths: trust)
    store.reads.clear()
    code = main(
        [
            "decision",
            "evidence",
            "evidence",
            "--run",
            run_id,
            "--artifact",
            "answer",
            *(["--expect-digest", expected_digest] if expected_digest else []),
            *(["--trust-key", "public.pem"] if signed else []),
        ]
    )

    captured = capsys.readouterr()
    assert code == EXIT_ERROR == 2
    assert captured.err == f"error: {message}\n"
    assert captured.out == ""
    assert store.reads == [(run_id, MANIFEST_PATH)]

    report = inspect_bundle(store, run_id, expected_digest=expected_digest, verifier=trust)
    assert sorted(item.code for item in report.problems) == sorted(
        [root_code, "evidence.manifest_not_canonical"]
    )


@pytest.mark.parametrize(
    "raw", [b"{", b"{}", b"[" * 100_000 + b"]" * 100_000], ids=["json", "model", "depth"]
)
def test_artifact_parse_refusal_keeps_its_cause(raw: bytes) -> None:
    store = MemoryStore()
    store.put(ORIGINAL, MANIFEST_PATH, raw)

    with pytest.raises(EvidenceReadError, match=r"no readable manifest\.json") as caught:
        read_artifact(store, ORIGINAL, "answer")

    expected = RecursionError if raw.startswith(b"[") else ValueError
    assert isinstance(caught.value.__cause__, expected)
    assert store.reads == [(ORIGINAL, MANIFEST_PATH)]
    report = inspect_bundle(store, ORIGINAL)
    assert [item.code for item in report.problems] == ["evidence.manifest_invalid"]


@pytest.mark.parametrize("error_type", [RuntimeError, MemoryError])
def test_unexpected_parse_errors_escape_reader_but_are_inspection_findings(
    monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
) -> None:
    store = MemoryStore()
    store.put(ORIGINAL, MANIFEST_PATH, b"{}")
    error = error_type("parser fault")

    def fail_parse(raw: str) -> None:
        raise error

    monkeypatch.setattr("auto_bi.evidence.manifest.json.loads", fail_parse)
    with pytest.raises(error_type) as caught:
        read_artifact(store, ORIGINAL, "answer")
    assert caught.value is error
    assert store.reads == [(ORIGINAL, MANIFEST_PATH)]
    report = inspect_bundle(store, ORIGINAL)
    assert report.verdict is Verdict.BLOCK
    assert [item.code for item in report.problems] == ["evidence.manifest_invalid"]
    assert report.problems[0].detail.endswith(error_type.__name__)


@pytest.mark.parametrize("fault", ["signature", "trust", "run_and_signature"])
def test_root_refusal_text_is_code_and_detail_for_api_and_cli(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], fault: str
) -> None:
    from auto_bi.evidence.canonical import canonical_bytes

    store = MemoryStore()
    written, trust, _ = signed_runs(store)
    assert written.bundle.signature is not None
    key_id = written.bundle.signature.key_id
    run_id = SWAPPED if fault == "run_and_signature" else ORIGINAL
    if fault == "trust":
        trust = None
        message = "evidence.signature_untrusted: "
        message += "signed evidence requires an explicitly configured public trust root"
    else:
        document = written.bundle.model_dump(mode="json")
        document["product_ref"] = "decision://sales/other_product@1"
        store.put(run_id, MANIFEST_PATH, canonical_bytes(document))
        message = "evidence.signature_invalid: "
        message += f"signature from key {key_id!r} does not authenticate the manifest"
    if run_id == SWAPPED:
        message = (
            f"evidence.run_id_mismatch: the manifest names run {ORIGINAL!r}, "
            f"but it is stored as {SWAPPED!r}; {message}"
        )
    store.reads.clear()
    with pytest.raises(EvidenceReadError) as caught:
        read_artifact(store, run_id, "answer", verifier=trust)
    assert str(caught.value) == message
    assert store.reads == [(run_id, MANIFEST_PATH)]

    monkeypatch.setattr(
        "auto_bi.decision.use_cases.locate_evidence",
        lambda path, run: EvidenceLocation(store=store, run_id=run),
    )
    monkeypatch.setattr("auto_bi.evidence.signing.load_ed25519_trust_store", lambda paths: trust)
    store.reads.clear()
    assert (
        main(
            ["decision", "evidence", "evidence", "--run", run_id, "--artifact", "answer"]
            + (["--trust-key", "public.pem"] if trust else [])
        )
        == 2
    )
    captured = capsys.readouterr()
    assert captured.err == f"error: {message}\n"
    assert captured.out == ""
    assert store.reads == [(run_id, MANIFEST_PATH)]


@pytest.mark.parametrize("fault", ["model", "signature", "trust"])
def test_artifact_and_inspection_share_root_refusals(fault: str) -> None:
    store = MemoryStore()
    written, trust, _ = signed_runs(store)
    if fault == "model":
        store.put(ORIGINAL, MANIFEST_PATH, b"{}")
        code = "evidence.manifest_invalid"
    elif fault == "signature":
        document = written.bundle.model_dump(mode="json")
        document["product_ref"] = "decision://sales/other_product@1"
        from auto_bi.evidence.canonical import canonical_bytes

        store.put(ORIGINAL, MANIFEST_PATH, canonical_bytes(document))
        code = "evidence.signature_invalid"
    else:
        trust = None
        code = "evidence.signature_untrusted"
    report = inspect_bundle(store, ORIGINAL, verifier=trust)
    assert [item.code for item in report.problems] == [code]
    store.reads.clear()

    with pytest.raises(EvidenceReadError) as caught:
        read_artifact(store, ORIGINAL, "answer", verifier=trust)

    assert ("no readable manifest.json" if fault == "model" else code) in str(caught.value)
    assert store.reads == [(ORIGINAL, MANIFEST_PATH)]
