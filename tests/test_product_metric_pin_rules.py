"""The product's rules answered on the candidate's definition of a pinned metric."""

import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from auto_bi.decision.validate import load_contracts, validate_paths
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.semantic.gateway import NativeModelSource
from auto_bi.verification.model import Verdict
from auto_bi.verification.release_gate import (
    CHECK_VERSION,
    VERIFIER_VERSION,
    CheckStatus,
    GateInputs,
    GateReport,
    run_gate,
)
from tests.test_decision_verify import (
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

PIN = "metric://sales/gross_margin_pct@3"
ID = "metric://sales/gross_margin_pct"
PRODUCT = "decision://sales/weekly_margin@1"
OWN_POLICY = "policy://release/default@1"
FINANCE = "policy://finance/close@2"
RISK = "policy://risk/limits@1"
NUMERATOR = {"table": "dm.sales_daily", "column": "revenue", "agg": "sum"}
MATCH = [{"ref": PIN, "candidate_versions": [3], "outcome": "match"}]
RETIRED = "metric contract is retired and must not back a decision product"
DRAFT = "metric contract is still a draft: its definition is not agreed yet"
DEPRECATED = "metric contract is deprecated: plan a replacement before it is retired"
RULE_FAMILIES = ("dimension.candidate_", "metric.candidate_", "policy.candidate_")


def _held(**changes: Any) -> dict[str, Any]:
    """The fixture's metric contract with the named fields replaced (`metadata__status`)."""
    contract = metric_contract()
    for dotted, value in changes.items():
        *parents, leaf = dotted.split("__")
        target = contract
        for key in parents:
            target = target[key]
        target[leaf] = value
    return contract


def _case(
    tmp_path: Path, *, validated: dict[str, Any], base: dict[str, Any], candidate: dict[str, Any]
) -> Case:
    case = Case(tmp_path).with_candidate(contract=deepcopy(candidate))
    _write_yaml(case.contracts / "gross_margin_pct.yaml", deepcopy(validated))
    _write_yaml(case.base / "metrics/gross_margin_pct.yaml", deepcopy(base))
    return case


def _own(code: str, severity: str, message: str, subject: str = PIN) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "subject": subject,
        "source": PRODUCT,
        "message": message,
    }


def _rule(code: str, severity: str, message: str, subject: str = PIN) -> dict[str, Any]:
    return _own(code, severity, "in the candidate snapshot, " + message, subject)


def _content(codes: list[str], severity: str) -> dict[str, Any]:
    return _own(
        "metric_ref.candidate_content_mismatch",
        severity,
        f"the candidate snapshot holds another definition of {ID} at version 3 "
        f"than the validated contract ({', '.join(codes)}), "
        "and the base snapshot does not hold the validated definition",
    )


def _governed(policies: str) -> str:
    return f"metric is governed by {policies}, the product verifies under {OWN_POLICY}"


def _undeclared(dimension: str) -> str:
    return f"dimension {dimension!r} is not declared by {PIN}"


def _head(issues: int, matching: int = 1) -> str:
    return (
        f"1 metric contract(s) resolved, {issues} issue(s); "
        f"{matching} of 1 metric pin(s) match the candidate snapshot"
    )


def _pin_clause(codes: list[str]) -> str:
    return (
        f": it holds another definition of {ID} at version 3 "
        f"than the validated contract ({', '.join(codes)})"
    )


def _rule_clause(codes: list[str], pin: str = PIN) -> str:
    return f"; the product's rules give {', '.join(codes)} on the candidate's definition of {pin}"


def _diff_codes(case: Case, report: GateReport) -> list[str]:
    return [
        change["code"] for change in _check_payload(case.root, report, "semantic.diff")["changes"]
    ]


def _assert_check(
    case: Case,
    report: GateReport,
    *,
    issues: list[dict[str, Any]],
    detail: str,
    status: CheckStatus,
    verdict: str,
    pins: list[dict[str, Any]] | None = None,
) -> None:
    assert status_of(report, "contracts.validate") is status
    assert summary_of(report, "contracts.validate") == (
        "contracts.issues" if issues else "contracts.clean"
    )
    assert detail_of(report, "contracts.validate") == detail
    payload = _check_payload(case.root, report, "contracts.validate")
    assert payload["issues"] == issues
    assert payload["candidate_pins"] == (MATCH if pins is None else pins)
    assert payload["verdict"] == verdict


