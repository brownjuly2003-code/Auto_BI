"""Finding severity — one grading vocabulary for the whole control plane.

A semantic gap, a contract validation issue and a gate check all answer the same question:
does this stop the release, does a human have to look, or is it worth saying out loud and
nothing more? Grading them with three different enums would make findings from different
layers incomparable exactly where they must be compared — when one verdict is computed
from all of them (DECISIONOPS_ARCHITECTURE §7.6).

It lives in the shared kernel next to ``strict``, ``refs`` and ``scalars`` so that
``semantic`` can grade its gaps without importing the ``verification`` layer above it.
"""

from __future__ import annotations

from enum import StrEnum


class Severity(StrEnum):
    ADVISORY = "advisory"
    REVIEW = "review"
    BLOCK = "block"
