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
straight off the query — the IR's named columns, or operator SQL where the statement
itself attributes them. `computes_metric` is an inference: a `ChartSpec` carries no metric
ref at all, so the only defensible evidence that a tile *is* the governed number is that
its measure reproduces the formula's terms exactly. Terms are taken from both snapshots,
because the tile that computed yesterday's definition is precisely the one an owner must
look at when the definition moves. Operator SQL is not that claim: matching a metric's
typed terms would mean reading an aggregate's shape out of arbitrary SQL.

What the resolver cannot answer leaves a named gap rather than an empty list: operator
`raw_sql` whose text could not be parsed (`chart.opaque_sql`), or that parsed but selects
`*` / holds names the statement does not attribute (`chart.sql_partial`); a parseable
operator tile whose SQL does not say whether it computes a changed metric
(`chart.metric_unread` — a separate claim from a partial read); a dashboard spec the run
did not load; a formula with no typed terms; a metric whose definition was not supplied.
Parseable operator SQL that attributes every read and matches no changed field is a
resolved "not this tile", not a hole; the same answer holds for a metric whose term
columns the SQL was read in whole and does not name. Metric-derived gaps
(`chart.metric_unread`, `chart.metric_unknown`, `chart.formula_untyped`) are raised
only for a change that reaches an in-scope product: a change nobody in scope depends
on does not ask those questions. `chart.metric_unread` is scoped one link further, to
the dashboard: it is raised on a tile only when an in-scope product delivering that
exact pinned dashboard ref declares one of the changed metrics, so a change that
reaches product A does not leave an unanswered question on a dashboard only product B
delivers. Relating a tile as `computes_metric` is not a gap and still happens — a
delivered tile that computes a metric no in-scope product declares is an undeclared
dependency the report is obliged to name.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

import sqlglot
from pydantic import Field
from sqlglot import expressions as exp

