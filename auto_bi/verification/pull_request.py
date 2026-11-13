"""The pull-request comment of one gate run (ADR 0012, D3).

`reports/summary.md` is written for the owner who opens a bundle; this comment is written for
the people who review the change that produced it, in the pull request where they review it.
It is derived the way the summary is and from the same things — the graded checks of the
run and the `impact.products` payload sealed in its bundle — and adds no finding of its own.
The bundle stays the record: the comment names it by its manifest digest.

Where the text goes makes four rules here:

* the comment is written after the bundle is sealed and re-verified, outside it, so unlike
  the summary it states `evidence.bundle` and the manifest digest;
* a pull request renders GitHub's references: an ``@name`` notifies a person, a ``#123``,
  a ``gh-123`` or a link to an issue writes into that issue's timeline. A ref or a detail
  is data, never a reference: every word of a detail that holds ``@``, ``#`` or ``://``, or
  reads ``gh-`` and a digit or ``www.``, is set as a code span, and every other word has
  its ASCII punctuation escaped as in the summary. Refs are code spans whatever they hold;
* GitHub refuses a comment body longer than 65,536 characters. The comment is held to
  `COMMENT_LIMIT` UTF-16 code units — never fewer than its characters — by caps on each
  value and list and, past them, by leaving out whole sections from the last one up, each
  named in the comment; nothing is cut in the middle of a section;
* it carries no clock and no ordering of its own: the same run renders the same bytes.
"""

from __future__ import annotations

import re
import string
from collections.abc import Sequence
from pathlib import Path

from auto_bi.decision.impact import ChartGap, ImpactReport, ProductImpact
from auto_bi.evidence.inspect import read_artifact
from auto_bi.evidence.signing import EvidenceVerifier
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.severity import Severity
from auto_bi.verification.model import exit_code_for
from auto_bi.verification.release_gate import (
    CheckResult,
    CheckStatus,
    GateReport,
    RunStatus,
    _reaching,
)
from auto_bi.verification.summary import UNDONE_CODES, owner_action

#: Room under GitHub's 65,536 characters for what the action adds around the comment: its
#: marker line and the line naming the commit and the workflow run.
COMMENT_LIMIT = 60_000
#: The head and the foot are bounded by the caps below and always fit in this many units, so
#: a comment can leave out sections and still say what it left out.
MIN_LIMIT = 8_192
#: Characters of one ref (a product, a run, a dashboard, a chart, an owner).
REF_CAP = 200
#: Characters of one detail.
DETAIL_CAP = 600
#: Characters of the reason a run did not reach a verdict.
REASON_CAP = 2_000
#: Items of one list of refs (metrics, dashboards, owners, charts of one change).
LIST_CAP = 10
#: Changes in the section of whose numbers a change reaches.
IMPACT_CAP = 20

TITLE = "Auto_BI release gate"

#: A word GitHub could read as a reference: a mention, an issue, an issue of another repo,
#: or a link, which may be one to an issue.
_REFERENCE = re.compile(r"[@#]|(?:^|[^0-9A-Za-z_])gh-[0-9]|://|(?:^|\W)www\.", re.IGNORECASE)
_BACKTICKS = re.compile(r"`+")

_Section = tuple[str, list[str]]


def comment_for_run(
    report: GateReport,
    evidence_dir: str | Path,
    *,
    verifier: EvidenceVerifier | None = None,
    limit: int = COMMENT_LIMIT,
) -> str:
    """Render *report*'s comment, reading its `impact.products` payload out of the bundle.

    The payload is read only if the manifest still hashes to the digest the run reported, and
    a signed bundle only through *verifier* (`read_artifact`): the changes and charts the
    comment names are the ones the bundle holds. A run whose impact check wrote no payload —
    it was skipped — has none to read, and the comment has no section of whose numbers move.
    """
    check = next((item for item in report.checks if item.check_id == "impact.products"), None)
    impact = None
    if check is not None and check.evidence_refs:
        _, payload = read_artifact(
            FilesystemArtifactStore(evidence_dir),
            report.run_id,
            check.evidence_refs[0],
            expected_digest=report.evidence_digest,
            verifier=verifier,
        )
        impact = ImpactReport.model_validate_json(payload)
    return render_pull_request_comment(report, impact, limit=limit)


