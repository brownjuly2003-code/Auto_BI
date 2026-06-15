"""Phase 0 happy path: description -> spec -> validated SQL -> Superset dashboard.

No dialogue yet (INTAKE/CLARIFY arrive in Phase 1) — single pass, fail loudly.
All collaborators are injected; the CLI wires real ones from settings.
"""

from collections.abc import Callable

from auto_bi.adapters.base import BIAdapter, DashboardRef
from auto_bi.agent.normalize import apply_chart_defaults, apply_label_joins
from auto_bi.agent.propose import SpecValidationError, propose_spec
from auto_bi.agent.sql_guard import LiveSQLValidator
from auto_bi.agent.sqlgen import generate_chart_sql
from auto_bi.ir.spec import TargetBI
from auto_bi.ir.validate import validate_spec
from auto_bi.llm.base import LLMClient
from auto_bi.semantic.model import SemanticModel
from auto_bi.store import Store

# Resolve the spec's BI target to a wired adapter (auto_bi.adapters.factory.make_adapter,
# partial-applied with settings+model). Injected as a resolver so the pipeline never names a
# concrete adapter (Phase 4 F1) and tests can supply a fake.
AdapterFor = Callable[[TargetBI], BIAdapter]


def build_dashboard(
    description: str,
    model: SemanticModel,
    llm: LLMClient,
    sql_validator: LiveSQLValidator,
    adapter_for: AdapterFor,
    log: Callable[[str], None] = print,
    *,
    include_samples: bool = True,
    store: Store | None = None,
    session_id: str | None = None,
    target_bi: TargetBI | None = None,
) -> DashboardRef:
    log(f"PROPOSE_SPEC: «{description}»")
    spec = propose_spec(
        llm, model, description, session_id=session_id, include_samples=include_samples
    )
    if target_bi is not None:
        # explicit user choice (e.g. CLI --target) wins over the spec default; the prompt
        # does not ask the LLM for a BI target, so spec.target_bi is otherwise SUPERSET
        spec.target_bi = target_bi
    log(f"spec ok: «{spec.title}», {len(spec.charts)} чартов → {spec.target_bi.value}")
    for chart in spec.charts:
        log(f"  - [{chart.viz.value}] {chart.title}")

    spec_id: int | None = None
    if store is not None and session_id is not None:
        spec_id = store.save_spec(session_id, spec.model_dump(mode="json"))

    return compile_and_build(
        spec,
        model,
        sql_validator,
        adapter_for,
        log,
        store=store,
        session_id=session_id,
        spec_id=spec_id,
    )


def compile_and_build(
    spec,
    model: SemanticModel,
    sql_validator: LiveSQLValidator,
    adapter_for: AdapterFor,
    log: Callable[[str], None] = print,
    *,
    store: Store | None = None,
    session_id: str | None = None,
    spec_id: int | None = None,
) -> DashboardRef:
    """SQL_GEN -> VALIDATE -> BUILD for an already-produced spec (chat APPROVE path)."""
    # deterministic dashboard-adequacy normalization, before SQL_GEN + adapter so BOTH the
    # validated SQL and the built dashboard see one normalized spec. Both passes are pure
    # and idempotent. The preview/advisor see the pre-normalization spec, so log changes.
    # B3 (label joins) runs first — it swaps raw FK id dimensions for their human-readable
    # name via a safe LEFT JOIN; B1 (top-N) then ranks the now-named categorical axis.
    labeled = apply_label_joins(spec, model)
    relabeled = [
        c.id for o, c in zip(spec.charts, labeled.charts, strict=True) if c.query != o.query
    ]
    if relabeled:
        log(f"нормализация: id-измерения заменены на названия через join в чартах {relabeled}")
    normalized = apply_chart_defaults(labeled, model)
    topn_changed = [
        c.id for o, c in zip(labeled.charts, normalized.charts, strict=True) if c.query != o.query
    ]
    if topn_changed:
        log(f"нормализация: дефолтный top-N применён к категориальным чартам {topn_changed}")
    spec = normalized
    # invariant 2 at the BI boundary: never let an unvalidated spec reach the adapter,
    # regardless of how `spec` was produced (defense-in-depth; no-op on the happy path).
    errors = validate_spec(spec, model)
    if errors:
        raise SpecValidationError(errors)

    for chart in spec.charts:
        sql = generate_chart_sql(chart.query)
        sql_validator.validate(sql)
        log(f"SQL ok ({chart.id}): EXPLAIN + LIMIT-прогон прошли")

    # dispatch on the spec's declared target so a "datalens" spec never silently builds in
    # Superset (invariant 2 at the BI boundary; Phase 4 F1)
    adapter = adapter_for(spec.target_bi)
    health = adapter.healthcheck()
    if not health.ok:
        raise RuntimeError(f"{spec.target_bi.value} healthcheck failed: {health.message}")

    try:
        ref = adapter.build(spec)
    except Exception as exc:
        if store is not None and session_id is not None:
            store.save_build(session_id, spec_id, status="failed", error=str(exc))
            store.set_session_status(session_id, "failed")
        raise
    log(f"BUILD done: {ref.title} -> {ref.url}")
    if store is not None and session_id is not None:
        store.save_build(session_id, spec_id, dashboard_id=ref.id, url=ref.url, status="ok")
        store.set_session_status(session_id, "built")
    return ref
