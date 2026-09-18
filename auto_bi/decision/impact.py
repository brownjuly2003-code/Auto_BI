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
on does not ask those questions. Relating a tile as `computes_metric` is not a gap
and still happens — a delivered tile that computes a metric no in-scope product
declares is an undeclared dependency the report is obliged to name.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

import sqlglot
from pydantic import Field
from sqlglot import expressions as exp
from sqlglot.optimizer.scope import Scope, build_scope

from auto_bi.agent.sql_guard import ColumnReads, extract_column_refs
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
    #: product; `computes_metric` on a change that reaches none is evidence, not a gap.
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
        self._trees: dict[str, exp.Expression | None] = {}
        self._projection_star: dict[str, bool] = {}
        self._hidden: dict[str, tuple[str, ...]] = {}
        self._dictionary: dict[str, _DictionaryReads] = {}
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
        # Metric-derived gaps answer a question an in-scope product asked. One flag
        # is read at two sites: `_typed_terms(..., name_gaps=reaches_product)` withholds
        # `chart.metric_unknown` / `chart.formula_untyped`, and `_match_raw` withholds
        # `chart.metric_unread`. Typed terms still resolve so an IR tile that
        # reproduces them is named `computes_metric`. Field reads run below.
        computed = self._typed_terms(change.impact.metrics, name_gaps=reaches_product)
        fields = set(change.impact.fields)
        found: dict[str, ChartImpact] = {}
        for dashboard_ref, spec in self._loaded:
            for chart in spec.charts:
                ref = f"{dashboard_ref}#{chart.id}"
                if chart.query.raw_sql is not None:
                    impact = self._match_raw(
                        dashboard_ref,
                        chart.id,
                        chart.query,
                        computed,
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
        product, because it answers a question only such a product asks.
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
        # star appears at all — COUNT(*) included — so a projection star is a narrower
        # question, asked only when sql_guard reported one.
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
            # A dictionary is addressed like a table (`db.dict`), so its attribute gets the
            # id a table column would, and a name that is not an id lands where one would.
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
            projection_star or reads.unattributed or unreconciled or unnamed_source or hidden
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
        """A star that reads columns, in a scope the statement actually runs.

        COUNT(*) and COUNT(DISTINCT *) read no column — sqlglot wraps the latter as
        Count(Distinct(Star)), so the star's parent is Distinct, not Count. Walking
        through that wrapper (not `find_ancestor(Count)`) keeps a SELECT * inside a
        counted subquery a hole. A star inside a CTE the FROM never names is a query
        nobody ran (`sql_guard._read_sources`); counting it would mark a fully
        attributed tile unread. Memoised so a model-wide edit does not re-walk the
        same tile per change.
        """
        if sql not in self._projection_star:
            root = self._parsed(sql)
            self._projection_star[sql] = (
                True if root is None else _projection_star_in_reachable(root)
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
        """What the ClickHouse dictionary calls the statement runs read. Memoised per text.

        Unparseable text has no calls to read; `_column_reads` has already named it opaque.
        """
        if sql not in self._dictionary:
            root = self._parsed(sql)
            self._dictionary[sql] = (
                _DictionaryReads() if root is None else _dictionary_reads_in(root)
            )
        return self._dictionary[sql]

    def _parsed(self, sql: str) -> exp.Expression | None:
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
    parts.extend(hidden)
    return "operator SQL was not fully resolved: " + "; ".join(parts)


def _hidden_read_clauses(root: exp.Expression) -> tuple[str, ...]:
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
    """ClickHouse dictionary calls in the scopes a statement runs, split by what they say.

    `reads` are `(dictionary, attribute)` pairs the call names in literals; `clauses` name
    each call whose read the statement does not spell out.
    """

    reads: tuple[tuple[str, str], ...] = ()
    clauses: tuple[str, ...] = ()


#: The attribute readers: `dictGet`, its typed forms (`dictGetString`, `dictGetUInt64`, …)
#: and either with `OrDefault` / `OrNull`. Their first argument is the dictionary, the
#: second the attribute or a tuple of attributes. The type list is closed on purpose:
#: `dictGetHierarchy` and its siblings share the prefix and name no attribute they read.
_DICT_GET = re.compile(
    r"dictget(?:u?int(?:8|16|32|64)|float(?:32|64)|date|datetime|uuid|ipv4|ipv6|string)?"
    r"(?:ordefault|ornull)?"
)


def _dictionary_reads_in(root: exp.Expression) -> _DictionaryReads:
    """Name the read of each dictionary call the statement runs, or name the call as unread.

    A dictionary call is any function whose name begins with `dict`, ClickHouse's
    dictionary family. `extract_column_refs` sees only its key column, so a call that
    reads an attribute and is not expanded here would leave the tile looking read in whole.
    Two outcomes, and the second is the default so a dictionary function added to
    ClickHouse later is a hole, not silence: an attribute reader with a literal dictionary
    and literal attribute(s) resolves to `(dictionary, attribute)` reads; any other call —
    `dictHas` and `dictIsIn` included, which read the key and parent attributes without
    naming them — is one clause naming it. A call the statement never runs (a CTE nobody
    names, a scalar `WITH` alias nobody reads) is skipped, as `_has_projection_star` skips
    a star there.
    """
    in_run = _runs_in_statement(root)
    reads: list[tuple[str, str]] = []
    clauses: list[str] = []
    for call in root.find_all(exp.Func):
        name = call.name if isinstance(call, exp.Anonymous) else call.sql_name()
        lowered = name.lower()
        if not lowered.startswith("dict") or not in_run(call):
            continue
        named = _literal_dictionary_reads(call) if _DICT_GET.fullmatch(lowered) else []
        if named:
            reads.extend(pair for pair in named if pair not in reads)
            continue
        clause = f"it calls {name}(...), which reads a dictionary without naming the attribute"
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


def _runs_in_statement(root: exp.Expression) -> Callable[[exp.Expression], bool]:
    """Whether a node sits in a scope the statement reads (see `_reachable_scopes`).

    Without a scope tree every node counts, and so does a node no scope owns: for a
    dictionary call the careful answer is that it ran — skipping it would be silence. The
    one exception is ClickHouse's scalar `WITH <expr> AS alias`: a CTE whose body is not a
    query, so no scope owns it, and it runs only when the alias is read elsewhere.
    """
    root_scope = build_scope(root)
    if root_scope is None:
        return lambda _node: True
    owners = {id(scope.expression): scope for scope in root_scope.traverse()}
    reachable = {id(scope) for scope in _reachable_scopes(root_scope)}

    def in_run(node: exp.Expression) -> bool:
        current: exp.Expression | None = node.parent
        while current is not None:
            if (
                isinstance(current, exp.CTE)
                and not isinstance(current.this, exp.Query)
                and not _scalar_alias_is_read(root, current)
            ):
                return False
            owner = owners.get(id(current))
            if owner is not None:
                return id(owner) in reachable
            current = current.parent
        return True

    return in_run


def _scalar_alias_is_read(root: exp.Expression, cte: exp.CTE) -> bool:
    """Whether a column outside the scalar `WITH` names its alias (case-insensitive)."""
    alias = cte.alias.lower()
    return any(
        not column.table
        and column.name.lower() == alias
        and not any(ancestor is cte for ancestor in _ancestors(column))
        for column in root.find_all(exp.Column)
    )


def _ancestors(node: exp.Expression) -> Iterator[exp.Expression]:
    current = node.parent
    while current is not None:
        yield current
        current = current.parent


def _star_is_count_arg(star: exp.Star) -> bool:
    """COUNT(*) / COUNT(DISTINCT *) read no column; any other star is a hole.

    Bound to the immediate aggregate: `find_ancestor(Count)` would treat
    `SELECT count(*) FROM (SELECT * FROM t)` as a count-star and hide the inner `*`.
    """
    parent = star.parent
    if isinstance(parent, exp.Distinct):
        parent = parent.parent
    return isinstance(parent, exp.Count)


def _reachable_scopes(root_scope: Scope) -> list[Scope]:
    """Scopes the executed statement reads — FROM/JOIN, used CTEs, subqueries, unions.

    `Scope.traverse()` includes a CTE nobody named. sql_guard._read_sources documents
    that shape as a query nobody ran; walking it here would mark a decidable tile unread.
    """
    seen: set[int] = set()
    stack = [root_scope]
    reachable: list[Scope] = []
    while stack:
        scope = stack.pop()
        if id(scope) in seen:
            continue
        seen.add(id(scope))
        reachable.append(scope)
        cte_sources = scope.cte_sources
        for alias, node in scope.references:
            if (
                isinstance(node, exp.Table)
                and not node.db
                and not node.catalog
                and node.name in cte_sources
            ):
                source = cte_sources[node.name]
                if isinstance(source, Scope):
                    stack.append(source)
            elif not isinstance(node, exp.Table):
                source = scope.sources.get(alias)
                if isinstance(source, Scope):
                    stack.append(source)
        stack.extend(scope.subquery_scopes)
        stack.extend(scope.union_scopes)
        stack.extend(scope.udtf_scopes)
        stack.extend(scope.derived_table_scopes)
    return reachable


def _projection_star_in_reachable(root: exp.Expression) -> bool:
    """True when a non-COUNT star appears in a scope the statement actually reads."""
    root_scope = build_scope(root)
    if root_scope is None:
        return any(not _star_is_count_arg(star) for star in root.find_all(exp.Star))
    owners = {id(scope.expression): scope for scope in root_scope.traverse()}
    reachable = {id(scope) for scope in _reachable_scopes(root_scope)}
    for star in root.find_all(exp.Star):
        if _star_is_count_arg(star):
            continue
        node: exp.Expression | None = star.parent
        while node is not None:
            owner = owners.get(id(node))
            if owner is not None:
                if id(owner) in reachable:
                    return True
                break
            node = node.parent
    return False


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
