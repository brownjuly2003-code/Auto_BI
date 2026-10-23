"""Verified asset metric pins resolved through the real offline release gate."""

import shutil
from pathlib import Path
from typing import Any

import pytest

from auto_bi.decision.validate import load_contracts
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.semantic.gateway import NativeModelSource
from auto_bi.verification.model import Verdict
from auto_bi.verification.release_gate import CheckStatus, GateInputs, GateReport, run_gate
from auto_bi.verification.repository import ReferenceQuery, VerifiedAsset, VerifiedAssetRepository
from tests.test_decision_verify import (
    CREATED_AT,
    ENVIRONMENT_REF,
    Case,
    _check_payload,
    _formula_change,
    _write_yaml,
    attestation,
    detail_of,
    metric_contract,
    product_contract,
    revenue_metric,
    semantic_model,
    shown,
    status_of,
    summary_of,
    verified_asset,
)

ASSET = "asset://sales/weekly_margin_by_region@2"
METRIC = "metric://sales/gross_margin_pct"
PIN = f"{METRIC}@3"
CLEAN = "1 verified asset(s) still stand as evidence"
ROWS = [["Москва", "0.412"], ["Казань", "0.377"]]


def _asset(case: Case, refs: list[str], **changes: Any) -> None:
    asset = verified_asset(case.base_digest)
    asset.update(metric_refs=refs, **changes)
    case.assets = case.root / "assets"
    _write_yaml(case.assets / "weekly_margin.yaml", asset)


def _version(version: int, *, changed: bool = False) -> dict[str, Any]:
    contract = _formula_change() if changed else metric_contract()
    contract["metadata"]["version"] = version
    return contract


def _move(case: Case, version: int) -> None:
    product = product_contract()
    product["scope"]["metrics"] = [f"{METRIC}@{version}"]
    _write_yaml(case.contracts / "weekly_margin.yaml", product)
    _write_yaml(case.contracts / "gross_margin_pct.yaml", _version(version))


def _pin(
    ref: str = PIN, versions: list[int] | None = None, outcome: str = "match", asset: str = ASSET
) -> dict[str, Any]:
    return {
        "asset": asset,
        "ref": ref,
        "candidate_versions": [3] if versions is None else versions,
        "outcome": outcome,
    }


def _finding(code: str, detail: str, severity: str = "block", asset: str = ASSET) -> dict[str, Any]:
    return {"asset": asset, "code": code, "severity": severity, "detail": detail}


def _mismatch(pinned: int = 99, version: int = 3, asset: str = ASSET) -> dict[str, Any]:
    return _finding(
        "asset.metric_ref_version_mismatch",
        f"the candidate snapshot holds version {version} of {METRIC}, pinned at version {pinned}",
        asset=asset,
    )


def _assert_assets(
    case: Case,
    report: GateReport,
    pins: list[dict[str, Any]],
    findings: list[dict[str, Any]] | None = None,
    *,
    count: int = 1,
    status: CheckStatus = CheckStatus.PASS,
    summary: str = "assets.valid",
) -> dict[str, Any]:
    findings = findings or []
    assert status_of(report, "assets.invalidated") is status
    assert summary_of(report, "assets.invalidated") == summary
    detail = "; ".join(f"{item['asset']}: {item['detail']}" for item in findings)
    if not findings:
        detail = f"{count} verified asset(s) still stand as evidence"
        if pins:
            detail += f"; {len(pins)} of {len(pins)} metric pin(s) match the candidate snapshot"
    assert detail_of(report, "assets.invalidated") == detail
    payload = _check_payload(case.root, report, "assets.invalidated")
    assert payload["invalidations"] == findings
    assert payload["metric_pins"] == pins
    return payload


def test_a_pin_to_a_version_the_candidate_does_not_hold_blocks_the_release(tmp_path: Path) -> None:
    case = Case(tmp_path)
    _asset(case, [f"{METRIC}@99"])
    case.with_results(ROWS)
    report = case.run(environment_ref=ENVIRONMENT_REF)
    _assert_assets(
        case,
        report,
        [_pin(f"{METRIC}@99", outcome="version_mismatch")],
        [_mismatch()],
        status=CheckStatus.BLOCK,
        summary="asset.metric_ref_version_mismatch",
    )
    assert status_of(report, "results.reconcile") is CheckStatus.PASS
    assert summary_of(report, "results.reconcile") == "result.equivalent"
    assert status_of(report, "results.provenance") is CheckStatus.PASS
    assert report.verdict is Verdict.BLOCK
    text = (case.root / "evidence" / report.run_id / "reports/summary.md").read_text(
        encoding="utf-8"
    )
    stops = text.split("What stops the release", 1)[1].split("\n## ", 1)[0]
    assert any(
        "assets.invalidated" in line and detail_of(report, "assets.invalidated") in line
        for line in shown(stops).splitlines()
    )


