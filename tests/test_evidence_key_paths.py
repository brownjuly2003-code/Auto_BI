"""Key read refusals stay operational errors before evidence work starts."""

from pathlib import Path

import pytest

import auto_bi.decision.use_cases as use_cases
from auto_bi.cli import main
from auto_bi.evidence.model import EvidenceSignature
from auto_bi.evidence.signing import (
    EvidenceSignatureError,
    load_ed25519_signer,
    load_ed25519_trust_store,
)
from tests.test_evidence_signing import _key_files


@pytest.mark.parametrize("private", [True, False], ids=["private", "trust"])
@pytest.mark.parametrize("name", ["ключ.pem", "key-\ud800\n.pem"], ids=["unicode", "unprintable"])
@pytest.mark.parametrize("encoding_error", [True, False], ids=["encoding", "os-error"])
@pytest.mark.parametrize("cli", [False, True], ids=["loader", "cli"])
def test_key_read_refusal_is_safe_and_stops_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    private: bool,
    name: str,
    encoding_error: bool,
    cli: bool,
) -> None:
    path = tmp_path / name
    later = tmp_path / "later.pem"
    secret = "exception-detail-must-not-be-printed"
    error = (
        UnicodeEncodeError("utf-8", "\ud800", 0, 1, secret)
        if encoding_error
        else PermissionError(secret)
    )
    reads: list[Path] = []

    def read_bytes(source: Path) -> bytes:
        reads.append(source)
        assert source == path, "must not read another file after the key refusal"
        raise error

    def unexpected_work(*args: object, **kwargs: object) -> None:
        pytest.fail("evidence work must not start after the key refusal")

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(use_cases, "verify", unexpected_work)
    monkeypatch.setattr(use_cases, "locate_evidence", unexpected_work)
    shown = str(path) if name.isprintable() else repr(str(path))
    kind = "private key" if private else "public trust key"
    detail = f"cannot read Ed25519 {kind} {shown}"

    if cli:
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
            argv = [
                "decision",
                "evidence",
                str(output),
                "--trust-key",
                str(path),
                "--trust-key",
                str(later),
            ]
        assert main(argv) == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == f"error: {detail}\n"
        assert not output.exists()
    else:
        with pytest.raises(EvidenceSignatureError) as caught:
            if private:
                load_ed25519_signer(path)
            else:
                load_ed25519_trust_store([path, later])
        assert caught.value.code == (
            "evidence.signing_key_unreadable" if private else "evidence.trust_key_unreadable"
        )
        assert str(caught.value) == detail
        assert caught.value.__cause__ is error
    assert reads == [path]


@pytest.mark.parametrize("private", [True, False])
def test_unexpected_key_read_errors_are_not_disguised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, private: bool
) -> None:
    error = RuntimeError("unexpected read failure")

    def read_bytes(source: Path) -> bytes:
        raise error

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    with pytest.raises(RuntimeError) as caught:
        if private:
            load_ed25519_signer(tmp_path / "key.pem")
        else:
            load_ed25519_trust_store([tmp_path / "key.pem"])
    assert caught.value is error


def test_printable_unicode_key_paths_still_sign_and_verify(tmp_path: Path) -> None:
    private, public, _ = _key_files(tmp_path, "ключ")
    signer = load_ed25519_signer(private)
    trust = load_ed25519_trust_store([public])
    payload = b"canonical evidence"
    trust.verify(
        payload,
        EvidenceSignature(
            algorithm=signer.algorithm,
            key_id=signer.key_id,
            value=signer.sign(payload),
            signed_at="2026-10-23T00:00:00Z",
        ),
    )
