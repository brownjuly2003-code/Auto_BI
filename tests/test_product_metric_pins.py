"""Product metric pins resolved through the real offline release gate."""

import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from auto_bi.decision.validate import load_contracts, validate_paths
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.semantic.gateway import NativeModelSource
from auto_bi.verification.model import Verdict
from auto_bi.verification.release_gate import CheckStatus, GateInputs, GateReport, run_gate
from tests.test_decision_verify import (
    Case,
    _check_payload,
    _write_yaml,
    detail_of,
    metric_contract,
    product_contract,
    revenue_metric,
    semantic_model,
    shown,
    status_of,
    summary_of,
)

PIN = "metric://sales/gross_margin_pct@3"
PRODUCT = "decision://sales/weekly_margin@1"
CLEAN_DETAIL = (
    "1 metric contract(s) resolved, 0 issue(s); 1 of 1 metric pin(s) match the candidate snapshot"
)
MISMATCH_DETAIL = (
    "1 metric contract(s) resolved, 1 issue(s); "
    "0 of 1 metric pin(s) match the candidate snapshot: "
    "it holds version 4 of metric://sales/gross_margin_pct, pinned at version 3"
)
MISMATCH_ISSUE = {
    "code": "metric_ref.candidate_version_mismatch",
    "severity": "block",
    "subject": PIN,
    "source": PRODUCT,
    "message": (
        "the candidate snapshot holds version 4 of metric://sales/gross_margin_pct; "
        "the product pins version 3"
    ),
}
ABSENT_ISSUE = {
    "code": "metric_ref.candidate_absent",
    "severity": "block",
    "subject": PIN,
    "source": PRODUCT,
    "message": (
        "the candidate snapshot holds no metric metric://sales/gross_margin_pct; "
        "the product pins version 3"
    ),
}


def _version(version: int) -> dict[str, Any]:
    contract = metric_contract()
    contract["metadata"]["version"] = version
    return contract


def _assert_check(
    case: Case,
    report: GateReport,
    *,
    status: CheckStatus = CheckStatus.PASS,
    summary: str = "contracts.clean",
    issues: list[dict[str, Any]] | None = None,
    pins: list[dict[str, Any]] | None = None,
    verdict: str = "pass",
    detail: str = CLEAN_DETAIL,
) -> dict[str, Any]:
    assert status_of(report, "contracts.validate") is status
    assert summary_of(report, "contracts.validate") == summary
    assert detail_of(report, "contracts.validate") == detail
    payload = _check_payload(case.root, report, "contracts.validate")
    assert payload["issues"] == (issues or [])
    assert payload["candidate_pins"] == (
        pins if pins is not None else [{"ref": PIN, "candidate_versions": [3], "outcome": "match"}]
    )
    assert payload["verdict"] == verdict
    return payload


def _assert_mismatch(case: Case, report: GateReport) -> None:
    _assert_check(
        case,
        report,
        status=CheckStatus.BLOCK,
        summary="contracts.issues",
        issues=[MISMATCH_ISSUE],
        pins=[{"ref": PIN, "candidate_versions": [4], "outcome": "version_mismatch"}],
        verdict="block",
        detail=MISMATCH_DETAIL,
    )


def test_a_candidate_at_another_version_blocks_the_release(tmp_path: Path) -> None:
    case = Case(tmp_path).with_candidate(contract=_version(4))
    _write_yaml(case.base / "metrics/gross_margin_pct.yaml", _version(4))
    report = case.run()
    _assert_mismatch(case, report)
    assert report.verdict is Verdict.BLOCK
    summary = (tmp_path / "evidence" / report.run_id / "reports/summary.md").read_text(
        encoding="utf-8"
    )
    stops = summary.split("What stops the release", 1)[1].split("\n## ", 1)[0]
    assert any(
        "contracts.validate" in line and MISMATCH_DETAIL in line
        for line in shown(stops).splitlines()
    )


