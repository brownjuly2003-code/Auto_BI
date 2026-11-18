"""The E3 record of the gate on the inventory snapshot on GitHub (ADR 0013, demo PR #108).

The bundle of the `block` run was downloaded from the workflow artifact and committed byte for
byte; the pull request comment was saved after each of the two runs. These tests hold the record
to what it claims: the committed bundle is the one the comment names, both runs wrote one
comment, and the dashboard the change reaches is the one of the example's snapshot.
"""

from __future__ import annotations

import re
from pathlib import Path

from auto_bi.cli import main
from auto_bi.verification.model import Verdict, exit_code_for

REPO = Path(__file__).resolve().parents[1]
RECORD = REPO / "docs" / "pilot" / "2026-11-18-e3-inventory-snapshot"
RUN = "run-ef964cc8a6ce7f3c"
_DIGEST = re.compile(r"\*\*Evidence manifest:\*\* `(sha256:[0-9a-f]{64})`")
_RUN = re.compile(r"\*\*Run:\*\* `(run-[0-9a-f]{16})`")
_COUNTS = re.compile(r"\(checks: (\d+) block, (\d+) review, (\d+) advisory, of (\d+)\)")


def _comment(name: str) -> str:
    return (RECORD / "pr-comment" / name).read_bytes().decode("utf-8")


def _one(pattern: re.Pattern[str], text: str) -> str:
    (value,) = pattern.findall(text)
    return value


def test_the_block_bundle_is_the_one_the_comment_names() -> None:
    second = _comment("after-run-2.md")
    assert "### Auto_BI release gate: block" in second
    assert _one(_RUN, second) == RUN
    evidence = str(RECORD / "evidence" / RUN)
    assert main(["decision", "evidence", evidence, "--expect-digest", _one(_DIGEST, second)]) == 0
    # The first run's digest names another bundle: the check refuses it.
    stale = _one(_DIGEST, _comment("after-run-1.md"))
    assert main(["decision", "evidence", evidence, "--expect-digest", stale]) == exit_code_for(
        Verdict.BLOCK
    )


def test_both_runs_wrote_one_comment_with_the_outcomes_recorded_beforehand() -> None:
    first, second = _comment("after-run-1.md"), _comment("after-run-2.md")
    marker = first.splitlines()[0]
    assert re.fullmatch(r"<!-- auto-bi-decision-gate:[0-9a-f]{16} -->", marker)
    assert second.splitlines()[0] == marker
    assert "### Auto_BI release gate: pass_with_advisory" in first
    assert _one(_DIGEST, first) != _one(_DIGEST, second)
    # The example's README table: yaml-comment and timezone-change on the snapshot.
    assert _COUNTS.findall(first) == [("0", "0", "7", "15")]
    assert _COUNTS.findall(second) == [("1", "1", "7", "15")]


def test_the_change_reaches_the_snapshot_dashboard_but_names_no_tile() -> None:
    second = _comment("after-run-2.md")
    dashboards = REPO / "examples/decisionops-gate/dashboards"
    assert sorted(p.name for p in dashboards.rglob("*.yaml")) == ["weekly_margin@7.yaml"]
    assert "  - dashboards: `dashboard://weekly_margin@7`" in second
    # The margin tile is a ratio SQL metric the inventory does not type (E4b): a gap, not a name.
    assert "0 delivered chart\\(s\\) named" in second
    assert "`chart.metric_unread` 2, `chart.sql_partial` 2" in second


def test_the_record_names_the_digests_of_the_comments() -> None:
    readme = (RECORD / "README.md").read_bytes().decode("utf-8")
    for name in ("after-run-1.md", "after-run-2.md"):
        comment = _comment(name)
        assert f"`{_one(_DIGEST, comment)}`" in readme
        assert f"`{_one(_RUN, comment)}`" in readme
