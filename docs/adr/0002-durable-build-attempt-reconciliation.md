# ADR 0002: Durable build-attempt reconciliation (pre-return BI crash window)

**Status:** Proposed — owner approval required (S4)
**Related:** [ADR 0001](0001-bi-adapter-contract.md), plan_sol step 8 residual (RR-4),
CLAUDE.md design invariants 1 / 7 and stopper S4
**Scope of this ADR:** design + evidence only. No production implementation.

## Context

### Exact crash window (RR-4)

The core RR-4 window is **from the first successful remote BI side effect inside
`BIAdapter.build(spec, ctx)` until `build()` returns a `BuildResult` to
`compile_and_build`** (`auto_bi/agent/pipeline.py`). The durable attempt must remain
open through the following Store commit as well: a process death after `BuildResult`
is in memory but before `commit_build_success` has the same restart symptom (remote
delivery with no durable native-id evidence).

Confirmed on current code (HEAD `9910f55`):

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
design’s primary target. It does not execute after SIGKILL/OOM; the proposed attempt
protocol therefore also covers the adjacent return-to-commit crash window without
claiming that the existing live exception path is broken.

### Compensating controls today (and their limits)

1. Session `building` + startup `reap_stuck_builds` → synthetic `failed` row, empty token.
2. DataLens best-effort `__wip` cleanup on ordinary `Exception` (not process death).
3. Dataset names often carry a namespace fingerprint; **native ids for charts/dashboards
   are not durable until return**, and many entities are not discoverable by namespace alone
   (see BI limits below).
4. Shared connections/databases are intentionally shared (`SHARED_BI_KINDS`); they must
   never be deleted as “orphans”.

## Decision (proposed)

### Required safety property

After process restart, a **stable build namespace** known before the first remote side
effect must allow the **owning adapter** to deterministically discover side effects of an
interrupted attempt and choose **exactly one** explicit outcome:

1. **finalize** a delivery only after proving its complete expected manifest and
   topology, then write build + ledger durably, or
2. **cleanup** partial **owned** remote entities created by that attempt.

If completeness cannot be proved, cleanup is the safe default. Shared
connections/databases are never deleted by attempt recovery.

### Minimal durable state machine

Per build attempt (one stable namespace / attempt id):

```
prepared → building → delivered → committed
                 ↘ cleanup_required → failed
```

| State | Meaning |
|---|---|
| `prepared` | Durable attempt row exists **before** first remote create; carries namespace, target BI, session, owner, expected shape enough for ops |
| `building` | At least one remote side effect may exist; process may die here |
| `delivered` | Adapter returned complete `BuildResult` (post-return path; already largely covered) |
| `committed` | Store success transaction finished (ledger included) |
| `cleanup_required` | Incomplete attempt; adapter must discover and delete owned partials |
| `failed` | Terminal after cleanup or after reaping with no remote work |

Transitions must be idempotent under restart and concurrent startup.

### Adapter-owned reconciliation seam (typed; generic pipeline stays BI-agnostic)

**Do not** put BI-native discovery in Store or generic pipeline. Proposed required seam
on `BIAdapter` (S4 — changes Protocol / factory / fakes):

```text
reconcile_build_attempt(attempt: BuildAttemptRef) -> ReconcileOutcome
```

Where:

- `BuildAttemptRef` carries at least: `namespace`, `target_bi`, `session_id`,
  `owner`, the durable `spec_id` and a normalized spec/expected-manifest fingerprint;
- `ReconcileOutcome` is a closed set, e.g.
  `nothing_found | finalized(BuildResult) | cleaned_partial | needs_operator`.

Minimum durable attempt data **before the first remote side effect**:

| Field | Why |
|---|---|
| `attempt_id` / `build_token` (stable namespace) | Discovery and idempotence key |
| `session_id`, `spec_id`, `owner` | Ownership + Store linkage; load the durable approved spec |
| `target_bi` | Which adapter factory to open at startup |
| `status` (`prepared`/`building`/…) | State machine |
| normalized spec / expected-manifest fingerprint | Prove the discovered set is complete before finalize |
| `created_at` | Operator ordering / stale detection |

The pipeline durably advances `prepared -> building` **before** invoking
`adapter.build`; every remote side effect therefore happens under a discoverable
`building` attempt. Optional later: per-side-effect outbox rows. Prefer **namespace
discovery first**; per-create durable recorder is an alternative (below).

`delete_artifact(kind, native_id)` remains the only destructive primitive. Reconciliation
**discovers** ids then deletes or finalizes; generic code never invents ids.

### Startup ordering, idempotence, concurrency

1. Open Store; migrate schema if needed.
2. List durable interrupted attempts (`building` / `cleanup_required`) without first
   converting their sessions to legacy synthetic failures.
3. For each attempt, construct the **owning** adapter (read-only settings; no user request).
4. Call `reconcile_build_attempt` once per attempt; record outcome durably.
5. Run legacy `reap_stuck_builds` only for `building` sessions that have no durable
   attempt row.
6. Keep existing `reconcile_pending_ledgers` for post-return `delivered_pending` (orthogonal).
7. Only then accept new builds for the same stable token.

Rules:

- **Idempotent:** second startup with no remote leftovers → `nothing_found` / already
  terminal; no double-delete failures treated as success (404/already-gone = ok).
- **Concurrency:** one reconciler per process at startup; if multi-worker ever appears,
  take a Store lock / lease on the attempt row before remote work.
- **Retry:** transient remote errors leave attempt in `cleanup_required` / `building` and
  surface to the operator; do not flip to silent `failed` without evidence.
