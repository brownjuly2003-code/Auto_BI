"""The pull-request comment of a gate run (ADR 0012, D3).

Durable spec: docs/specs/pull-request-comment.md. `decision verify --pr-comment` writes, after
the bundle is sealed, the comment a pull request shows: the verdict, the changes and charts
that reach the product, the findings and the manifest digest, derived from the run's checks
and the `impact.products` payload read back out of the bundle. Its text is data, never a
reference GitHub would follow, and it is held under GitHub's limit on a comment.
"""

from __future__ import annotations

import os
import random
import re
import sys
from pathlib import Path

import pytest
from markdown_it import MarkdownIt
from markdown_it.token import Token

from auto_bi.cli import main
from auto_bi.decision import use_cases
from auto_bi.decision.impact import ChartGap, ChartImpact, ChartRelation, ImpactReport
from auto_bi.decision.use_cases import VerifyRequest, verify
from auto_bi.evidence.inspect import EvidenceReadError, read_artifact
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.severity import Severity
from auto_bi.verification import pull_request
from auto_bi.verification.model import Verdict
from auto_bi.verification.pull_request import (
    COMMENT_LIMIT,
    DETAIL_CAP,
    IMPACT_CAP,
    LIST_CAP,
    MIN_LIMIT,
    REASON_CAP,
    REF_CAP,
    comment_for_run,
    render_pull_request_comment,
    render_pull_request_failure,
)
from auto_bi.verification.release_gate import CheckResult, CheckStatus, GateReport, RunStatus
from auto_bi.verification.summary import UNDONE_CODES, owner_action
from tests.test_evidence_signing import _key_files

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "docs" / "pilot" / "2026-08-25-ch-tz-margin"
OVERLAY = REPO / "docs" / "pilot" / "2026-09-12-owner-review-chart-impact"
PRODUCT = "decision://sales/weekly_margin"
CREATED_AT = "2026-09-12T16:00:00Z"
CHART = "dashboard://weekly_margin@7#margin_by_region"

_MARKDOWN = MarkdownIt("commonmark").enable(["table", "strikethrough"])
#: What GitHub reads as a reference in text: a mention, an issue, a link, a `gh-` issue —
#: the last two only where a word or the text starts, as GitHub and GFM read them.
_REFERENCE_TEXT = re.compile(r"[@#]|://|(?:^|[^0-9A-Za-z_])gh-[0-9]|(?:^|\W)www\.", re.IGNORECASE)
_HTML_LINES = re.compile(r"<details>|</details>|<summary>Every check \([0-9]+\)</summary>")
_LABELS = {
    "Product:",
    "Verdict:",
    "Run:",
    "Evidence manifest:",
    "Semantics:",
    "Run status:",
    "Reason:",
    *(severity.value for severity in Severity),
}
#: Characters a detail or a ref might hold that Markdown or GitHub would act on.
_NASTY = [
    *"@#:/.w-gh0123[]()<>*_`|\\!&;~^$=+{}'\"%",
    " ",
    " ",
    "\n",
    "\t",
    "\r",
    "\x00",
    "\u00a0",
    "\u2028",
    "\ud800",
    "\U0001f600",
    "é",
    "www.",
    "gh-1",
    "://",
    "@x",
    "#1",
    "<img src=x>",
    "](javascript:x)",
    "    ",
]


def pilot_argv(evidence: Path, *extra: str) -> list[str]:
    """The chart-impact pilot (README of the overlay): a governed change that blocks."""
    return [
        "decision",
        "verify",
        str(SOURCE / "contracts"),
        f"--base={SOURCE / 'base'}",
        f"--candidate={SOURCE / 'candidate'}",
        f"--dashboards={OVERLAY / 'dashboards'}",
        f"--assets={SOURCE / 'assets'}",
        f"--results={SOURCE / 'results.yaml'}",
        f"--product={PRODUCT}",
        "--environment=synthetic-offline",
        f"--created-at={CREATED_AT}",
        f"--evidence={evidence}",
        *extra,
    ]


