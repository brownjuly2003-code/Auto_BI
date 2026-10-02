"""Clean cost evidence covers only the declared bounds the gate actually checked."""

from pathlib import Path

import pytest
import yaml

from auto_bi.verification.environment import (
    CostBound,
    EnvironmentFacts,
    EnvironmentQuestions,
    ExecutionFacts,
    PlanFacts,
)
from auto_bi.verification.release_gate import CheckStatus
from tests.test_decision_verify import (
    Case,
    _check_payload,
    _write_yaml,
    budgeted_contract,
    environment,
    estimate,
    executed,
)
from tests.test_freshness_coverage import two_clock_case


@pytest.mark.parametrize(
    "coverage",
    [
        "complete",
        "unreadable",
        "absent",
        "missing",
        "unattributed",
        "duplicate",
        "deny_all",
        "bypass",
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_public_cost_detail_counts_only_eligible_metric_bounds(
    tmp_path: Path, coverage: str, reverse: bool
) -> None:
    case = two_clock_case(tmp_path)
    for path in (case.candidate / "metrics").glob("*.yaml"):
        contract = yaml.safe_load(path.read_text(encoding="utf-8"))
        contract["budgets"] = {
            "max_scanned_rows": 1000,
            "max_scanned_bytes": 2000,
            "timeout_ms": 3000,
        }
        _write_yaml(path, contract)

    def probe(scope: EnvironmentQuestions) -> EnvironmentFacts:
        observed = environment()
        template = observed.tables[0]
        observed.tables = [
            template.model_copy(update={"event_time_column": item.event_time_column}, deep=True)
            for item in scope.tables
        ]
        shipped = next(item for item in observed.tables if item.event_time_column == "shipped_at")
        if coverage == "unreadable":
            shipped.readable = False
        elif coverage == "absent":
            shipped.exists = False
        elif coverage == "missing":
            observed.tables.remove(shipped)
        elif coverage == "unattributed":
            shipped.event_time_column = None
        elif coverage == "duplicate":
            observed.tables.append(shipped.model_copy(deep=True))
        elif coverage in {"deny_all", "bypass"}:
            shipped.row_security_enabled = True
            if coverage == "bypass":
                observed.role.bypasses_row_security = True
        observed.plans = [
            PlanFacts(metric_ref=item.metric_ref, scanned_rows=0, evidence="scan estimate")
            for item in scope.plans
        ]
        observed.executions = [
            ExecutionFacts(
                metric_ref=item.metric_ref,
                scanned_bytes=0,
                checked_bounds=[CostBound.TIMEOUT],
                evidence="exact query completed under timeout",
            )
            for item in scope.plans
        ]
        observed.plans.append(
            PlanFacts(metric_ref="metric://unasked@1", scanned_rows=0, evidence="extra")
        )
        observed.executions.append(
            ExecutionFacts(metric_ref="metric://unasked@1", elapsed_ms=0, evidence="extra")
        )
        if reverse:
            observed.tables.reverse()
            observed.plans.reverse()
            observed.executions.reverse()
        return observed

    report = case.run(probe=probe)
    check = next(item for item in report.checks if item.check_id == "dwh.cost")
    assert check.status is CheckStatus.PASS
    assert check.summary_code == "cost.within_budget"
    assert _check_payload(tmp_path, report, "dwh.cost")["findings"] == []
    count = 2 if coverage in {"complete", "bypass"} else 1
    assert f"{count} of 2 declared scanned-row ceilings checked from plans" in check.detail
    assert f"{count} of 2 declared scanned-byte ceilings" in check.detail
    assert (
        f"{count} of 2 declared timeout ceilings checked from exact-query runtime evidence"
        in check.detail
    )
    assert f"coverage is {'complete' if count == 2 else 'incomplete'}" in check.detail
    assert "checked bounds are within their declared ceilings" in check.detail
    assert "every governed query" not in check.detail


@pytest.mark.parametrize("observed", [False, True])
def test_unreadable_mart_has_no_cost_comparisons(tmp_path: Path, observed: bool) -> None:
    case = Case(tmp_path).with_candidate(
        contract=budgeted_contract(max_scanned_rows=1000, max_scanned_bytes=2000, timeout_ms=3000)
    )
    facts = environment(
        exists=False,
        plans=[estimate(0)] if observed else [],
        executions=[executed(scanned_bytes=0, elapsed_ms=0)] if observed else [],
    )
    report = case.run(probe=lambda scope: facts)
    check = next(item for item in report.checks if item.check_id == "dwh.cost")
    assert check.status is CheckStatus.PASS
    assert check.summary_code == ("cost.within_budget" if observed else "cost.not_run")
    assert "0 of 1 declared scanned-row ceilings checked from plans" in check.detail
    assert "0 of 1 declared scanned-byte ceilings" in check.detail
    assert "0 of 1 declared timeout ceilings" in check.detail
    assert "coverage is incomplete" in check.detail
    assert "no cost comparison was made" in check.detail
    assert "checked bounds are within" not in check.detail


@pytest.mark.parametrize("bound", ["max_scanned_rows", "max_scanned_bytes", "timeout_ms"])
@pytest.mark.parametrize("source", ["counter", "enforced"])
def test_cost_coverage_counts_only_declared_bounds(tmp_path: Path, bound: str, source: str) -> None:
    case = Case(tmp_path).with_candidate(contract=budgeted_contract(**{bound: 1000}))
    runtime = (
        executed(scanned_bytes=0, elapsed_ms=0)
        if source == "counter"
        else executed(checked_bounds=[CostBound.SCANNED_BYTES, CostBound.TIMEOUT])
    )
    report = case.run(probe=lambda scope: environment(plans=[estimate(0)], executions=[runtime]))
    check = next(item for item in report.checks if item.check_id == "dwh.cost")
    for name, label in (
        ("max_scanned_rows", "scanned-row"),
        ("max_scanned_bytes", "scanned-byte"),
        ("timeout_ms", "timeout"),
    ):
        count = int(name == bound)
        assert f"{count} of {count} declared {label} ceilings" in check.detail
    assert "coverage is complete" in check.detail


@pytest.mark.parametrize(
    "scenario,code",
    [
        ("no_estimate", "cost.not_estimated"),
        ("no_runtime", "cost.not_measured"),
        ("over_rows", "cost.over_budget"),
        ("over_bytes", "cost.over_budget"),
        ("timeout", "cost.over_budget"),
    ],
)
def test_cost_findings_keep_their_details(tmp_path: Path, scenario: str, code: str) -> None:
    case = Case(tmp_path).with_candidate(
        contract=budgeted_contract(max_scanned_rows=1000, max_scanned_bytes=2000, timeout_ms=3000)
    )
    plan = estimate(None if scenario == "no_estimate" else 1001 if scenario == "over_rows" else 0)
    runtime = executed(scanned_bytes=2001 if scenario == "over_bytes" else 0, elapsed_ms=0)
    if scenario == "timeout":
        runtime.exceeded_bound = CostBound.TIMEOUT
    report = case.run(
        probe=lambda scope: environment(
            plans=[plan], executions=[] if scenario == "no_runtime" else [runtime]
        )
    )
    check = next(item for item in report.checks if item.check_id == "dwh.cost")
    findings = _check_payload(tmp_path, report, "dwh.cost")["findings"]
    assert check.status is CheckStatus.REVIEW
    assert check.summary_code == code
    assert check.detail == "; ".join(item["detail"] for item in findings)


def test_no_budget_keeps_its_existing_answer(tmp_path: Path) -> None:
    report = Case(tmp_path).run(probe=lambda scope: environment())
    check = next(item for item in report.checks if item.check_id == "dwh.cost")
    assert check.summary_code == "cost.no_budget_declared"
    assert check.detail == "no in-scope metric declares a cost ceiling, so no query was priced"


def test_duplicate_cost_facts_keep_first_match_semantics(tmp_path: Path) -> None:
    case = Case(tmp_path).with_candidate(
        contract=budgeted_contract(max_scanned_rows=1000, max_scanned_bytes=2000, timeout_ms=3000)
    )
    report = case.run(
        probe=lambda scope: environment(
            plans=[estimate(0), estimate(9999)],
            executions=[
                executed(scanned_bytes=0, elapsed_ms=0),
                executed(scanned_bytes=9999, elapsed_ms=9999),
            ],
        )
    )
    check = next(item for item in report.checks if item.check_id == "dwh.cost")
    assert check.status is CheckStatus.PASS
    for label in ("scanned-row", "scanned-byte", "timeout"):
        assert f"1 of 1 declared {label} ceilings" in check.detail
    assert "coverage is complete" in check.detail
