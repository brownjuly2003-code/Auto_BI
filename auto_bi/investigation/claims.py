"""Compile deterministic skill outputs into evidence-backed claims and Markdown blocks."""

from __future__ import annotations

import re
import string
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, ValidationError, model_validator

from auto_bi.evidence.canonical import canonical_bytes, sha256_digest
from auto_bi.evidence.model import Claim, ClaimType, ComparisonSemantics
from auto_bi.investigation.model import InvestigationPlan, InvestigationStep
from auto_bi.investigation.skills import (
    ConcentrationResult,
    ContributionResult,
    DataHealthResult,
    InvestigationGap,
    PeriodVarianceResult,
    SkillResult,
)
from auto_bi.scalars import NonBlankText, Sha256Digest, Slug
from auto_bi.strict import StrictModel

API_VERSION = "auto-bi.io/v2alpha1"

_CAUSAL_WORDING = re.compile(
    r"\b(?:caus(?:e|ed|es|ing|al|ality)|because|due\s+to|" r"причин\w*|вызвал\w*|из-за)\b",
    re.IGNORECASE,
)
_CAUSAL_POLICY_REASON = "causal wording is forbidden in the offline investigation alpha"


class BriefCompilationError(ValueError):
    """The supplied outputs cannot produce a complete, bounded brief."""


class _CausalWordingError(ValueError):
    """Distinguish the wording policy from unexpected block validation failures."""


type EvidenceOutput = Annotated[
    DataHealthResult
    | PeriodVarianceResult
    | ContributionResult
    | ConcentrationResult
    | InvestigationGap,
    Field(discriminator="kind"),
]


class EvidenceBackedResult(StrictModel):
    artifact_id: Slug
    digest: Sha256Digest
    output: EvidenceOutput

    @model_validator(mode="after")
    def _check_digest(self) -> EvidenceBackedResult:
        expected = self.output.digest()
        if self.digest != expected:
            raise ValueError(
                f"evidence digest does not address output {self.artifact_id!r}; "
                f"expected {expected}"
            )
        return self


class BriefBlockKind(StrEnum):
    OBSERVATION = "observation"
    CONTRIBUTION = "contribution"
    GAP = "gap"


class BriefBlock(StrictModel):
    block_id: Slug
    kind: BriefBlockKind
    title: NonBlankText
    body: NonBlankText
    claim_ids: list[Slug] = Field(default_factory=list)
    evidence_refs: list[Slug] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_contract(self) -> BriefBlock:
        for name, values in (
            ("claim_ids", self.claim_ids),
            ("evidence_refs", self.evidence_refs),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"brief block {name} must be unique")
        if self.kind is BriefBlockKind.GAP and self.claim_ids:
            raise ValueError("a gap block cannot present factual claims")
        if self.kind is not BriefBlockKind.GAP and not self.claim_ids:
            raise ValueError("a factual brief block requires at least one claim")
        if _CAUSAL_WORDING.search(f"{self.title}\n{self.body}"):
            raise _CausalWordingError(_CAUSAL_POLICY_REASON)
        return self


