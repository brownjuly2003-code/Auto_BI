# ADR 0002: Durable build-attempt reconciliation (pre-return BI crash window)

**Status:** Accepted and implemented (RR-4, 2026-07-29)
**Related:** [ADR 0001](0001-bi-adapter-contract.md), plan_sol step 8 residual (RR-4),
CLAUDE.md design invariants 1 / 7 and stopper S4
**Scope of this ADR:** cleanup-only crash recovery for pre-return BI side effects.

## Context

### Exact crash window (RR-4)

The core RR-4 window is **from the first successful remote BI side effect inside
`BIAdapter.build(spec, ctx)` until `build()` returns a `BuildResult` to
`compile_and_build`** (`auto_bi/agent/pipeline.py`). The durable attempt must remain
open through the following Store commit as well: a process death after `BuildResult`
is in memory but before `commit_build_success` has the same restart symptom (remote
delivery with no durable native-id evidence).

The pre-fix evidence was:

| Fact | Evidence |
|---|---|
| Pipeline receives native ids / ownership artifacts **only after** `adapter.build()` returns | `result: BuildResult = adapter.build(spec, ctx)` then `_commit_delivery(..., result.artifacts)` |
| `except Exception` does **not** catch `BaseException` / process death | Failure path uses `except Exception`; SIGKILL/OOM never runs it |
| Superset accumulates `_build_artifacts` only in process memory | `SupersetAdapter.build` buffer; no durable write until return |
| DataLens cleans `__wip` only in `except Exception` | `except Exception: self._cleanup_wip(...)`; promotion is sequential, not multi-entry atomic |
| Startup reaping has **no** adapter / remote-entity evidence | `Store.reap_stuck_builds` inserts `failed` with `build_token=''` and no `bi_artifacts`; `reconcile_pending_ledgers` only audits **post-return** `delivered_pending` |

### What is already fixed (do not regress / do not claim broken)

Post-return exception handling is already bounded (plan_sol step 8 / P1-2):

- after `build()` returns, success commits build + session + ledger atomically;
- ledger commit failure → `delivered_pending` + `built_with_cleanup_degraded` (dashboard
  still returned; no UI split-brain of “BI has it / Store says failed”);
- stable `build_token` per `(session, approved spec_row)` makes approve retry idempotent
  for **already delivered** builds.

`delivered_pending` is a different residual (operator ledger repair), not this
design’s primary target. It does not execute after SIGKILL/OOM; the durable attempt
protocol also covers the adjacent return-to-commit crash window without
claiming that the existing live exception path is broken.

### Compensating controls today (and their limits)

1. Session `building` + startup `reap_stuck_builds` → synthetic `failed` row, empty token.
2. DataLens best-effort `__wip` cleanup on ordinary `Exception` (not process death).
3. Dataset names often carry a namespace fingerprint; **native ids for charts/dashboards
   are not durable until return**, and many entities are not discoverable by namespace alone
   (see BI limits below).
4. Shared connections/databases are intentionally shared (`SHARED_BI_KINDS`); they must
   never be deleted as “orphans”.

## Decision

### Required safety property

After process restart, a **stable build namespace** stored before the first remote side
effect lets the **owning adapter** deterministically discover and remove side effects of
an interrupted attempt. RR-4 v1 is deliberately **cleanup-only**: startup never adopts a
remote dashboard as successful without the normal in-process `BuildResult`. This may
discard a fully-created dashboard when the process dies just before the local commit, but
it prevents both an orphan and a fabricated ownership ledger. Shared
connections/databases are never deleted by attempt recovery.

### Minimal durable state machine

Per build attempt (one stable namespace / attempt id):

```
prepared → building → committed
    ↘ aborted       ↘ delivered_pending
                    ↘ cleanup_required → cleaned
                                         ↘ cleanup_required
```

| State | Meaning |
|---|---|
| `prepared` | Durable attempt row exists before the remote phase; exact normalized spec snapshot + fingerprint are stored |
| `building` | At least one remote side effect may exist; process may die here |
| `committed` | Attempt + build row + session success + ownership ledger committed in one transaction |
| `delivered_pending` | BI delivery returned, but the full success transaction failed; existing degraded-delivery audit owns this terminal path |
| `cleanup_required` | Incomplete attempt; adapter must discover and delete owned partials |
| `cleaned` | Adapter proved all discoverable attempt-owned artifacts absent/deleted; failed build row carries the real token |
| `aborted` | Process died while still `prepared`; no remote call was permitted, so recovery is non-destructive |

Transitions must be idempotent under restart and concurrent startup.

### Adapter-owned reconciliation seam (typed; generic pipeline stays BI-agnostic)

