"""The run of a decision product its schema refuses says which product and why.

Durable spec: docs/specs/schema-refused-products.md. A product file that fails its schema is a
`contract.invalid` issue of the report and no product of the paths, so `decision verify` refused
its run as the run of a product that is not there — `no decision product …` — and only
`decision validate` said why. The refusal now names the decision products the schema refused:
under `--product`, those that declare the id asked for; without it, every one.
"""

from pathlib import Path
from typing import Any

import pytest

from auto_bi.cli import main
from auto_bi.decision import use_cases
from auto_bi.decision.validate import LoadedContracts, ValidationReport, validate_paths
from auto_bi.verification.model import Verdict
from auto_bi.verification.release_gate import CHECK_VERSION, VERIFIER_VERSION
from tests.test_decision_cli import _verify_argv
from tests.test_decision_verify import (
    PRODUCT_ID,
    Case,
    _write_yaml,
    metric_contract,
    product_contract,
)
from tests.test_unlisted_paths import _deny_listing
from tests.test_unreadable_run_inputs import _refused

DASHBOARD = "dashboard://weekly_margin@7"
METRIC = "metric://sales/gross_margin_pct@3"
OTHER_ID = "decision://sales/other_margin"
NO_PRODUCT = "no decision product contract found in the given paths"
FAILED = "decision product(s) failed the schema"
REPEATED = f"delivery: Value error, duplicate entries in delivery.dashboards: {DASHBOARD}"
PINNED_TWICE = f"scope: Value error, duplicate entries in scope.metrics: {METRIC}"
FORMAT_TWICE = "delivery: Value error, duplicate entries in delivery.brief_formats: markdown"


def _product(identifier: str = PRODUCT_ID) -> dict[str, Any]:
    product = product_contract()
    product["metadata"]["id"] = identifier
    return product


def _delivering_twice(identifier: str = PRODUCT_ID) -> dict[str, Any]:
    product = _product(identifier)
    product["delivery"]["dashboards"] = [{"ref": DASHBOARD}, {"ref": DASHBOARD}]
    return product


def _pinning_twice(identifier: str) -> dict[str, Any]:
    product = _product(identifier)
    product["scope"]["metrics"] = [METRIC, METRIC]
    return product


def _listing_a_format_twice(identifier: str = PRODUCT_ID) -> dict[str, Any]:
    product = _product(identifier)
    product["delivery"]["brief_formats"] = ["markdown", "markdown"]
    return product


def _only(case: Case, name: str, contract: dict[str, Any]) -> Path:
    """Replace the product of *case* with *contract*, written to *name*; its path."""
    (case.contracts / "weekly_margin.yaml").unlink()
    path = case.contracts / name
    _write_yaml(path, contract)
    return path


# --- the product asked for, and no product asked for ---------------------------------------


def test_the_refusal_names_the_product_and_what_its_schema_refused(tmp_path: Path) -> None:
    case = Case(tmp_path)
    path = case.contracts / "weekly_margin.yaml"
    _write_yaml(path, _delivering_twice())
    named = f"1 {FAILED}: '{PRODUCT_ID}' in $1/{path.name} ({REPEATED})"

    assert _refused(case, product_id=PRODUCT_ID) == (
        f"no decision product '{PRODUCT_ID}' in the given paths (none); {named}"
    )
    assert _refused(case) == f"{NO_PRODUCT}; {named}"


def test_the_command_says_it_in_one_line_with_exit_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = Case(tmp_path).with_candidate()
    path = case.contracts / "weekly_margin.yaml"
    _write_yaml(path, _delivering_twice())

    for extra in ([], ["--product", PRODUCT_ID]):
        assert main(_verify_argv(case, *extra)) == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.startswith("error: no decision product ")
        assert captured.err.endswith(
            f"; 1 {FAILED}: '{PRODUCT_ID}' in $1/{path.name} ({REPEATED})\n"
        )
        assert captured.err.count("\n") == 1
        assert "Traceback" not in captured.err
        assert not (tmp_path / "evidence").exists()


def test_only_the_product_asked_for_is_named(tmp_path: Path) -> None:
    case = Case(tmp_path)
    path = case.contracts / "other_margin.yaml"
    _write_yaml(path, _pinning_twice(OTHER_ID))

    assert _refused(case, product_id=OTHER_ID) == (
        f"no decision product '{OTHER_ID}' in the given paths ({PRODUCT_ID}); "
        f"1 {FAILED}: '{OTHER_ID}' in $1/{path.name} ({PINNED_TWICE})"
    )
    # The refused product declares another id than the one asked for.
    assert _refused(case, product_id="decision://sales/missing") == (
        f"no decision product 'decision://sales/missing' in the given paths ({PRODUCT_ID})"
    )


