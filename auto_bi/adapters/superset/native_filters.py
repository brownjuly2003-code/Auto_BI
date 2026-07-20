"""Native dashboard filters: compile spec.filters -> Superset native_filter_configuration.

D-1 (variant A): charts on the shared semantic-grain source dataset carry every mart
column (plus label joins), so a native filter (a WHERE by the column's bare alias)
scopes to ALL SOURCE-role charts of that mart whose source dataset exposes the column.
OWN-role charts keep the pre-D-1 grain rule: only a chart that GROUPs by the filtered
column can honor it (its aggregated dataset has nothing else to filter on).

Qualified comparison (audit 19.07, finding #1): scope never matches on bare column
name alone — `dm.products.name` must not grab a chart grouped by `dm.stores.name`.
Both sides are normalized to a fully qualified `schema.table.col` before equality.

Auto-overview (P1-1) additionally bakes the default period into OWN charts'
`query.filters` (SQL WHERE via a relative "last N …" token). SOURCE charts open on
the native filter's default instead (no WHERE in the shared source SQL). Interactive
re-scoping on the dashboard moves every in-scope chart; OWN charts stay on the baked
window and the preview marks them "фильтр не влияет".

filterType comes from the column's semantic role (TIME -> filter_time, else
filter_select), not from DashboardFilter.type — the LLM does not reliably override that
field's schema default ("time_range"), so a categorical filter would be mis-typed.

Format reverse-engineered from the pinned 4.1 stand (create -> GET json_metadata) and
verified live (filter_select on a dim filters its in-scope charts and leaves the
excluded KPI untouched). Round-trip is pinned by tests/test_superset_contract.py.
"""

from __future__ import annotations

import hashlib

from auto_bi.agent.dataset_plan import (
    DatasetPlan,
    DatasetRole,
    plan_datasets,
    source_dataset_inputs,
)
from auto_bi.ir.spec import ChartSpec, DashboardFilter, DashboardSpec, column_alias
from auto_bi.semantic.model import ColumnRole, SemanticModel

# (chart, superset slice id, virtual dataset id) for one placed chart
Placement = tuple[ChartSpec, int, int]


def _filter_id(column: str) -> str:
    digest = hashlib.sha1(column.encode()).hexdigest()[:6]
    return f"NATIVE_FILTER-auto_bi_{column_alias(column)}_{digest}"


def _column(column: str, model: SemanticModel):
    table_name, _, col = column.rpartition(".")
    table = model.table(table_name)
    return table.column(col) if table else None


def _is_temporal(column: str, model: SemanticModel) -> bool:
    c = _column(column, model)
    return c is not None and c.role == ColumnRole.TIME


def _filter_name(filter_: DashboardFilter, model: SemanticModel) -> str:
    """Readable label: the column's model description, else its bare name."""
    c = _column(filter_.column, model)
    if c is not None and c.description.strip():
        return c.description.strip()
    return column_alias(filter_.column)


def qualified_column_ref(ref: str, default_table: str) -> str:
    """Fully qualify a column ref against `default_table` when it is bare.

    Dashboard filters and joined dimensions are already `schema.table.col`; mart grain
    columns are often bare (`date`, `store_id`). Comparing bare names alone wrongly
    equates `dm.products.name` with `dm.stores.name` (both alias to `name`).
    """
    if "." in ref:
        return ref
    return f"{default_table}.{ref}"


def grain_exposes_column(chart: ChartSpec, filter_column: str) -> bool:
    """Whether the chart's GROUP BY grain carries `filter_column` (qualified match)."""
    target = qualified_column_ref(filter_column, chart.query.table)
    for col in chart.query.group_columns():
        if qualified_column_ref(col, chart.query.table) == target:
            return True
    return False


def source_exposes_column(
    spec: DashboardSpec,
    plan: DatasetPlan,
    model: SemanticModel,
    table: str,
    filter_column: str,
) -> bool:
    """Whether the shared source dataset for `table` carries `filter_column`."""
    inputs = source_dataset_inputs(spec, plan, model, table)
    target = qualified_column_ref(filter_column, table)
    # mart's own column: filter names schema.table.col and the bare name is in columns
    ft, _, fname = filter_column.rpartition(".")
    if ft == table and fname in inputs.columns:
        return True
    if target in inputs.joined_refs or filter_column in inputs.joined_refs:
        return True
    # bare filter (unusual for DashboardFilter) against mart columns
    return "." not in filter_column and filter_column in inputs.columns


def chart_accepts_filter(
    chart: ChartSpec,
    filter_: DashboardFilter,
    spec: DashboardSpec,
    plan: DatasetPlan,
    model: SemanticModel,
) -> bool:
    """Whether a dashboard filter's WHERE can reach this chart's dataset."""
    cp = plan.chart(chart.id)
    if cp.role is DatasetRole.SOURCE:
        return source_exposes_column(spec, plan, model, cp.table, filter_.column)
    return grain_exposes_column(chart, filter_.column)


