"""Semantic diff between two snapshots (DECISIONOPS_ARCHITECTURE §8.3).

Every difference gets a stable code and a default classification. The rules are the ones in
§8.3, and the reasoning behind them is uniform: what is *gone* blocks (nothing downstream
can be re-derived from it), what *changes a number's meaning* — formula, grain, event time,
unit, additivity — needs review until impact says whether anything depends on it, what
*weakens a policy, a classification or a cost ceiling* needs a person to have meant it, and
what changes only a label is advisory.

The escalation from `review` to `block` happens one layer up, in
`auto_bi/decision/impact.py`, because it needs the decision products — and `semantic` does
not import `decision` (§5). Here impact stops at the metric: which metrics a changed field
feeds is knowable from the graph alone.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import StrEnum

from pydantic import Field

from auto_bi.scalars import Sha256Digest, duration_seconds
from auto_bi.semantic.gateway import (
    FieldNode,
    FormulaBinding,
    MetricNode,
    SemanticGraph,
    SemanticSnapshot,
    TableNode,
)
from auto_bi.semantic.model import DataClassification
from auto_bi.severity import Severity
from auto_bi.strict import StrictModel

# Sensitivity ladder: moving down it is a weakening, which is a security finding (§8.3).
_CLASSIFICATION_RANK: dict[DataClassification, int] = {
    DataClassification.PUBLIC: 0,
    DataClassification.INTERNAL: 1,
    DataClassification.CONFIDENTIAL: 2,
    DataClassification.RESTRICTED: 3,
}

# Lifecycle order; moving towards retirement is a question for a human, moving back is not.
_LIFECYCLE_RANK = {"draft": 0, "active": 1, "deprecated": 2, "retired": 3}

_PHYSICAL_ATTRS = ("engine", "table_engine", "sorting_key", "distribution_key", "partition_key")


class ChangeKind(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"


class SemanticImpact(StrictModel):
    """What the change reaches inside the semantic graph."""

    fields: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)


class SemanticChange(StrictModel):
    code: str = Field(min_length=1)
    kind: ChangeKind
    severity: Severity
    subject: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    impact: SemanticImpact = Field(default_factory=SemanticImpact)


class SemanticDiff(StrictModel):
    base_digest: Sha256Digest
    candidate_digest: Sha256Digest
    changes: list[SemanticChange] = Field(default_factory=list)


def diff_snapshots(base: SemanticSnapshot, candidate: SemanticSnapshot) -> SemanticDiff:
    """Classify every difference between *base* and *candidate*."""
    metrics = _metric_index(base.graph, candidate.graph)
    changes: list[SemanticChange] = []
    changes.extend(_diff_tables(base.graph, candidate.graph, metrics))
    changes.extend(_diff_fields(base.graph, candidate.graph, metrics))
    changes.extend(_diff_joins(base.graph, candidate.graph))
    changes.extend(_diff_metrics(base.graph, candidate.graph))
    changes.extend(_diff_gaps(base, candidate))
    return SemanticDiff(
        base_digest=base.canonical_digest,
        candidate_digest=candidate.canonical_digest,
        changes=changes,
    )


def _metric_index(base: SemanticGraph, candidate: SemanticGraph) -> list[MetricNode]:
    """Metrics from both sides — a removal must still find the metrics it fed."""
    merged = {node.id: node for node in base.metrics}
    merged.update({node.id: node for node in candidate.metrics})
    return [merged[key] for key in sorted(merged)]


def _metrics_using_field(field_id: str, metrics: Sequence[MetricNode]) -> list[str]:
    hits = []
    for metric in metrics:
        dimension_fields = {f"{metric.grain.table}.{name}" for name in metric.dimensions}
        if field_id in metric.fields or field_id in dimension_fields:
            hits.append(metric.id)
    return sorted(hits)


def _metrics_on_table(table_id: str, metrics: Sequence[MetricNode]) -> list[str]:
    prefix = f"{table_id}."
    return sorted(
        metric.id
        for metric in metrics
        if metric.grain.table == table_id or any(f.startswith(prefix) for f in metric.fields)
    )


def _diff_tables(
    base: SemanticGraph, candidate: SemanticGraph, metrics: Sequence[MetricNode]
) -> list[SemanticChange]:
    before = {node.id: node for node in base.tables}
    after = {node.id: node for node in candidate.tables}
    changes: list[SemanticChange] = []
    for table_id in sorted(set(before) | set(after)):
        old, new = before.get(table_id), after.get(table_id)
        impact = SemanticImpact(metrics=_metrics_on_table(table_id, metrics))
        if old is None and new is not None:
            changes.append(
                _change("table.added", ChangeKind.ADDED, Severity.ADVISORY, table_id, "new table")
            )
        elif new is None and old is not None:
            changes.append(
                _change(
                    "table.removed",
                    ChangeKind.REMOVED,
                    Severity.BLOCK,
                    table_id,
                    "table is gone from the semantic model",
                    impact,
                )
            )
        elif old is not None and new is not None:
            changes.extend(_diff_table_attrs(old, new, impact))
    return changes


def _diff_table_attrs(
    old: TableNode, new: TableNode, impact: SemanticImpact
) -> list[SemanticChange]:
    changes: list[SemanticChange] = []
    if old.grain != new.grain:
        changes.append(
            _change(
                "table.grain_changed",
                ChangeKind.MODIFIED,
                Severity.REVIEW,
                new.id,
                f"grain {old.grain} -> {new.grain}",
                impact,
            )
        )
    changes.extend(
        _classification_change("table", old.id, old.classification, new.classification, impact)
    )
    if old.description != new.description:
        changes.append(
            _change(
                "table.description_changed",
                ChangeKind.MODIFIED,
                Severity.ADVISORY,
                new.id,
                "description changed",
            )
        )
    physical = [attr for attr in _PHYSICAL_ATTRS if getattr(old, attr) != getattr(new, attr)]
    if physical:
        changes.append(
            _change(
                "physical.keys_changed",
                ChangeKind.MODIFIED,
                Severity.REVIEW,
                new.id,
                f"physical layout changed ({', '.join(physical)}): re-run the physical gate",
                impact,
            )
        )
    return changes


def _diff_fields(
    base: SemanticGraph, candidate: SemanticGraph, metrics: Sequence[MetricNode]
) -> list[SemanticChange]:
    before = {node.id: node for node in base.fields}
    after = {node.id: node for node in candidate.fields}
    changes: list[SemanticChange] = []
    for field_id in sorted(set(before) | set(after)):
        old, new = before.get(field_id), after.get(field_id)
        impact = SemanticImpact(fields=[field_id], metrics=_metrics_using_field(field_id, metrics))
        if old is None and new is not None:
            changes.append(
                _change(
                    "field.added",
                    ChangeKind.ADDED,
                    Severity.ADVISORY,
                    field_id,
                    f"new {new.role.value} field",
                    SemanticImpact(fields=[field_id]),
                )
            )
        elif new is None and old is not None:
            changes.append(
                _change(
                    "field.removed",
                    ChangeKind.REMOVED,
                    Severity.BLOCK,
                    field_id,
                    "field is gone from the semantic model",
                    impact,
                )
            )
        elif old is not None and new is not None:
            changes.extend(_diff_field_attrs(old, new, impact))
    return changes


def _diff_field_attrs(
    old: FieldNode, new: FieldNode, impact: SemanticImpact
) -> list[SemanticChange]:
    changes: list[SemanticChange] = []
    for attr, code in (
        ("type", "field.type_changed"),
        ("role", "field.role_changed"),
        ("agg", "field.agg_changed"),
        ("additivity", "field.additivity_changed"),
        ("fk", "field.fk_changed"),
    ):
        before, after = getattr(old, attr), getattr(new, attr)
        if before != after:
            changes.append(
                _change(
                    code,
                    ChangeKind.MODIFIED,
                    Severity.REVIEW,
                    new.id,
                    f"{attr} {_render(before)} -> {_render(after)}",
                    impact,
                )
            )
    changes.extend(
        _classification_change("field", new.id, old.classification, new.classification, impact)
    )
    if old.description != new.description:
        changes.append(
            _change(
                "field.description_changed",
                ChangeKind.MODIFIED,
                Severity.ADVISORY,
                new.id,
                "description changed",
                # Fields only, deliberately — the same shape `field.added` carries. Naming
                # the field lets a reader (and chart resolution) follow the change to what
                # reads it; naming the metrics would make a relabel invalidate verified
                # assets, which grade on `impact.metrics`.
                SemanticImpact(fields=[new.id]),
            )
        )
    return changes


def _diff_joins(base: SemanticGraph, candidate: SemanticGraph) -> list[SemanticChange]:
    before = {node.id: node for node in base.joins}
    after = {node.id: node for node in candidate.joins}
    changes: list[SemanticChange] = []
    for join_id in sorted(set(before) | set(after)):
        old, new = before.get(join_id), after.get(join_id)
        if old is None:
            changes.append(
                _change("join.added", ChangeKind.ADDED, Severity.ADVISORY, join_id, "new join path")
            )
        elif new is None:
            changes.append(
                _change(
                    "join.removed",
                    ChangeKind.REMOVED,
                    Severity.BLOCK,
                    join_id,
                    "join path is gone: anything that spanned it cannot be re-derived",
                )
            )
        elif old.type != new.type:
            changes.append(
                _change(
                    "join.type_changed",
                    ChangeKind.MODIFIED,
                    Severity.REVIEW,
                    join_id,
                    f"cardinality {old.type} -> {new.type}",
                )
            )
    return changes


def _diff_metrics(base: SemanticGraph, candidate: SemanticGraph) -> list[SemanticChange]:
    before = {node.id: node for node in base.metrics}
    after = {node.id: node for node in candidate.metrics}
    changes: list[SemanticChange] = []
    for metric_id in sorted(set(before) | set(after)):
        old, new = before.get(metric_id), after.get(metric_id)
        impact = SemanticImpact(metrics=[metric_id])
        if old is None and new is not None:
            changes.append(
                _change(
                    "metric.added", ChangeKind.ADDED, Severity.ADVISORY, metric_id, "new metric"
                )
            )
        elif new is None and old is not None:
            changes.append(
                _change(
                    "metric.removed",
                    ChangeKind.REMOVED,
                    Severity.BLOCK,
                    metric_id,
                    "metric contract is gone from the validated set",
                    impact,
                )
            )
        elif old is not None and new is not None:
            changes.extend(_diff_metric_attrs(old, new, impact))
    return changes


def metric_changes(old: MetricNode, new: MetricNode) -> list[SemanticChange]:
    """The changes `diff_snapshots` names between two definitions of one metric, in its order."""
    return _diff_metric_attrs(old, new, SemanticImpact(metrics=[new.id]))


def _diff_metric_attrs(
    old: MetricNode, new: MetricNode, impact: SemanticImpact
) -> list[SemanticChange]:
    changes: list[SemanticChange] = []
    for attr, code in (
        ("formula", "metric.formula_changed"),
        ("grain", "metric.grain_changed"),
        ("time", "metric.time_changed"),
        ("unit", "metric.unit_changed"),
    ):
        old_value = getattr(old, attr)
        new_value = getattr(new, attr)
        if old_value != new_value:
            detail = f"{attr} changed"
            if attr == "time":
                changed_fields = [
                    f"{field} {getattr(old_value, field)} -> {getattr(new_value, field)}"
                    for field in ("event_time_column", "timezone", "calendar")
                    if getattr(old_value, field) != getattr(new_value, field)
                ]
                detail = f"time changed: {', '.join(changed_fields)}"
            changes.append(
                _change(
                    code,
                    ChangeKind.MODIFIED,
                    Severity.REVIEW,
                    new.id,
                    detail,
                    impact,
                )
            )
    if old.binding != new.binding:
        changes.append(
            _change(
                "metric.binding_changed",
                ChangeKind.MODIFIED,
                Severity.REVIEW,
                new.id,
                _binding_detail(old.binding, new.binding),
                impact,
            )
        )
    if (old.additivity, old.non_additive_axes) != (new.additivity, new.non_additive_axes):
        changes.append(
            _change(
                "metric.additivity_changed",
                ChangeKind.MODIFIED,
                Severity.REVIEW,
                new.id,
                f"additivity {old.additivity.value} -> {new.additivity.value}",
                impact,
            )
        )

    removed = sorted(set(old.dimensions) - set(new.dimensions))
    added = sorted(set(new.dimensions) - set(old.dimensions))
    if removed:
        changes.append(
            _change(
                "metric.dimensions_removed",
                ChangeKind.MODIFIED,
                Severity.BLOCK,
                new.id,
                f"dimensions no longer allowed: {', '.join(removed)}",
                impact,
            )
        )
    if added:
        changes.append(
            _change(
                "metric.dimensions_added",
                ChangeKind.MODIFIED,
                Severity.ADVISORY,
                new.id,
                f"dimensions now allowed: {', '.join(added)}",
                impact,
            )
        )

    changes.extend(_join_paths_change(old, new, impact))

    dropped_policies = sorted(set(old.policy_refs) - set(new.policy_refs))
    if dropped_policies:
        changes.append(
            _change(
                "metric.policy_weakened",
                ChangeKind.MODIFIED,
                Severity.BLOCK,
                new.id,
                f"governing policy dropped: {', '.join(dropped_policies)}",
                impact,
            )
        )
    elif set(new.policy_refs) - set(old.policy_refs):
        changes.append(
            _change(
                "metric.policy_added",
                ChangeKind.MODIFIED,
                Severity.ADVISORY,
                new.id,
                "additional governing policy",
                impact,
            )
        )

    changes.extend(
        _classification_change("metric", new.id, old.classification, new.classification, impact)
    )

    if old.status != new.status:
        towards_retirement = _LIFECYCLE_RANK[new.status.value] > _LIFECYCLE_RANK[old.status.value]
        changes.append(
            _change(
                "metric.status_changed",
                ChangeKind.MODIFIED,
                Severity.REVIEW if towards_retirement else Severity.ADVISORY,
                new.id,
                f"lifecycle {old.status.value} -> {new.status.value}",
                impact,
            )
        )
    if old.version != new.version:
        changes.append(
            _change(
                "metric.version_changed",
                ChangeKind.MODIFIED,
                Severity.ADVISORY,
                new.id,
                f"domain version {old.version} -> {new.version}: pinned refs must be re-pointed",
                impact,
            )
        )
    if old.owner != new.owner:
        changes.append(
            _change(
                "metric.owner_changed",
                ChangeKind.MODIFIED,
                Severity.ADVISORY,
                new.id,
                f"owner {old.owner} -> {new.owner}",
                impact,
            )
        )
    if old.description != new.description:
        changes.append(
            _change(
                "metric.description_changed",
                ChangeKind.MODIFIED,
                Severity.ADVISORY,
                new.id,
                "description changed",
            )
        )
    changes.extend(_budget_change(old, new, impact))
    changes.extend(_freshness_change(old, new, impact))
    return changes


#: The parts of a formula binding, in the order a reader expects them, with the words a
#: finding uses for each.
_BINDING_PARTS = (
    ("source", "source"),
    ("ref", "ref"),
    ("source_revision", "pin"),
    ("closure_digest", "closure"),
)


def _binding_detail(old: FormulaBinding | None, new: FormulaBinding | None) -> str:
    """What moved in where a metric's formula is defined (ADR 0008, item 9).

    Review whichever way it moved, as `metric.formula_changed` is: the closure digest moves
    with any change of the definition in dbt — one the typed formula shows or not, and one in
    a construct that leaves the formula `external_ref` — so the change is named even where the
    formula reads the same, and a formula that came to be or stopped being defined in dbt is a
    change of what defines the number even where the typed formula is one.
    """
    if old is None and new is not None:
        return f"formula now defined by {new.source} metric {new.ref}"
    if new is None and old is not None:
        return f"formula no longer defined by {old.source} metric {old.ref}"
    # A closure no artifact gave is "not read": no artifact, a refused one, or no such metric.
    parts = [
        f"{label} {getattr(old, attr) or 'not read'} -> {getattr(new, attr) or 'not read'}"
        for attr, label in _BINDING_PARTS
        if getattr(old, attr) != getattr(new, attr)
    ]
    return f"external definition changed: {', '.join(parts)}"


def _join_paths_change(
    old: MetricNode, new: MetricNode, impact: SemanticImpact
) -> list[SemanticChange]:
    """A metric's declared join paths, graded by which way they moved.

    A path that is gone blocks (§8.3 "Удалён field/metric/join path"): what was joined
    through it cannot be re-derived. A changed cardinality changes how rows fan out
    across the join and therefore a number's meaning — review, until impact says whether
    anything depends on it. A new path only widens what the metric may be joined to,
    so it is advisory. A path written in the opposite direction is a different path.
    The model's joins are compared by `_diff_joins`, apart from these metric paths.
    """
    before: dict[tuple[str, str], set[str]] = {}
    after: dict[tuple[str, str], set[str]] = {}
    for node, grouped in ((old, before), (new, after)):
        for join in node.join_paths:
            grouped.setdefault((join.left, join.right), set()).add(join.type)

    removed: list[str] = []
    changed: list[str] = []
    added: list[str] = []
    for left, right in sorted(
        before.keys() | after.keys(), key=lambda path: (f"{path[0]}->{path[1]}", *path)
    ):
        path = (left, right)
        old_types = ", ".join(sorted(before.get(path, set())))
        new_types = ", ".join(sorted(after.get(path, set())))
        if path not in after:
            removed.append(f"{left}->{right} ({old_types})")
        elif path not in before:
            added.append(f"{left}->{right} ({new_types})")
        elif before[path] != after[path]:
            changed.append(f"{left}->{right} ({old_types}) -> ({new_types})")

    changes: list[SemanticChange] = []
    for code, severity, prefix, items in (
        ("metric.join_path_removed", Severity.BLOCK, "join paths no longer declared: ", removed),
        ("metric.join_path_changed", Severity.REVIEW, "join path cardinality changed: ", changed),
        ("metric.join_path_added", Severity.ADVISORY, "join paths now declared: ", added),
    ):
        if items:
            changes.append(
                _change(
                    code, ChangeKind.MODIFIED, severity, new.id, prefix + "; ".join(items), impact
                )
            )
    return changes


#: The bounds a metric may declare on what one run of it costs, in the order a reader
#: expects them, with the words a finding uses for each.
_BUDGET_BOUNDS = (
    ("max_scanned_rows", "scanned rows"),
    ("max_scanned_bytes", "scanned bytes"),
    ("timeout_ms", "timeout (ms)"),
)


def _budget_change(
    old: MetricNode, new: MetricNode, impact: SemanticImpact
) -> list[SemanticChange]:
    """A moved cost ceiling, split by which way it moved.

    Both directions are named, because the digest moves either way and §8.3 says every
    difference between two snapshots has a name. They are not graded the same: a ceiling
    that went *up* — or away — is what makes a plan the gate refused yesterday pass today,
    and only a person can say that was intended. A tightened bound cannot hide anything;
    if it now refuses the release, `dwh.cost` says so itself, and grading it here as well
    would give one edit two weights in the verdict.
    """
    if old.budgets == new.budgets:
        return []
    relaxed: list[str] = []
    tightened: list[str] = []
    for attr, label in _BUDGET_BOUNDS:
        before = getattr(old.budgets, attr, None) if old.budgets else None
        after = getattr(new.budgets, attr, None) if new.budgets else None
        if before == after:
            continue
        # An absent bound is no ceiling at all, so dropping one is the largest relaxation
        # there is — never a comparison against a missing number.
        if after is None or (before is not None and after > before):
            relaxed.append(f"{label} {_bound(before)} -> {_bound(after)}")
        else:
            tightened.append(f"{label} {_bound(before)} -> {_bound(after)}")
    changes: list[SemanticChange] = []
    if relaxed:
        changes.append(
            _change(
                "metric.budget_relaxed",
                ChangeKind.MODIFIED,
                Severity.REVIEW,
                new.id,
                f"cost ceiling raised: {'; '.join(relaxed)}",
                impact,
            )
        )
    if tightened:
        changes.append(
            _change(
                "metric.budget_tightened",
                ChangeKind.MODIFIED,
                Severity.ADVISORY,
                new.id,
                f"cost ceiling lowered: {'; '.join(tightened)}",
                impact,
            )
        )
    return changes


def _freshness_change(
    old: MetricNode, new: MetricNode, impact: SemanticImpact
) -> list[SemanticChange]:
    """A moved freshness promise, split by which way it moved.

    A longer or unorderable window, or a dropped quality precondition, can serve older
    or less checked numbers to consumers: only a person can say that was intended.
    Tightening the promise is advisory, as is restating the same fixed window: neither
    weakens it, but every digest move must still have a name (§8.3).
    """
    if old.freshness == new.freshness:
        return []
    relaxed_items: list[str] = []
    tightened_items: list[str] = []
    restated_items: list[str] = []
    if old.freshness.max_age != new.freshness.max_age:
        before = duration_seconds(old.freshness.max_age)
        after = duration_seconds(new.freshness.max_age)
        item = f"max_age {old.freshness.max_age} -> {new.freshness.max_age}"
        if before is None or after is None:
            relaxed_items.append(f"{item}, which cannot be ordered")
        elif after > before:
            relaxed_items.append(item)
        elif after < before:
            tightened_items.append(item)
        else:
            restated_items.append(f"{item}, the same length")
    before_preconditions = set(old.freshness.quality_preconditions)
    after_preconditions = set(new.freshness.quality_preconditions)
    for name in sorted(before_preconditions - after_preconditions):
        relaxed_items.append(f"quality precondition dropped: {name}")
    for name in sorted(after_preconditions - before_preconditions):
        tightened_items.append(f"quality precondition added: {name}")
    changes: list[SemanticChange] = []
    for direction, severity, items in (
        ("relaxed", Severity.REVIEW, relaxed_items),
        ("tightened", Severity.ADVISORY, tightened_items),
        ("restated", Severity.ADVISORY, restated_items),
    ):
        if items:
            changes.append(
                _change(
                    f"metric.freshness_{direction}",
                    ChangeKind.MODIFIED,
                    severity,
                    new.id,
                    f"freshness {direction}: {'; '.join(items)}",
                    impact,
                )
            )
    return changes


def _bound(value: int | None) -> str:
    return "unbounded" if value is None else str(value)


def _diff_gaps(base: SemanticSnapshot, candidate: SemanticSnapshot) -> list[SemanticChange]:
    before = {(gap.code, gap.path): gap for gap in base.gaps}
    after = {(gap.code, gap.path): gap for gap in candidate.gaps}
    changes: list[SemanticChange] = []
    for key in sorted(set(after) - set(before)):
        gap = after[key]
        changes.append(
            _change(
                "gap.appeared",
                ChangeKind.ADDED,
                gap.severity,
                gap.path,
                f"{gap.code}: {gap.reason}",
            )
        )
    for key in sorted(set(before) - set(after)):
        gap = before[key]
        changes.append(
            _change(
                "gap.resolved",
                ChangeKind.REMOVED,
                Severity.ADVISORY,
                gap.path,
                f"{gap.code} no longer reported",
            )
        )
    return changes


def _classification_change(
    prefix: str,
    subject: str,
    old: DataClassification,
    new: DataClassification,
    impact: SemanticImpact,
) -> list[SemanticChange]:
    if old == new:
        return []
    detail = f"classification {old.value} -> {new.value}"
    if _CLASSIFICATION_RANK[new] < _CLASSIFICATION_RANK[old]:
        return [
            _change(
                f"{prefix}.classification_weakened",
                ChangeKind.MODIFIED,
                Severity.BLOCK,
                subject,
                f"{detail}: sensitivity weakened",
                impact,
            )
        ]
    return [
        _change(
            f"{prefix}.classification_tightened",
            ChangeKind.MODIFIED,
            Severity.ADVISORY,
            subject,
            detail,
            impact,
        )
    ]


def _change(
    code: str,
    kind: ChangeKind,
    severity: Severity,
    subject: str,
    detail: str,
    impact: SemanticImpact | None = None,
) -> SemanticChange:
    return SemanticChange(
        code=code,
        kind=kind,
        severity=severity,
        subject=subject,
        detail=detail,
        impact=impact or SemanticImpact(),
    )


def _render(value: object) -> str:
    if value is None:
        return "unset"
    if isinstance(value, StrEnum):
        return value.value
    return str(value)


def severities(diff: SemanticDiff) -> Iterable[Severity]:
    """Severities of every change — the input a verdict is computed from."""
    return (change.severity for change in diff.changes)
