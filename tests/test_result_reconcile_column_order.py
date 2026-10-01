"""Supplied results reconcile named columns; native capture keeps its ordered boundary."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from auto_bi.evidence.manifest import read_bundle
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.verification.reconcile import (
    ComparisonReport,
    ResultSet,
    ResultTolerance,
    compare_results,
)
from auto_bi.verification.release_gate import CheckStatus
from auto_bi.verification.result_probe import (
    QueryColumn,
    QueryResult,
    ResultCaptureError,
    capture,
)
from tests.test_decision_verify import ASSET_ID, Case, status_of, summary_of
from tests.test_result_probe import CANDIDATE_DIGEST, CAPTURED_AT, _asset


def _result(columns: list[tuple[str, str]], rows: list[list[str | None]]) -> ResultSet:
    return ResultSet.model_validate(
        {"columns": [{"name": name, "type": kind} for name, kind in columns], "rows": rows}
    )


def _sales(
    rows: list[list[str | None]], names: tuple[str, ...] = ("id", "revenue", "cost")
) -> ResultSet:
    return _result([(name, "string" if name == "id" else "decimal") for name in names], rows)


def _differences(report: ComparisonReport) -> list[tuple[str, str]]:
    return [(item.code, item.detail) for item in report.differences]


def test_permuted_same_named_values_are_equivalent() -> None:
    report = compare_results(
        _sales([["east", "100", "40"]]),
        _sales([["east", "40", "100"]], ("id", "cost", "revenue")),
        ResultTolerance(business_key=["id"]),
    )
    assert report.outcome == "equivalent"
    assert report.compared_rows == 1
    assert report.differences == []


def test_permuted_changed_named_values_are_different() -> None:
    report = compare_results(
        _sales([["east", "100", "40"]]),
        _sales([["east", "100", "40"]], ("id", "cost", "revenue")),
        ResultTolerance(business_key=["id"]),
    )
    assert report.outcome == "different"
    assert report.compared_rows == 1
    assert _differences(report) == [
        ("value.changed", "east.revenue: '100' -> '40'"),
        ("value.changed", "east.cost: '40' -> '100'"),
    ]


def test_moved_single_key_matches_the_same_row() -> None:
    report = compare_results(
        _sales([["east", "100", "40"]]),
        _sales([["100", "east", "40"]], ("revenue", "id", "cost")),
        ResultTolerance(business_key=["id"]),
    )
    assert report.outcome == "equivalent"
    assert report.compared_rows == 1
    assert report.differences == []


@pytest.mark.parametrize("variant", ["same", "changed", "missing", "added"])
def test_permuted_composite_key_uses_declared_key_order(variant: str) -> None:
    base = _result(
        [("region", "string"), ("day", "date"), ("revenue", "decimal")],
        [["north", "2026-09-01", "100"], ["south", "2026-09-02", "40"]],
    )
    rows: list[list[str | None]] = [
        ["100", "2026-09-01", "north"],
        ["40", "2026-09-02", "south"],
    ]
    expected: list[tuple[str, str]] = []
    if variant == "changed":
        rows[0][0] = "90"
        expected = [("value.changed", "north|2026-09-01.revenue: '100' -> '90'")]
    elif variant == "missing":
        rows.pop(0)
        expected = [("row.missing", "row north|2026-09-01 is gone")]
    elif variant == "added":
        rows.append(["20", "2026-09-03", "west"])
        expected = [("row.added", "new row west|2026-09-03")]
    candidate = _result([("revenue", "decimal"), ("day", "date"), ("region", "string")], rows)
    report = compare_results(base, candidate, ResultTolerance(business_key=["region", "day"]))
    assert report.outcome == ("equivalent" if variant == "same" else "different")
    assert report.compared_rows == (1 if variant == "missing" else 2)
    assert _differences(report) == expected


def test_duplicate_named_key_in_permuted_candidate_is_insufficient() -> None:
    report = compare_results(
        _sales([["east", "100", "40"]]),
        _sales([["100", "east", "40"], ["90", "east", "30"]], ("revenue", "id", "cost")),
        ResultTolerance(business_key=["id"]),
    )
    assert report.outcome == "insufficient"
    assert _differences(report) == [
        ("key.not_unique", "business key does not identify a row: east appears more than once")
    ]


@pytest.mark.parametrize("changed", [False, True], ids=["same", "changed"])
def test_null_keeps_its_named_column(changed: bool) -> None:
    report = compare_results(
        _sales([["east", None, "40"]]),
        _sales(
            [["east", None, "40"] if changed else ["east", "40", None]], ("id", "cost", "revenue")
        ),
        ResultTolerance(business_key=["id"]),
    )
    assert report.outcome == ("different" if changed else "equivalent")
    assert _differences(report) == (
        [
            ("value.null_became_value", "east.revenue: NULL -> '40'"),
            ("value.value_became_null", "east.cost: '40' -> NULL"),
        ]
        if changed
        else []
    )


@pytest.mark.parametrize("value", ["100.05", "100.2"], ids=["within", "outside"])
def test_moved_float_uses_its_own_tolerance(value: str) -> None:
    base = _result(
        [("id", "string"), ("amount", "float"), ("label", "string")], [["east", "100", "retail"]]
    )
    candidate = _result(
        [("id", "string"), ("label", "string"), ("amount", "float")], [["east", "retail", value]]
    )
    report = compare_results(
        base, candidate, ResultTolerance(business_key=["id"], float_absolute="0.1")
    )
    assert report.outcome == ("equivalent" if value == "100.05" else "different")
    assert _differences(report) == (
        [] if value == "100.05" else [("value.changed", "east.amount: '100' -> '100.2'")]
    )


@pytest.mark.parametrize("reversed_rows", [False, True], ids=["same", "reversed"])
def test_ordered_permuted_result_compares_named_keys(reversed_rows: bool) -> None:
    base = _sales([["east", "100", "40"], ["west", "90", "30"]])
    rows = [["100", "east", "40"], ["90", "west", "30"]]
    if reversed_rows:
        rows.reverse()
    report = compare_results(
        base,
        _sales(rows, ("revenue", "id", "cost")),
        ResultTolerance(business_key=["id"], ordered=True),
    )
    assert report.outcome == ("different" if reversed_rows else "equivalent")
    assert _differences(report) == (
        [
            (
                "row.order_changed",
                "row order changed: [('east',), ('west',)] -> [('west',), ('east',)]",
            )
        ]
        if reversed_rows
        else []
    )


@pytest.mark.parametrize("kind", ["ratio_reconstruction", "additive_decomposition"])
@pytest.mark.parametrize("broken", [False, True], ids=["holding", "broken"])
def test_permuted_aggregate_names_candidate_row_by_its_key(kind: str, broken: bool) -> None:
    check: dict[str, Any]
    if kind == "ratio_reconstruction":
        measure, valid, invalid = "ratio", "2.5", "3"
        check = {
            "kind": kind,
            "ratio_column": measure,
            "numerator_column": "revenue",
            "denominator_column": "cost",
        }
        detail = "candidate ratio_reconstruction: row east has ratio 3, reconstructed 2.5"
    else:
        measure, valid, invalid = "total", "140", "150"
        check = {"kind": kind, "total_column": measure, "component_columns": ["revenue", "cost"]}
        detail = "candidate additive_decomposition: row east has total 150, components sum to 140"
    base = _sales([["east", "100", "40", valid]], ("id", "revenue", "cost", measure))
    candidate = _sales(
        [["100", "east", "40", invalid if broken else valid]], ("revenue", "id", "cost", measure)
    )
    tolerance = ResultTolerance.model_validate(
        {"business_key": ["id"], "aggregate_checks": [check]}
    )
    report = compare_results(base, candidate, tolerance)
    assert report.outcome == ("different" if broken else "equivalent")
    aggregate_issues = [item for item in _differences(report) if item[0].startswith("aggregate.")]
    assert aggregate_issues == ([(f"aggregate.{kind}.candidate_failed", detail)] if broken else [])
    if not broken:
        assert report.differences == []
    else:
        assert report.compared_rows == 1


@pytest.mark.parametrize("changed", [False, True], ids=["equivalent", "different"])
def test_comparison_does_not_mutate_either_supplied_result(changed: bool) -> None:
    base = _sales([["east", "100", "40"]])
    candidate = _sales(
        [["east", "100", "40"] if changed else ["east", "40", "100"]], ("id", "cost", "revenue")
    )
    before = (base.model_dump_json(), candidate.model_dump_json())
    report = compare_results(base, candidate, ResultTolerance(business_key=["id"]))
    assert (base.model_dump_json(), candidate.model_dump_json()) == before
    assert report.outcome == ("different" if changed else "equivalent")


@pytest.mark.parametrize("variant", ["added", "removed", "type"])
def test_permuted_schema_differences_stop_before_values(variant: str) -> None:
    columns = [("cost", "decimal"), ("id", "string"), ("revenue", "decimal")]
    row: list[str | None] = ["40", "east", "100"]
    if variant == "added":
        columns.append(("extra", "string"))
        row.append("unused")
        expected = [("schema.column_added", "new column 'extra'")]
    elif variant == "removed":
        columns.pop()
        row.pop()
        expected = [("schema.column_removed", "column 'revenue' is gone")]
    else:
        columns[0] = ("cost", "string")
        expected = [("schema.type_changed", "cost: decimal -> string")]
    report = compare_results(
        _sales([["east", "100", "40"]]),
        _result(columns, [row]),
        ResultTolerance(business_key=["id"]),
    )
    assert report.outcome == "different"
    assert report.compared_rows == 0
    assert _differences(report) == expected


def test_truncated_permuted_candidate_is_insufficient() -> None:
    candidate = _sales([["east", "40", "100"]], ("id", "cost", "revenue"))
    candidate.truncated = True
    report = compare_results(
        _sales([["east", "100", "40"]]), candidate, ResultTolerance(business_key=["id"])
    )
    assert report.outcome == "insufficient"
    assert report.compared_rows == 0
    assert [item.code for item in report.differences] == ["result.truncated"]


@pytest.mark.parametrize("changed", [False, True], ids=["unchanged", "changed"])
def test_same_order_control(changed: bool) -> None:
    report = compare_results(
        _sales([["east", "100", "40"]]),
        _sales([["east", "90" if changed else "100", "40"]]),
        ResultTolerance(business_key=["id"]),
    )
    assert report.outcome == ("different" if changed else "equivalent")
    assert report.compared_rows == 1
    assert _differences(report) == (
        [("value.changed", "east.revenue: '100' -> '90'")] if changed else []
    )


@pytest.mark.parametrize("named_values", [True, False], ids=["named-values", "positional-values"])
def test_gate_permuted_attested_results_seal_only_named_equivalence(
    tmp_path: Path, named_values: bool
) -> None:
    rows = (
        [["0.412", "Москва"], ["0.377", "Казань"]]
        if named_values
        else [["Москва", "0.412"], ["Казань", "0.377"]]
    )
    case = Case(tmp_path).with_asset().with_results(rows)
    assert case.results is not None
    payload = yaml.safe_load(case.results.read_text(encoding="utf-8"))
    payload[ASSET_ID]["result"]["columns"].reverse()
    case.results.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    report = case.run()
    bundle = read_bundle(FilesystemArtifactStore(tmp_path / "evidence"), report.run_id)
    claims = [claim for claim in bundle.claims if claim.summary_code == "result.equivalent"]
    if named_values:
        assert status_of(report, "results.reconcile") is CheckStatus.PASS
        assert len(claims) == 1
        assert claims[0].evidence_refs == ["check-results.reconcile", "check-results.provenance"]
    else:
        assert status_of(report, "results.reconcile") is CheckStatus.BLOCK
        assert summary_of(report, "results.reconcile") == "result.not_equivalent"
        assert claims == []


def test_native_capture_still_refuses_permuted_query_columns() -> None:
    with pytest.raises(
        ResultCaptureError, match=r"expected column 'region' at position 1, got 'gross_margin_pct'"
    ):
        capture(
            _asset(),
            lambda _sql: QueryResult(
                columns=(
                    QueryColumn("gross_margin_pct", "Decimal(18, 6)"),
                    QueryColumn("region", "String"),
                ),
                rows=(),
            ),
            candidate_digest=CANDIDATE_DIGEST,
            environment_ref="clickhouse-stage:8123/dm",
            executed_by="gate_ro",
            captured_at=CAPTURED_AT,
        )
