"""Introspector seam: dialects plug in as implementations of this protocol."""

from typing import Protocol

from auto_bi.semantic.model import SemanticModel


class Introspector(Protocol):
    def introspect(self, database: str) -> SemanticModel:
        """Read engine catalogs and return a draft semantic model (incl. physical layer)."""
        ...