class InvestigationBrief(StrictModel):
    api_version: str = Field(default=API_VERSION, pattern=r"^auto-bi\.io/v2alpha1$")
    kind: str = Field(default="InvestigationBrief", pattern=r"^InvestigationBrief$")
    plan_id: Slug
    plan_digest: Sha256Digest
    trust: Literal["exploratory"] = "exploratory"
    evidence: list[EvidenceBackedResult] = Field(min_length=1)
    claims: list[Claim] = Field(default_factory=list)
    blocks: list[BriefBlock] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_references(self) -> InvestigationBrief:
        artifact_ids = [item.artifact_id for item in self.evidence]
        claim_ids = [claim.claim_id for claim in self.claims]
        block_ids = [block.block_id for block in self.blocks]
        for name, values in (
            ("evidence artifact ids", artifact_ids),
            ("claim ids", claim_ids),
            ("block ids", block_ids),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"duplicate {name}")

        known_artifacts = set(artifact_ids)
        known_claims = set(claim_ids)
        used_claims: set[str] = set()
        for claim in self.claims:
            missing = sorted(set(claim.evidence_refs) - known_artifacts)
            if missing:
                raise ValueError(
                    f"claim {claim.claim_id} references missing evidence: {', '.join(missing)}"
                )
        for block in self.blocks:
            missing_evidence = sorted(set(block.evidence_refs) - known_artifacts)
            if missing_evidence:
                raise ValueError(
                    f"block {block.block_id} references missing evidence: "
                    f"{', '.join(missing_evidence)}"
                )
            missing_claims = sorted(set(block.claim_ids) - known_claims)
            if missing_claims:
                raise ValueError(
                    f"block {block.block_id} references missing claims: "
                    f"{', '.join(missing_claims)}"
                )
            used_claims.update(block.claim_ids)
        orphaned = sorted(known_claims - used_claims)
        if orphaned:
            raise ValueError(f"claims are not rendered by any block: {', '.join(orphaned)}")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.model_dump(mode="json"))

    def digest(self) -> str:
        return sha256_digest(self.model_dump(mode="json"))

    def render_markdown(self) -> str:
        evidence_by_id = {item.artifact_id: item for item in self.evidence}
        lines = [
            "# Offline investigation brief",
            "",
            f"- Plan: `{self.plan_id}`",
            f"- Plan digest: `{self.plan_digest}`",
            "- Trust: `exploratory`",
            "- Human acceptance: `not measured`",
        ]
        for block in self.blocks:
            title, body = block.title, block.body
            # Compiler-produced templates retain raw text in JSON and policy checks.
            # Encode only their supplied labels and step ids at the Markdown boundary,
            # including when rendering an already-saved brief. Custom titles and prose
            # stay as stored.
            if len(block.evidence_refs) == 1:
                output = evidence_by_id[block.evidence_refs[0]].output
                if isinstance(output, InvestigationGap):
                    if title == _gap_title(output, markdown=False):
                        title = _gap_title(output, markdown=True)
                    if body == _gap_body(output, markdown=False):
                        body = _gap_body(output, markdown=True)
                elif isinstance(output, PeriodVarianceResult) and body == _variance_body(
                    output, markdown=False
                ):
                    body = _variance_body(output, markdown=True)
                elif isinstance(output, ContributionResult) and body == _contribution_body(
                    output, markdown=False
                ):
                    body = _contribution_body(output, markdown=True)
            lines.extend(["", f"## {title}", "", body, ""])
            evidence = ", ".join(
                f"`{ref}` (`{evidence_by_id[ref].digest}`; "
                f"step `{evidence_by_id[ref].output.step_id}`; "
                f"metric `{evidence_by_id[ref].output.metric_ref}`)"
                for ref in block.evidence_refs
            )
            lines.append(f"Evidence: {evidence}")
            if block.claim_ids:
                claims = ", ".join(f"`{claim_id}`" for claim_id in block.claim_ids)
                lines.append(f"Claims: {claims}")
        return "\n".join(lines) + "\n"


def compile_brief(plan: InvestigationPlan, outputs: list[SkillResult]) -> InvestigationBrief:
    """Compile one complete output per declared step/metric, in plan order."""
    step_by_id = {step.step_id: step for step in plan.steps}
    expected = [(step.step_id, metric_ref) for step in plan.steps for metric_ref in step.metrics]
    by_key: dict[tuple[str, str], SkillResult] = {}
    for output in outputs:
        key = (output.step_id, output.metric_ref)
        if key in by_key:
            raise BriefCompilationError(
                f"duplicate output for step {output.step_id}, metric {output.metric_ref}"
            )
        step = step_by_id.get(output.step_id)
        if step is None or output.metric_ref not in step.metrics:
            raise BriefCompilationError(
                f"unexpected output for step {output.step_id}, metric {output.metric_ref}"
            )
        if output.skill is not step.skill:
            raise BriefCompilationError(
                f"output for step {step.step_id} uses {output.skill.value}, "
                f"expected {step.skill.value}"
            )
        if output.skill_version != step.skill_version:
            raise BriefCompilationError(
                f"output for step {step.step_id} has skill_version {output.skill_version}, "
                f"expected {step.skill_version}"
            )
        if not isinstance(output, InvestigationGap) and output.kind != step.expected_evidence.value:
            raise BriefCompilationError(
                f"output for step {step.step_id} has kind {output.kind}, "
                f"expected {step.expected_evidence.value}"
            )
        if (
            isinstance(output, (ContributionResult, ConcentrationResult))
            and output.dimension != step.dimensions[0]
        ):
            raise BriefCompilationError(
                f"output for step {step.step_id} has dimension {output.dimension!r}, "
                f"expected {step.dimensions[0]!r}"
            )
        if isinstance(output, ConcentrationResult) and output.top_n != step.top_n:
            raise BriefCompilationError(
                f"output for step {step.step_id} has top_n {output.top_n}, "
                f"expected {step.top_n}"
            )
        by_key[key] = output

    missing = [key for key in expected if key not in by_key]
    if missing:
        rendered = ", ".join(f"{step}/{metric}" for step, metric in missing)
        raise BriefCompilationError(f"missing output for: {rendered}")

    evidence: list[EvidenceBackedResult] = []
    claims: list[Claim] = []
    blocks: list[BriefBlock] = []
    for key in expected:
        output = by_key[key]
        step = step_by_id[output.step_id]
        artifact_id = _artifact_id(step, output.metric_ref)
        evidence.append(
            EvidenceBackedResult(
                artifact_id=artifact_id,
                digest=output.digest(),
                output=output,
            )
        )
        try:
            output_claims, block = _compile_output(artifact_id, output)
        except ValidationError as exc:
            errors = exc.errors(include_url=False, include_input=False)
            if len(errors) != 1 or not isinstance(
                errors[0].get("ctx", {}).get("error"), _CausalWordingError
            ):
                raise
            raise BriefCompilationError(_CAUSAL_POLICY_REASON) from exc
        claims.extend(output_claims)
        blocks.append(block)

    if len(claims) > plan.budget.max_output_claims:
        raise BriefCompilationError(
            f"compiled {len(claims)} claims, exceeding "
            f"budget.max_output_claims={plan.budget.max_output_claims}"
        )
    return InvestigationBrief(
        plan_id=plan.plan_id,
        plan_digest=plan.digest(),
        evidence=evidence,
        claims=claims,
        blocks=blocks,
    )