def render_pull_request_comment(
    report: GateReport,
    impact: ImpactReport | None = None,
    *,
    limit: int = COMMENT_LIMIT,
) -> str:
    """The comment of one completed gate run."""
    _check_limit(limit)
    checks = report.checks
    # Every check lands in at most one section, as in the summary: a finding repeated under
    # two headings reads as two findings.
    graded = [check for check in checks if check.summary_code not in UNDONE_CODES]
    unaffected = any(
        check.check_id == "impact.products" and check.summary_code == "impact.none"
        for check in checks
    )
    sections: list[_Section] = [
        _findings(
            "What stops the release",
            [check for check in graded if check.severity() is Severity.BLOCK],
        ),
        _impact_section(_reaching(impact) if impact is not None else [], impact),
        _findings(
            "What needs a human",
            [check for check in graded if check.severity() is Severity.REVIEW],
        ),
        _findings(
            "Advisory findings",
            [check for check in graded if check.severity() is Severity.ADVISORY],
        ),
        _undone_section([check for check in checks if check.summary_code in UNDONE_CODES]),
        _check_table(checks),
    ]
    return _fit(
        _head(report, unaffected=unaffected),
        [section for section in sections if section[1]],
        _foot(report),
        limit,
    )


def render_pull_request_failure(reason: str, *, limit: int = COMMENT_LIMIT) -> str:
    """The comment of a run that ended with no verdict a pull request can rely on.

    That is a run that stopped before a verdict, or one whose bundle no longer holds what the
    comment would state: either way the command exits 2, and the comment is not a pass.
    """
    _check_limit(limit)
    lines = [
        f"### {TITLE}: no verdict",
        "",
        "This run ended without a verdict a pull request can rely on, so this commit has none. "
        "That is not a pass.",
        "",
        f"- **Reason:** {_code(reason, REASON_CAP)}",
        "",
        "Fix the run and push again; the job log has the whole output.",
    ]
    return "\n".join(lines) + "\n"


def _head(report: GateReport, *, unaffected: bool) -> list[str]:
    verdict = report.verdict.value
    statuses = [check.status for check in report.checks]
    counted = ", ".join(
        f"{statuses.count(status)} {status.value}"
        for status in (CheckStatus.BLOCK, CheckStatus.REVIEW, CheckStatus.ADVISORY)
    )
    bundle = next(
        (check for check in report.checks if check.check_id == "evidence.bundle"),
        None,
    )
    evidence = f"- **Evidence manifest:** {_code(report.evidence_digest)}"
    if bundle is not None:
        evidence += f" ({_code(bundle.summary_code)})"
    lines = [
        f"### {TITLE}: {verdict}",
        "",
        f"- **Product:** {_code(report.product_ref)}",
        f"- **Verdict:** {verdict}, exit code {exit_code_for(report.verdict)} "
        f"(checks: {counted}, of {len(statuses)})",
        f"- **Run:** {_code(report.run_id)}, created {_code(report.created_at)}",
        evidence,
        f"- **Semantics:** base {_code(report.base_digest)}, "
        f"candidate {_code(report.candidate_digest)}",
    ]
    if report.operational_status is not RunStatus.COMPLETED:
        lines.append(f"- **Run status:** {report.operational_status.value}")
    return [*lines, "", owner_action(report.verdict, product_unaffected=unaffected)]


def _foot(report: GateReport) -> list[str]:
    command = "auto_bi decision evidence <evidence> --expect-digest " + _visible(
        report.evidence_digest, REF_CAP
    )
    return [
        "",
        "---",
        "",
        "Derived from this run's checks and the `impact.products` payload sealed in its "
        "evidence; it adds no finding of its own. The bundle is the record, and its "
        f"`reports/summary.md` the whole report. Check the bundle with {_span(command)}.",
    ]


def _findings(title: str, checks: Sequence[CheckResult]) -> _Section:
    if not checks:
        return title, []
    lines = ["", f"#### {title}", ""]
    lines += [_finding(check) for check in checks]
    return title, lines


def _finding(check: CheckResult) -> str:
    return (
        f"- {_code(check.check_id)} · {_code(check.summary_code)} — "
        f"{_prose(check.detail, DETAIL_CAP)}"
    )


def _undone_section(checks: Sequence[CheckResult]) -> _Section:
    """Always present, as in the summary: a hole named is a hole a reader can see."""
    title = "Not done in this run"
    lines = ["", f"#### {title}", ""]
    if not checks:
        lines.append("- every step of §8.2 produced evidence in this run")
    else:
        lines += [_finding(check) for check in checks]
    return title, lines


def _impact_section(impacts: Sequence[ProductImpact], impact: ImpactReport | None) -> _Section:
    """Whose numbers move and which tiles show them — what a verdict alone never says."""
    title = "Whose numbers this reaches"
    if not impacts:
        return title, []
    lines = ["", f"#### {title}", ""]
    for item in impacts[:IMPACT_CAP]:
        line = f"- **{item.severity.value}** {_code(item.code)} · {_code(item.subject)}"
        if item.severity is not item.classified_severity:
            line += (
                f" — raised from {item.classified_severity.value} because a governed "
                "product depends on it"
            )
        lines += [line, f"  - {_prose(item.detail, DETAIL_CAP)}"]
        for label, refs in (
            ("metrics", [] if item.metrics == [item.subject] else item.metrics),
            ("dashboards", item.dashboards),
            ("owners", item.owners),
        ):
            if refs:
                lines.append(f"  - {label}: {_refs(refs, label)}")
        if item.charts:
            charts = [f"{_code(chart.ref)} ({chart.relation.value})" for chart in item.charts]
            lines.append(f"  - charts: {_listed(charts, 'charts')}")
    if len(impacts) > IMPACT_CAP:
        lines.append(f"- and {len(impacts) - IMPACT_CAP} more changes in the evidence summary")
    gaps = _gap_counts(impact.chart_gaps if impact is not None else ())
    if gaps:
        lines += [
            "",
            f"Charts this run could not resolve in whole: {gaps}. A tile missing above may be "
            "one of them; the evidence summary lists them.",
        ]
    return title, lines


