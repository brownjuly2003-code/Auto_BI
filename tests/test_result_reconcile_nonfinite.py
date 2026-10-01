"""Non-finite value cells compare by kind; an expected NaN cannot prove equality."""

from pathlib import Path

import pytest
import yaml

from auto_bi.evidence.manifest import read_bundle
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.verification.reconcile import ResultTolerance, ShareSumCheck, compare_results
from auto_bi.verification.release_gate import CheckStatus
from tests.test_decision_verify import ASSET_ID, Case, _check_payload, status_of, summary_of
from tests.test_result_reconcile_column_order import _differences, _result


@pytest.mark.parametrize("kind", ["float", "decimal", "integer"])
@pytest.mark.parametrize(
    ("before", "after", "equal"),
    [
        pytest.param("inf", "inf", True, id="positive-same"),
        pytest.param("inf", "Infinity", True, id="positive-spelling"),
        pytest.param("+inf", "Infinity", True, id="positive-explicit-sign"),
        pytest.param("-inf", "-Infinity", True, id="negative-spelling"),
        pytest.param("Infinity", "Infinity", True, id="infinity-control"),
        pytest.param("inf", "-inf", False, id="opposite-signs"),
        pytest.param("-Infinity", "Infinity", False, id="opposite-signs-reverse"),
        pytest.param("Infinity", "-Infinity", False, id="opposite-signs-spelling"),
        pytest.param("inf", "1e308", False, id="infinity-finite"),
        pytest.param("1e308", "inf", False, id="finite-infinity"),
        pytest.param("inf", "nan", False, id="infinity-nan"),
        pytest.param("-inf", "NaN", False, id="negative-infinity-nan"),
        pytest.param("1", "nan", False, id="finite-nan"),
        pytest.param("1", "NaN", False, id="finite-nan-spelling"),
        pytest.param("invalid", "nan", False, id="unparsed-nan"),
        pytest.param("inf", "invalid", False, id="infinity-unparsed"),
        pytest.param("invalid", "invalid", True, id="unparsed-same-control"),
        pytest.param("invalid", "other", False, id="unparsed-different-control"),
    ],
)
def test_nonfinite_kinds(kind: str, before: str, after: str, equal: bool) -> None:
    base = _result([("id", "string"), ("m", kind)], [["east", before]])
    candidate = _result([("id", "string"), ("m", kind)], [["east", after]])
    unchanged = (base.model_dump_json(), candidate.model_dump_json())
    report = compare_results(
        base,
        candidate,
        ResultTolerance(business_key=["id"], float_absolute="0.01", float_relative="0.5"),
    )
    assert report.outcome == ("equivalent" if equal else "different")
    assert report.compared_rows == 1
    assert _differences(report) == (
        [] if equal else [("value.changed", f"east.m: {before!r} -> {after!r}")]
    )
    assert (base.model_dump_json(), candidate.model_dump_json()) == unchanged


@pytest.mark.parametrize(
    ("kind", "before"),
    [
        ("float", "nan"),
        ("float", "NaN"),
        ("decimal", "NaN"),
        ("decimal", "sNaN"),
        ("integer", "NaN"),
        ("integer", "sNaN"),
    ],
    ids=["float-nan", "float-NaN", "decimal-NaN", "decimal-sNaN", "integer-NaN", "integer-sNaN"],
)
@pytest.mark.parametrize("after", ["nan", "NaN", "sNaN", "1", "inf", "-inf", "invalid"])
def test_expected_nan_cannot_settle_cell(kind: str, before: str, after: str) -> None:
    report = compare_results(
        _result([("id", "string"), ("m", kind)], [["east", before]]),
        _result([("id", "string"), ("m", kind)], [["east", after]]),
        ResultTolerance(business_key=["id"], float_absolute="0.01"),
    )
    assert report.outcome == "insufficient"
    assert report.compared_rows == 1
    assert _differences(report) == [
        ("value.expected_nan", f"east.m: expected {before!r} is not a number")
    ]


@pytest.mark.parametrize(
    ("kind", "before", "after", "equal"),
    [
        pytest.param("float", "1e400", "inf", True, id="float-overflow-positive"),
        pytest.param("float", "-1e400", "-Infinity", True, id="float-overflow-negative"),
        pytest.param("float", "1e400", "-inf", False, id="float-overflow-opposite"),
        pytest.param("decimal", "1e400", "inf", False, id="decimal-no-overflow"),
        pytest.param("integer", "1e400", "inf", False, id="integer-no-overflow"),
        pytest.param("decimal", "1", "sNaN", False, id="decimal-candidate-sNaN"),
        pytest.param("integer", "inf", "sNaN", False, id="integer-candidate-sNaN"),
        pytest.param("float", "sNaN", "sNaN", True, id="float-sNaN-unparsed"),
        pytest.param("float", "0.1", "0.3", True, id="finite-within-control"),
        pytest.param("float", "0.1", "0.4", False, id="finite-outside-control"),
        pytest.param("decimal", "0.4120", "0.412", True, id="decimal-finite-control"),
        pytest.param("integer", "01", "1", True, id="integer-finite-control"),
    ],
)
def test_column_number_syntax(kind: str, before: str, after: str, equal: bool) -> None:
    report = compare_results(
        _result([("id", "string"), ("m", kind)], [["east", before]]),
        _result([("id", "string"), ("m", kind)], [["east", after]]),
        ResultTolerance(business_key=["id"], float_absolute="0.2"),
    )
    assert report.outcome == ("equivalent" if equal else "different")
    assert report.compared_rows == 1
    assert _differences(report) == (
        [] if equal else [("value.changed", f"east.m: {before!r} -> {after!r}")]
    )


