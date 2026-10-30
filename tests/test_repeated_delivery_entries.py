"""A dashboard ref or a brief format repeated in a product's delivery, from schema to `verify`.

Durable spec: docs/specs/repeated-delivery-entries.md. `DeliverySpec` took its lists as they
were written. A product delivering one dashboard ref twice passed `decision validate`; the gate
compiled each entry, and when the spec compiled, `EvidenceBundle` refused the second payload of
one id: the run ended in a traceback with exit 1 and no bundle. Without a spec, or with one that
did not validate or compile, it wrote a bundle naming the dashboard twice. Both lists are sets
by meaning, as the lists of `DecisionScope` are, and the schema now refuses a repeat.
"""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from auto_bi.cli import main
from auto_bi.decision import use_cases
from auto_bi.decision.spec import (
    BriefFormat,
    DashboardDelivery,
    DecisionProductSpec,
    DeliverySpec,
)
from auto_bi.decision.validate import validate_paths
from auto_bi.evidence.manifest import verify_bundle
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.verification.model import Severity, Verdict
from auto_bi.verification.release_gate import CHECK_VERSION, VERIFIER_VERSION, CheckStatus
from tests.test_decision_cli import _verify_argv
from tests.test_decision_verify import (
    PRODUCT_ID,
    Case,
    _check_payload,
    _write_yaml,
    dashboard_spec,
    product_contract,
    status_of,
    summary_of,
)
from tests.test_unreadable_run_inputs import ROWS, _refused

DASHBOARD = "dashboard://weekly_margin@7"
OTHER_ID = "decision://sales/other_margin"
NO_PRODUCT = "no decision product contract found in the given paths"
REPEATED = f"delivery: Value error, duplicate entries in delivery.dashboards: {DASHBOARD}"


def _product(*refs: str, brief_formats: list[str] | None = None) -> dict[str, Any]:
    product = product_contract()
    product["delivery"]["dashboards"] = [{"ref": ref} for ref in refs]
    if brief_formats is not None:
        product["delivery"]["brief_formats"] = brief_formats
    return product


def _schema_refusal(product: dict[str, Any]) -> str:
    with pytest.raises(ValidationError) as caught:
        DecisionProductSpec.model_validate(product)
    (error,) = caught.value.errors()
    assert error["loc"] == ("delivery",)
    message: str = error["msg"]
    return message


def _delivering_twice(case: Case) -> Case:
    _write_yaml(case.contracts / "weekly_margin.yaml", _product(DASHBOARD, DASHBOARD))
    return case


# --- the schema ---------------------------------------------------------------------------


def test_a_dashboard_ref_delivered_twice_fails_the_schema() -> None:
    assert _schema_refusal(_product(DASHBOARD, DASHBOARD)) == (
        f"Value error, duplicate entries in delivery.dashboards: {DASHBOARD}"
    )


def test_every_repeated_ref_is_named_once_in_sorted_order() -> None:
    first, second, third = (
        "dashboard://weekly_margin@7",
        "dashboard://ops/weekly_margin@7",
        "dashboard://weekly_revenue@1",
    )
    product = _product(first, second, first, third, second, first)
    assert _schema_refusal(product) == (
        f"Value error, duplicate entries in delivery.dashboards: {second}, {first}"
    )


def test_a_brief_format_listed_twice_fails_the_schema() -> None:
    product = _product(DASHBOARD, brief_formats=["markdown", "markdown"])
    assert _schema_refusal(product) == (
        "Value error, duplicate entries in delivery.brief_formats: markdown"
    )


def test_a_delivery_validated_on_its_own_refuses_a_repeat() -> None:
    """The rule is `DeliverySpec`'s own, not one of the product built of it."""
    for delivery, said in (
        (
            {"dashboards": [{"ref": DASHBOARD}, {"ref": DASHBOARD}]},
            f"duplicate entries in delivery.dashboards: {DASHBOARD}",
        ),
        (
            {"dashboards": [{"ref": DASHBOARD}], "brief_formats": ["markdown", "markdown"]},
            "duplicate entries in delivery.brief_formats: markdown",
        ),
    ):
        with pytest.raises(ValidationError) as caught:
            DeliverySpec.model_validate(delivery)
        (error,) = caught.value.errors()
        assert error["loc"] == ()
        assert error["msg"] == f"Value error, {said}"


@pytest.mark.parametrize(
    "refs",
    [
        ("dashboard://weekly_margin@7", "dashboard://weekly_margin@8"),
        ("dashboard://weekly_margin@7", "dashboard://weekly-margin@7"),
        ("dashboard://sales/weekly_margin@7", "dashboard://ops/weekly_margin@7"),
        ("dashboard://weekly_margin@7", "dashboard://weekly_margin@17"),
    ],
    ids=["two versions", "one slug", "one last segment", "one version a prefix of the other"],
)
def test_refs_that_differ_are_delivered_together(refs: tuple[str, str]) -> None:
    """The rule compares whole refs, and two versions of a dashboard are two dashboards."""
    spec = DecisionProductSpec.model_validate(_product(*refs))
    assert [item.ref for item in spec.delivery.dashboards] == list(refs)


# --- decision validate --------------------------------------------------------------------