def _artifact_id(step: InvestigationStep, metric_ref: str) -> str:
    # Hash the canonical pair: joining slugs loses both path and step boundaries.
    identity = sha256_digest([step.step_id, metric_ref]).removeprefix("sha256:")
    return f"result-{identity}"


def _compile_output(artifact_id: str, output: SkillResult) -> tuple[list[Claim], BriefBlock]:
    if isinstance(output, InvestigationGap):
        return [], BriefBlock(
            block_id=f"block-{artifact_id}",
            kind=BriefBlockKind.GAP,
            title=_gap_title(output, markdown=False),
            body=_gap_body(output, markdown=False),
            evidence_refs=[artifact_id],
        )
    if isinstance(output, DataHealthResult):
        return _data_health_claims(artifact_id, output)
    if isinstance(output, PeriodVarianceResult):
        return _variance_claims(artifact_id, output)
    if isinstance(output, ContributionResult):
        return _contribution_claims(artifact_id, output)
    if isinstance(output, ConcentrationResult):
        return _concentration_claims(artifact_id, output)
    raise BriefCompilationError(f"unsupported skill output type: {type(output).__name__}")


def _data_health_claims(
    artifact_id: str, output: DataHealthResult
) -> tuple[list[Claim], BriefBlock]:
    claim = Claim(
        claim_id=f"{artifact_id}-health",
        claim_type=ClaimType.OBSERVATION,
        summary_code="investigation.data_health",
        value=str(output.null_values),
        unit="null_values",
        evidence_refs=[artifact_id],
        caveats=[
            f"complete={str(output.complete).lower()}",
            f"current_rows={output.current_rows}",
            f"comparison_rows={output.comparison_rows}",
            f"duplicate_grains={output.duplicate_grains}",
            f"freshness={output.freshness.value}",
        ],
    )
    age = output.freshness_age_seconds or "unknown"
    body = (
        f"complete={str(output.complete).lower()}; current/comparison rows="
        f"{output.current_rows}/{output.comparison_rows}; NULL={output.null_values}; "
        f"duplicate grains={output.duplicate_grains}; freshness={output.freshness.value}; "
        f"age_seconds={age}."
    )
    return [claim], BriefBlock(
        block_id=f"block-{artifact_id}",
        kind=BriefBlockKind.OBSERVATION,
        title="Качество данных",
        body=body,
        claim_ids=[claim.claim_id],
        evidence_refs=[artifact_id],
    )


def _variance_claims(
    artifact_id: str, output: PeriodVarianceResult
) -> tuple[list[Claim], BriefBlock]:
    claim = Claim(
        claim_id=f"{artifact_id}-delta",
        claim_type=ClaimType.OBSERVATION,
        summary_code="investigation.period_variance",
        value=output.absolute_delta,
        unit=output.unit,
        comparison=ComparisonSemantics.ABSOLUTE,
        evidence_refs=[artifact_id],
        caveats=[
            f"comparison_value={output.comparison_value}",
            f"current_value={output.current_value}",
            f"relative_delta={output.relative_delta or 'undefined'}",
        ],
    )
    return [claim], BriefBlock(
        block_id=f"block-{artifact_id}",
        kind=BriefBlockKind.OBSERVATION,
        title="Изменение периода",
        body=_variance_body(output, markdown=False),
        claim_ids=[claim.claim_id],
        evidence_refs=[artifact_id],
    )


