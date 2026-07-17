"""Greenplum / Greengage rule pack (ARCHITECTURE §3.3, D9): mechanisms, not cases.

GP performance is dominated by data movement between segments and partition pruning,
so the pack reasons about the distribution key (`physical.distribution_key`), the range
partition column, and measured EXPLAIN evidence (motion nodes, "Partitions selected").
Engine-agnostic rules (no_filter_on_large_fact, group_by_high_cardinality) are shared
with the ClickHouse pack. The LLM never invents these facts — it narrates the findings.
"""

from __future__ import annotations

import re

from auto_bi.advisor.clickhouse import (
    RuleContext,
    group_by_high_cardinality,
    no_filter_on_large_fact,
)
from auto_bi.advisor.findings import Finding, Remediation, Severity, VerdictClass
from auto_bi.introspect.base import RunQuery

# a distribution key with few distinct values can't spread rows evenly across segments
DIST_KEY_LOW_CARDINALITY = 1000
LARGE_FACT_ROWS = 10_000_000

_PARTITIONS_RE = re.compile(r"Partitions selected:\s*(\d+)\s*\(out of\s*(\d+)\)")
_MOTION_RE = re.compile(r"(Broadcast|Redistribute) Motion")


def gp_explain_evidence(run_query: RunQuery, sql: str) -> dict | None:
    """`EXPLAIN sql` -> {motions, partitions_selected, partitions_total}; None on failure.

    Never raises: advisory-only, a failed EXPLAIN degrades to "no measured evidence".
    GP plans carry the motion nodes and the partition-selector count we key rules off."""
    try:
        rows = run_query(f"EXPLAIN {sql}")
    except Exception:  # advisory only: any failure => no measured evidence, never raise
        return None
    plan = "\n".join(str(v) for r in rows for v in r.values())
    evidence: dict = {}
    motions = sorted(set(_MOTION_RE.findall(plan)))
    if motions:
        evidence["motions"] = motions  # e.g. ["Broadcast", "Redistribute"]
    m = _PARTITIONS_RE.search(plan)
    if m:
        evidence["partitions_selected"] = int(m.group(1))
        evidence["partitions_total"] = int(m.group(2))
    return evidence


def non_colocated_join(ctx: RuleContext) -> list[Finding]:
    """A join whose key is not the fact's distribution key forces a motion (the planner
    redistributes the fact or broadcasts the dimension) — costly as the tables grow."""
    dist = ctx.physical.distribution_key
    if not ctx.query.joins or not dist:
        return []
    # on_left is the base-table column ("dm.sales.store_id"); co-located iff its bare
    # name is the distribution key (distribution_key stores bare column names)
    dist_set = set(dist)
    off_key = [j for j in ctx.query.joins if j.on_left.rpartition(".")[2] not in dist_set]
    if not off_key:
        return []  # every join is on the distribution key -> co-located
    motions = ctx.evidence.get("motions")
    return [
        Finding(
            rule="non_colocated_join",
            severity=Severity.WARN,
            verdict_class=VerdictClass.SPEC_ADJUSTMENT,
            chart_id=ctx.chart_id,
            title=(
                f"join on {[j.on_left for j in off_key]} is off the distribution key "
                f"{dist} of {ctx.table.name} -> segment motion"
                + (f" (EXPLAIN: {', '.join(motions)} Motion)" if motions else "")
            ),
            evidence={"distribution_key": dist, "motions": motions or []},
            suggestions=[
                "co-locate by aligning distribution keys, or keep the joined table small "
                "enough that a broadcast is cheap"
            ],
        )
    ]


def partition_not_pruned(ctx: RuleContext) -> list[Finding]:
    """Measured: the plan scanned every partition though the query is filtered -> the
    filter misses the partition column, so partition elimination didn't kick in."""
    selected = ctx.evidence.get("partitions_selected")
    total = ctx.evidence.get("partitions_total")
    if not ctx.query.filters or not selected or not total or total <= 1 or selected < total:
        return []
    return [
        Finding(
            rule="partition_not_pruned",
            severity=Severity.WARN,
            verdict_class=VerdictClass.SPEC_ADJUSTMENT,
            chart_id=ctx.chart_id,
            title=(
                f"all {total} partitions of {ctx.table.name} scanned despite filters "
                f"(no filter on partition column {ctx.physical.partition_key!r})"
            ),
            evidence={
                "partitions_selected": selected,
                "partitions_total": total,
                "partition_key": ctx.physical.partition_key,
            },
            suggestions=[f"add a filter on {ctx.physical.partition_key!r} to prune partitions"],
        )
    ]


def distribution_skew(ctx: RuleContext) -> list[Finding]:
    """A low-cardinality distribution key can't spread a large fact evenly across
    segments -> some segments hold far more data and become the bottleneck."""
    dist = ctx.physical.distribution_key
    if not dist or ctx.physical.rows < LARGE_FACT_ROWS:
        return []
    cards = [ctx.physical.cardinality.get(c) for c in dist]
    if any(c is None for c in cards):
        return []
    total_distinct = 1
    for c in cards:
        assert c is not None  # the `any(... is None)` guard above returned otherwise
        total_distinct *= c
    if total_distinct >= DIST_KEY_LOW_CARDINALITY:
        return []
    # concrete fix: redistribute on the highest-cardinality column we know, else go random
    dist_set = set(dist)
    candidates = sorted(
        ((card, col) for col, card in ctx.physical.cardinality.items() if col not in dist_set),
        reverse=True,
    )
    if candidates and candidates[0][0] >= DIST_KEY_LOW_CARDINALITY:
        best = candidates[0][1]
        remediation = Remediation(
            kind="gp_redistribute",
            summary=f"redistribute {ctx.table.name} by higher-cardinality {best!r}",
            ddl=f"ALTER TABLE {ctx.table.name} SET DISTRIBUTED BY ({best});",
            rationale=(
                f"{best!r} has ~{candidates[0][0]} distinct values (vs ~{total_distinct} for the "
                f"current key {dist}); a higher-cardinality key spreads rows evenly across segments"
            ),
        )
    else:
        remediation = Remediation(
            kind="gp_redistribute",
            summary=f"redistribute {ctx.table.name} randomly (no higher-cardinality key known)",
            ddl=f"ALTER TABLE {ctx.table.name} SET DISTRIBUTED RANDOMLY;",
            rationale=(
                f"no column with cardinality ≥ {DIST_KEY_LOW_CARDINALITY} is recorded for an even "
                "key; random distribution avoids the skew at the cost of redistribute on joins"
            ),
        )
    return [
        Finding(
            rule="distribution_skew",
            severity=Severity.WARN,
            verdict_class=VerdictClass.DM_CHANGE_REQUEST,
            chart_id=ctx.chart_id,
            title=(
                f"distribution key {dist} of {ctx.table.name} has only ~{total_distinct} "
                f"combinations -> uneven spread across segments on {ctx.physical.rows} rows"
            ),
            evidence={"distribution_key": dist, "key_cardinality": total_distinct},
            suggestions=[
                "this is a DM design choice: a higher-cardinality distribution key spreads "
                "the fact evenly across segments"
            ],
            remediation=remediation,
        )
    ]


# GP pack: EXPLAIN-evidence + distribution/partition mechanisms, then the shared rules
RULES = (
    non_colocated_join,
    partition_not_pruned,
    distribution_skew,
    no_filter_on_large_fact,
    group_by_high_cardinality,
)
