"""The derived summary a person reads (DECISIONOPS_ARCHITECTURE §9.2).

Everything else in a bundle is written for a machine: canonical JSON, content addresses,
typed findings. `reports/summary.md` is the one artifact written for the owner of a
governed product — usually someone opening evidence for a change they did not make and do
not want to read as JSON.

Being *derived* is the contract, not a disclaimer. This module restates findings the checks
already carry, in the order an owner needs them (what stops the release, whose numbers it
reaches, what a human must decide, what the run did not do), and introduces nothing of its
own. If a sentence here is not backed by a check in the same bundle, the report is lying
about what was verified — which is the exact failure the product exists to prevent.

Two boundaries follow from where the report lives:

* it does not grade `evidence.bundle`. That check re-verifies the bundle this report is
  sealed inside, and is decided after the sealing; a report claiming its verdict would be
  claiming something nobody had checked yet. The header says as much rather than leaving
  the difference to be discovered.
* it carries no clock, no counts of its own and no ordering that depends on a dict — the
  same inputs must render the same bytes, because the bytes are part of a content address
  (§9.1).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from auto_bi.decision.impact import ProductImpact
from auto_bi.severity import Severity
from auto_bi.verification.model import verdict_for

if TYPE_CHECKING:  # a runtime import would close the loop: release_gate imports this module
    from auto_bi.verification.release_gate import CheckResult

#: Outcomes that mean a step produced nothing: not implemented offline, skipped because an
#: earlier finding made it unanswerable, run with nothing to compare, or unable to compile a
#: single query. `result.no_expectations` is the line an owner most needs under a green
#: verdict — a gate that compared no numbers has verified none of them.
#:
#: A *partial* outcome deliberately stays out: `sql.partially_compiled` says in its own detail
#: how many of how many compiled, and filing it under "not done" would deny the part that was.
_UNDONE = frozenset(
    {
        "check.not_implemented",
        "check.not_run",
        "result.no_expectations",
        "sql.not_compiled",
    }
)


@dataclass(frozen=True)
class SummaryFacts:
    """Run identity, exactly as the report header states it."""

    product_ref: str
    run_id: str
    base_digest: str
    candidate_digest: str
    created_at: str
    verifier_version: str
    environment_ref: str = ""


def render_summary(
    facts: SummaryFacts,
    checks: Sequence[CheckResult],
    impacts: Sequence[ProductImpact] = (),
) -> str:
    """Render the owner-facing summary of one gate run."""
    verdict = verdict_for(
        severity for check in checks if (severity := check.severity()) is not None
    )
    lines: list[str] = [
        f"# Release gate — {facts.product_ref}",
        "",
        f"**Verdict:** {verdict.value}",
        f"**Run:** {facts.run_id}",
        f"**Base semantics:** {facts.base_digest}",
        f"**Candidate semantics:** {facts.candidate_digest}",
    ]
    if facts.environment_ref:
        lines.append(f"**Environment:** {facts.environment_ref}")
    lines += [
        f"**Created:** {facts.created_at} by verifier {facts.verifier_version}",
        "",
        "Derived from the checks below (§9.2): this report restates what they found and adds",
        "no finding of its own. The verdict above is the one those checks carry — the bundle's",
        "own re-verification is graded after this report is sealed inside it, so it is reported",
        "with the run, not here.",
    ]

    # Every check lands in exactly one section: a finding repeated under two headings reads
    # as two findings, and an owner counting problems would count it twice.
    graded = [check for check in checks if check.summary_code not in _UNDONE]
    lines += _severity_section(
        "What stops the release",
        [check for check in graded if check.severity() is Severity.BLOCK],
    )
    lines += _impact_section(impacts)
    lines += _severity_section(
        "What needs a human",
        [check for check in graded if check.severity() is Severity.REVIEW],
    )
    lines += _undone_section([check for check in checks if check.summary_code in _UNDONE])
    lines += _table_section(checks)
    return "\n".join(lines) + "\n"


def _severity_section(title: str, checks: Sequence[CheckResult]) -> list[str]:
    if not checks:
        return []
    lines = ["", f"## {title}", ""]
    for check in checks:
        lines.append(f"- **{check.check_id}** — `{check.summary_code}` — {check.detail}")
    return lines


def _impact_section(impacts: Sequence[ProductImpact]) -> list[str]:
    """Whose numbers move — the question a verdict alone never answers (§8.3)."""
    if not impacts:
        return []
    lines = ["", "## Whose numbers this reaches", ""]
    for item in impacts:
        lines.append(f"- **{item.severity.value}** `{item.code}` — {item.subject}")
        lines.append(f"  - {item.detail}")
        for label, refs in (
            # The subject is already on the line above; repeating it as "metrics: <subject>"
            # adds a word and no fact.
            ("metrics", [] if item.metrics == [item.subject] else item.metrics),
            ("dashboards", item.dashboards),
            ("owners", item.owners),
        ):
            if refs:
                lines.append(f"  - {label}: {', '.join(refs)}")
        if item.severity is not item.classified_severity:
            lines.append(
                f"  - raised from {item.classified_severity.value} to {item.severity.value} "
                "because a governed product depends on it"
            )
    return lines


def _undone_section(checks: Sequence[CheckResult]) -> list[str]:
    """Always present, even when empty of surprises: a hole named is a hole a reader can see."""
    lines = ["", "## What was not done", ""]
    if not checks:
        lines.append("- every step of §8.2 produced evidence in this run")
        return lines
    for check in checks:
        lines.append(f"- **{check.check_id}** — `{check.summary_code}` — {check.detail}")
    return lines


def _table_section(checks: Sequence[CheckResult]) -> list[str]:
    lines = ["", "## Every check", "", "| check | status | outcome | detail |", "|---|---|---|---|"]
    for check in checks:
        lines.append(
            f"| `{check.check_id}` | {check.status.value} | `{check.summary_code}` "
            f"| {_cell(check.detail)} |"
        )
    return lines


def _cell(text: str) -> str:
    """A finding is data, not markup: a pipe inside it must not become a column."""
    return text.replace("|", "\\|")


__all__ = ["SummaryFacts", "render_summary"]
