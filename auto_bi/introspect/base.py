"""Introspector seam: dialects plug in as implementations of this protocol."""

import re
from collections.abc import Callable
from typing import Protocol

from auto_bi.semantic.model import SemanticModel

# Rate/ratio-shaped column names: a row-wise SUM over them is business-meaningless, so the
# draft marks them non-additive with `agg: avg` instead of the numeric default `sum` (audit
# P1-6: `effective_tax_rate`/`return_rate` shipped as `agg: sum`). Shared by both dialect
# introspectors so the heuristic can't drift. Deliberately conservative: whole name tokens
# only (`conversion_rate`, `pct_returned`, `market_share`), not substrings — `rated_power`
# or `priceless` must not match. `price`/`unit_price` are unit prices (avg), but
# `total_price` is a line total (additive) and stays out.
_NON_ADDITIVE_NAME_RE = re.compile(r"(^|_)(rate|ratio|pct|percent|share)(_|$)|^(unit_)?price$")


def rate_like(column_name: str) -> bool:
    """Whether a column name says non-additive (rate/ratio/share/unit price)."""
    return _NON_ADDITIVE_NAME_RE.search(column_name.lower()) is not None


# run_query(sql) -> rows as dicts; the read-only seam to the real DWH client, shared by
# introspection, the SQL guard and the advisor (stubbed in tests). Engine-neutral so a
# Greengage/PG path can implement the same callable without importing the ClickHouse module.
RunQuery = Callable[[str], list[dict]]


class Introspector(Protocol):
    def introspect(self, database: str) -> SemanticModel:
        """Read engine catalogs and return a draft semantic model (incl. physical layer)."""
        ...
