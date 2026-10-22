"""A redirected run directory must not relocate an artifact store's boundary."""

from __future__ import annotations

import errno
import os
import sys
from pathlib import Path

import pytest

from auto_bi.evidence.store import (
    ArtifactRefusalError,
    ArtifactStoreError,
    FilesystemArtifactStore,
)
from tests.test_evidence_store import RUN, full_bundle


def _redirect(link: Path, target: Path, kind: str) -> None:
    if kind == "junction":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            if sys.platform == "win32" and exc.winerror == 1314:
                pytest.skip("Windows symbolic-link privilege is unavailable")
            raise


@pytest.mark.parametrize(
    "kind",
    [
        "symlink",
        pytest.param(
            "junction", marks=pytest.mark.skipif(sys.platform != "win32", reason="Windows junction")
        ),
    ],
)
@pytest.mark.parametrize("destination", ["outside", "sibling", "inside"])
@pytest.mark.parametrize("operation", ["get", "put", "exists"])
def test_store_refuses_redirected_run_root(
    tmp_path: Path, kind: str, destination: str, operation: str
) -> None:
    root = tmp_path / "store"
    root.mkdir()
    target = {
        "outside": tmp_path / "outside",
        "sibling": root / "other-run",
        "inside": root / "nested" / "target",
    }[destination]
    target.mkdir(parents=True)
    artifact = target / "manifest.json"
    artifact.write_bytes(b"original external bytes")
    link = root / "run-one"
    _redirect(link, target, kind)
    store = FilesystemArtifactStore(root)
    try:
        if operation == "exists":
            assert store.exists("run-one", "manifest.json") is False
        else:
            with pytest.raises(ArtifactStoreError, match=r"run directory.*(symlink|junction)"):
                if operation == "put":
                    store.put("run-one", "manifest.json", b"replacement")
                else:
                    store.get("run-one", "manifest.json")
        assert artifact.read_bytes() == b"original external bytes"
        assert sorted(path.name for path in target.iterdir()) == ["manifest.json"]
    finally:
        # Remove the redirect itself, never recurse into its destination.
        if kind == "junction":
            link.rmdir()
        else:
            link.unlink()


@pytest.mark.parametrize("operation", ["get", "put", "exists"])
def test_run_root_lookup_refusal_is_not_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    store = FilesystemArtifactStore(tmp_path)
    store.put("run-one", "manifest.json", b"original")
    base = tmp_path / "run-one"
    original = os.lstat
    refusal = PermissionError(errno.EACCES, "private diagnostic", str(base))

    def denied(path: object, *args: object, **kwargs: object) -> os.stat_result:
        if path == base:
            raise refusal
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", denied)
    with pytest.raises(ArtifactRefusalError) as caught:
        if operation == "put":
            store.put("run-one", "manifest.json", b"replacement")
        else:
            getattr(store, operation)("run-one", "manifest.json")
    assert caught.value.refusal is refusal
    assert (base / "manifest.json").read_bytes() == b"original"


def test_new_run_round_trips_unchanged(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "not-created")
    store.put("run-one", "checks/check.json", b'{"status":"pass"}')
    assert store.exists("run-one", "checks/check.json")
    assert store.get("run-one", "checks/check.json") == b'{"status":"pass"}'


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction")
def test_configured_root_junction_preserves_bundle_bytes(tmp_path: Path) -> None:
    target = tmp_path / "chosen-location"
    target.mkdir()
    configured = tmp_path / "configured-store"
    _redirect(configured, target, "junction")
    try:
        direct = full_bundle(tmp_path / "direct").write()
        redirected = full_bundle(configured).write()
        assert redirected == direct
        expected = {
            path.relative_to(tmp_path / "direct" / RUN): path.read_bytes()
            for path in (tmp_path / "direct" / RUN).rglob("*")
            if path.is_file()
        }
        actual = {
            path.relative_to(target / RUN): path.read_bytes()
            for path in (target / RUN).rglob("*")
            if path.is_file()
        }
        assert actual == expected
    finally:
        configured.rmdir()
