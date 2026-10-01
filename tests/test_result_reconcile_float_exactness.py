"""Float tolerances bound the exact difference of the parsed binary64 values."""

from pathlib import Path

import pytest
import yaml

from auto_bi.evidence.manifest import read_bundle
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.verification.reconcile import ResultTolerance, compare_results
from auto_bi.verification.release_gate import CheckStatus
from tests.test_decision_verify import ASSET_ID, Case, _check_payload, status_of, summary_of
from tests.test_result_reconcile_column_order import _differences, _result


@pytest.mark.parametrize(
    ("before", "after", "absolute", "relative", "equivalent"),
    [
        pytest.param("0.1", "0.3", "0.19999999999999997", None, False, id="absolute-over"),
        pytest.param("0.1", "0.3", "0.2", None, True, id="absolute-within-control"),
        pytest.param("100", "110", None, "0.090909090909090906", False, id="relative-over"),
        pytest.param(
            "100", "110", None, "0.0909090909090909091", True, id="relative-within-control"
        ),
        pytest.param(
            "100", "110", None, "0.0909090909090909", False, id="relative-outside-control"
        ),
        pytest.param(
            "0.0017108284528077355",
            "0.009525102111858402",
            None,
            "0.820387389792092933943225376839",
            True,
            id="relative-rounded-ratio-rejects-within",
        ),
        pytest.param(
            "0.0017108284528077355",
            "0.009525102111858402",
            None,
            "0.820387389792092933943225376838",
            False,
            id="relative-neighbor-outside",
        ),
        pytest.param(
            "0.1", "0.1000000000000000055511151231257827", "0", None, True, id="same-binary64"
        ),
        pytest.param("0.5", "0.75", "0.25", None, True, id="absolute-at-bound"),
        pytest.param("1", "2", None, "0.5", True, id="relative-at-bound"),
        pytest.param("0.1", "0.3", "0.2", "0.1", True, id="both-absolute-holds"),
        pytest.param("100", "110", "9", "0.1", True, id="both-relative-holds"),
        pytest.param("0.1", "0.3", "0.19999999999999997", "0.1", False, id="both-outside"),
        pytest.param("0.0", "-0.0", None, "0", True, id="signed-zero"),
        pytest.param("-100", "-110", None, "0.0909090909090909091", True, id="negative-values"),
        pytest.param("-0.5", "0.5", "1", None, True, id="cross-zero"),
        pytest.param("unparsed", "unparsed", "0", None, True, id="unparsed-same-control"),
        pytest.param("unparsed", "other", "100", None, False, id="unparsed-different-control"),
        pytest.param("1e308", "-1e308", "3" + "0" * 308, None, True, id="finite-large-absolute"),
        pytest.param("1e308", "-1e308", None, "2", True, id="finite-large-relative"),
        pytest.param("1", "inf", "3" + "0" * 308, None, False, id="beyond-binary64-infinity"),
        pytest.param("-inf", "inf", "3" + "0" * 308, None, False, id="beyond-binary64-opposite"),
    ],
)
def test_float_tolerance_exact_difference(
    before: str, after: str, absolute: str | None, relative: str | None, equivalent: bool
) -> None:
    report = compare_results(
        _result([("id", "string"), ("m", "float")], [["east", before]]),
        _result([("id", "string"), ("m", "float")], [["east", after]]),
        ResultTolerance(business_key=["id"], float_absolute=absolute, float_relative=relative),
    )
    assert report.outcome == ("equivalent" if equivalent else "different")
    assert report.compared_rows == 1
    assert _differences(report) == (
        [] if equivalent else [("value.changed", f"east.m: {before!r} -> {after!r}")]
    )


@pytest.mark.parametrize(
    ("absolute", "equivalent"),
    [("0.2", True), ("0.19999999999999997", False)],
    ids=["within", "outside"],
)
def test_moved_float_keeps_exact_tolerance(absolute: str, equivalent: bool) -> None:
    base = _result(
        [("id", "string"), ("m", "float"), ("label", "string")], [["east", "0.1", "retail"]]
    )
    candidate = _result(
        [("id", "string"), ("label", "string"), ("m", "float")], [["east", "retail", "0.3"]]
    )
    before = (base.model_dump_json(), candidate.model_dump_json())
    report = compare_results(
        base, candidate, ResultTolerance(business_key=["id"], float_absolute=absolute)
    )
    assert report.outcome == ("equivalent" if equivalent else "different")
    assert report.compared_rows == 1
    assert _differences(report) == (
        [] if equivalent else [("value.changed", "east.m: '0.1' -> '0.3'")]
    )
    assert (base.model_dump_json(), candidate.model_dump_json()) == before


@pytest.mark.parametrize(
    ("absolute", "equivalent"),
    [("0.2", True), ("0.19999999999999997", False)],
    ids=["valid-control", "absolute-over"],
)
def test_gate_float_absolute_exactness(tmp_path: Path, absolute: str, equivalent: bool) -> None:
    case = Case(tmp_path).with_asset().with_results([["east", "0.3"]])
    assert case.assets is not None
    asset_path = case.assets / "weekly_margin_by_region.yaml"
    asset = yaml.safe_load(asset_path.read_text(encoding="utf-8"))
    asset["expectation"]["result"]["columns"][1]["type"] = "float"
    asset["expectation"]["result"]["rows"] = [["east", "0.1"]]
    asset["tolerance"]["float_absolute"] = absolute
    asset_path.write_text(yaml.safe_dump(asset, allow_unicode=True), encoding="utf-8")
    assert case.results is not None
    payload = yaml.safe_load(case.results.read_text(encoding="utf-8"))
    payload[ASSET_ID]["result"]["columns"][1]["type"] = "float"
    case.results.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    report = case.run()
    assert status_of(report, "results.reconcile") is (
        CheckStatus.PASS if equivalent else CheckStatus.BLOCK
    )
    assert summary_of(report, "results.reconcile") == (
        "result.equivalent" if equivalent else "result.not_equivalent"
    )
    comparisons = _check_payload(tmp_path, report, "results.reconcile")["comparisons"]
    assert len(comparisons) == 1
    assert comparisons[0]["outcome"] == ("equivalent" if equivalent else "different")
    assert comparisons[0]["compared_rows"] == 1
    assert comparisons[0]["differences"] == (
        []
        if equivalent
        else [{"code": "value.changed", "detail": "east.gross_margin_pct: '0.1' -> '0.3'"}]
    )
    bundle = read_bundle(FilesystemArtifactStore(tmp_path / "evidence"), report.run_id)
    claims = [claim for claim in bundle.claims if claim.summary_code == "result.equivalent"]
    assert len(claims) == (1 if equivalent else 0)