@pytest.fixture(scope="module")
def pilot(tmp_path_factory: pytest.TempPathFactory) -> tuple[GateReport, Path, ImpactReport]:
    evidence = tmp_path_factory.mktemp("pilot") / "evidence"
    report = verify(
        VerifyRequest(
            contract_paths=[SOURCE / "contracts"],
            base=SOURCE / "base",
            candidate=SOURCE / "candidate",
            evidence_dir=evidence,
            created_at=CREATED_AT,
            dashboards_dir=OVERLAY / "dashboards",
            assets_dir=SOURCE / "assets",
            results_file=SOURCE / "results.yaml",
            environment_ref="synthetic-offline",
            product_id=PRODUCT,
        )
    )
    check = _check(report, "impact.products")
    _, payload = read_artifact(
        FilesystemArtifactStore(evidence),
        report.run_id,
        check.evidence_refs[0],
        expected_digest=report.evidence_digest,
    )
    return report, evidence, ImpactReport.model_validate_json(payload)


def _check(report: GateReport, check_id: str) -> CheckResult:
    return next(check for check in report.checks if check.check_id == check_id)


def _units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _visible(text: str, cap: int) -> str:
    """The spec's spelling of a value: each character that is not printable by its escape."""
    shown = "".join(char if char.isprintable() else ascii(char)[1:-1] for char in text)
    return shown if len(shown) <= cap else shown[:cap] + "…"


def _inlines(text: str) -> list[list[Token]]:
    """The inline tokens of each paragraph, list item, heading and cell, after checking the
    blocks: HTML only where the comment folds its table, and no block a value could open."""
    inlines = []
    for token in _MARKDOWN.parse(text):
        if token.type in ("html_block", "html_inline"):
            for line in token.content.splitlines():
                assert _HTML_LINES.fullmatch(line), line
        assert token.type not in ("code_block", "fence", "blockquote_open"), token
        if token.type == "inline":
            inlines.append(list(token.children or []))
    return inlines


def _plain(children: list[Token]) -> str:
    """What a reader sees of one inline: text and the content of code spans."""
    return "".join(child.content for child in children if child.type in ("text", "code_inline"))


def assert_inert(text: str) -> None:
    """Nothing of the comment is a link, an image, HTML, emphasis or a reference GitHub follows,
    except the comment's own labels in bold and its folded table."""
    for children in _inlines(text):
        for index, child in enumerate(children):
            assert child.type in ("text", "code_inline", "strong_open", "strong_close"), child
            if child.type == "text":
                assert not _REFERENCE_TEXT.search(child.content), child.content
            if child.type == "strong_open":
                assert children[index + 1].content in _LABELS, children[index + 1]


def _nasty(rng: random.Random, size: int) -> str:
    return "".join(rng.choice(_NASTY) for _ in range(size))


# --- the comment of a run, through the command ------------------------------------------------


def test_verify_writes_the_comment_of_its_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    comment = tmp_path / "comment.md"

    assert main(pilot_argv(tmp_path / "evidence", "--json", f"--pr-comment={comment}")) == 4

    report = GateReport.model_validate_json(capsys.readouterr().out)
    raw = comment.read_bytes()
    text = raw.decode("utf-8")
    assert text == comment_for_run(report, tmp_path / "evidence")
    assert text.startswith("### Auto_BI release gate: block\n")
    assert f"- **Product:** `{PRODUCT}@1`\n" in text
    assert (
        "- **Verdict:** block, exit code 4 (checks: 1 block, 3 review, 6 advisory, of 15)\n" in text
    )
    assert f"- **Evidence manifest:** `{report.evidence_digest}` (`evidence.verified`)\n" in text
    assert f"\n{owner_action(Verdict.BLOCK, product_unaffected=False)}\n" in text
    assert f"  - charts: `{CHART}` (computes_metric)\n" in text
    assert "Charts this run could not resolve in whole: `chart.metric_unread` 1." in text
    assert f"--expect-digest {report.evidence_digest}`." in text
    assert b"\r" not in raw
    assert sorted(path.name for path in tmp_path.iterdir()) == ["comment.md", "evidence"]
    assert_inert(text)


