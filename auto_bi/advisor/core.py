"""Feasibility Advisor orchestrator (ARCHITECTURE §3.6).

Per chart: gather measured EXPLAIN evidence (if a read-only RunQuery is available),
then run the engine rule pack. Advisory-only — returns findings, never blocks a build
(CLAUDE.md invariant 5). The LLM narrates these in PROPOSE_SPEC (task 1.7).
"""

from __future__ import annotations

from auto_bi.advisor.clickhouse import RULES, RuleContext
from auto_bi.advisor.explain import estimate_scan
from auto_bi.advisor.findings import Finding
from auto_bi.agent.sqlgen import generate_chart_sql
from auto_bi.introspect.clickhouse import RunQuery
from auto_bi.ir.spec import ChartSpec, DashboardSpec
from auto_bi.semantic.model import SemanticModel


class Advisor:
    def __init__(self, model: SemanticModel, run_query: RunQuery | None = None) -> None:
        self._model = model
        self._run_query = run_query  # read-only seam; None => metadata-only (no EXPLAIN)

    def review_chart(self, chart: ChartSpec) -> list[Finding]:
        table = self._model.table(chart.query.table)
        if table is None or table.physical is None:
            return []  # nothing to reason about without physical metadata

        evidence: dict = {}
        if self._run_query is not None:
            est = estimate_scan(self._run_query, generate_chart_sql(chart.query))
            if est:
                evidence = est

        ctx = RuleContext(
            chart_id=chart.id,
            query=chart.query,
            table=table,
            physical=table.physical,
            evidence=evidence,
        )
        findings: list[Finding] = []
        for rule in RULES:
            findings.extend(rule(ctx))
        return findings

    def review(self, spec: DashboardSpec) -> list[Finding]:
        findings: list[Finding] = []
        for chart in spec.charts:
            findings.extend(self.review_chart(chart))
        return findings
