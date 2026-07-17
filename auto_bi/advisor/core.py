"""Feasibility Advisor orchestrator (ARCHITECTURE §3.3).

Per chart: gather measured EXPLAIN evidence (if a read-only RunQuery is available),
then run the engine rule pack. Advisory-only — returns findings, never blocks a build
(CLAUDE.md invariant 5). The LLM narrates these in PROPOSE_SPEC (task 1.7).
"""

from __future__ import annotations

from auto_bi.advisor.clickhouse import RULES as CH_RULES
from auto_bi.advisor.clickhouse import RuleContext
from auto_bi.advisor.effective import effective_filters
from auto_bi.advisor.explain import estimate_scan, live_row_count
from auto_bi.advisor.findings import Finding
from auto_bi.advisor.greenplum import RULES as GP_RULES
from auto_bi.advisor.greenplum import gp_explain_evidence
from auto_bi.agent.sqlgen import generate_chart_sql
from auto_bi.engine import CLICKHOUSE, GREENPLUM, sqlglot_dialect
from auto_bi.introspect.base import RunQuery
from auto_bi.ir.spec import ChartSpec, DashboardSpec
from auto_bi.semantic.model import SemanticModel


class Advisor:
    def __init__(self, model: SemanticModel, run_query: RunQuery | None = None) -> None:
        self._model = model
        self._run_query = run_query  # read-only seam; None => metadata-only (no EXPLAIN)
        # one engine per model: pick the rule pack + EXPLAIN-evidence shape by it
        self._engine = next((t.physical.engine for t in model.tables if t.physical), CLICKHOUSE)
        self._dialect = sqlglot_dialect(self._engine)
        self._rules = GP_RULES if self._engine == GREENPLUM else CH_RULES
        # live row counts per table, one catalog query each per Advisor instance: the
        # scan-fraction denominator must be as live as the EXPLAIN numerator (P1-6)
        self._live_rows: dict[str, int | None] = {}

    def _live_total_rows(self, table_name: str) -> int | None:
        """Live table size through the same read-only seam; None => model fallback.
        CH-only: the GP pack has no scan-fraction rule to feed."""
        if self._run_query is None or self._engine == GREENPLUM:
            return None
        if table_name not in self._live_rows:
            self._live_rows[table_name] = live_row_count(self._run_query, table_name)
        return self._live_rows[table_name]

    def _gather_evidence(self, sql: str) -> dict:
        if self._run_query is None:
            return {}
        if self._engine == GREENPLUM:
            return gp_explain_evidence(self._run_query, sql) or {}
        return estimate_scan(self._run_query, sql) or {}

    def review_chart(self, chart: ChartSpec, spec: DashboardSpec | None = None) -> list[Finding]:
        """Findings for one chart. Pass `spec` so the dashboard's controls are taken into
        account (P1-2); without it the chart is judged on its own filters alone, which
        overstates the scan for a chart the dashboard opens filtered."""
        if chart.query.raw_sql is not None:
            return []  # X-5 raw hatch: advisor reasons over IR, it is blind to raw SQL (by design)
        table = self._model.table(chart.query.table)
        if table is None or table.physical is None:
            return []  # nothing to reason about without physical metadata

        # Judge (and measure) the query the BI actually runs on refresh: a control's default
        # is part of that query, so EXPLAIN-ing the verbatim spec query would overstate the scan.
        query = chart.query.model_copy(
            update={"filters": effective_filters(chart, spec, self._model)}
        )
        evidence = self._gather_evidence(generate_chart_sql(query, dialect=self._dialect))
        live_rows = self._live_total_rows(chart.query.table)
        if live_rows is not None:
            evidence = {**evidence, "live_total_rows": live_rows}

        ctx = RuleContext(
            chart_id=chart.id,
            query=query,
            table=table,
            physical=table.physical,
            evidence=evidence,
            model=self._model,
        )
        findings: list[Finding] = []
        for rule in self._rules:
            findings.extend(rule(ctx))
        return findings

    def review(self, spec: DashboardSpec) -> list[Finding]:
        findings: list[Finding] = []
        for chart in spec.charts:
            findings.extend(self.review_chart(chart, spec))
        return findings
