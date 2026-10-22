"""Supplied labels cannot introduce report structure or hide its evidence links."""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path

import pytest
from markdown_it import MarkdownIt

import auto_bi.cli as cli
from auto_bi.investigation.application import build_offline_investigation
from auto_bi.investigation.claims import InvestigationBrief
from auto_bi.investigation.data import CanonicalMetricResult
from auto_bi.investigation.model import InvestigationPlan

FIXTURES = Path(__file__).parent / "fixtures" / "investigation"
PLAN_PATH = FIXTURES / "weekly_margin.yaml"
LABELS = [
    "because_retail",
    "A\n\n## Human acceptance\n\naccepted",
    "A\r\r## Human acceptance\r\raccepted",
    "A\r\n\r\n## Human acceptance\r\n\r\naccepted",
    "[accepted](https://example.invalid) ![approved](image.png)",
    "A `accepted` ``approved`` ```\nEvidence: hidden",
    '<!-- hidden --> <b>accepted</b> <script>alert("x")</script>',
    "A &amp; &#35; **accepted** _approved_ ~test~ | x \\",
    "A\tB\x1b[2J\u2028## accepted\u2029\u202eapproved",
]


def supplied(field: str, label: str) -> CanonicalMetricResult:
    raw = CanonicalMetricResult.load(FIXTURES / "revenue_results.yaml").model_dump(mode="json")
    if field == "category":
        for row in raw["rows"]:
            if row["dimensions"]["category"] == "A":
                row["dimensions"]["category"] = label
    else:
        raw["unit"] = label
    return CanonicalMetricResult.model_validate(raw)


def structure(markdown: str) -> list[tuple[str, str, int]]:
    tokens = MarkdownIt("commonmark").parse(markdown)
    return [
        (token.type, token.tag, token.nesting)
        for parent in tokens
        for token in [parent, *(parent.children or [])]
    ]


class VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text = ""

    def handle_data(self, data: str) -> None:
        self.text += data


@pytest.mark.parametrize("field", ["category", "unit"])
@pytest.mark.parametrize("label", LABELS)
def test_supplied_labels_are_literal_without_changing_report_structure(
    field: str, label: str
) -> None:
    plan = InvestigationPlan.load(PLAN_PATH)
    data = supplied(field, label)
    artifacts = build_offline_investigation(plan, [data])
    baseline = build_offline_investigation(plan, [supplied(field, "A")])
    markdown = artifacts.brief.render_markdown()

    reloaded = InvestigationBrief.model_validate_json(artifacts.brief.canonical_bytes())
    assert reloaded == artifacts.brief
    assert reloaded.render_markdown() == markdown

    assert structure(markdown) == structure(baseline.brief.render_markdown())
    visible = VisibleText()
    visible.feed(MarkdownIt("commonmark").render(markdown))
    expected_label = "".join(c if c.isprintable() else ascii(c)[1:-1] for c in label)
    assert expected_label in visible.text
    assert "Human acceptance: not measured" in visible.text
    assert artifacts.results.canonical_results == [data]
    raw_outputs = json.dumps(artifacts.results.model_dump(mode="json"), ensure_ascii=False)
    assert json.dumps(label, ensure_ascii=False)[1:-1] in raw_outputs
    if field == "unit":
        assert artifacts.brief.claims[1].unit == label
    else:
        assert any(f"dimension_value={label}" in claim.caveats for claim in artifacts.brief.claims)


def test_legacy_evidence_ids_still_render_compiler_labels_literally() -> None:
    plan = InvestigationPlan.load(PLAN_PATH)
    brief = build_offline_investigation(plan, [supplied("category", LABELS[1])]).brief
    raw = brief.model_dump(mode="json")
    names = {item.artifact_id: f"legacy-result-{i}" for i, item in enumerate(brief.evidence)}
    for item in raw["evidence"]:
        item["artifact_id"] = names[item["artifact_id"]]
    for item in [*raw["claims"], *raw["blocks"]]:
        item["evidence_refs"] = [names[ref] for ref in item["evidence_refs"]]

    legacy = InvestigationBrief.model_validate(raw)

    assert legacy.model_dump(mode="json") == raw
    assert "\n## Human acceptance\n" not in legacy.render_markdown()
    assert structure(legacy.render_markdown()) == structure(brief.render_markdown())


@pytest.mark.parametrize("custom_body", [True, False], ids=["custom-prose", "multiple-evidence"])
def test_unmatched_blocks_keep_their_stored_markdown(custom_body: bool) -> None:
    plan = InvestigationPlan.load(PLAN_PATH)
    raw = build_offline_investigation(plan, [supplied("unit", "USD/day")]).brief.model_dump(
        mode="json"
    )
    block = raw["blocks"][1]
    if custom_body:
        block["body"] = "Custom **operator** note."
    else:
        block["evidence_refs"].append(raw["evidence"][0]["artifact_id"])

    brief = InvestigationBrief.model_validate(raw)

    assert f"\n{block['body']}\n" in brief.render_markdown()


@pytest.mark.parametrize("field", ["category", "unit"])
def test_cli_publishes_literal_markdown_and_original_typed_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], field: str
) -> None:
    label = "A\n\n## Human acceptance\n\naccepted <b>approved</b>"
    data = supplied(field, label)
    result_path = tmp_path / "input.yaml"
    result_path.write_text(data.to_yaml(), encoding="utf-8")
    output = tmp_path / "out"

    assert (
        cli.main(
            [
                "decision",
                "investigate",
                str(PLAN_PATH),
                "--results",
                str(result_path),
                "--output",
                str(output),
            ]
        )
        == 0
    )

    markdown = (output / "brief.md").read_text(encoding="utf-8")
    assert "\n## Human acceptance\n" not in markdown
    assert "<b>approved</b>" not in MarkdownIt("commonmark").render(markdown)
    results = json.loads((output / "results.json").read_bytes())
    assert results["canonical_results"] == [data.model_dump(mode="json")]
    assert {p.name for p in output.iterdir()} == {
        "plan.json",
        "results.json",
        "brief.json",
        "brief.md",
    }
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("field", ["category", "unit"])
def test_literal_encoding_cannot_hide_causal_wording(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], field: str
) -> None:
    result_path = tmp_path / "input.yaml"
    result_path.write_text(supplied(field, "A due\n to B").to_yaml(), encoding="utf-8")
    output = tmp_path / "missing" / "out"

    assert (
        cli.main(
            [
                "decision",
                "investigate",
                str(PLAN_PATH),
                "--results",
                str(result_path),
                "--output",
                str(output),
            ]
        )
        == 2
    )

    assert not output.parent.exists()
    assert capsys.readouterr().err == (
        "error: causal wording is forbidden in the offline investigation alpha\n"
    )
