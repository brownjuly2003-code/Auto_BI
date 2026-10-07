"""Offline application use case for a bounded typed investigation."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from time import monotonic_ns
from typing import Literal

import yaml
from pydantic import Field, ValidationError, model_validator

from auto_bi import filesystem
from auto_bi.evidence.canonical import canonical_bytes
from auto_bi.investigation.claims import (
    BriefCompilationError,
    EvidenceOutput,
    InvestigationBrief,
    compile_brief,
)
from auto_bi.investigation.data import CanonicalMetricResult
from auto_bi.investigation.model import InvestigationPlan
from auto_bi.investigation.skills import GapCode, InvestigationGap, SkillResult, run_skill
from auto_bi.scalars import Sha256Digest, Slug
from auto_bi.strict import StrictModel
from auto_bi.yaml_document import schema_reason, unread_reason

API_VERSION = "auto-bi.io/v2alpha1"
ARTIFACT_NAMES = ("plan.json", "results.json", "brief.json", "brief.md")
_STOP_GAPS = {
    GapCode.BUDGET_INPUT_ROWS_EXCEEDED,
    GapCode.BUDGET_GROUPS_EXCEEDED,
    GapCode.SCOPE_METRIC_MISMATCH,
    GapCode.SCOPE_UNSUPPORTED_DIMENSION,
    GapCode.METHOD_WRONG_SKILL,
}


class OfflineInvestigationError(ValueError):
    """The offline run cannot produce a complete, policy-compliant artifact set."""


class InvestigationResults(StrictModel):
    """Canonical inputs and deterministic skill outputs for one plan."""

    api_version: str = Field(default=API_VERSION, pattern=r"^auto-bi\.io/v2alpha1$")
    kind: Literal["InvestigationResults"] = "InvestigationResults"
    plan_id: Slug
    plan_digest: Sha256Digest
    canonical_results: list[CanonicalMetricResult] = Field(min_length=1)
    outputs: list[EvidenceOutput] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_unique_results(self) -> InvestigationResults:
        metric_refs = [result.metric_ref for result in self.canonical_results]
        if len(set(metric_refs)) != len(metric_refs):
            raise ValueError("canonical result metric refs must be unique")
        output_keys = [(output.step_id, output.metric_ref) for output in self.outputs]
        if len(set(output_keys)) != len(output_keys):
            raise ValueError("skill output step/metric keys must be unique")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.model_dump(mode="json"))


@dataclass(frozen=True)
class OfflineInvestigationArtifacts:
    plan: InvestigationPlan
    results: InvestigationResults
    brief: InvestigationBrief

    def payloads(self) -> Mapping[str, bytes]:
        return {
            "plan.json": canonical_bytes(self.plan.model_dump(mode="json")),
            "results.json": self.results.canonical_bytes(),
            "brief.json": self.brief.canonical_bytes(),
            "brief.md": self.brief.render_markdown().encode("utf-8"),
        }


def build_offline_investigation(
    plan: InvestigationPlan,
    canonical_results: Iterable[CanonicalMetricResult],
    *,
    clock_ns: Callable[[], int] = monotonic_ns,
) -> OfflineInvestigationArtifacts:
    """Execute only declared pure skills and compile their evidence-backed brief."""
    indexed = _index_results(plan, canonical_results)
    ordered_metrics = list(
        dict.fromkeys(metric_ref for step in plan.steps for metric_ref in step.metrics)
    )
    ordered_inputs = [indexed[metric_ref] for metric_ref in ordered_metrics]
    started_ns = clock_ns()
    outputs: list[SkillResult] = []
    for step in plan.steps:
        for metric_ref in step.metrics:
            _check_runtime_budget(plan, started_ns, clock_ns)
            output = run_skill(step, indexed[metric_ref], plan.budget)
            if isinstance(output, InvestigationGap) and output.code in _STOP_GAPS:
                raise OfflineInvestigationError(
                    f"policy stopped at {output.step_id}/{output.metric_ref}: "
                    f"{output.code.value} — {output.detail}"
                )
            outputs.append(output)
            _check_runtime_budget(plan, started_ns, clock_ns)

    try:
        brief = compile_brief(plan, outputs)
    except BriefCompilationError as exc:
        raise OfflineInvestigationError(str(exc)) from exc
    _check_runtime_budget(plan, started_ns, clock_ns)
    results = InvestigationResults(
        plan_id=plan.plan_id,
        plan_digest=plan.digest(),
        canonical_results=ordered_inputs,
        outputs=outputs,
    )
    return OfflineInvestigationArtifacts(plan=plan, results=results, brief=brief)


def write_offline_investigation(
    plan_path: str | Path,
    result_paths: Iterable[str | Path],
    output_dir: str | Path,
) -> OfflineInvestigationArtifacts:
    """Load strict YAML inputs and write a new deterministic local artifact directory."""
    plan = _load_input(plan_path, InvestigationPlan)
    inputs = [_load_input(path, CanonicalMetricResult) for path in result_paths]

    artifacts = build_offline_investigation(plan, inputs)
    payloads = artifacts.payloads()
    target = Path(output_dir)
    staging: Path | None = None
    try:
        if filesystem.is_link(target) or filesystem.exists(target):
            raise OfflineInvestigationError(f"output directory already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".investigation-", dir=target.parent))
        for name in ARTIFACT_NAMES:
            (staging / name).write_bytes(payloads[name])
        if filesystem.is_link(target) or filesystem.exists(target):
            raise OfflineInvestigationError(f"output directory already exists: {target}")
        os.rename(staging, target)
        staging = None
    except (OfflineInvestigationError, OSError) as exc:
        if staging is not None:
            try:
                shutil.rmtree(staging)
            except OSError as cleanup_exc:
                reason = (
                    str(exc)
                    if isinstance(exc, OfflineInvestigationError)
                    else f"cannot write output directory {target}: {exc}"
                )
                raise OfflineInvestigationError(
                    f"{reason}; cannot clean temporary output directory {staging}: {cleanup_exc}"
                ) from cleanup_exc
        if isinstance(exc, OfflineInvestigationError):
            raise
        raise OfflineInvestigationError(f"cannot write output directory {target}: {exc}") from exc
    return artifacts


def _load_input[Input: (InvestigationPlan, CanonicalMetricResult)](
    path: str | Path, kind: type[Input]
) -> Input:
    """The file at *path* as *kind* — or the one line that names the file and says why not.

    Each input is answered for by its own path: a run is handed several results, and an
    error that names none leaves whoever ran it to open each. A file that cannot be opened,
    is not UTF-8 or holds no YAML document that can be built was not read. A file that was
    read and is not what *kind* describes is named with what the schema refuses.
    """
    try:
        return kind.load(path)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise OfflineInvestigationError(f"cannot read {path}: {unread_reason(exc)}") from exc
    except ValidationError as exc:
        raise OfflineInvestigationError(
            f"{path} is not a valid {kind.__name__}: {schema_reason(exc)}"
        ) from exc


def _index_results(
    plan: InvestigationPlan,
    canonical_results: Iterable[CanonicalMetricResult],
) -> dict[str, CanonicalMetricResult]:
    indexed: dict[str, CanonicalMetricResult] = {}
    for result in canonical_results:
        if result.metric_ref in indexed:
            raise OfflineInvestigationError(
                f"duplicate canonical result for metric {result.metric_ref}"
            )
        indexed[result.metric_ref] = result

    expected = {metric_ref for step in plan.steps for metric_ref in step.metrics}
    missing = sorted(expected - set(indexed))
    if missing:
        raise OfflineInvestigationError(f"missing canonical result for: {', '.join(missing)}")
    unexpected = sorted(set(indexed) - expected)
    if unexpected:
        raise OfflineInvestigationError(
            f"unexpected canonical result outside declared steps: {', '.join(unexpected)}"
        )
    return indexed


def _check_runtime_budget(
    plan: InvestigationPlan,
    started_ns: int,
    clock_ns: Callable[[], int],
) -> None:
    elapsed_ns = clock_ns() - started_ns
    if elapsed_ns > plan.budget.max_runtime_ms * 1_000_000:
        raise OfflineInvestigationError(
            "budget.max_runtime_ms exceeded: " f"limit={plan.budget.max_runtime_ms}ms"
        )
