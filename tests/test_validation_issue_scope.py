"""Which issues of the validation report a run grades: the verified product's, not another's."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from auto_bi.cli import main
from auto_bi.decision.use_cases import VerifyRequest, verify
from auto_bi.decision.validate import (
    LoadedContracts,
    ValidationIssue,
    ValidationReport,
    load_contracts,
    validate_paths,
)
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.semantic.gateway import NativeModelSource
from auto_bi.severity import Severity
from auto_bi.verification.model import Verdict
from auto_bi.verification.release_gate import (
    CHECK_VERSION,
    VERIFIER_VERSION,
    CheckStatus,
    GateInputs,
    GateReport,
    run_gate,
)
from tests.test_decision_cli import _verify_argv
from tests.test_decision_verify import (
    CREATED_AT,
    PRODUCT_ID,
    Case,
    _check_payload,
    _write_yaml,
    detail_of,
    metric_contract,
    product_contract,
    revenue_metric,
    semantic_model,
    status_of,
    summary_of,
)

VERIFIED = f"{PRODUCT_ID}@1"
OTHER_ID = "decision://sales/monthly_margin"
OTHER = f"{OTHER_ID}@1"
METRIC = "metric://sales/gross_margin_pct"
PIN = f"{METRIC}@3"
MOVED_PIN = f"{METRIC}@2"
REVENUE_PIN = "metric://sales/revenue@3"
OWN_POLICY = "policy://release/default@1"
STRICT = "policy://release/strict@1"
WITH_STORE = ["region", "category", "store"]
MOVED = f"the repository holds version 3 of {METRIC}; the pinned version is gone"
UNKNOWN = "no metric contract with this id in the validated set"
RETIRED = "metric contract is retired and must not back a decision product"
DRAFT = "metric contract is still a draft: its definition is not agreed yet"
DEPRECATED = "metric contract is deprecated: plan a replacement before it is retired"


def _product(
    product_id: str = OTHER_ID,
    *,
    metrics: list[str] | None = None,
    dimensions: list[str] | None = None,
    policy: str | None = None,
) -> dict[str, Any]:
    """The fixture's product under *product_id*, with the named parts of it replaced."""
    product = product_contract()
    product["metadata"]["id"] = product_id
    if metrics is not None:
        product["scope"]["metrics"] = metrics
    if dimensions is not None:
        product["scope"]["dimensions"] = dimensions
    if policy is not None:
        product["verification"]["policy"] = policy
    return product


def _beside(case: Case, product: dict[str, Any]) -> Case:
    """Write *product* into the validated paths, in the file its id names."""
    name = product["metadata"]["id"].rsplit("/", 1)[1]
    _write_yaml(case.contracts / f"{name}.yaml", product)
    return case


def _two_products(tmp_path: Path, **other: Any) -> Case:
    """The verified product, clean, beside the other product with *other* replaced."""
    return _beside(Case(tmp_path).with_candidate(), _product(**other))


def _drafted(tmp_path: Path, candidate_version: int = 3) -> Case:
    """Validated paths and base holding the pinned metric as a draft; so does the candidate."""
    draft = metric_contract()
    draft["metadata"]["status"] = "draft"
    held = {**draft, "metadata": {**draft["metadata"], "version": candidate_version}}
    case = Case(tmp_path).with_candidate(contract=held)
    _write_yaml(case.contracts / "gross_margin_pct.yaml", draft)
    _write_yaml(case.base / "metrics" / "gross_margin_pct.yaml", draft)
    return case


def _issue(
    code: str, severity: str, subject: str, message: str, source: str = OTHER
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "subject": subject,
        "source": source,
        "message": message,
    }


def _moved(source: str = OTHER) -> dict[str, Any]:
    return _issue("metric_ref.version_mismatch", "block", MOVED_PIN, MOVED, source)


def _undeclared(dimension: str, source: str = OTHER) -> dict[str, Any]:
    message = f"dimension {dimension!r} is not declared by {PIN}"
    return _issue("dimension.not_allowed", "block", dimension, message, source)


def _foreign_policy(source: str = OTHER) -> dict[str, Any]:
    message = f"metric is governed by {OWN_POLICY}, the product verifies under {STRICT}"
    return _issue("policy.mismatch", "review", PIN, message, source)


def _draft(source: str) -> dict[str, Any]:
    return _issue("metric.draft", "review", PIN, DRAFT, source)