def participating_chart_ids(spec: DashboardSpec, model: SemanticModel) -> set[str]:
    """Spec-side chart ids that fall in at least one dashboard filter's scope.

    Their virtual datasets must drop the SQL top-N LIMIT (the limit moves to form_data)
    so the filter re-ranks AFTER filtering instead of over a pre-truncated top-N — and
    so a select filter's option list isn't itself capped to the pre-filter top-N.
    SOURCE charts already have no LIMIT in SQL; including them is harmless and keeps
    the helper's meaning ("in some filter's scope") honest for preview/logs.
    Computable from the spec alone, before any slice exists.
    """
    plan = plan_datasets(spec)
    ids: set[str] = set()
    for filter_ in spec.filters:
        for chart in spec.charts:
            if chart_accepts_filter(chart, filter_, spec, plan, model):
                ids.add(chart.id)
    return ids


def build_native_filter_configuration(
    spec: DashboardSpec,
    placements: list[Placement],
    model: SemanticModel,
    plan: DatasetPlan | None = None,
) -> tuple[list[dict], list[tuple[DashboardFilter, list[int], list[int]]]]:
    """(native_filter_configuration, applied) where `applied` pairs each WIRED filter
    with the slice ids it scopes to and the ones it skips — for an honest preview/log.
    A filter no chart can honor is skipped entirely; the baked query.filters still
    constrain OWN charts, so nothing silently breaks.
    """
    plan = plan or plan_datasets(spec)
    config: list[dict] = []
    applied: list[tuple[DashboardFilter, list[int], list[int]]] = []
    all_ids = [sid for _, sid, _ in placements]

    for filter_ in spec.filters:
        alias = column_alias(filter_.column)
        in_scope: list[int] = []
        target_dataset: int | None = None
        for chart, sid, dataset_id in placements:
            if chart_accepts_filter(chart, filter_, spec, plan, model):
                in_scope.append(sid)
                if target_dataset is None:
                    target_dataset = dataset_id
        if not in_scope:
            continue
        # in_scope is non-empty here, so target_dataset was set alongside its first chart
        assert target_dataset is not None
        excluded = [sid for sid in all_ids if sid not in in_scope]
        name = _filter_name(filter_, model)
        if _is_temporal(filter_.column, model):
            config.append(_time_filter(filter_, name, in_scope, excluded))
        else:
            config.append(_select_filter(filter_, name, alias, target_dataset, in_scope, excluded))
        applied.append((filter_, in_scope, excluded))
    return config, applied


def _scope(excluded: list[int]) -> dict:
    return {"rootPath": ["ROOT_ID"], "excluded": excluded}


def superset_time_range(default: str) -> str:
    """Normalize a DashboardFilter.default period phrase to a Superset time_range token.

    Superset parses relative tokens natively ("Last quarter", "Last 90 days"); the LLM/CLI
    emits them lower-cased ("last 90 days"), so we only title-case the leading "last ". An
    already-valid token or an ISO range ("2026-01-01 : 2026-06-30") passes through unchanged.
    """
    s = default.strip()
    if s.lower().startswith("last "):
        return "Last " + s[5:].strip()
    return s


def _time_default_mask(default: str) -> dict:
    """defaultDataMask for a time filter: preset the dashboard's time_range (B5).

    Empty default => the neutral empty mask (no preset, unchanged behavior). A non-empty
    default seeds both extraFormData.time_range (what actually re-scopes the queries) and
    filterState.value (what the filter control shows as selected)."""
    if not default.strip():
        return {"filterState": {}, "extraFormData": {}}
    tr = superset_time_range(default)
    return {"extraFormData": {"time_range": tr}, "filterState": {"value": tr}}


def _select_default_mask(default: str, alias: str) -> dict:
    """defaultDataMask for a select filter: preset a single categorical value (B5).

    extraFormData.filters is what re-scopes the in-scope charts (a WHERE alias IN [value]);
    filterState.value is the control's shown selection. Empty default => neutral mask."""
    if not default.strip():
        return {"filterState": {}, "extraFormData": {}}
    value = [default.strip()]
    return {
        "extraFormData": {"filters": [{"col": alias, "op": "IN", "val": value}]},
        "filterState": {"value": value},
    }


def _select_filter(
    filter_: DashboardFilter,
    name: str,
    alias: str,
    dataset_id: int,
    in_scope: list[int],
    excluded: list[int],
) -> dict:
    return {
        "id": _filter_id(filter_.column),
        "name": name,
        "filterType": "filter_select",
        "type": "NATIVE_FILTER",
        "targets": [{"datasetId": dataset_id, "column": {"name": alias}}],
        "defaultDataMask": _select_default_mask(filter_.default, alias),
        "cascadeParentIds": [],
        "scope": _scope(excluded),
        "controlValues": {
            "enableEmptyFilter": False,
            "multiSelect": True,
            "searchAllOptions": False,
            "inverseSelection": False,
        },
        "chartsInScope": in_scope,
        "tabsInScope": [],
    }


def _time_filter(
    filter_: DashboardFilter,
    name: str,
    in_scope: list[int],
    excluded: list[int],
) -> dict:
    # a time-range filter targets no specific column: Superset applies it to each
    # in-scope dataset's main datetime column (auto-detected on the virtual dataset)
    return {
        "id": _filter_id(filter_.column),
        "name": name,
        "filterType": "filter_time",
        "type": "NATIVE_FILTER",
        "targets": [{}],
        "defaultDataMask": _time_default_mask(filter_.default),
        "cascadeParentIds": [],
        "scope": _scope(excluded),
        "controlValues": {},
        "chartsInScope": in_scope,
        "tabsInScope": [],
    }
