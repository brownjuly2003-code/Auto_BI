"""MetricFlow's PostgreSQL SQL for metrics of a dbt semantic manifest — the other side of the
parity matrix (`metricflow_parity.py`).

Runs in the dbt/MetricFlow environment, not in this project's: it imports MetricFlow and
nothing of ``auto_bi``. Reads ``{"artifact": path, "metrics": [...], "group_by": [...]}`` on
stdin and writes ``{metric: sql}`` on stdout. MetricFlow only plans the query
(`MetricFlowEngine.explain`); the client it is given renders PostgreSQL and refuses to run
anything.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from metricflow.engine.metricflow_engine import MetricFlowEngine, MetricFlowQueryRequest
from metricflow.protocols.sql_client import SqlEngine
from metricflow.sql.render.postgres import PostgresSQLSqlPlanRenderer
from metricflow_semantics.model.dbt_manifest_parser import (
    parse_manifest_from_dbt_generated_manifest,
)
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup


class _PlanOnly:
    """A PostgreSQL client that renders and never connects."""

    sql_engine_type = SqlEngine.POSTGRES
    sql_plan_renderer = PostgresSQLSqlPlanRenderer()

    def render_bind_parameter_key(self, bind_parameter_key: str) -> str:
        return f"%({bind_parameter_key})s"

    def _refuse(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("the parity matrix only plans MetricFlow's queries")

    query = execute = dry_run = _refuse

    def close(self) -> None:
        return None


def main() -> int:
    request = json.load(sys.stdin)
    raw = Path(request["artifact"]).read_text(encoding="utf-8")
    # MetricFlow's own reading of a dbt artifact, with the transformations it applies to one
    manifest = parse_manifest_from_dbt_generated_manifest(raw)
    engine = MetricFlowEngine(SemanticManifestLookup(manifest), _PlanOnly())
    answer: dict[str, str] = {}
    for name in request["metrics"]:
        statement = engine.explain(
            MetricFlowQueryRequest.create(metric_names=[name], group_by_names=request["group_by"])
        ).sql_statement
        if statement.bind_parameter_set.param_dict:
            raise RuntimeError(f"{name}: MetricFlow's query takes bind parameters")
        answer[name] = statement.sql
    json.dump(answer, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
