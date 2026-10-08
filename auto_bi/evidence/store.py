"""Artifact storage port and its filesystem adapter (DECISIONOPS_ARCHITECTURE §9.2).

The store owns the paths. A caller names an artifact; where its bytes land inside
``evidence/<run_id>/`` is the store's decision, and the store re-checks confinement even
though the manifest model already validated the path shape. The two checks answer different
questions: the model asks "is this a well-formed bundle-relative name?", the store asks "does
this resolve inside this run's directory on *this* filesystem?" — which is where symlinks,
case-insensitivity and existing files live.

The MVP writes to the local filesystem. An object-storage adapter arrives with pilot
requirements, behind this same protocol (§11).
"""

from __future__ import annotations

import errno
import os
import re
import stat
import uuid
from pathlib import Path
from typing import Protocol

from auto_bi import filesystem

_MAX_SEGMENTS = 8
#: A run id is one plain directory name, the same shape the manifest declares it in
#: (`scalars.Slug`). Enforced here because the confinement check below is rooted *at* the
#: run directory: a run id of `../..` would move the root itself, and every path inside it
#: would then resolve cleanly into somewhere else entirely.
_RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*\Z")


class ArtifactStoreError(Exception):
    """Refused write or read — never a silent fallback to another location."""


class ArtifactRefusalError(ArtifactStoreError):
    """The filesystem refused the store a look, a read or a write.

    Told from what the store says itself because the two are different answers to whoever
    grades a bundle. An artifact the store says is missing is a finding about the bundle. A
    path that may not be looked at or read is none: the artifact is there or it is not, and
    this reader was not let know. `refusal` is what the operating system raised, and it
    names the path.
    """

    def __init__(self, message: str, refusal: OSError) -> None:
        super().__init__(message)
        self.refusal = refusal


class ArtifactStore(Protocol):
    """Port used by the bundle writer and validator."""

    def put(self, run_id: str, path: str, payload: bytes) -> None: ...

    def get(self, run_id: str, path: str) -> bytes: ...

    def exists(self, run_id: str, path: str) -> bool: ...