from auto_bi.agent.sql_guard import (
    ColumnReads,
    _clickhouse_setting_items,
    _is_clickhouse_setting_column,
    clickhouse_in_table_fields,
    extract_column_refs,
    star_reads_columns,
)
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
    #: The query names a column the change touches. No inference: off the IR, or off
    #: operator SQL where the statement itself attributes the column.
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
    #: Why a tile is missing from `charts` where the resolver could not answer.
    #: Report-level: a gap is a property of the resolver's inputs, not of one change.
    #: Metric-derived gaps (`chart.metric_unread`, `chart.metric_unknown`,
    #: `chart.formula_untyped`) are raised only for a change that reaches an in-scope
    #: product; `chart.metric_unread`, further, only on a dashboard one of whose
    #: deliverers declares a changed metric. `computes_metric` on a change that reaches
    #: none is evidence, not a gap.
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
        for identifier in _scoped_metric_ids(product):
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
                charts=resolver.charts_for(change, reaches_product=bool(affected)),
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

    Stateful in its gaps and in a parse memo: a gap is deduplicated by `(code, subject)`
    because the same unreadable tile met by ten changes is one hole, not ten; the parse
    is a pure function of the SQL text and is reused across those changes.
    """

    def __init__(
        self,
        products: Sequence[DecisionProductSpec],
        dashboards: Mapping[str, DashboardSpec],
        metrics: Sequence[MetricNode],
    ) -> None:
        self._gaps: dict[tuple[str, str], ChartGap] = {}
        self._reads: dict[str, ColumnReads | None] = {}
        self._trees: dict[str, exp.Expr | None] = {}
        self._projection_star: dict[str, bool] = {}
        self._open_cte: dict[str, set[int]] = {}
        self._hidden: dict[str, tuple[str, ...]] = {}
        self._dictionary: dict[str, _DictionaryReads] = {}
        self._known = _terms_by_metric(metrics)
        self._loaded: list[tuple[str, DashboardSpec]] = []
        self._missing: list[str] = []
        self._declared: dict[str, frozenset[str]] = {}
        for product in products:
            # Per exact pinned dashboard ref: the union of what its deliverers declare,
            # so the metric question a dashboard was asked is decided dashboard by
            # dashboard. Two versions of one dashboard are two refs and never merge.
            scoped = frozenset(_scoped_metric_ids(product))
            for item in product.delivery.dashboards:
                self._declared[item.ref] = self._declared.get(item.ref, frozenset()) | scoped
        for ref in sorted(self._declared):
            spec = dashboards.get(ref)
            if spec is None:
                self._missing.append(ref)
            else:
                self._loaded.append((ref, spec))

    def gaps(self) -> list[ChartGap]:
        return [self._gaps[key] for key in sorted(self._gaps)]

    def charts_for(self, change: SemanticChange, *, reaches_product: bool) -> list[ChartImpact]:
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
        # Metric-derived gaps answer a question an in-scope product asked.
        # `_typed_terms(..., name_gaps=reaches_product)` withholds `chart.metric_unknown`
        # / `chart.formula_untyped`; `_match_raw` withholds `chart.metric_unread`, and
        # what it may decide on is scoped here to the dashboard — the changed metrics the
        # in-scope products delivering that exact pinned ref declare. Typed terms still
        # resolve for the whole change so an IR tile that reproduces them is named
        # `computes_metric`. Field reads run below.
        computed = self._typed_terms(change.impact.metrics, name_gaps=reaches_product)
        fields = set(change.impact.fields)
        found: dict[str, ChartImpact] = {}
        for dashboard_ref, spec in self._loaded:
            # A dashboard asks the union of what its deliverers declare: a shared one
            # asks every changed metric any of them scopes, a mixed change its own half,
            # and two versions of one dashboard are two refs that never merge.
            declared = self._declared.get(dashboard_ref, frozenset())
            asked = {
                metric_id: terms for metric_id, terms in computed.items() if metric_id in declared
            }
            for chart in spec.charts:
                ref = f"{dashboard_ref}#{chart.id}"
                if chart.query.raw_sql is not None:
                    impact = self._match_raw(
                        dashboard_ref,
                        chart.id,
                        chart.query,
                        asked,
                        fields,
                        reaches_product=reaches_product,
                    )
                else:
                    impact = self._match(dashboard_ref, chart.id, chart.query, computed, fields)
                if impact is not None:
                    found[ref] = impact
        return [found[ref] for ref in sorted(found)]

    def _match_raw(
        self,
        dashboard_ref: str,
        chart_id: str,
        query: ChartQuery,
        computed: Mapping[str, tuple[MetricTerm, ...]],
        fields: set[str],
        *,
        reaches_product: bool,
    ) -> ChartImpact | None:
        """Field reads of an operator SQL tile, attributed only where the statement decides.

        v1 is ClickHouse (CLAUDE.md: ClickHouse DM + Superset). Identifiers are compared as
        written — ClickHouse does not fold unquoted names — so an attributed read is
        `table.name.column` intersected with `change.impact.fields` exactly, the same
        comparison `_columns_read` already uses. A bare table name is qualified through
        the tile's own `query.table` so the id has the same form; see `_reconcile_sql_table`.

        `computes_metric` is not claimed here: matching a metric's typed terms would mean
        reading an aggregate's shape out of arbitrary SQL, and this resolver does not make
        that claim. The unanswered metric relation is a separate gap (`chart.metric_unread`)
        from a partial SQL read (`chart.sql_partial`): they are different claims, and folding
        them would make a tile that was only unread as a metric look like one whose columns
        were not attributed. The gap is raised only for a change that reaches an in-scope
        product, because it answers a question only such a product asks: `computed` arrives
        already scoped to that question — the changed metrics the in-scope products
        delivering this tile's dashboard declare — so a change reaching only another
        product's metric leaves this tile an answer, not a hole.
        """
        sql = query.raw_sql
        if sql is None:
            return None
        ref = f"{dashboard_ref}#{chart_id}"
        reads = self._column_reads(sql)
        if reads is None:
            self._add_gap("chart.opaque_sql", ref, "this tile's SQL could not be read")
            return None

        # `unattributed` is the honest remainder. `ambiguous` is not consulted: chart
        # impact has no semantic model to settle a name with. `starred` is whether a
        # column-reading star appears anywhere in the text, run or not, so a star in a
        # part that runs is a narrower question, asked only when sql_guard reported one.
        projection_star = reads.starred and self._has_projection_star(sql)
        # Constructs that can hide a read are asked about unconditionally: none of them
        # leaves a star or an unattributed name behind, so the reads above would look
        # complete and the tile would resolve to "not this tile".
        dictionary = self._dictionary_reads(sql)
        hidden = self._hidden_reads(sql) + dictionary.clauses
        attributed: set[str] = set()
        unreconciled: set[str] = set()
        unnamed_source = False
        for name, attribute in dictionary.reads:
            # A dictionary or Join table is addressed like a table (`db.dict` / `db.table`),
            # so its column gets the id a table column would, and a name that is not an id
            # lands where one would.
            table_id = _reconcile_sql_table(name, query.table)
            if table_id is None:
                unreconciled.add(name)
            else:
                attributed.add(f"{table_id}.{attribute}")
        for column in reads.attributed:
            # A table function (`numbers()`, `merge()`, `cluster()`) is parsed as a Table
            # with no catalog/db/identifier, so `_table_ref` yields "". Putting that in
            # `unreconciled` names nothing and two such sources collapse into one blank.
            if not column.table.name:
                unnamed_source = True
                continue
            table_id = _reconcile_sql_table(column.table.name, query.table)
            if table_id is None:
                unreconciled.add(column.table.name)
                continue
            attributed.add(f"{table_id}.{column.name}")

        unread = bool(
            projection_star
            or reads.unattributed
            or unreconciled
            or unnamed_source
            or hidden
            or reads.opaque_settings
        )
        if unread:
            self._add_gap(
                "chart.sql_partial",
                ref,
                _sql_partial_detail(
                    projection_star=projection_star,
                    unattributed=reads.unattributed,
                    unreconciled=frozenset(unreconciled),
                    unnamed_source=unnamed_source,
                    opaque_settings=_opaque_setting_clauses(reads.opaque_settings),
                    hidden=hidden,
                ),
            )

        if computed and reaches_product:
            # Separate from sql_partial: this is the metric relation we refuse to infer,
            # not a column the statement failed to attribute. Detail is independent of
            # the change because `_add_gap` keeps the first (code, subject).
            term_columns = {
                f"{term.table}.{term.column}" for terms in computed.values() for term in terms
            }
            ruled_out = not unread and term_columns.isdisjoint(attributed)
            if not ruled_out:
                self._add_gap(
                    "chart.metric_unread",
                    ref,
                    "this tile's SQL does not say whether it computes a governed metric: "
                    "the resolver does not read an aggregate's shape out of arbitrary SQL",
                )

        read = sorted(fields & attributed)
        if read:
            return ChartImpact(
                ref=ref,
                dashboard=dashboard_ref,
                chart=chart_id,
                relation=ChartRelation.READS_FIELD,
                detail=f"query reads {', '.join(read)}",
            )
        return None

    def _column_reads(self, sql: str) -> ColumnReads | None:
        """Parse operator SQL once per distinct text; `None` is a parse failure."""
        if sql not in self._reads:
            try:
                # Dialect is ClickHouse because that is the v1 engine; passing it at the
                # call site keeps the comparison rule from becoming an unstated default.
                self._reads[sql] = extract_column_refs(sql, dialect="clickhouse")
            except Exception:
                # SQLGuardError, sqlglot ParseError, or anything else the helper raises:
                # a malformed operator SQL is a gap, not a crashed gate.
                self._reads[sql] = None
        return self._reads[sql]

    def _has_projection_star(self, sql: str) -> bool:
        """A star that reads columns, in a part of the statement that runs.

        Only COUNT(*) reads no column. sqlglot wraps COUNT(DISTINCT *) as
        Count(Distinct(Star)), so its star's parent is Distinct and it reads source
        columns. Binding the exception to the direct Count parent (not
        `find_ancestor(Count)`) keeps a SELECT * inside a counted subquery a hole. A
        star inside a named CTE the FROM never names, or
        inside a scalar `WITH` whose alias no running part reads, is a query nobody
        ran; counting it would mark a fully attributed tile unread. Memoised so a
        model-wide edit does not re-walk the same tile per change.
        """
        if sql not in self._projection_star:
            root = self._parsed(sql)
            self._projection_star[sql] = (
                True
                if root is None
                else _projection_star_in_reachable(root, open_ids=self._open_cte_ids(sql))
            )
        return self._projection_star[sql]

    def _hidden_reads(self, sql: str) -> tuple[str, ...]:
        """Clauses for the constructs that can hide a column read. Memoised per text."""
        if sql not in self._hidden:
            root = self._parsed(sql)
            self._hidden[sql] = (
                ("this run could not re-read its SQL",)
                if root is None
                else _hidden_read_clauses(root)
            )
        return self._hidden[sql]

    def _dictionary_reads(self, sql: str) -> _DictionaryReads:
        """What the ClickHouse dictionary and Join-table calls the statement runs read.

        Memoised per text. Unparseable text has no calls to read; `_column_reads` has
        already named it opaque.
        """
        if sql not in self._dictionary:
            root = self._parsed(sql)
            if root is None:
                self._dictionary[sql] = _DictionaryReads()
            else:
                calls = _dictionary_calls(root)
                if not calls:
                    self._dictionary[sql] = _DictionaryReads()
                else:
                    self._dictionary[sql] = _dictionary_reads_in(
                        root, open_ids=self._open_cte_ids(sql)
                    )
        return self._dictionary[sql]

    def _open_cte_ids(self, sql: str) -> set[int]:
        """Ids of open CTE gates for this SQL; computed at most once per text."""
        if sql not in self._open_cte:
            root = self._parsed(sql)
            self._open_cte[sql] = set() if root is None else _open_cte_gates(root)
        return self._open_cte[sql]

    def _parsed(self, sql: str) -> exp.Expr | None:
        """The statement, parsed once per text; `None` when sqlglot could not parse it."""
        if sql not in self._trees:
            try:
                self._trees[sql] = sqlglot.parse_one(sql, read="clickhouse")
            except Exception:
                self._trees[sql] = None
        return self._trees[sql]

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

    def _typed_terms(
        self, metric_ids: Sequence[str], *, name_gaps: bool
    ) -> dict[str, tuple[MetricTerm, ...]]:
        typed: dict[str, tuple[MetricTerm, ...]] = {}
        for metric_id in metric_ids:
            known = self._known.get(metric_id)
            if known is None:
                if name_gaps:
                    self._add_gap(
                        "chart.metric_unknown",
                        metric_id,
                        "the metric's definition was not supplied, so no tile could be matched",
                    )
                continue
            if known.untyped and name_gaps:
                # Recorded even when the other snapshot's terms did match a tile: the half
                # that cannot be resolved is a hole, not a rounding error on the half that
                # could. A change that reaches no in-scope product does not ask this.
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


def _scoped_metric_ids(product: DecisionProductSpec) -> set[str]:
    """The metric ids a product scopes, version-stripped.

    Products pin a version; a change to the metric reaches them whatever the new version
    is. Whether the pin still resolves is `decision validate`'s question. Both halves of
    the chain strip the same way: which products a change reaches, and which changed
    metrics each delivered dashboard was asked about.
    """
    return {str(ContractRef.parse(ref).without_version()) for ref in product.scope.metrics}


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


def _sql_partial_detail(
    *,
    projection_star: bool,
    unattributed: frozenset[str],
    unreconciled: frozenset[str],
    unnamed_source: bool = False,
    opaque_settings: tuple[str, ...] = (),
    hidden: tuple[str, ...] = (),
) -> str:
    """Which part of the operator SQL the statement itself did not decide."""
    parts: list[str] = []
    if projection_star:
        parts.append("it selects *")
    if unattributed:
        names = ", ".join(sorted(unattributed))
        verb = "is" if len(unattributed) == 1 else "are"
        parts.append(f"{names} {verb} not attributed to a table")
    if unreconciled:
        names = ", ".join(sorted(name for name in unreconciled if name))
        if names:
            parts.append(f"{names} could not be reconciled with the tile's table")
    if unnamed_source:
        # Empty `_table_ref` name: a table function, not a missing word. A subquery or CTE
        # source never reaches here — sql_guard attributes the inner query's reads itself.
        parts.append("a column is read from a table function the statement does not name")
    parts.extend(opaque_settings)
    parts.extend(hidden)
    return "operator SQL was not fully resolved: " + "; ".join(parts)


def _opaque_setting_clauses(settings: frozenset[str]) -> tuple[str, ...]:
    """One unread row-filter clause for every setting the SQL reader leaves opaque."""
    return tuple(
        f"it sets {setting}, a row filter written as SQL text that this resolver does not read"
        for setting in sorted(settings)
    )


def _hidden_read_clauses(root: exp.Expr) -> tuple[str, ...]:
    """Constructs that can hide a column read, each named as an unread part.

    The rule is the contract, not the list: an expression this resolver cannot expand
    into column reads leaves the tile read in part, never resolved as "not this tile".
    Each of these leaves neither a star nor an unattributed name, so without this the
    reads would look complete — a Superset tile templated with `{{ … }}` (sqlglot parses
    the placeholder as an empty map), ClickHouse's `COLUMNS('re')` selector, which is its
    form of `*`, and a `JOIN … USING` key, which names no table to attribute it to.
    A `{% … %}` block does not parse at all and is already `chart.opaque_sql`.
    """
    clauses: list[str] = []
    if any(_is_template_placeholder(node) for node in root.find_all(exp.VarMap)):
        clauses.append("it holds a template placeholder this run cannot expand")
    if root.find(exp.Columns) is not None:
        clauses.append("it selects COLUMNS(...), which is ClickHouse's form of *")
    if any(join.args.get("using") for join in root.find_all(exp.Join)):
        clauses.append("a JOIN USING key is attributed to no table")
    return tuple(clauses)


def _is_template_placeholder(node: exp.VarMap) -> bool:
    """An empty map: what `{{ name }}` becomes. A written `map('k', v)` carries entries."""
    keys, values = node.args.get("keys"), node.args.get("values")
    return not (keys.expressions if keys else []) and not (values.expressions if values else [])


@dataclass(frozen=True)
class _DictionaryReads:
    """ClickHouse dictionary and Join-table calls a statement runs, split by what they say.

    `reads` are `(table, column)` pairs the call names in literals — a dictionary and
    attribute, or a Join table and column; `clauses` name each call whose read the
    statement does not spell out.
    """

    reads: tuple[tuple[str, str], ...] = ()
    clauses: tuple[str, ...] = ()


#: Closed set of attribute readers. `dictGet`, `dictGetAll`, the typed forms below, and
#: each of those with `OrDefault` / `OrNull`. First argument is the dictionary, second
#: the attribute or a tuple of attributes. A `dict*` name outside this set — including
#: `dictGetHierarchy` and its siblings — is a clause, not a read.
_DICT_GET_TYPES = (
    "uint8",
    "uint16",
    "uint32",
    "uint64",
    "uint128",
    "uint256",
    "int8",
    "int16",
    "int32",
    "int64",
    "int128",
    "int256",
    "float32",
    "float64",
    "decimal32",
    "decimal64",
    "decimal128",
    "decimal256",
    "date",
    "date32",
    "datetime",
    "datetime64",
    "uuid",
    "ipv4",
    "ipv6",
    "string",
    "bool",
)
_DICT_GET = re.compile(
    r"dictget(?:all|(?:" + "|".join(_DICT_GET_TYPES) + r"))?(?:ordefault|ornull)?"
)
#: Closed set of Join-table readers. ClickHouse's `joinGet` takes a single value column;
#: `joinGetOrNull` is the only sibling that still names that column. Compared lower-cased.
_JOIN_GET_READERS = frozenset({"joinget", "joingetornull"})


def _dictionary_calls(root: exp.Expr) -> list[tuple[exp.Func, str]]:
    """Functions in ClickHouse's dictionary (`dict…`) or Join-table (`joinget…`) family."""
    found: list[tuple[exp.Func, str]] = []
    for call in root.find_all(exp.Func):
        name = call.name if isinstance(call, exp.Anonymous) else call.sql_name()
        if name.lower().startswith(("dict", "joinget")):
            found.append((call, name))
    return found


