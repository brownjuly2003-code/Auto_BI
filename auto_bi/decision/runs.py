"""Read-only discovery of local runs, without evidence integrity acceptance."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Protocol

from auto_bi import filesystem
from auto_bi.evidence.canonical import digest_bytes
from auto_bi.evidence.manifest import MANIFEST_PATH, read_bundle
from auto_bi.evidence.store import ArtifactStoreError, FilesystemArtifactStore
from auto_bi.strict import StrictModel


class RunSummary(StrictModel):
    run_id: str
    manifest_run_id: str
    product_ref: str
    created_at: str
    manifest_digest: str
    integrity_checked: Literal[False] = False


class RunDiscoveryError(Exception):
    """Discovery could not produce a complete catalogue."""


class RunRepository(Protocol):
    def list_runs(self) -> list[RunSummary]: ...


class FilesystemRunRepository:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def list_runs(self) -> list[RunSummary]:
        store = FilesystemArtifactStore(self.root)
        rows: list[RunSummary] = []
        current = self.root
        try:
            for name in sorted(os.listdir(self.root)):
                current = self.root / name
                if not filesystem.is_dir(current):
                    continue
                current = current / MANIFEST_PATH
                # A missing seal is an unfinished run; an existing non-file is an error.
                if not filesystem.exists(current) and not filesystem.is_link(current):
                    continue
                raw = store.get(name, MANIFEST_PATH)
                bundle = read_bundle(store, name, raw=raw)
                rows.append(
                    RunSummary(
                        run_id=name,
                        manifest_run_id=bundle.run_id,
                        product_ref=bundle.product_ref,
                        created_at=bundle.created_at,
                        manifest_digest=digest_bytes(raw),
                    )
                )
        except (OSError, ValueError, RecursionError, ArtifactStoreError) as exc:
            # Validation details may contain manifest data; report only location and class.
            raise RunDiscoveryError(
                f"cannot discover runs at {filesystem.display_path(current)}: {type(exc).__name__}"
            ) from exc
        return rows