def test_two_runs_of_one_change_write_the_same_comment(tmp_path: Path) -> None:
    for name in ("first", "second"):
        argv = pilot_argv(tmp_path / name / "evidence", f"--pr-comment={tmp_path / name}.md")
        assert main(argv) == 4

    assert (tmp_path / "first.md").read_bytes() == (tmp_path / "second.md").read_bytes()


def test_the_comment_leaves_the_report_and_the_bundle_as_a_run_without_it_writes_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = {}
    for name, extra in (("plain", []), ("commented", [f"--pr-comment={tmp_path / 'c.md'}"])):
        assert main(pilot_argv(tmp_path / name, "--json", *extra)) == 4
        files = sorted(path for path in (tmp_path / name).rglob("*") if path.is_file())
        bundle = {path.relative_to(tmp_path / name).as_posix(): path.read_bytes() for path in files}
        runs[name] = (capsys.readouterr().out, bundle)

    assert runs["plain"] == runs["commented"]
    assert (tmp_path / "c.md").is_file()


def test_a_signed_bundle_is_read_back_through_the_key_that_signed_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    private_path, _, _ = _key_files(tmp_path, "gate")
    comment = tmp_path / "comment.md"
    argv = pilot_argv(
        tmp_path / "evidence", f"--signing-key={private_path}", "--json", f"--pr-comment={comment}"
    )

    assert main(argv) == 4

    report = GateReport.model_validate_json(capsys.readouterr().out)
    assert f"  - charts: `{CHART}` (computes_metric)\n" in comment.read_text(encoding="utf-8")
    with pytest.raises(EvidenceReadError):
        comment_for_run(report, tmp_path / "evidence")


def test_a_run_that_stops_before_a_verdict_writes_that_it_has_none(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    comment = tmp_path / "comment.md"
    argv = pilot_argv(tmp_path / "evidence", f"--pr-comment={comment}")
    argv[argv.index(f"--base={SOURCE / 'base'}")] = f"--base={tmp_path / 'missing'}"

    assert main(argv) == 2

    err = capsys.readouterr().err
    assert err.startswith("error: ") and err.count("\n") == 1
    text = comment.read_bytes().decode("utf-8")
    assert text == render_pull_request_failure(err.removeprefix("error: ").rstrip("\n"))
    assert text.startswith("### Auto_BI release gate: no verdict\n")
    assert "That is not a pass." in text
    assert_inert(text)


@pytest.mark.parametrize("loss", ["manifest", "digest"])
def test_a_bundle_that_cannot_be_read_back_gets_the_comment_of_no_verdict(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    loss: str,
) -> None:
    sealed = use_cases.verify

    def verify_then_lose_the_bundle(request: VerifyRequest) -> GateReport:
        report = sealed(request)
        if loss == "digest":
            # A bundle that hashes whole, but not to the digest the run reported.
            return report.model_copy(update={"evidence_digest": "sha256:" + "0" * 64})
        (Path(request.evidence_dir) / report.run_id / "manifest.json").unlink()
        return report

    monkeypatch.setattr(use_cases, "verify", verify_then_lose_the_bundle)
    comment = tmp_path / "comment.md"

    assert main(pilot_argv(tmp_path / "evidence", f"--pr-comment={comment}")) == 2

    captured = capsys.readouterr()
    assert "verdict: block" in captured.out
    prefix = "error: cannot read the run's evidence back for --pr-comment: "
    assert captured.err.startswith(prefix) and captured.err.count("\n") == 1
    reason = captured.err.removeprefix("error: ").rstrip("\n")
    assert comment.read_bytes().decode("utf-8") == render_pull_request_failure(reason)


def test_a_payload_that_does_not_read_as_an_impact_report_gets_the_comment_of_no_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    class CutShort:
        """The payload one byte short: what the validator says of it is the reason."""

        @staticmethod
        def model_validate_json(payload: bytes) -> ImpactReport:
            return ImpactReport.model_validate_json(payload[:-1])

    monkeypatch.setattr(pull_request, "ImpactReport", CutShort)
    comment = tmp_path / "comment.md"

    assert main(pilot_argv(tmp_path / "evidence", f"--pr-comment={comment}")) == 2

    captured = capsys.readouterr()
    assert "verdict: block" in captured.out
    prefix = "error: cannot read the run's evidence back for --pr-comment: "
    assert captured.err.startswith(prefix) and captured.err.count("\n") == 1
    assert "validation error for ImpactReport" in captured.err
    reason = captured.err.removeprefix("error: ").rstrip("\n")
    assert comment.read_bytes().decode("utf-8") == render_pull_request_failure(reason)


@pytest.mark.parametrize(
    ("where", "said"),
    [
        ("a directory", "is a directory"),
        ("no directory", "there is no directory"),
        ("in the evidence", "is inside the evidence directory, and the comment is no evidence"),
        ("deep in the evidence", "is inside the evidence directory, and the comment is no"),
        ("the evidence", "is inside the evidence directory, and the comment is no evidence"),
        ("the evidence in another case", "is inside the evidence directory, and the comment is no"),
        ("through a link", "is inside the evidence directory, and the comment is no evidence"),
    ],
)
def test_a_comment_path_the_comment_cannot_take_is_refused_before_the_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], where: str, said: str
) -> None:
    evidence = tmp_path / "evidence"
    if where in (
        "in the evidence",
        "deep in the evidence",
        "the evidence in another case",
        "through a link",
    ):
        (evidence / "run").mkdir(parents=True)
    if where == "through a link":
        _link(tmp_path / "link", evidence / "run")
    target = {
        "a directory": tmp_path,
        "no directory": tmp_path / "missing" / "comment.md",
        "in the evidence": evidence / "comment.md",
        "deep in the evidence": evidence / "run" / "comment.md",
        "the evidence": evidence,
        "the evidence in another case": tmp_path / "EVIDENCE" / "run" / "comment.md",
        "through a link": tmp_path / "link" / "comment.md",
    }[where]
    if where == "the evidence in another case" and not (tmp_path / "EVIDENCE").is_dir():
        said = "there is no directory"  # a filesystem that keeps case: no such directory
    held = sorted(evidence.rglob("*")) if evidence.exists() else None

    assert main(pilot_argv(evidence, f"--pr-comment={target}")) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: --pr-comment ") and captured.err.count("\n") == 1
    assert said in captured.err
    assert (sorted(evidence.rglob("*")) if evidence.exists() else None) == held


