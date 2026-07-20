"""Phase 0 happy path: description -> spec -> validated SQL -> Superset dashboard.

No dialogue yet (INTAKE/CLARIFY arrive in Phase 1) — single pass, fail loudly.
All collaborators are injected; the CLI wires real ones from settings.
"""

import logging
from collections.abc import Callable

from auto_bi.adapters.artifacts import new_build_namespace
from auto_bi.adapters.base import BIAdapter, DashboardRef
from auto_bi.adapters.factory import close_adapter
from auto_bi.advisor.core import Advisor
from auto_bi.advisor.narrate import ChartVerdict, worst_verdicts
from auto_bi.agent.dataset_plan import DatasetRole, plan_datasets, source_dataset_inputs
from auto_bi.agent.normalize import apply_chart_defaults, apply_label_joins
from auto_bi.agent.propose import SpecValidationError, propose_spec
from auto_bi.agent.query_plan import PlanCache
from auto_bi.agent.sql_guard import LiveSQLValidator
from auto_bi.agent.sqlgen import generate_chart_sql, generate_source_sql
from auto_bi.ir.spec import DashboardSpec, TargetBI
from auto_bi.ir.validate import validate_spec
from auto_bi.llm.base import LLMClient
from auto_bi.semantic.model import SemanticModel
from auto_bi.store import SHARED_BI_KINDS, Store

logger = logging.getLogger(__name__)

# Resolve the spec's BI target to a wired adapter (auto_bi.adapters.factory.make_adapter,
# partial-applied with settings+model). Injected as a resolver so the pipeline never names a
# concrete adapter (Phase 4 F1) and tests can supply a fake.
AdapterFor = Callable[[TargetBI], BIAdapter]