def _dictionary_reads_in(root: exp.Expr, *, open_ids: set[int] | None = None) -> _DictionaryReads:
    """Name the read of each dictionary or Join-table call the statement runs, or name it unread.

    A dictionary call is any function whose name begins with `dict`; a Join-table call,
    with `joinget` — ClickHouse's two families that fetch a column while leaving
    `extract_column_refs` looking only at the key. A call that reads a column and is not
    expanded here would leave the tile looking read in whole. Two outcomes, and the second
    is the default so a function added to ClickHouse later is a hole, not silence: an
    attribute reader in `_DICT_GET` with a literal dictionary and literal attribute(s)
    resolves to `(dictionary, attribute)` reads; a Join-table reader in `_JOIN_GET_READERS`
    with a literal table and one literal column resolves to `(table, column)` — a tuple of
    columns is not ClickHouse's `joinGet` signature, so it is a clause, not a read. Any
    other call — `dictHas` and `dictIsIn` included, which read the key and parent
    attributes without naming them, or a `joinget*` name outside the reader set — is one
    clause naming it. A call the statement never runs (a named CTE the FROM never names,
    a scalar `WITH` whose alias no running part reads) is skipped. The star path uses
    the same `_runs_in_statement` predicate.

    The reachability walk runs only when there is a call to place: on this path a tile
    with no `dict…` or `joinget…` function must not enter `_runs_in_statement`. A starred
    tile still walks on the star path, whether or not it calls either family. The
    `_ChartResolver` computes the open set once per SQL and passes it here so the two
    paths do not walk twice.
    """
    calls = _dictionary_calls(root)
    if not calls:
        return _DictionaryReads()
    in_run = (
        _runs_in_statement(root)
        if open_ids is None
        else _runs_in_statement(root, open_ids=open_ids)
    )
    reads: list[tuple[str, str]] = []
    clauses: list[str] = []
    for call, name in calls:
        if not in_run(call):
            continue
        lowered = name.lower()
        if lowered.startswith("joinget"):
            named = _literal_join_get_reads(call) if lowered in _JOIN_GET_READERS else []
            unread = "Join table"
        else:
            named = _literal_dictionary_reads(call) if _DICT_GET.fullmatch(lowered) else []
            unread = "dictionary"
        if named:
            reads.extend(pair for pair in named if pair not in reads)
            continue
        clause = f"it calls {name}(...), whose {unread} read this run could not name"
        if clause not in clauses:
            clauses.append(clause)
    return _DictionaryReads(reads=tuple(reads), clauses=tuple(clauses))