def _gate_inputs(case: Case, loaded: Any, *, compared: bool = True) -> GateInputs:
    return GateInputs(
        product=next(iter(loaded.products.values())),
        validation=loaded.report,
        base=NativeModelSource(case.base / "model.yaml", case.base / "metrics").snapshot(),
        candidate=NativeModelSource(
            case.candidate / "model.yaml", case.candidate / "metrics"
        ).snapshot(),
        candidate_model=semantic_model(),
        **({"validated_metrics": loaded.metrics} if compared else {}),
    )


# --- the rules on the candidate's definition ----------------------------------


def test_a_candidate_that_moves_the_pinned_metric_back_to_draft_needs_review(
    tmp_path: Path,
) -> None:
    case = _case(
        tmp_path, validated=_held(), base=_held(), candidate=_held(metadata__status="draft")
    )
    report = case.run()
    _assert_check(
        case,
        report,
        issues=[
            {
                "code": "metric.candidate_draft",
                "severity": "review",
                "subject": "metric://sales/gross_margin_pct@3",
                "source": "decision://sales/weekly_margin@1",
                "message": (
                    "in the candidate snapshot, metric contract is still a draft: "
                    "its definition is not agreed yet"
                ),
            }
        ],
        detail=(
            "1 metric contract(s) resolved, 1 issue(s); "
            "1 of 1 metric pin(s) match the candidate snapshot; "
            "the product's rules give metric.candidate_draft on the candidate's definition of "
            "metric://sales/gross_margin_pct@3"
        ),
        status=CheckStatus.REVIEW,
        verdict="needs_review",
    )
    assert "metric.status_changed" in _diff_codes(case, report)
    assert report.verdict is Verdict.NEEDS_REVIEW
    summary = (tmp_path / "evidence" / report.run_id / "reports/summary.md").read_text(
        encoding="utf-8"
    )
    needs = summary.split("What needs a human", 1)[1].split("\n## ", 1)[0]
    assert any(
        "contracts.validate" in line and detail_of(report, "contracts.validate") in line
        for line in needs.splitlines()
    )


def test_a_draft_that_the_base_holds_too_needs_review(tmp_path: Path) -> None:
    draft = _held(metadata__status="draft")
    case = _case(tmp_path, validated=_held(), base=draft, candidate=draft)
    report = case.run()
    _assert_check(
        case,
        report,
        issues=[
            _content(["metric.status_changed"], "advisory"),
            _rule("metric.candidate_draft", "review", DRAFT),
        ],
        pins=[
            {
                "ref": PIN,
                "candidate_versions": [3],
                "outcome": "content_mismatch",
                "differences": ["metric.status_changed"],
            }
        ],
        detail=(
            "1 metric contract(s) resolved, 2 issue(s); "
            "0 of 1 metric pin(s) match the candidate snapshot: "
            "it holds another definition of metric://sales/gross_margin_pct at version 3 "
            "than the validated contract (metric.status_changed); "
            "the product's rules give metric.candidate_draft on the candidate's definition of "
            "metric://sales/gross_margin_pct@3"
        ),
        status=CheckStatus.REVIEW,
        verdict="needs_review",
    )
    assert report.verdict is Verdict.NEEDS_REVIEW


@pytest.mark.parametrize("base_holds", ["validated", "candidate"])
def test_a_governing_policy_the_product_does_not_verify_under_needs_review(
    tmp_path: Path, base_holds: str
) -> None:
    validated = _held(policy_refs=[])
    candidate = _held(policy_refs=[FINANCE])
    case = _case(
        tmp_path,
        validated=validated,
        base=validated if base_holds == "validated" else candidate,
        candidate=candidate,
    )
    report = case.run()
    mismatch = {
        "code": "policy.candidate_mismatch",
        "severity": "review",
        "subject": PIN,
        "source": PRODUCT,
        "message": (
            "in the candidate snapshot, metric is governed by policy://finance/close@2, "
            "the product verifies under policy://release/default@1"
        ),
    }
    clause = _rule_clause(["policy.candidate_mismatch"])
    if base_holds == "validated":
        _assert_check(
            case,
            report,
            issues=[mismatch],
            detail=_head(1) + clause,
            status=CheckStatus.REVIEW,
            verdict="needs_review",
        )
    else:
        _assert_check(
            case,
            report,
            issues=[_content(["metric.policy_added"], "advisory"), mismatch],
            pins=[
                {
                    "ref": PIN,
                    "candidate_versions": [3],
                    "outcome": "content_mismatch",
                    "differences": ["metric.policy_added"],
                }
            ],
            detail=_head(2, 0) + _pin_clause(["metric.policy_added"]) + clause,
            status=CheckStatus.REVIEW,
            verdict="needs_review",
        )
    assert report.verdict is Verdict.NEEDS_REVIEW


