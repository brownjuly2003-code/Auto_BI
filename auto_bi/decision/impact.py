"""Who a semantic change reaches (DECISIONOPS_ARCHITECTURE §8.3).

`semantic/diff.py` classifies a change on its own terms and stops at the metric, because it
must not know about decision products (§5). This module finishes the chain — metric →
dashboard → decision product → owner — and applies the one rule that needs both halves:

    a `review` finding becomes a `block` once a governed product depends on the metric.

That is what §8.3 means by "review/block по impact". A formula, grain, event-time or unit
change is a question while nothing depends on it, and a release stopper the moment
something does. `advisory` never escalates, `block` is already terminal.

Chart resolution finishes the same chain one link further, down to the tile. It is
deliberately *evidence only*: the verdict is still decided by which products carry a
change, so a chart never escalates a severity and never puts a product in scope. Two things
follow, and both are the point:

* a change whose `products` is empty can still name charts — a delivered tile reading a
  column no metric contract covers is an undeclared dependency, and the report is obliged
  to say it saw one (§8.3 "записывается весь дифф, грейдится только дошедшее");
* naming a tile never changes what stops the release.

The two relations are kept apart because they are different claims. `reads_field` is read
straight off the IR — the chart's query names that column. `computes_metric` is an
inference: a `ChartSpec` carries no metric ref at all, so the only defensible evidence that
a tile *is* the governed number is that its measure reproduces the formula's terms exactly.
Terms are taken from both snapshots, because the tile that computed yesterday's definition
is precisely the one an owner must look at when the definition moves.

What the IR cannot answer leaves a named gap rather than an empty list: operator `raw_sql`
(the IR does not say which columns it reads), a dashboard spec the run did not load, a
formula with no typed terms, a metric whose definition was not supplied.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from pydantic import Field

from auto_bi.decision.spec import DecisionProductSpec
from auto_bi.ir.spec import ChartQuery, DashboardSpec, Measure, measure_alias
from auto_bi.refs import ContractRef
from auto_bi.scalars import Sha256Digest
from auto_bi.semantic.diff import SemanticChange, SemanticDiff
from auto_bi.semantic.gateway import MetricNode
from auto_bi.semantic.metric_contract import FormulaKind, MetricFormula, MetricTerm
from auto_bi.severity import Severity
from auto_bi.strict import StrictModel


class ChartRelation(StrEnum):
    """How a tile is reached. Ordered by strength: the stronger claim wins a tie."""

    #: The measure reproduces the metric's formula terms — this tile is that number.
    COMPUTES_METRIC = "computes_metric"
    #: The query names a column the change touches. No inference, straight off the IR.
    READS_FIELD = "reads_field"


class ChartImpact(StrictModel):
    #: `dashboard://weekly_margin@7#margin_by_region` — the pinned dashboard and the chart
    #: id inside its spec. A fragment, because a chart has no contract ref of its own.
    ref: str = Field(min_length=1)
    dashboard: str = Field(min_length=1)
    chart: str = Field(min_length=1)
    relation: ChartRelation
    detail: str = Field(min_length=1)


class ChartGap(StrictModel):
    """A tile the resolver could not decide about — named, so silence is not read as "no"."""

    code: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    detail: str = Field(min_length=1)


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
    #: Tiles this change reaches inside the dashboards the in-scope products deliver.
    #: Evidence, not grading: see the module docstring.
    charts: list[ChartImpact] = Field(default_factory=list)


class ImpactReport(StrictModel):
    base_digest: Sha256Digest
    candidate_digest: Sha256Digest
    products_in_scope: list[str] = Field(default_factory=list)
    impacts: list[ProductImpact] = Field(default_factory=list)
    #: Why a tile is missing from `charts` where the IR could not answer. Report-level: a
    #: gap is a property of the resolver's inputs, not of one change.
    chart_gaps: list[ChartGap] = Field(default_factory=list)


def resolve_impact(
    diff: SemanticDiff,
    products: Sequence[DecisionProductSpec],
    *,
    dashboards: Mapping[str, DashboardSpec] | None = None,
    metrics: Sequence[MetricNode] = (),
) -> ImpactReport:
    """Attach product, dashboard, owner and chart impact to every change in *diff*.

    `dashboards` maps a pinned dashboard ref to the spec the run loaded, and `metrics` are
    the metric nodes of both snapshots (duplicated ids are expected — a metric's definition
    on each side of the change). Supplying neither is legal and resolves no chart; it says
    so through `chart_gaps` rather than through an empty list.
    """
    specs: Mapping[str, DashboardSpec] = dashboards or {}
    by_metric: dict[str, list[DecisionProductSpec]] = {}
    for product in products:
        for ref in product.scope.metrics:
            # Products pin a version; a change to the metric reaches them whatever the new
            # version is. Whether the pin still resolves is `decision validate`'s question.
            identifier = str(ContractRef.parse(ref).without_version())
            by_metric.setdefault(identifier, []).append(product)

    resolver = _ChartResolver(products, specs, metrics)
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
                charts=resolver.charts_for(change),
            )
        )

    return ImpactReport(
        base_digest=diff.base_digest,
        candidate_digest=diff.candidate_digest,
        products_in_scope=sorted(product.self_ref() for product in products),
        impacts=impacts,
        chart_gaps=resolver.gaps(),
    )


class _ChartResolver:
    """Resolves changes to tiles across the dashboards the in-scope products deliver.

    Stateful only in its gaps: a gap is deduplicated by `(code, subject)` because the same
    unreadable tile met by ten changes is one hole, not ten.
    """

    def __init__(
        self,
        products: Sequence[DecisionProductSpec],
        dashboards: Mapping[str, DashboardSpec],
        metrics: Sequence[MetricNode],
    ) -> None:
        self._gaps: dict[tuple[str, str], ChartGap] = {}
        self._known = _terms_by_metric(metrics)
        self._loaded: list[tuple[str, DashboardSpec]] = []
        self._missing: list[str] = []
        delivered = {item.ref for product in products for item in product.delivery.dashboards}
        for ref in sorted(delivered):
            spec = dashboards.get(ref)
            if spec is None:
                self._missing.append(ref)
            else:
                self._loaded.append((ref, spec))

    def gaps(self) -> list[ChartGap]:
        return [self._gaps[key] for key in sorted(self._gaps)]

    def charts_for(self, change: SemanticChange) -> list[ChartImpact]:
        for ref in self._missing:
            # Named from the first change on, not at construction: an empty diff resolves
            # nothing, and a hole in a resolution nobody attempted is not a finding.
            self._add_gap(
                "chart.dashboard_spec_missing",
                ref,
                "the run did not load this dashboard spec, so its charts were not read",
            )
        if not self._loaded:
            # Nothing to scan: the missing specs are already named, and reporting a metric
            # as unresolvable on top of that would give one hole two names.
            return []
        computed = self._typed_terms(change.impact.metrics)
        fields = set(change.impact.fields)
        found: dict[str, ChartImpact] = {}
        for dashboard_ref, spec in self._loaded:
            for chart in spec.charts:
                ref = f"{dashboard_ref}#{chart.id}"
                if chart.query.raw_sql is not None:
                    self._add_gap(
                        "chart.opaque_sql",
                        ref,
                        "operator SQL: the IR does not say which columns this tile reads",
                    )
                    continue
                impact = self._match(dashboard_ref, chart.id, chart.query, computed, fields)
                if impact is not None:
                    found[ref] = impact
        return [found[ref] for ref in sorted(found)]

    def _match(
        self,
        dashboard_ref: str,
        chart_id: str,
        query: ChartQuery,
        computed: Mapping[str, tuple[MetricTerm, ...]],
        fields: set[str],
    ) -> ChartImpact | None:
        """The stronger claim wins: one tile listed twice would read as two findings."""
        for metric_id in sorted(computed):
            measure = _computing_measure(query, computed[metric_id])
            if measure is not None:
                return ChartImpact(
                    ref=f"{dashboard_ref}#{chart_id}",
                    dashboard=dashboard_ref,
                    chart=chart_id,
                    relation=ChartRelation.COMPUTES_METRIC,
                    detail=(
                        f"measure {measure_alias(measure)} reproduces the formula terms of "
                        f"{metric_id}"
                    ),
                )
        read = sorted(fields & _columns_read(query))
        if read:
            return ChartImpact(
                ref=f"{dashboard_ref}#{chart_id}",
                dashboard=dashboard_ref,
                chart=chart_id,
                relation=ChartRelation.READS_FIELD,
                detail=f"query reads {', '.join(read)}",
            )
        return None

    def _typed_terms(self, metric_ids: Sequence[str]) -> dict[str, tuple[MetricTerm, ...]]:
        typed: dict[str, tuple[MetricTerm, ...]] = {}
        for metric_id in metric_ids:
            known = self._known.get(metric_id)
            if known is None:
                self._add_gap(
                    "chart.metric_unknown",
                    metric_id,
                    "the metric's definition was not supplied, so no tile could be matched",
                )
                continue
            if known.untyped:
                # Recorded even when the other snapshot's terms did match a tile: the half
                # that cannot be resolved is a hole, not a rounding error on the half that could.
                self._add_gap(
                    "chart.formula_untyped",
                    metric_id,
                    "the formula carries no typed terms, so no tile can be shown to compute it",
                )
            if known.terms:
                typed[metric_id] = known.terms
        return typed

    def _add_gap(self, code: str, subject: str, detail: str) -> None:
        self._gaps.setdefault((code, subject), ChartGap(code=code, subject=subject, detail=detail))


@dataclass(frozen=True)
class _KnownMetric:
    """What the supplied snapshots say about one metric, across both sides of the change."""

    #: Terms any side declared, in first-seen order. Empty when no side declared one.
    terms: tuple[MetricTerm, ...] = ()
    #: True when at least one side declared no terms at all — tracked separately from
    #: `terms`, because a metric typed before and external after is both matchable and
    #: unresolvable, and collapsing that into one field would drop half the answer.
    untyped: bool = False


def _terms_by_metric(metrics: Sequence[MetricNode]) -> dict[str, _KnownMetric]:
    """Typed terms per metric, unioned over the snapshots that carry the metric.

    Both sides are read because the tile that computed yesterday's definition is precisely
    the one an owner must look at when the definition moves; matching only the candidate
    would answer "no tile computes it" for every formula change.
    """
    known: dict[str, _KnownMetric] = {}
    for node in metrics:
        current = known.get(node.id, _KnownMetric())
        found = _formula_terms(node.formula)
        if found is None:
            known[node.id] = replace(current, untyped=True)
            continue
        merged = list(current.terms)
        merged += [term for term in found if term not in merged]
        known[node.id] = replace(current, terms=tuple(merged))
    return known


def _formula_terms(formula: MetricFormula) -> tuple[MetricTerm, ...] | None:
    """The terms a chart measure could reproduce; `None` when the formula declares none.

    `external_ref` and `reviewed_sql` are exactly the two the release gate already calls a
    semantic gap for reference SQL (§8.2 step 6) — a definition this repository does not
    hold cannot be matched against an IR measure either.
    """
    if formula.kind is FormulaKind.TYPED_AGGREGATE and formula.aggregate is not None:
        return (formula.aggregate,)
    if (
        formula.kind is FormulaKind.TYPED_RATIO
        and formula.numerator is not None
        and formula.denominator is not None
    ):
        return (formula.numerator, formula.denominator)
    return None


def _computing_measure(query: ChartQuery, terms: tuple[MetricTerm, ...]) -> Measure | None:
    """A measure whose base aggregate(s) are exactly this metric's terms.

    A `transform` or `compare` is a window over the base aggregate (IR `Measure`), so the
    tile still moves precisely when the metric moves — it stays the metric's number, and the
    alias in the detail says which shape of it. A `denominator`, by contrast, makes the
    measure a different number: a ratio is matched only against a ratio, and a plain
    aggregate only against a plain one. Half of a ratio is the numerator, not the metric.
    """
    for measure in query.measures:
        if len(terms) == 1:
            if measure.denominator is None and _same_term(query.table, measure, terms[0]):
                return measure
        elif measure.denominator is not None and (
            _same_term(query.table, measure, terms[0])
            and _same_term(query.table, measure.denominator, terms[1])
        ):
            return measure
    return None


def _same_term(table: str, measure: Measure, term: MetricTerm) -> bool:
    return term.table == table and term.column == measure.column and term.agg == measure.agg


def _columns_read(query: ChartQuery) -> set[str]:
    """Every `table.column` the query names.

    Measures live on the base table and are written bare; dimension-like references are bare
    on the base table and fully qualified when they come through a join (`ir/validate.py`),
    so one qualification rule covers both. `order_by` is deliberately absent: it addresses
    SELECT aliases, not source columns, and `limit`/`bins` name no column at all.
    """
    refs: set[str] = set()
    for measure in query.measures:
        refs.add(_qualified(query.table, measure.column))
        if measure.denominator is not None:
            refs.add(_qualified(query.table, measure.denominator.column))
        if measure.compare is not None:
            refs.add(_qualified(query.table, measure.compare.column))
    for column in query.group_columns():
        refs.add(_qualified(query.table, column))
    for condition in query.filters:
        refs.add(_qualified(query.table, condition.column))
    for join in query.joins:
        refs.update((join.on_left, join.on_right))
    return refs


def _qualified(table: str, column: str) -> str:
    return column if "." in column else f"{table}.{column}"