- **Operator visibility:** unresolved attempts appear as Store rows + trace events
  (`build_reconcile` / new kind) with namespace, target_bi, status, last error — never
  raw provider bodies (SafeError / store_error_text discipline).

### BI-specific discoverability limits (must be truthful before claiming “bounded”)

#### Superset

| Entity | Namespaced today? | Discoverable after crash? |
|---|---|---|
| database / connection | shared name | **Must not delete**; reuse only |
| dataset | yes (`dataset_table_name` + namespace fingerprint) | By name pattern **if** naming is treated as contract |
| chart | **no** — `slice_name` is human `chart.title` | **Not** by namespace alone |
| dashboard | **no** — `dashboard_title` is human title | **Not** by namespace alone |

**Required before bounded cleanup is truthful:** charts and dashboards must carry the
stable namespace in a durable, queryable place (name fingerprint and/or metadata /
extra JSON that list+filter APIs can use). Until then, cleanup of charts/dashboards
after process death is **not** bounded by namespace discovery alone.

#### DataLens

| Entity | Namespaced today? | Crash notes |
|---|---|---|
| connection | shared | never delete as orphan |
| dataset / widget / dash | fingerprint in entry name + `__wip` temp names | `__wip` cleanup only on `Exception`; process death leaves temps; promote loop is sequential |

**Required before bounded cleanup is truthful:**

1. Treat `__wip` + canonical fingerprint names as a **stable discovery contract**
   (document charset, suffix, fingerprint length).
2. Catch-up path must list workbook entries by name pattern / metadata for the attempt
   namespace — not only in-memory `wip_created`.
3. Promotion remains non-atomic across entries: reconcile must accept partial promote
   (some canonical, some `__wip`) and either finish promote or roll back owned partials.

### Crash-window table (each window needs an idempotent recovery rule)

| # | Window | Remote state | Local durable evidence today | Recovery rule (proposed) |
|---|---|---|---|---|
| W0 | Before first remote create | none | none / optional `prepared` | Mark `failed` or leave absent; no remote work |
| W1 | After remote create response, before any per-entity evidence write | entity exists | durable attempt is already `building`, but has no native id | Adapter list-by-namespace; cleanup or attach |
| W2 | Mid-build, memory buffer holds some artifacts | partial owned set | session `building`; no ledger | Discover all owned; if incomplete → cleanup; if complete set found → finalize |
| W3 | DataLens after some creates, before promote | `__wip` entries | same | Delete owned `__wip` or complete promote if full set present |
| W4 | DataLens mid-promote | mix of `__wip` + canonical | same | Finish promote **or** delete this attempt’s owned entries only |
| W5 | `build()` returned, before Store commit | full dashboard | durable attempt + namespace, but `BuildResult` only in memory | Rediscover and finalize only if the full expected manifest/topology is proved; otherwise cleanup |
| W6 | Process death during W1–W4 | orphaned partials | reap → `failed`, empty token, **no ids** | **Gap today** — this ADR |

**Design is not “bounded” until every W1–W5 path has an idempotent rule that does not
require guessing foreign entities.** Superset chart/dashboard naming is currently an
**unresolved S4 blocker** for full truthfulness of W2 cleanup without a wider public
naming/metadata contract change.

### Schema / migration / compatibility

Likely Store schema **v9** (illustrative; not implemented here):

- `build_attempts` table **or** extended `builds` rows written at `prepared` with
  non-empty `build_token` **before** remote work;
- status enum expansion; indexes on `(status)`, `(build_token)`;
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

### Affected consumers (when implemented)

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

## Implementation gate (no code in this round)

**S4:** changing `BIAdapter` / `BuildContext` / Store attempt schema / design invariants
requires owner approval. Evidence (this ADR + RED test) is authorized; **implementation
is not**.

Unresolved S4 blockers to resolve **in the approval** (do not hide):

1. Add required `reconcile_build_attempt` (name finalizable) to Protocol + fakes.
2. Durable attempt row **before** first remote create (schema v9-class).
3. **Superset chart/dashboard discoverability** — public naming or metadata contract
   change so cleanup is not a guess. Without (3), only dataset-level cleanup is honest;
   claiming full bounded recovery would be false.
4. Define the normalized expected manifest/topology used to prove “complete” before
   finalization; otherwise interrupted attempts must take the cleanup outcome.

### Staged GREEN plan (future, separate rounds)

1. Owner approves this ADR + names the seam.
2. RED stays red; implement Store attempt + pipeline `prepared` write (no remote yet).
3. Fake adapter GREEN for fault-injection (finalize/cleanup).
4. Superset: naming/metadata for charts/dashboards + discover + cleanup/finalize.
5. DataLens: durable `__wip`/fingerprint discovery + promote/repair.
6. Startup wiring + legacy-reap exclusion + ops trace; contract suite; no silent
   schema drift.

## Consequences

- Residual RR-4 remains open until GREEN stages land under S4 approval.
- Closure docs may keep “future” until then; this ADR is the decision package.
- Accepting alternative D is an explicit product decision, not silent tech debt.

## References

- `auto_bi/agent/pipeline.py` — `compile_and_build`, `_commit_delivery`
- `auto_bi/store/db.py` — schema v8, `reap_stuck_builds`, `reconcile_pending_ledgers`
- `auto_bi/cli.py` — startup recovery order
- `auto_bi/adapters/superset/adapter.py` / `datalens/adapter.py` — build buffers, `__wip`
- `tests/test_build_attempt_reconciliation.py` — reproducible RED for W1/W2 + BaseException