def _detail(
    issues: int,
    other: tuple[int, int] | None = None,
    *,
    metrics: int = 1,
    matching: int = 1,
    tail: str = "",
) -> str:
    """The check's detail: *other* is (issues, products) of what was not graded."""
    text = f"{metrics} metric contract(s) resolved, {issues} issue(s)"
    if other is not None:
        text += f", {other[0]} issue(s) of {other[1]} other product(s) not graded"
    return f"{text}; {matching} of 1 metric pin(s) match the candidate snapshot{tail}"


def _payload(case: Case, report: GateReport) -> dict[str, Any]:
    return _check_payload(case.root, report, "contracts.validate")


def _inputs(
    case: Case, loaded: LoadedContracts, *, report: ValidationReport | None = None
) -> GateInputs:
    """Gate inputs for the verified product; *report* replaces the report of its paths."""
    return GateInputs(
        product=loaded.products[PRODUCT_ID],
        validation=loaded.report if report is None else report,
        base=NativeModelSource(case.base / "model.yaml", case.base / "metrics").snapshot(),
        candidate=NativeModelSource(
            case.candidate / "model.yaml", case.candidate / "metrics"
        ).snapshot(),
        candidate_model=semantic_model(),
        validated_metrics=loaded.metrics,
    )


def _gate(case: Case, inputs: GateInputs) -> GateReport:
    return run_gate(
        inputs, store=FilesystemArtifactStore(case.root / "evidence"), created_at=CREATED_AT
    )


# --- another product's rule issues are not this run's ---------------------------


def test_another_products_moved_pin_does_not_block_this_products_release(tmp_path: Path) -> None:
    case = _two_products(tmp_path, metrics=[MOVED_PIN])

    report = case.run(product_id=PRODUCT_ID)

    assert status_of(report, "contracts.validate") is CheckStatus.PASS
    assert summary_of(report, "contracts.validate") == "contracts.clean"
    assert detail_of(report, "contracts.validate") == (
        "1 metric contract(s) resolved, 0 issue(s), 1 issue(s) of 1 other product(s) not graded; "
        "1 of 1 metric pin(s) match the candidate snapshot"
    )
    payload = _payload(case, report)
    assert payload["issues"] == []
    assert payload["verdict"] == "pass"
    assert payload["other_product_issues"] == [
        {
            "code": "metric_ref.version_mismatch",
            "severity": "block",
            "subject": "metric://sales/gross_margin_pct@2",
            "source": "decision://sales/monthly_margin@1",
            "message": (
                "the repository holds version 3 of metric://sales/gross_margin_pct; "
                "the pinned version is gone"
            ),
        }
    ]
    assert payload["products"] == [OTHER, VERIFIED]
    check = next(check for check in report.checks if check.check_id == "contracts.validate")
    assert check.subject_refs == [VERIFIED, PIN]
    assert report.verdict is Verdict.PASS_WITH_ADVISORY
    assert report.blocking() == []
    summary = (tmp_path / "evidence" / report.run_id / "reports" / "summary.md").read_text(
        encoding="utf-8"
    )
    assert "## What stops the release" not in summary
    assert "## What needs a human" not in summary
    row = next(line for line in summary.splitlines() if line.startswith("| `contracts.validate`"))
    assert "1 issue(s) of 1 other product(s) not graded" in row


def _pins_unknown(case: Case) -> dict[str, Any]:
    unknown = "metric://sales/net_margin_pct@1"
    _beside(case, _product(metrics=[unknown]))
    return _issue("metric_ref.unknown", "block", unknown, UNKNOWN)


def _pins_moved(case: Case) -> dict[str, Any]:
    _beside(case, _product(metrics=[MOVED_PIN]))
    return _moved()


def _splits_by_store(case: Case) -> dict[str, Any]:
    _beside(case, _product(dimensions=WITH_STORE))
    return _undeclared("store")


def _verifies_under_strict(case: Case) -> dict[str, Any]:
    _beside(case, _product(policy=STRICT))
    return _foreign_policy()


def _pins_its_own(status: str, severity: str, message: str) -> Callable[[Case], dict[str, Any]]:
    """The other product on a metric of its own, held with *status* by the paths and both refs."""

    def build(case: Case) -> dict[str, Any]:
        revenue = revenue_metric()
        revenue["metadata"]["status"] = status
        for root in (case.contracts, case.base / "metrics", case.candidate / "metrics"):
            _write_yaml(root / "revenue.yaml", revenue)
        _beside(case, _product(metrics=[REVENUE_PIN]))
        return _issue(f"metric.{status}", severity, REVENUE_PIN, message)

    return build