def test_a_candidate_without_the_metric_blocks_the_release(tmp_path: Path) -> None:
    case = Case(tmp_path).with_candidate()
    shutil.rmtree(case.base / "metrics")
    shutil.rmtree(case.candidate / "metrics")
    report = case.run()
    _assert_check(
        case,
        report,
        status=CheckStatus.BLOCK,
        summary="contracts.issues",
        issues=[ABSENT_ISSUE],
        pins=[{"ref": PIN, "candidate_versions": [], "outcome": "absent"}],
        verdict="block",
        detail=(
            "1 metric contract(s) resolved, 1 issue(s); "
            "0 of 1 metric pin(s) match the candidate snapshot: "
            "it holds no metric://sales/gross_margin_pct, pinned at version 3"
        ),
    )
    assert report.verdict is Verdict.BLOCK


def test_a_matching_pin_keeps_the_check_clean(tmp_path: Path) -> None:
    case = Case(tmp_path)
    report = case.run()
    _assert_check(case, report)
    assert report.verdict is Verdict.PASS_WITH_ADVISORY


def test_an_older_base_is_not_compared(tmp_path: Path) -> None:
    case = Case(tmp_path)
    _write_yaml(case.base / "metrics/gross_margin_pct.yaml", _version(2))
    report = case.run()
    _assert_check(case, report)
    diff = _check_payload(tmp_path, report, "semantic.diff")
    assert "metric.version_changed" in {change["code"] for change in diff["changes"]}


@pytest.mark.parametrize("base_kind", ["several", "none", "invalid"])
def test_a_base_with_several_contracts_or_none_is_not_compared(
    tmp_path: Path, base_kind: str
) -> None:
    case = Case(tmp_path)
    if base_kind == "several":
        _write_yaml(case.base / "metrics/second.yaml", _version(4))
    elif base_kind == "none":
        shutil.rmtree(case.base / "metrics")
    else:
        _invalid(case.base / "metrics/gross_margin_pct.yaml")
    _assert_check(case, case.run())


def test_a_product_re_pointed_together_with_the_candidate_passes(tmp_path: Path) -> None:
    case = Case(tmp_path).with_candidate(contract=_version(4))
    product = product_contract()
    product["scope"]["metrics"] = ["metric://sales/gross_margin_pct@4"]
    _write_yaml(case.contracts / "weekly_margin.yaml", product)
    _write_yaml(case.contracts / "gross_margin_pct.yaml", _version(4))
    _assert_check(
        case,
        case.run(),
        pins=[
            {
                "ref": "metric://sales/gross_margin_pct@4",
                "candidate_versions": [4],
                "outcome": "match",
            }
        ],
    )


@pytest.mark.parametrize("filename", ["aaa.yaml", "zzz.yaml"])
def test_two_contracts_of_the_pinned_metric_are_ambiguous(tmp_path: Path, filename: str) -> None:
    case = Case(tmp_path).with_candidate()
    _write_yaml(case.candidate / "metrics" / filename, _version(4))
    _assert_check(
        case,
        case.run(),
        status=CheckStatus.BLOCK,
        summary="contracts.issues",
        issues=[
            {
                "code": "metric_ref.candidate_ambiguous",
                "severity": "block",
                "subject": PIN,
                "source": PRODUCT,
                "message": (
                    "the candidate snapshot holds 2 contracts of metric://sales/gross_margin_pct "
                    "(versions 3, 4); the pin to version 3 does not resolve to one of them"
                ),
            }
        ],
        pins=[{"ref": PIN, "candidate_versions": [3, 4], "outcome": "ambiguous"}],
        verdict="block",
        detail=(
            "1 metric contract(s) resolved, 1 issue(s); "
            "0 of 1 metric pin(s) match the candidate snapshot: "
            "it holds 2 contracts of metric://sales/gross_margin_pct "
            "(versions 3, 4), pinned at version 3"
        ),
    )


