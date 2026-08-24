"""Stable ids and version-pinned refs (DECISIONOPS_ARCHITECTURE §7.1).

An id names a contract — ``metric://sales/gross_margin_pct``. A ref pins the exact domain
version a run was verified against — ``metric://sales/gross_margin_pct@3``. Keeping them as
different field types is what stops a verified claim from silently re-pointing at a metric
whose formula changed after the evidence was written.

Refs are opaque strings on the wire (they canonicalize and diff as text); this module is
the single place that knows their grammar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BeforeValidator


class RefKind(StrEnum):
    """Schemes the contract kernel resolves. New kinds arrive with their first consumer."""

    METRIC = "metric"
    DECISION = "decision"
    DASHBOARD = "dashboard"
    POLICY = "policy"
    ASSET = "asset"
    SNAPSHOT = "snapshot"


class RefError(ValueError):
    """Malformed id/ref — never repaired by guessing (invariant 6)."""


# `\Z`, not `$`: Python's `$` also matches before a trailing newline, which would let an id
# ending in a newline validate — identical to the real one in any report, apart in every hash.
_REF_RE = re.compile(r"^(?P<kind>[a-z]+)://(?P<path>[^@]*)(?:@(?P<version>.*))?\Z", re.S)
_SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*\Z")
_VERSION_RE = re.compile(r"^[1-9][0-9]*\Z")


@dataclass(frozen=True, slots=True)
class ContractRef:
    """Parsed ``kind://segment/segment[@version]``."""

    kind: RefKind
    path: tuple[str, ...]
    version: int | None = None

    @classmethod
    def parse(cls, raw: object) -> ContractRef:
        if not isinstance(raw, str):
            raise RefError(f"ref must be a string, got {type(raw).__name__}")
        match = _REF_RE.match(raw)
        if not match:
            raise RefError(f"malformed ref {raw!r}: expected kind://path[@version]")
        try:
            kind = RefKind(match.group("kind"))
        except ValueError:
            known = ", ".join(sorted(k.value for k in RefKind))
            raise RefError(
                f"unknown ref kind {match.group('kind')!r} in {raw!r}; known kinds: {known}"
            ) from None
        segments = tuple(match.group("path").split("/"))
        if not segments or any(not _SEGMENT_RE.match(segment) for segment in segments):
            raise RefError(
                f"malformed path in {raw!r}: lowercase segments [a-z0-9][a-z0-9_-]*, '/'-separated"
            )
        version_text = match.group("version")
        if version_text is None:
            return cls(kind, segments, None)
        if not _VERSION_RE.match(version_text):
            raise RefError(f"malformed version pin in {raw!r}: expected @N with N >= 1")
        return cls(kind, segments, int(version_text))

    def with_version(self, version: int) -> ContractRef:
        if version < 1:
            raise RefError(f"domain versions start at 1, got {version}")
        return ContractRef(self.kind, self.path, version)

    def without_version(self) -> ContractRef:
        return ContractRef(self.kind, self.path, None)

    def __str__(self) -> str:
        base = f"{self.kind.value}://{'/'.join(self.path)}"
        return base if self.version is None else f"{base}@{self.version}"


def parse_ref(raw: object) -> ContractRef:
    """Parse *raw* into a :class:`ContractRef` or raise :class:`RefError`."""
    return ContractRef.parse(raw)


def _checker(kind: RefKind, *, pinned: bool) -> BeforeValidator:
    def check(value: object) -> str:
        try:
            ref = ContractRef.parse(value)
        except RefError as exc:
            raise ValueError(str(exc)) from None
        if ref.kind is not kind:
            raise ValueError(f"expected a {kind.value} ref, got {ref.kind.value}")
        if pinned and ref.version is None:
            raise ValueError(f"{kind.value} ref must pin a version: {ref}@N")
        if not pinned and ref.version is not None:
            raise ValueError(
                f"{kind.value} id must not carry a version pin: {ref.without_version()}"
            )
        return str(ref)

    return BeforeValidator(check)


def stable_id(kind: RefKind) -> Any:
    """Field type for an unversioned id of *kind* (``metric://sales/gross_margin_pct``)."""
    return Annotated[str, _checker(kind, pinned=False)]


def versioned_ref(kind: RefKind) -> Any:
    """Field type for a version-pinned ref of *kind* (``…@3``)."""
    return Annotated[str, _checker(kind, pinned=True)]


# Concrete aliases used by the contract models (mypy accepts these in annotations, while a
# `stable_id(...)` call in an annotation position would not type-check).
MetricId = Annotated[str, _checker(RefKind.METRIC, pinned=False)]
MetricRef = Annotated[str, _checker(RefKind.METRIC, pinned=True)]
DecisionId = Annotated[str, _checker(RefKind.DECISION, pinned=False)]
DecisionRef = Annotated[str, _checker(RefKind.DECISION, pinned=True)]
DashboardRef = Annotated[str, _checker(RefKind.DASHBOARD, pinned=True)]
PolicyRef = Annotated[str, _checker(RefKind.POLICY, pinned=True)]
AssetId = Annotated[str, _checker(RefKind.ASSET, pinned=False)]
AssetRef = Annotated[str, _checker(RefKind.ASSET, pinned=True)]
