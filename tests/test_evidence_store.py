"""Writing and re-verifying an evidence bundle (ARCHITECTURE §9.2–§9.3, bootstrap task 6).

The bundle is the artifact a release decision is defended with, so writing it is not just
serialization. Paths are built by the store, never by a caller; the same inputs must
produce the same bytes; a claim without its evidence never reaches disk; and a restricted
payload lives in the restricted store while the public manifest carries only its digest.

Re-verification is the other half: a bundle read back from disk must still hash to what it
claims, or it is not evidence.
"""

from __future__ import annotations

import builtins
import io
import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from auto_bi.evidence.canonical import digest_bytes, sha256_digest
from auto_bi.evidence.manifest import (
    BundleWriter,
    EvidenceConflictError,
    read_bundle,
    verify_bundle,
)
from auto_bi.evidence.model import ClaimType, EvidenceClassification, EvidenceIntegrity
from auto_bi.evidence.store import ArtifactRefusalError, ArtifactStoreError, FilesystemArtifactStore

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


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX file modes")
@pytest.mark.parametrize("mode", [0o600, 0o640], ids=["owner-only", "owner-group-read"])
def test_put_preserves_existing_restrictive_mode_under_umask(tmp_path: Path, mode: int) -> None:
    store = FilesystemArtifactStore(tmp_path)
    target = tmp_path / RUN / "checks" / "one.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old")
    target.chmod(mode)

    previous_umask = os.umask(0o022)
    try:
        store.put(RUN, "checks/one.json", b"new")
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE(target.stat().st_mode) == mode


@pytest.mark.skipif(os.name != "nt", reason="requires Windows read-only replacement semantics")
def test_read_only_target_refusal_preserves_bytes_and_cleans_staging(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path)
    target = tmp_path / RUN / "checks" / "one.json"
    target.parent.mkdir(parents=True)
    old = b"sealed"
    target.write_bytes(old)
    target.chmod(0o444)

    try:
        with pytest.raises(ArtifactRefusalError):
            store.put(RUN, "checks/one.json", b"replacement")

        assert target.read_bytes() == old
        assert list((tmp_path / RUN).rglob(".artifact-*")) == []
    finally:
        target.chmod(0o666)
        for staged in (tmp_path / RUN).rglob(".artifact-*"):
            staged.chmod(0o666)


@pytest.mark.skipif(os.name != "nt", reason="requires Windows file ACLs")
def test_put_preserves_existing_windows_dacl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FilesystemArtifactStore(tmp_path)
    parent = tmp_path / RUN / "checks"
    parent.mkdir(parents=True)
    subprocess.run(
        ["icacls", str(parent), "/grant", "*S-1-1-0:(OI)(CI)(RX)"],
        check=True,
        capture_output=True,
    )
    target = parent / "one.json"
    target.write_bytes(b"old")
    sid = subprocess.check_output(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "[Security.Principal.WindowsIdentity]::GetCurrent().User.Value",
        ],
        text=True,
    ).strip()
    subprocess.run(
        ["icacls", str(target), "/grant:r", f"*{sid}:(F)"], check=True, capture_output=True
    )
    subprocess.run(["icacls", str(target), "/inheritance:r"], check=True, capture_output=True)
    before = subprocess.check_output(["icacls", str(target)], text=True)
    staging_acls: list[str] = []
    real_fdopen = os.fdopen

    class ObserveAclAtWrite:
        def __init__(self, handle: Any) -> None:
            self.handle = handle

        def __enter__(self) -> ObserveAclAtWrite:
            self.handle.__enter__()
            return self

        def __exit__(self, *args: Any) -> Any:
            return self.handle.__exit__(*args)

        def write(self, data: bytes) -> int:
            staged = next(parent.glob(".artifact-*"))
            acl = subprocess.check_output(["icacls", str(staged)], text=True)
            staging_acls.append(acl.replace(str(staged), "", 1))
            return int(self.handle.write(data))

        def __getattr__(self, name: str) -> Any:
            return getattr(self.handle, name)

    def fdopen_with_acl_observation(fd: int, mode: str) -> ObserveAclAtWrite:
        return ObserveAclAtWrite(real_fdopen(fd, mode))

    monkeypatch.setattr(os, "fdopen", fdopen_with_acl_observation)

    store.put(RUN, "checks/one.json", b"new")

    assert target.read_bytes() == b"new"
    assert subprocess.check_output(["icacls", str(target)], text=True) == before
    assert staging_acls == [before.replace(str(target), "", 1)]
    subprocess.run(
        ["icacls", str(parent), "/grant", "*S-1-1-0:(OI)(CI)(M)"],
        check=True,
        capture_output=True,
    )
    assert subprocess.check_output(["icacls", str(target)], text=True) == before


