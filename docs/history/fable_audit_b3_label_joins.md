# S6 Code Review — B3 "label joins" (`phase-4/b3-label-joins`, commit `d2dedf8`)

Scope: `auto_bi/agent/normalize.py` (new `apply_label_joins` + helpers), `auto_bi/agent/pipeline.py`
(wiring before `apply_chart_defaults`), `tests/test_label_joins.py` (16 tests). Read for context:
`ir/spec.py`, `ir/validate.py`, `agent/sqlgen.py`, `semantic/model.py`,
`adapters/superset/form_data.py`, `adapters/datalens/{chart_config,dataset,adapter}.py`,
`tests/conftest.py`. Live stand NOT run (per instruction).

## Verdict

**ACCEPT — merge.** 0 P1 / 0 P2 / 5 P3.

The hard constraint B3 was gated on — *never silently merge distinct ids* — holds on every path I
could construct. The transform is a strict no-op on anything it cannot make valid: the merge-safety
guard, the model-edge check (invariant 2), the bare-alias-collision bail, and the order_by remap are
all sound, and `apply_label_joins` runs before `validate_spec` so any spec it emits is re-validated
anyway. Idempotency, B1 interaction order, viz-shape preservation, and purity are all correct. The
DataLens/Superset inheritance claim is real: both adapters address dataset columns by `column_alias`,
and a swapped `dm.stores.name` produces a dataset field keyed `"name"` that both consume unchanged.

Findings below are all P3 (nits / hardening / doc precision); none block merge.

## Merge-safety analysis (the gated constraint)

The guard in `_label_column` (`normalize.py:130-155`) is the load-bearing safety check. Walked the
attack surface:

- **Missing physical or empty cardinality** -> `None` (no swap). `normalize.py:137-139`. Correct: the
  conservative default the constraint requires. Covered by `test_no_cardinality_evidence_is_not_swapped`.
- **`id_card` fallback to `phys.rows`** (`normalize.py:140`): when the id column has no recorded
  cardinality, `phys.rows` is used as the id's distinct-count proxy. This is *conservative for a true
  PK target table* (rows == distinct ids), and the swap only fires when `label_card >= 0.99 * id_card`.
  If `phys.rows` *over*-estimates `id_card` (e.g. target table is not at id-grain), the threshold gets
  *harder*, never easier — so the fallback cannot cause an unsafe swap. Sound. (See P3-1 for the
  opposite direction — a spurious *miss* — which is only a readability loss, not a correctness risk.)
- **Zero cardinality / zero rows** -> `if not id_card: return None` (`normalize.py:141`). Correct.
- **`label_card` missing or zero** -> `if label_card and ...` short-circuits, candidate skipped
  (`normalize.py:152-153`). Correct: a label with no recorded cardinality is never trusted.
- **Threshold** `label_card >= 0.99 * id_card` (`normalize.py:153`): the regions fixture
  (8 names / 80 ids) is correctly rejected (`test_non_unique_name_is_not_swapped`); stores
  (4203 names / 4200 ids, label slightly *above* id from approximate `uniqCombined`) is correctly
  accepted. The `>=` with a label exceeding the id count is the intended tolerance for approximate
  counts — fine.

No path swaps to a name that is not ~unique per id. The constraint holds.

## Validity (must be a no-op on anything it can't make valid)

`apply_label_joins` runs at `pipeline.py:84`, **before** `validate_spec` at `pipeline.py:99`, so even a
hypothetical bad emission would be caught. But the transform is independently careful:

- **Invented join** — impossible: `_label_join_for` (`normalize.py:181-183`) checks
  `frozenset((on_left, col.fk)) in {edges of model.joins}` and returns `None` otherwise. Invariant 2
  upheld at the source. The emitted `JoinSpec(on_left=base.col, on_right=col.fk)` exactly mirrors the
  model edge that `validate_spec` accepts (`validate.py:46-69`).
- **"join declared but unused"** (`validate.py:126-128`) — cannot happen: a join is only added to
  `added` when its id ref was actually swapped into a dim role (`normalize.py:196-201`), so the joined
  table always contributes a used column. Confirmed by `test_swapped_spec_validates` /
  `test_swap_in_pivot_rows_keeps_shape`.
