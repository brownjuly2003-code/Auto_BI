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
    # PLAN 2.8: 25 golden cases including fields-first and iterations (+1 joins);
    # S01 adds 12 analytical-core cases and converts a3_avg_ticket to clear (ratio)
    # (PLAN 1.11 base: clear/ambiguous/infeasible + >=5 seeded anti-patterns)
    assert len(GOLDEN_CASES) == 37
    kinds = {k: sum(c.kind == k for c in GOLDEN_CASES) for k in CaseKind}
    assert kinds[CaseKind.CLEAR] >= 8
    assert kinds[CaseKind.AMBIGUOUS] >= 3
    assert kinds[CaseKind.INFEASIBLE] >= 4
    assert sum(c.seed is not None for c in GOLDEN_CASES) >= 3  # fields-first entries
    assert sum(bool(c.edit) for c in GOLDEN_CASES) >= 5  # iteration entries
    # S01 coverage: every analytical primitive of the IR is exercised by some case
    core = [c for c in GOLDEN_CASES if c.kind == CaseKind.CLEAR]
    exercised = {t for c in core for t in c.expect_transforms}
    from auto_bi.ir.spec import MeasureTransform

    assert exercised >= {
        MeasureTransform.YOY_PCT,
        MeasureTransform.POP_PCT,
        MeasureTransform.RUNNING_TOTAL,
        MeasureTransform.RUNNING_SHARE,
        MeasureTransform.SHARE_OF_TOTAL,
    }
    assert any(c.expect_ratio for c in core)
    assert any(c.expect_time_grain for c in core)
    assert any(c.expect_bins for c in core)
    assert any(c.expect_lag for c in core)
    assert any(c.edit_expect_transforms for c in GOLDEN_CASES)
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


def test_golden_case_drives_begin_end_case_hooks_when_present(demo_model) -> None:
    # eval/runner.py duck-types begin_case/end_case (llm/fixture.py record/replay clients);
    # ScriptedLLM has neither, so ordinary golden tests above never exercise this path.
    class HookedLLM(ScriptedLLM):
        def __init__(self, responses):
            super().__init__(responses)
            self.hook_calls: list[str] = []

        def begin_case(self, case_id: str) -> None:
            self.hook_calls.append(f"begin:{case_id}")

        def end_case(self) -> None:
            self.hook_calls.append("end")

    case = next(c for c in GOLDEN_CASES if c.id == "g1_revenue_by_day")
    llm = HookedLLM([CLEAR_REPORT, GOOD_SPEC])
    report = run_golden_suite(demo_model, llm, cases=[case])
    assert report.results[0].passed
    assert llm.hook_calls == ["begin:g1_revenue_by_day", "end"]


def test_golden_case_calls_end_case_hook_even_on_failure(demo_model) -> None:
    class HookedLLM(ScriptedLLM):
        def __init__(self, responses):
            super().__init__(responses)
            self.ended = False

        def begin_case(self, case_id: str) -> None:
            pass

        def end_case(self) -> None:
            self.ended = True

    case = next(c for c in GOLDEN_CASES if c.id == "g1_revenue_by_day")
    llm = HookedLLM([AMBIGUOUS_REPORT])  # clear case, but agent asks -> fails
    report = run_golden_suite(demo_model, llm, cases=[case])
    assert not report.results[0].passed
    assert llm.ended


# --- S01: analytical-core expectations (ratio / grain / transforms / bins) ----------

MONTHLY_SPEC = {
    "title": "Выручка по месяцам",
    "charts": [
        {
            "id": "c1",
            "title": "Выручка по месяцам",
            "viz": "line",
            "query": {
                "table": "dm.sales_daily",
                "dimensions": ["date"],
                "measures": [{"column": "revenue", "agg": "sum", "label": "Выручка"}],
                "time_grain": "month",
            },
        }
    ],
}


def _core_case(**kwargs):
    from auto_bi.eval.cases import GoldenCase

    defaults = dict(id="t", request="t", kind=CaseKind.CLEAR)
    defaults.update(kwargs)
    return GoldenCase(**defaults)


def _one_chart_spec(query: dict, viz: str = "line"):
    from auto_bi.ir.spec import DashboardSpec

    return DashboardSpec.model_validate(
        {
            "title": "t",
            "charts": [{"id": "c1", "title": "t", "viz": viz, "query": query}],
        }
    )


