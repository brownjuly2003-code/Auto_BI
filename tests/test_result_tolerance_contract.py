"""Tolerance admission and the value boundary of supplied results."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import TypeAdapter, ValidationError

from auto_bi.cli import main
from auto_bi.scalars import DecimalString
from auto_bi.verification.reconcile import (
    ComparisonOutcome,
    RatioReconstructionCheck,
    ResultSet,
    ResultTolerance,
    ShareSumCheck,
    compare_results,
)
from auto_bi.verification.repository import VerifiedAsset
from auto_bi.verification.result_probe import QueryColumn, QueryResult, ResultCaptureError, capture
from tests.test_decision_cli import _verify_argv
from tests.test_decision_verify import ASSET_ID, CREATED_AT, Case
from tests.test_result_probe import _asset
from tests.test_verified_assets import asset

FLOAT_FIELDS = ["float_absolute", "float_relative"]
TINY_NEGATIVE = "-0." + "0" * 400 + "1"
INVALID_DECIMALS = ["NaN", "nan", "sNaN", "Infinity", "-Infinity", "inf", "+inf", "-0", "-0.0"]


def float_result(value: str) -> ResultSet:
    return ResultSet.model_validate(
        {
            "columns": [{"name": "id", "type": "string"}, {"name": "m", "type": "float"}],
            "rows": [["east", value]],
        }
    )


@pytest.mark.parametrize("field", FLOAT_FIELDS)
@pytest.mark.parametrize("value", ["-1", "-0.01", pytest.param(TINY_NEGATIVE, id="tiny-negative")])
def test_negative_float_tolerance_is_an_invalid_contract(field: str, value: str) -> None:
    assert TypeAdapter(DecimalString).validate_python(value) == value
    with pytest.raises(ValidationError) as caught:
        VerifiedAsset.model_validate(asset(tolerance={"business_key": ["region"], field: value}))

    error = caught.value.errors()[0]
    assert error["loc"] == ("tolerance", field)
    assert error["input"] == value
    assert field in error["msg"] and value in error["msg"]


@pytest.mark.parametrize("field", FLOAT_FIELDS)
@pytest.mark.parametrize("value", INVALID_DECIMALS)
def test_noncanonical_float_tolerance_is_refused(field: str, value: str) -> None:
    with pytest.raises(ValidationError) as caught:
        ResultTolerance.model_validate({field: value})
    assert caught.value.errors()[0]["loc"] == (field,)
    assert caught.value.errors()[0]["input"] == value


@pytest.mark.parametrize("value", INVALID_DECIMALS)
def test_decimal_string_refuses_nonfinite_values_and_negative_zero(value: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(DecimalString).validate_python(value)


@pytest.mark.parametrize("field", FLOAT_FIELDS)
@pytest.mark.parametrize("value", ["0", "0.00", "0.01", "1", "1" + "0" * 309])
def test_nonnegative_float_tolerance_accepts_equal_values(field: str, value: str) -> None:
    tolerance = ResultTolerance.model_validate({"business_key": ["id"], field: value})
    base = float_result("1.0")
    report = compare_results(base, base, tolerance)
    assert report.outcome is ComparisonOutcome.EQUIVALENT
    assert report.differences == []
    assert report.compared_rows == 1


@pytest.mark.parametrize("field", FLOAT_FIELDS)
def test_zero_float_tolerance_still_detects_a_change(field: str) -> None:
    report = compare_results(
        float_result("1.0"),
        float_result("1.0000000000000002"),
        ResultTolerance.model_validate({"business_key": ["id"], field: "0"}),
    )
    assert report.outcome is ComparisonOutcome.DIFFERENT
    assert [item.code for item in report.differences] == ["value.changed"]


@pytest.mark.parametrize("value", ["-1", *INVALID_DECIMALS])
def test_aggregate_absolute_tolerance_already_refuses_invalid_values(value: str) -> None:
    with pytest.raises(ValidationError, match="absolute_tolerance"):
        ShareSumCheck(share_column="m", absolute_tolerance=value)


@pytest.mark.parametrize("field", ["expected_sum", "factor"])
@pytest.mark.parametrize("value", INVALID_DECIMALS)
def test_aggregate_parameters_already_refuse_noncanonical_values(field: str, value: str) -> None:
    document = (
        {"kind": "share_sum", "share_column": "m"}
        if field == "expected_sum"
        else {
            "kind": "ratio_reconstruction",
            "ratio_column": "m",
            "numerator_column": "n",
            "denominator_column": "d",
        }
    )
    with pytest.raises(ValidationError, match=field):
        ResultTolerance.model_validate({"aggregate_checks": [{**document, field: value}]})


@pytest.mark.parametrize("value", ["-1", "0", "1"])
@pytest.mark.parametrize("kind", ["share_sum", "ratio_reconstruction"])
def test_signed_aggregate_parameters_compare_the_declared_value(kind: str, value: str) -> None:
    base = ResultSet.model_validate(
        {
            "columns": [
                {"name": "id", "type": "string"},
                *({"name": name, "type": "decimal"} for name in ("m", "n", "d")),
            ],
            "rows": [["east", value, "1", "1"]],
        }
    )
    check = (
        ShareSumCheck(share_column="m", expected_sum=value)
        if kind == "share_sum"
        else RatioReconstructionCheck(
            ratio_column="m", numerator_column="n", denominator_column="d", factor=value
        )
    )
    tolerance = ResultTolerance(business_key=["id"], aggregate_checks=[check])
    assert compare_results(base, base, tolerance).outcome is ComparisonOutcome.EQUIVALENT
    changed = base.model_copy(deep=True)
    changed.rows[0][1] = "2"
    report = compare_results(base, changed, tolerance)
    assert report.outcome is ComparisonOutcome.DIFFERENT
    assert f"aggregate.{kind}.candidate_failed" in [item.code for item in report.differences]


@pytest.mark.parametrize("field", FLOAT_FIELDS)
@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity", "-0"])
@pytest.mark.parametrize("command", ["verify", "capture"])
def test_cli_refuses_an_invalid_tolerance_at_asset_load(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
    command: str,
) -> None:
    case = Case(tmp_path).with_candidate().with_asset()
    if command == "capture":

        def unexpected_query():
            pytest.fail("an invalid asset reached the query factory")

        monkeypatch.setattr(
            "auto_bi.cli._result_capture_target",
            lambda engine: (unexpected_query, "clickhouse-stage:9000/dm", "gate_ro"),
        )
    assert case.assets is not None
    asset_path = case.assets / "weekly_margin_by_region.yaml"
    document = yaml.safe_load(asset_path.read_text(encoding="utf-8"))
    document["tolerance"][field] = value
    asset_path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    output = tmp_path / "captured.yaml"
    argv = (
        _verify_argv(case, "--json")
        if command == "verify"
        else [
            "decision",
            "capture",
            str(case.assets),
            "--candidate",
            str(case.candidate),
            "--asset",
            ASSET_ID,
            "--dwh",
            "clickhouse",
            "--output",
            str(output),
            "--captured-at",
            CREATED_AT,
        ]
    )

    assert main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "is not a valid verified asset" in captured.err
    assert "weekly_margin_by_region.yaml" in captured.err
    assert field in captured.err
    if value == "-1":
        assert value in captured.err
    assert not output.exists()
    assert not (tmp_path / "evidence").exists()


@pytest.mark.parametrize("kind,value", [("integer", None), ("integer", "not-an-integer")])
def test_supplied_values_are_not_validated_against_column_constraints(
    kind: str, value: str | None
) -> None:
    supplied = ResultSet.model_validate(
        {
            "columns": [
                {"name": "id", "type": "string"},
                {"name": "m", "type": kind, "nullable": False},
            ],
            "rows": [["east", value]],
        }
    )
    report = compare_results(supplied, supplied, ResultTolerance(business_key=["id"]))
    assert supplied.rows == [["east", value]]
    assert report.outcome is ComparisonOutcome.EQUIVALENT
    assert report.compared_rows == 1
    assert report.differences == []


def test_native_capture_refuses_null_in_a_nonnullable_column() -> None:
    reviewed = _asset(
        engine="postgres",
        sql="SELECT region FROM dm.sales_daily",
        columns=[{"name": "region", "type": "string", "nullable": False}],
    )
    with pytest.raises(ResultCaptureError, match="NULL for non-nullable column 'region'"):
        capture(
            reviewed,
            lambda sql: QueryResult(columns=(QueryColumn("region", "text"),), rows=((None,),)),
            candidate_digest="sha256:" + "1" * 64,
            environment_ref="postgres-stage:5432/analytics",
            executed_by="gate_ro",
            captured_at=CREATED_AT,
        )


def test_tolerance_admission_moves_only_the_verifier_version() -> None:
    from auto_bi.verification.release_gate import CHECK_VERSION, VERIFIER_VERSION

    assert int(VERIFIER_VERSION) >= 125
    assert int(CHECK_VERSION) >= 81
