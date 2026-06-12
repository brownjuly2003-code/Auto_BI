"""Eval suite (task 1.11): advisor anti-patterns deterministically, golden machinery
on a scripted LLM. The live golden run is the phase exit-criteria check, not a unit test."""

from auto_bi.eval.cases import ADVISOR_CASES, GOLDEN_CASES, CaseKind
from auto_bi.eval.runner import (
    advisor_suite_ok,
    golden_suite_ok,
    run_advisor_suite,
    run_golden_suite,
)
from auto_bi.semantic.model import SemanticModel
from tests.test_machine import AMBIGUOUS_REPORT, CLEAR_REPORT, GOOD_SPEC, ScriptedLLM

REPO_MODEL = SemanticModel.load("semantic/model.yaml")


def test_case_inventory_matches_plan() -> None:
    # PLAN 1.11: 15 golden (clear/ambiguous/infeasible) + >=5 seeded anti-patterns
    assert len(GOLDEN_CASES) == 15
    kinds = {k: sum(c.kind == k for c in GOLDEN_CASES) for k in CaseKind}
    assert kinds[CaseKind.CLEAR] >= 8
    assert kinds[CaseKind.AMBIGUOUS] >= 3
    assert kinds[CaseKind.INFEASIBLE] >= 3
    seeded = [c for c in ADVISOR_CASES if not c.expect_clean]
    clean = [c for c in ADVISOR_CASES if c.expect_clean]
    assert len(seeded) >= 5
    assert len(clean) >= 3


def test_advisor_suite_passes_on_repo_model() -> None:
    # Phase 1 exit criterion, deterministic half: every seeded anti-pattern is caught
    # with the right rule AND clean cases produce zero findings
    report = run_advisor_suite(REPO_MODEL)
    failures = [r for r in report.results if not r.passed]
    assert advisor_suite_ok(report), failures


def test_advisor_seeding_does_not_mutate_the_model() -> None:
    before = REPO_MODEL.table("dm.sales_daily").physical.cardinality.get("manager_id")
    run_advisor_suite(REPO_MODEL)
    after = REPO_MODEL.table("dm.sales_daily").physical.cardinality.get("manager_id")
    assert before == after  # seeded cases work on a deep copy


def test_golden_clear_case_passes_on_good_flow(demo_model) -> None:
    case = next(c for c in GOLDEN_CASES if c.id == "g1_revenue_by_day")
    llm = ScriptedLLM([CLEAR_REPORT, GOOD_SPEC])
    report = run_golden_suite(demo_model, llm, cases=[case])
    (result,) = report.results
    assert result.passed, result.detail


def test_golden_clear_case_fails_on_stray_question(demo_model) -> None:
    case = next(c for c in GOLDEN_CASES if c.id == "g1_revenue_by_day")
    llm = ScriptedLLM([AMBIGUOUS_REPORT])  # agent asks -> clear case must fail
    report = run_golden_suite(demo_model, llm, cases=[case])
    (result,) = report.results
    assert not result.passed
    assert "questions" in result.detail


def test_golden_infeasible_case_requires_flagging(demo_model) -> None:
    case = next(c for c in GOLDEN_CASES if c.id == "i2_returns")
    flagged_report = {
        "tables": [],
        "matched": [],
        "ambiguous": [],
        "unmatched": ["возвраты"],
    }
    llm = ScriptedLLM([flagged_report])
    report = run_golden_suite(demo_model, llm, cases=[case])
    assert report.results[0].passed

    # hallucinating a dashboard instead of flagging -> fail
    llm = ScriptedLLM([CLEAR_REPORT, GOOD_SPEC])
    report = run_golden_suite(demo_model, llm, cases=[case])
    assert not report.results[0].passed


def test_golden_suite_thresholds(demo_model) -> None:
    # 80% threshold applies to clear cases; flagged cases are all-or-nothing
    from auto_bi.eval.runner import CaseResult, EvalReport

    report = EvalReport(
        results=[CaseResult(case_id=f"g{i}", kind="clear", passed=i > 1) for i in range(10)]
        + [CaseResult(case_id="a1", kind="ambiguous", passed=True)]
    )
    assert golden_suite_ok(report)  # 8/10 clear = 80%
    report.results.append(CaseResult(case_id="i1", kind="infeasible", passed=False))
    assert not golden_suite_ok(report)


def test_eval_survives_broken_case(demo_model) -> None:
    class ExplodingLLM:
        def complete(self, *a, **kw):
            raise RuntimeError("boom")

    case = next(c for c in GOLDEN_CASES if c.id == "g1_revenue_by_day")
    report = run_golden_suite(demo_model, ExplodingLLM(), cases=[case])
    (result,) = report.results
    assert not result.passed
    assert "boom" in result.detail
