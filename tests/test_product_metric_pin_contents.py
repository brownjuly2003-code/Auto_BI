"""Pinned metric definitions compared through the offline release gate."""

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
    shown,
    status_of,
    summary_of,
)

PIN = "metric://sales/gross_margin_pct@3"
ID = "metric://sales/gross_margin_pct"
PRODUCT = "decision://sales/weekly_margin@1"
JOIN = {"left": "dm.sales_daily.store_id", "right": "dm.stores.id", "type": "many_to_one"}
NUMERATOR = {"table": "dm.sales_daily", "column": "revenue", "agg": "sum"}
CLEAN = (
    "1 metric contract(s) resolved, 0 issue(s); 1 of 1 metric pin(s) match the candidate snapshot"
)
DRAFT = {
    "code": "metric.draft",
    "severity": "review",
    "subject": PIN,
    "source": PRODUCT,
    "message": "metric contract is still a draft: its definition is not agreed yet",
}
# What the product's rules add on the candidate's definition (tests/test_product_metric_pin_rules).
NOT_ALLOWED = {
    "code": "dimension.candidate_not_allowed",
    "severity": "block",
    "subject": "category",
    "source": PRODUCT,
    "message": f"in the candidate snapshot, dimension 'category' is not declared by {PIN}",
}
DEPRECATED = {
    "code": "metric.candidate_deprecated",
    "severity": "advisory",
    "subject": PIN,
    "source": PRODUCT,
    "message": (
        "in the candidate snapshot, metric contract is deprecated: "
        "plan a replacement before it is retired"
    ),
}
GRADES = {
    "block": (CheckStatus.BLOCK, "block"),
    "review": (CheckStatus.REVIEW, "needs_review"),
    "advisory": (CheckStatus.ADVISORY, "pass_with_advisory"),
}


def _copies(tmp_path: Path, validated: dict[str, Any], refs: dict[str, Any]) -> Case:
    case = Case(tmp_path).with_candidate(contract=deepcopy(refs))
    _write_yaml(case.contracts / "gross_margin_pct.yaml", deepcopy(validated))
    _write_yaml(case.base / "metrics/gross_margin_pct.yaml", deepcopy(refs))
    return case


def _clause(codes: list[str]) -> str:
    return (
        f"it holds another definition of {ID} at version 3 "
        f"than the validated contract ({', '.join(codes)})"
    )


def _issue(codes: list[str], severity: str = "block") -> dict[str, Any]:
    return {
        "code": "metric_ref.candidate_content_mismatch",
        "severity": severity,
        "subject": PIN,
        "source": PRODUCT,
        "message": (
            f"the candidate snapshot holds another definition of {ID} at version 3 "
            f"than the validated contract ({', '.join(codes)}), "
            "and the base snapshot does not hold the validated definition"
        ),
    }


def _assert_check(
    case: Case,
    report: GateReport,
    *,
    issues: list[dict[str, Any]] | None = None,
    pins: list[dict[str, Any]] | None = None,
    detail: str = CLEAN,
    status: CheckStatus = CheckStatus.PASS,
    verdict: str = "pass",
) -> None:
    assert status_of(report, "contracts.validate") is status
    assert summary_of(report, "contracts.validate") == (
        "contracts.issues" if issues else "contracts.clean"
    )
    assert detail_of(report, "contracts.validate") == detail
    payload = _check_payload(case.root, report, "contracts.validate")
    assert payload["issues"] == (issues or [])
    assert payload["candidate_pins"] == (
        pins if pins is not None else [{"ref": PIN, "candidate_versions": [3], "outcome": "match"}]
    )
    assert payload["verdict"] == verdict


def _assert_content(
    case: Case,
    report: GateReport,
    codes: list[str],
    severity: str = "block",
    own: list[dict[str, Any]] | None = None,
    rules: list[dict[str, Any]] | None = None,
) -> None:
    issues = [*(own or []), _issue(codes, severity), *(rules or [])]
    detail = (
        f"1 metric contract(s) resolved, {len(issues)} issue(s); "
        "0 of 1 metric pin(s) match the candidate snapshot: "
    ) + _clause(codes)
    if rules:
        named = ", ".join(dict.fromkeys(rule["code"] for rule in rules))
        detail += f"; the product's rules give {named} on the candidate's definition of {PIN}"
    severities = {issue["severity"] for issue in issues}
    status, verdict = next(grade for name, grade in GRADES.items() if name in severities)
    _assert_check(
        case,
        report,
        issues=issues,
        pins=[
            {
                "ref": PIN,
                "candidate_versions": [3],
                "outcome": "content_mismatch",
                "differences": codes,
            }
        ],
        detail=detail,
        status=status,
        verdict=verdict,
    )