def test_a_deprecated_candidate_is_an_advisory_of_the_rules(tmp_path: Path) -> None:
    case = _case(
        tmp_path, validated=_held(), base=_held(), candidate=_held(metadata__status="deprecated")
    )
    report = case.run()
    _assert_check(
        case,
        report,
        issues=[
            {
                "code": "metric.candidate_deprecated",
                "severity": "advisory",
                "subject": PIN,
                "source": PRODUCT,
                "message": (
                    "in the candidate snapshot, metric contract is deprecated: "
                    "plan a replacement before it is retired"
                ),
            }
        ],
        detail=_head(1) + _rule_clause(["metric.candidate_deprecated"]),
        status=CheckStatus.ADVISORY,
        verdict="pass_with_advisory",
    )
    assert status_of(report, "impact.products") is CheckStatus.BLOCK
    assert report.verdict is Verdict.BLOCK


def test_a_retired_candidate_blocks(tmp_path: Path) -> None:
    case = _case(
        tmp_path, validated=_held(), base=_held(), candidate=_held(metadata__status="retired")
    )
    report = case.run()
    _assert_check(
        case,
        report,
        issues=[
            {
                "code": "metric.candidate_retired",
                "severity": "block",
                "subject": PIN,
                "source": PRODUCT,
                "message": (
                    "in the candidate snapshot, metric contract is retired "
                    "and must not back a decision product"
                ),
            }
        ],
        detail=(
            "1 metric contract(s) resolved, 1 issue(s); "
            "1 of 1 metric pin(s) match the candidate snapshot; "
            "the product's rules give metric.candidate_retired on the candidate's definition of "
            "metric://sales/gross_margin_pct@3"
        ),
        status=CheckStatus.BLOCK,
        verdict="block",
    )
    assert report.verdict is Verdict.BLOCK


@pytest.mark.parametrize("held", [["region"], ["store_id"]])
def test_a_dimension_the_product_splits_by_is_missing_in_the_candidate(
    tmp_path: Path, held: list[str]
) -> None:
    case = _case(tmp_path, validated=_held(), base=_held(), candidate=_held(dimensions=held))
    report = case.run()
    category = {
        "code": "dimension.candidate_not_allowed",
        "severity": "block",
        "subject": "category",
        "source": PRODUCT,
        "message": (
            "in the candidate snapshot, dimension 'category' is not declared by "
            "metric://sales/gross_margin_pct@3"
        ),
    }
    if held == ["region"]:
        issues = [category]
        detail = _head(1) + _rule_clause(["dimension.candidate_not_allowed"])
    else:
        issues = [
            category,
            _rule("dimension.candidate_not_allowed", "block", _undeclared("region"), "region"),
        ]
        detail = (
            "1 metric contract(s) resolved, 2 issue(s); "
            "1 of 1 metric pin(s) match the candidate snapshot; "
            "the product's rules give dimension.candidate_not_allowed on the candidate's "
            "definition of metric://sales/gross_margin_pct@3"
        )
    _assert_check(
        case, report, issues=issues, detail=detail, status=CheckStatus.BLOCK, verdict="block"
    )


def test_a_dimension_the_product_does_not_split_by_is_not_the_rules_concern(
    tmp_path: Path,
) -> None:
    wide = _held(dimensions=["region", "category", "store_id"])
    case = _case(tmp_path, validated=wide, base=wide, candidate=_held())
    report = case.run()
    _assert_check(case, report, issues=[], detail=_head(0), status=CheckStatus.PASS, verdict="pass")
    assert "metric.dimensions_removed" in _diff_codes(case, report)


