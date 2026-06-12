"""ClickHouse rule pack (ARCHITECTURE §3.6, D9): mechanisms, not enumerated cases.

Each rule reads the DM's physical metadata (`sorting_key`, `partition_key`, engine,
rows, cardinality) and/or the measured EXPLAIN evidence, and decides a verdict. The
LLM never invents these performance facts — it only narrates the findings (task 1.7).

Phase 1 has no joins, so `join_large_large` is intentionally absent (it arrives with
join support). Rules stay silent on clean queries — warning fatigue is the enemy.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from auto_bi.advisor.findings import Finding, Severity, VerdictClass
from auto_bi.ir.spec import ChartQuery
from auto_bi.semantic.model import Physical, Table

# thresholds — tuned to the demo-DM scale; revisit with real DMs
LARGE_FACT_ROWS = 10_000_000
HIGH_CARDINALITY = 100_000
SCAN_FRACTION_WARN = 0.5
SCAN_FRACTION_CRITICAL = 0.9
COLLAPSING_ENGINES = ("Replacing", "Collapsing", "VersionedCollapsing")


@dataclass
class RuleContext:
    chart_id: str
    query: ChartQuery
    table: Table
    physical: Physical
    evidence: dict = field(default_factory=dict)  # EXPLAIN-derived facts, may be empty

    @property
    def filter_columns(self) -> list[str]:
        return [f.column for f in self.query.filters]


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
    return [
        Finding(
            rule="explain_high_scan_fraction",
            severity=severity,
            verdict_class=VerdictClass.SPEC_ADJUSTMENT,
            chart_id=ctx.chart_id,
            title=f"query reads ~{fraction:.0%} of {ctx.table.name} ({est_rows} of {total} rows)",
            evidence={
                "est_rows": est_rows,
                "total_rows": total,
                "scan_fraction": round(fraction, 3),
            },
            suggestions=["narrow the time range or add a filter on a sorting-key/partition column"],
        )
    ]


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
    return [
        Finding(
            rule="filter_not_in_sorting_key_prefix",
            severity=_size_severity(ctx.physical.rows),
            verdict_class=verdict,
            chart_id=ctx.chart_id,
            title=title,
            evidence={"sorting_key": sk, "filter_columns": filters, "leading_key": leading},
            suggestions=suggestions,
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


# the ClickHouse rule pack (mechanisms); EXPLAIN evidence first, then metadata rules
RULES: tuple[Callable[[RuleContext], list[Finding]], ...] = (
    explain_high_scan_fraction,
    filter_not_in_sorting_key_prefix,
    partition_misaligned_filter,
    no_filter_on_large_fact,
    group_by_high_cardinality,
    collapsing_engine_needs_final,
)