def test_two_files_of_the_id_asked_for_are_both_named_in_the_order_they_were_read(
    tmp_path: Path,
) -> None:
    case = Case(tmp_path)
    first = _only(case, "a_weekly_margin.yaml", _listing_a_format_twice())
    second = case.contracts / "b_weekly_margin.yaml"
    _write_yaml(second, _delivering_twice())

    assert _refused(case, product_id=PRODUCT_ID) == (
        f"no decision product '{PRODUCT_ID}' in the given paths (none); "
        f"2 {FAILED}: '{PRODUCT_ID}' in $1/{first.name} ({FORMAT_TWICE}), "
        f"'{PRODUCT_ID}' in $1/{second.name} ({REPEATED})"
    )


def test_products_of_other_ids_are_left_out_before_three_are_taken(tmp_path: Path) -> None:
    case = Case(tmp_path)
    (case.contracts / "weekly_margin.yaml").unlink()
    # Refusals of other ids are read first: a limit taken before the filter names none of these.
    for index in range(3):
        _write_yaml(case.contracts / f"a{index}.yaml", _pinning_twice(f"decision://sales/o{index}"))
    paths = []
    for index in range(4):
        path = case.contracts / f"b{index}.yaml"
        _write_yaml(path, _delivering_twice())
        paths.append(path)
    named = ", ".join(f"'{PRODUCT_ID}' in $1/{path.name} ({REPEATED})" for path in paths[:3])

    assert _refused(case, product_id=PRODUCT_ID) == (
        f"no decision product '{PRODUCT_ID}' in the given paths (none); "
        f"4 {FAILED}: {named} (+1 more)"
    )


def test_with_no_product_asked_for_the_first_three_are_named_in_the_order_they_were_read(
    tmp_path: Path,
) -> None:
    case = Case(tmp_path)
    (case.contracts / "weekly_margin.yaml").unlink()
    # The ids run against the order of the files, so an order by id is not the order read.
    paths = []
    for index in range(5):
        path = case.contracts / f"p{index}.yaml"
        _write_yaml(path, _pinning_twice(f"decision://sales/p{4 - index}"))
        paths.append(path)
    named = ", ".join(
        f"'decision://sales/p{4 - index}' in $1/{path.name} ({PINNED_TWICE})"
        for index, path in enumerate(paths[:3])
    )

    assert _refused(case) == f"{NO_PRODUCT}; 5 {FAILED}: {named} (+2 more)"

    # The order is the report's: decision validate gives the issues of these files in it.
    issues = validate_paths([case.contracts]).issues
    assert [issue.source for issue in issues if issue.code == "contract.invalid"] == [
        f"$1/{path.name}" for path in paths
    ]

    for path in paths[3:]:
        path.unlink()
    assert _refused(case) == f"{NO_PRODUCT}; 3 {FAILED}: {named}"


def test_a_file_that_declares_no_id_is_named_by_its_file(tmp_path: Path) -> None:
    case = Case(tmp_path)
    product = _product()
    del product["metadata"]
    path = _only(case, "weekly_margin.yaml", product)

    assert _refused(case) == f"{NO_PRODUCT}; 1 {FAILED}: $1/{path.name} (metadata: Field required)"

    # decision validate names it by its file's name; that is no id a run may ask for.
    (issue,) = validate_paths([case.contracts]).issues
    assert issue.code == "contract.invalid"
    assert issue.subject == "weekly_margin.yaml"
    assert _refused(case, product_id="weekly_margin.yaml") == (
        "no decision product 'weekly_margin.yaml' in the given paths (none)"
    )


def test_an_id_that_is_no_text_is_compared_as_the_report_spells_it(tmp_path: Path) -> None:
    case = Case(tmp_path)
    path = _only(case, "weekly_margin.yaml", _product())
    product = _product()
    product["metadata"]["id"] = 7
    _write_yaml(path, product)

    (issue,) = validate_paths([case.contracts]).issues
    assert issue.subject == "7"
    assert _refused(case, product_id="7").startswith(
        f"no decision product '7' in the given paths (none); 1 {FAILED}: '7' in $1/{path.name} ("
    )


# --- one line, whatever the file holds -----------------------------------------------------


def test_a_reason_or_an_id_with_a_line_break_is_said_in_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = Case(tmp_path).with_candidate()
    product = _product("decision://sales/weekly\nmargin")
    product["bad\nkey"] = 1
    path = _only(case, "weekly_margin.yaml", product)

    # What decision validate keeps of it spans lines; the usage error does not.
    (issue,) = validate_paths([case.contracts]).issues
    assert "\n" in issue.subject
    assert "\n" in issue.message

    assert main(_verify_argv(case)) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("\n") == 1
    assert captured.err.startswith(
        f"error: {NO_PRODUCT}; 1 {FAILED}: 'decision://sales/weekly\\nmargin' in $1/{path.name} ("
    )
    assert "bad key: Extra inputs are not permitted" in captured.err


