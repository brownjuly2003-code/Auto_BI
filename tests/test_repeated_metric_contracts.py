"""Two contracts of one metric id in a semantic ref, through the real offline release gate.

Durable spec: docs/specs/repeated-metric-contracts.md. A diff keeps the last node of a metric
id and the gate reads the first, and until verifier 117 which node was last was the order of
the files: an older contract of the pinned metric beside the current one blocked the release
from `metrics/` and let it pass from `metrics/archive/`. Such an id is now the blocking gap
`gap.metric_duplicate`, and the checks a contract that did not load leaves not run are not run.
"""

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from auto_bi.evidence.canonical import digest_bytes
from auto_bi.semantic.gateway import NativeModelSource
from auto_bi.verification.environment import EnvironmentFacts, EnvironmentQuestions
from auto_bi.verification.model import Verdict
from auto_bi.verification.release_gate import CheckStatus, GateReport
from tests.test_decision_verify import (
    Case,
    _check_payload,
    _formula_change,
    _write_yaml,
    detail_of,
    environment,
    metric_contract,
    revenue_metric,
    status_of,
    summary_of,
)

GROSS_MARGIN = "metric://sales/gross_margin_pct"
REVENUE = "metric://sales/revenue"
DUPLICATE = (
    "a semantic ref holds more than one contract of a metric id, so it does not say "
    "which of them a diff should compare"
)
UNLOADED = (
    "a metric contract did not load, so the snapshot does not represent its source; "
    "a diff would report the parse failure as a removed metric"
)
NOT_RUN = [
    "semantic.diff",
    "impact.products",
    "assets.invalidated",
    "results.reconcile",
    "results.provenance",
    "sql.compile",
]


def _at(contract: dict[str, Any], version: int) -> dict[str, Any]:
    contract["metadata"]["version"] = version
    return contract


def _gap(metric_id: str, versions: str) -> dict[str, Any]:
    return {
        "code": "gap.metric_duplicate",
        "severity": "block",
        "path": f"metrics.{metric_id}",
        "reason": (
            f"metric is defined by 2 contracts (versions {versions}): "
            "which of them the ref means is not known"
        ),
        "source_value_ref": "",
    }


def _assert_not_run(report: GateReport, reason: str = DUPLICATE) -> None:
    """The six checks a contract that did not load leaves not run, and no other check."""
    not_run = [check.check_id for check in report.checks if check.summary_code == "check.not_run"]
    assert not_run == NOT_RUN
    for check_id in NOT_RUN:
        assert status_of(report, check_id) is CheckStatus.REVIEW
        assert detail_of(report, check_id) == reason
    assert status_of(report, "semantic.gaps") is CheckStatus.BLOCK
    assert summary_of(report, "semantic.gaps") == "semantic.gaps_present"
    assert report.verdict is Verdict.BLOCK


def test_an_older_contract_beside_the_pinned_one_blocks_wherever_its_file_lies(
    tmp_path: Path,
) -> None:
    """Verifier 110 blocked this base from `metrics/` and passed the same bytes moved to
    `metrics/archive/`."""
    reports = []
    for layout in ("flat", "below"):
        place = "archive/" * (layout == "below") + "gross_margin_pct_old.yaml"
        case = Case(tmp_path / layout)
        _write_yaml(case.base / "metrics" / place, _at(_formula_change(), 2))

        report = case.run()

        _assert_not_run(report)
        assert _check_payload(case.root, report, "semantic.gaps") == {
            "base": [_gap(GROSS_MARGIN, "2, 3")],
            "candidate": [],
        }
        assert status_of(report, "evidence.bundle") is CheckStatus.PASS
        reports.append(report)
    # Verifier 116 kept the nodes in the order of their files, and the two bases were two
    # snapshots; since 117 they stand in the order of their versions, wherever the files lie.
    assert reports[0].base_digest == reports[1].base_digest
    assert reports[0].run_id == reports[1].run_id