@pytest.mark.parametrize(
    ("build", "metrics"),
    [
        pytest.param(_pins_unknown, 1, id="metric_ref.unknown"),
        pytest.param(_pins_moved, 1, id="metric_ref.version_mismatch"),
        pytest.param(_splits_by_store, 1, id="dimension.not_allowed"),
        pytest.param(_verifies_under_strict, 1, id="policy.mismatch"),
        pytest.param(_pins_its_own("retired", "block", RETIRED), 2, id="metric.retired"),
        pytest.param(_pins_its_own("draft", "review", DRAFT), 2, id="metric.draft"),
        pytest.param(
            _pins_its_own("deprecated", "advisory", DEPRECATED), 2, id="metric.deprecated"
        ),
    ],
)
def test_no_rule_issue_of_another_product_is_graded(
    tmp_path: Path, build: Callable[[Case], dict[str, Any]], metrics: int
) -> None:
    alone = Case(tmp_path / "alone").with_candidate().run()
    case = Case(tmp_path / "beside").with_candidate()
    issue = build(case)
    assert [found.model_dump(mode="json") for found in validate_paths([case.contracts]).issues] == [
        issue
    ]

    report = case.run(product_id=PRODUCT_ID)

    assert status_of(report, "contracts.validate") is CheckStatus.PASS
    assert summary_of(report, "contracts.validate") == "contracts.clean"
    assert detail_of(report, "contracts.validate") == _detail(0, (1, 1), metrics=metrics)
    payload = _payload(case, report)
    assert payload["issues"] == []
    assert payload["verdict"] == "pass"
    assert payload["other_product_issues"] == [issue]
    assert report.verdict is alone.verdict is Verdict.PASS_WITH_ADVISORY


def test_the_verified_products_own_issue_is_graded_beside_another_products(
    tmp_path: Path,
) -> None:
    case = _two_products(tmp_path, metrics=[MOVED_PIN])
    _beside(case, _product(PRODUCT_ID, dimensions=WITH_STORE))

    report = case.run(product_id=PRODUCT_ID)

    assert status_of(report, "contracts.validate") is CheckStatus.BLOCK
    assert summary_of(report, "contracts.validate") == "contracts.issues"
    assert detail_of(report, "contracts.validate") == _detail(1, (1, 1))
    payload = _payload(case, report)
    assert payload["issues"] == [_undeclared("store", VERIFIED)]
    assert payload["verdict"] == "block"
    assert payload["other_product_issues"] == [_moved()]


def test_a_rule_answered_for_both_products_is_graded_once(tmp_path: Path) -> None:
    case = _beside(_drafted(tmp_path), _product())
    stated = validate_paths([case.contracts]).issues
    assert [issue.model_dump(mode="json") for issue in stated] == [_draft(OTHER), _draft(VERIFIED)]

    report = case.run(product_id=PRODUCT_ID)

    assert status_of(report, "contracts.validate") is CheckStatus.REVIEW
    assert summary_of(report, "contracts.validate") == "contracts.issues"
    assert detail_of(report, "contracts.validate") == _detail(1, (1, 1))
    payload = _payload(case, report)
    assert payload["issues"] == [_draft(VERIFIED)]
    assert payload["verdict"] == "needs_review"
    assert payload["other_product_issues"] == [_draft(OTHER)]


def test_verifying_the_other_product_grades_its_issues_and_not_this_ones(
    tmp_path: Path,
) -> None:
    case = _two_products(tmp_path, metrics=[MOVED_PIN])
    _beside(case, _product(PRODUCT_ID, dimensions=WITH_STORE))

    report = case.run(product_id=OTHER_ID)

    assert report.product_ref == OTHER
    assert status_of(report, "contracts.validate") is CheckStatus.BLOCK
    payload = _payload(case, report)
    assert [issue["code"] for issue in payload["issues"]] == [
        "metric_ref.version_mismatch",
        "metric_ref.candidate_version_mismatch",
    ]
    assert payload["issues"][0] == _moved()
    assert payload["other_product_issues"] == [_undeclared("store", VERIFIED)]
    check = next(check for check in report.checks if check.check_id == "contracts.validate")
    assert check.subject_refs == [OTHER, MOVED_PIN]