- **Bare-alias collision** (`validate.py:114-121`) — explicitly bailed: `normalize.py:211-214`
  recomputes bare aliases over the post-swap dim roles and returns the *original* chart if any collide
  (`store_id`+`product_id` both -> `name`). `test_bare_alias_collision_bails_whole_chart` covers it.
  This mirrors the validator's own collision rule (`column_alias` over `group_columns`). Good.
- **order_by referencing a removed id** (`validate.py:154-158`) — remapped: `normalize.py:218-220`
  rewrites any `ob.by` that was swapped to the new qualified ref. `test_order_by_on_id_is_remapped`
  covers it. The qualified ref is then orderable because `validate.py:147-148` adds both
  `group_cols` (qualified) and their bare aliases to `orderable`.
- **Qualified ref into a non-joined table** — impossible: the only refs the transform introduces are
  `f"{target_name}.{label.name}"` for a join it simultaneously adds to `merged_joins`.

## Idempotency, ordering, purity

- **Idempotent**: after a swap the dimension is `dm.stores.name` (qualified); `_label_join_for`
  returns `None` on any `"." in ref` (`normalize.py:165-166`), so a 2nd pass swaps nothing and
  `merged_joins`/`order_by` reconstruct identically. `test_idempotent` asserts `f(f(x)) == f(x)`.
- **B1 interaction** (`pipeline.py:84-90`): B3 runs first, then B1 ranks the now-named categorical
  axis. `_orders_by_measure` (B1) is unaffected by the rename because B3 only ever rewrites a
  *dimension* into order_by, never a measure ref. `test_composes_with_topn` confirms the combined
  SQL has both the join and `ORDER BY "sum_revenue" DESC LIMIT 25`. Order is correct: doing B1 first
  would rank by the raw id, then B3 would rename — same result here, but B3-first is the cleaner
  invariant. Fine.
- **Purity**: every mutation goes through `model_copy(update=...)` on the query / chart / spec; inputs
  are never mutated. `swaps`/`added`/`new_roles` are fresh locals. `rewrite` builds new lists. No
  aliasing of the input model or spec. Correct.

## Viz-shape preservation

In-place `swaps.get(r, r)` over each role list (`normalize.py:205-208`) preserves list length and
position for every role, so counts are conserved:

- **pie = exactly 1 dim**: swap is 1:1, `test_pie_swap_keeps_single_dimension` + re-validate confirms
  the shape rule (`validate.py:189-194`) still passes.
- **heatmap = exactly 2 dims**: not directly tested, but the 1:1 rewrite cannot change `len(dimensions)`,
  and any id among the two dims is independently swappable; the `swaps` dedup
  (`if ref in swaps: continue`, `normalize.py:195`) means two dims sharing the same id (degenerate)
  still produce one swap entry but `rewrite` applies it to both positions, preserving count. (Tested
  indirectly via the role-agnostic rewrite; see P3-4 for an explicit heatmap test suggestion.)
- **series / rows / columns**: `_DIM_ROLES` covers all four dimension-like roles; measures, filters,
  and `joins` are not in `_DIM_ROLES`, so they keep the raw id. `test_swap_in_series_role`,
  `test_swap_in_pivot_rows_keeps_shape`, `test_filter_on_id_is_preserved`, `test_measures_untouched`
  all confirm.

## Adapter inheritance (the "BOTH adapters" claim)

Verified the swap actually reaches both adapters as a *named* column, not just a valid spec:

- **DataLens**: `build_dataset_payload` (`dataset.py:220-221`) iterates `query.group_columns()` and
  keys each result_schema field by `column_alias(ref)` -> `dm.stores.name` becomes field `"name"`;
  `adapter.py:454` keys `fields_by_alias` by `f["title"]` == `"name"`; `build_chart_shared`
  (`chart_config.py:140`) looks up `column_alias(r)` == `"name"`. Resolves. (And because the swapped
  dim is now a `String` joined column, the B2 numeric-axis cast is a no-op for it — correct: it's no
  longer numeric.)
