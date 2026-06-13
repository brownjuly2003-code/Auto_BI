"""Eval runner (task 1.11): one command, two suites.

- advisor suite: deterministic (rule pack vs seeded anti-patterns + clean cases),
  runs offline in milliseconds;
- golden suite: 15 dialogue cases through the real agent (GROUNDING/CLARIFY/PROPOSE)
  against the live GraceKelly — измеряет, не мокает.

Exit criteria (PLAN Phase 1): clear pass-rate >= 80% with ZERO stray questions;
ambiguous/infeasible must be flagged; advisor catches every seeded anti-pattern
with the right rule and 0 false positives on clean cases.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from auto_bi.advisor.core import Advisor
from auto_bi.agent.machine import AgentPhase, AgentSession
from auto_bi.eval.cases import ADVISOR_CASES, GOLDEN_CASES, AdvisorCase, CaseKind, GoldenCase
from auto_bi.ir.spec import DashboardSpec
from auto_bi.llm.base import LLMClient
from auto_bi.semantic.model import SemanticModel

CLEAR_PASS_THRESHOLD = 0.8


@dataclass
class CaseResult:
    case_id: str
    kind: str
    passed: bool
    detail: str = ""


@dataclass
class EvalReport:
    results: list[CaseResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(r.passed for r in self.results)

    @property
    def total(self) -> int:
        return len(self.results)

    def by_kind(self, kind: str) -> list[CaseResult]:
        return [r for r in self.results if r.kind == kind]


# --- advisor suite (deterministic) ---------------------------------------------------


def run_advisor_suite(model: SemanticModel, cases: list[AdvisorCase] | None = None) -> EvalReport:
    report = EvalReport()
    for case in cases or ADVISOR_CASES:
        case_model = model
        if case.seed is not None:
            case_model = copy.deepcopy(model)
            case.seed(case_model)
        findings = Advisor(case_model).review_chart(case.chart)
        found_rules = {f.rule for f in findings}
        if case.expect_clean:
            passed = not findings
            detail = "" if passed else f"false positives: {sorted(found_rules)}"
        else:
            missing = case.expect_rules - found_rules
            passed = not missing
            detail = (
                "" if passed else f"missing rules: {sorted(missing)} (found {sorted(found_rules)})"
            )
        report.results.append(
            CaseResult(case_id=case.id, kind="advisor", passed=passed, detail=detail)
        )
    return report


def advisor_suite_ok(report: EvalReport) -> bool:
    """Exit criterion: every seeded anti-pattern caught AND zero false positives."""
    return all(r.passed for r in report.results)


# --- golden suite (live LLM) ---------------------------------------------------------


def _check_clear(case: GoldenCase, phase: AgentPhase, spec: DashboardSpec | None) -> str:
    """Empty string = pass; otherwise the failure reason."""
    if phase != AgentPhase.APPROVE:
        return f"asked questions on an unambiguous request (phase={phase})"
    if spec is None:
        return "no spec produced"
    if case.table:
        tables = {c.query.table for c in spec.charts}
        if tables != {case.table}:
            return f"unexpected tables: {sorted(tables)}"
    columns = _spec_columns(spec)
    missing = case.expect_columns - columns
    if missing:
        return f"expected columns missing from the spec: {sorted(missing)}"
    for group in case.expect_columns_any:
        if not group & columns:
            return f"none of the alternative columns present: {sorted(group)}"
    if case.expect_viz and not ({c.viz for c in spec.charts} & case.expect_viz):
        return f"no chart of expected viz {sorted(v.value for v in case.expect_viz)}"
    return ""


def _spec_columns(spec: DashboardSpec) -> set[str]:
    columns: set[str] = set()
    for chart in spec.charts:
        columns.update(chart.query.group_columns())
        columns.update(m.column for m in chart.query.measures)
        columns.update(f.column for f in chart.query.filters)
    return columns


def _check_edit(case: GoldenCase, agent: AgentSession) -> str:
    """Iteration check (task 2.8): word edit after APPROVE -> patched spec.

    Mirrors the 2.4 contract: the edit returns the session to APPROVE with a new
    valid spec; expectations are checked on the PATCHED spec only — the initial
    spec already passed the clear checks."""
    try:
        turn = agent.reply(case.edit)
    except Exception as exc:
        return f"edit failed: {exc}"
    if turn.phase != AgentPhase.APPROVE or turn.spec is None:
        return f"edit did not return to APPROVE (phase={turn.phase})"
    columns = _spec_columns(turn.spec)
    missing = case.edit_expect_columns - columns
    if missing:
        return f"edit did not add expected columns: {sorted(missing)}"
    still_there = case.edit_expect_gone & columns
    if still_there:
        return f"edit did not remove columns: {sorted(still_there)}"
    if case.edit_expect_viz and not ({c.viz for c in turn.spec.charts} & case.edit_expect_viz):
        wanted = sorted(v.value for v in case.edit_expect_viz)
        return f"no chart of expected viz {wanted} after edit"
    return ""


def _check_flagged(case: GoldenCase, phase: AgentPhase, questions: list[str]) -> str:
    if phase != AgentPhase.CLARIFY:
        return f"request was not flagged (phase={phase}, expected a question)"
    needle = case.expect_phrase.lower()
    if needle and not any(needle in q.lower() for q in questions):
        return f"questions do not mention {case.expect_phrase!r}: {questions}"
    return ""


def run_golden_case(
    case: GoldenCase,
    model: SemanticModel,
    llm: LLMClient,
    *,
    advisor: Advisor | None = None,
    session_id: str | None = None,
) -> CaseResult:
    agent = AgentSession(model, llm, advisor, session_id=session_id)
    try:
        turn = agent.start(case.request, seed=case.seed)
    except Exception as exc:  # an eval run must survive a single broken case
        return CaseResult(case_id=case.id, kind=case.kind.value, passed=False, detail=str(exc))

    if case.kind == CaseKind.CLEAR:
        detail = _check_clear(case, turn.phase, turn.spec)
        if not detail and case.edit:
            detail = _check_edit(case, agent)
    else:  # ambiguous and infeasible both require the agent to flag, not hallucinate
        detail = _check_flagged(case, turn.phase, turn.questions)
    return CaseResult(case_id=case.id, kind=case.kind.value, passed=not detail, detail=detail)


def run_golden_suite(
    model: SemanticModel,
    llm: LLMClient,
    *,
    advisor: Advisor | None = None,
    cases: list[GoldenCase] | None = None,
    progress=None,
) -> EvalReport:
    report = EvalReport()
    for case in cases or GOLDEN_CASES:
        result = run_golden_case(case, model, llm, advisor=advisor)
        report.results.append(result)
        if progress is not None:
            progress(result)
    return report


def golden_suite_ok(report: EvalReport) -> bool:
    """Exit criteria: clear >= 80% (each pass implies zero stray questions);
    every ambiguous/infeasible case flagged."""
    clear = report.by_kind("clear")
    flagged = report.by_kind("ambiguous") + report.by_kind("infeasible")
    clear_ok = bool(clear) and sum(r.passed for r in clear) / len(clear) >= CLEAR_PASS_THRESHOLD
    return clear_ok and all(r.passed for r in flagged)