def test_several_issues_of_one_other_product_are_counted_by_issue_and_by_product(
    tmp_path: Path,
) -> None:
    unknown = "metric://sales/net_margin_pct@1"
    case = _two_products(
        tmp_path, metrics=[unknown, PIN], dimensions=[*WITH_STORE, "warehouse"], policy=STRICT
    )
    _beside(case, _product("decision://sales/yearly_margin"))  # a clean product names nothing

    report = case.run(product_id=PRODUCT_ID)

    assert status_of(report, "contracts.validate") is CheckStatus.PASS
    assert detail_of(report, "contracts.validate") == (
        "1 metric contract(s) resolved, 0 issue(s), 4 issue(s) of 1 other product(s) not graded; "
        "1 of 1 metric pin(s) match the candidate snapshot"
    )
    payload = _payload(case, report)
    assert len(payload["products"]) == 3
    assert payload["other_product_issues"] == [
        _issue("metric_ref.unknown", "block", unknown, UNKNOWN),
        _undeclared("store"),
        _undeclared("warehouse"),
        _foreign_policy(),
    ]


def test_the_issues_keep_the_reports_order_on_both_sides(tmp_path: Path) -> None:
    case = Case(tmp_path).with_candidate()
    daily, yearly = "decision://sales/daily_margin", "decision://sales/yearly_margin"
    _beside(case, _product(yearly, policy=STRICT))
    _beside(case, _product(PRODUCT_ID, dimensions=WITH_STORE))
    _beside(case, _product(daily, metrics=[MOVED_PIN]))
    notes = case.contracts / "notes.yaml"
    notes.write_text("- a list\n- not a mapping\n", encoding="utf-8")

    report = case.run(product_id=PRODUCT_ID)

    assert status_of(report, "contracts.validate") is CheckStatus.BLOCK
    assert detail_of(report, "contracts.validate") == _detail(2, (2, 2))
    payload = _payload(case, report)
    assert payload["issues"] == [
        _issue(
            "contract.unreadable",
            "block",
            str(notes),
            "contract file must contain a YAML mapping",
            str(notes),
        ),
        _undeclared("store", VERIFIED),
    ]
    assert payload["other_product_issues"] == [
        _moved(f"{daily}@1"),
        _foreign_policy(f"{yearly}@1"),
    ]


def test_a_report_stated_in_another_order_keeps_it_on_both_sides(tmp_path: Path) -> None:
    """The order is the report's, not one the gate derives from codes, subjects or sources."""
    case = _two_products(tmp_path)
    _beside(case, _product("decision://sales/yearly_margin"))
    loaded = load_contracts([case.contracts])
    stated = [
        _foreign_policy("decision://sales/yearly_margin@1"),
        _undeclared("store", VERIFIED),
        _moved(),
        _issue("contract.unreadable", "block", "notes.yaml", "path does not exist", "notes.yaml"),
        _draft(VERIFIED),
        _undeclared("store", OTHER),
    ]
    report = _gate(
        case,
        _inputs(
            case,
            loaded,
            report=ValidationReport(
                verdict=Verdict.BLOCK,
                products=loaded.report.products,
                metrics=loaded.report.metrics,
                issues=[ValidationIssue.model_validate(issue) for issue in stated],
            ),
        ),
    )

    payload = _payload(case, report)
    assert payload["issues"] == [stated[1], stated[3], stated[4]]
    assert payload["other_product_issues"] == [stated[0], stated[2], stated[5]]
    assert detail_of(report, "contracts.validate") == _detail(3, (3, 2))


def test_the_pin_and_rule_issues_follow_the_verified_products_own_report_issues(
    tmp_path: Path,
) -> None:
    case = _beside(_drafted(tmp_path, candidate_version=4), _product())

    report = case.run(product_id=PRODUCT_ID)

    assert status_of(report, "contracts.validate") is CheckStatus.BLOCK
    assert detail_of(report, "contracts.validate") == (
        "1 metric contract(s) resolved, 2 issue(s), 1 issue(s) of 1 other product(s) not graded; "
        "0 of 1 metric pin(s) match the candidate snapshot: "
        "it holds version 4 of metric://sales/gross_margin_pct, pinned at version 3"
    )
    payload = _payload(case, report)
    assert payload["issues"] == [
        _draft(VERIFIED),
        _issue(
            "metric_ref.candidate_version_mismatch",
            "block",
            PIN,
            f"the candidate snapshot holds version 4 of {METRIC}; the product pins version 3",
            VERIFIED,
        ),
    ]
    assert payload["other_product_issues"] == [_draft(OTHER)]


