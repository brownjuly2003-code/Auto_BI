"""Ed25519 authentication of canonical evidence manifests (ARCHITECTURE §9.3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from auto_bi.cli import main
from auto_bi.evidence.canonical import canonical_bytes, digest_bytes
from auto_bi.evidence.inspect import inspect_bundle
from auto_bi.evidence.manifest import BundleWriter, verify_bundle
from auto_bi.evidence.model import EvidenceIntegrity
from auto_bi.evidence.signing import load_ed25519_signer, load_ed25519_trust_store
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.verification.model import EXIT_ERROR, Verdict
from tests.test_decision_cli import _verify_argv
from tests.test_decision_verify import Case
from tests.test_evidence_store import CREATED, PRODUCT, RUN


def _key_files(root: Path, name: str) -> tuple[Path, Path, bytes]:
    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_key = private_key.public_key()
    public_pem = public_key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    private_path = root / f"{name}-private.pem"
    public_path = root / f"{name}-public.pem"
    private_path.write_bytes(private_pem)
    public_path.write_bytes(public_pem)
    return (
        private_path,
        public_path,
        public_key.public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        ),
    )


def _signed_bundle(root: Path, private_path: Path) -> tuple[Path, object]:
    store = FilesystemArtifactStore(root)
    writer = BundleWriter(
        store=store,
        run_id=RUN,
        product_ref=PRODUCT,
        verifier_version="test-signed.1",
        created_at=CREATED,
        signer=load_ed25519_signer(private_path),
    )
    writer.add_check("freshness", {"status": "pass"})
    return root / RUN / "manifest.json", writer.write()


class SwapAfterFirstArtifactStore(FilesystemArtifactStore):
    def __init__(self, root: Path, replacement: bytes) -> None:
        super().__init__(root)
        self.replacement = replacement
        self.manifest_reads = 0
        self.artifact_reads = 0

    def get(self, run_id: str, path: str) -> bytes:
        payload = super().get(run_id, path)
        if path == "manifest.json":
            self.manifest_reads += 1
        elif self.artifact_reads == 0:
            self.artifact_reads += 1
            self.put(run_id, "manifest.json", self.replacement)
        return payload


def test_signed_bundle_passes_only_with_its_public_trust_root(tmp_path: Path) -> None:
    private_path, public_path, public_raw = _key_files(tmp_path, "trusted")
    _, rotation_public_path, _ = _key_files(tmp_path, "rotation")
    manifest_path, written = _signed_bundle(tmp_path / "evidence", private_path)
    store = FilesystemArtifactStore(tmp_path / "evidence")
    trust = load_ed25519_trust_store([rotation_public_path, public_path])

    assert written.bundle.integrity is EvidenceIntegrity.SIGNED
    assert written.bundle.signature is not None
    assert written.bundle.signature.algorithm == "ed25519"
    assert written.bundle.signature.key_id == digest_bytes(public_raw)
    assert verify_bundle(store, RUN, verifier=trust) == []
    assert [problem.code for problem in verify_bundle(store, RUN)] == [
        "evidence.signature_untrusted"
    ]
    assert private_path.read_bytes() not in manifest_path.read_bytes()


def test_inspection_keeps_signed_manifest_snapshot_after_signature_check(
    tmp_path: Path,
) -> None:
    private_path, public_path, _ = _key_files(tmp_path, "trusted")
    manifest_path, written = _signed_bundle(tmp_path / "evidence", private_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["product_ref"] = "decision://sales/other_product@1"
    store = SwapAfterFirstArtifactStore(tmp_path / "evidence", canonical_bytes(payload))
    trust = load_ed25519_trust_store([public_path])

    inspection = inspect_bundle(store, RUN, expected_digest=written.digest, verifier=trust)

    assert inspection.manifest_digest == written.digest
    assert inspection.bundle is not None
    assert inspection.bundle.product_ref == written.bundle.product_ref
    assert inspection.signature_verified is True
    assert inspection.problems == []
    assert store.manifest_reads == 1


def test_signing_identity_is_part_of_the_gate_run_identity(tmp_path: Path) -> None:
    first_private, _, _ = _key_files(tmp_path, "first")
    second_private, _, _ = _key_files(tmp_path, "second")

    unsigned = Case(tmp_path / "unsigned").run()
    first_signed = Case(tmp_path / "first-signed").run(signer=load_ed25519_signer(first_private))
    second_signed = Case(tmp_path / "second-signed").run(signer=load_ed25519_signer(second_private))

    assert (
        len({unsigned.idempotency_key, first_signed.idempotency_key, second_signed.idempotency_key})
        == 3
    )
    assert len({unsigned.run_id, first_signed.run_id, second_signed.run_id}) == 3


def test_wrong_trust_root_does_not_authenticate_a_signed_bundle(tmp_path: Path) -> None:
    private_path, _, _ = _key_files(tmp_path, "signing")
    _, wrong_public_path, _ = _key_files(tmp_path, "wrong")
    _signed_bundle(tmp_path / "evidence", private_path)

    problems = verify_bundle(
        FilesystemArtifactStore(tmp_path / "evidence"),
        RUN,
        verifier=load_ed25519_trust_store([wrong_public_path]),
    )

    assert [problem.code for problem in problems] == ["evidence.signature_key_untrusted"]


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("product_ref", "decision://sales/other_product@1", "evidence.signature_invalid"),
        ("signed_at", "2026-08-24T09:16:00Z", "evidence.signature_invalid"),
        ("algorithm", "unknown-signature", "evidence.signature_algorithm_unsupported"),
        ("key_id", "not-a-sha256-fingerprint", "evidence.manifest_invalid"),
        ("value", "not-base64url", "evidence.signature_invalid"),
    ],
)
def test_manifest_or_signature_tampering_is_blocked(
    tmp_path: Path, field: str, value: str, code: str
) -> None:
    private_path, public_path, _ = _key_files(tmp_path, "trusted")
    manifest_path, _ = _signed_bundle(tmp_path / "evidence", private_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if field == "product_ref":
        payload[field] = value
    else:
        payload["signature"][field] = value
    manifest_path.write_bytes(canonical_bytes(payload))

    problems = verify_bundle(
        FilesystemArtifactStore(tmp_path / "evidence"),
        RUN,
        verifier=load_ed25519_trust_store([public_path]),
    )

    assert [problem.code for problem in problems] == [code]


def test_cli_signs_and_reports_independent_signature_verification(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    private_path, public_path, _ = _key_files(tmp_path, "trusted")
    case = Case(tmp_path / "case").with_candidate()

    assert main(_verify_argv(case, "--signing-key", str(private_path), "--json")) == 0
    evidence = case.root / "evidence"
    capsys.readouterr()

    assert main(["decision", "evidence", str(evidence), "--json"]) == 4
    untrusted = json.loads(capsys.readouterr().out)
    assert untrusted["signature_verified"] is False
    assert [problem["code"] for problem in untrusted["problems"]] == [
        "evidence.signature_untrusted"
    ]

    assert (
        main(
            [
                "decision",
                "evidence",
                str(evidence),
                "--trust-key",
                str(public_path),
                "--json",
            ]
        )
        == 0
    )
    trusted = json.loads(capsys.readouterr().out)
    assert trusted["verdict"] == Verdict.PASS.value
    assert trusted["signature_verified"] is True
    assert trusted["bundle"]["integrity"] == EvidenceIntegrity.SIGNED.value


def test_cli_refuses_signed_artifact_without_the_trust_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    private_path, public_path, _ = _key_files(tmp_path, "trusted")
    case = Case(tmp_path / "case").with_candidate()
    assert main(_verify_argv(case, "--signing-key", str(private_path), "--json")) == 0
    capsys.readouterr()
    evidence = case.root / "evidence"
    manifest_digest = digest_bytes(next(evidence.iterdir()).joinpath("manifest.json").read_bytes())

    assert (
        main(
            [
                "decision",
                "evidence",
                str(evidence),
                "--artifact",
                "check-semantic.diff",
                "--expect-digest",
                manifest_digest,
            ]
        )
        == EXIT_ERROR
    )
    assert "trust root" in capsys.readouterr().err

    assert (
        main(
            [
                "decision",
                "evidence",
                str(evidence),
                "--artifact",
                "check-semantic.diff",
                "--expect-digest",
                manifest_digest,
                "--trust-key",
                str(public_path),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["changes"] == []


def test_a_trusted_signature_cannot_override_an_unexpected_artifact_pin(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    private_path, public_path, _ = _key_files(tmp_path, "trusted")
    case = Case(tmp_path / "case").with_candidate()
    assert main(_verify_argv(case, "--signing-key", str(private_path), "--json")) == 0
    evidence = case.root / "evidence"
    capsys.readouterr()

    assert (
        main(
            [
                "decision",
                "evidence",
                str(evidence),
                "--artifact",
                "check-semantic.diff",
                "--expect-digest",
                "sha256:" + "0" * 64,
                "--trust-key",
                str(public_path),
            ]
        )
        == 4
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "evidence.digest_unexpected" in captured.err


def test_cli_reports_an_unreadable_private_key_without_echoing_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad_key = tmp_path / "private.pem"
    secret = "super-secret-private-key-material"
    bad_key.write_text(secret, encoding="utf-8")
    case = Case(tmp_path / "case").with_candidate()

    assert main(_verify_argv(case, "--signing-key", str(bad_key), "--json")) == EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "private.pem" in captured.err
    assert secret not in captured.err