def test_a_version_that_moved_past_the_asset_blocks_the_release(tmp_path: Path) -> None:
    case = Case(tmp_path).with_candidate(contract=_version(4))
    _move(case, 4)
    _asset(case, [PIN])
    report = case.run()
    _assert_assets(
        case,
        report,
        [_pin(versions=[4], outcome="version_mismatch")],
        [_mismatch(3, 4)],
        status=CheckStatus.BLOCK,
        summary="asset.metric_ref_version_mismatch",
    )
    assert status_of(report, "contracts.validate") is CheckStatus.PASS
    assert summary_of(report, "contracts.validate") == "contracts.clean"
    assert "metric.version_changed" in {
        c["code"] for c in _check_payload(tmp_path, report, "semantic.diff")["changes"]
    }
    assert report.verdict is Verdict.BLOCK


def test_an_asset_re_pointed_together_with_the_candidate_passes(tmp_path: Path) -> None:
    case = Case(tmp_path).with_candidate(contract=_version(4))
    _move(case, 4)
    _asset(case, [f"{METRIC}@4"])
    _assert_assets(case, case.run(), [_pin(f"{METRIC}@4", [4])])


def test_a_matching_pin_keeps_the_check_clean(tmp_path: Path) -> None:
    case = Case(tmp_path)
    _asset(case, [PIN])
    case.with_results(ROWS)
    report = case.run(environment_ref=ENVIRONMENT_REF)
    _assert_assets(case, report, [_pin()])
    assert detail_of(report, "assets.invalidated") == (
        f"{CLEAN}; 1 of 1 metric pin(s) match the candidate snapshot"
    )
    assert report.verdict is Verdict.PASS_WITH_ADVISORY


def test_a_base_without_the_metric_is_not_compared(tmp_path: Path) -> None:
    case = Case(tmp_path).with_candidate()
    shutil.rmtree(case.base / "metrics")
    _asset(case, [PIN])
    _assert_assets(case, case.run(), [_pin()])


@pytest.mark.parametrize("pinned", [3, 99])
def test_an_asset_verified_against_another_snapshot_keeps_its_review_and_has_its_pins_graded(
    tmp_path: Path, pinned: int
) -> None:
    case = Case(tmp_path)
    _asset(case, [f"{METRIC}@{pinned}"], semantic_digest="sha256:" + "a" * 64)
    findings = [
        _finding(
            "asset.stale_snapshot",
            f"verified against another snapshot (sha256:{'a' * 64}), not {case.base_digest}",
            "review",
        )
    ]
    if pinned == 99:
        findings.append(_mismatch())
    _assert_assets(
        case,
        case.run(),
        [_pin(f"{METRIC}@{pinned}", outcome="match" if pinned == 3 else "version_mismatch")],
        findings,
        status=CheckStatus.REVIEW if pinned == 3 else CheckStatus.BLOCK,
        summary="asset.stale_snapshot" if pinned == 3 else "asset.metric_ref_version_mismatch",
    )


@pytest.mark.parametrize("filename,version", [("aaa.yaml", 4), ("zzz.yaml", 4), ("second.yaml", 3)])
def test_several_contracts_of_the_pinned_metric_are_ambiguous(
    tmp_path: Path, filename: str, version: int
) -> None:
    case = Case(tmp_path).with_candidate()
    _write_yaml(case.candidate / "metrics" / filename, _version(version))
    _asset(case, [PIN])
    _assert_assets(
        case,
        case.run(),
        [_pin(versions=[3, version], outcome="ambiguous")],
        [
            _finding(
                "asset.metric_ref_ambiguous",
                f"the candidate snapshot holds 2 contracts of {METRIC} "
                f"(versions 3, {version}), pinned at version 3",
            )
        ],
        status=CheckStatus.BLOCK,
        summary="asset.metric_ref_ambiguous",
    )


