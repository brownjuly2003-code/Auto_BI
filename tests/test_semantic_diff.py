"""Semantic diff between two snapshots (DECISIONOPS_ARCHITECTURE §8.3).

The classification table is the contract under test: a removal or a weakened policy blocks
on its own, a formula/grain/time/additivity change is `review` until impact says otherwise,
and a label change is advisory. Escalating `review` to `block` is the job of the layer that
knows which decision products depend on the metric (`tests/test_decision_impact.py`) —
`semantic` never imports `decision`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from auto_bi.semantic.diff import ChangeKind, SemanticDiff, diff_snapshots
from auto_bi.semantic.gateway import SemanticSnapshot
from auto_bi.semantic.model import (
    Aggregation,
    Column,
    ColumnRole,
    DataClassification,
    Join,
    SemanticModel,
)
from auto_bi.severity import Severity
from tests.test_semantic_gateway import contract, model, source


def snap(
    root: Path,
    name: str,
    *,
    semantic: SemanticModel | None = None,
    contracts: dict[str, Any] | None = None,
) -> SemanticSnapshot:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    return source(directory, semantic=semantic, contracts=contracts).snapshot()


def only(diff: SemanticDiff, code: str) -> Any:
    matches = [change for change in diff.changes if change.code == code]
    assert matches, f"{code} not in {[change.code for change in diff.changes]}"
    assert len(matches) == 1
    return matches[0]


def test_identical_snapshots_have_no_changes(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    candidate = snap(tmp_path, "candidate")
    diff = diff_snapshots(base, candidate)
    assert diff.changes == []
    assert diff.base_digest == diff.candidate_digest


def test_removed_field_blocks_and_names_its_metrics(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    trimmed = model()
    trimmed.tables[0].columns = [
        column for column in trimmed.tables[0].columns if column.name != "gross_margin"
    ]
    candidate = snap(tmp_path, "candidate", semantic=trimmed)
    change = only(diff_snapshots(base, candidate), "field.removed")
    assert change.kind is ChangeKind.REMOVED
    assert change.severity is Severity.BLOCK
    assert change.subject == "dm.sales_daily.gross_margin"
    assert change.impact.metrics == ["metric://sales/gross_margin_pct"]


def test_added_field_is_advisory(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    widened = model()
    widened.tables[0].columns.append(
        Column(name="channel", type="String", role=ColumnRole.DIMENSION)
    )
    diff = diff_snapshots(base, snap(tmp_path, "candidate", semantic=widened))
    change = only(diff, "field.added")
    assert change.severity is Severity.ADVISORY
    assert change.impact.metrics == []


def test_weakened_classification_blocks_as_a_security_finding(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    leaked = model()
    leaked.tables[0].columns[4].classification = DataClassification.PUBLIC
    change = only(
        diff_snapshots(base, snap(tmp_path, "candidate", semantic=leaked)),
        "field.classification_weakened",
    )
    assert change.severity is Severity.BLOCK
    assert "confidential" in change.detail and "public" in change.detail


def test_tightened_classification_is_advisory(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    tightened = model()
    tightened.tables[0].columns[4].classification = DataClassification.RESTRICTED
    change = only(
        diff_snapshots(base, snap(tmp_path, "candidate", semantic=tightened)),
        "field.classification_tightened",
    )
    assert change.severity is Severity.ADVISORY


def test_label_only_change_is_advisory(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    relabelled = model()
    relabelled.tables[0].columns[4].description = "Revenue, net of VAT"
    change = only(
        diff_snapshots(base, snap(tmp_path, "candidate", semantic=relabelled)),
        "field.description_changed",
    )
    assert change.severity is Severity.ADVISORY


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda m: setattr(m.tables[0].columns[4], "type", "Float64"), "field.type_changed"),
        (lambda m: setattr(m.tables[0].columns[4], "agg", Aggregation.AVG), "field.agg_changed"),
        (
            lambda m: setattr(m.tables[0].columns[4], "role", ColumnRole.DIMENSION),
            "field.role_changed",
        ),
    ],
)
def test_measure_semantics_changes_need_review(tmp_path: Path, mutate: Any, code: str) -> None:
    base = snap(tmp_path, "base")
    changed = model()
    mutate(changed)
    change = only(diff_snapshots(base, snap(tmp_path, "candidate", semantic=changed)), code)
    assert change.severity is Severity.REVIEW
    assert change.impact.metrics == ["metric://sales/gross_margin_pct"]


def test_physical_key_change_needs_a_physical_re_run(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    repartitioned = model()
    assert repartitioned.tables[0].physical is not None
    repartitioned.tables[0].physical.partition_key = "toYYYYMMDD(date)"
    change = only(
        diff_snapshots(base, snap(tmp_path, "candidate", semantic=repartitioned)),
        "physical.keys_changed",
    )
    assert change.severity is Severity.REVIEW


def test_removed_join_blocks(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    unjoined = model()
    unjoined.joins = []
    change = only(
        diff_snapshots(base, snap(tmp_path, "candidate", semantic=unjoined)), "join.removed"
    )
    assert change.severity is Severity.BLOCK


def test_removed_table_blocks(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    shrunk = model()
    shrunk.tables = shrunk.tables[:1]
    shrunk.joins = []
    codes = {
        change.code for change in diff_snapshots(base, snap(tmp_path, "c", semantic=shrunk)).changes
    }
    assert {"table.removed", "field.removed", "join.removed"} <= codes


def test_metric_formula_change_needs_review(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    reformulated = contract()
    reformulated["formula"]["denominator"]["column"] = "gross_margin"
    diff = diff_snapshots(base, snap(tmp_path, "candidate", contracts={"m.yaml": reformulated}))
    change = only(diff, "metric.formula_changed")
    assert change.severity is Severity.REVIEW
    assert change.impact.metrics == ["metric://sales/gross_margin_pct"]


@pytest.mark.parametrize(
    ("patch", "code"),
    [
        ({"time": {"event_time_column": "date", "timezone": "UTC"}}, "metric.time_changed"),
        ({"grain": {"table": "dm.sales_daily", "keys": ["date"]}}, "metric.grain_changed"),
        (
            {
                "unit": {
                    "unit": "percent",
                    "precision": 2,
                    "null_semantics": "null_is_missing",
                    "zero_semantics": "zero_is_value",
                }
            },
            "metric.unit_changed",
        ),
        ({"additivity": "additive"}, "metric.additivity_changed"),
    ],
)
def test_metric_meaning_changes_need_review(
    tmp_path: Path, patch: dict[str, Any], code: str
) -> None:
    base = snap(tmp_path, "base")
    changed = contract(**patch)
    change = only(
        diff_snapshots(base, snap(tmp_path, "candidate", contracts={"m.yaml": changed})), code
    )
    assert change.severity is Severity.REVIEW


def test_removed_metric_blocks(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    candidate = snap(tmp_path, "candidate", contracts={"readme.txt": "not a contract"})
    diff = diff_snapshots(base, candidate)
    change = only(diff, "metric.removed")
    assert change.severity is Severity.BLOCK
    assert change.impact.metrics == ["metric://sales/gross_margin_pct"]


def test_removed_dimension_blocks_and_added_one_is_advisory(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    narrowed = contract(dimensions=["region"])
    assert (
        only(
            diff_snapshots(base, snap(tmp_path, "narrow", contracts={"m.yaml": narrowed})),
            "metric.dimensions_removed",
        ).severity
        is Severity.BLOCK
    )
    widened = contract(dimensions=["region", "category", "store_id"])
    assert (
        only(
            diff_snapshots(base, snap(tmp_path, "wide", contracts={"m.yaml": widened})),
            "metric.dimensions_added",
        ).severity
        is Severity.ADVISORY
    )


def test_dropped_policy_ref_blocks(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    ungoverned = contract(policy_refs=[])
    change = only(
        diff_snapshots(base, snap(tmp_path, "candidate", contracts={"m.yaml": ungoverned})),
        "metric.policy_weakened",
    )
    assert change.severity is Severity.BLOCK


def test_lifecycle_move_towards_retirement_needs_review(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    retiring = contract()
    retiring["metadata"]["status"] = "deprecated"
    change = only(
        diff_snapshots(base, snap(tmp_path, "candidate", contracts={"m.yaml": retiring})),
        "metric.status_changed",
    )
    assert change.severity is Severity.REVIEW


def test_new_gap_is_reported_with_its_own_severity(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    dangling = model()
    dangling.joins = [Join(left="dm.sales_daily.store_id", right="dm.stores.gone")]
    change = only(
        diff_snapshots(base, snap(tmp_path, "candidate", semantic=dangling)), "gap.appeared"
    )
    assert change.severity is Severity.BLOCK
    assert "dangling_join" in change.detail


def test_changes_are_ordered_deterministically(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    changed = model()
    changed.tables[0].columns[4].description = "Revenue, net of VAT"
    changed.tables[0].columns = [c for c in changed.tables[0].columns if c.name != "category"]
    candidate = snap(tmp_path, "candidate", semantic=changed)
    first = diff_snapshots(base, candidate)
    second = diff_snapshots(base, candidate)
    assert [c.subject for c in first.changes] == [c.subject for c in second.changes]
    assert [c.code for c in first.changes] == [
        "field.removed",
        "field.description_changed",
        "gap.appeared",
    ]


def test_diff_round_trips(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    changed = model()
    changed.tables[0].columns[4].description = "x"
    diff = diff_snapshots(base, snap(tmp_path, "candidate", semantic=changed))
    assert SemanticDiff.model_validate(diff.model_dump(mode="json")) == diff


def test_a_raised_cost_ceiling_needs_review_and_a_lowered_one_is_advisory(tmp_path: Path) -> None:
    """A budget moves the digest either way, so §8.3 says both directions get a name — but
    only the loosened one lets a plan the gate refused yesterday through today."""
    base = snap(tmp_path, "base", contracts={"m.yaml": contract(budgets={"max_scanned_rows": 500})})

    relaxed = snap(
        tmp_path, "up", contracts={"m.yaml": contract(budgets={"max_scanned_rows": 5000})}
    )
    change = only(diff_snapshots(base, relaxed), "metric.budget_relaxed")
    assert change.severity is Severity.REVIEW
    assert "scanned rows 500 -> 5000" in change.detail

    tightened = snap(
        tmp_path, "down", contracts={"m.yaml": contract(budgets={"max_scanned_rows": 50})}
    )
    change = only(diff_snapshots(base, tightened), "metric.budget_tightened")
    assert change.severity is Severity.ADVISORY
    assert "scanned rows 500 -> 50" in change.detail


def test_dropping_a_bound_is_the_largest_relaxation_there_is(tmp_path: Path) -> None:
    """No ceiling is not a bigger number to compare against — it is no comparison at all."""
    base = snap(tmp_path, "base", contracts={"m.yaml": contract(budgets={"timeout_ms": 30000})})
    candidate = snap(tmp_path, "candidate", contracts={"m.yaml": contract()})

    change = only(diff_snapshots(base, candidate), "metric.budget_relaxed")
    assert change.severity is Severity.REVIEW
    assert "timeout (ms) 30000 -> unbounded" in change.detail


def test_one_edit_that_moves_two_bounds_both_ways_is_named_both_ways(tmp_path: Path) -> None:
    base = snap(
        tmp_path,
        "base",
        contracts={"m.yaml": contract(budgets={"max_scanned_rows": 500, "timeout_ms": 1000})},
    )
    candidate = snap(
        tmp_path,
        "candidate",
        contracts={"m.yaml": contract(budgets={"max_scanned_rows": 5000, "timeout_ms": 100})},
    )

    diff = diff_snapshots(base, candidate)
    assert only(diff, "metric.budget_relaxed").detail.endswith("scanned rows 500 -> 5000")
    assert only(diff, "metric.budget_tightened").detail.endswith("timeout (ms) 1000 -> 100")


def test_an_empty_budget_block_is_not_a_change(tmp_path: Path) -> None:
    """`budgets: {}` and no budgets are one contract, so they must not diff against each
    other — the same normalisation the contract itself applies (invariant 9)."""
    base = snap(tmp_path, "base", contracts={"m.yaml": contract()})
    candidate = snap(tmp_path, "candidate", contracts={"m.yaml": contract(budgets={})})

    assert diff_snapshots(base, candidate).changes == []