@pytest.mark.skipif(os.name != "nt", reason="requires Windows file ACLs")
def test_replacement_staging_never_exposes_bytes_before_dacl_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    store = FilesystemArtifactStore(tmp_path)
    parent = tmp_path / RUN / "checks"
    parent.mkdir(parents=True)
    subprocess.run(
        ["icacls", str(parent), "/grant", "*S-1-1-0:(OI)(CI)(RX)"],
        check=True,
        capture_output=True,
    )
    target = parent / "one.json"
    target.write_bytes(b"old")
    subprocess.run(
        ["icacls", str(target), "/deny", "*S-1-1-0:(RD)"],
        check=True,
        capture_output=True,
    )

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    invalid_handle = ctypes.c_void_p(-1).value
    real_fdopen = os.fdopen
    reader_fds: list[int] = []
    attempts = 0

    def fdopen_with_early_reader(fd: int, mode: str) -> Any:
        nonlocal attempts
        attempts += 1
        staged = next(parent.glob(".artifact-*"))
        handle = create_file(str(staged), 0x80000000, 7, None, 3, 0, None)
        if handle == invalid_handle:
            assert ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED
        else:
            reader_fds.append(msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY))
        return real_fdopen(fd, mode)

    monkeypatch.setattr(os, "fdopen", fdopen_with_early_reader)
    try:
        store.put(RUN, "checks/one.json", b"restricted-new-payload")
        leaked = [os.read(fd, 100) for fd in reader_fds]
    finally:
        for fd in reader_fds:
            os.close(fd)
        subprocess.run(
            ["icacls", str(target), "/remove:d", "*S-1-1-0"],
            check=True,
            capture_output=True,
        )

    assert attempts == 1
    assert leaked == []


