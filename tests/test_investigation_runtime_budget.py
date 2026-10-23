"""The runtime budget stops publication at each application boundary, without sleeps."""

from collections.abc import Iterable
from pathlib import Path

import pytest

import auto_bi.investigation.application as application
from auto_bi.investigation.claims import InvestigationBrief
from auto_bi.investigation.data import CanonicalMetricResult
from auto_bi.investigation.model import InvestigationBudget, InvestigationPlan, InvestigationStep
from auto_bi.investigation.skills import SkillResult
from tests.test_investigation_cli import ARTIFACT_NAMES, PLAN_FIXTURE, RESULT_FIXTURE, invoke

START_NS = 87_000_000_000
LIMIT_MS = 7
LIMIT_NS = LIMIT_MS * 1_000_000


class Clock:
    """A nonzero start followed by time advanced only at a selected real work boundary."""

    def __init__(self) -> None:
        self.now = START_NS
        self.started = False

    def __call__(self) -> int:
        if not self.started:
            self.started = True
            return START_NS
        return self.now


@pytest.mark.parametrize(
    "phase", ["before-skill", "after-first-skill", "after-last-skill", "after-compile"]
)
@pytest.mark.parametrize("elapsed_ns", [LIMIT_NS - 1, LIMIT_NS, LIMIT_NS + 1])
def test_runtime_boundary_controls_execution_and_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    phase: str,
    elapsed_ns: int,
) -> None:
    raw = InvestigationPlan.load(PLAN_FIXTURE).model_dump(mode="json")
    raw["budget"]["max_runtime_ms"] = LIMIT_MS
    plan = InvestigationPlan.model_validate(raw)
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(plan.to_yaml(), encoding="utf-8")
    output = tmp_path / "missing" / "output"
    clock = Clock()
    if phase == "before-skill":
        clock.now = START_NS + elapsed_ns

    executed: list[str] = []
    compiled: list[InvestigationBrief] = []
    real_skill = application.run_skill
    real_compile = application.compile_brief
    real_build = application.build_offline_investigation

    def run_skill(
        step: InvestigationStep, data: CanonicalMetricResult, budget: InvestigationBudget
    ) -> SkillResult:
        result = real_skill(step, data, budget)
        executed.append(step.step_id)
        if (phase == "after-first-skill" and step.step_id == plan.steps[0].step_id) or (
            phase == "after-last-skill" and step.step_id == plan.steps[-1].step_id
        ):
            clock.now = START_NS + elapsed_ns
        return result

    def compile_brief(plan: InvestigationPlan, outputs: list[SkillResult]) -> InvestigationBrief:
        brief = real_compile(plan, outputs)
        compiled.append(brief)
        if phase == "after-compile":
            clock.now = START_NS + elapsed_ns
        return brief

    def build(
        plan: InvestigationPlan, canonical_results: Iterable[CanonicalMetricResult]
    ) -> application.OfflineInvestigationArtifacts:
        return real_build(plan, canonical_results, clock_ns=clock)

    monkeypatch.setattr(application, "run_skill", run_skill)
    monkeypatch.setattr(application, "compile_brief", compile_brief)
    monkeypatch.setattr(application, "build_offline_investigation", build)

    exit_code = invoke(output, plan_path, RESULT_FIXTURE)

    captured = capsys.readouterr()
    if elapsed_ns > LIMIT_NS:
        assert exit_code == 2
        assert captured.out == ""
        assert captured.err == f"error: budget.max_runtime_ms exceeded: limit={LIMIT_MS}ms\n"
        assert not output.parent.exists()
        if phase == "before-skill":
            assert executed == []
        elif phase == "after-first-skill":
            assert executed == [plan.steps[0].step_id]
        else:
            assert executed == [step.step_id for step in plan.steps]
        assert bool(compiled) == (phase == "after-compile")
    else:
        assert exit_code == 0
        assert captured.err == ""
        assert "trust    exploratory; human acceptance not measured" in captured.out
        assert executed == [step.step_id for step in plan.steps]
        assert len(compiled) == 1
        assert {path.name for path in output.iterdir()} == ARTIFACT_NAMES
        assert (output / "brief.json").read_bytes() == compiled[0].canonical_bytes()
        assert compiled[0].plan_digest == plan.digest()
        assert len(compiled[0].claims) == 7