def test_the_reason_is_said_as_every_schema_refusal_is_said(tmp_path: Path) -> None:
    """A boundary: whitespace is one space, and any other character is left as it is."""
    case = Case(tmp_path)
    path = case.contracts / "weekly_margin.yaml"
    path.write_text(path.read_text(encoding="utf-8") + '"bad\\ekey\\tthere": 1\n', encoding="utf-8")

    assert _refused(case) == (
        f"{NO_PRODUCT}; 1 {FAILED}: '{PRODUCT_ID}' in $1/{path.name} "
        "(bad\x1bkey there: Extra inputs are not permitted)"
    )


def test_a_file_whose_path_holds_a_control_character_is_quoted() -> None:
    """No file of a Windows path holds one, so the refusal is asked of what was loaded."""
    from auto_bi.decision.validate import RefusedProduct

    loaded = LoadedContracts(
        products={},
        metrics={},
        report=ValidationReport(verdict=Verdict.PASS),
        refused_products=(
            RefusedProduct(declared=PRODUCT_ID, source="contracts/weekly\nmargin.yaml", reason="r"),
            RefusedProduct(declared=None, source="contracts/no\tid.yaml", reason="s"),
        ),
    )
    with pytest.raises(use_cases.VerifyError) as caught:
        use_cases._select_product(loaded, "")
    assert str(caught.value) == (
        f"{NO_PRODUCT}; 2 {FAILED}: '{PRODUCT_ID}' in 'contracts/weekly\\nmargin.yaml' (r), "
        "'contracts/no\\tid.yaml' (s)"
    )


# --- what is not named ---------------------------------------------------------------------


def test_a_contract_of_another_kind_is_not_named_whatever_id_it_declares(tmp_path: Path) -> None:
    case = Case(tmp_path)
    (case.contracts / "weekly_margin.yaml").unlink()
    metric = metric_contract()
    metric["metadata"]["id"] = PRODUCT_ID
    _write_yaml(case.contracts / "gross_margin_pct.yaml", metric)

    # decision validate gives that metric contract an issue of the id asked for.
    issues = validate_paths([case.contracts]).issues
    assert [(issue.code, issue.subject) for issue in issues] == [("contract.invalid", PRODUCT_ID)]

    assert _refused(case) == NO_PRODUCT
    assert _refused(case, product_id=PRODUCT_ID) == (
        f"no decision product '{PRODUCT_ID}' in the given paths (none)"
    )


def test_a_file_that_cannot_be_read_is_named_as_one_and_not_as_refused(tmp_path: Path) -> None:
    case = Case(tmp_path)
    path = _only(case, "weekly_margin.yaml", _product())
    path.write_text("kind: DecisionProduct\nmetadata: [unclosed\n", encoding="utf-8")

    assert _refused(case) == (
        f"{NO_PRODUCT}; 1 path(s) could not be read: $1/{path.name} "
        "(cannot read contract: ParserError)"
    )

    path.write_text("kind: Other\n", encoding="utf-8")
    assert _refused(case) == NO_PRODUCT


def test_a_refused_product_comes_before_a_path_that_could_not_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = Case(tmp_path)
    path = case.contracts / "weekly_margin.yaml"
    _write_yaml(path, _delivering_twice())
    archive = case.contracts / "archive"
    archive.mkdir()
    _deny_listing(monkeypatch, archive)

    assert _refused(case) == (
        f"{NO_PRODUCT}; 1 {FAILED}: '{PRODUCT_ID}' in $1/{path.name} ({REPEATED}); "
        f"1 path(s) could not be read: $1/{archive.name} "
        "(cannot list directory: PermissionError)"
    )


def test_two_products_read_still_ask_for_one_without_naming_the_refused(tmp_path: Path) -> None:
    case = Case(tmp_path)
    _write_yaml(case.contracts / "other_margin.yaml", _product(OTHER_ID))
    _write_yaml(case.contracts / "third_margin.yaml", _delivering_twice("decision://sales/third"))

    assert _refused(case) == (
        f"the given paths hold 2 decision products ({OTHER_ID}, {PRODUCT_ID}); "
        "name the one to verify with --product"
    )


def test_a_product_of_the_id_that_validates_is_run_beside_a_refused_one(tmp_path: Path) -> None:
    """Nothing is refused when a product answers the run; the other file blocks it."""
    case = Case(tmp_path).with_candidate()
    _write_yaml(case.contracts / "archive" / "weekly_margin.yaml", _delivering_twice())

    report = case.run(product_id=PRODUCT_ID)

    assert report.verdict is Verdict.BLOCK
    assert (tmp_path / "evidence").exists()


def test_the_versions_of_evidence_are_at_least_those_this_was_written_under() -> None:
    """The refusal is printed and never kept: no report, check or bundle changes with it.

    That this change moved neither version is told by its diff; a later bump is no regression.
    """
    assert int(VERIFIER_VERSION) >= 114
    assert int(CHECK_VERSION) >= 77
