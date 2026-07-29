"""Ownership live-cleanup: delete superseded BI artifacts via the ledger."""

from collections.abc import Callable
from typing import Any

from auto_bi.adapters.base import BIAdapter
from auto_bi.store import SHARED_BI_KINDS, Store

# Ownership live-cleanup delete order, proven live on the stand (2026-07-18): charts first,
# then the dashboard, then datasets — a dataset is never deleted while a chart still reads it.
_PRUNE_ORDER = {"chart": 0, "dashboard": 1, "dataset": 2}


def prune_artifact_rows(
    store: Store,
    rows: list[dict[str, Any]],
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
    for row in sorted(rows, key=lambda r: _PRUNE_ORDER.get(r["kind"], len(_PRUNE_ORDER))):
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