@pytest.mark.parametrize("absolute,relative", [("3" + "0" * 308, None), (None, "1" + "0" * 400)])
@pytest.mark.parametrize("before,after", [("inf", "1"), ("1", "inf"), ("-inf", "inf")])
def test_tolerance_never_reaches_nonfinite(
    before: str, after: str, absolute: str | None, relative: str | None
) -> None:
    report = compare_results(
        _result([("id", "string"), ("m", "float")], [["east", before]]),
        _result([("id", "string"), ("m", "float")], [["east", after]]),
        ResultTolerance(business_key=["id"], float_absolute=absolute, float_relative=relative),
    )
    assert report.outcome == "different"
    assert report.compared_rows == 1
    assert _differences(report) == [("value.changed", f"east.m: {before!r} -> {after!r}")]


@pytest.mark.parametrize(
    "kind,written", [("float", "nan"), ("decimal", "sNaN"), ("integer", "NaN")]
)
@pytest.mark.parametrize("expected_null", [False, True])
def test_null_precedes_nonfinite(kind: str, written: str, expected_null: bool) -> None:
    before, after = (None, written) if expected_null else (written, None)
    report = compare_results(
        _result([("id", "string"), ("m", kind)], [["east", before]]),
        _result([("id", "string"), ("m", kind)], [["east", after]]),
        ResultTolerance(business_key=["id"], float_absolute="0.01"),
    )
    assert report.outcome == "different"
    assert report.compared_rows == 1
    assert _differences(report) == (
        [("value.null_became_value", f"east.m: NULL -> {written!r}")]
        if expected_null
        else [("value.value_became_null", f"east.m: {written!r} -> NULL")]
    )


def test_expected_nan_listed_after_other_value_changes_in_expected_row_order() -> None:
    columns = [("id", "string"), ("m", "float"), ("label", "string")]
    base = _result(columns, [["east", "nan", "a"], ["west", "1", "b"], ["north", "NaN", "c"]])
    candidate = _result(
        columns, [["north", "2", "c"], ["west", "2", "b"], ["east", "nan", "changed"]]
    )
    report = compare_results(
        base, candidate, ResultTolerance(business_key=["id"], float_absolute="0.01")
    )
    assert report.outcome == "different"
    assert report.compared_rows == 3
    assert _differences(report) == [
        ("value.changed", "east.label: 'a' -> 'changed'"),
        ("value.changed", "west.m: '1' -> '2'"),
        ("value.expected_nan", "east.m: expected 'nan' is not a number"),
        ("value.expected_nan", "north.m: expected 'NaN' is not a number"),
    ]


def test_expected_nan_does_not_hide_value_change_elsewhere() -> None:
    columns = [("id", "string"), ("m", "float")]
    report = compare_results(
        _result(columns, [["east", "nan"], ["west", "1"]]),
        _result(columns, [["east", "nan"], ["west", "2"]]),
        ResultTolerance(business_key=["id"], float_absolute="0.01"),
    )
    assert report.outcome == "different"
    assert report.compared_rows == 2
    assert _differences(report) == [
        ("value.changed", "west.m: '1' -> '2'"),
        ("value.expected_nan", "east.m: expected 'nan' is not a number"),
    ]


def test_expected_nan_does_not_hide_membership_changes() -> None:
    columns = [("id", "string"), ("m", "float")]
    report = compare_results(
        _result(columns, [["east", "nan"], ["west", "1"]]),
        _result(columns, [["east", "nan"], ["north", "1"]]),
        ResultTolerance(business_key=["id"], float_absolute="0.01"),
    )
    assert report.outcome == "different"
    assert report.compared_rows == 1
    assert _differences(report) == [
        ("row.missing", "row west is gone"),
        ("row.added", "new row north"),
        ("value.expected_nan", "east.m: expected 'nan' is not a number"),
    ]


@pytest.mark.parametrize("reordered", [False, True])
def test_expected_nan_does_not_hide_order_change(reordered: bool) -> None:
    columns = [("id", "string"), ("m", "float")]
    rows = [["east", "nan"], ["west", "NaN"]]
    report = compare_results(
        _result(columns, rows),
        _result(columns, list(reversed(rows)) if reordered else rows),
        ResultTolerance(business_key=["id"], float_absolute="0.01", ordered=True),
    )
    assert report.outcome == ("different" if reordered else "insufficient")
    assert report.compared_rows == 2
    assert _differences(report) == (
        [
            (
                "row.order_changed",
                "row order changed: [('east',), ('west',)] -> " "[('west',), ('east',)]",
            )
        ]
        if reordered
        else []
    ) + [
        ("value.expected_nan", "east.m: expected 'nan' is not a number"),
        ("value.expected_nan", "west.m: expected 'NaN' is not a number"),
    ]


