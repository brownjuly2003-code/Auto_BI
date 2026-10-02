"""Freshness success evidence covers only the questions whose ages were compared."""

from pathlib import Path

import pytest

from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.semantic.model import Column, ColumnRole
from auto_bi.verification.environment import EnvironmentFacts, EnvironmentQuestions
from auto_bi.verification.release_gate import CheckStatus
from tests.test_decision_verify import (
    Case,
    _check_payload,
    _write_yaml,
    environment,
    metric_contract,
    product_contract,
    semantic_model,
)


def two_clock_case(tmp_path: Path) -> Case:
    case = Case(tmp_path)
    model = semantic_model()
    model.tables[0].columns.append(Column(name="shipped_at", type="Date", role=ColumnRole.TIME))
    model.dump(case.base / "model.yaml")
    case.with_candidate(model=model)
    contract = metric_contract()
    contract["metadata"]["id"] = "metric://sales/shipped_margin_pct"
    contract["time"]["event_time_column"] = "shipped_at"
    for root in (case.contracts, case.base / "metrics", case.candidate / "metrics"):
        _write_yaml(root / "shipped.yaml", contract)
    product = product_contract()
    product["scope"]["metrics"].append("metric://sales/shipped_margin_pct@3")
    _write_yaml(case.contracts / "weekly_margin.yaml", product)
    return case


@pytest.mark.parametrize(
    "coverage",
    ["complete", "unreadable", "absent", "missing", "unattributed", "duplicate", "deny_all"],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_public_freshness_detail_counts_compared_clock_questions(
    tmp_path: Path, coverage: str, reverse: bool
) -> None:
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
        elif coverage == "deny_all":
            shipped.row_security_enabled = True
            shipped.row_policies = []
            shipped.rows_seen = 0
        observed.tables.append(template.model_copy(update={"table": "dm.unasked"}))
        if reverse:
            observed.tables.reverse()
        return observed

    report = two_clock_case(tmp_path).run(probe=probe)
    check = next(item for item in report.checks if item.check_id == "dwh.freshness")
    assert check.status is CheckStatus.PASS
    assert check.summary_code == "freshness.fresh"
    assert _check_payload(tmp_path, report, "dwh.freshness")["findings"] == []
    count = 2 if coverage == "complete" else 1
    assert f"{count} of 2 in-scope mart/event-time questions compared" in check.detail
    assert f"coverage is {'complete' if count == 2 else 'incomplete'}" in check.detail
    assert "compared ages are inside" in check.detail
    assert "every in-scope mart" not in check.detail


@pytest.mark.parametrize("readable", [False, True])
def test_absent_mart_has_no_freshness_comparison(tmp_path: Path, readable: bool) -> None:
    observed = environment(exists=False)
    observed.tables[0].readable = readable
    report = Case(tmp_path).run(probe=lambda scope: observed)
    check = next(item for item in report.checks if item.check_id == "dwh.freshness")
    assert check.status is CheckStatus.PASS
    assert check.summary_code == "freshness.not_run"
    assert _check_payload(tmp_path, report, "dwh.freshness")["findings"] == []
    assert "every in-scope mart" not in check.detail
    assert "nothing was aged" in check.detail


@pytest.mark.parametrize(
    "unusable", ["absent", "unreadable", "missing", "unattributed", "duplicate", "deny_all"]
)
@pytest.mark.parametrize("reverse", [False, True])
def test_public_freshness_without_eligible_questions_is_not_run(
    tmp_path: Path, unusable: str, reverse: bool
) -> None:
    def probe(scope: EnvironmentQuestions) -> EnvironmentFacts:
        observed = environment()
        template = observed.tables[0]
        observed.tables = []
        for question in scope.tables:
            table = template.model_copy(deep=True)
            table.event_time_column = question.event_time_column
            if unusable == "absent":
                table.exists = False
            elif unusable == "unreadable":
                table.readable = False
            elif unusable == "missing":
                continue
            elif unusable == "unattributed":
                table.event_time_column = None
            elif unusable == "duplicate":
                observed.tables.append(table.model_copy(deep=True))
            else:
                table.row_security_enabled = True
                table.row_policies = []
                table.rows_seen = 0
            observed.tables.append(table)
        observed.tables.append(template.model_copy(update={"table": "dm.unasked"}))
        if reverse:
            observed.tables.reverse()
        return observed

    report = two_clock_case(tmp_path).run(probe=probe)
    check = next(item for item in report.checks if item.check_id == "dwh.freshness")
    assert check.status is CheckStatus.PASS
    assert check.summary_code == "freshness.not_run"
    assert "nothing was aged" in check.detail
    assert _check_payload(tmp_path, report, "dwh.freshness")["findings"] == []
    summary = (
        FilesystemArtifactStore(tmp_path / "evidence")
        .get(report.run_id, "reports/summary.md")
        .decode("utf-8")
    )
    undone = summary.split("## What was not done")[1].split("\n## ")[0]
    assert "**dwh.freshness**" in undone
    assert "| `dwh.freshness` | pass | `freshness.not_run` |" in summary


@pytest.mark.parametrize(
    "overrides,window,code",
    [
        ({"max_event_time": "2020-01-01T00:00:00Z"}, "P1D", "freshness.stale"),
        ({"max_event_time": None}, "P1D", "freshness.unknown"),
        ({"rows_seen": 0}, "P1D", "freshness.unknown"),
        ({"max_event_time": "invalid"}, "P1D", "freshness.unreadable_event_time"),
        ({}, "P1M", "freshness.window_not_comparable"),
    ],
)
def test_freshness_findings_keep_their_details(
    tmp_path: Path, overrides: dict, window: str, code: str
) -> None:
    case = Case(tmp_path)
    product = product_contract()
    product["verification"]["freshness_max_age"] = window
    _write_yaml(case.contracts / "weekly_margin.yaml", product)
    observed = environment()
    observed.tables[0] = observed.tables[0].model_copy(update=overrides)
    report = case.run(probe=lambda scope: observed)
    check = next(item for item in report.checks if item.check_id == "dwh.freshness")
    assert check.status is CheckStatus.REVIEW
    assert check.summary_code == code
    findings = _check_payload(tmp_path, report, "dwh.freshness")["findings"]
    assert [item["code"] for item in findings] == [code]
    assert check.detail == findings[0]["detail"]