def test_decision_validate_blocks_on_the_repeat(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = _delivering_twice(Case(tmp_path))

    report = validate_paths([case.contracts])

    assert [issue.code for issue in report.issues] == ["contract.invalid"]
    (issue,) = report.issues
    assert issue.severity is Severity.BLOCK
    assert issue.subject == PRODUCT_ID
    assert issue.source == "$1/weekly_margin.yaml"
    assert issue.message == REPEATED
    assert report.verdict is Verdict.BLOCK
    assert report.products == []

    # The only product of the paths failed its schema: nothing was validated, exit 2.
    assert main(["decision", "validate", str(case.contracts)]) == 2
    out = capsys.readouterr().out
    assert REPEATED in out
    assert NO_PRODUCT in out

    # Beside a product that validates, the verdict is the exit code.
    other = product_contract()
    other["metadata"]["id"] = OTHER_ID
    _write_yaml(case.contracts / "other_margin.yaml", other)
    assert main(["decision", "validate", str(case.contracts)]) == 4
    assert REPEATED in capsys.readouterr().out


# --- decision verify of the product -------------------------------------------------------


def _spec_present(case: Case) -> None:
    """The run that ended in the traceback: the spec compiles, twice, under one id."""


def _spec_missing(case: Case) -> None:
    (case.dashboards / "weekly_margin@7.yaml").unlink()


def _spec_invalid(case: Case) -> None:
    spec = dashboard_spec()
    spec["charts"][0]["query"]["dimensions"] = ["no_such_column"]
    _write_yaml(case.dashboards / "weekly_margin@7.yaml", spec)


def _spec_refused(case: Case) -> None:
    spec = dashboard_spec()
    spec["title"] = ""
    _write_yaml(case.dashboards / "weekly_margin@7.yaml", spec)


SPEC_STATES = [
    pytest.param(_spec_present, id="spec compiles"),
    pytest.param(_spec_missing, id="no spec"),
    pytest.param(_spec_invalid, id="spec invalid"),
    pytest.param(_spec_refused, id="compile refuses"),
]


@pytest.mark.parametrize("arrange", SPEC_STATES)
def test_verify_refuses_the_product_before_anything_is_written(
    tmp_path: Path, arrange: Any
) -> None:
    case = _delivering_twice(Case(tmp_path).with_asset().with_results(ROWS))
    arrange(case)
    # The refusal names the product and what its schema refused (schema-refused-products).
    named = (
        f"1 decision product(s) failed the schema: '{PRODUCT_ID}' in "
        f"$1/weekly_margin.yaml ({REPEATED})"
    )

    assert _refused(case) == f"{NO_PRODUCT}; {named}"
    assert _refused(case, product_id=PRODUCT_ID) == (
        f"no decision product '{PRODUCT_ID}' in the given paths (none); {named}"
    )


@pytest.mark.parametrize("arrange", SPEC_STATES)
def test_the_cli_says_it_in_one_line_with_exit_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], arrange: Any
) -> None:
    case = _delivering_twice(Case(tmp_path).with_asset().with_results(ROWS))
    arrange(case)

    assert main(_verify_argv(case)) == 2

    captured = capsys.readouterr()
    assert NO_PRODUCT in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""
    assert not (tmp_path / "evidence").exists()


# --- decision verify of another product of the paths --------------------------------------


def test_a_repeat_in_another_product_blocks_the_run_of_this_one(tmp_path: Path) -> None:
    """A file that fails its schema blocks every product of the paths (validation-issue-scope)."""
    case = Case(tmp_path).with_asset().with_results(ROWS)
    clean = case.run(product_id=PRODUCT_ID)
    assert status_of(clean, "contracts.validate") is not CheckStatus.BLOCK

    other = _product(DASHBOARD, DASHBOARD)
    other["metadata"]["id"] = OTHER_ID
    _write_yaml(case.contracts / "other_margin.yaml", other)
    report = case.run(product_id=PRODUCT_ID)

    assert status_of(report, "contracts.validate") is CheckStatus.BLOCK
    assert summary_of(report, "contracts.validate") == "contracts.issues"
    assert report.verdict is Verdict.BLOCK
    assert report.run_id != clean.run_id
    payload = _check_payload(tmp_path, report, "contracts.validate")
    assert REPEATED in str(payload)
    store = FilesystemArtifactStore(tmp_path / "evidence")
    assert verify_bundle(store, report.run_id) == []


# --- what is not changed ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("arrange", "named_in"),
    [
        pytest.param(_spec_missing, "missing", id="no spec"),
        pytest.param(_spec_invalid, "invalid", id="spec invalid"),
        pytest.param(_spec_refused, "refused", id="compile refuses"),
    ],
)
def test_a_bundle_naming_a_dashboard_twice_still_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arrange: Any,
    named_in: str,
) -> None:
    """No reader of a bundle builds the product, so a bundle that names a dashboard twice
    reads and re-verifies — the bundle verifier 113 or before sealed for a product with no
    spec for it, an invalid one, or one the compile refused. The product is handed to the gate
    past the schema, and today's writer seals such a bundle: none of verifier 113 is kept."""
    case = Case(tmp_path).with_asset().with_results(ROWS)
    arrange(case)
    valid = DecisionProductSpec.model_validate(product_contract())
    twice = DeliverySpec.model_construct(
        dashboards=[DashboardDelivery(ref=DASHBOARD), DashboardDelivery(ref=DASHBOARD)],
        brief_formats=[BriefFormat.MARKDOWN],
    )
    repeated = valid.model_copy(update={"delivery": twice})
    monkeypatch.setattr(use_cases, "_select_product", lambda loaded, product_id: repeated)

    report = case.run()

    named = _check_payload(tmp_path, report, "bi.contract")[named_in]
    assert [item if named_in == "missing" else item["dashboard"] for item in named] == [
        DASHBOARD,
        DASHBOARD,
    ]
    store = FilesystemArtifactStore(tmp_path / "evidence")
    assert verify_bundle(store, report.run_id) == []
    assert main(["decision", "evidence", str(tmp_path / "evidence"), "--run", report.run_id]) == 0
    assert report.run_id in capsys.readouterr().out


def test_refusing_a_repeated_delivery_entry_moved_both_versions() -> None:
    assert int(VERIFIER_VERSION) >= 114
    assert int(CHECK_VERSION) >= 77