def _markdown_literal(value: str) -> str:
    """One inline text value: visible controls and CommonMark-escaped punctuation."""
    visible = "".join(char if char.isprintable() else ascii(char)[1:-1] for char in value)
    return "".join(f"\\{char}" if char in string.punctuation else char for char in visible)


def _gap_title(output: InvestigationGap, *, markdown: bool) -> str:
    # A slug may hold `-_x_-`, which CommonMark would show as emphasis without underscores.
    step_id = _markdown_literal(output.step_id) if markdown else output.step_id
    return f"Gap: {step_id}"


def _gap_body(output: InvestigationGap, *, markdown: bool) -> str:
    detail = _markdown_literal(output.detail) if markdown else output.detail
    return f"Факт не сформирован: `{output.code.value}` — {detail}"


def _variance_body(output: PeriodVarianceResult, *, markdown: bool) -> str:
    unit = _markdown_literal(output.unit) if markdown else output.unit
    relative = output.relative_delta or "undefined"
    return (
        f"Наблюдаемое значение: {output.comparison_value} {unit} -> "
        f"{output.current_value} {unit}; delta {output.absolute_delta} {unit}; "
        f"relative {relative}."
    )


def _contribution_claims(
    artifact_id: str, output: ContributionResult
) -> tuple[list[Claim], BriefBlock]:
    claims = [
        Claim(
            claim_id=f"{artifact_id}-item-{index:03d}",
            claim_type=ClaimType.CONTRIBUTION,
            summary_code="investigation.contribution",
            value=item.delta,
            unit=output.unit,
            comparison=ComparisonSemantics.ABSOLUTE,
            evidence_refs=[artifact_id],
            caveats=[
                f"dimension={output.dimension}",
                f"dimension_value={item.dimension_value}",
                f"share_of_total_delta={item.share_of_total_delta or 'undefined'}",
            ],
        )
        for index, item in enumerate(output.contributions, start=1)
    ]
    return claims, BriefBlock(
        block_id=f"block-{artifact_id}",
        kind=BriefBlockKind.CONTRIBUTION,
        title="Декомпозиция наблюдаемой дельты",
        body=_contribution_body(output, markdown=False),
        claim_ids=[claim.claim_id for claim in claims],
        evidence_refs=[artifact_id],
    )


def _contribution_body(output: ContributionResult, *, markdown: bool) -> str:
    unit = _markdown_literal(output.unit) if markdown else output.unit
    lines = [
        f"Общая наблюдаемая дельта: {output.total_delta} {unit}; "
        f"reconciliation={'pass' if output.reconciled else 'fail'}.",
    ]
    for item in output.contributions:
        category = _markdown_literal(item.dimension_value) if markdown else item.dimension_value
        lines.append(
            f"- {category}: {item.delta} {unit}; "
            f"share {item.share_of_total_delta or 'undefined'}"
        )
    return "\n".join(lines)


def _concentration_claims(
    artifact_id: str, output: ConcentrationResult
) -> tuple[list[Claim], BriefBlock]:
    hhi = Claim(
        claim_id=f"{artifact_id}-hhi-delta",
        claim_type=ClaimType.OBSERVATION,
        summary_code="investigation.concentration_hhi",
        value=output.hhi_delta,
        unit="ratio",
        comparison=ComparisonSemantics.ABSOLUTE,
        evidence_refs=[artifact_id],
        caveats=[
            f"comparison_hhi={output.comparison.hhi}",
            f"current_hhi={output.current.hhi}",
        ],
    )
    top_n = Claim(
        claim_id=f"{artifact_id}-top-n-delta",
        claim_type=ClaimType.OBSERVATION,
        summary_code="investigation.concentration_top_n",
        value=output.top_n_share_delta,
        unit="ratio",
        comparison=ComparisonSemantics.SHARE,
        evidence_refs=[artifact_id],
        caveats=[
            f"top_n={output.top_n}",
            f"comparison_share={output.comparison.top_n_share}",
            f"current_share={output.current.top_n_share}",
        ],
    )
    body = (
        f"HHI: {output.comparison.hhi} -> {output.current.hhi}; "
        f"delta {output.hhi_delta}. top-{output.top_n}: "
        f"{output.comparison.top_n_share} -> {output.current.top_n_share}; "
        f"delta {output.top_n_share_delta}."
    )
    return [hhi, top_n], BriefBlock(
        block_id=f"block-{artifact_id}",
        kind=BriefBlockKind.OBSERVATION,
        title="Изменение концентрации",
        body=body,
        claim_ids=[hhi.claim_id, top_n.claim_id],
        evidence_refs=[artifact_id],
    )
