"""Numeric key identity keeps each result's written keys in reconciliation details."""

import pytest

from auto_bi.verification.reconcile import (
    ComparisonReport,
    PartitionConservationCheck,
    ResultSet,
    ResultTolerance,
    ShareSumCheck,
    compare_results,
)


def _result(columns: list[tuple[str, str]], rows: list[list[str | None]]) -> ResultSet:
    return ResultSet.model_validate(
        {"columns": [{"name": name, "type": kind} for name, kind in columns], "rows": rows}
    )


def _rates(rows: list[list[str | None]], kind: str = "decimal") -> ResultSet:
    return _result([("rate", kind), ("amount", "integer")], rows)


def _assert_report(
    report: ComparisonReport,
    outcome: str,
    compared_rows: int,
    differences: list[tuple[str, str]],
) -> None:
    assert report.outcome == outcome
    assert report.compared_rows == compared_rows
    assert [(item.code, item.detail) for item in report.differences] == differences


@pytest.mark.parametrize(
    ("before", "after", "kind"),
    [
        ("0.4120", "0.412", "decimal"),
        ("7", "07", "integer"),
        ("+0.4120", "4.12e-1", "decimal"),
        ("-0.00", "0", "decimal"),
        ("123456789012345678901234567890.1", "123456789012345678901234567890.10", "decimal"),
    ],
    ids=["decimal-scale", "integer-leading-zero", "exponent", "signed-zero", "exact-precision"],
)
def test_numeric_key_matches_by_number(before: str, after: str, kind: str) -> None:
    report = compare_results(
        _rates([[before, "7"]], kind),
        _rates([[after, "7"]], kind),
        ResultTolerance(business_key=["rate"]),
    )
    _assert_report(report, "equivalent", 1, [])


def test_changed_value_under_rescaled_key_names_expected_written_key() -> None:
    report = compare_results(
        _rates([["0.4120", "7"]]),
        _rates([["0.412", "8"]]),
        ResultTolerance(business_key=["rate"]),
    )
    _assert_report(report, "different", 1, [("value.changed", "0.4120.amount: '7' -> '8'")])


@pytest.mark.parametrize("side", ["candidate", "expected", "both"])
def test_numeric_duplicate_names_repeating_rows_written_key(side: str) -> None:
    unique = _rates([["0.4120", "7"]])
    duplicate = _rates([["0.4120", "7"], ["0.412", "8"]])
    report = compare_results(
        duplicate if side in ("expected", "both") else unique,
        duplicate if side in ("candidate", "both") else unique,
        ResultTolerance(business_key=["rate"]),
    )
    _assert_report(
        report,
        "insufficient",
        0,
        [("key.not_unique", "business key does not identify a row: 0.412 appears more than once")],
    )


@pytest.mark.parametrize(
    ("before", "after", "kind"),
    [
        ("0.412", "0.4121", "decimal"),
        ("NaN", "nan", "decimal"),
        ("Infinity", "inf", "decimal"),
        ("0.4120", "0.412", "string"),
        ("0.4120", "0.412", "float"),
        (None, "0", "decimal"),
    ],
    ids=["different-numbers", "nan-spelling", "infinity-spelling", "string", "float", "null-zero"],
)
def test_distinct_key_control_keeps_written_details(
    before: str | None, after: str, kind: str
) -> None:
    report = compare_results(
        _rates([[before, "7"]], kind),
        _rates([[after, "7"]], kind),
        ResultTolerance(business_key=["rate"], float_absolute="0.1"),
    )
    _assert_report(
        report,
        "different",
        0,
        [
            ("row.missing", f"row {before if before is not None else 'NULL'} is gone"),
            ("row.added", f"new row {after}"),
        ],
    )


@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "n/a", None, "0.4120"])
def test_identical_written_key_control_is_accepted(value: str | None) -> None:
    report = compare_results(
        _rates([[value, "7"]]),
        _rates([[value, "7"]]),
        ResultTolerance(business_key=["rate"]),
    )
    _assert_report(report, "equivalent", 1, [])


def test_rescaled_composite_key_matches_by_name_after_column_permutation() -> None:
    report = compare_results(
        _result(
            [("region", "string"), ("rate", "decimal"), ("amount", "integer")],
            [["east", "0.4120", "7"]],
        ),
        _result(
            [("amount", "integer"), ("rate", "decimal"), ("region", "string")],
            [["7", "0.412", "east"]],
        ),
        ResultTolerance(business_key=["region", "rate"]),
    )
    _assert_report(report, "equivalent", 1, [])