def _literal_dictionary_reads(call: exp.Func) -> list[tuple[str, str]]:
    """`(dictionary, attribute)` pairs when both are string literals; empty otherwise."""
    args = call.expressions
    if len(args) < 2 or not (isinstance(args[0], exp.Literal) and args[0].is_string):
        return []
    attributes = args[1].expressions if isinstance(args[1], exp.Tuple) else [args[1]]
    if not attributes or not all(
        isinstance(item, exp.Literal) and item.is_string for item in attributes
    ):
        return []
    return [(args[0].name, item.name) for item in attributes]


def _literal_join_get_reads(call: exp.Func) -> list[tuple[str, str]]:
    """`(table, column)` when both are one string literal; empty otherwise.

    ClickHouse's joinGet takes a single value column; a tuple in the second argument is
    not a read.
    """
    args = call.expressions
    if len(args) < 2:
        return []
    table, column = args[0], args[1]
    if not (
        isinstance(table, exp.Literal)
        and table.is_string
        and isinstance(column, exp.Literal)
        and column.is_string
    ):
        return []
    return [(table.name, column.name)]


def _runs_in_statement(
    root: exp.Expr, *, open_ids: set[int] | None = None
) -> Callable[[exp.Expr], bool]:
    """Whether a node sits in a part of the statement that runs.

    The gates are the statement's `exp.CTE` nodes: named CTEs and ClickHouse scalar
    `WITH` (both `WITH <expr> AS alias` and `WITH (SELECT …) AS alias`). A node runs
    iff every CTE among its ancestors is open. Nothing else is a gate: a subquery,
    derived table, UNION branch, LATERAL / UNNEST / VALUES, or a query nested inside
    a scalar `WITH` body runs when what encloses it runs.

    A named CTE opens when a running `exp.Table` without a database or catalog names
    it (case-insensitive) and the CTE's declaring query is an ancestor of that table,
    or when a running bare Column in `In.args["field"]` that is not a visible alias
    names it — ClickHouse reads that as `IN (SELECT * FROM <name>)`, the same
    unqualified table. A scalar CTE opens when a running column names it
    (case-insensitive), the column
    is not inside that CTE, and the declaring query is an ancestor of the column.
    A column names the alias through its leftmost written part: catalog if set, else
    db, else table, else its own name (a bare column). A tuple access at any depth
    (`t.1`, `t.name`, `t.a.b`) therefore reads the alias. A table alias or a database
    qualifier with the same name therefore opens the scalar `WITH` as well — an
    over-report, the direction that hides nothing. Start with every gate closed. A
    name referenced only from inside the gate it would open cannot open it, so a
    cycle nobody outside reads stays closed.

    A nested scalar WITH declaration shadows an outer one when the reference binds
    unambiguously. Named CTE shadowing and references inside their own scalar WITH
    definition stay conservative and can still over-report.
    """
    resolved_ids = _open_cte_gates(root) if open_ids is None else open_ids

    def in_run(node: exp.Expr) -> bool:
        return _node_runs(node, resolved_ids)

    return in_run