def review_and_log(
    advisor: Advisor | None,
    spec: DashboardSpec,
    log: Callable[[str], None] = print,
    *,
    plans: PlanCache | None = None,
) -> list[ChartVerdict]:
    """Advisor pass for the one-shot CLI paths (P1-2), which otherwise never ran it.

    Mechanical on purpose: the verdict is decided by the rules either way (invariant 5) and
    only the wording would be the LLM's, so narrating here would cost an extra provider call
    per build to reword text an engineer-facing CLI reads fine as-is. The chat path (machine)
    still narrates, where a user is conversing. Advisory-only — the build proceeds regardless.
    """
    if advisor is None:
        return []
    verdicts = list(worst_verdicts(advisor.review(spec, plans=plans)).values())
    if not verdicts:
        return []
    titles = {c.id: c.title for c in spec.charts}
    log("Advisor:")
    for v in verdicts:
        log(f"  [{v.severity.value}] {titles.get(v.chart_id, v.chart_id)}: {v.text}")
        for suggestion in v.suggestions:
            log(f"      → {suggestion}")
    return verdicts


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
    advisor: Advisor | None = None,
    prune_orphans: bool = True,
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
    # D-2 §3: review and build happen back to back here, so the advisor's plan of a chart
    # statement is still current when the guard reaches the same statement — one cache for
    # the whole call, discarded with it.
    plans = PlanCache()
    review_and_log(advisor, spec, log, plans=plans)

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
        prune_orphans=prune_orphans,
        plans=plans,
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
    prune_orphans: bool = True,
    plans: PlanCache | None = None,
) -> DashboardRef:
    """SQL_GEN -> VALIDATE -> BUILD for an already-produced spec (chat APPROVE path).

    The whole sequence below runs under one try/except (B-7): a session is marked
    'building' before it starts, and ANY exception — spec validation, SQL guard,
    adapter healthcheck, or the build call itself — records a 'failed' build row and
    flips the session to 'failed'. Previously only `adapter.build()` failures were
    recorded, so a SpecValidationError or a healthcheck failure vanished without a
    trace. A process killed mid-build (SIGKILL/OOM) still leaves the session stuck at
    'building' — `Store.reap_stuck_builds()` cleans those up on the next server start.

    `plans` (D-2 §3) carries the advisor's EXPLAIN evidence from a review that ran in the
    same call, letting the guard skip a re-plan of a statement it would plan identically.
    Omitted (the API approve path, where preview and build are separate requests) every
    chart is planned here as before.
    """
    if store is not None and session_id is not None:
        store.set_session_status(session_id, "building")
    # D-2 lifecycle: the adapter (and its HTTP pool) is created per build, so it must be
    # released on EVERY exit — after the ledger/prune on success, and on any failure. The
    # outer finally is the single release point.
    adapter: BIAdapter | None = None
    try:
        try:
            # deterministic dashboard-adequacy normalization, before SQL_GEN + adapter so BOTH
            # the validated SQL and the built dashboard see one normalized spec. Both passes are
            # pure and idempotent. The preview/advisor see the pre-normalization spec, so log
            # changes. B3 (label joins) runs first — it swaps raw FK id dimensions for their
            # human-readable name via a safe LEFT JOIN; B1 (top-N) then ranks the now-named
            # categorical axis.
            labeled = apply_label_joins(spec, model)
            relabeled = [
                c.id for o, c in zip(spec.charts, labeled.charts, strict=True) if c.query != o.query
            ]
            if relabeled:
                log(
                    f"нормализация: id-измерения заменены на названия через join в чартах "
                    f"{relabeled}"
                )
            normalized = apply_chart_defaults(labeled, model)
            topn_changed = [
                c.id
                for o, c in zip(labeled.charts, normalized.charts, strict=True)
                if c.query != o.query
            ]
            if topn_changed:
                log(
                    f"нормализация: дефолтный top-N применён к категориальным чартам "
                    f"{topn_changed}"
                )
            spec = normalized
            # invariant 2 at the BI boundary: never let an unvalidated spec reach the adapter,
            # regardless of how `spec` was produced (defense-in-depth; no-op on the happy path).
            errors = validate_spec(spec, model)
            if errors:
                raise SpecValidationError(errors)

            # D-1: gate the SQL the BI actually runs.
            # SOURCE charts share one semantic-grain dataset per mart — validate that
            # once. OWN charts keep today's per-chart aggregated SQL. The advisor still
            # judges pre-D-1 chart SQL for SOURCE charts (accepted risk; PlanCache miss
            # against the source statement is legitimate).
            ds_plan = plan_datasets(spec)
            for table in ds_plan.source_tables:
                inputs = source_dataset_inputs(spec, ds_plan, model, table)
                sql = generate_source_sql(
                    inputs.table, list(inputs.columns), list(inputs.joins), inputs.joined_refs
                )
                sql_validator.validate(sql, plans=plans)
                log(f"SQL ok (source:{table}): EXPLAIN + LIMIT-прогон прошли")
            for chart in spec.charts:
                if ds_plan.chart(chart.id).role is DatasetRole.SOURCE:
                    continue  # BI dataset already gated above
                sql = generate_chart_sql(chart.query)
                sql_validator.validate(sql, plans=plans)
                log(f"SQL ok ({chart.id}/own): EXPLAIN + LIMIT-прогон прошли")

            # dispatch on the spec's declared target so a "datalens" spec never silently builds
            # in Superset (invariant 2 at the BI boundary; Phase 4 F1)
            adapter = adapter_for(spec.target_bi)
            health = adapter.healthcheck()
            if not health.ok:
                raise RuntimeError(f"{spec.target_bi.value} healthcheck failed: {health.message}")

            # P0-2: pin technical BI artifact names to this build/session so two independent
            # sessions with the same title/chart ids never share or overwrite datasets. The same
            # namespace is the build's `build_token` = its revision id in the ownership ledger
            # (P0-2 criterion 4). Optional helper on concrete adapters (Protocol unchanged — S4).
            build_token = new_build_namespace(session_id)
            set_ns = getattr(adapter, "set_artifact_namespace", None)
            if callable(set_ns):
                set_ns(build_token)

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
            # ownership ledger (P0-2 criterion 4): build_token/adapter are in scope here —
            # reaching this point means adapter.build(spec) returned without raising
            _record_bi_artifacts(store, session_id, spec, adapter, build_token)
            if prune_orphans:
                _prune_superseded_artifacts(store, session_id, build_token, adapter, log)
        return ref
    finally:
        if adapter is not None:
            try:
                close_adapter(adapter)
            except Exception:
                # a failing pool release must never mask the build outcome: the dashboard is
                # already delivered, or the original error is already propagating
                logger.debug("BI adapter close failed", exc_info=True)


