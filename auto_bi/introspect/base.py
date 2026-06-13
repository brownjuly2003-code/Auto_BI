"""Introspector seam: dialects plug in as implementations of this protocol."""

from collections.abc import Callable
from typing import Protocol

from auto_bi.semantic.model import SemanticModel

# run_query(sql) -> rows as dicts; the read-only seam to the real DWH client, shared by
# introspection, the SQL guard and the advisor (stubbed in tests). Engine-neutral so a
# Greengage/PG path can implement the same callable without importing the ClickHouse module.
RunQuery = Callable[[str], list[dict]]


class Introspector(Protocol):
    def introspect(self, database: str) -> SemanticModel:
        """Read engine catalogs and return a draft semantic model (incl. physical layer)."""
        ...