def _ancestors(node: exp.Expr) -> Iterator[exp.Expr]:
    current = node.parent
    while current is not None:
        yield current
        current = current.parent


def _node_runs(node: exp.Expr, open_ids: set[int]) -> bool:
    """A node runs iff every `exp.CTE` among its ancestors is open."""
    return all(
        not isinstance(ancestor, exp.CTE) or id(ancestor) in open_ids
        for ancestor in _ancestors(node)
    )


def _declaring_query(cte: exp.CTE) -> exp.Expr | None:
    """The query that owns this WITH: CTE → With → Select or Union."""
    parent = cte.parent
    if parent is None:
        return None
    return parent.parent


@dataclass(frozen=True, slots=True)
class _CteGate:
    cte_id: int
    declaring_id: int


@dataclass(frozen=True, slots=True)
class _CteRef:
    """A name that can open a CTE gate once it sits in a running part of the statement."""

    name: str
    enclosing: frozenset[int]
    ancestors: frozenset[int]
    named: bool
    scalar_declaring_id: int | None = None


def _enclosing_and_ancestors(node: exp.Expression) -> tuple[frozenset[int], frozenset[int]]:
    """CTE ids among *node*'s ancestors, and the ids of every ancestor."""
    enclosing: list[int] = []
    ancestors: list[int] = []
    for ancestor in _ancestors(node):
        ancestors.append(id(ancestor))
        if isinstance(ancestor, exp.CTE):
            enclosing.append(id(ancestor))
    return frozenset(enclosing), frozenset(ancestors)