**Do not** put BI-native discovery in Store or generic pipeline. The required `BIAdapter`
seam (owner-approved S4 change) is:

```text
reconcile_build_attempt(attempt: BuildAttempt) -> BuildReconcileResult
```

Where:

- `BuildAttempt` carries the full stable `build_token`, `session_id`, `owner`,
  `spec_id`, and the fingerprint-verified durable `DashboardSpec` snapshot;
- returning `BuildReconcileResult(discovered, deleted)` proves exact owned cleanup
  completed; provider/search/shape failures raise and keep `cleanup_required`.

Minimum durable attempt data **before the first remote side effect**:

| Field | Why |
|---|---|
| `attempt_id` / `build_token` (stable namespace) | Discovery and idempotence key |
| `session_id`, `spec_id`, `owner` | Ownership + Store linkage; load the durable approved spec |
| `target_bi` | Which adapter factory to open at startup |
| `status` (`prepared`/`building`/…) | State machine |
| normalized spec snapshot + fingerprint | Recompute the exact names used by the interrupted build and detect Store corruption |
| `created_at` | Operator ordering / stale detection |

The pipeline durably advances `prepared -> building` **before** invoking
`adapter.build`; every remote side effect therefore happens under a discoverable
`building` attempt. Optional later: per-side-effect outbox rows. Prefer **namespace
discovery first**; per-create durable recorder is an alternative (below).

`delete_artifact(kind, native_id)` remains the only destructive primitive. Reconciliation
**discovers** exact owned ids then deletes them; generic code never invents ids or adopts
a remote delivery.

### Startup ordering, idempotence, concurrency

1. Open Store; migrate schema if needed.
2. List durable interrupted attempts (`prepared` / `building` / `cleanup_required`) without first
   converting their sessions to legacy synthetic failures.
3. For each attempt, construct the **owning** adapter (read-only settings; no user request).
4. Mark `prepared` as `aborted` without a remote call; otherwise call
   `reconcile_build_attempt` and record `cleaned` or `cleanup_required` durably.
5. Run legacy `reap_stuck_builds` only for `building` sessions that have no durable
   attempt row.
6. Keep existing `reconcile_pending_ledgers` for post-return `delivered_pending` (orthogonal).
7. Only then accept new builds for the same stable token.

Rules:

- **Idempotent:** second startup with no remote leftovers → `nothing_found` / already
  terminal; no double-delete failures treated as success (404/already-gone = ok).
- **Concurrency:** a partial unique SQLite index permits one nonterminal attempt per
  session. Startup is single-process today; a future multi-worker startup still requires
  a row lease/CAS before remote cleanup.
- **Retry:** transient remote errors leave attempt in `cleanup_required` / `building` and
  surface to the operator; do not flip to silent `failed` without evidence.
- **Operator visibility:** unresolved attempts appear as Store rows + startup logs with
  namespace, target_bi, status and last safe error — never raw provider bodies
  (`SafeError` / `store_error_text` discipline).

### BI-specific discoverability limits (must be truthful before claiming “bounded”)

#### Superset

| Entity | Namespaced today? | Discoverable after crash? |
|---|---|---|
| database / connection | shared name | **Must not delete**; reuse only |
| dataset | yes (`dataset_table_name` + namespace fingerprint) | By name pattern **if** naming is treated as contract |
| chart | full token in `params.auto_bi_build_token`; human `slice_name` unchanged | Exact-title candidate pagination, detail fetch, then exact full-token equality |
| dashboard | full token in `json_metadata.auto_bi_build_token`; human title unchanged | Exact-title candidate pagination, detail fetch, then exact full-token equality |

Human titles are candidate filters only and never deletion authority. A chart/dashboard
without the exact marker is retained. Datasets are deleted only by the exact technical
name recomputed from the durable spec snapshot and full namespace input.

#### DataLens

| Entity | Namespaced today? | Crash notes |
|---|---|---|
| connection | shared | never delete as orphan |
| dataset / widget / dash | fingerprint in entry name + `__wip` temp names | `__wip` cleanup only on `Exception`; process death leaves temps; promote loop is sequential |

Recovery recomputes canonical + `__wip` names with the same `dataset_name`,
`_owned_entry_name`, and `_wip_name` functions used by `build()`, performs exact-name
workbook lookups, and deletes both sides of a partial promotion. It never searches or
deletes connection scope.

### Crash-window table (each window needs an idempotent recovery rule)

