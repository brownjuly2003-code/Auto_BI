"""Phase 0 happy path: description -> spec -> validated SQL -> Superset dashboard.

No dialogue yet (INTAKE/CLARIFY arrive in Phase 1) — single pass, fail loudly.
All collaborators are injected; the CLI wires real ones from settings.
"""

import logging
from collections.abc import Callable

from auto_bi.adapters.artifacts import new_build_namespace, stable_build_token
from auto_bi.adapters.base import BIAdapter, BuildContext, BuildResult, DashboardRef
from auto_bi.adapters.factory import close_adapter
from auto_bi.advisor.core import Advisor
from auto_bi.advisor.narrate import ChartVerdict, worst_verdicts
from auto_bi.agent.dataset_plan import DatasetRole, plan_datasets, source_dataset_inputs
from auto_bi.agent.normalize import apply_chart_defaults, apply_label_joins
from auto_bi.agent.propose import SpecValidationError, propose_spec
from auto_bi.agent.query_plan import PlanCache
from auto_bi.agent.sql_guard import LiveSQLValidator
from auto_bi.agent.sqlgen import generate_chart_sql, generate_source_sql
from auto_bi.errors import CODE_BI_HEALTH, SafeError, store_error_text, to_safe_error
from auto_bi.ir.spec import DashboardSpec, TargetBI
from auto_bi.ir.validate import validate_spec
from auto_bi.llm.base import LLMClient
from auto_bi.semantic.model import SemanticModel
from auto_bi.store import SHARED_BI_KINDS, Store
from auto_bi.store.db import (
    BUILD_DELIVERED_PENDING,
    BUILD_OK,
    SESSION_BUILT,
    SESSION_BUILT_DEGRADED,
)

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
    include_samples: bool = False,
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
    'building' before it starts, and ANY exception BEFORE adapter.build returns —
    spec validation, SQL guard, adapter healthcheck, or the build call itself —
    records a 'failed' build row and flips the session to 'failed' (atomic
    `commit_build_failure`). After adapter.build returns a DashboardRef, the
    dashboard is treated as delivered: build row + session + ownership ledger are
    written in ONE SQLite transaction (`commit_build_success`, plan_sol step 8 /
    audit P1-2). A ledger commit failure MUST NOT mark the build failed (UI split-
    brain: BI has the dashboard, Store would say failed) — we fall back to
    `delivered_pending` + `built_with_cleanup_degraded` and still return the ref.
    A process killed mid-build (SIGKILL/OOM) still leaves the session stuck at
    'building' — `Store.reap_stuck_builds()` cleans those up on the next server start.

    `plans` (D-2 §3) carries the advisor's EXPLAIN evidence from a review that ran in the
    same call, letting the guard skip a re-plan of a statement it would plan identically.
    When omitted (the API approve path, where preview and build are separate requests) a
    fresh build-local PlanCache is created so D-2 §5 trial capture still works inside this
    build; it starts empty (no advisor evidence — EXPLAIN-skip semantics unchanged) and
    dies with the call. Nothing is persisted.
    """
    if plans is None:
        plans = PlanCache()

    # plan_sol step 8 residual: durable (session, spec_row) → stable token so a retry of
    # the same approve (or re-approve after delivered_pending) does not create a second
    # BI dashboard. Without a known spec revision, fall back to a random namespace so
    # intentional multi-build tests / CLI one-shots still get distinct revisions.
    resolved_spec_id = spec_id
    if resolved_spec_id is None and store is not None and session_id is not None:
        specs = store.specs(session_id)
        if specs:
            resolved_spec_id = int(specs[-1]["id"])
    build_token = (
        stable_build_token(session_id, resolved_spec_id)
        if session_id is not None and resolved_spec_id is not None
        else ""
    )
    if store is not None and session_id is not None and build_token:
        existing = store.build_by_token(build_token)
        if existing is not None and existing.get("status") in (
            BUILD_OK,
            BUILD_DELIVERED_PENDING,
        ):
            log(
                f"BUILD idempotent: reusing delivered dashboard for token "
                f"{build_token} (no second BI create)"
            )
            if existing.get("status") == BUILD_DELIVERED_PENDING:
                store.set_session_status(session_id, SESSION_BUILT_DEGRADED)
            else:
                store.set_session_status(session_id, SESSION_BUILT)
            dash_id = existing.get("dashboard_id")
            return DashboardRef(
                id=dash_id if dash_id is not None else 0,
                title=spec.title,
                url=existing.get("url") or "",
            )

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

            # D-1 gating split applies only when the target consumes the dataset plan
            # (Superset: one source dataset per mart + skip SOURCE per-chart SQL).
            # DataLens still builds per-chart datasets from generate_chart_sql — keep
            # pre-D-1 per-chart EXPLAIN+LIMIT gating so invariant 3 is not silently
            # bypassed on that lane (PR-2 finding 4).
            if spec.target_bi is TargetBI.SUPERSET:
                ds_plan = plan_datasets(spec)
                for table in ds_plan.source_tables:
                    inputs = source_dataset_inputs(spec, ds_plan, model, table)
                    sql = generate_source_sql(
                        inputs.table,
                        list(inputs.columns),
                        list(inputs.joins),
                        inputs.joined_refs,
                    )
                    sql_validator.validate(sql, plans=plans)
                    log(f"SQL ok (source:{table}): EXPLAIN + LIMIT-прогон прошли")
                for chart in spec.charts:
                    if ds_plan.chart(chart.id).role is DatasetRole.SOURCE:
                        continue  # BI dataset already gated above
                    sql = generate_chart_sql(chart.query)
                    sql_validator.validate(sql, plans=plans)
                    log(f"SQL ok ({chart.id}/own): EXPLAIN + LIMIT-прогон прошли")
            else:
                for chart in spec.charts:
                    sql = generate_chart_sql(chart.query)
                    sql_validator.validate(sql, plans=plans)
                    log(f"SQL ok ({chart.id}): EXPLAIN + LIMIT-прогон прошли")

            # dispatch on the spec's declared target so a "datalens" spec never silently builds
            # in Superset (invariant 2 at the BI boundary; Phase 4 F1)
            adapter = adapter_for(spec.target_bi)
            health = adapter.healthcheck()
            if not health.ok:
                # SafeError: health.message may echo provider/network detail — keep it
                # internal; public channels get a stable code (plan_sol step 3).
                raise SafeError(
                    CODE_BI_HEALTH,
                    internal_detail=f"{spec.target_bi.value} healthcheck failed: {health.message}",
                    retryable=True,
                    provider_class=type(adapter).__name__,
                )

            # P0-2 + plan_sol step 7/8: namespace isolation via BuildContext.
            # Stable token when (session, spec_row) known; else random per attempt.
            if not build_token:
                build_token = new_build_namespace(session_id)
            owner: str | None = None
            if store is not None and session_id is not None:
                session_row = store.session_row(session_id)
                owner = session_row.get("owner") if session_row else None
            ctx = BuildContext(
                namespace=build_token,
                plans=plans,
                session_id=session_id,
                owner=owner,
            )
            result: BuildResult = adapter.build(spec, ctx)
            ref = result.dashboard
        except Exception as exc:
            if store is not None and session_id is not None:
                # Durable row must never hold raw provider bodies / DSN / tokens.
                # Atomic with session status (plan_sol step 8) so a crash mid-write
                # cannot leave session=building with a half-failed build row.
                if not build_token:
                    build_token = new_build_namespace(session_id)
                store.commit_build_failure(
                    session_id,
                    resolved_spec_id if resolved_spec_id is not None else spec_id,
                    error=store_error_text(exc),
                    build_token=build_token,
                )
            # Re-raise as SafeError so API/SSE see the public face; preserve original chain.
            if isinstance(exc, SafeError):
                raise
            raise to_safe_error(exc) from exc
        log(f"BUILD done: {ref.title} -> {ref.url}")
        if store is not None and session_id is not None:
            # plan_sol step 8: after BI delivery, never report failed. Atomic commit of
            # build + session + ledger; on failure record delivered_pending and return ref.
            _commit_delivery(
                store,
                session_id,
                resolved_spec_id if resolved_spec_id is not None else spec_id,
                spec,
                result,
                build_token,
            )
            if prune_orphans and adapter is not None:
                pruned_ok = _prune_superseded_artifacts(
                    store, session_id, build_token, adapter, log
                )
                if not pruned_ok:
                    # Dashboard + ledger are fine; only prior-revision cleanup degraded.
                    store.set_session_status(session_id, SESSION_BUILT_DEGRADED)
        return ref
    finally:
        if adapter is not None:
            try:
                close_adapter(adapter)
            except Exception:
                # a failing pool release must never mask the build outcome: the dashboard is
                # already delivered, or the original error is already propagating
                logger.debug("BI adapter close failed", exc_info=True)


def _commit_delivery(
    store: Store,
    session_id: str,
    spec_id: int | None,
    spec: DashboardSpec,
    result: BuildResult,
    build_token: str,
) -> None:
    """Persist delivery after adapter.build: atomic success, or delivered_pending fallback.

    Never raises to the caller — BI already has the dashboard. A raised exception here
    would be turned into UI `failed` by the API build thread (the split-brain P1-2).
    """
    ref = result.dashboard
    session = store.session_row(session_id)
    owner = session.get("owner") if session else None
    arts = [
        {
            "kind": art.kind,
            "native_id": art.native_id,
            "name": art.name,
            "schema_set": art.schema_set,
        }
        for art in result.artifacts
    ]
    try:
        store.commit_build_success(
            session_id,
            spec_id,
            dashboard_id=ref.id,
            url=ref.url,
            build_token=build_token,
            target_bi=spec.target_bi.value,
            owner=owner,
            artifacts=arts,
            session_status=SESSION_BUILT,
        )
    except Exception as exc:
        logger.exception(
            "atomic build+ledger commit failed for session %s token %s; "
            "recording delivered_pending (dashboard already in BI)",
            session_id,
            build_token,
        )
        try:
            store.commit_build_delivered_pending(
                session_id,
                spec_id,
                dashboard_id=ref.id,
                url=ref.url,
                build_token=build_token,
                error=store_error_text(exc),
            )
        except Exception:
            logger.exception(
                "could not record delivered_pending for session %s; "
                "BI dashboard %s at %s has no durable row",
                session_id,
                ref.id,
                ref.url,
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
) -> bool:
    """Auto-prune on rebuild: delete THIS session's prior-revision BI artifacts by id.

    Runs after a successful build + ledger record, so the freshly delivered dashboard is
    never touched (its rows carry `current_build_token`). Selection is `orphan_bi_artifacts`
    — ownership-keyed (session/owner/build_token, never name/title), shared kinds excluded
    in SQL. `delete_artifact` is a required BIAdapter method (plan_sol step 7). NEVER fails
    the build: the dashboard is already delivered, so any error here is logged and the
    leftover rows stay 'live' for a later prune. Returns True when prune completed without
    structural error (per-row delete failures still return True — they retry next prune);
    False when the prune path itself raised (caller may mark session degraded).
    Kill-switch: AUTO_BI_PRUNE_ON_REBUILD=false (wired via the `prune_orphans` parameter).

    INVARIANT (builds of ONE session are serial): `orphan_bi_artifacts` selects every ledger
    row of the session whose token differs from `current_build_token` — a CONCURRENT build of
    the same session that already recorded its ledger rows would be deleted here as a "prior
    revision". Today this is unreachable (the API rejects a second build of a running session
    with 409, the CLI creates a fresh session per run), but a future parallel executor MUST
    keep per-session builds serial or rework this selection (see ARCHITECTURE §3.17).
    """
    try:
        session = store.session_row(session_id)
        owner = session.get("owner") if session else None
        orphans = store.orphan_bi_artifacts(session_id, current_build_token, owner=owner)
        if not orphans:
            return True
        removed, failed = prune_artifact_rows(store, orphans, adapter.delete_artifact, log)
        line = f"prune: удалены артефакты прошлых сборок сессии: {removed}"
        if failed:
            line += f" (не удалось: {failed}, будут повторены следующим прунингом)"
        log(line)
        return True
    except Exception as exc:  # the build itself already succeeded — never re-raise
        log(f"prune: пропущен ({exc})")
        return False
