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
    # PLAN 2.8: 25 golden cases including fields-first and iterations (+1 joins)
    # (PLAN 1.11 base: clear/ambiguous/infeasible + >=5 seeded anti-patterns)
    assert len(GOLDEN_CASES) == 26
    kinds = {k: sum(c.kind == k for c in GOLDEN_CASES) for k in CaseKind}
    assert kinds[CaseKind.CLEAR] >= 8
    assert kinds[CaseKind.AMBIGUOUS] >= 4
    assert kinds[CaseKind.INFEASIBLE] >= 4
    assert sum(c.seed is not None for c in GOLDEN_CASES) >= 3  # fields-first entries
    assert sum(bool(c.edit) for c in GOLDEN_CASES) >= 4  # iteration entries
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


def test_golden_iteration_case_checks_patched_spec(demo_model) -> None:
    case = next(c for c in GOLDEN_CASES if c.id == "it1_add_orders")
    patched = {
        **GOOD_SPEC,
        "charts": GOOD_SPEC["charts"]
        + [
            {
                "id": "c2",
                "title": "Заказы по дням",
                "viz": "line",
                "query": {
                    "table": "dm.sales_daily",
                    "dimensions": ["date"],
                    "measures": [{"column": "orders", "agg": "sum"}],
                },
            }
        ],
    }
    llm = ScriptedLLM([CLEAR_REPORT, GOOD_SPEC, patched])
    report = run_golden_suite(demo_model, llm, cases=[case])
    (result,) = report.results
    assert result.passed, result.detail
    # the edit prompt actually went to the LLM
    assert "Добавь на дашборд" in llm.calls[-1][1]

    # an edit that does NOT add the expected column -> fail with the reason
    llm = ScriptedLLM([CLEAR_REPORT, GOOD_SPEC, GOOD_SPEC])
    report = run_golden_suite(demo_model, llm, cases=[case])
    (result,) = report.results
    assert not result.passed
    assert "did not add" in result.detail


def test_eval_survives_broken_case(demo_model) -> None:
    class ExplodingLLM:
        def complete(self, *a, **kw):
            raise RuntimeError("boom")

    case = next(c for c in GOLDEN_CASES if c.id == "g1_revenue_by_day")
    report = run_golden_suite(demo_model, ExplodingLLM(), cases=[case])
    (result,) = report.results
    assert not result.passed
    assert "boom" in result.detail