@pytest.mark.parametrize("failed", [False, True])
def test_expected_nan_precedes_candidate_aggregate_issue(failed: bool) -> None:
    columns = [("id", "string"), ("m", "float"), ("share", "float")]
    report = compare_results(
        _result(columns, [["east", "nan", "1"]]),
        _result(columns, [["east", "nan", "2" if failed else "1"]]),
        ResultTolerance(
            business_key=["id"],
            float_absolute="10",
            aggregate_checks=[ShareSumCheck(share_column="share")],
        ),
    )
    assert report.outcome == ("different" if failed else "insufficient")
    assert report.compared_rows == 1
    assert _differences(report) == [
        ("value.expected_nan", "east.m: expected 'nan' is not a number")
    ] + (
        [
            (
                "aggregate.share_sum.candidate_failed",
                "candidate share_sum: group all sums to 2, expected 1",
            )
        ]
        if failed
        else []
    )


@pytest.mark.parametrize("side", ["base", "candidate"])
def test_aggregate_still_refuses_nonfinite_input(side: str) -> None:
    columns = [("id", "string"), ("m", "decimal")]
    report = compare_results(
        _result(columns, [["east", "NaN" if side == "base" else "1"]]),
        _result(columns, [["east", "NaN"]]),
        ResultTolerance(business_key=["id"], aggregate_checks=[ShareSumCheck(share_column="m")]),
    )
    assert report.outcome == ("insufficient" if side == "base" else "different")
    assert report.compared_rows == (0 if side == "base" else 1)
    assert _differences(report) == (
        [] if side == "base" else [("value.changed", "east.m: '1' -> 'NaN'")]
    ) + [
        (
            f"aggregate.share_sum.{side}_insufficient",
            f"{side} share_sum: numeric column 'm' contains non-finite value 'NaN'",
        )
    ]


@pytest.mark.parametrize("kind", ["float", "decimal", "integer", "string"])
def test_key_cell_is_settled_by_row_match(kind: str) -> None:
    report = compare_results(
        _result([("id", kind), ("m", "decimal")], [["nan", "1"]]),
        _result([("m", "decimal"), ("id", kind)], [["1", "nan"]]),
        ResultTolerance(business_key=["id"], float_absolute="0.01"),
    )
    assert report.outcome == "equivalent"
    assert report.compared_rows == 1
    assert _differences(report) == []


def test_nonfinite_float_key_keeps_written_identity() -> None:
    report = compare_results(
        _result([("id", "float"), ("m", "decimal")], [["nan", "1"]]),
        _result([("id", "float"), ("m", "decimal")], [["NaN", "1"]]),
        ResultTolerance(business_key=["id"], float_absolute="0.01"),
    )
    assert report.outcome == "different"
    assert report.compared_rows == 0
    assert _differences(report) == [
        ("row.missing", "row nan is gone"),
        ("row.added", "new row NaN"),
    ]


def test_gate_expected_nan_requires_review(tmp_path: Path) -> None:
    case = Case(tmp_path).with_asset().with_results([["east", "nan"]])
    assert case.assets is not None
    asset_path = case.assets / "weekly_margin_by_region.yaml"
    asset = yaml.safe_load(asset_path.read_text(encoding="utf-8"))
    asset["expectation"]["result"]["columns"][1]["type"] = "float"
    asset["expectation"]["result"]["rows"] = [["east", "nan"]]
    asset["tolerance"]["float_absolute"] = "0.01"
    asset_path.write_text(yaml.safe_dump(asset, allow_unicode=True), encoding="utf-8")
    assert case.results is not None
    payload = yaml.safe_load(case.results.read_text(encoding="utf-8"))
    payload[ASSET_ID]["result"]["columns"][1]["type"] = "float"
    case.results.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    report = case.run()
    assert status_of(report, "results.reconcile") is CheckStatus.REVIEW
    assert summary_of(report, "results.reconcile") == "result.not_equivalent"
    comparisons = _check_payload(tmp_path, report, "results.reconcile")["comparisons"]
    assert len(comparisons) == 1
    assert comparisons[0]["outcome"] == "insufficient"
    assert comparisons[0]["compared_rows"] == 1
    assert comparisons[0]["differences"] == [
        {
            "code": "value.expected_nan",
            "detail": "east.gross_margin_pct: expected 'nan' is not a number",
        }
    ]
    bundle = read_bundle(FilesystemArtifactStore(tmp_path / "evidence"), report.run_id)
    assert not [claim for claim in bundle.claims if claim.summary_code == "result.equivalent"]
