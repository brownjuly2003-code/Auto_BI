"""Who a semantic change reaches (DECISIONOPS_ARCHITECTURE §8.3).

`semantic/diff.py` classifies a change on its own terms and stops at the metric, because it
must not know about decision products (§5). This module finishes the chain — metric →
dashboard → decision product → owner — and applies the one rule that needs both halves:

    a `review` finding becomes a `block` once a governed product depends on the metric.

That is what §8.3 means by "review/block по impact". A formula, grain, event-time or unit
change is a question while nothing depends on it, and a release stopper the moment
something does. `advisory` never escalates, `block` is already terminal.

Chart-level impact stops at the dashboard ref: which charts inside a dashboard use the
metric is a property of its `DashboardSpec`, which the release-gate slice loads. Saying
"this dashboard" rather than "this chart" is the honest resolution available here.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import Field

from auto_bi.decision.spec import DecisionProductSpec
from auto_bi.refs import ContractRef
from auto_bi.scalars import Sha256Digest
from auto_bi.semantic.diff import SemanticDiff
from auto_bi.severity import Severity
from auto_bi.strict import StrictModel


class ProductImpact(StrictModel):
    code: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    #: Severity after the impact rule; what a gate acts on.
    severity: Severity
    #: Severity the semantic diff assigned on its own; kept so the escalation stays visible.
    classified_severity: Severity
    detail: str = Field(min_length=1)
    metrics: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    dashboards: list[str] = Field(default_factory=list)
    owners: list[str] = Field(default_factory=list)


class ImpactReport(StrictModel):
    base_digest: Sha256Digest
    candidate_digest: Sha256Digest
    products_in_scope: list[str] = Field(default_factory=list)
    impacts: list[ProductImpact] = Field(default_factory=list)


def resolve_impact(diff: SemanticDiff, products: Sequence[DecisionProductSpec]) -> ImpactReport:
    """Attach product, dashboard and owner impact to every change in *diff*."""
    by_metric: dict[str, list[DecisionProductSpec]] = {}
    for product in products:
        for ref in product.scope.metrics:
            # Products pin a version; a change to the metric reaches them whatever the new
            # version is. Whether the pin still resolves is `decision validate`'s question.
            identifier = str(ContractRef.parse(ref).without_version())
            by_metric.setdefault(identifier, []).append(product)

    impacts: list[ProductImpact] = []
    for change in diff.changes:
        affected: list[DecisionProductSpec] = []
        seen: set[str] = set()
        for metric_id in change.impact.metrics:
            for product in by_metric.get(metric_id, []):
                if product.metadata.id not in seen:
                    seen.add(product.metadata.id)
                    affected.append(product)

        severity = change.severity
        if severity is Severity.REVIEW and affected:
            severity = Severity.BLOCK

        impacts.append(
            ProductImpact(
                code=change.code,
                subject=change.subject,
                severity=severity,
                classified_severity=change.severity,
                detail=change.detail,
                metrics=list(change.impact.metrics),
                products=sorted(item.self_ref() for item in affected),
                dashboards=sorted(
                    {dashboard.ref for item in affected for dashboard in item.delivery.dashboards}
                ),
                owners=sorted({item.metadata.owner for item in affected}),
            )
        )

    return ImpactReport(
        base_digest=diff.base_digest,
        candidate_digest=diff.candidate_digest,
        products_in_scope=sorted(product.self_ref() for product in products),
        impacts=impacts,
    )
