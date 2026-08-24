"""Cross-contract validation of Git-tracked decision products (bootstrap task 3).

`decision validate` answers one question offline: do the contracts in the working tree
agree with each other? A pinned metric ref that resolves to nothing, to a different domain
version, or to a retired metric is exactly the drift that makes a later "pass" meaningless
— so it is caught here, before any DWH, BI or LLM is involved.

Severity is a property of the finding, not of the reader: unresolvable refs block, a
lifecycle or policy question needs review, a deprecation is advisory.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from auto_bi.decision.validate import ValidationReport, validate_paths
from auto_bi.verification.model import Severity, Verdict

FIXTURES = Path(__file__).parent / "fixtures" / "decision"

Edit = Callable[[dict[str, Any]], None]


def write_contracts(
    tmp_path: Path, *, product: Edit | None = None, metric: Edit | None = None
) -> Path:
    """Copy the good fixtures into *tmp_path*, applying optional edits."""
    product_data = yaml.safe_load((FIXTURES / "weekly_margin.yaml").read_text(encoding="utf-8"))
    metric_data = yaml.safe_load((FIXTURES / "gross_margin_pct.yaml").read_text(encoding="utf-8"))
    if product is not None:
        product(product_data)
    if metric is not None:
        metric(metric_data)
    for name, data in (
        ("weekly_margin.yaml", product_data),
        ("gross_margin_pct.yaml", metric_data),
    ):
        (tmp_path / name).write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return tmp_path


def codes(report: ValidationReport) -> list[str]:
    return [issue.code for issue in report.issues]


def test_valid_contract_set_passes(tmp_path: Path) -> None:
    report = validate_paths([write_contracts(tmp_path)])
    assert report.issues == []
    assert report.verdict is Verdict.PASS
    assert report.products == ["decision://sales/weekly_margin@1"]
    assert report.metrics == ["metric://sales/gross_margin_pct@3"]


def test_fixture_directory_is_a_valid_set() -> None:
    """The committed fixtures are the reference example — they must stay consistent."""
    assert validate_paths([FIXTURES]).verdict is Verdict.PASS


def test_single_file_path_is_accepted(tmp_path: Path) -> None:
    root = write_contracts(tmp_path)
    report = validate_paths([root / "weekly_margin.yaml", root / "gross_margin_pct.yaml"])
    assert report.verdict is Verdict.PASS


def test_unknown_metric_ref_blocks(tmp_path: Path) -> None:
    def repoint(data: dict[str, Any]) -> None:
        data["scope"]["metrics"] = ["metric://sales/net_margin_pct@1"]

    report = validate_paths([write_contracts(tmp_path, product=repoint)])
    assert codes(report) == ["metric_ref.unknown"]
    assert report.issues[0].severity is Severity.BLOCK
    assert report.issues[0].subject == "metric://sales/net_margin_pct@1"
    assert report.verdict is Verdict.BLOCK


def test_version_mismatch_blocks(tmp_path: Path) -> None:
    """The metric exists but has moved on: the product is pinned to a version that is gone."""

    def bump(data: dict[str, Any]) -> None:
        data["metadata"]["version"] = 4

    report = validate_paths([write_contracts(tmp_path, metric=bump)])
    assert codes(report) == ["metric_ref.version_mismatch"]
    assert report.issues[0].subject == "metric://sales/gross_margin_pct@3"
    assert "4" in report.issues[0].message
    assert report.verdict is Verdict.BLOCK


def test_duplicate_metric_ref_blocks(tmp_path: Path) -> None:
    def duplicate(data: dict[str, Any]) -> None:
        data["scope"]["metrics"] = ["metric://sales/gross_margin_pct@3"] * 2

    report = validate_paths([write_contracts(tmp_path, product=duplicate)])
    assert codes(report) == ["metric_ref.duplicate"]
    assert report.verdict is Verdict.BLOCK


def test_dimension_outside_the_metric_contract_blocks(tmp_path: Path) -> None:
    def widen(data: dict[str, Any]) -> None:
        data["scope"]["dimensions"] = ["region", "channel"]

    report = validate_paths([write_contracts(tmp_path, product=widen)])
    assert codes(report) == ["dimension.not_allowed"]
    assert "channel" in report.issues[0].message
    assert report.verdict is Verdict.BLOCK


@pytest.mark.parametrize(
    ("status", "code", "severity", "verdict"),
    [
        ("retired", "metric.retired", Severity.BLOCK, Verdict.BLOCK),
        ("draft", "metric.draft", Severity.REVIEW, Verdict.NEEDS_REVIEW),
        ("deprecated", "metric.deprecated", Severity.ADVISORY, Verdict.PASS_WITH_ADVISORY),
    ],
)
def test_metric_lifecycle_is_graded(
    tmp_path: Path, status: str, code: str, severity: Severity, verdict: Verdict
) -> None:
    def relabel(data: dict[str, Any]) -> None:
        data["metadata"]["status"] = status

    report = validate_paths([write_contracts(tmp_path, metric=relabel)])
    assert codes(report) == [code]
    assert report.issues[0].severity is severity
    assert report.verdict is verdict


def test_policy_mismatch_needs_review(tmp_path: Path) -> None:
    """The metric is governed by a different policy than the product verifies under."""

    def regovern(data: dict[str, Any]) -> None:
        data["policy_refs"] = ["policy://release/strict@2"]

    report = validate_paths([write_contracts(tmp_path, metric=regovern)])
    assert codes(report) == ["policy.mismatch"]
    assert report.issues[0].severity is Severity.REVIEW
    assert report.verdict is Verdict.NEEDS_REVIEW


def test_unparsable_yaml_blocks(tmp_path: Path) -> None:
    root = write_contracts(tmp_path)
    (root / "broken.yaml").write_text("kind: [unclosed\n", encoding="utf-8")
    report = validate_paths([root])
    assert codes(report) == ["contract.unreadable"]
    assert report.issues[0].source.endswith("broken.yaml")
    assert report.verdict is Verdict.BLOCK


def test_unknown_kind_blocks(tmp_path: Path) -> None:
    root = write_contracts(tmp_path)
    (root / "other.yaml").write_text("kind: DashboardSpec\n", encoding="utf-8")
    report = validate_paths([root])
    assert codes(report) == ["contract.unknown_kind"]
    assert report.verdict is Verdict.BLOCK


def test_schema_violation_blocks_and_names_the_field(tmp_path: Path) -> None:
    def typo(data: dict[str, Any]) -> None:
        data["schedule"] = {"cron": "0 9 * * 1"}

    report = validate_paths([write_contracts(tmp_path, product=typo)])
    assert codes(report) == ["contract.invalid"]
    assert "schedule" in report.issues[0].message
    assert report.verdict is Verdict.BLOCK


def test_duplicate_contract_id_blocks(tmp_path: Path) -> None:
    root = write_contracts(tmp_path)
    (root / "copy.yaml").write_text(
        (root / "gross_margin_pct.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    report = validate_paths([root])
    assert codes(report) == ["contract.duplicate_id"]
    assert report.verdict is Verdict.BLOCK


def test_missing_path_is_reported_not_raised(tmp_path: Path) -> None:
    report = validate_paths([tmp_path / "nope"])
    assert codes(report) == ["contract.unreadable"]
    assert report.verdict is Verdict.BLOCK


def test_issue_order_is_deterministic(tmp_path: Path) -> None:
    """CI diffs the report; the same tree must always produce the same sequence."""

    def widen(data: dict[str, Any]) -> None:
        data["scope"]["dimensions"] = ["zeta", "alpha"]
        data["scope"]["metrics"] = ["metric://sales/gross_margin_pct@3"] * 2

    def relabel(data: dict[str, Any]) -> None:
        data["metadata"]["status"] = "deprecated"

    root = write_contracts(tmp_path, product=widen, metric=relabel)
    first = validate_paths([root])
    second = validate_paths([root])
    assert codes(first) == codes(second)
    assert codes(first) == [
        "metric_ref.duplicate",
        "dimension.not_allowed",
        "dimension.not_allowed",
        "metric.deprecated",
    ]
    assert [issue.subject for issue in first.issues][1:3] == ["alpha", "zeta"]


def test_report_round_trips_as_a_strict_model(tmp_path: Path) -> None:
    report = validate_paths([write_contracts(tmp_path)])
    again = ValidationReport.model_validate(report.model_dump(mode="json"))
    assert again == report


def test_validation_never_opens_a_socket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Offline by construction: no DWH, BI or LLM is reachable from this path."""
    import socket

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("decision validate must not open a network connection")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    assert validate_paths([write_contracts(tmp_path)]).verdict is Verdict.PASS