def _record_bi_artifacts(
    store: Store,
    session_id: str,
    spec: DashboardSpec,
    adapter: BIAdapter,
    build_token: str,
) -> None:
    """Ownership ledger (audit P0-2 criterion 4): after a successful build, drain the BI
    artifacts the adapter created and record them in `Store.bi_artifacts` keyed on
    session/owner/build_token, so a future ownership-based orphan cleanup can select prior
    revisions' OWNED artifacts by id — NEVER by name (two dashboards may share a title).

    `drain_build_artifacts` is a concrete adapter helper, not a BIAdapter Protocol method
    (like `set_artifact_namespace`); a bare-protocol adapter lacks it, in which case nothing is
    recorded. `owner` is the session's persisted owner (NULL when auth is off); `schema_set`
    per dataset/chart comes from the chart query's table (RBAC scoping).

    Live-cleanup IS wired (2026-07-18): right after this record, `compile_and_build` calls
    `_prune_superseded_artifacts`, which deletes THIS session's prior-revision orphans that
    `Store.orphan_bi_artifacts` selects — by native id via a concrete adapter `delete_artifact`,
    then `Store.mark_bi_artifacts_superseded` — and never fails the build. The operator path for
    superseded revisions is `auto_bi prune` (selection `Store.stale_bi_artifacts`).
    """
    drain = getattr(adapter, "drain_build_artifacts", None)
    if not callable(drain):
        return
    session = store.session_row(session_id)
    owner = session.get("owner") if session else None
    target_bi = spec.target_bi.value
    for art in drain():
        store.record_bi_artifact(
            session_id=session_id,
            build_token=build_token,
            target_bi=target_bi,
            kind=art.kind,
            native_id=art.native_id,
            name=art.name,
            owner=owner,
            schema_set=art.schema_set,
        )


# Ownership live-cleanup delete order, proven live on the stand (2026-07-18): charts first,
# then the dashboard, then datasets — a dataset is never deleted while a chart still reads it.
_PRUNE_ORDER = {"chart": 0, "dashboard": 1, "dataset": 2}


def prune_artifact_rows(
    store: Store,
    rows: list[dict],
    delete: Callable[[str, str], None],
    log: Callable[[str], None] = print,
) -> tuple[int, int]:
    """Feed ledger rows into a BI delete-by-id callable, superseding the removed ones.

    The shared deletion engine of both prune paths (auto-prune on rebuild and the operator
    `auto_bi prune`); `delete` is a concrete adapter's `delete_artifact`. Shared kinds are
    skipped defensively even though both selections already exclude them in SQL. A per-row
    failure keeps that row 'live' — it is re-selected and retried by a later prune — and
    never propagates. Returns (removed, failed).
    """
    removed: list[int] = []
    failed = 0
    for row in sorted(rows, key=lambda r: _PRUNE_ORDER.get(r["kind"], 99)):
        if row["kind"] in SHARED_BI_KINDS:
            continue
        try:
            delete(row["kind"], str(row["native_id"]))
        except Exception as exc:
            failed += 1
            log(f"prune: {row['kind']} {row['native_id']} не удалён ({exc}) — остаётся в леджере")
            continue
        removed.append(row["id"])
    if removed:
        store.mark_bi_artifacts_superseded(removed)
    return len(removed), failed


def _prune_superseded_artifacts(
    store: Store,
    session_id: str,
    current_build_token: str,
    adapter: BIAdapter,
    log: Callable[[str], None],
) -> None:
    """Auto-prune on rebuild: delete THIS session's prior-revision BI artifacts by id.

    Runs after a successful build + ledger record, so the freshly delivered dashboard is
    never touched (its rows carry `current_build_token`). Selection is `orphan_bi_artifacts`
    — ownership-keyed (session/owner/build_token, never name/title), shared kinds excluded
    in SQL. `delete_artifact` is a concrete adapter helper; a bare-protocol adapter lacks it
    and the prune is a no-op. NEVER fails the build: the dashboard is already delivered, so
    any error here is logged and the leftover rows stay 'live' for a later prune.
    Kill-switch: AUTO_BI_PRUNE_ON_REBUILD=false (wired via the `prune_orphans` parameter).

    INVARIANT (builds of ONE session are serial): `orphan_bi_artifacts` selects every ledger
    row of the session whose token differs from `current_build_token` — a CONCURRENT build of
    the same session that already recorded its ledger rows would be deleted here as a "prior
    revision". Today this is unreachable (the API rejects a second build of a running session
    with 409, the CLI creates a fresh session per run), but a future parallel executor MUST
    keep per-session builds serial or rework this selection (see ARCHITECTURE §3.17).
    """
    delete = getattr(adapter, "delete_artifact", None)
    if not callable(delete):
        return
    try:
        session = store.session_row(session_id)
        owner = session.get("owner") if session else None
        orphans = store.orphan_bi_artifacts(session_id, current_build_token, owner=owner)
        if not orphans:
            return
        removed, failed = prune_artifact_rows(store, orphans, delete, log)
        line = f"prune: удалены артефакты прошлых сборок сессии: {removed}"
        if failed:
            line += f" (не удалось: {failed}, будут повторены следующим прунингом)"
        log(line)
    except Exception as exc:  # the build itself already succeeded — never re-raise
        log(f"prune: пропущен ({exc})")