def _variant(name: str) -> tuple[dict[str, Any], dict[str, Any], str]:
    validated, refs = metric_contract(), metric_contract()
    code = name
    if name == "formula_changed":
        refs["formula"]["numerator"] = deepcopy(NUMERATOR)
    elif name == "grain_changed":
        refs["grain"]["keys"] = ["date"]
    elif name == "time_changed":
        refs["time"]["timezone"] = "UTC"
    elif name == "unit_changed":
        refs["unit"]["precision"] = 2
    elif name == "additivity_changed":
        refs.update(additivity="semi_additive", non_additive_axes=["date"])
    elif name == "dimensions_removed":
        refs["dimensions"] = ["region"]
    elif name == "dimensions_added":
        refs["dimensions"] = ["region", "category", "store_id"]
    elif name in {"join_path_removed", "join_path_changed"}:
        validated["join_paths"] = [deepcopy(JOIN)]
        if name == "join_path_changed":
            refs["join_paths"] = [{**JOIN, "type": "one_to_one"}]
    elif name == "join_path_added":
        refs["join_paths"] = [deepcopy(JOIN)]
    elif name == "policy_weakened":
        refs["policy_refs"] = []
    elif name == "policy_added":
        validated["policy_refs"] = []
    elif name.startswith("classification_"):
        refs["classification"] = "public" if name.endswith("weakened") else "confidential"
    elif name == "deprecated":
        refs["metadata"]["status"] = name
        code = "status_changed"
    elif name.startswith("budget_"):
        validated["budgets"] = {"max_scanned_rows": 1000}
        code = "budget_relaxed" if "relaxed" in name else "budget_tightened"
        if name == "budget_relaxed_none":
            pass
        else:
            refs["budgets"] = {"max_scanned_rows": 5000 if "relaxed" in name else 500}
        if name == "budget_tightened_new":
            validated.pop("budgets")
    elif name.startswith("freshness_"):
        refs["freshness"] = {
            "max_age": {
                "freshness_relaxed": "PT12H",
                "freshness_restated": "PT360M",
                "freshness_tightened": "PT1H",
            }[name]
        }
    elif name == "description_changed":
        refs["metadata"]["description"] = "another wording"
    elif name == "owner_changed":
        refs["metadata"]["owner"] = "finance"
    else:
        raise AssertionError(name)
    return validated, refs, "metric." + code


def test_a_validated_contract_that_neither_snapshot_holds_blocks_the_release(
    tmp_path: Path,
) -> None:
    refs = metric_contract()
    refs["formula"]["numerator"] = deepcopy(NUMERATOR)
    refs["dimensions"] = ["region"]
    refs["metadata"]["status"] = "deprecated"
    case = _copies(tmp_path, metric_contract(), refs)
    report = case.run()
    codes = ["metric.formula_changed", "metric.dimensions_removed", "metric.status_changed"]
    _assert_content(case, report, codes, rules=[NOT_ALLOWED, DEPRECATED])
    assert report.verdict is Verdict.BLOCK
    assert summary_of(report, "semantic.diff") == "semantic.unchanged"
    assert summary_of(report, "sql.compile") == "sql.compiled"
    summary = (tmp_path / "evidence" / report.run_id / "reports/summary.md").read_text(
        encoding="utf-8"
    )
    stops = summary.split("What stops the release", 1)[1].split("\n## ", 1)[0]
    assert any(
        "contracts.validate" in line and detail_of(report, "contracts.validate") in line
        for line in shown(stops).splitlines()
    )


@pytest.mark.parametrize(
    "name",
    [
        "formula_changed",
        "grain_changed",
        "time_changed",
        "unit_changed",
        "additivity_changed",
        "dimensions_removed",
        "join_path_removed",
        "join_path_changed",
        "policy_weakened",
        "classification_weakened",
        "deprecated",
        "budget_relaxed",
        "budget_relaxed_none",
        "freshness_relaxed",
    ],
)
def test_a_difference_that_changes_meaning_or_weakens_a_promise_blocks(
    tmp_path: Path, name: str
) -> None:
    validated, refs, code = _variant(name)
    case = _copies(tmp_path, validated, refs)
    report = case.run()
    # the dimension is one the product splits by; a deprecated metric is the rules' advisory
    rules = {"dimensions_removed": [NOT_ALLOWED], "deprecated": [DEPRECATED]}.get(name)
    _assert_content(case, report, [code], rules=rules)
    assert report.verdict is Verdict.BLOCK