@pytest.mark.parametrize("version", [None, 3, 9])
def test_every_pin_of_a_selected_asset_is_graded_a_metric_outside_the_product_included(
    tmp_path: Path, version: int | None
) -> None:
    case = Case(tmp_path).with_candidate()
    if version is not None:
        metric = revenue_metric()
        metric["metadata"]["version"] = version
        _write_yaml(case.candidate / "metrics/revenue.yaml", metric)
    ref = "metric://sales/revenue@3"
    _asset(case, [PIN, ref])
    outcome = "absent" if version is None else "match" if version == 3 else "version_mismatch"
    findings = (
        []
        if version == 3
        else [
            _finding(
                f"asset.metric_ref_{outcome}",
                (
                    "the candidate snapshot holds no metric://sales/revenue, pinned at version 3"
                    if version is None
                    else "the candidate snapshot holds version 9 of metric://sales/revenue, "
                    "pinned at version 3"
                ),
            )
        ]
    )
    report = case.run()
    _assert_assets(
        case,
        report,
        [_pin(), _pin(ref, [] if version is None else [version], outcome)],
        findings,
        status=CheckStatus.PASS if version == 3 else CheckStatus.BLOCK,
        summary="assets.valid" if version == 3 else f"asset.metric_ref_{outcome}",
    )
    if version == 3:
        assert detail_of(report, "assets.invalidated") == (
            f"{CLEAN}; 2 of 2 metric pin(s) match the candidate snapshot"
        )
    assert status_of(report, "contracts.validate") is CheckStatus.PASS
    assert summary_of(report, "contracts.validate") == "contracts.clean"


def test_a_removed_metric_keeps_its_finding_and_its_pin_is_named_absent(tmp_path: Path) -> None:
    case = Case(tmp_path).with_candidate()
    shutil.rmtree(case.candidate / "metrics")
    _asset(case, [PIN])
    _assert_assets(
        case,
        case.run(),
        [_pin(versions=[], outcome="absent")],
        [
            _finding("asset.metric_removed", f"metric gone: {METRIC}"),
            _finding(
                "asset.metric_ref_absent",
                f"the candidate snapshot holds no {METRIC}, pinned at version 3",
            ),
        ],
        status=CheckStatus.BLOCK,
        summary="asset.metric_removed",
    )


@pytest.mark.parametrize("version", [3, 4])
def test_a_changed_metric_keeps_its_review_beside_the_pin_finding(
    tmp_path: Path, version: int
) -> None:
    case = Case(tmp_path).with_candidate(contract=_version(version, changed=True))
    _move(case, version)
    _asset(case, [PIN])
    findings = [
        _finding(
            "asset.metric_changed",
            f"re-review required: metric.formula_changed on {METRIC}",
            "review",
        )
    ]
    if version == 4:
        findings.append(_mismatch(3, 4))
    _assert_assets(
        case,
        case.run(),
        [_pin(versions=[version], outcome="match" if version == 3 else "version_mismatch")],
        findings,
        status=CheckStatus.REVIEW if version == 3 else CheckStatus.BLOCK,
        summary="asset.metric_changed" if version == 3 else "asset.metric_ref_version_mismatch",
    )


def test_a_finding_about_the_reviewed_query_follows_the_pin_finding(tmp_path: Path) -> None:
    case = Case(tmp_path).with_candidate()
    sql = 'SELECT "region", sum("revenue") AS "gross_margin_pct" FROM "dm"."sales_daily_v2"'
    _asset(case, [f"{METRIC}@99"], reference_query={"engine": "clickhouse", "sql": sql})
    case.with_results(
        ROWS,
        attested=attestation(
            case.candidate_digest,
            query_digest=ReferenceQuery(engine="clickhouse", sql=sql).digest(),
        ),
    )
    report = case.run(environment_ref=ENVIRONMENT_REF)
    _assert_assets(
        case,
        report,
        [_pin(f"{METRIC}@99", outcome="version_mismatch")],
        [
            _mismatch(),
            _finding(
                "asset.query_ungoverned",
                "the reviewed reference_query reads dm.sales_daily_v2, which this snapshot "
                "does not govern: a number from it is not a number about this model",
                "review",
            ),
        ],
        status=CheckStatus.BLOCK,
        summary="asset.metric_ref_version_mismatch",
    )
    assert status_of(report, "results.provenance") is CheckStatus.PASS
    assert status_of(report, "results.reconcile") is CheckStatus.PASS
    assert summary_of(report, "results.reconcile") == "result.equivalent"


def test_a_pin_written_twice_is_graded_once(tmp_path: Path) -> None:
    case = Case(tmp_path)
    _asset(case, [f"{METRIC}@99"] * 2)
    _assert_assets(
        case,
        case.run(),
        [_pin(f"{METRIC}@99", outcome="version_mismatch")],
        [_mismatch()],
        status=CheckStatus.BLOCK,
        summary="asset.metric_ref_version_mismatch",
    )


