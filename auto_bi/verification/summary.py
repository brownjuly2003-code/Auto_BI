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

import string
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from auto_bi.decision.impact import ChartGap, ProductImpact
from auto_bi.severity import Severity
from auto_bi.verification.model import Verdict, verdict_for

if TYPE_CHECKING:  # a runtime import would close the loop: release_gate imports this module
    from auto_bi.decision.validate import ValidationIssue
    from auto_bi.verification.release_gate import CheckResult

#: Outcomes that mean a step produced nothing: not implemented offline, skipped because an
#: earlier finding made it unanswerable, run with nothing to compare, or unable to compile a
#: single query. `result.no_expectations` is the line an owner most needs under a green
#: verdict — a gate that compared no numbers has verified none of them, and
#: `cost.no_budget_declared` is the same sentence about what a run may cost, and
#: `provenance.no_results` about numbers whose origin there was nothing to establish.
#:
#: A *partial* outcome deliberately stays out: `sql.partially_compiled` says in its own detail
#: how many of how many compiled, and filing it under "not done" would deny the part that was.
_UNDONE = frozenset(
    {
        "check.not_configured",
        "check.not_implemented",
        "check.not_run",
        "cost.no_budget_declared",
        "cost.not_run",
        "freshness.not_run",
        "physical.not_run",
        "provenance.no_results",
        "result.no_expectations",
        "security.not_run",
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
    chart_gaps: Sequence[ChartGap] = (),
    *,
    other_product_issues: Sequence[ValidationIssue] = (),
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
    unaffected = next(
        (
            check
            for check in checks
            if check.check_id == "impact.products" and check.summary_code == "impact.none"
        ),
        None,
    )
    lines += _owner_action_section(verdict, product_unaffected=unaffected is not None)
    lines += _severity_section(
        "What stops the release",
        [check for check in graded if check.severity() is Severity.BLOCK],
    )
    lines += _impact_section(impacts, chart_gaps)
    review_checks = [check for check in graded if check.severity() is Severity.REVIEW]
    review_intro = ""
    if unaffected is not None:
        review_intro = (
            f"**Product scope:** `{unaffected.summary_code}` — {unaffected.detail}\n\n"
            "The human review below does not mean this product's numbers changed."
        )
    lines += _severity_section(
        "What needs a human",
        review_checks,
        intro=review_intro,
    )
    lines += _undone_section([check for check in checks if check.summary_code in _UNDONE])
    lines += _other_product_section(other_product_issues)
    lines += _reading_guide_section()
    lines += _table_section(checks)
    return "\n".join(lines) + "\n"


def _other_product_section(issues: Sequence[ValidationIssue]) -> list[str]:
    """Restate the validation payload's ungraded issues without extending this verdict."""
    if not issues:
        return []
    lines = [
        "",
        "## Validation issues for other products",
        "",
        "These issues belong to other products in the validated paths and are not graded "
        "for this product. They do not change this run's verdict.",
        "",
    ]
    for issue in issues:
        lines += [
            f"- source: {_literal(issue.source)}",
            f"  - code: {_literal(issue.code)}; reported severity: {issue.severity.value}",
            f"  - subject: {_literal(issue.subject)}",
            f"  - message: {_literal(issue.message)}",
        ]
    return lines


def _literal(value: str) -> str:
    """One inline value: visible controls and CommonMark-escaped punctuation."""
    visible = "".join(char if char.isprintable() else ascii(char)[1:-1] for char in value)
    return "".join(f"\\{char}" if char in string.punctuation else char for char in visible)


def _owner_action_section(verdict: Verdict, *, product_unaffected: bool) -> list[str]:
    """Turn only the graded verdict and product-scope finding into the next gate action."""
    if verdict is Verdict.BLOCK:
        action = (
            "Do not release this candidate. Resolve the blocking findings in this report, "
            "then run the release gate again."
        )
    elif verdict is Verdict.NEEDS_REVIEW and product_unaffected:
        action = (
            "The `impact.none` finding says the semantic changes do not reach this product. "
            "Review the remaining human findings before deciding whether to release it."
        )
    elif verdict is Verdict.NEEDS_REVIEW:
        action = (
            "Review the human findings in this report and record the owner's decision before "
            "release."
        )
    elif verdict is Verdict.PASS_WITH_ADVISORY:
        action = "Release is not blocked by this gate. Read the advisory findings before release."
    else:
        action = "No owner action is required by the graded findings."
    return ["", "## Owner action", "", action]


def _severity_section(
    title: str,
    checks: Sequence[CheckResult],
    *,
    intro: str = "",
) -> list[str]:
    if not checks:
        return []
    lines = ["", f"## {title}", ""]
    if intro:
        lines += [intro, ""]
    for check in checks:
        lines.append(f"- **{check.check_id}** — `{check.summary_code}` — {check.detail}")
    return lines


def _impact_section(
    impacts: Sequence[ProductImpact], chart_gaps: Sequence[ChartGap] = ()
) -> list[str]:
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
        for chart in item.charts:
            # The tile, not just the folder it sits in: the one line an owner opens the
            # dashboard for. The relation says whether it *is* the metric or merely reads
            # the column, because those are different things to go and check.
            lines.append(f"  - chart `{chart.ref}` — {chart.relation.value}: {chart.detail}")
        if item.severity is not item.classified_severity:
            lines.append(
                f"  - raised from {item.classified_severity.value} to {item.severity.value} "
                "because a governed product depends on it"
            )
    if chart_gaps:
        lines += [
            "",
            "Charts this run could not resolve in whole — a tile missing from the lists "
            "above may be one of these rather than an absence, and a tile named above may "
            "appear here because part of its query was not read, or because its SQL does "
            "not settle whether it computes a governed metric:",
            "",
        ]
        lines += [f"- `{gap.code}` {gap.subject} — {gap.detail}" for gap in chart_gaps]
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


def _reading_guide_section() -> list[str]:
    """State the boundaries an owner needs before reading the check table."""
    return [
        "",
        "## How to read this evidence",
        "",
        "- If `results.reconcile` is `result.equivalent`, it means only that the supplied "
        "result rows agree; it is not semantic approval.",
        "- When `results.provenance` is established, it means a supplied attestation matches "
        "the reviewed query and semantic snapshot, and, when this run declares one, the "
        "environment. It does not independently prove that SQL executed.",
        "- `dwh.cost` bounds concern compiled metric reference SQL; they do not measure the "
        "captured reviewed asset SQL.",
        "- Unavailable numeric counters stay unknown. Successful execution under "
        "engine-enforced limits can confirm bounds without an exact numeric measurement; "
        "neither `cost.not_measured` "
        "nor a skipped check is a pass.",
        "- A successfully verified signature authenticates the producer and bundle integrity, "
        "but it does not prove SQL execution or production sign-off. Verify it separately "
        "with `auto_bi decision evidence` and a trusted public key.",
        "- The report is sealed before the bundle verification check. It does not assert that "
        "this bundle is signed or verified.",
    ]


def _cell(text: str) -> str:
    """A finding is data, not markup: a pipe inside it must not become a column."""
    return text.replace("|", "\\|")


__all__ = ["SummaryFacts", "render_summary"]