@pytest.mark.parametrize(
    "name",
    [
        "description_changed",
        "owner_changed",
        "dimensions_added",
        "classification_tightened",
        "freshness_restated",
        "freshness_tightened",
        "join_path_added",
        "budget_tightened",
        "budget_tightened_new",
        "policy_added",
    ],
)
def test_a_difference_of_labels_alone_is_an_advisory(tmp_path: Path, name: str) -> None:
    validated, refs, code = _variant(name)
    case = _copies(tmp_path, validated, refs)
    report = case.run()
    _assert_content(case, report, [code], "advisory")
    assert report.verdict is Verdict.PASS_WITH_ADVISORY


def test_differences_of_both_kinds_block_and_are_all_named(tmp_path: Path) -> None:
    validated, refs, code = _variant("formula_changed")
    refs["metadata"]["description"] = "another wording"
    case = _copies(tmp_path, validated, refs)
    _assert_content(case, case.run(), [code, "metric.description_changed"])


@pytest.mark.parametrize(
    "name",
    ["time_changed", "formula_changed", "description_changed", "dimensions_added"],
)
def test_the_change_under_review_is_not_reported_a_second_time(tmp_path: Path, name: str) -> None:
    validated, refs, code = _variant(name)
    case = _copies(tmp_path, validated, refs)
    _write_yaml(case.base / "metrics/gross_margin_pct.yaml", deepcopy(validated))
    report = case.run()
    _assert_check(case, report)
    assert summary_of(report, "semantic.diff") == "semantic.changed"
    assert code in [
        change["code"] for change in _check_payload(tmp_path, report, "semantic.diff")["changes"]
    ]


def test_a_validated_contract_the_candidate_holds_is_a_match(tmp_path: Path) -> None:
    old, refs, _ = _variant("time_changed")
    case = _copies(tmp_path, refs, refs)
    _write_yaml(case.base / "metrics/gross_margin_pct.yaml", deepcopy(old))
    report = case.run()
    _assert_check(case, report)
    assert "metric.time_changed" in [
        change["code"] for change in _check_payload(tmp_path, report, "semantic.diff")["changes"]
    ]


@pytest.mark.parametrize("writing", ["dimensions", "policies", "joins", "quality", "keys"])
def test_another_writing_of_one_definition_is_a_match(tmp_path: Path, writing: str) -> None:
    validated, refs = metric_contract(), metric_contract()
    if writing == "dimensions":
        validated["dimensions"] = ["category", "region"]
    elif writing == "policies":
        validated["policy_refs"] = ["policy://release/default@1", "policy://finance/close@2"]
        refs["policy_refs"] = list(reversed(validated["policy_refs"]))
    elif writing == "joins":
        validated["join_paths"] = [deepcopy(JOIN), deepcopy(JOIN)]
        refs["join_paths"] = [deepcopy(JOIN)]
    elif writing == "quality":
        validated["freshness"] = {"max_age": "PT6H", "quality_preconditions": ["loaded", "loaded"]}
        refs["freshness"] = {"max_age": "PT6H", "quality_preconditions": ["loaded"]}
    else:
        validated = dict(reversed(list(validated.items())))
    case = _copies(tmp_path, validated, refs)
    _assert_check(case, case.run())


@pytest.mark.parametrize("base_kind", ["numerator", "version", "several", "none", "invalid"])
def test_a_base_that_does_not_hold_the_validated_definition_accounts_for_nothing(
    tmp_path: Path, base_kind: str
) -> None:
    validated, refs, code = _variant("time_changed")
    case = _copies(tmp_path, validated, refs)
    base = metric_contract()
    if base_kind == "numerator":
        base["formula"]["numerator"] = deepcopy(NUMERATOR)
    elif base_kind == "version":
        base["metadata"]["version"] = 2
    _write_yaml(case.base / "metrics/gross_margin_pct.yaml", deepcopy(base))
    if base_kind == "several":
        _write_yaml(case.base / "metrics/second.yaml", metric_contract())
    elif base_kind == "none":
        shutil.rmtree(case.base / "metrics")
    elif base_kind == "invalid":
        _invalid(case.base / "metrics/gross_margin_pct.yaml")
    _assert_content(case, case.run(), [code])