class _ModeAtWrite:
    def __init__(self, handle: Any, modes: list[int]) -> None:
        self._handle = handle
        self._modes = modes

    def __enter__(self) -> _ModeAtWrite:
        self._handle.__enter__()
        return self

    def __exit__(self, *args: Any) -> Any:
        return self._handle.__exit__(*args)

    def write(self, data: bytes | str) -> int:
        self._modes.append(stat.S_IMODE(os.fstat(self._handle.fileno()).st_mode))
        return int(self._handle.write(data))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._handle, name)


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX file modes")
def test_staging_file_is_private_at_payload_write_under_umask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FilesystemArtifactStore(tmp_path)
    modes: list[int] = []
    real_io_open = io.open

    def open_for_mode(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        handle = real_io_open(file, mode, *args, **kwargs)
        if any(flag in mode for flag in ("w", "x", "a")):
            return _ModeAtWrite(handle, modes)
        return handle

    def fdopen_for_mode(fd: int, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        handle = real_io_open(fd, mode, *args, **kwargs)
        if any(flag in mode for flag in ("w", "x", "a")):
            return _ModeAtWrite(handle, modes)
        return handle

    monkeypatch.setattr(io, "open", open_for_mode)
    monkeypatch.setattr(builtins, "open", open_for_mode)
    monkeypatch.setattr(os, "fdopen", fdopen_for_mode)

    previous_umask = os.umask(0o022)
    try:
        store.put(RUN, "checks/one.json", b"payload")
    finally:
        os.umask(previous_umask)

    assert modes == [0o600]


class _PrefixThenFail:
    def __init__(self, handle: Any) -> None:
        self._handle = handle

    def __enter__(self) -> _PrefixThenFail:
        self._handle.__enter__()
        return self

    def __exit__(self, *args: Any) -> Any:
        return self._handle.__exit__(*args)

    def write(self, data: bytes | str) -> int:
        self._handle.write(data[:8])
        self._handle.flush()
        raise OSError("manifest write failed")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._handle, name)


def _fail_after_prefix(monkeypatch: pytest.MonkeyPatch, failed_write: int = 1) -> None:
    real_open = io.open
    writes = 0

    def wrap_for_partial_failure(handle: Any, mode: str) -> Any:
        nonlocal writes
        if any(flag in mode for flag in ("w", "x", "a")):
            writes += 1
            if writes == failed_write:
                return _PrefixThenFail(handle)
        return handle

    def open_for_partial_failure(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        handle = real_open(file, mode, *args, **kwargs)
        return wrap_for_partial_failure(handle, mode)

    def fdopen_for_partial_failure(fd: int, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        handle = real_open(fd, mode, *args, **kwargs)
        return wrap_for_partial_failure(handle, mode)

    monkeypatch.setattr(io, "open", open_for_partial_failure)
    monkeypatch.setattr(builtins, "open", open_for_partial_failure)
    monkeypatch.setattr(os, "fdopen", fdopen_for_partial_failure)


def test_a_partial_new_manifest_write_leaves_no_file_and_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder = full_bundle(tmp_path)
    bundle = builder.build()
    manifest = tmp_path / RUN / "manifest.json"
    run_root = tmp_path / RUN
    artifact_files = {run_root / artifact.path for artifact in bundle.artifacts}

    with monkeypatch.context() as failure:
        _fail_after_prefix(failure, len(bundle.artifacts) + 1)
        with pytest.raises(ArtifactRefusalError) as caught:
            builder.write()

    assert isinstance(caught.value.refusal, OSError)
    assert not manifest.exists()
    assert {path for path in run_root.rglob("*") if path.is_file()} == artifact_files

    builder.write()
    assert verify_bundle(FilesystemArtifactStore(tmp_path), RUN) == []


def test_a_partial_manifest_write_preserves_existing_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FilesystemArtifactStore(tmp_path)
    old = b'{"bundle":"sealed","status":"old"}\n'
    payload = b'{"bundle":"new","status":"sealed"}\n'
    store.put(RUN, "manifest.json", old)
    manifest = tmp_path / RUN / "manifest.json"

    with monkeypatch.context() as failure:
        _fail_after_prefix(failure)
        with pytest.raises(ArtifactRefusalError) as caught:
            store.put(RUN, "manifest.json", payload)

    assert isinstance(caught.value.refusal, OSError)
    assert manifest.read_bytes() == old
    assert set(manifest.parent.iterdir()) == {manifest}


@pytest.mark.parametrize(
    "old", [None, b'{"bundle":"sealed","status":"old"}\n'], ids=["absent", "existing"]
)
def test_a_publish_failure_preserves_the_manifest_and_removes_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, old: bytes | None
) -> None:
    store = FilesystemArtifactStore(tmp_path)
    if old is not None:
        store.put(RUN, "manifest.json", old)
    manifest = tmp_path / RUN / "manifest.json"
    payload = b'{"bundle":"new","status":"sealed"}\n'

    def fail_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        assert Path(target) == manifest
        raise OSError("manifest publish failed")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(ArtifactRefusalError) as caught:
        store.put(RUN, "manifest.json", payload)

    assert isinstance(caught.value, ArtifactRefusalError)
    assert isinstance(caught.value.refusal, OSError)
    if old is None:
        assert not manifest.exists()
    else:
        assert manifest.read_bytes() == old
    remaining = set(manifest.parent.iterdir()) if manifest.parent.exists() else set()
    assert remaining == ({manifest} if old is not None else set())


def test_rewriting_the_same_bundle_under_its_run_id_is_a_no_op(tmp_path: Path) -> None:
    """A run id is the idempotency key: the same verification lands on the same bytes."""
    first = full_bundle(tmp_path).write()
    again = full_bundle(tmp_path).write()

    assert again.digest == first.digest


def test_a_different_bundle_never_overwrites_a_sealed_one(tmp_path: Path) -> None:
    """Evidence is not replaced silently: same key, other content, refused before any write."""
    first = full_bundle(tmp_path).write()

    other = full_bundle(tmp_path)
    other.add_check("cost", {"status": "pass", "summary_code": "cost.within_budget"})
    with pytest.raises(EvidenceConflictError, match="refusing to overwrite"):
        other.write()

    manifest = (tmp_path / RUN / "manifest.json").read_bytes()
    assert digest_bytes(manifest) == first.digest
    assert verify_bundle(FilesystemArtifactStore(tmp_path), RUN) == []