def _gap_counts(gaps: Sequence[ChartGap]) -> str:
    counts: dict[str, int] = {}
    for gap in gaps:
        counts[gap.code] = counts.get(gap.code, 0) + 1
    return ", ".join(f"{_code(code)} {counts[code]}" for code in sorted(counts))


def _refs(refs: Sequence[str], label: str) -> str:
    return _listed([_code(ref) for ref in refs], label)


def _listed(items: Sequence[str], label: str) -> str:
    shown = ", ".join(items[:LIST_CAP])
    if len(items) > LIST_CAP:
        shown += f" and {len(items) - LIST_CAP} more {label} in the evidence summary"
    return shown


def _check_table(checks: Sequence[CheckResult]) -> _Section:
    """Every check, folded, one row each."""
    title = "Every check"
    lines = [
        "",
        "<details>",
        f"<summary>Every check ({len(checks)})</summary>",
        "",
        "| check | status | outcome |",
        "|---|---|---|",
    ]
    lines += [
        f"| {_cell(check.check_id)} | {check.status.value} | {_cell(check.summary_code)} |"
        for check in checks
    ]
    return title, [*lines, "", "</details>"]


def _cell(value: str) -> str:
    """A code span in a table cell: GFM splits a row at a `|` even inside a span, not at `\\|`."""
    return _code(value).replace("|", "\\|")


def _fit(head: list[str], sections: list[_Section], foot: list[str], limit: int) -> str:
    """Head, the sections that fit, a line naming those left out, and the foot."""
    kept = list(sections)
    left_out: list[str] = []
    while True:
        lines = list(head)
        for _, body in kept:
            lines += body
        if left_out:
            lines += [
                "",
                f"Left out to fit a pull request comment: {', '.join(left_out)}. The evidence "
                "summary holds them.",
            ]
        text = "\n".join([*lines, *foot]) + "\n"
        if _units(text) <= limit or not kept:
            return text
        left_out.insert(0, kept.pop()[0])


def _units(text: str) -> int:
    """Length in UTF-16 code units: a character outside the BMP is two of them."""
    return len(text.encode("utf-16-le")) // 2


def _check_limit(limit: int) -> None:
    if limit < MIN_LIMIT:
        raise ValueError(f"a pull request comment needs at least {MIN_LIMIT} units, not {limit}")


def _visible(text: str, cap: int) -> str:
    """*text* on one line, each character that is not printable spelled by its escape, cut."""
    visible = "".join(char if char.isprintable() else ascii(char)[1:-1] for char in text)
    return visible if len(visible) <= cap else visible[:cap] + "…"


def _code(value: str, cap: int = REF_CAP) -> str:
    """*value* as one code span: literal whatever it holds, and no reference to GitHub."""
    return _span(_visible(value, cap))


def _span(text: str) -> str:
    if not text:
        return "(empty)"
    fence = "`" * (max((len(run) for run in _BACKTICKS.findall(text)), default=0) + 1)
    # CommonMark strips one space from each end of a span that has one at both ends, and a
    # span may not open or close on a backtick of its own: either way a space pads it.
    padded = text.startswith("`") or text.endswith("`")
    padded = padded or (text.startswith(" ") and text.endswith(" ") and bool(text.strip(" ")))
    pad = " " if padded else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _prose(text: str, cap: int) -> str:
    """A detail: words GitHub would read as references are code spans, the rest escaped.

    After `_visible` the only whitespace left is the ASCII space — every other kind is not
    printable — so splitting at it keeps every other character of the detail. Words are
    joined by one space: a page shows a run of them as one anyway, and a detail that opened
    on four would open a block of code in the list item it is set in.
    """
    words = [word for word in _visible(text, cap).split(" ") if word]
    return " ".join(_span(word) if _REFERENCE.search(word) else _escape(word) for word in words)


def _escape(word: str) -> str:
    return "".join(f"\\{char}" if char in string.punctuation else char for char in word)


__all__ = [
    "COMMENT_LIMIT",
    "MIN_LIMIT",
    "comment_for_run",
    "render_pull_request_comment",
    "render_pull_request_failure",
]