@pytest.mark.parametrize("kind", ["version", "several"])
def test_the_version_and_the_number_of_contracts_are_graded_before_the_contents(
    tmp_path: Path, kind: str
) -> None:
    validated, refs, _ = _variant("formula_changed")
    if kind == "version":
        refs["metadata"]["version"] = 4
    case = _copies(tmp_path, validated, refs)
    if kind == "several":
        _write_yaml(case.candidate / "metrics/second.yaml", metric_contract())
    outcome = "version_mismatch" if kind == "version" else "ambiguous"
    message = (
        f"the candidate snapshot holds version 4 of {ID}; the product pins version 3"
        if kind == "version"
        else (
            f"the candidate snapshot holds 2 contracts of {ID} (versions 3, 3); "
            "the pin to version 3 does not resolve to one of them"
        )
    )
    clause = (
        f"it holds version 4 of {ID}, pinned at version 3"
        if kind == "version"
        else f"it holds 2 contracts of {ID} (versions 3, 3), pinned at version 3"
    )
    _assert_check(
        case,
        case.run(),
        issues=[
            {
                "code": "metric_ref.candidate_" + outcome,
                "severity": "block",
                "subject": PIN,
                "source": PRODUCT,
                "message": message,
            }
        ],
        pins=[
            {
                "ref": PIN,
                "candidate_versions": [4] if kind == "version" else [3, 3],
                "outcome": outcome,
            }
        ],
        status=CheckStatus.BLOCK,
        verdict="block",
        detail=(
            "1 metric contract(s) resolved, 1 issue(s); "
            "0 of 1 metric pin(s) match the candidate snapshot: "
        )
        + clause,
    )


@pytest.mark.parametrize("kind", ["absent", "version"])
def test_a_pin_the_validated_set_does_not_resolve_is_not_compared(
    tmp_path: Path, kind: str
) -> None:
    validated, refs, _ = _variant("formula_changed")
    case = _copies(tmp_path, validated, refs)
    if kind == "absent":
        (case.contracts / "gross_margin_pct.yaml").unlink()
    else:
        validated["metadata"]["version"] = 4
        _write_yaml(case.contracts / "gross_margin_pct.yaml", deepcopy(validated))
    own = [
        {
            "code": "metric_ref.unknown" if kind == "absent" else "metric_ref.version_mismatch",
            "severity": "block",
            "subject": PIN,
            "source": PRODUCT,
            "message": (
                "no metric contract with this id in the validated set"
                if kind == "absent"
                else f"the repository holds version 4 of {ID}; the pinned version is gone"
            ),
        }
    ]
    _assert_check(
        case,
        case.run(),
        issues=own,
        status=CheckStatus.BLOCK,
        verdict="block",
        detail=(
            f"{0 if kind == 'absent' else 1} metric contract(s) resolved, 1 issue(s); "
            "1 of 1 metric pin(s) match the candidate snapshot"
        ),
    )


def test_gate_inputs_without_validated_contracts_are_not_compared(tmp_path: Path) -> None:
    validated, refs, code = _variant("formula_changed")
    case = _copies(tmp_path, validated, refs)
    loaded = load_contracts([case.contracts])
    before = loaded.report.model_dump()
    run_ids = []
    for compared in [False, True]:
        inputs = GateInputs(
            product=next(iter(loaded.products.values())),
            validation=loaded.report,
            base=NativeModelSource(case.base / "model.yaml", case.base / "metrics").snapshot(),
            candidate=NativeModelSource(
                case.candidate / "model.yaml", case.candidate / "metrics"
            ).snapshot(),
            candidate_model=semantic_model(),
            **({"validated_metrics": loaded.metrics} if compared else {}),
        )
        case.root = tmp_path / ("compared" if compared else "without")
        report = run_gate(
            inputs,
            store=FilesystemArtifactStore(case.root / "evidence"),
            created_at="2026-08-25T09:00:00Z",
        )
        run_ids.append(report.run_id)
        if compared:
            _assert_content(case, report, [code])
        else:
            _assert_check(case, report)
    assert run_ids[0] != run_ids[1]
    assert loaded.report.model_dump() == before == validate_paths([case.contracts]).model_dump()


