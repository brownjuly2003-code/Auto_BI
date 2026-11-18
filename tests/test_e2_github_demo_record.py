"""The E2 record of the gate on GitHub (docs/pilot/2026-11-17-e2-github-demo, ADR 0013).

The bundle of the `block` run was downloaded from the workflow artifact and committed byte for
byte; the pull request comment was saved after each of the two runs. These tests hold the record
to what it claims: the committed bundle is the one the comment names, and both runs wrote one
comment.
"""

from __future__ import annotations

import re
from pathlib import Path

from auto_bi.cli import main
from auto_bi.verification.model import Verdict, exit_code_for

RECORD = Path(__file__).resolve().parents[1] / "docs" / "pilot" / "2026-11-17-e2-github-demo"
RUN = "run-d57b54f44a9c2240"
_DIGEST = re.compile(r"\*\*Evidence manifest:\*\* `(sha256:[0-9a-f]{64})`")
_RUN = re.compile(r"\*\*Run:\*\* `(run-[0-9a-f]{16})`")


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


def test_both_runs_wrote_one_comment() -> None:
    first, second = _comment("after-run-1.md"), _comment("after-run-2.md")
    marker = first.splitlines()[0]
    assert re.fullmatch(r"<!-- auto-bi-decision-gate:[0-9a-f]{16} -->", marker)
    assert second.splitlines()[0] == marker
    assert "### Auto_BI release gate: pass_with_advisory" in first
    assert _one(_DIGEST, first) != _one(_DIGEST, second)


def test_the_record_names_the_digests_of_the_comments() -> None:
    readme = (RECORD / "README.md").read_bytes().decode("utf-8")
    for name in ("after-run-1.md", "after-run-2.md"):
        comment = _comment(name)
        assert f"`{_one(_DIGEST, comment)}`" in readme
        assert f"`{_one(_RUN, comment)}`" in readme
