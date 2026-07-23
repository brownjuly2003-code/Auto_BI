# ADR 0001: BIAdapter BuildContext / BuildResult / lifecycle

**Status:** Accepted (2026-07-23)
**Plan:** [plan_sol_23_07_26.md](../../plan_sol_23_07_26.md) шаг 7
**Audit:** [audit_gpt_23_07_26.md](../../audit_gpt_23_07_26.md) P1-1

## Context

`BIAdapter` historically declared only health / ensure_* / build → `DashboardRef`.
The real production contract was wider and **optional**:

- `set_artifact_namespace` — namespace isolation (P0-2)
- `set_query_plans` — PlanCache reuse for magnitude (D-2 §5)
- `drain_build_artifacts` — ownership ledger payload
- `delete_artifact` — live prune / `auto_bi prune`
- `close` — HTTP pool release (D-2 lifecycle)

The pipeline and factory reached these via `getattr`. A new adapter could satisfy
the Protocol yet silently drop ownership, cleanup, or pool release.

## Decision

1. **`BuildContext`** — required input to `build(spec, ctx=None)` carrying
   `namespace`, `plans`, `session_id`, `owner`.
2. **`BuildResult`** — required output of `build()` with `dashboard: DashboardRef`
   and `artifacts: tuple[BuildArtifact, ...]`.
3. **`delete_artifact` and `close`** are required Protocol methods.
4. **`validate_adapter_contract`** runs in `make_adapter` (and is testable for
   third-party fakes).
5. Deprecated helpers `set_artifact_namespace` / `set_query_plans` /
   `drain_build_artifacts` remain on concrete adapters for unit tests only;
   production orchestrators must not use getattr.

## Consequences

- Fake third adapters must implement ownership + cleanup + close to pass the
  contract suite (`tests/test_adapter_contract.py`).
- Integration/unit tests that called `adapter.build(spec)` still work: return
  value proxies `id`/`title`/`url` to the inner dashboard; prefer
  `result.dashboard` / `result.artifacts` in new code.
- Atomic build/ledger state (plan_sol step 8) can consume `BuildResult` without
  a second drain hop.

## Alternatives considered

- Separate capability Protocols (`OwnershipCapable`, `Closeable`) composed at
  factory time — more precise, but more complex for two first-party adapters.
- Keep getattr with a capability registry — still optional by construction.
