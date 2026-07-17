"""ClickHouse rule pack (ARCHITECTURE §3.3, D9): mechanisms, not enumerated cases.

Each rule reads the DM's physical metadata (`sorting_key`, `partition_key`, engine,
rows, cardinality) and/or the measured EXPLAIN evidence, and decides a verdict. The
LLM never invents these performance facts — it only narrates the findings (task 1.7).

Phase 1 has no joins, so `join_large_large` is intentionally absent (it arrives with
join support); `point_lookup_pattern` from PLAN 1.6 is deferred for the same reason —
no real case to tune it against yet. Rules stay silent on clean queries — warning
fatigue is the enemy.

Filter rules read `ctx.query.filters`, and `ctx.query` is the EFFECTIVE query: the chart's own
filters plus the dashboard controls that actually narrow it (`advisor/effective.py`, P1-2).
Native dashboard filters landed in Phase 2, so reasoning over the spec's verbatim query — as
these rules once did — false-positives on specs whose period lives solely in a control. Rules
need not know where a filter came from: they judge what the BI runs, which is also what the
EXPLAIN evidence measured.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from auto_bi.advisor.findings import Finding, Remediation, Severity, VerdictClass
from auto_bi.ir.spec import ChartQuery
from auto_bi.semantic.model import Physical, SemanticModel, Table

# thresholds — tuned to the demo-DM scale; revisit with real DMs
LARGE_FACT_ROWS = 10_000_000
HIGH_CARDINALITY = 100_000
SCAN_FRACTION_WARN = 0.5
SCAN_FRACTION_CRITICAL = 0.9
COLLAPSING_ENGINES = ("Replacing", "Collapsing", "VersionedCollapsing")


@dataclass
class RuleContext:
    chart_id: str
    # the EFFECTIVE query (advisor/effective.py): the chart's filters plus the dashboard
    # controls that narrow it. Deliberately not the spec's verbatim query — a rule that
    # reasoned over the latter would report a full scan on a chart the dashboard opens
    # filtered, which is the P1-2 bug. `Advisor.review_chart` builds it.
    query: ChartQuery
    table: Table
    physical: Physical
    evidence: dict = field(default_factory=dict)  # EXPLAIN-derived facts, may be empty
    # the whole model — only rules that reason across tables (join_large_large) need it;
    # None keeps metadata-only callers and the existing single-table rules unaffected
    model: SemanticModel | None = None

    @property
    def filter_columns(self) -> list[str]:
        return [f.column for f in self.query.filters]


def _projection_name(columns: list[str]) -> str:
    """Stable ClickHouse projection identifier from the filtered columns."""
    bare = [c.rpartition(".")[2] for c in columns]
    return "p_by_" + "_".join(bare)


def _size_severity(rows: int) -> Severity:
    return Severity.CRITICAL if rows >= LARGE_FACT_ROWS else Severity.WARN


def explain_high_scan_fraction(ctx: RuleContext) -> list[Finding]:
    """Measured: EXPLAIN ESTIMATE says the query reads most of the table."""
    est_rows = ctx.evidence.get("est_rows")
    total = ctx.physical.rows
    if not est_rows or not total:
        return []
    fraction = est_rows / total
    if fraction < SCAN_FRACTION_WARN:
        return []
    severity = Severity.CRITICAL if fraction >= SCAN_FRACTION_CRITICAL else Severity.WARN
    # est_rows sums EVERY pass over the table, so a query that reads it more than once — a
    # period-compare scans the current window and the prior one — lands above 100%. Phrasing
    # that as a share of the table ("reads ~146% of X") reads like a broken number and costs
    # the finding its credibility, so above 1 we report passes instead of a share.
    if fraction > 1:
        title = (
            f"query reads {est_rows} rows from {ctx.table.name} — {fraction:.1f}× its "
            f"{total} rows (more than one pass over the table)"
        )
    else:
        title = f"query reads ~{fraction:.0%} of {ctx.table.name} ({est_rows} of {total} rows)"
    return [
        Finding(
            rule="explain_high_scan_fraction",
            severity=severity,
            verdict_class=VerdictClass.SPEC_ADJUSTMENT,
            chart_id=ctx.chart_id,
            title=title,
            evidence={
                "est_rows": est_rows,
                "total_rows": total,
                "scan_fraction": round(fraction, 3),
                "period_compare": _is_period_compare(ctx),
            },
            suggestions=_scan_suggestions(ctx),
        )
    ]


def _is_period_compare(ctx: RuleContext) -> bool:
    """A scalar period-compare KPI (`Measure.compare`) — one number vs a period back."""
    return any(m.compare is not None for m in ctx.query.measures)


def _scan_suggestions(ctx: RuleContext) -> list[str]:
    """What to actually do about a heavy scan — which differs by why it is heavy.

    A period-compare reads the current window AND the prior one by construction, and SQL_GEN
    widens its outer scan on purpose to reach the prior bucket. Telling its author to "narrow
    the time range or add a filter" is wrong twice over: the window is already filtered, and
    narrowing it is what the widening exists to undo. The honest lever is the DM — compare
    over pre-aggregated buckets instead of raw rows.
    """
    if _is_period_compare(ctx):
        return [
            "a period-compare reads the current window and the prior one — that second pass is "
            "inherent to the question, not a missing filter; to cut it, expose a pre-aggregated "
            "rollup of this measure in the DM so the compare scans buckets instead of raw rows"
        ]
    return ["narrow the time range or add a filter on a sorting-key/partition column"]


def filter_not_in_sorting_key_prefix(ctx: RuleContext) -> list[Finding]:
    """Filters exist but the leading sorting-key column isn't among them -> the primary
    index can't prune. If none of the filters is in the key at all, it's a DM mismatch."""
    sk = ctx.physical.sorting_key
    filters = ctx.filter_columns
    if not sk or not filters:
        return []
    leading = sk[0]
    if leading in filters:
        return []  # prefix used -> index works
    in_key = [c for c in filters if c in sk]
    remediation: Remediation | None = None
    if in_key:
        verdict = VerdictClass.SPEC_ADJUSTMENT
        title = f"filters {filters} miss the leading sorting key {leading!r} on {ctx.table.name}"
        suggestions = [f"add a filter on {leading!r} (leading sorting key)"]
    else:
        verdict = VerdictClass.DM_CHANGE_REQUEST
        title = f"filtered columns {filters} are not in the sorting key {sk} of {ctx.table.name}"
        suggestions = [
            f"this access path isn't in the DM design (sorting key {sk}); "
            "needs a projection or a different ORDER BY"
        ]
        # concrete fix: a ClickHouse projection that physically re-orders the data by the
        # filtered columns, so this access path gets its own primary index inside the table
        proj = _projection_name(ctx.filter_columns)
        order_cols = ", ".join(c.rpartition(".")[2] for c in ctx.filter_columns)
        remediation = Remediation(
            kind="ch_projection",
            summary=f"ClickHouse projection {proj!r} on {ctx.table.name} ordered by ({order_cols})",
            ddl=(
                f"ALTER TABLE {ctx.table.name}\n"
                f"  ADD PROJECTION {proj} (SELECT * ORDER BY {order_cols});\n"
                f"ALTER TABLE {ctx.table.name} MATERIALIZE PROJECTION {proj};"
            ),
            rationale=(
                f"the table is sorted by {sk}, so a filter on {ctx.filter_columns} can't use the "
                f"primary index; a projection ordered by ({order_cols}) gives that access path its "
                "own sorted copy without changing the base table's ORDER BY"
            ),
        )
    return [
        Finding(
            rule="filter_not_in_sorting_key_prefix",
            severity=_size_severity(ctx.physical.rows),
            verdict_class=verdict,
            chart_id=ctx.chart_id,
            title=title,
            evidence={"sorting_key": sk, "filter_columns": filters, "leading_key": leading},
            suggestions=suggestions,
            remediation=remediation,
        )
    ]


def _partition_columns(physical: Physical, table: Table) -> list[str]:
    """Columns referenced by the partition expression, e.g. toYYYYMM(date) -> [date]."""
    pk = physical.partition_key
    if not pk:
        return []
    return [c.name for c in table.columns if c.name in pk]


def partition_misaligned_filter(ctx: RuleContext) -> list[Finding]:
    """Filters exist but none on the partition column -> no partition pruning."""
    pk_cols = _partition_columns(ctx.physical, ctx.table)
    filters = ctx.filter_columns
    if not pk_cols or not filters:
        return []
    if set(filters) & set(pk_cols):
        return []  # partition pruning possible
    return [
        Finding(
            rule="partition_misaligned_filter",
            severity=_size_severity(ctx.physical.rows),
            verdict_class=VerdictClass.SPEC_ADJUSTMENT,
            chart_id=ctx.chart_id,
            title=f"no filter on partition column(s) {pk_cols} -> every partition scanned",
            evidence={"partition_key": ctx.physical.partition_key, "partition_columns": pk_cols},
            suggestions=[f"add a filter on {pk_cols[0]!r} to prune partitions"],
        )
    ]


def no_filter_on_large_fact(ctx: RuleContext) -> list[Finding]:
    """A large fact queried with no filters at all -> full scan on every refresh."""
    if ctx.query.filters or ctx.physical.rows < LARGE_FACT_ROWS:
        return []
    return [
        Finding(
            rule="no_filter_on_large_fact",
            severity=Severity.CRITICAL,
            verdict_class=VerdictClass.SPEC_ADJUSTMENT,
            chart_id=ctx.chart_id,
            title=f"{ctx.table.name} has {ctx.physical.rows} rows and the query has no filters",
            evidence={"rows": ctx.physical.rows},
            suggestions=["add a time/date filter to bound the scan"],
        )
    ]


def group_by_high_cardinality(ctx: RuleContext) -> list[Finding]:
    """GROUP BY a very high-cardinality column -> huge result set and memory."""
    findings: list[Finding] = []
    for col in ctx.query.group_columns():
        card = ctx.physical.cardinality.get(col)
        if card is None or card < HIGH_CARDINALITY:
            continue
        findings.append(
            Finding(
                rule="group_by_high_cardinality",
                severity=Severity.WARN,
                verdict_class=VerdictClass.SPEC_ADJUSTMENT,
                chart_id=ctx.chart_id,
                title=f"GROUP BY {col!r} has ~{card} distinct values -> large result set",
                evidence={"column": col, "cardinality": card},
                suggestions=[f"aggregate {col!r} to a coarser grain or add a row limit"],
            )
        )
    return findings


def collapsing_engine_needs_final(ctx: RuleContext) -> list[Finding]:
    """Replacing/Collapsing engines can double-count without FINAL; SQL_GEN omits it."""
    engine = ctx.physical.table_engine or ""
    if not any(marker in engine for marker in COLLAPSING_ENGINES):
        return []
    return [
        Finding(
            rule="collapsing_engine_needs_final",
            severity=Severity.WARN,
            verdict_class=VerdictClass.SPEC_ADJUSTMENT,
            chart_id=ctx.chart_id,
            title=f"{ctx.table.name} is {engine}; aggregates may double-count without FINAL",
            evidence={"table_engine": engine},
            suggestions=["confirm the DM exposes a merged/FINAL view, or aggregate accordingly"],
        )
    ]


def join_large_large(ctx: RuleContext) -> list[Finding]:
    """A join between two large tables. ClickHouse builds the RIGHT side into an in-memory
    hash table for every query, so joining two big facts is a memory/latency cliff that no
    filter fixes — the DM answer is a denormalised mart that pre-joins them. Needs the model
    to size the joined tables; silent without it (metadata-only callers) or on fact->small-dim
    joins (the common, cheap case)."""
    if not ctx.query.joins or ctx.model is None or ctx.physical.rows < LARGE_FACT_ROWS:
        return []
    big = [
        (j, jt)
        for j in ctx.query.joins
        if (jt := ctx.model.table(j.table)) is not None
        and jt.physical is not None
        and jt.physical.rows >= LARGE_FACT_ROWS
    ]
    if not big:
        return []  # joined tables are small dimensions -> a broadcast hash join is cheap
    joined_names = [jt.name for _, jt in big]
    order_key = ", ".join(ctx.physical.sorting_key) or "<sorting key>"
    join_clauses = "\n".join(f"LEFT JOIN {j.table} ON {j.on_left} = {j.on_right}" for j, _ in big)
    wide = f"{ctx.table.name}__wide"
    remediation = Remediation(
        kind="denormalised_mart",
        summary=f"denormalised mart {wide!r} pre-joining {ctx.table.name} + {joined_names}",
        ddl=(
            f"-- Денормализующая витрина: предзаджойнить большие таблицы один раз при сборке,\n"
            f"-- чтобы убрать large×large join из каждого запроса дашборда.\n"
            f"CREATE TABLE {wide}\n"
            f"ENGINE = MergeTree ORDER BY ({order_key})\n"
            f"AS SELECT *\n"
            f"FROM {ctx.table.name}\n"
            f"{join_clauses};"
        ),
        rationale=(
            "ClickHouse loads the right-hand table of a join into memory per query; with both "
            f"sides ≥ {LARGE_FACT_ROWS} rows this dominates latency. A mart that materialises the "
            "join once turns it into a single sorted scan."
        ),
    )
    return [
        Finding(
            rule="join_large_large",
            severity=Severity.CRITICAL,
            verdict_class=VerdictClass.DM_CHANGE_REQUEST,
            chart_id=ctx.chart_id,
            title=(
                f"join of {ctx.table.name} ({ctx.physical.rows} rows) with large table(s) "
                f"{joined_names} -> in-memory hash join on every refresh"
            ),
            evidence={
                "base_rows": ctx.physical.rows,
                "joined_tables": {
                    jt.name: jt.physical.rows for _, jt in big if jt.physical is not None
                },
            },
            suggestions=["pre-join into a denormalised mart instead of joining at query time"],
            remediation=remediation,
        )
    ]


# the ClickHouse rule pack (mechanisms); EXPLAIN evidence first, then metadata rules
RULES: tuple[Callable[[RuleContext], list[Finding]], ...] = (
    explain_high_scan_fraction,
    filter_not_in_sorting_key_prefix,
    partition_misaligned_filter,
    no_filter_on_large_fact,
    group_by_high_cardinality,
    collapsing_engine_needs_final,
    join_large_large,
)
