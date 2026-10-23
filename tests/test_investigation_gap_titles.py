"""A grammar-valid step id cannot turn the heading of its GAP block into emphasis."""

from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path

import pytest
from markdown_it import MarkdownIt

from auto_bi.investigation.application import build_offline_investigation
from auto_bi.investigation.claims import InvestigationBrief
from auto_bi.investigation.data import CanonicalMetricResult
from auto_bi.investigation.model import InvestigationPlan
from auto_bi.investigation.skills import InvestigationGap
from tests.test_investigation_cli import invoke
from tests.test_investigation_markdown_literals import VisibleText, gap_inputs, structure

# Slugs allow `_`, `-` and `.`; CommonMark reads `_` between punctuation and a letter as
# the opening or closing of emphasis.
STEP_IDS = [
    ("null-_dimension_-check", "share-_top_"),
    ("a._b_.c", "concentration"),
]


def _arguments(step_ids: tuple[str, str]) -> tuple[InvestigationPlan, list[CanonicalMetricResult]]:
    """Both steps of the plan end in a GAP: the supplied dimension holds a NULL."""
    plan, data = gap_inputs("region")
    raw = plan.model_dump(mode="json")
    for step, step_id in zip(raw["steps"], step_ids, strict=True):
        step["step_id"] = step_id
    return InvestigationPlan.model_validate(raw), [data]


def _write_inputs(tmp_path: Path, step_ids: tuple[str, str]) -> tuple[Path, Path]:
    plan, data = _arguments(step_ids)
    plan_path, result_path = tmp_path / "plan.yaml", tmp_path / "result.yaml"
    plan_path.write_text(plan.to_yaml(), encoding="utf-8")
    result_path.write_text(data[0].to_yaml(), encoding="utf-8")
    return plan_path, result_path


def headings(markdown: str) -> list[str]:
    """The text a CommonMark reader sees in each second-level heading."""
    tokens = MarkdownIt("commonmark").parse(markdown)
    return [
        "".join(child.content for child in (inline.children or []))
        for heading, inline in pairwise(tokens)
        if heading.type == "heading_open" and heading.tag == "h2"
    ]


@pytest.mark.parametrize("step_ids", STEP_IDS, ids=lambda pair: pair[0])
def test_gap_headings_show_the_step_id_without_changing_json(step_ids: tuple[str, str]) -> None:
    artifacts = build_offline_investigation(*_arguments(step_ids))
    baseline = build_offline_investigation(*_arguments(("contribution", "concentration")))
    before = artifacts.brief.canonical_bytes()
    markdown = artifacts.brief.render_markdown()

    assert [block.title for block in artifacts.brief.blocks] == [f"Gap: {s}" for s in step_ids]
    for evidence in artifacts.brief.evidence:
        assert isinstance(evidence.output, InvestigationGap)
    assert headings(markdown) == [f"Gap: {step_id}" for step_id in step_ids]
    rendered = MarkdownIt("commonmark").render(markdown)
    assert "<em>" not in rendered
    assert structure(markdown) == structure(baseline.brief.render_markdown())
    visible = VisibleText()
    visible.feed(rendered)
    for step_id in step_ids:
        assert f"step {step_id}" in visible.text
    assert artifacts.brief.canonical_bytes() == before
    assert InvestigationBrief.model_validate_json(before).render_markdown() == markdown


@pytest.mark.parametrize(
    ("step_id", "line"),
    [
        ("contribution", "## Gap: contribution"),
        ("weekly_margin", r"## Gap: weekly\_margin"),
        ("x-y.z", r"## Gap: x\-y\.z"),
    ],
)
def test_gap_heading_bytes_escape_every_punctuation_mark_of_the_step_id(
    step_id: str, line: str
) -> None:
    # Visible text alone would also pass if only `_` were escaped.
    brief = build_offline_investigation(*_arguments((step_id, "concentration"))).brief
    markdown = brief.render_markdown()

    assert brief.blocks[0].title == f"Gap: {step_id}"
    assert f"\n{line}\n" in markdown
    assert "\n## Gap: concentration\n" in markdown
    assert headings(markdown) == [f"Gap: {step_id}", "Gap: concentration"]


@pytest.mark.parametrize(
    "compatibility", ["legacy-id", "custom-body", "custom-title", "multiple-evidence"]
)
def test_stored_gap_headings_keep_their_compatibility(compatibility: str) -> None:
    step_ids = STEP_IDS[0]
    raw = build_offline_investigation(*_arguments(step_ids)).brief.model_dump(mode="json")
    block = raw["blocks"][0]
    if compatibility == "legacy-id":
        raw["evidence"][0]["artifact_id"] = "legacy-result"
        block["evidence_refs"] = ["legacy-result"]
    elif compatibility == "custom-body":
        block["body"] = "Custom operator note."
    elif compatibility == "custom-title":
        block["title"] = "Gap: custom _operator_ note"
    else:
        block["evidence_refs"].append(raw["evidence"][1]["artifact_id"])
    brief = InvestigationBrief.model_validate(raw)

    markdown = brief.render_markdown()

    assert brief.model_dump(mode="json") == raw
    if compatibility in {"legacy-id", "custom-body"}:
        assert headings(markdown)[0] == f"Gap: {step_ids[0]}"
    else:
        assert f"\n## {block['title']}\n" in markdown


def test_cli_publishes_literal_gap_headings_and_raw_titles(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    step_ids = STEP_IDS[0]
    output = tmp_path / "out"

    assert invoke(output, *_write_inputs(tmp_path, step_ids)) == 0

    assert capsys.readouterr().err == ""
    markdown = (output / "brief.md").read_text(encoding="utf-8")
    assert headings(markdown) == [f"Gap: {step_id}" for step_id in step_ids]
    stored = json.loads((output / "brief.json").read_bytes())
    assert [block["title"] for block in stored["blocks"]] == [f"Gap: {s}" for s in step_ids]


def test_causal_wording_in_a_gap_step_id_is_still_refused_before_any_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "missing" / "out"

    assert invoke(output, *_write_inputs(tmp_path, ("because", "concentration"))) == 2

    assert not output.parent.exists()
    assert capsys.readouterr().err == (
        "error: causal wording is forbidden in the offline investigation alpha\n"
    )