def test_two_contracts_at_the_pinned_version_are_ambiguous_too(tmp_path: Path) -> None:
    case = Case(tmp_path).with_candidate()
    _write_yaml(case.candidate / "metrics/second.yaml", metric_contract())
    _assert_check(
        case,
        case.run(),
        status=CheckStatus.BLOCK,
        summary="contracts.issues",
        issues=[
            {
                "code": "metric_ref.candidate_ambiguous",
                "severity": "block",
                "subject": PIN,
                "source": PRODUCT,
                "message": (
                    "the candidate snapshot holds 2 contracts of metric://sales/gross_margin_pct "
                    "(versions 3, 3); the pin to version 3 does not resolve to one of them"
                ),
            }
        ],
        pins=[{"ref": PIN, "candidate_versions": [3, 3], "outcome": "ambiguous"}],
        verdict="block",
        detail=(
            "1 metric contract(s) resolved, 1 issue(s); "
            "0 of 1 metric pin(s) match the candidate snapshot: "
            "it holds 2 contracts of metric://sales/gross_margin_pct "
            "(versions 3, 3), pinned at version 3"
        ),
    )


@pytest.mark.parametrize("duplicate", [False, True])
def test_a_metric_the_product_does_not_pin_is_not_compared(tmp_path: Path, duplicate: bool) -> None:
    case = Case(tmp_path).with_candidate()
    revenue = revenue_metric()
    revenue["metadata"]["version"] = 9
    _write_yaml(case.candidate / "metrics/revenue.yaml", revenue)
    if duplicate:
        _write_yaml(case.candidate / "metrics/revenue_second.yaml", revenue)
    _assert_check(case, case.run())


def test_several_pins_are_graded_one_by_one_in_declared_order(tmp_path: Path) -> None:
    case = Case(tmp_path).with_candidate()
    product = product_contract()
    product["scope"]["metrics"] = [PIN, "metric://sales/revenue@2", "metric://sales/units@1"]
    _write_yaml(case.contracts / "weekly_margin.yaml", product)
    for name, pinned, candidate_version in [("revenue", 2, 4), ("units", 1, None)]:
        metric = revenue_metric()
        metric["metadata"].update(id=f"metric://sales/{name}", version=pinned)
        _write_yaml(case.contracts / f"{name}.yaml", metric)
        if candidate_version is not None:
            metric["metadata"]["version"] = candidate_version
            for root in [case.base, case.candidate]:
                _write_yaml(root / "metrics" / f"{name}.yaml", metric)
    _assert_check(
        case,
        case.run(),
        status=CheckStatus.BLOCK,
        summary="contracts.issues",
        issues=[
            {
                "code": "metric_ref.candidate_version_mismatch",
                "severity": "block",
                "subject": "metric://sales/revenue@2",
                "source": PRODUCT,
                "message": (
                    "the candidate snapshot holds version 4 of metric://sales/revenue; "
                    "the product pins version 2"
                ),
            },
            {
                "code": "metric_ref.candidate_absent",
                "severity": "block",
                "subject": "metric://sales/units@1",
                "source": PRODUCT,
                "message": (
                    "the candidate snapshot holds no metric metric://sales/units; "
                    "the product pins version 1"
                ),
            },
        ],
        pins=[
            {"ref": PIN, "candidate_versions": [3], "outcome": "match"},
            {
                "ref": "metric://sales/revenue@2",
                "candidate_versions": [4],
                "outcome": "version_mismatch",
            },
            {"ref": "metric://sales/units@1", "candidate_versions": [], "outcome": "absent"},
        ],
        verdict="block",
        detail=(
            "3 metric contract(s) resolved, 2 issue(s); "
            "1 of 3 metric pin(s) match the candidate snapshot: "
            "it holds version 4 of metric://sales/revenue, pinned at version 2; "
            "it holds no metric://sales/units, pinned at version 1"
        ),
    )


