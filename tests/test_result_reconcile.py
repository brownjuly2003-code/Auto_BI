"""Typed result comparison (DECISIONOPS_ARCHITECTURE §8.4, bootstrap task 5).

The comparator's job is to be honest about equivalence, not to be lenient. Schema is
compared before values; rows are matched by a declared business key rather than by
position; exact types compare exactly and floats only within a declared tolerance; `NULL`,
zero, a missing row and an empty result are four different things; and a truncated result
cannot prove that two full results agree — it returns `insufficient`, never `equivalent`.
Explicit aggregate checks also prevent row-wise tolerance from hiding a broken whole.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from auto_bi.verification.reconcile import (
    ColumnSchema,
    ColumnType,
    ComparisonOutcome,
    ResultSet,
    ResultTolerance,
    compare_results,
)

KEY = ResultTolerance(business_key=["region"])


def result(rows: list[list[Any]], *, truncated: bool = False, **columns: str) -> ResultSet:
    schema = columns or {"region": "string", "margin": "decimal"}
    return ResultSet(
        columns=[ColumnSchema(name=name, type=ColumnType(kind)) for name, kind in schema.items()],
        rows=rows,
        truncated=truncated,
    )


def codes(report: Any) -> list[str]:
    return [difference.code for difference in report.differences]


def test_identical_results_are_equivalent() -> None:
    rows = [["north", "0.412"], ["south", "0.318"]]
    report = compare_results(result(rows), result(rows), KEY)
    assert report.outcome is ComparisonOutcome.EQUIVALENT
    assert report.differences == []
    assert report.compared_rows == 2


def test_row_order_alone_is_not_a_difference() -> None:
    """Order is part of the contract only for rank/top-N results (§8.4)."""
    base = result([["north", "0.412"], ["south", "0.318"]])
    candidate = result([["south", "0.318"], ["north", "0.412"]])
    assert compare_results(base, candidate, KEY).outcome is ComparisonOutcome.EQUIVALENT


def test_row_order_matters_when_the_contract_says_so() -> None:
    base = result([["north", "0.412"], ["south", "0.318"]])
    candidate = result([["south", "0.318"], ["north", "0.412"]])
    report = compare_results(
        base, candidate, ResultTolerance(business_key=["region"], ordered=True)
    )
    assert report.outcome is ComparisonOutcome.DIFFERENT
    assert codes(report) == ["row.order_changed"]


def test_schema_is_compared_before_values() -> None:
    base = result([["north", "0.412"]])
    candidate = result([["north", "0.999"]], region="string", margin_pct="decimal")
    report = compare_results(base, candidate, KEY)
    assert report.outcome is ComparisonOutcome.DIFFERENT
    assert codes(report) == ["schema.column_removed", "schema.column_added"]
    assert report.compared_rows == 0


def test_type_change_is_a_schema_difference() -> None:
    base = result([["north", "0.412"]])
    candidate = result([["north", "0.412"]], region="string", margin="float")
    assert codes(compare_results(base, candidate, KEY)) == ["schema.type_changed"]


def test_unit_change_is_a_schema_difference() -> None:
    base = ResultSet(
        columns=[
            ColumnSchema(name="region", type=ColumnType.STRING),
            ColumnSchema(name="revenue", type=ColumnType.DECIMAL, unit="RUB"),
        ],
        rows=[["north", "100"]],
    )
    candidate = ResultSet(
        columns=[
            ColumnSchema(name="region", type=ColumnType.STRING),
            ColumnSchema(name="revenue", type=ColumnType.DECIMAL, unit="kRUB"),
        ],
        rows=[["north", "100"]],
    )
    assert codes(compare_results(base, candidate, KEY)) == ["schema.unit_changed"]


def test_nullability_change_is_a_schema_difference() -> None:
    base = ResultSet(
        columns=[
            ColumnSchema(name="region", type=ColumnType.STRING),
            ColumnSchema(name="margin", type=ColumnType.DECIMAL, nullable=False),
        ],
        rows=[["north", "0.4"]],
    )
    candidate = ResultSet(
        columns=[
            ColumnSchema(name="region", type=ColumnType.STRING),
            ColumnSchema(name="margin", type=ColumnType.DECIMAL, nullable=True),
        ],
        rows=[["north", "0.4"]],
    )
    assert codes(compare_results(base, candidate, KEY)) == ["schema.nullability_changed"]


def test_decimals_compare_exactly_but_ignore_trailing_zeros() -> None:
    base = result([["north", "0.4120"]])
    same = result([["north", "0.412"]])
    off_by_one_ulp = result([["north", "0.4121"]])
    assert compare_results(base, same, KEY).outcome is ComparisonOutcome.EQUIVALENT
    report = compare_results(base, off_by_one_ulp, KEY)
    assert report.outcome is ComparisonOutcome.DIFFERENT
    assert codes(report) == ["value.changed"]
    assert "0.4121" in report.differences[0].detail


def test_float_needs_a_declared_tolerance() -> None:
    """Without a tolerance a float comparison cannot prove anything (§8.4)."""
    base = result([["north", "0.412"]], region="string", margin="float")
    candidate = result([["north", "0.4120000000001"]], region="string", margin="float")
    report = compare_results(base, candidate, KEY)
    assert report.outcome is ComparisonOutcome.INSUFFICIENT
    assert codes(report) == ["tolerance.missing"]


def test_float_within_absolute_tolerance_is_equivalent() -> None:
    base = result([["north", "0.412"]], region="string", margin="float")
    candidate = result([["north", "0.4120000000001"]], region="string", margin="float")
    tolerance = ResultTolerance(business_key=["region"], float_absolute="0.000001")
    assert compare_results(base, candidate, tolerance).outcome is ComparisonOutcome.EQUIVALENT


def test_float_outside_relative_tolerance_differs() -> None:
    base = result([["north", "100.0"]], region="string", margin="float")
    candidate = result([["north", "101.0"]], region="string", margin="float")
    tolerance = ResultTolerance(business_key=["region"], float_relative="0.001")
    report = compare_results(base, candidate, tolerance)
    assert report.outcome is ComparisonOutcome.DIFFERENT
    assert codes(report) == ["value.changed"]


def test_null_zero_and_missing_are_three_different_states() -> None:
    with_null = result([["north", None]])
    with_zero = result([["north", "0"]])
    without_row = result([])

    null_vs_zero = compare_results(with_null, with_zero, KEY)
    assert null_vs_zero.outcome is ComparisonOutcome.DIFFERENT
    assert codes(null_vs_zero) == ["value.null_became_value"]

    zero_vs_null = compare_results(with_zero, with_null, KEY)
    assert codes(zero_vs_null) == ["value.value_became_null"]

    null_vs_missing = compare_results(with_null, without_row, KEY)
    assert codes(null_vs_missing) == ["row.missing"]


def test_added_and_missing_rows_are_named_with_their_key() -> None:
    base = result([["north", "0.4"], ["south", "0.3"]])
    candidate = result([["north", "0.4"], ["west", "0.2"]])
    report = compare_results(base, candidate, KEY)
    assert codes(report) == ["row.missing", "row.added"]
    assert "south" in report.differences[0].detail
    assert "west" in report.differences[1].detail


def test_empty_result_is_not_the_same_as_no_result() -> None:
    report = compare_results(result([]), result([["north", "0.4"]]), KEY)
    assert report.outcome is ComparisonOutcome.DIFFERENT
    assert codes(report) == ["row.added"]
    assert compare_results(result([]), result([]), KEY).outcome is ComparisonOutcome.EQUIVALENT


def test_truncated_result_cannot_prove_equivalence() -> None:
    rows = [["north", "0.412"]]
    report = compare_results(result(rows), result(rows, truncated=True), KEY)
    assert report.outcome is ComparisonOutcome.INSUFFICIENT
    assert codes(report) == ["result.truncated"]


def test_non_unique_business_key_refuses_to_guess() -> None:
    """A key that does not identify a row makes row matching arbitrary — say so (§8.4)."""
    rows = [["north", "0.4"], ["north", "0.5"]]
    report = compare_results(result(rows), result(rows), KEY)
    assert report.outcome is ComparisonOutcome.INSUFFICIENT
    assert codes(report) == ["key.not_unique"]
    assert "north" in report.differences[0].detail


def test_business_key_must_exist_in_the_schema() -> None:
    report = compare_results(
        result([["north", "0.4"]]),
        result([["north", "0.4"]]),
        ResultTolerance(business_key=["zone"]),
    )
    assert report.outcome is ComparisonOutcome.INSUFFICIENT
    assert codes(report) == ["key.unknown_column"]


def test_missing_business_key_is_refused_rather_than_sorted() -> None:
    rows = [["north", "0.4"]]
    report = compare_results(result(rows), result(rows), ResultTolerance())
    assert report.outcome is ComparisonOutcome.INSUFFICIENT
    assert codes(report) == ["key.undeclared"]


@pytest.mark.parametrize(
    ("rows", "columns", "check", "expected_code"),
    [
        (
            [["north", "40", "100"], ["south", "50", "100"]],
            {"region": "string", "amount": "decimal", "total": "decimal"},
            {
                "kind": "partition_conservation",
                "value_column": "amount",
                "total_column": "total",
            },
            "aggregate.partition_conservation.base_failed",
        ),
        (
            [["north", "0.4"], ["south", "0.5"]],
            {"region": "string", "share": "decimal"},
            {"kind": "share_sum", "share_column": "share"},
            "aggregate.share_sum.base_failed",
        ),
        (
            [["north", "40", "100", "0.5"]],
            {
                "region": "string",
                "numerator": "decimal",
                "denominator": "decimal",
                "ratio": "decimal",
            },
            {
                "kind": "ratio_reconstruction",
                "ratio_column": "ratio",
                "numerator_column": "numerator",
                "denominator_column": "denominator",
            },
            "aggregate.ratio_reconstruction.base_failed",
        ),
        (
            [["north", "100", "60", "30"]],
            {
                "region": "string",
                "total": "decimal",
                "online": "decimal",
                "retail": "decimal",
            },
            {
                "kind": "additive_decomposition",
                "total_column": "total",
                "component_columns": ["online", "retail"],
            },
            "aggregate.additive_decomposition.base_failed",
        ),
    ],
    ids=[
        "partition-conservation",
        "share-sum",
        "ratio-reconstruction",
        "additive-decomposition",
    ],
)
def test_invalid_aggregate_cannot_pass_when_both_results_are_identical(
    rows: list[list[Any]],
    columns: dict[str, str],
    check: dict[str, Any],
    expected_code: str,
) -> None:
    aggregate = result(rows, **columns)
    tolerance = ResultTolerance.model_validate(
        {"business_key": ["region"], "aggregate_checks": [check]}
    )

    report = compare_results(aggregate, aggregate, tolerance)

    assert report.outcome is ComparisonOutcome.INSUFFICIENT
    assert codes(report) == [expected_code]


def test_valid_aggregate_checks_allow_equivalence() -> None:
    aggregate = result(
        [
            ["north", "40", "100", "0.4", "20", "0.5", "7", "4", "3"],
            ["south", "60", "100", "0.6", "30", "0.5", "9", "5", "4"],
        ],
        region="string",
        amount="decimal",
        partition_total="decimal",
        share="decimal",
        numerator="decimal",
        ratio="decimal",
        delta="decimal",
        price_effect="decimal",
        volume_effect="decimal",
    )
    tolerance = ResultTolerance.model_validate(
        {
            "business_key": ["region"],
            "aggregate_checks": [
                {
                    "kind": "partition_conservation",
                    "value_column": "amount",
                    "total_column": "partition_total",
                },
                {"kind": "share_sum", "share_column": "share"},
                {
                    "kind": "ratio_reconstruction",
                    "ratio_column": "ratio",
                    "numerator_column": "numerator",
                    "denominator_column": "amount",
                },
                {
                    "kind": "additive_decomposition",
                    "total_column": "delta",
                    "component_columns": ["price_effect", "volume_effect"],
                },
            ],
        }
    )

    assert compare_results(aggregate, aggregate, tolerance).outcome is ComparisonOutcome.EQUIVALENT


def test_aggregate_check_catches_drift_hidden_by_row_tolerance() -> None:
    base = result([["north", "0.50"], ["south", "0.50"]], region="string", share="float")
    candidate = result([["north", "0.51"], ["south", "0.51"]], region="string", share="float")
    tolerance = ResultTolerance.model_validate(
        {
            "business_key": ["region"],
            "float_absolute": "0.02",
            "aggregate_checks": [
                {
                    "kind": "share_sum",
                    "share_column": "share",
                    "absolute_tolerance": "0",
                }
            ],
        }
    )

    report = compare_results(base, candidate, tolerance)

    assert report.outcome is ComparisonOutcome.DIFFERENT
    assert codes(report) == ["aggregate.share_sum.candidate_failed"]


def test_aggregate_check_with_unknown_column_is_insufficient() -> None:
    aggregate = result([["north", "1"]], region="string", share="decimal")
    tolerance = ResultTolerance.model_validate(
        {
            "business_key": ["region"],
            "aggregate_checks": [{"kind": "share_sum", "share_column": "missing"}],
        }
    )

    report = compare_results(aggregate, aggregate, tolerance)

    assert report.outcome is ComparisonOutcome.INSUFFICIENT
    assert codes(report) == ["aggregate.share_sum.base_insufficient"]


def test_aggregate_check_refuses_non_finite_numbers() -> None:
    aggregate = result([["north", "NaN"]], region="string", share="decimal")
    tolerance = ResultTolerance.model_validate(
        {
            "business_key": ["region"],
            "aggregate_checks": [{"kind": "share_sum", "share_column": "share"}],
        }
    )

    report = compare_results(aggregate, aggregate, tolerance)

    assert report.outcome is ComparisonOutcome.INSUFFICIENT
    assert codes(report) == ["aggregate.share_sum.base_insufficient"]


def test_ratio_check_rejects_irrelevant_grouping_contract() -> None:
    with pytest.raises(ValidationError, match="group_by"):
        ResultTolerance.model_validate(
            {
                "business_key": ["region"],
                "aggregate_checks": [
                    {
                        "kind": "ratio_reconstruction",
                        "ratio_column": "ratio",
                        "numerator_column": "numerator",
                        "denominator_column": "denominator",
                        "group_by": ["region"],
                    }
                ],
            }
        )


def test_row_width_must_match_the_schema() -> None:
    with pytest.raises(ValueError, match="columns"):
        ResultSet(
            columns=[ColumnSchema(name="region", type=ColumnType.STRING)],
            rows=[["north", "extra"]],
        )


def test_report_round_trips() -> None:
    base = result([["north", "0.4"]])
    report = compare_results(base, result([["north", "0.5"]]), KEY)
    assert type(report).model_validate(report.model_dump(mode="json")) == report