def _column_leftmost_name(column: exp.Column) -> str:
    """The leftmost written part of a column: catalog, else db, else table, else name.

    sqlglot parks a deeper tuple access in the qualifier slots (`t.a.b` is
    Column(this=b, table=a, db=t); `t.a.b.c` puts `t` in catalog). Scalar gates
    read that leftmost part as the candidate alias.
    """
    for part in (column.catalog, column.db, column.table, column.name):
        if part:
            return part.lower()
    return ""


def _open_cte_gates(root: exp.Expr) -> set[int]:
    """Ids of CTE gates that a running part of the statement opens.

    Linear worklist: each reference is processed once, each gate opens once. A name
    opens every named CTE it could name, compared case-insensitively, whose
    declaring query encloses the name. A column names a scalar alias through its
    leftmost written part, so a tuple access at any depth (`t.1`, `t.a.b`) opens
    the nearest scalar declaration; same-level duplicates and self-references stay
    conservative. Named CTEs open through an unqualified table and through a bare
    `IN <name>` that is not a visible alias.
    """
    named_by_alias: dict[str, list[_CteGate]] = defaultdict(list)
    scalar_by_alias: dict[str, list[_CteGate]] = defaultdict(list)
    for cte in root.find_all(exp.CTE):
        alias = cte.alias.lower()
        if not alias:
            continue
        declaring = _declaring_query(cte)
        if declaring is None:
            continue
        gate = _CteGate(id(cte), id(declaring))
        if cte.args.get("scalar"):
            scalar_by_alias[alias].append(gate)
        else:
            named_by_alias[alias].append(gate)
    if not named_by_alias and not scalar_by_alias:
        return set()
    scalar_declaring_ids = {
        alias: {gate.declaring_id for gate in gates} for alias, gates in scalar_by_alias.items()
    }

    setting_items = frozenset(id(item) for item in _clickhouse_setting_items(root))
    refs: list[_CteRef] = []
    for table in root.find_all(exp.Table):
        if table.db or table.catalog or not table.name:
            continue
        enclosing, ancestors = _enclosing_and_ancestors(table)
        refs.append(_CteRef(table.name.lower(), enclosing, ancestors, True))
    for column in root.find_all(exp.Column):
        if _is_clickhouse_setting_column(column, setting_items):
            continue
        alias = _column_leftmost_name(column)
        if not alias:
            continue
        enclosing, ancestors = _enclosing_and_ancestors(column)
        nearest_declaring = None
        declaring_ids = scalar_declaring_ids.get(alias)
        if declaring_ids:
            nearest_declaring = next(
                (id(ancestor) for ancestor in _ancestors(column) if id(ancestor) in declaring_ids),
                None,
            )
            if any(
                gate.declaring_id == nearest_declaring and gate.cte_id in enclosing
                for gate in scalar_by_alias[alias]
            ):
                nearest_declaring = None
        refs.append(_CteRef(alias, enclosing, ancestors, False, nearest_declaring))
    if named_by_alias:
        for column in clickhouse_in_table_fields(root):
            if column.table or column.db or column.catalog or not column.name:
                continue
            enclosing, ancestors = _enclosing_and_ancestors(column)
            refs.append(_CteRef(column.name.lower(), enclosing, ancestors, True))

    waiting: dict[int, list[int]] = defaultdict(list)
    for index, ref in enumerate(refs):
        for cte_id in ref.enclosing:
            waiting[cte_id].append(index)

    open_ids: set[int] = set()
    queued = [not ref.enclosing for ref in refs]
    work: deque[int] = deque(index for index, ready in enumerate(queued) if ready)

    def _open_gate(gate: _CteGate, ref: _CteRef) -> None:
        if gate.cte_id in open_ids:
            return
        if gate.declaring_id not in ref.ancestors:
            return
        if not ref.named and gate.cte_id in ref.enclosing:
            return
        open_ids.add(gate.cte_id)
        for waiter in waiting.get(gate.cte_id, ()):
            if not queued[waiter] and refs[waiter].enclosing <= open_ids:
                queued[waiter] = True
                work.append(waiter)

    while work:
        ref = refs[work.popleft()]
        if ref.named:
            for gate in named_by_alias.get(ref.name, ()):
                _open_gate(gate, ref)
        else:
            for gate in scalar_by_alias.get(ref.name, ()):
                if (
                    ref.scalar_declaring_id is not None
                    and gate.declaring_id != ref.scalar_declaring_id
                ):
                    continue
                _open_gate(gate, ref)
    return open_ids