| # | Window | Remote state | Durable evidence | Implemented recovery rule |
|---|---|---|---|---|
| W0 | Before first remote create | none | `prepared` | Mark `aborted`; never call adapter cleanup |
| W1 | After remote create response, before any per-entity evidence write | entity exists | `building` + snapshot + token | Adapter exact token/name discovery; cleanup |
| W2 | Mid-build, memory buffer holds some artifacts | partial owned set | same | Discover and clean all provably owned objects |
| W3 | DataLens after some creates, before promote | `__wip` entries | same | Delete exact owned `__wip` and canonical names |
| W4 | DataLens mid-promote | mix of `__wip` + canonical | same | Delete both exact name sets; no promotion adoption |
| W5 | `build()` returned, before Store commit | full dashboard | attempt still `building` | Cleanup-only; a restart never fabricates success |
| W6 | Process death during W1–W5 | partial/full remote set | nonterminal attempt with real token | Startup adapter reconcile, then token-bearing failed build |

### Schema / migration / compatibility

Store schema **v9**:

- `build_attempts` stores session/spec linkage, unique non-empty `build_token`,
  target, owner, exact canonical JSON snapshot, sha256 fingerprint, status/error and
  timestamps;
- indexes cover status and enforce one nonterminal attempt per session;
- success updates attempt=`committed` in the same transaction as build/session/ledger;
- migration default: legacy DBs have no open attempts → no behavior change for happy path.

Compatibility:

- old sessions without attempts: startup no-op;
- adapters without new method: factory contract fails closed (same pattern as ADR 0001);
- CLI/API consumers: only startup path + ops listing change; public dashboard URL contract
  unchanged on success.

### Security and ownership

- Reconcile only entities owned by Auto_BI attempt namespace / workbook scope.
- Never delete `SHARED_BI_KINDS` (`database`).
- No provider bodies in Store errors; reuse SafeError redaction.
- Auth: startup uses process credentials, not end-user tokens; attempt rows retain
  `owner` for RBAC of any later operator UI.

### Affected consumers

- `auto_bi/adapters/base.py` (Protocol, types)
- both production adapters + factory / `validate_adapter_contract`
- `auto_bi/agent/pipeline.py` (prepare attempt before `build`)
- `auto_bi/store/db.py` + CLI startup
- contract / pipeline / store tests; third-party fakes

## Alternatives considered

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **A. Generic outbox only** (pipeline inserts “intent” row, no adapter discovery) | Simple Store change | Cannot map intent → native ids after crash; `delete_artifact` needs ids | Insufficient alone |
| **B. Per-side-effect durable recorder** (write each id before/after create) | Strong evidence | Extra latency; still loses the window between remote success and local write (W1); needs two-phase care | Optional reinforcement, not sole design |
| **C. Stable namespace discovery** (this ADR) | Matches ownership model; keeps Store BI-agnostic; restart-safe | Requires truthful naming/metadata on **all** owned kinds; Protocol change (S4) | **Preferred** |
| **D. Accept v1 residual** | Zero code risk | Orphan BI entities accumulate under kill/OOM; ops burden | Acceptable only as explicit product freeze (`PROJECT_CLOSURE` future) |

## Implementation record

The owner explicitly authorized resolving the existing RR-4 RED and directed use of
Grok instead of Claude for the second opinion. The local Grok CLI (`grok-4.5`) approved
the cleanup-only shape with these mandatory corrections, all implemented here:

1. `BUILDING` commits before `adapter.build`.
2. Attempt=`committed`, build, session and ledger share one success transaction.
3. `prepared` recovery is non-destructive.
4. Provider/search failures stay `cleanup_required` and block retry.
5. Superset deletion requires exact full-token metadata; titles only narrow candidates.
6. DataLens uses exact canonical/`__wip` names from the stored snapshot and never
   deletes shared connections.
7. Startup reconciles attempts before a reaper that excludes attempt-covered sessions.

`tests/test_build_attempt_reconciliation.py` injects a `BaseException` after a fake
remote create and before `BuildResult`; the persisted attempt is `building`, restart
cleanup removes the entity, records a token-bearing failed build, and the legacy reaper
does nothing. Store, adapter-contract, Superset and DataLens tests cover the remaining
ordering and false-positive deletion rules.

## Consequences

- RR-4 is closed for startup cleanup under the single-process server model.
- A future multi-worker startup needs an attempt lease/CAS; the current unique active
  index prevents concurrent build intents but is not a distributed cleanup lease.
- Startup intentionally prefers cleanup over remote-success adoption when the process
  dies before the atomic local success transaction.

## References

- `auto_bi/agent/pipeline.py` — `compile_and_build`, `_commit_delivery`
- `auto_bi/store/db.py` — schema v9, durable attempts, attempt-aware reaper
- `auto_bi/cli.py` — startup recovery order
- `auto_bi/adapters/superset/adapter.py` / `datalens/adapter.py` — build buffers, `__wip`
- `tests/test_build_attempt_reconciliation.py` — RED-to-GREEN W1/W2 BaseException proof