# --- what is graded as before ---------------------------------------------------


def _not_a_mapping(case: Case) -> tuple[Path, str, int]:
    path = case.contracts / "notes.yaml"
    path.write_text("- a list\n- not a mapping\n", encoding="utf-8")
    return path, "contract.unreadable", 1


def _unknown_kind(case: Case) -> tuple[Path, str, int]:
    path = case.contracts / "playbook.yaml"
    _write_yaml(path, {"kind": "Playbook", "metadata": {"id": "playbook://sales/close"}})
    return path, "contract.unknown_kind", 1


def _third_product_invalid(case: Case) -> tuple[Path, str, int]:
    broken = _product("decision://sales/quarterly_margin")
    del broken["decision"]
    path = case.contracts / "quarterly_margin.yaml"
    _write_yaml(path, broken)
    return path, "contract.invalid", 1


def _unpinned_metric_invalid(case: Case) -> tuple[Path, str, int]:
    broken = revenue_metric()
    del broken["grain"]
    path = case.contracts / "revenue.yaml"
    _write_yaml(path, broken)
    return path, "contract.invalid", 1


def _other_product_twice(case: Case) -> tuple[Path, str, int]:
    path = case.contracts / "monthly_margin_copy.yaml"
    _write_yaml(path, _product())
    return path, "contract.duplicate_id", 1


def _unpinned_metric_twice(case: Case) -> tuple[Path, str, int]:
    _write_yaml(case.contracts / "revenue.yaml", revenue_metric())
    path = case.contracts / "revenue_copy.yaml"
    _write_yaml(path, revenue_metric())
    return path, "contract.duplicate_id", 2


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(_not_a_mapping, id="not a mapping"),
        pytest.param(_unknown_kind, id="unknown kind"),
        pytest.param(_third_product_invalid, id="a third product's contract is invalid"),
        pytest.param(_unpinned_metric_invalid, id="an unpinned metric's contract is invalid"),
        pytest.param(_other_product_twice, id="the other product is defined twice"),
        pytest.param(_unpinned_metric_twice, id="an unpinned metric is defined twice"),
    ],
)
def test_an_issue_of_loading_a_file_is_graded_whatever_the_file_holds(
    tmp_path: Path, build: Callable[[Case], tuple[Path, str, int]]
) -> None:
    case = _two_products(tmp_path)
    path, code, metrics = build(case)

    report = case.run(product_id=PRODUCT_ID)

    assert status_of(report, "contracts.validate") is CheckStatus.BLOCK
    assert summary_of(report, "contracts.validate") == "contracts.issues"
    assert detail_of(report, "contracts.validate") == _detail(1, metrics=metrics)
    payload = _payload(case, report)
    assert [(issue["code"], issue["source"]) for issue in payload["issues"]] == [(code, str(path))]
    assert payload["other_product_issues"] == []
    assert report.verdict is Verdict.BLOCK


def test_a_path_that_does_not_exist_is_graded_however_it_is_spelled(tmp_path: Path) -> None:
    case = _two_products(tmp_path)
    spelled = Path(OTHER)
    assert str(spelled) != OTHER

    report = verify(
        VerifyRequest(
            contract_paths=[case.contracts, spelled],
            base=case.base,
            candidate=case.candidate,
            evidence_dir=tmp_path / "evidence",
            created_at=CREATED_AT,
            dashboards_dir=case.dashboards,
            product_id=PRODUCT_ID,
        )
    )

    assert status_of(report, "contracts.validate") is CheckStatus.BLOCK
    payload = _payload(case, report)
    assert payload["products"] == [OTHER, VERIFIED]
    assert payload["issues"] == [
        _issue("contract.unreadable", "block", str(spelled), "path does not exist", str(spelled))
    ]
    assert payload["other_product_issues"] == []


@pytest.mark.parametrize("own", [False, True], ids=["clean", "an issue of its own"])
def test_one_product_in_the_paths_has_nothing_set_aside(tmp_path: Path, own: bool) -> None:
    case = _drafted(tmp_path) if own else Case(tmp_path)

    report = case.run()

    payload = _payload(case, report)
    assert payload["products"] == [VERIFIED]
    assert payload["issues"] == ([_draft(VERIFIED)] if own else [])
    assert payload["other_product_issues"] == []
    assert detail_of(report, "contracts.validate") == (
        f"1 metric contract(s) resolved, {int(own)} issue(s); "
        "1 of 1 metric pin(s) match the candidate snapshot"
    )