@pytest.mark.parametrize("reversed_rows", [False, True], ids=["same", "reversed"])
def test_ordered_numeric_keys_use_identity_and_render_each_written_order(
    reversed_rows: bool,
) -> None:
    rows = [["0.412", "7"], ["0.50", "8"]]
    if reversed_rows:
        rows.reverse()
    report = compare_results(
        _rates([["0.4120", "7"], ["0.5", "8"]]),
        _rates(rows),
        ResultTolerance(business_key=["rate"], ordered=True),
    )
    _assert_report(
        report,
        "different" if reversed_rows else "equivalent",
        2,
        (
            [
                (
                    "row.order_changed",
                    "row order changed: [('0.4120',), ('0.5',)] -> [('0.50',), ('0.412',)]",
                )
            ]
            if reversed_rows
            else []
        ),
    )


@pytest.mark.parametrize("kind", ["share_sum", "partition_conservation"])
@pytest.mark.parametrize("band_type", ["decimal", "integer"])
def test_numeric_group_combines_written_forms(kind: str, band_type: str) -> None:
    band, other_band = ("0.5", "0.50") if band_type == "decimal" else ("7", "07")
    base = _result(
        [("id", "string"), ("band", band_type), ("share", "decimal"), ("total", "integer")],
        [["a", band, "0.6", "1"], ["b", other_band, "0.4", "1"]],
    )
    candidate = _result(
        [("total", "integer"), ("share", "decimal"), ("band", band_type), ("id", "string")],
        [["1", "0.6", other_band, "a"], ["1", "0.4", band, "b"]],
    )
    check = (
        ShareSumCheck(share_column="share", group_by=["band"])
        if kind == "share_sum"
        else PartitionConservationCheck(
            value_column="share", total_column="total", group_by=["band"]
        )
    )
    report = compare_results(
        base, candidate, ResultTolerance(business_key=["id"], aggregate_checks=[check])
    )
    _assert_report(report, "equivalent", 2, [])


@pytest.mark.parametrize("kind", ["share_sum", "partition_conservation"])
@pytest.mark.parametrize("side", ["base", "candidate"])
def test_broken_numeric_group_names_its_first_rows_written_key(kind: str, side: str) -> None:
    columns = [("id", "string"), ("band", "decimal"), ("share", "decimal"), ("total", "integer")]
    base = _result(columns, [["a", "0.50", "0.6", "1"], ["b", "0.5", "0.4", "1"]])
    candidate = _result(columns, [["a", "0.500", "0.6", "1"], ["b", "0.5", "0.4", "1"]])
    (base if side == "base" else candidate).rows[1][2] = "0.3"
    check = (
        ShareSumCheck(share_column="share", group_by=["band"])
        if kind == "share_sum"
        else PartitionConservationCheck(
            value_column="share", total_column="total", group_by=["band"]
        )
    )
    report = compare_results(
        base, candidate, ResultTolerance(business_key=["id"], aggregate_checks=[check])
    )
    written = "0.50" if side == "base" else "0.500"
    differences = [] if side == "base" else [("value.changed", "b.share: '0.4' -> '0.3'")]
    differences.append(
        (
            f"aggregate.{kind}.{side}_failed",
            f"{side} {kind}: group {written} sums to 0.9, expected 1",
        )
    )
    _assert_report(
        report,
        "insufficient" if side == "base" else "different",
        0 if side == "base" else 2,
        differences,
    )


def test_conflicting_group_totals_render_without_decimal_repr() -> None:
    result = _result(
        [("id", "string"), ("band", "decimal"), ("share", "decimal"), ("total", "integer")],
        [["a", "0.50", "0.6", "1"], ["b", "0.5", "0.4", "2"]],
    )
    report = compare_results(
        result,
        result,
        ResultTolerance(
            business_key=["id"],
            aggregate_checks=[
                PartitionConservationCheck(
                    value_column="share", total_column="total", group_by=["band"]
                )
            ],
        ),
    )
    _assert_report(
        report,
        "insufficient",
        0,
        [
            (
                "aggregate.partition_conservation.base_failed",
                "base partition_conservation: group 0.50 declares conflicting totals [1, 2]",
            )
        ],
    )


@pytest.mark.parametrize("changed", [False, True], ids=["equivalent", "different"])
def test_numeric_key_comparison_leaves_supplied_results_unchanged(changed: bool) -> None:
    base = _rates([["0.4120", "7"]])
    candidate = _result(
        [("amount", "integer"), ("rate", "decimal")], [["8" if changed else "7", "0.412"]]
    )
    before = (base.model_dump_json(), candidate.model_dump_json())
    report = compare_results(base, candidate, ResultTolerance(business_key=["rate"]))
    assert (base.model_dump_json(), candidate.model_dump_json()) == before
    _assert_report(
        report,
        "different" if changed else "equivalent",
        1,
        [("value.changed", "0.4120.amount: '7' -> '8'")] if changed else [],
    )
