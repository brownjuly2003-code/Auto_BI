"""Evidence-backed claims and deterministic Markdown for investigation outputs."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from auto_bi.evidence.model import ClaimType
from auto_bi.investigation.claims import (
    BriefBlock,
    BriefBlockKind,
    BriefCompilationError,
    EvidenceBackedResult,
    InvestigationBrief,
    compile_brief,
)
from auto_bi.investigation.data import CanonicalMetricResult
from auto_bi.investigation.model import InvestigationPlan
from auto_bi.investigation.skills import SkillResult, run_skill

FIXTURES = Path(__file__).parent / "fixtures" / "investigation"
PLAN_FIXTURE = FIXTURES / "weekly_margin.yaml"
RESULT_FIXTURE = FIXTURES / "revenue_results.yaml"


def inputs() -> tuple[InvestigationPlan, list[SkillResult]]:
    plan = InvestigationPlan.load(PLAN_FIXTURE)
    data = CanonicalMetricResult.load(RESULT_FIXTURE)
    outputs = [run_skill(step, data, plan.budget) for step in plan.steps]
    return plan, outputs


def test_every_factual_claim_and_block_is_backed_by_real_evidence() -> None:
    plan, outputs = inputs()
    brief = compile_brief(plan, outputs)
    artifact_ids = {item.artifact_id for item in brief.evidence}

    assert len(brief.claims) == 7
    assert {claim.claim_type for claim in brief.claims} == {
        ClaimType.OBSERVATION,
        ClaimType.CONTRIBUTION,
    }
    assert all(claim.evidence_refs for claim in brief.claims)
    assert all(set(claim.evidence_refs) <= artifact_ids for claim in brief.claims)
    assert all(block.evidence_refs for block in brief.blocks)
    assert all(set(block.evidence_refs) <= artifact_ids for block in brief.blocks)
    assert all(item.digest == item.output.digest() for item in brief.evidence)


def test_compilation_is_independent_of_output_arrival_order() -> None:
    plan, outputs = inputs()

    first = compile_brief(plan, outputs)
    second = compile_brief(plan, list(reversed(outputs)))

    assert first.canonical_bytes() == second.canonical_bytes()
    assert first.render_markdown().encode("utf-8") == second.render_markdown().encode("utf-8")


def test_markdown_is_deterministic_explicitly_exploratory_and_non_causal() -> None:
    plan, outputs = inputs()
    markdown = compile_brief(plan, outputs).render_markdown()

    assert markdown.startswith("# Offline investigation brief\n")
    assert "Trust: `exploratory`" in markdown
    assert "200 RUB" in markdown and "180 RUB" in markdown and "-20 RUB" in markdown
    assert "A: -30 RUB" in markdown
    assert "HHI" in markdown and "top-2" in markdown
    assert markdown.endswith("\n")
    assert not re.search(r"\b(caused?|because|due to|причин\w*|вызвал\w*|из-за)\b", markdown, re.I)


def test_gap_becomes_an_evidence_backed_gap_block_not_a_fact() -> None:
    plan, outputs = inputs()
    data = CanonicalMetricResult.load(RESULT_FIXTURE)
    raw = data.model_dump(mode="json")
    raw["additivity"] = "non_additive"
    non_additive = CanonicalMetricResult.model_validate(raw)
    outputs[2] = run_skill(plan.steps[2], non_additive, plan.budget)

    brief = compile_brief(plan, outputs)
    gap_block = next(block for block in brief.blocks if block.kind is BriefBlockKind.GAP)

    assert gap_block.claim_ids == []
    assert gap_block.evidence_refs
    assert "method.non_additive_metric" in gap_block.body
    assert all(claim.summary_code != "investigation.contribution" for claim in brief.claims)


def test_missing_or_duplicate_step_output_fails_closed() -> None:
    plan, outputs = inputs()

    with pytest.raises(BriefCompilationError, match="missing output"):
        compile_brief(plan, outputs[:-1])
    with pytest.raises(BriefCompilationError, match="duplicate output"):
        compile_brief(plan, [*outputs, outputs[0]])


def test_claim_budget_is_enforced_before_a_brief_is_returned() -> None:
    plan, outputs = inputs()
    raw = plan.model_dump(mode="json")
    raw["budget"]["max_output_claims"] = 3
    constrained = InvestigationPlan.model_validate(raw)

    with pytest.raises(BriefCompilationError, match="max_output_claims"):
        compile_brief(constrained, outputs)


def test_causal_wording_is_rejected_even_for_an_observation_block() -> None:
    with pytest.raises(ValidationError, match="causal wording"):
        BriefBlock(
            block_id="bad-causal-block",
            kind=BriefBlockKind.OBSERVATION,
            title="Root cause",
            body="Category A caused the decline.",
            claim_ids=["claim-1"],
            evidence_refs=["evidence-1"],
        )


def test_evidence_digest_and_references_are_revalidated_on_parse() -> None:
    plan, outputs = inputs()
    brief = compile_brief(plan, outputs)

    bad_evidence = brief.evidence[0].model_dump(mode="json")
    bad_evidence["digest"] = "sha256:" + "0" * 64
    with pytest.raises(ValidationError, match="digest"):
        EvidenceBackedResult.model_validate(bad_evidence)

    dangling = brief.model_dump(mode="json")
    dangling["claims"][0]["evidence_refs"] = ["missing-evidence"]
    with pytest.raises(ValidationError, match="missing evidence"):
        InvestigationBrief.model_validate(dangling)