def test_the_validation_reports_own_issues_come_before_the_content_issue(tmp_path: Path) -> None:
    validated = metric_contract()
    validated["metadata"]["status"] = "draft"
    case = _copies(tmp_path, validated, metric_contract())
    _assert_content(case, case.run(), ["metric.status_changed"], own=[DRAFT])


def test_several_pins_are_graded_one_by_one_in_declared_order(tmp_path: Path) -> None:
    validated, refs, code = _variant("formula_changed")
    case = _copies(tmp_path, validated, refs)
    product = product_contract()
    product["scope"]["metrics"] = ["metric://sales/revenue@2", PIN, "metric://sales/units@1"]
    _write_yaml(case.contracts / "weekly_margin.yaml", product)
    for name, version in [("revenue", 2), ("units", 1)]:
        contract = revenue_metric()
        contract["metadata"].update(id=f"metric://sales/{name}", version=version)
        _write_yaml(case.contracts / f"{name}.yaml", deepcopy(contract))
        if name == "revenue":
            for root in [case.base, case.candidate]:
                _write_yaml(root / "metrics/revenue.yaml", deepcopy(contract))
    _assert_check(
        case,
        case.run(),
        issues=[
            _issue([code]),
            {
                "code": "metric_ref.candidate_absent",
                "severity": "block",
                "subject": "metric://sales/units@1",
                "source": PRODUCT,
                "message": (
                    "the candidate snapshot holds no metric metric://sales/units; "
                    "the product pins version 1"
                ),
            },
        ],
        pins=[
            {"ref": "metric://sales/revenue@2", "candidate_versions": [2], "outcome": "match"},
            {
                "ref": PIN,
                "candidate_versions": [3],
                "outcome": "content_mismatch",
                "differences": [code],
            },
            {"ref": "metric://sales/units@1", "candidate_versions": [], "outcome": "absent"},
        ],
        status=CheckStatus.BLOCK,
        verdict="block",
        detail=(
            "3 metric contract(s) resolved, 2 issue(s); "
            "1 of 3 metric pin(s) match the candidate snapshot: "
        )
        + _clause([code])
        + "; it holds no metric://sales/units, pinned at version 1",
    )


def _invalid(path: Path) -> None:
    path.write_text(
        "kind: MetricContract\nmetadata: {id: metric://sales/gross_margin_pct}\n", encoding="utf-8"
    )


def test_a_snapshot_with_an_unloaded_contract_still_has_the_metric_it_holds_compared(
    tmp_path: Path,
) -> None:
    validated, refs, code = _variant("formula_changed")
    case = _copies(tmp_path, validated, refs)
    _invalid(case.candidate / "metrics/invalid.yaml")
    report = case.run()
    _assert_content(case, report, [code])
    assert status_of(report, "semantic.gaps") is CheckStatus.BLOCK


def test_the_validation_report_is_not_modified_and_decision_validate_is_unchanged(
    tmp_path: Path,
) -> None:
    refs = metric_contract()
    refs["formula"]["numerator"] = deepcopy(NUMERATOR)
    refs["dimensions"] = ["region"]
    refs["metadata"]["status"] = "deprecated"
    case = _copies(tmp_path, metric_contract(), refs)
    loaded = load_contracts([case.contracts])
    before = loaded.report.model_dump()
    report = run_gate(
        GateInputs(
            product=next(iter(loaded.products.values())),
            validation=loaded.report,
            base=NativeModelSource(case.base / "model.yaml", case.base / "metrics").snapshot(),
            candidate=NativeModelSource(
                case.candidate / "model.yaml", case.candidate / "metrics"
            ).snapshot(),
            candidate_model=semantic_model(),
            validated_metrics=loaded.metrics,
        ),
        store=FilesystemArtifactStore(tmp_path / "evidence"),
        created_at="2026-08-25T09:00:00Z",
    )
    _assert_content(
        case,
        report,
        ["metric.formula_changed", "metric.dimensions_removed", "metric.status_changed"],
        rules=[NOT_ALLOWED, DEPRECATED],
    )
    assert loaded.report.model_dump() == before == validate_paths([case.contracts]).model_dump()
    assert loaded.report.issues == []
    assert loaded.report.verdict is Verdict.PASS