class FilesystemArtifactStore:
    """Filesystem adapter rooted at *root*; one directory per run."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def run_root(self, run_id: str) -> Path:
        if not _RUN_ID_RE.match(run_id):
            raise ArtifactStoreError(
                f"run id must be one plain lowercase name ([a-z0-9._-]), got {run_id!r}"
            )
        return self._root / run_id

    # What the filesystem refuses is this store's answer too: a caller that handles
    # `ArtifactStoreError` is not left with an `OSError` from under it. Looking at a path can
    # be refused as well as writing to it: under a directory that may not be searched the
    # look itself is refused. The looks are the operating system's (`auto_bi.filesystem`),
    # not `pathlib`'s, which from Python 3.14 on answers "no" to such a refusal.

    def put(self, run_id: str, path: str, payload: bytes) -> None:
        staged_path: Path | None = None
        try:
            target = self._resolve(run_id, path, for_write=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                existing_mode = stat.S_IMODE(target.stat().st_mode)
            except FileNotFoundError:
                existing_mode = None
            candidate = target.with_name(f".artifact-{uuid.uuid4().hex}")
            if existing_mode is not None and os.name == "nt":
                if not existing_mode & stat.S_IWRITE:
                    # A read-only target refuses a write, while a read-only staged file
                    # cannot be cleaned up after a failed Windows replacement.
                    with target.open("ab"):
                        pass
                    raise PermissionError(errno.EACCES, "artifact is read-only", str(target))
                descriptor = _open_windows_staged(target, candidate)
            else:
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
                descriptor = os.open(candidate, flags, 0o600)
            staged_path = candidate
            try:
                staged = os.fdopen(descriptor, "wb")
            except OSError:
                os.close(descriptor)
                raise
            with staged:
                staged.write(payload)
            if existing_mode is not None:
                candidate.chmod(existing_mode)
            os.replace(candidate, target)
            staged_path = None
        except OSError as exc:
            detail = f"cannot write {path!r} of run {run_id!r}: {type(exc).__name__}"
            if staged_path is not None:
                try:
                    staged_path.unlink(missing_ok=True)
                except OSError as cleanup_exc:
                    detail += (
                        f"; cannot remove temporary file {str(staged_path)!r}: "
                        f"{type(cleanup_exc).__name__}"
                    )
            raise ArtifactRefusalError(detail, exc) from exc

    def get(self, run_id: str, path: str) -> bytes:
        try:
            return self._resolve(run_id, path, for_write=False).read_bytes()
        except OSError as exc:
            if filesystem.not_there(exc):  # it went away between the look and the read
                raise ArtifactStoreError(_missing(run_id, path)) from exc
            raise ArtifactRefusalError(
                f"cannot read {path!r} of run {run_id!r}: {type(exc).__name__}", exc
            ) from exc

    def exists(self, run_id: str, path: str) -> bool:
        try:
            self._resolve(run_id, path, for_write=False)
        except ArtifactStoreError:
            return False
        except OSError as exc:
            # Not "no": whether the artifact is there was not established.
            raise ArtifactRefusalError(
                f"cannot look for {path!r} of run {run_id!r}: {type(exc).__name__}", exc
            ) from exc
        return True

    def _resolve(self, run_id: str, path: str, *, for_write: bool) -> Path:
        if not path or path.startswith("/") or "\\" in path:
            raise ArtifactStoreError(f"artifact path must be bundle-relative, got {path!r}")
        segments = path.split("/")
        if len(segments) > _MAX_SEGMENTS or any(segment in ("", ".", "..") for segment in segments):
            raise ArtifactStoreError(f"artifact path is not confined to the run: {path!r}")

        base = self.run_root(run_id)
        target = base.joinpath(*segments)

        # Components are checked before resolution: a symlink is the specific diagnosis, and
        # one pointing back inside the run resolves cleanly while still not being ours.
        current = base
        for segment in segments[:-1]:
            current = current / segment
            if filesystem.is_link(current):
                raise ArtifactStoreError(f"artifact path crosses a symlink: {current}")
        if filesystem.is_link(target):
            raise ArtifactStoreError(f"artifact path is a symlink: {target}")

        if not target.resolve().is_relative_to(base.resolve()):
            raise ArtifactStoreError(f"artifact path escapes the run directory: {path!r}")
        # An artifact is a file. A path that holds a directory, or anything else that is no
        # regular file, holds no artifact — and is not opened to find that out.
        if not for_write and not filesystem.is_file(target):
            raise ArtifactStoreError(_missing(run_id, path))
        return target


def _missing(run_id: str, path: str) -> str:
    return f"artifact {path!r} of run {run_id!r} is missing"


def _open_windows_staged(source: Path, destination: Path) -> int:
    """Create staging with the existing owner and DACL from its first instant."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    owner_and_dacl = 0x00000001 | 0x00000004
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    get_security = advapi32.GetFileSecurityW
    get_security.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    get_security.restype = wintypes.BOOL

    def refused(path: Path) -> OSError:
        exc = ctypes.WinError(ctypes.get_last_error())
        exc.filename = str(path)
        return exc

    size = wintypes.DWORD()
    get_security(str(source), owner_and_dacl, None, 0, ctypes.byref(size))
    if ctypes.get_last_error() != 122:  # ERROR_INSUFFICIENT_BUFFER
        raise refused(source)
    descriptor = ctypes.create_string_buffer(size.value)
    if not get_security(str(source), owner_and_dacl, descriptor, size.value, ctypes.byref(size)):
        raise refused(source)

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", wintypes.LPVOID),
            ("bInheritHandle", wintypes.BOOL),
        ]

    attributes = SecurityAttributes(
        ctypes.sizeof(SecurityAttributes), ctypes.cast(descriptor, wintypes.LPVOID), False
    )
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(SecurityAttributes),
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(destination),
        0x40000000,  # GENERIC_WRITE
        7,  # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
        ctypes.byref(attributes),
        1,  # CREATE_NEW
        0x80,  # FILE_ATTRIBUTE_NORMAL
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise refused(destination)
    try:
        return msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
    except OSError:
        close_handle(handle)
        destination.unlink(missing_ok=True)
        raise
