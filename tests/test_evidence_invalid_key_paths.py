"""Readable invalid-key files cannot add lines to key refusal diagnostics."""

from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

import auto_bi.decision.use_cases as use_cases
from auto_bi.cli import main
from auto_bi.evidence.signing import (
    EvidenceSignatureError,
    load_ed25519_signer,
    load_ed25519_trust_store,
)


@pytest.mark.parametrize("private", [True, False], ids=["private", "trust"])
@pytest.mark.parametrize("wrong_type", [False, True], ids=["malformed-pem", "ec-key"])
@pytest.mark.parametrize("name", ["ключ.pem", "ключ\u2028name.pem"], ids=["unicode", "separator"])
def test_invalid_key_path_is_literal_in_loader_and_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    private: bool,
    wrong_type: bool,
    name: str,
) -> None:
    path = tmp_path / name
    payload = b"invalid-pem-material-must-not-be-printed"
    if wrong_type:
        # Deterministic test-only key; exercise the real parser's non-Ed25519 branch.
        key = ec.derive_private_key(1, ec.SECP256R1())
        payload = (
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            if private
            else key.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        )
    path.write_bytes(payload)
    shown = str(path) if name.isprintable() else repr(str(path))
    if wrong_type:
        kind = "private key" if private else "public trust key"
        detail = f"{kind} {shown} is not Ed25519"
    else:
        kind = "PKCS#8 private key" if private else "public trust key"
        detail = f"cannot load Ed25519 {kind} {shown}"

    with pytest.raises(EvidenceSignatureError) as caught:
        if private:
            load_ed25519_signer(path)
        else:
            load_ed25519_trust_store([path])
    assert caught.value.code == (
        "evidence.signing_key_invalid" if private else "evidence.trust_key_invalid"
    )
    assert str(caught.value) == detail
    if wrong_type:
        assert caught.value.__cause__ is None
    else:
        assert isinstance(caught.value.__cause__, ValueError)

    def unexpected_work(*args: object, **kwargs: object) -> None:
        pytest.fail("invalid keys must prevent evidence work")

    monkeypatch.setattr(use_cases, "verify", unexpected_work)
    monkeypatch.setattr(use_cases, "locate_evidence", unexpected_work)
    output = tmp_path / "evidence"
    if private:
        argv = [
            "decision",
            "verify",
            str(tmp_path / "contracts"),
            "--base",
            str(tmp_path / "base"),
            "--candidate",
            str(tmp_path / "candidate"),
            "--evidence",
            str(output),
            "--signing-key",
            str(path),
        ]
    else:
        argv = ["decision", "evidence", str(output), "--trust-key", str(path)]
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: {detail}\n"
    assert len(captured.err.splitlines()) == 1
    assert not output.exists()
    assert path.read_bytes() == payload