def _projection_star_in_reachable(root: exp.Expr, *, open_ids: set[int] | None = None) -> bool:
    """True when a column-reading star sits in a part of the statement that runs.

    This is `_runs_in_statement` applied to each star — the same predicate as the
    dictionary and Join-table path. `open_ids` is the set `_ChartResolver` shares
    with that path; omitted, it is computed here.
    """
    in_run = (
        _runs_in_statement(root)
        if open_ids is None
        else _runs_in_statement(root, open_ids=open_ids)
    )
    if any(star_reads_columns(star) and in_run(star) for star in root.find_all(exp.Star)):
        return True
    # ClickHouse `IN <table>` is `IN (SELECT * FROM <table>)`; sqlglot leaves no Star.
    return any(in_run(column) for column in clickhouse_in_table_fields(root))


def _reconcile_sql_table(sql_table: str, query_table: str) -> str | None:
    """Place a SQL table name in the same id form `_qualified` produces for IR columns.

    `change.impact.fields` is always schema-qualified (`dm.sales_daily.revenue`). The IR
    path gets that form through `_qualified(query.table, column)`. This path must agree:
    a match is an exact string compare, and a different form would silently answer
    "not this tile" for a tile that reads the column.

    A name this helper did not reconcile is not handed back — three shapes, one rule:

    * A two-part name (`schema.table`) is already in the id form and is used as written.
    * A longer name is the tile's table when it ends with `"." + query_table` — the same
      table written with a catalog prefix (`cat.dm.sales_daily` for `dm.sales_daily`).
      Any other longer name is not that table and is not an id `fields` can match.
    * A bare name is the tile's own table only when it equals that table's unqualified
      part — ClickHouse will read `FROM sales_daily` under the default database, and
      inventing a schema for any other bare name would pretend the statement decided
      what it did not.
    """
    dots = sql_table.count(".")
    if dots > 1:
        return query_table if sql_table.endswith("." + query_table) else None
    if dots == 1:
        return sql_table
    schema, sep, own = query_table.rpartition(".")
    if sql_table != own:
        return None
    return f"{schema}.{sql_table}" if sep else sql_table


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
