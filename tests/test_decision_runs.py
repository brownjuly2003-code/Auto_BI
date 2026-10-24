"""The run catalogue discovers manifests; it does not accept evidence."""

import errno
import json
import os
import sys
from pathlib import Path

import pytest

from auto_bi.cli import main
from auto_bi.evidence.canonical import digest_bytes
from auto_bi.evidence.manifest import BundleWriter
from auto_bi.evidence.store import FilesystemArtifactStore
from tests.test_evidence_inspect import manifest_past_json_parser_depth
from tests.test_evidence_run_root import _redirect
from tests.test_evidence_signing import _key_files, _signed_bundle


def seal(root: Path, run_id: str) -> bytes:
    BundleWriter(
        store=FilesystemArtifactStore(root),
        run_id=run_id,
        product_ref="decision://revenue@1",
        verifier_version="test",
        created_at="2026-10-23T00:00:00Z",
    ).write()
    return (root / run_id / "manifest.json").read_bytes()


def test_cli_lists_sorted_manifest_snapshots(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raws = {name: seal(tmp_path, name) for name in ["z-run", "a-run"]}
    assert main(["decision", "runs", str(tmp_path), "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows == [
        dict(
            run_id=name,
            manifest_run_id=name,
            product_ref="decision://revenue@1",
            created_at="2026-10-23T00:00:00Z",
            manifest_digest=digest_bytes(raws[name]),
            integrity_checked=False,
        )
        for name in sorted(raws)
    ]


def test_empty_and_unsealed_directories(tmp_path, capsys):
    (tmp_path / "unfinished").mkdir()
    (tmp_path / "note.txt").write_text("note", encoding="utf-8")
    assert main(["decision", "runs", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []


@pytest.mark.parametrize(
    "broken",
    [b"{", b"\xff", b"{}", None],
    ids=["json", "utf8", "schema", "deep-json"],
)
def test_invalid_manifest_has_no_partial_output(tmp_path, capsys, broken):
    if broken is None:
        broken = manifest_past_json_parser_depth(b"[", b"]")
    seal(tmp_path, "a-good")
    seal(tmp_path, "z-bad")
    (tmp_path / "z-bad" / "manifest.json").write_bytes(broken)
    assert main(["decision", "runs", str(tmp_path), "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "z-bad" in captured.err


def test_missing_root_is_not_empty_catalogue(tmp_path, capsys):
    assert main(["decision", "runs", str(tmp_path / "absent"), "--json"]) == 2
    assert capsys.readouterr().out == ""


def test_single_read_no_writes_and_distinct_ids(tmp_path, monkeypatch, capsys):
    raw = seal(tmp_path, "original")
    (tmp_path / "original").rename(tmp_path / "address")
    get = FilesystemArtifactStore.get
    calls = []

    def read(self, run_id, path):
        calls.append((run_id, path))
        return get(self, run_id, path)

    def forbidden(*args, **kwargs):
        pytest.fail("discovery must not write")

    monkeypatch.setattr(FilesystemArtifactStore, "get", read)
    monkeypatch.setattr(FilesystemArtifactStore, "put", forbidden)
    assert main(["decision", "runs", str(tmp_path), "--json"]) == 0
    row = json.loads(capsys.readouterr().out)[0]
    assert row["run_id"] == "address"
    assert row["manifest_run_id"] == "original"
    assert row["manifest_digest"] == digest_bytes(raw)
    assert calls == [("address", "manifest.json")]


def test_refused_read_has_no_partial_stdout(tmp_path, monkeypatch, capsys):
    seal(tmp_path, "a-good")
    seal(tmp_path, "z-refused")
    read = Path.read_bytes

    def refused(path):
        if path.parent.name == "z-refused":
            raise PermissionError("refused")
        return read(path)

    monkeypatch.setattr(Path, "read_bytes", refused)
    assert main(["decision", "runs", str(tmp_path), "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "z-refused" in captured.err


@pytest.mark.parametrize("options", [[], ["--json"]], ids=["text", "json"])
@pytest.mark.parametrize("operation", ["listdir", "run-stat", "manifest-stat", "manifest-lstat"])
def test_refused_discovery_metadata_has_no_partial_output(
    tmp_path, monkeypatch, capsys, options, operation
):
    seal(tmp_path, "a-good")
    run = tmp_path / "z-refused"
    run.mkdir()
    manifest = run / "manifest.json"
    target = tmp_path if operation == "listdir" else run if operation == "run-stat" else manifest
    name = (
        "listdir"
        if operation == "listdir"
        else "lstat" if operation == "manifest-lstat" else "stat"
    )
    original = getattr(os, name)
    refused = []
    reads = []
    get = FilesystemArtifactStore.get

    def refuse(path, *args, **kwargs):
        if Path(path) == target:
            refused.append(path)
            raise PermissionError(errno.EACCES, "private diagnostic must not leak", str(path))
        return original(path, *args, **kwargs)

    def read(self, run_id, path):
        reads.append((run_id, path))
        return get(self, run_id, path)

    with monkeypatch.context() as patch:
        patch.setattr(os, name, refuse)
        patch.setattr(FilesystemArtifactStore, "get", read)
        result = main(["decision", "runs", str(tmp_path), *options])

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert len(captured.err.splitlines()) == 1
    assert str(target) in captured.err
    assert "PermissionError" in captured.err
    assert "private diagnostic" not in captured.err
    assert len(refused) == 1
    assert reads == ([] if operation == "listdir" else [("a-good", "manifest.json")])


@pytest.mark.parametrize("options", [[], ["--json"]], ids=["text", "json"])
def test_manifest_directory_is_not_an_unsealed_run(tmp_path, capsys, options):
    seal(tmp_path, "a-good")
    manifest = tmp_path / "z-invalid" / "manifest.json"
    manifest.mkdir(parents=True)

    assert main(["decision", "runs", str(tmp_path), *options]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert len(captured.err.splitlines()) == 1
    assert str(manifest) in captured.err
    assert "ArtifactStoreError" in captured.err
    assert manifest.is_dir()


@pytest.mark.parametrize(
    "kind",
    [
        "symlink",
        pytest.param(
            "junction", marks=pytest.mark.skipif(sys.platform != "win32", reason="Windows junction")
        ),
    ],
)
def test_redirected_run_is_refused(tmp_path, capsys, kind):
    root = tmp_path / "root"
    root.mkdir()
    seal(tmp_path / "external", "sealed")
    link = root / "redirect"
    _redirect(link, tmp_path / "external" / "sealed", kind)
    try:
        assert main(["decision", "runs", str(root), "--json"]) == 2
        assert capsys.readouterr().out == ""
    finally:
        if kind == "junction":
            link.rmdir()
        else:
            link.unlink()


def test_text_catalogue_states_integrity_boundary(tmp_path, capsys):
    seal(tmp_path, "run")
    assert main(["decision", "runs", str(tmp_path)]) == 0
    text = capsys.readouterr().out
    assert "integrity_checked=false" in text
    assert "decision://revenue@1" in text


def test_signed_bundle_with_tampered_payload_is_only_discovered(tmp_path, monkeypatch, capsys):
    private_path, _, _ = _key_files(tmp_path, "key")
    root = tmp_path / "evidence"
    manifest, _ = _signed_bundle(root, private_path)
    raw = manifest.read_bytes()
    document = json.loads(raw)
    assert document["signature"] is not None
    payload = manifest.parent / document["artifacts"][0]["path"]
    payload.write_bytes(b"tampered")
    read = Path.read_bytes
    calls = []

    def manifest_only(path):
        calls.append(path)
        assert path == manifest
        return read(path)

    monkeypatch.setattr(Path, "read_bytes", manifest_only)
    assert main(["decision", "runs", str(root), "--json"]) == 0
    row = json.loads(capsys.readouterr().out)[0]
    assert row["integrity_checked"] is False
    assert row["manifest_digest"] == digest_bytes(raw)
    assert calls == [manifest]