def test_the_rules_are_answered_in_their_order_on_one_pin(tmp_path: Path) -> None:
    candidate = _held(metadata__status="deprecated", dimensions=["region"], policy_refs=[FINANCE])
    case = _case(tmp_path, validated=_held(), base=_held(), candidate=candidate)
    _assert_check(
        case,
        case.run(),
        issues=[
            _rule("dimension.candidate_not_allowed", "block", _undeclared("category"), "category"),
            _rule("metric.candidate_deprecated", "advisory", DEPRECATED),
            _rule("policy.candidate_mismatch", "review", _governed(FINANCE)),
        ],
        detail=(
            "1 metric contract(s) resolved, 3 issue(s); "
            "1 of 1 metric pin(s) match the candidate snapshot; "
            "the product's rules give dimension.candidate_not_allowed, "
            "metric.candidate_deprecated, policy.candidate_mismatch on the candidate's "
            "definition of metric://sales/gross_margin_pct@3"
        ),
        status=CheckStatus.BLOCK,
        verdict="block",
    )


def test_the_rule_issues_follow_the_pin_issues(tmp_path: Path) -> None:
    refs = _held(metadata__status="deprecated", dimensions=["region"], formula__numerator=NUMERATOR)
    case = _case(tmp_path, validated=_held(), base=refs, candidate=refs)
    report = case.run()
    codes = ["metric.formula_changed", "metric.dimensions_removed", "metric.status_changed"]
    _assert_check(
        case,
        report,
        issues=[
            _content(codes, "block"),
            _rule("dimension.candidate_not_allowed", "block", _undeclared("category"), "category"),
            _rule("metric.candidate_deprecated", "advisory", DEPRECATED),
        ],
        pins=[
            {
                "ref": PIN,
                "candidate_versions": [3],
                "outcome": "content_mismatch",
                "differences": codes,
            }
        ],
        detail=(
            "1 metric contract(s) resolved, 3 issue(s); "
            "0 of 1 metric pin(s) match the candidate snapshot: "
            "it holds another definition of metric://sales/gross_margin_pct at version 3 "
            "than the validated contract (metric.formula_changed, metric.dimensions_removed, "
            "metric.status_changed); "
            "the product's rules give dimension.candidate_not_allowed, "
            "metric.candidate_deprecated on the candidate's definition of "
            "metric://sales/gross_margin_pct@3"
        ),
        status=CheckStatus.BLOCK,
        verdict="block",
    )
    assert report.verdict is Verdict.BLOCK
    assert summary_of(report, "semantic.diff") == "semantic.unchanged"


# --- what the validation report already says ----------------------------------


@pytest.mark.parametrize("kind", ["status", "dimension", "policy"])
def test_an_answer_the_validated_contract_gives_too_is_not_repeated(
    tmp_path: Path, kind: str
) -> None:
    held, expected, status, verdict = {
        "status": (
            _held(metadata__status="draft"),
            _own("metric.draft", "review", DRAFT),
            CheckStatus.REVIEW,
            "needs_review",
        ),
        "dimension": (
            _held(dimensions=["region"]),
            _own("dimension.not_allowed", "block", _undeclared("category"), "category"),
            CheckStatus.BLOCK,
            "block",
        ),
        "policy": (
            _held(policy_refs=[FINANCE]),
            _own("policy.mismatch", "review", _governed(FINANCE)),
            CheckStatus.REVIEW,
            "needs_review",
        ),
    }[kind]
    case = _case(tmp_path, validated=held, base=held, candidate=held)
    _assert_check(
        case, case.run(), issues=[expected], detail=_head(1), status=status, verdict=verdict
    )


@pytest.mark.parametrize("kind", ["status", "dimension", "policy"])
def test_another_answer_of_the_same_rule_is_reported_beside_the_reports_own(
    tmp_path: Path, kind: str
) -> None:
    validated, candidate, issues, status, verdict = {
        "status": (
            _held(metadata__status="draft"),
            _held(metadata__status="deprecated"),
            [
                _own("metric.draft", "review", DRAFT),
                _rule("metric.candidate_deprecated", "advisory", DEPRECATED),
            ],
            CheckStatus.REVIEW,
            "needs_review",
        ),
        "dimension": (
            _held(dimensions=["region"]),
            _held(dimensions=["store_id"]),
            [
                _own("dimension.not_allowed", "block", _undeclared("category"), "category"),
                _rule("dimension.candidate_not_allowed", "block", _undeclared("region"), "region"),
            ],
            CheckStatus.BLOCK,
            "block",
        ),
        "policy": (
            _held(policy_refs=[FINANCE]),
            _held(policy_refs=[RISK, FINANCE]),
            [
                _own("policy.mismatch", "review", _governed(FINANCE)),
                {
                    "code": "policy.candidate_mismatch",
                    "severity": "review",
                    "subject": PIN,
                    "source": PRODUCT,
                    "message": (
                        "in the candidate snapshot, metric is governed by "
                        "policy://finance/close@2, policy://risk/limits@1, "
                        "the product verifies under policy://release/default@1"
                    ),
                },
            ],
            CheckStatus.REVIEW,
            "needs_review",
        ),
    }[kind]
    case = _case(tmp_path, validated=validated, base=validated, candidate=candidate)
    _assert_check(
        case,
        case.run(),
        issues=issues,
        detail=_head(2) + _rule_clause([issues[1]["code"]]),
        status=status,
        verdict=verdict,
    )