def _link(link: Path, target: Path) -> None:
    """A symbolic link to the directory *target*; skips where the account may not make one."""
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        if sys.platform == "win32" and exc.winerror == 1314:
            pytest.skip("Windows symbolic-link privilege is unavailable")
        raise


def test_an_evidence_path_that_loops_ends_the_run_in_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A link that leads to itself: Python 3.12 resolves it with `RuntimeError`, which the
    precheck turns into its one line, exit 2, as it does an `OSError` — no traceback."""
    loop = tmp_path / "loop"
    _link(loop, loop)
    comment = tmp_path / "comment.md"

    assert main(pilot_argv(loop, f"--pr-comment={comment}")) == 2

    captured = capsys.readouterr()
    assert captured.err.startswith("error: ") and captured.err.count("\n") == 1
    assert "Traceback" not in captured.err


def test_a_comment_the_filesystem_refuses_after_the_run_ends_with_exit_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    replace = os.replace

    def refuse_the_comment(source: str, target: str) -> None:
        if Path(target).name == "comment.md":
            raise OSError(28, "No space left on device")
        replace(source, target)

    monkeypatch.setattr(os, "replace", refuse_the_comment)
    comment = tmp_path / "comment.md"

    assert main(pilot_argv(tmp_path / "evidence", f"--pr-comment={comment}")) == 2

    captured = capsys.readouterr()
    assert "verdict: block" in captured.out
    assert captured.err.startswith("error: cannot write --pr-comment ")
    assert "No space left on device" in captured.err and captured.err.count("\n") == 1
    assert sorted(path.name for path in tmp_path.iterdir()) == ["evidence"]


# --- what the comment says ------------------------------------------------------------------


def test_the_comment_is_derived_from_the_checks_and_the_sealed_impact(
    pilot: tuple[GateReport, Path, ImpactReport],
) -> None:
    report, evidence, impact = pilot

    text = comment_for_run(report, evidence)

    assert text == render_pull_request_comment(report, impact)
    headings = [line for line in text.splitlines() if line.startswith("#")]
    assert headings == [
        "### Auto_BI release gate: block",
        "#### What stops the release",
        "#### Whose numbers this reaches",
        "#### What needs a human",
        "#### Advisory findings",
        "#### Not done in this run",
    ]
    assert "<summary>Every check (15)</summary>" in text
    for check in report.checks:
        assert f"| `{check.check_id}` | {check.status.value} | `{check.summary_code}` |" in text
        listed = check.summary_code in UNDONE_CODES or check.severity() is not None
        assert text.count(f"- `{check.check_id}` · ") == int(listed), check.check_id
    assert (
        "- **block** `metric.time_changed` · `metric://sales/gross_margin_pct` — raised from "
        "review because a governed product depends on it\n" in text
    )
    assert "  - dashboards: `dashboard://weekly_margin@7`\n" in text
    assert "  - metrics:" not in text


def test_a_change_that_reaches_no_product_is_not_listed_as_one_that_reaches_this_one(
    pilot: tuple[GateReport, Path, ImpactReport],
) -> None:
    report, _, impact = pilot
    reaching = impact.impacts[0]
    elsewhere = reaching.model_copy(update={"subject": "metric://other/count", "products": []})

    text = render_pull_request_comment(
        report, impact.model_copy(update={"impacts": [reaching, elsewhere]})
    )

    assert f"· `{reaching.subject}`" in text
    assert "metric://other/count" not in text


def test_a_run_whose_impact_check_wrote_nothing_has_no_section_of_whose_numbers_move(
    pilot: tuple[GateReport, Path, ImpactReport],
) -> None:
    report, evidence, _ = pilot
    skipped = _check(report, "impact.products").model_copy(update={"evidence_refs": []})
    checks = [skipped if check.check_id == "impact.products" else check for check in report.checks]

    text = comment_for_run(report.model_copy(update={"checks": checks}), evidence)

    assert "Whose numbers this reaches" not in text
    assert "#### What stops the release" in text


def test_a_review_of_a_product_no_change_reaches_says_so_as_the_summary_does(
    pilot: tuple[GateReport, Path, ImpactReport],
) -> None:
    report, _, _ = pilot
    unaffected = _check(report, "impact.products").model_copy(
        update={"summary_code": "impact.none", "status": CheckStatus.PASS}
    )
    checks = [
        unaffected if check.check_id == "impact.products" else check for check in report.checks
    ]
    review = report.model_copy(update={"checks": checks, "verdict": Verdict.NEEDS_REVIEW})

    text = render_pull_request_comment(review)

    assert f"\n{owner_action(Verdict.NEEDS_REVIEW, product_unaffected=True)}\n" in text
    assert "exit code 3" in text
    assert "#### What stops the release" not in text


def test_a_run_that_did_not_complete_says_its_status(
    pilot: tuple[GateReport, Path, ImpactReport],
) -> None:
    report, _, impact = pilot

    failed = report.model_copy(update={"operational_status": RunStatus.FAILED})

    assert "- **Run status:** failed\n" in render_pull_request_comment(failed, impact)
    assert "Run status" not in render_pull_request_comment(report, impact)


def test_every_step_done_is_said_in_place_of_the_steps_not_done(
    pilot: tuple[GateReport, Path, ImpactReport],
) -> None:
    report, _, impact = pilot
    done = [
        (
            check.model_copy(update={"summary_code": "dwh.answered"})
            if check.summary_code == "check.not_configured"
            else check
        )
        for check in report.checks
    ]

    text = render_pull_request_comment(report.model_copy(update={"checks": done}), impact)

    assert "#### Not done in this run\n\n- every step of §8.2 produced evidence in this run\n" in (
        text
    )


# --- caps and the limit ---------------------------------------------------------------------


def test_a_value_is_cut_at_its_cap_and_a_list_past_its_cap_says_how_many_more(
    pilot: tuple[GateReport, Path, ImpactReport],
) -> None:
    report, _, impact = pilot
    item = impact.impacts[0]
    chart = item.charts[0]
    many = item.model_copy(
        update={
            "detail": "d" * (DETAIL_CAP + 50),
            "dashboards": [f"dashboard://d{index}@1" for index in range(LIST_CAP + 3)],
            "owners": ["o" * (REF_CAP + 9)],
            "metrics": [item.subject, "metric://sales/revenue"],
            "charts": [
                chart.model_copy(update={"ref": f"{chart.ref}{index}"})
                for index in range(LIST_CAP + 2)
            ],
        }
    )
    changes = [many] * (IMPACT_CAP + 4)

    text = render_pull_request_comment(
        report, impact.model_copy(update={"impacts": changes}), limit=10**7
    )

    assert text.count("- **block** `metric.time_changed`") == IMPACT_CAP
    assert "- and 4 more changes in the evidence summary\n" in text
    assert f"  - {'d' * DETAIL_CAP}…\n" in text
    assert "and 3 more dashboards in the evidence summary\n" in text
    assert "and 2 more charts in the evidence summary\n" in text
    assert f"  - owners: `{'o' * REF_CAP}…`\n" in text
    assert f"  - metrics: `{item.subject}`, `metric://sales/revenue`\n" in text


def test_a_list_past_its_cap_shows_its_first_items_and_no_more(
    pilot: tuple[GateReport, Path, ImpactReport],
) -> None:
    report, _, impact = pilot
    refs = [f"dashboard://d{index}@1" for index in range(LIST_CAP + 3)]
    item = impact.impacts[0].model_copy(update={"dashboards": refs})

    text = render_pull_request_comment(report, impact.model_copy(update={"impacts": [item]}))

    assert [ref for ref in refs if f"`{ref}`" in text] == refs[:LIST_CAP]
    assert "and 3 more dashboards in the evidence summary\n" in text


def test_the_reason_a_run_stopped_for_is_cut_at_its_cap() -> None:
    text = render_pull_request_failure("r" * (REASON_CAP + 7))

    assert f"- **Reason:** `{'r' * REASON_CAP}…`\n" in text


def _flooded(report: GateReport, count: int, check_id: str = "bi.contract") -> GateReport:
    """The report with *count* copies more of the check *check_id*, each detail past its cap."""
    copied = _check(report, check_id)
    more = [
        copied.model_copy(update={"check_id": f"{check_id}.{index}", "detail": "x" * 700})
        for index in range(count)
    ]
    return report.model_copy(update={"checks": [*report.checks, *more]})


def test_a_comment_past_the_limit_leaves_out_sections_from_the_last_one_up(
    pilot: tuple[GateReport, Path, ImpactReport],
) -> None:
    report, _, impact = pilot

    text = render_pull_request_comment(_flooded(report, 200), impact)

    assert _units(text) <= COMMENT_LIMIT
    assert (
        "\nLeft out to fit a pull request comment: Advisory findings, Not done in this run, "
        "Every check. The evidence summary holds them.\n" in text
    )
    assert "#### What needs a human" in text
    assert "#### Advisory findings" not in text
    assert "<details>" not in text
    assert text.endswith(f"--expect-digest {report.evidence_digest}`.\n")


def test_a_comment_under_the_limit_leaves_out_nothing(
    pilot: tuple[GateReport, Path, ImpactReport],
) -> None:
    report, _, impact = pilot

    text = render_pull_request_comment(_flooded(report, 20), impact)

    assert "Left out" not in text
    assert text.count("`bi.contract.") == 2 * 20


def test_the_caps_are_the_numbers_the_spec_gives() -> None:
    # docs/specs/pull-request-comment.md names each of these numbers.
    assert (COMMENT_LIMIT, MIN_LIMIT, REF_CAP, DETAIL_CAP, REASON_CAP, LIST_CAP, IMPACT_CAP) == (
        60_000,
        8_192,
        200,
        600,
        2_000,
        10,
        20,
    )


# A character of two UTF-16 units, and the one a code span costs most for: a value of
# backticks only is fenced by a run longer than its own on each side.
@pytest.mark.parametrize("unit", [chr(0x1F600), "`"])
def test_the_head_and_the_foot_fit_the_least_limit_at_every_cap(
    pilot: tuple[GateReport, Path, ImpactReport], unit: str
) -> None:
    report, _, impact = pilot
    wide = unit * (REF_CAP * 3)
    flooded = _flooded(report, 200, "impact.products")
    checks = [
        (
            check.model_copy(update={"summary_code": wide})
            if check.check_id == "evidence.bundle"
            else check
        )
        for check in flooded.checks
    ]
    worst = flooded.model_copy(
        update={
            "product_ref": wide,
            "run_id": wide,
            "created_at": wide,
            "base_digest": wide,
            "candidate_digest": wide,
            "evidence_digest": wide,
            "operational_status": RunStatus.FAILED,
            "checks": checks,
        }
    )

    text = render_pull_request_comment(worst, impact, limit=MIN_LIMIT)

    assert _units(text) <= MIN_LIMIT
    assert (
        "\nLeft out to fit a pull request comment: What stops the release, Whose numbers this "
        "reaches, What needs a human, Advisory findings, Not done in this run, Every check. "
        "The evidence summary holds them.\n" in text
    )
    assert "- **Run status:** failed\n" in text
    assert text.count(unit * REF_CAP + "…") == 8


def test_the_limit_is_counted_in_utf16_units_not_in_characters(
    pilot: tuple[GateReport, Path, ImpactReport],
) -> None:
    report, _, impact = pilot
    copied = _check(report, "bi.contract")
    wide = [
        copied.model_copy(
            update={"check_id": f"bi.contract.{index}", "detail": chr(0x1F600) * DETAIL_CAP}
        )
        for index in range(20)
    ]
    flooded = report.model_copy(update={"checks": [*report.checks, *wide]})
    whole = render_pull_request_comment(flooded, impact, limit=10**7)
    # Every character of the whole comment fits under this limit; its units do not.
    limit = len(whole)
    assert limit >= MIN_LIMIT and _units(whole) > limit

    text = render_pull_request_comment(flooded, impact, limit=limit)

    assert _units(text) <= limit
    assert "\nLeft out to fit a pull request comment: " in text


@pytest.mark.parametrize("render", ["comment", "failure"])
def test_a_limit_below_the_least_is_refused(
    pilot: tuple[GateReport, Path, ImpactReport], render: str
) -> None:
    report, _, _ = pilot
    with pytest.raises(ValueError, match="at least 8192 units"):
        if render == "comment":
            render_pull_request_comment(report, limit=MIN_LIMIT - 1)
        else:
            render_pull_request_failure("x", limit=MIN_LIMIT - 1)


# --- text is data ---------------------------------------------------------------------------


def test_a_reference_in_a_detail_is_a_code_span_and_the_rest_is_escaped(
    pilot: tuple[GateReport, Path, ImpactReport],
) -> None:
    report, _, _ = pilot
    detail = (
        "ping @octocat and @org/team, see #12, owner/repo#5, GH-7, https://example.com/x "
        "www.example.com (www.x.org) **bold** [a](javascript:x) <img src=x> | 1. ~~s~~ $x$"
    )
    noisy = _check(report, "semantic.diff").model_copy(update={"detail": detail})
    checks = [noisy if check.check_id == "semantic.diff" else check for check in report.checks]

    text = render_pull_request_comment(report.model_copy(update={"checks": checks}))

    assert_inert(text)
    line = next(line for line in text.splitlines() if line.startswith("- `semantic.diff`"))
    for word in ("`@octocat`", "`@org/team,`", "`#12,`", "`owner/repo#5,`", "`GH-7,`"):
        assert word in line
    assert "`https://example.com/x`" in line and "`(www.x.org)`" in line
    assert r"\*\*bold\*\* \[a\]\(javascript\:x\) \<img src\=x\> \| 1\. \~\~s\~\~ \$x\$" in line


@pytest.mark.parametrize("seed", range(40))
def test_any_detail_and_ref_renders_as_the_text_it_is(
    pilot: tuple[GateReport, Path, ImpactReport], seed: int
) -> None:
    report, _, impact = pilot
    rng = random.Random(seed)
    detail = _nasty(rng, rng.randint(0, 80)) + "d"
    ref = _nasty(rng, rng.randint(1, 30))
    noisy = _check(report, "semantic.diff").model_copy(update={"detail": detail})
    checks = [noisy if check.check_id == "semantic.diff" else check for check in report.checks]
    item = impact.impacts[0].model_copy(update={"detail": detail, "owners": [ref]})

    text = render_pull_request_comment(
        report.model_copy(update={"checks": checks, "product_ref": ref}),
        impact.model_copy(update={"impacts": [item]}),
    )

    assert_inert(text)
    words = " ".join(word for word in _visible(detail, DETAIL_CAP).split(" ") if word)
    shown = [_plain(children) for children in _inlines(text)]
    assert f"semantic.diff · semantic.changed — {words}" in shown
    assert words in shown
    assert f"Product: {_visible(ref, REF_CAP)}" in shown
    assert f"owners: {_visible(ref, REF_CAP)}" in shown


@pytest.mark.parametrize("seed", range(10))
def test_any_reason_a_run_stopped_for_renders_as_the_text_it_is(seed: int) -> None:
    rng = random.Random(seed)
    reason = _nasty(rng, rng.randint(1, 120))

    text = render_pull_request_failure(reason)

    assert_inert(text)
    assert f"Reason: {_visible(reason, 2_000)}" in [_plain(c) for c in _inlines(text)]


@pytest.mark.parametrize(
    ("value", "span"),
    [
        ("", "(empty)"),
        ("a`b", "``a`b``"),
        ("`a", "`` `a ``"),
        ("a``", "``` a`` ```"),
        (" a ", "`  a  `"),
        ("   ", "`   `"),
        (" a", "` a`"),
    ],
)
def test_a_ref_is_one_code_span_whatever_it_holds(
    pilot: tuple[GateReport, Path, ImpactReport], value: str, span: str
) -> None:
    report, _, _ = pilot

    text = render_pull_request_comment(report.model_copy(update={"product_ref": value}))

    assert f"- **Product:** {span}\n" in text
    shown = [_plain(children) for children in _inlines(text)]
    assert f"Product: {value or '(empty)'}" in shown


def test_a_pipe_in_a_cell_keeps_its_row(pilot: tuple[GateReport, Path, ImpactReport]) -> None:
    report, _, _ = pilot
    piped = _check(report, "sql.compile").model_copy(update={"summary_code": "sql|compiled"})
    checks = [piped if check.check_id == "sql.compile" else check for check in report.checks]

    text = render_pull_request_comment(report.model_copy(update={"checks": checks}))

    assert r"| `sql.compile` | pass | `sql\|compiled` |" in text
    cells = [
        _plain(children)
        for children in _inlines(text)
        if children and _plain(children) == "sql|compiled"
    ]
    assert cells == ["sql|compiled"]


def test_chart_gaps_are_counted_by_code_in_code_order(
    pilot: tuple[GateReport, Path, ImpactReport],
) -> None:
    report, _, impact = pilot
    gaps = [
        ChartGap(code=code, subject="dashboard://d@1#c", detail="d")
        for code in ("chart.opaque_sql", "chart.metric_unread", "chart.opaque_sql")
    ]
    chart = ChartImpact(
        ref=CHART,
        dashboard="dashboard://weekly_margin@7",
        chart="margin_by_region",
        relation=ChartRelation.READS_FIELD,
        detail="d",
    )
    item = impact.impacts[0].model_copy(update={"charts": [chart]})

    text = render_pull_request_comment(
        report, impact.model_copy(update={"chart_gaps": gaps, "impacts": [item]})
    )

    assert "resolve in whole: `chart.metric_unread` 1, `chart.opaque_sql` 2." in text
    assert f"  - charts: `{CHART}` (reads_field)\n" in text
