"""Phase 0 happy path: description -> spec -> validated SQL -> Superset dashboard.

No dialogue yet (INTAKE/CLARIFY arrive in Phase 1) — single pass, fail loudly.
All collaborators are injected; the CLI wires real ones from settings.
"""

from collections.abc import Callable

from auto_bi.adapters.base import DashboardRef
from auto_bi.adapters.superset.adapter import SupersetAdapter
from auto_bi.agent.propose import SpecValidationError, propose_spec
from auto_bi.agent.sql_guard import LiveSQLValidator
from auto_bi.agent.sqlgen import generate_chart_sql
from auto_bi.ir.validate import validate_spec
from auto_bi.llm.base import LLMClient
from auto_bi.semantic.model import SemanticModel


def build_dashboard(
    description: str,
    model: SemanticModel,
    llm: LLMClient,
    sql_validator: LiveSQLValidator,
    adapter: SupersetAdapter,
    log: Callable[[str], None] = print,
    *,
    include_samples: bool = True,
) -> DashboardRef:
    log(f"PROPOSE_SPEC: «{description}»")
    spec = propose_spec(llm, model, description, include_samples=include_samples)
    log(f"spec ok: «{spec.title}», {len(spec.charts)} чартов")
    for chart in spec.charts:
        log(f"  - [{chart.viz.value}] {chart.title}")

    # invariant 2 at the BI boundary: never let an unvalidated spec reach the adapter,
    # regardless of how `spec` was produced (defense-in-depth; no-op on the happy path).
    errors = validate_spec(spec, model)
    if errors:
        raise SpecValidationError(errors)

    for chart in spec.charts:
        sql = generate_chart_sql(chart.query)
        sql_validator.validate(sql)
        log(f"SQL ok ({chart.id}): EXPLAIN + LIMIT-прогон прошли")

    health = adapter.healthcheck()
    if not health.ok:
        raise RuntimeError(f"Superset healthcheck failed: {health.message}")

    ref = adapter.build(spec)
    log(f"BUILD done: {ref.title} -> {ref.url}")
    return ref