def test_a_report_that_does_not_list_the_verified_product_is_graded_whole(
    tmp_path: Path,
) -> None:
    case = Case(tmp_path).with_candidate()
    elsewhere = tmp_path / "elsewhere"
    _write_yaml(elsewhere / "monthly_margin.yaml", _product(metrics=[MOVED_PIN]))
    _write_yaml(elsewhere / "gross_margin_pct.yaml", metric_contract())
    stated = load_contracts([elsewhere]).report
    assert stated.products == [OTHER]

    report = _gate(case, _inputs(case, load_contracts([case.contracts]), report=stated))

    assert status_of(report, "contracts.validate") is CheckStatus.BLOCK
    assert summary_of(report, "contracts.validate") == "contracts.issues"
    assert detail_of(report, "contracts.validate") == _detail(1)
    payload = _payload(case, report)
    assert payload["issues"] == [_moved()]
    assert payload["other_product_issues"] == []


def test_an_issue_whose_source_the_report_does_not_list_is_graded(tmp_path: Path) -> None:
    case = _two_products(tmp_path)
    loaded = load_contracts([case.contracts])
    unlisted = ValidationIssue(
        code="metric.draft",
        severity=Severity.REVIEW,
        subject=PIN,
        source="decision://sales/quarterly_margin@1",
        message=DRAFT,
    )
    stated = ValidationReport(
        verdict=Verdict.NEEDS_REVIEW,
        products=loaded.report.products,
        metrics=loaded.report.metrics,
        issues=[unlisted],
    )

    report = _gate(case, _inputs(case, loaded, report=stated))

    assert status_of(report, "contracts.validate") is CheckStatus.REVIEW
    payload = _payload(case, report)
    assert payload["issues"] == [unlisted.model_dump(mode="json")]
    assert payload["other_product_issues"] == []


# --- what stays as it was --------------------------------------------------------


def _outcome(report: GateReport) -> list[tuple[str, CheckStatus, str, list[str]]]:
    return [
        (check.check_id, check.status, check.summary_code, check.subject_refs)
        for check in report.checks
    ]


def test_another_products_issue_moves_the_run_id_and_nothing_else(tmp_path: Path) -> None:
    case = _two_products(tmp_path)
    first = case.run(product_id=PRODUCT_ID)

    _beside(case, _product(metrics=[MOVED_PIN]))
    second = case.run(product_id=PRODUCT_ID)

    assert second.verdict is first.verdict
    assert _outcome(second) == _outcome(first)
    assert second.run_id != first.run_id
    assert sorted(path.name for path in (tmp_path / "evidence").iterdir()) == sorted(
        [first.run_id, second.run_id]
    )


def test_the_validation_report_is_not_modified_and_decision_validate_is_unchanged(
    tmp_path: Path,
) -> None:
    case = _two_products(tmp_path, metrics=[MOVED_PIN])
    loaded = load_contracts([case.contracts])
    before = loaded.report.model_dump()

    report = _gate(case, _inputs(case, loaded))

    assert status_of(report, "contracts.validate") is CheckStatus.PASS
    assert loaded.report.model_dump() == before == validate_paths([case.contracts]).model_dump()
    assert [issue.model_dump(mode="json") for issue in loaded.report.issues] == [_moved()]
    assert loaded.report.verdict is Verdict.BLOCK


def test_verify_exits_by_its_product_and_validate_by_the_paths(tmp_path: Path) -> None:
    case = _two_products(tmp_path, metrics=[MOVED_PIN])

    assert main(["decision", "validate", str(case.contracts)]) == 4
    assert main(_verify_argv(case, "--product", PRODUCT_ID)) == 0
    assert main(_verify_argv(case, "--product", OTHER_ID)) == 4


def test_the_versions_say_the_rule_changed(tmp_path: Path) -> None:
    assert int(VERIFIER_VERSION) >= 92
    assert int(CHECK_VERSION) >= 67
    report = Case(tmp_path).run()
    check = next(check for check in report.checks if check.check_id == "contracts.validate")
    assert check.check_version == CHECK_VERSION