def test_core_check_requires_the_ratio_primitive() -> None:
    from auto_bi.eval.runner import _check_core, _spec_columns

    case = _core_case(expect_ratio=True, expect_columns={"revenue", "orders"})
    plain = _one_chart_spec(
        {
            "table": "dm.sales_daily",
            "dimensions": ["date"],
            "measures": [{"column": "revenue", "agg": "sum"}],
        }
    )
    assert "no ratio measure" in _check_core(case, plain)
    ratio = _one_chart_spec(
        {
            "table": "dm.sales_daily",
            "dimensions": ["date"],
            "measures": [
                {
                    "column": "revenue",
                    "agg": "sum",
                    "label": "Средний чек",
                    "denominator": {"column": "orders", "agg": "sum"},
                }
            ],
        }
    )
    assert _check_core(case, ratio) == ""
    # the denominator's column counts as present («средний чек» carries orders)
    assert {"revenue", "orders"} <= _spec_columns(ratio)


def test_core_check_requires_transform_grain_and_lag() -> None:
    from auto_bi.eval.runner import _check_core
    from auto_bi.ir.spec import MeasureTransform, TimeGrain

    case = _core_case(
        expect_transforms={MeasureTransform.YOY_PCT},
        expect_time_grain={TimeGrain.MONTH},
    )
    from auto_bi.ir.spec import DashboardSpec

    plain_monthly = DashboardSpec.model_validate(MONTHLY_SPEC)
    assert "expected transforms missing" in _check_core(case, plain_monthly)
    yoy = _one_chart_spec(
        {
            "table": "dm.sales_daily",
            "dimensions": ["date"],
            "measures": [{"column": "revenue", "agg": "sum", "transform": "yoy_pct"}],
            "time_grain": "month",
        }
    )
    assert _check_core(case, yoy) == ""
    lag_case = _core_case(expect_lag=3)
    assert "lag_periods=3" in _check_core(lag_case, yoy)
    lagged = _one_chart_spec(
        {
            "table": "dm.sales_daily",
            "dimensions": ["date"],
            "measures": [
                {"column": "revenue", "agg": "sum", "transform": "pop_pct", "lag_periods": 3}
            ],
            "time_grain": "month",
        }
    )
    assert _check_core(lag_case, lagged) == ""


def test_core_check_requires_histogram_bins() -> None:
    from auto_bi.eval.runner import _check_core

    case = _core_case(expect_bins=True)
    bar = _one_chart_spec(
        {
            "table": "dm.products",
            "dimensions": ["category"],
            "measures": [{"column": "price", "agg": "avg"}],
        },
        viz="bar",
    )
    assert "bins" in _check_core(case, bar)
    hist = _one_chart_spec(
        {
            "table": "dm.products",
            "dimensions": ["price"],
            "measures": [{"column": "price", "agg": "count"}],
            "bins": 10,
        },
        viz="histogram",
    )
    assert _check_core(case, hist) == ""


def test_golden_edit_checks_added_transform(demo_model) -> None:
    case = next(c for c in GOLDEN_CASES if c.id == "it4_add_yoy")
    with_yoy = {
        **MONTHLY_SPEC,
        "charts": MONTHLY_SPEC["charts"]
        + [
            {
                "id": "c2",
                "title": "Выручка г/г",
                "viz": "line",
                "query": {
                    "table": "dm.sales_daily",
                    "dimensions": ["date"],
                    "measures": [{"column": "revenue", "agg": "sum", "transform": "yoy_pct"}],
                    "time_grain": "month",
                },
            }
        ],
    }
    llm = ScriptedLLM([CLEAR_REPORT, MONTHLY_SPEC, with_yoy])
    report = run_golden_suite(demo_model, llm, cases=[case])
    (result,) = report.results
    assert result.passed, result.detail

    # an edit that comes back without the transform -> fail with the reason
    llm = ScriptedLLM([CLEAR_REPORT, MONTHLY_SPEC, {**MONTHLY_SPEC, "title": "Другое"}])
    report = run_golden_suite(demo_model, llm, cases=[case])
    (result,) = report.results
    assert not result.passed
    assert "did not add expected transforms" in result.detail


def test_eval_survives_broken_case(demo_model) -> None:
    class ExplodingLLM:
        def complete(self, *a, **kw):
            raise RuntimeError("boom")

    case = next(c for c in GOLDEN_CASES if c.id == "g1_revenue_by_day")
    report = run_golden_suite(demo_model, ExplodingLLM(), cases=[case])
    (result,) = report.results
    assert not result.passed
    assert "boom" in result.detail
