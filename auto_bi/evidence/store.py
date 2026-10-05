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

import re
from pathlib import Path
from typing import Protocol

_MAX_SEGMENTS = 8
#: A run id is one plain directory name, the same shape the manifest declares it in
#: (`scalars.Slug`). Enforced here because the confinement check below is rooted *at* the
#: run directory: a run id of `../..` would move the root itself, and every path inside it
#: would then resolve cleanly into somewhere else entirely.
_RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*\Z")


class ArtifactStoreError(Exception):
    """Refused write or read — never a silent fallback to another location."""


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
    # look itself raises `PermissionError`.

    def put(self, run_id: str, path: str, payload: bytes) -> None:
        try:
            target = self._resolve(run_id, path, for_write=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        except OSError as exc:
            raise ArtifactStoreError(
                f"cannot write {path!r} of run {run_id!r}: {type(exc).__name__}"
            ) from exc

    def get(self, run_id: str, path: str) -> bytes:
        try:
            return self._resolve(run_id, path, for_write=False).read_bytes()
        except OSError as exc:
            raise ArtifactStoreError(f"cannot read {path!r} of run {run_id!r}") from exc

    def exists(self, run_id: str, path: str) -> bool:
        try:
            return self._resolve(run_id, path, for_write=False).is_file()
        except ArtifactStoreError:
            return False
        except OSError as exc:
            # Not "no": whether the artifact is there was not established.
            raise ArtifactStoreError(
                f"cannot look for {path!r} of run {run_id!r}: {type(exc).__name__}"
            ) from exc

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
            if current.is_symlink():
                raise ArtifactStoreError(f"artifact path crosses a symlink: {current}")
        if target.is_symlink():
            raise ArtifactStoreError(f"artifact path is a symlink: {target}")

        if not target.resolve().is_relative_to(base.resolve()):
            raise ArtifactStoreError(f"artifact path escapes the run directory: {path!r}")
        if not for_write and not target.exists():
            raise ArtifactStoreError(f"artifact {path!r} of run {run_id!r} is missing")
        return target