@pytest.mark.parametrize("writing", ["order", "repeat"])
def test_one_set_of_governing_policies_written_in_another_way_is_one_answer(
    tmp_path: Path, writing: str
) -> None:
    if writing == "order":
        refs = _held(policy_refs=[FINANCE, RISK])
        case = _case(
            tmp_path, validated=_held(policy_refs=[RISK, FINANCE]), base=refs, candidate=refs
        )
        said = "metric is governed by policy://risk/limits@1, policy://finance/close@2, "
    else:
        validated = _held(policy_refs=[FINANCE])
        case = _case(
            tmp_path,
            validated=validated,
            base=validated,
            candidate=_held(policy_refs=[FINANCE, FINANCE]),
        )
        said = "metric is governed by policy://finance/close@2, "
    _assert_check(
        case,
        case.run(),
        issues=[
            _own(
                "policy.mismatch",
                "review",
                said + "the product verifies under policy://release/default@1",
            )
        ],
        detail=_head(1),
        status=CheckStatus.REVIEW,
        verdict="needs_review",
    )


@pytest.mark.parametrize("kind", ["status", "policy", "beside"])
def test_a_candidate_that_satisfies_a_rule_the_validated_contract_fails_adds_nothing(
    tmp_path: Path, kind: str
) -> None:
    if kind == "status":
        case = _case(
            tmp_path, validated=_held(metadata__status="draft"), base=_held(), candidate=_held()
        )
        _assert_check(
            case,
            case.run(),
            issues=[
                _own("metric.draft", "review", DRAFT),
                _content(["metric.status_changed"], "block"),
            ],
            pins=[
                {
                    "ref": PIN,
                    "candidate_versions": [3],
                    "outcome": "content_mismatch",
                    "differences": ["metric.status_changed"],
                }
            ],
            detail=_head(2, 0) + _pin_clause(["metric.status_changed"]),
            status=CheckStatus.BLOCK,
            verdict="block",
        )
    elif kind == "policy":
        validated = _held(policy_refs=[FINANCE])
        case = _case(tmp_path, validated=validated, base=validated, candidate=_held())
        _assert_check(
            case,
            case.run(),
            issues=[_own("policy.mismatch", "review", _governed(FINANCE))],
            detail=_head(1),
            status=CheckStatus.REVIEW,
            verdict="needs_review",
        )
    else:
        case = _case(
            tmp_path,
            validated=_held(),
            base=_held(),
            candidate=_held(policy_refs=[OWN_POLICY, FINANCE]),
        )
        _assert_check(
            case, case.run(), issues=[], detail=_head(0), status=CheckStatus.PASS, verdict="pass"
        )


# --- pins that are not compared -----------------------------------------------