- **Superset**: `form_data.py` addresses every dimension via `column_alias` (lines 63, 73, 84, 92-94,
  113). `dm.stores.name` -> `"name"`. Resolves.
- **SQL**: `generate_chart_sql` qualifies + aliases joined columns to their bare name and emits the
  LEFT JOIN (`sqlgen.py:98-117`). `test_swap_emits_join_and_groups_by_name` confirms `"name"` in the
  grouped/selected output.

The dataset field type for the swapped column is resolved from `dm.stores.name`'s declared type via
`_resolve_column_type` (`dataset.py:133-141`), which handles the qualified ref correctly. Good.

## Findings (all P3)

**P3-1 — `_label_column` only inspects DIMENSION-role label columns; a name column typed
`role=TIME`/`MEASURE` or missing the role would be skipped.** `normalize.py:146-148` filters
`c.role == ColumnRole.DIMENSION`. This is the safe direction (a non-dimension "name" is unusual and
skipping it only forgoes a readability swap), so no correctness issue — noting only that an
introspected dim table whose `name` column was mis-roled would silently never get the swap. Acceptable
as-is; mention in the docstring if you want the limitation explicit.

**P3-2 — id-column cardinality fallback to `phys.rows` is undocumented at the call site for the
*ratio* implication.** `normalize.py:140` `id_card = phys.cardinality.get(id_col) or phys.rows`. The
docstring explains "no cardinality -> no swap" but not that the `rows` fallback only ever *tightens*
the threshold. The reasoning is correct (see merge-safety section) but a one-line comment would prevent
a future reader from "fixing" it into an unsafe direction. Doc-only.

**P3-3 — multiple label candidates: tie-break is `name`-first then arbitrary `sort` stability.**
`normalize.py:150` sorts candidates so an exact `"name"` wins, but among several non-`name` hints
(`title`, `label`, …) the order is whatever `target.columns` yields. Deterministic given a fixed model
(so idempotent/pure hold), but the choice between e.g. `title` and `label` is incidental. Low impact;
consider a documented secondary key (e.g. highest cardinality, or column order) if dim tables ever have
several label-like columns. No test covers the multi-candidate path.

**P3-4 — no explicit heatmap (2-dim) or two-ids-in-one-chart test.** The role-agnostic rewrite makes
shape preservation obvious, but the prompt called these out as attack targets and the suite has no
heatmap case nor a chart where two *different* swappable ids both succeed (distinct names, no bare-alias
collision — e.g. `store_id` + a second FK whose label is not `name`). Adding one would lock in the
multi-swap + count-preservation behavior. Test-only.

**P3-5 — `_label_joins_chart` recomputes `column_alias` for the collision check over a dict-deduped
list, but the validator dedups over `group_columns()` (insertion-order dict).** `normalize.py:211-212`
builds `group_cols` then `dict.fromkeys` then `column_alias`; `validate.py:114-115` does
`group_columns()` then `column_alias`. Both dedup the *qualified* refs before aliasing, so they agree —
but the two implementations are subtly parallel (one inlines the dedup, one calls the IR method). If
`group_columns()` ever changes its dedup semantics, the bail check could drift out of sync. Cosmetic;
consider calling `q.group_columns()`-style dedup in one place. Defense is anyway backstopped by the
post-transform `validate_spec`.

## Notes (not findings)

- The `pipeline.py:85-87` relabel-detection compares `c.query != o.query` to log changed chart ids —
  correct and cheap; pydantic models compare by value.
- `apply_label_joins` correctly leaves a chart with no swaps as the *same object* (`return chart`,
  `normalize.py:203`), so `model_copy` cost is only paid on charts that change. Good.
- Filters keeping the raw id is the right call: a `store_id = 7` filter stays exact and the displayed
  axis shows the name — confirmed `test_filter_on_id_is_preserved`. (The selector-scope machinery in
  the DataLens adapter scopes by `group_columns()`, which now contains `dm.stores.name`; a dashboard
  filter on the raw `store_id` would not match the swapped chart's group columns and would be excluded
  from its scope — this is pre-existing scope behavior, not a regression, but worth a live check when
  a dashboard combines a `store_id` selector with a swapped chart.)