@pytest.mark.parametrize("version", [3, 4])
def test_the_validation_reports_own_issues_are_kept(tmp_path: Path, version: int) -> None:
    draft = metric_contract()
    draft["metadata"]["status"] = "draft"
    candidate = deepcopy(draft)
    candidate["metadata"]["version"] = version
    case = Case(tmp_path).with_candidate(contract=candidate)
    _write_yaml(case.contracts / "gross_margin_pct.yaml", draft)
    issue = {
        "code": "metric.draft",
        "severity": "review",
        "subject": PIN,
        "source": PRODUCT,
        "message": "metric contract is still a draft: its definition is not agreed yet",
    }
    _assert_check(
        case,
        case.run(),
        status=CheckStatus.REVIEW if version == 3 else CheckStatus.BLOCK,
        summary="contracts.issues",
        issues=[issue] if version == 3 else [issue, MISMATCH_ISSUE],
        pins=[
            {
                "ref": PIN,
                "candidate_versions": [version],
                "outcome": "match" if version == 3 else "version_mismatch",
            }
        ],
        verdict="needs_review" if version == 3 else "block",
        detail=(
            "1 metric contract(s) resolved, 1 issue(s); "
            "1 of 1 metric pin(s) match the candidate snapshot"
            if version == 3
            else "1 metric contract(s) resolved, 2 issue(s); "
            "0 of 1 metric pin(s) match the candidate snapshot: "
            "it holds version 4 of metric://sales/gross_margin_pct, pinned at version 3"
        ),
    )


def _invalid(path: Path) -> None:
    path.write_text(
        "kind: MetricContract\nmetadata: {id: metric://sales/gross_margin_pct}\n", encoding="utf-8"
    )


def test_a_candidate_contract_that_did_not_load_is_not_reported_as_an_absent_metric(
    tmp_path: Path,
) -> None:
    case = Case(tmp_path).with_candidate()
    _invalid(case.candidate / "metrics/gross_margin_pct.yaml")
    report = case.run()
    _assert_check(
        case,
        report,
        pins=[{"ref": PIN, "candidate_versions": [], "outcome": "not_compared"}],
        detail=(
            "1 metric contract(s) resolved, 0 issue(s); "
            "0 of 1 metric pin(s) match the candidate snapshot: "
            "metric://sales/gross_margin_pct@3 was not compared, "
            "because a metric contract of the candidate did not load"
        ),
    )
    assert status_of(report, "semantic.gaps") is CheckStatus.BLOCK
    assert summary_of(report, "semantic.diff") == "check.not_run"


def test_a_snapshot_with_an_unloaded_contract_still_names_a_version_it_does_hold(
    tmp_path: Path,
) -> None:
    case = Case(tmp_path).with_candidate(contract=_version(4))
    _invalid(case.candidate / "metrics/invalid.yaml")
    report = case.run()
    _assert_mismatch(case, report)
    assert status_of(report, "semantic.gaps") is CheckStatus.BLOCK


def test_the_validation_report_is_not_modified_and_decision_validate_is_unchanged(
    tmp_path: Path,
) -> None:
    case = Case(tmp_path).with_candidate(contract=_version(4))
    _write_yaml(case.base / "metrics/gross_margin_pct.yaml", _version(4))
    validated = validate_paths([case.contracts])
    assert validated.issues == []
    assert validated.verdict is Verdict.PASS
    loaded = load_contracts([case.contracts])
    before = loaded.report.model_dump()
    inputs = GateInputs(
        product=next(iter(loaded.products.values())),
        validation=loaded.report,
        base=NativeModelSource(case.base / "model.yaml", case.base / "metrics").snapshot(),
        candidate=NativeModelSource(
            case.candidate / "model.yaml", case.candidate / "metrics"
        ).snapshot(),
        candidate_model=semantic_model(),
    )
    report = run_gate(
        inputs,
        store=FilesystemArtifactStore(tmp_path / "evidence"),
        created_at="2026-08-25T09:00:00Z",
    )
    _assert_mismatch(case, report)
    assert loaded.report.model_dump() == before == validated.model_dump()
    payload = _check_payload(tmp_path, report, "contracts.validate")
    for key, value in loaded.report.model_dump(mode="json").items():
        if key not in {"issues", "verdict"}:
            assert payload[key] == value