@pytest.mark.parametrize("kind", ["version", "several", "absent", "unknown", "validated_version"])
def test_a_pin_that_is_not_compared_has_no_rule_issue(tmp_path: Path, kind: str) -> None:
    draft = _held(metadata__status="draft")
    candidate = {
        "version": _held(metadata__version=4, metadata__status="draft"),
        "several": _held(),
    }.get(kind, draft)
    validated = _held(metadata__version=4) if kind == "validated_version" else _held()
    case = _case(tmp_path, validated=validated, base=_held(), candidate=candidate)
    if kind == "several":
        _write_yaml(case.candidate / "metrics/second.yaml", deepcopy(draft))
    elif kind == "absent":
        shutil.rmtree(case.candidate / "metrics")
    elif kind == "unknown":
        (case.contracts / "gross_margin_pct.yaml").unlink()
    issue, pin, clause = {
        "version": (
            _own(
                "metric_ref.candidate_version_mismatch",
                "block",
                f"the candidate snapshot holds version 4 of {ID}; the product pins version 3",
            ),
            {"ref": PIN, "candidate_versions": [4], "outcome": "version_mismatch"},
            f": it holds version 4 of {ID}, pinned at version 3",
        ),
        "several": (
            _own(
                "metric_ref.candidate_ambiguous",
                "block",
                f"the candidate snapshot holds 2 contracts of {ID} (versions 3, 3); "
                "the pin to version 3 does not resolve to one of them",
            ),
            {"ref": PIN, "candidate_versions": [3, 3], "outcome": "ambiguous"},
            f": it holds 2 contracts of {ID} (versions 3, 3), pinned at version 3",
        ),
        "absent": (
            _own(
                "metric_ref.candidate_absent",
                "block",
                f"the candidate snapshot holds no metric {ID}; the product pins version 3",
            ),
            {"ref": PIN, "candidate_versions": [], "outcome": "absent"},
            f": it holds no {ID}, pinned at version 3",
        ),
        "unknown": (
            _own(
                "metric_ref.unknown",
                "block",
                "no metric contract with this id in the validated set",
            ),
            MATCH[0],
            "",
        ),
        "validated_version": (
            _own(
                "metric_ref.version_mismatch",
                "block",
                f"the repository holds version 4 of {ID}; the pinned version is gone",
            ),
            MATCH[0],
            "",
        ),
    }[kind]
    report = case.run()
    _assert_check(
        case,
        report,
        issues=[issue],
        pins=[pin],
        detail=(
            f"{0 if kind == 'unknown' else 1} metric contract(s) resolved, 1 issue(s); "
            f"{1 if pin == MATCH[0] else 0} of 1 metric pin(s) match the candidate snapshot"
        )
        + clause,
        status=CheckStatus.BLOCK,
        verdict="block",
    )
    payload = _check_payload(case.root, report, "contracts.validate")
    assert not [i for i in payload["issues"] if i["code"].startswith(RULE_FAMILIES)]


def test_gate_inputs_without_validated_contracts_have_no_rule_issue(tmp_path: Path) -> None:
    case = _case(
        tmp_path, validated=_held(), base=_held(), candidate=_held(metadata__status="draft")
    )
    loaded = load_contracts([case.contracts])
    run_ids = []
    for compared in [False, True]:
        case.root = tmp_path / ("compared" if compared else "without")
        report = run_gate(
            _gate_inputs(case, loaded, compared=compared),
            store=FilesystemArtifactStore(case.root / "evidence"),
            created_at="2026-08-25T09:00:00Z",
        )
        run_ids.append(report.run_id)
        if compared:
            _assert_check(
                case,
                report,
                issues=[_rule("metric.candidate_draft", "review", DRAFT)],
                detail=_head(1) + _rule_clause(["metric.candidate_draft"]),
                status=CheckStatus.REVIEW,
                verdict="needs_review",
            )
        else:
            _assert_check(
                case, report, issues=[], detail=_head(0), status=CheckStatus.PASS, verdict="pass"
            )
    assert run_ids[0] == run_ids[1]


