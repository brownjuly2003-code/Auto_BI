"""A JSON escape must not let non-Unicode claim text reach evidence readers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from auto_bi.cli import main
from auto_bi.evidence.canonical import digest_bytes
from auto_bi.evidence.inspect import EvidenceReadError, inspect_bundle, read_artifact
from auto_bi.evidence.manifest import BundleWriter, read_bundle
from auto_bi.evidence.model import Claim
from auto_bi.evidence.signing import load_ed25519_signer, load_ed25519_trust_store
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.verification.model import Verdict
from tests.test_evidence_bundle import claim
from tests.test_evidence_signing import _key_files
from tests.test_evidence_store import CREATED, PRODUCT, RUN


@pytest.mark.parametrize("field", ["unit", "caveats"])
@pytest.mark.parametrize("text", ["\ud800", "\udfff", "\ud83d\ude00"])
def test_claim_rejects_surrogate_codepoints(field: str, text: str) -> None:
    value = text if field == "unit" else [text]
    with pytest.raises(ValidationError):
        Claim.model_validate(claim(**{field: value}))


@pytest.mark.parametrize(
    "text", ["", " \t\n ", "\u0440\u0443\u0431\u043b\u0438", "e\u0301", "\U0001f600"]
)
def test_claim_unicode_is_preserved_without_normalization(text: str) -> None:
    # json.loads joins valid escaped surrogate pairs into one Unicode scalar.
    source = json.loads(json.dumps(claim(unit=text, caveats=[text]), ensure_ascii=True))
    parsed = Claim.model_validate(source)
    assert parsed.unit == text
    assert parsed.caveats == [text]
    assert parsed.model_dump(mode="json")["unit"] == text


@pytest.mark.parametrize("signed", [False, True])
def test_valid_json_surrogate_pair_preserves_artifact_and_signature(
    tmp_path: Path, signed: bool
) -> None:
    private_path, public_path, _ = _key_files(tmp_path, "trusted")
    trust = load_ed25519_trust_store([public_path]) if signed else None
    store = FilesystemArtifactStore(tmp_path / "evidence")
    writer = BundleWriter(
        store=store,
        run_id=RUN,
        product_ref=PRODUCT,
        verifier_version="test-unicode.1",
        created_at=CREATED,
        signer=load_ed25519_signer(private_path) if signed else None,
    )
    writer.add_check("freshness", {"status": "pass"})
    text = " \U0001f600 e\u0301\t "
    writer.add_claim(**claim(unit=text, caveats=[text], evidence_refs=["check-freshness"]))
    written = writer.write()
    assert inspect_bundle(store, RUN, verifier=trust).verdict is Verdict.PASS
    manifest = tmp_path / "evidence" / RUN / "manifest.json"
    original = manifest.read_bytes()
    escaped = original.replace("\U0001f600".encode(), b"\\ud83d\\ude00")
    assert escaped != original
    manifest.write_bytes(escaped)

    parsed = read_bundle(store, RUN)
    assert parsed.claims[0].unit == text
    assert parsed.claims[0].caveats == [text]
    assert parsed.canonical_bytes() == original
    inspection = inspect_bundle(store, RUN, verifier=trust)
    assert [problem.code for problem in inspection.problems] == ["evidence.manifest_not_canonical"]
    assert inspection.signature_verified is signed
    artifact, payload = read_artifact(
        store, RUN, "check-freshness", expected_digest=digest_bytes(escaped), verifier=trust
    )
    assert artifact == written.bundle.artifacts[0]
    assert payload == b'{"status":"pass"}'


@pytest.mark.parametrize("signed", [False, True])
@pytest.mark.parametrize("field", ["unit", "caveats"])
@pytest.mark.parametrize("text", ["private-claim-marker\ud800", "private-claim-marker\udfff"])
def test_evidence_refuses_surrogate_claim_text(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    signed: bool,
    field: str,
    text: str,
) -> None:
    private_path, public_path, _ = _key_files(tmp_path, "trusted")
    trust = load_ed25519_trust_store([public_path]) if signed else None
    trust_args = ["--trust-key", str(public_path)] if signed else []
    store = FilesystemArtifactStore(tmp_path / "evidence")
    writer = BundleWriter(
        store=store,
        run_id=RUN,
        product_ref=PRODUCT,
        verifier_version="test-unicode.1",
        created_at=CREATED,
        signer=load_ed25519_signer(private_path) if signed else None,
    )
    writer.add_check("freshness", {"status": "pass"})
    writer.add_claim(**claim(evidence_refs=["check-freshness"]))
    written = writer.write()
    assert inspect_bundle(store, RUN, verifier=trust).verdict is Verdict.PASS
    run_dir = tmp_path / "evidence" / RUN
    manifest = run_dir / "manifest.json"
    source = json.loads(manifest.read_bytes())
    source["claims"][0][field] = text if field == "unit" else [text]
    raw = json.dumps(source, ensure_ascii=True).encode("utf-8")
    manifest.write_bytes(raw)
    before = {path: path.read_bytes() for path in run_dir.rglob("*") if path.is_file()}

    with pytest.raises(ValidationError):
        read_bundle(store, RUN)
    inspection = inspect_bundle(store, RUN, verifier=trust)
    assert inspection.verdict is Verdict.BLOCK
    assert inspection.bundle is None
    assert inspection.manifest_digest == digest_bytes(raw)
    assert [problem.code for problem in inspection.problems] == ["evidence.manifest_invalid"]
    with pytest.raises(EvidenceReadError, match=r"no readable manifest\.json"):
        read_artifact(store, RUN, "check-freshness", verifier=trust)

    args = ["decision", "evidence", str(run_dir), *trust_args]
    assert main([*args, "--json"]) == 4
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out)["bundle"] is None
    assert "private-claim-marker" not in captured.out
    for pin, expected_exit in [(None, 2), (digest_bytes(raw), 2), (written.digest, 4)]:
        pin_args = ["--expect-digest", pin] if pin else []
        assert main([*args, "--artifact", "check-freshness", *pin_args]) == expected_exit
        captured = capsys.readouterr()
        assert captured.out == ""
        assert len(captured.err.splitlines()) == 1
        assert "private-claim-marker" not in captured.err
        assert "Traceback" not in captured.err
        expected = "digest_unexpected" if expected_exit == 4 else "no readable manifest.json"
        assert expected in captured.err
    assert {path: path.read_bytes() for path in run_dir.rglob("*") if path.is_file()} == before