@pytest.mark.parametrize("side", ["base", "candidate"])
@pytest.mark.parametrize("metric", [GROSS_MARGIN, REVENUE])
@pytest.mark.parametrize("copy", [False, True], ids=["versions", "copies"])
def test_two_contracts_of_one_id_are_one_gap_and_two_nodes(
    tmp_path: Path, side: str, metric: str, copy: bool
) -> None:
    """The pinned metric or one no pin names, in either ref, at two versions or byte for byte."""
    case = Case(tmp_path).with_candidate()
    ref = case.base if side == "base" else case.candidate
    first = metric_contract() if metric == GROSS_MARGIN else revenue_metric()
    name = f"{metric.rsplit('/', 1)[1]}.yaml"
    _write_yaml(ref / "metrics" / name, first)
    _write_yaml(ref / "metrics" / "archive" / name, first if copy else _at(deepcopy(first), 4))
    versions = [3, 3] if copy else [3, 4]

    report = case.run()

    _assert_not_run(report)
    gap = _gap(metric, ", ".join(str(version) for version in versions))
    assert _check_payload(tmp_path, report, "semantic.gaps") == {
        "base": [gap] if side == "base" else [],
        "candidate": [gap] if side == "candidate" else [],
    }
    snapshot = NativeModelSource(ref / "model.yaml", ref / "metrics").snapshot()
    assert sorted(node.version for node in snapshot.graph.metrics if node.id == metric) == versions
    issues = _check_payload(tmp_path, report, "contracts.validate")["issues"]
    pinned_twice = side == "candidate" and metric == GROSS_MARGIN
    assert [issue["code"] for issue in issues] == (
        ["metric_ref.candidate_ambiguous"] if pinned_twice else []
    )


def test_a_pin_the_candidate_does_not_hold_is_absent_beside_another_metrics_duplicate(
    tmp_path: Path,
) -> None:
    """Two contracts of revenue say nothing of whether a contract of the pinned metric loaded."""
    case = Case(tmp_path).with_candidate()
    (case.candidate / "metrics" / "gross_margin_pct.yaml").unlink()
    _write_yaml(case.candidate / "metrics" / "revenue.yaml", revenue_metric())
    _write_yaml(case.candidate / "metrics" / "archive" / "revenue.yaml", revenue_metric())

    report = case.run()

    _assert_not_run(report)
    payload = _check_payload(tmp_path, report, "contracts.validate")
    assert payload["candidate_pins"] == [
        {"ref": f"{GROSS_MARGIN}@3", "candidate_versions": [], "outcome": "absent"}
    ]
    assert [issue["code"] for issue in payload["issues"]] == ["metric_ref.candidate_absent"]


@pytest.mark.parametrize("budgeted_first", [True, False], ids=["read-first", "read-last"])
def test_with_an_engine_the_environment_is_asked_about_the_contract_read_first(
    tmp_path: Path, budgeted_first: bool
) -> None:
    """A known boundary: step 7 asks about a pinned metric by its id, so of two contracts of it
    in the candidate it asks about the one a lookup of the id finds — of one version, the one of
    the lower digest of bytes, wherever its file lies (verifier 117). The run blocks either way."""
    case = Case(tmp_path).with_candidate()
    budgeted = metric_contract()
    budgeted["budgets"] = {"max_scanned_rows": 1000}
    name = "a_gross_margin_pct.yaml" if budgeted_first else "z_gross_margin_pct.yaml"
    _write_yaml(case.candidate / "metrics" / name, budgeted)
    asked: list[EnvironmentQuestions] = []

    def probe(questions: EnvironmentQuestions) -> EnvironmentFacts:
        asked.append(questions)
        return environment()

    report = case.run(probe=probe)

    _assert_not_run(report)
    metrics = case.candidate / "metrics"
    found_first = digest_bytes((metrics / name).read_bytes()) < digest_bytes(
        (metrics / "gross_margin_pct.yaml").read_bytes()
    )
    assert [plan.metric_ref for plan in asked[0].plans] == (
        [f"{GROSS_MARGIN}@3"] if found_first else []
    )
    assert (summary_of(report, "dwh.cost") == "cost.no_budget_declared") is not found_first


def test_a_contract_that_did_not_load_and_a_duplicate_are_both_named(tmp_path: Path) -> None:
    """The contract that did not load is not counted among the contracts of the id."""
    case = Case(tmp_path).with_candidate()
    (case.candidate / "metrics" / "broken.yaml").write_text(
        "kind: MetricContract\nmetadata: {id: metric://sales/gross_margin_pct}\n", encoding="utf-8"
    )
    _write_yaml(case.candidate / "metrics" / "archive" / "gross_margin_pct.yaml", metric_contract())

    report = case.run()

    _assert_not_run(report, f"{UNLOADED}; {DUPLICATE}")
    gaps = _check_payload(tmp_path, report, "semantic.gaps")["candidate"]
    assert [gap["code"] for gap in gaps] == ["gap.contract_invalid", "gap.metric_duplicate"]
    assert gaps[1] == _gap(GROSS_MARGIN, "3, 3")