def test_two_pins_of_one_asset_that_do_not_resolve_are_reported_in_declared_order(
    tmp_path: Path,
) -> None:
    case = Case(tmp_path)
    _asset(case, ["metric://sales/revenue@5", f"{METRIC}@99"])
    report = case.run()
    _assert_assets(
        case,
        report,
        [
            _pin("metric://sales/revenue@5", [], "absent"),
            _pin(f"{METRIC}@99", outcome="version_mismatch"),
        ],
        [
            _finding(
                "asset.metric_ref_absent",
                "the candidate snapshot holds no metric://sales/revenue, pinned at version 5",
            ),
            _mismatch(),
        ],
        status=CheckStatus.BLOCK,
        summary="asset.metric_ref_absent",
    )
    assert detail_of(report, "assets.invalidated") == (
        f"{ASSET}: the candidate snapshot holds no metric://sales/revenue, pinned at version 5; "
        f"{ASSET}: the candidate snapshot holds version 3 of {METRIC}, pinned at version 99"
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_several_assets_are_graded_in_the_order_of_their_ids(tmp_path: Path, reverse: bool) -> None:
    case = Case(tmp_path)
    case.assets = case.root / "assets"
    items = [("a_margin", 1, 99), ("weekly_margin_by_region", 2, 98)]
    for name, version, pinned in reversed(items) if reverse else items:
        asset = verified_asset(case.base_digest)
        asset["metadata"].update(id=f"asset://sales/{name}", version=version)
        asset["metric_refs"] = [f"{METRIC}@{pinned}"]
        _write_yaml(case.assets / f"{name}.yaml", asset)
    first = "asset://sales/a_margin@1"
    _assert_assets(
        case,
        case.run(),
        [
            _pin(f"{METRIC}@99", outcome="version_mismatch", asset=first),
            _pin(f"{METRIC}@98", outcome="version_mismatch"),
        ],
        [_mismatch(asset=first), _mismatch(98)],
        count=2,
        status=CheckStatus.BLOCK,
        summary="asset.metric_ref_version_mismatch",
    )


def test_an_asset_without_metric_refs_has_no_pin_to_grade(tmp_path: Path) -> None:
    case = Case(tmp_path).with_candidate()
    asset = verified_asset(case.base_digest)
    asset.update(
        intent="clarification",
        metric_refs=[],
        expectation={"kind": "clarification", "clarification": "Which region?"},
    )
    asset.pop("reference_query")
    asset.pop("tolerance")
    loaded = load_contracts([case.contracts])
    inputs = GateInputs(
        product=next(iter(loaded.products.values())),
        validation=loaded.report,
        base=NativeModelSource(case.base / "model.yaml", case.base / "metrics").snapshot(),
        candidate=NativeModelSource(
            case.candidate / "model.yaml", case.candidate / "metrics"
        ).snapshot(),
        candidate_model=semantic_model(),
        assets=VerifiedAssetRepository([VerifiedAsset.model_validate(asset)]),
    )
    report = run_gate(
        inputs, store=FilesystemArtifactStore(tmp_path / "evidence"), created_at=CREATED_AT
    )
    _assert_assets(case, report, [])
    assert detail_of(report, "assets.invalidated") == CLEAN


def test_an_asset_of_another_products_metric_stays_out_of_the_run(tmp_path: Path) -> None:
    case = Case(tmp_path)
    _asset(case, ["metric://sales/revenue@99"])
    payload = _assert_assets(case, case.run(), [], count=0)
    assert payload["assets"] == []


def test_a_contract_that_did_not_load_leaves_the_check_not_run(tmp_path: Path) -> None:
    case = Case(tmp_path).with_candidate()
    _asset(case, [PIN])
    (case.candidate / "metrics/gross_margin_pct.yaml").write_text(
        "kind: MetricContract\nmetadata: {id: metric://sales/gross_margin_pct}\n", encoding="utf-8"
    )
    report = case.run()
    assert status_of(report, "semantic.gaps") is CheckStatus.BLOCK
    assert status_of(report, "assets.invalidated") is CheckStatus.REVIEW
    assert summary_of(report, "assets.invalidated") == "check.not_run"
    assert detail_of(report, "assets.invalidated") == (
        "a metric contract did not load, so the snapshot does not represent its source; "
        "a diff would report the parse failure as a removed metric"
    )
    for path in (case.root / "evidence" / report.run_id).rglob("*"):
        if path.is_file():
            assert b"asset.metric_ref_" not in path.read_bytes()
