"""Severities, verdicts and process exit codes (DECISIONOPS_ARCHITECTURE §7.6, §12.1).

A verdict is computed from findings, never from prose: the worst severity present decides.
Advisory findings do not fail a pipeline — they are reported and the run still passes — so
`pass_with_advisory` shares the success exit code while staying a distinct verdict in the
report itself.

Exit codes are a contract with CI and must stay stable:

| code | meaning                                                              |
|------|----------------------------------------------------------------------|
| 0    | `pass` / `pass_with_advisory` — nothing blocks the release            |
| 2    | runtime or usage error (argparse's own code, and the v1 CLI's)        |
| 3    | `needs_review` — a human decision is required, no automatic verdict   |
| 4    | `block` — the change must not ship as it stands                       |

1 is deliberately unused: it is what the interpreter returns on an unhandled exception, and
a crash must never be mistaken for a graded verdict (invariant 8).
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from auto_bi.severity import Severity

__all__ = ["EXIT_ERROR", "Severity", "Verdict", "exit_code_for", "verdict_for"]


class Verdict(StrEnum):
    PASS = "pass"
    PASS_WITH_ADVISORY = "pass_with_advisory"
    NEEDS_REVIEW = "needs_review"
    BLOCK = "block"


EXIT_ERROR = 2

_EXIT_CODES: dict[Verdict, int] = {
    Verdict.PASS: 0,
    Verdict.PASS_WITH_ADVISORY: 0,
    Verdict.NEEDS_REVIEW: 3,
    Verdict.BLOCK: 4,
}

_VERDICT_FOR_SEVERITY: dict[Severity, Verdict] = {
    Severity.ADVISORY: Verdict.PASS_WITH_ADVISORY,
    Severity.REVIEW: Verdict.NEEDS_REVIEW,
    Severity.BLOCK: Verdict.BLOCK,
}

_RANK: dict[Verdict, int] = {
    Verdict.PASS: 0,
    Verdict.PASS_WITH_ADVISORY: 1,
    Verdict.NEEDS_REVIEW: 2,
    Verdict.BLOCK: 3,
}


def exit_code_for(verdict: Verdict) -> int:
    """Stable process exit code for *verdict*."""
    return _EXIT_CODES[verdict]


def verdict_for(severities: Iterable[Severity]) -> Verdict:
    """Worst-severity verdict over *severities* (empty → :attr:`Verdict.PASS`)."""
    verdict = Verdict.PASS
    for severity in severities:
        candidate = _VERDICT_FOR_SEVERITY[severity]
        if _RANK[candidate] > _RANK[verdict]:
            verdict = candidate
    return verdict
