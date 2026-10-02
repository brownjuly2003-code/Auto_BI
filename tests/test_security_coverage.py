"""Security success evidence names only the reach and policy checks actually answered."""

from pathlib import Path

import pytest

from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.verification.environment import (
    EnvironmentFacts,
    EnvironmentQuestions,
    RowPolicyFacts,
)
from auto_bi.verification.environment_probe import collect
from auto_bi.verification.model import Verdict
from auto_bi.verification.release_gate import CheckStatus
from tests.test_decision_verify import Case, _check_payload, environment
from tests.test_environment_probe import postgres_engine
from tests.test_freshness_coverage import two_clock_case


@pytest.mark.parametrize(
    "coverage,columns,policies",
    [
        ("complete", 9, 2),
        ("other_role_policies", 9, 2),
        ("unreadable", 4, 1),
        ("absent", 4, 1),
        ("missing", 4, 1),
        ("unattributed", 4, 1),
        ("duplicate", 4, 1),
        ("partial_columns", 8, 2),
        ("empty_columns", 4, 2),
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_public_security_detail_counts_observed_reach_and_policies(
    tmp_path: Path, coverage: str, columns: int, policies: int, reverse: bool
) -> None:
    def probe(scope: EnvironmentQuestions) -> EnvironmentFacts:
        observed = environment()
        template = observed.tables[0]
        observed.tables = [
            template.model_copy(
                update={
                    "event_time_column": item.event_time_column,
                    "column_reads": dict.fromkeys(item.columns, True),
                },
                deep=True,
            )
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
        elif coverage == "partial_columns":
            del shipped.column_reads["gross_margin"]
        elif coverage == "empty_columns":
            shipped.column_reads = {}
        elif coverage == "other_role_policies":
            shipped.row_policies = [
                RowPolicyFacts(name=name, applies_to_session=False) for name in ("p1", "p2")
            ]
        for table in observed.tables:
            table.column_reads["unasked"] = True
        observed.tables.append(template.model_copy(update={"table": "dm.unasked"}))
        if reverse:
            observed.tables.reverse()
        return observed

    report = two_clock_case(tmp_path).run(probe=probe)
    check = next(item for item in report.checks if item.check_id == "dwh.security")
    assert check.status is CheckStatus.PASS
    assert check.summary_code == "security.unfiltered"
    assert _check_payload(tmp_path, report, "dwh.security")["findings"] == []
    assert f"{columns} of 9 requested column reads confirmed" in check.detail
    assert f"{policies} of 2 row-policy questions answered" in check.detail
    complete = coverage in {"complete", "other_role_policies"}
    assert f"coverage is {'complete' if complete else 'incomplete'}" in check.detail
    assert "nothing narrows" not in check.detail
    assert "every column" not in check.detail


def test_postgres_absent_column_is_not_confirmed_readable(tmp_path: Path) -> None:
    def probe(scope: EnvironmentQuestions) -> EnvironmentFacts:
        present = {
            name: kind for name, kind in scope.tables[0].columns.items() if name != "gross_margin"
        }
        engine = postgres_engine(
            **{
                "format_type": [{"name": name, "type": kind} for name, kind in present.items()],
                "has_column_privilege(a.attrelid": [{"name": name, "ok": True} for name in present],
            }
        )
        return collect(engine, scope, engine="greengage", target="offline")

    report = Case(tmp_path).run(probe=probe)
    security = next(item for item in report.checks if item.check_id == "dwh.security")
    physical = next(item for item in report.checks if item.check_id == "dwh.physical")
    assert security.status is CheckStatus.PASS
    assert security.summary_code == "security.unfiltered"
    assert physical.summary_code == "physical.column_absent"
    assert report.verdict is Verdict.BLOCK
    assert "3 of 4 requested column reads confirmed" in security.detail
    assert "1 of 1 row-policy questions answered" in security.detail
    assert "coverage is incomplete" in security.detail


@pytest.mark.parametrize("readable", [False, True])
def test_absent_mart_has_no_security_observations(tmp_path: Path, readable: bool) -> None:
    observed = environment(exists=False)
    observed.tables[0].readable = readable
    report = Case(tmp_path).run(probe=lambda scope: observed)
    check = next(item for item in report.checks if item.check_id == "dwh.security")
    assert check.status is CheckStatus.PASS
    assert check.summary_code == "security.not_run"
    if readable:
        assert "0 of 4 requested column reads confirmed" in check.detail
        assert "0 of 1 row-policy questions answered" in check.detail
        assert "no security observation was made" in check.detail
    else:
        assert "nothing was asked" in check.detail


@pytest.mark.parametrize(
    "unusable", ["unreadable", "absent", "missing", "unattributed", "duplicate"]
)
@pytest.mark.parametrize("reverse", [False, True])
def test_public_security_with_no_eligible_observations_is_not_run(
    tmp_path: Path, unusable: str, reverse: bool
) -> None:
    def probe(scope: EnvironmentQuestions) -> EnvironmentFacts:
        observed = environment()
        template = observed.tables[0]
        observed.tables = []
        for question in scope.tables:
            table = template.model_copy(deep=True)
            table.event_time_column = question.event_time_column
            table.column_reads = dict.fromkeys(question.columns, True)
            if unusable == "unreadable":
                table.readable = False
            elif unusable == "absent":
                table.exists = False
            elif unusable == "missing":
                continue
            elif unusable == "unattributed":
                table.event_time_column = None
            else:
                observed.tables.append(table.model_copy(deep=True))
            observed.tables.append(table)
        observed.tables.append(template.model_copy(update={"table": "dm.unasked"}))
        if reverse:
            observed.tables.reverse()
        return observed

    report = two_clock_case(tmp_path).run(probe=probe)
    check = next(item for item in report.checks if item.check_id == "dwh.security")
    assert check.status is CheckStatus.PASS
    assert check.summary_code == "security.not_run"
    assert _check_payload(tmp_path, report, "dwh.security")["findings"] == []
    summary = (
        FilesystemArtifactStore(tmp_path / "evidence")
        .get(report.run_id, "reports/summary.md")
        .decode("utf-8")
    )
    undone = summary.split("## What was not done")[1].split("\n## ")[0]
    assert "**dwh.security**" in undone
    assert "| `dwh.security` | pass | `security.not_run` |" in summary


@pytest.mark.parametrize("columns", [{}, {"unasked": True}])
def test_empty_policy_catalog_is_a_security_observation(tmp_path: Path, columns: dict) -> None:
    observed = environment()
    observed.tables[0].column_reads = columns
    observed.tables[0].row_policies = []
    report = Case(tmp_path).run(probe=lambda scope: observed)
    check = next(item for item in report.checks if item.check_id == "dwh.security")
    assert check.status is CheckStatus.PASS
    assert check.summary_code == "security.unfiltered"
    assert _check_payload(tmp_path, report, "dwh.security")["findings"] == []
    assert "0 of 4 requested column reads confirmed" in check.detail
    assert "1 of 1 row-policy questions answered" in check.detail
    assert "coverage is incomplete" in check.detail
    summary = (
        FilesystemArtifactStore(tmp_path / "evidence")
        .get(report.run_id, "reports/summary.md")
        .decode("utf-8")
    )
    undone = summary.split("## What was not done")[1].split("\n## ")[0]
    assert "**dwh.security**" not in undone


@pytest.mark.parametrize(
    "scenario,code,status",
    [
        ("unknown_columns", "security.not_observed", CheckStatus.REVIEW),
        ("unknown_policies", "security.not_observed", CheckStatus.REVIEW),
        ("denied_column", "security.column_unreadable", CheckStatus.BLOCK),
        ("filtered", "security.row_filtered", CheckStatus.REVIEW),
        ("deny_all", "security.row_policy_denies_all", CheckStatus.BLOCK),
        ("bypassed", "security.row_security_bypassed", CheckStatus.REVIEW),
    ],
)
def test_security_findings_keep_their_details(
    tmp_path: Path, scenario: str, code: str, status: CheckStatus
) -> None:
    observed = environment()
    table = observed.tables[0]
    if scenario == "unknown_columns":
        table.column_reads = None
    elif scenario == "unknown_policies":
        table.row_policies = None
    elif scenario == "denied_column":
        table.column_reads["gross_margin"] = False
    elif scenario == "deny_all":
        table.row_security_enabled = True
    else:
        table.row_policies = [RowPolicyFacts(name="p", applies_to_session=True)]
        if scenario == "bypassed":
            observed.role.bypasses_row_security = True
    report = Case(tmp_path).run(probe=lambda scope: observed)
    check = next(item for item in report.checks if item.check_id == "dwh.security")
    assert check.status is status
    assert check.summary_code == code
    findings = _check_payload(tmp_path, report, "dwh.security")["findings"]
    assert [item["code"] for item in findings] == [code]
    assert check.detail == findings[0]["detail"]