def test_several_pins_are_answered_one_by_one_in_declared_order(tmp_path: Path) -> None:
    case = _case(
        tmp_path,
        validated=_held(),
        base=_held(),
        candidate=_held(metadata__status="retired", dimensions=["region"]),
    )
    product = product_contract()
    product["scope"]["metrics"] = ["metric://sales/revenue@2", PIN, "metric://sales/units@1"]
    _write_yaml(case.contracts / "weekly_margin.yaml", product)
    for name, version, status in [("revenue", 2, "draft"), ("units", 1, "active")]:
        contract = revenue_metric()
        contract["metadata"].update(id=f"metric://sales/{name}", version=version)
        _write_yaml(case.contracts / f"{name}.yaml", deepcopy(contract))
        _write_yaml(case.base / f"metrics/{name}.yaml", deepcopy(contract))
        held = deepcopy(contract)
        held["metadata"]["status"] = status
        _write_yaml(case.candidate / f"metrics/{name}.yaml", held)
    _assert_check(
        case,
        case.run(),
        issues=[
            _rule("metric.candidate_draft", "review", DRAFT, "metric://sales/revenue@2"),
            _rule("dimension.candidate_not_allowed", "block", _undeclared("category"), "category"),
            _rule("metric.candidate_retired", "block", RETIRED),
        ],
        pins=[
            {"ref": "metric://sales/revenue@2", "candidate_versions": [2], "outcome": "match"},
            {"ref": PIN, "candidate_versions": [3], "outcome": "match"},
            {"ref": "metric://sales/units@1", "candidate_versions": [1], "outcome": "match"},
        ],
        detail=(
            "3 metric contract(s) resolved, 3 issue(s); "
            "3 of 3 metric pin(s) match the candidate snapshot; "
            "the product's rules give metric.candidate_draft on the candidate's definition of "
            "metric://sales/revenue@2; "
            "the product's rules give dimension.candidate_not_allowed, metric.candidate_retired "
            "on the candidate's definition of metric://sales/gross_margin_pct@3"
        ),
        status=CheckStatus.BLOCK,
        verdict="block",
    )


def test_the_validation_report_is_not_modified_and_decision_validate_is_unchanged(
    tmp_path: Path,
) -> None:
    case = _case(
        tmp_path, validated=_held(), base=_held(), candidate=_held(metadata__status="draft")
    )
    loaded = load_contracts([case.contracts])
    before = loaded.report.model_dump()
    report = run_gate(
        _gate_inputs(case, loaded),
        store=FilesystemArtifactStore(tmp_path / "evidence"),
        created_at="2026-08-25T09:00:00Z",
    )
    assert status_of(report, "contracts.validate") is CheckStatus.REVIEW
    assert loaded.report.model_dump() == before == validate_paths([case.contracts]).model_dump()
    assert loaded.report.issues == []
    assert loaded.report.verdict is Verdict.PASS


# --- controls against a false reject ------------------------------------------


@pytest.mark.parametrize("base_holds", ["validated", "candidate"])
@pytest.mark.parametrize(
    "change",
    [
        {"time__timezone": "UTC"},
        {"formula__numerator": NUMERATOR},
        {"metadata__description": "another wording"},
        {"dimensions": ["region", "category", "store_id"]},
        {"policy_refs": []},
        {"classification": "confidential"},
    ],
    ids=["timezone", "numerator", "description", "dimension_added", "policy_removed", "classified"],
)
def test_a_change_the_rules_do_not_read_gives_no_rule_issue(
    tmp_path: Path, change: dict[str, Any], base_holds: str
) -> None:
    candidate = _held(**change)
    case = _case(
        tmp_path,
        validated=_held(),
        base=_held() if base_holds == "validated" else candidate,
        candidate=candidate,
    )
    report = case.run()
    payload = _check_payload(case.root, report, "contracts.validate")
    assert not [i for i in payload["issues"] if i["code"].startswith(RULE_FAMILIES)]
    assert "the product's rules give" not in detail_of(report, "contracts.validate")
    if base_holds == "validated":
        _assert_check(
            case, report, issues=[], detail=_head(0), status=CheckStatus.PASS, verdict="pass"
        )
    else:
        assert [i["code"] for i in payload["issues"]] == ["metric_ref.candidate_content_mismatch"]


@pytest.mark.parametrize("status", ["active", "draft", "deprecated"])
def test_a_validated_contract_that_is_the_candidates_own_gives_no_rule_issue(
    tmp_path: Path, status: str
) -> None:
    held = _held(metadata__status=status)
    case = _case(tmp_path, validated=held, base=_held(), candidate=held)
    own = {
        "active": [],
        "draft": [_own("metric.draft", "review", DRAFT)],
        "deprecated": [_own("metric.deprecated", "advisory", DEPRECATED)],
    }[status]
    _assert_check(
        case,
        case.run(),
        issues=own,
        detail=_head(len(own)),
        status={
            "active": CheckStatus.PASS,
            "draft": CheckStatus.REVIEW,
            "deprecated": CheckStatus.ADVISORY,
        }[status],
        verdict={
            "active": "pass",
            "draft": "needs_review",
            "deprecated": "pass_with_advisory",
        }[status],
    )


def test_verifier_and_check_versions() -> None:
    assert VERIFIER_VERSION == "90"
    assert CHECK_VERSION == "66"
