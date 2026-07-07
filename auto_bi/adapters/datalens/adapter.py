"""DataLensAdapter: deterministic IR compiler -> DataLens self-hosted (reversal §5).

Flow (mirrors SupersetAdapter, ARCHITECTURE §3.5):
  ensure_database  -> bi/createConnection            (DataLens connection entry)
  ensure_dataset   -> bi/createDataset               (one validated-SQL subselect / chart)
  create_chart     -> POST /api/charts/v1/charts     (US widget-entry; engine adds JS stubs)
  assemble_dashboard -> mix/createDashboardV1         (US dash-entry, scope=Dash)

All five steps are LIVE-VERIFIED end-to-end on the self-hosted stand: connection ->
datasets -> charts render real ClickHouse data, and the dashboard entry is created via the
`mix/createDashboardV1` gateway action (reversal §5.3; the action injects schemeVersion,
gathers chart links and runs Dash.validateData server-side, then calls us._createEntry).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid

from auto_bi.adapters.base import (
    AdapterHealth,
    ChartRef,
    DashboardRef,
    DatabaseRef,
    DatasetRef,
    DWHConfig,
)
from auto_bi.adapters.datalens.chart_config import DEGRADED, build_chart_shared
from auto_bi.adapters.datalens.client import DataLensAPIError, DataLensClient
from auto_bi.adapters.datalens.dataset import (
    build_connection_payload,
    build_dataset_payload,
    dataset_name,
    safe_entry_name,
)
from auto_bi.adapters.superset.form_data import ru_kpi_scale
from auto_bi.adapters.superset.native_filters import participating_chart_ids
from auto_bi.agent.normalize import is_horizontal_bar
from auto_bi.ir.spec import (
    ChartQuery,
    ChartSpec,
    DashboardFilter,
    DashboardSpec,
    LayoutHint,
    Measure,
    Viz,
    column_alias,
    is_compact_number,
    measure_alias,
)
from auto_bi.semantic.model import ColumnRole, SemanticModel

logger = logging.getLogger(__name__)

# A placed chart: its spec, the created widget entryId, and its dataset id (for selectors).
Placement = tuple[ChartSpec, str, str]

# Connection entry name, engine-aware (F11): a CH and a GP connection in the same workbook
# must get distinct names, else idempotent-by-name reuse (ensure_database) would conflate
# them — both would resolve to one "Auto_BI ClickHouse" entry. The label is the human
# spelling of the engine (so the default CH name stays "Auto_BI ClickHouse", backward
# compatible with connections already created on the stand); unknown engines fall back to
# the raw engine string.
_CONNECTION_ENGINE_LABEL = {
    "clickhouse": "ClickHouse",
    "greenplum": "Greenplum",
    "greengage": "Greengage",
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
}


def connection_name(engine: str) -> str:
    return f"Auto_BI {_CONNECTION_ENGINE_LABEL.get(engine, engine)}"


# a measure is money (KPI unit gets a "₽") when its model description says so — kept as
# markers, not a hard-coded currency, so a count/qty KPI never gets a spurious ruble sign
# (mirrors adapters/superset/adapter.py `_MONEY_MARKERS`).
_MONEY_MARKERS = ("руб", "₽", "rub")

# charts-engine config stype for an inline (unsaved) /api/run of a metric chart — the
# wizard runner is `safeConfig: true`, so a body `config` is allowed (datalens-ui 0.3831.0
# `charts-engine/runners/index.js`; live-verified 2026-07-06: an inline metric run returns
# the same value blob as a saved chart's run).
_METRIC_STYPE = "metric_wizard_node"

# cartesian charts whose value axis gets RU magnitude units (scaled metric + the unit as a
# manual axis title) instead of the SI "15B" — mirrors adapters/superset/adapter.py
# `_AXIS_SCALE_VIZ`; big_number scales its headline separately (`_kpi_ru_scale`).
_AXIS_SCALE_VIZ = (Viz.LINE, Viz.BAR, Viz.STACKED_BAR, Viz.AREA)

# Atomic-rebuild temp-name suffix (F2): a fully-built entry lives under <canonical>__wip
# until the whole build succeeds, then is renamed to its canonical name. `_` is allowed
# anywhere in a DataLens entry name (charset, see safe_entry_name), so appending it keeps a
# sanitized canonical name valid.
_WIP_SUFFIX = "__wip"


def _wip_name(canonical: str) -> str:
    return f"{canonical}{_WIP_SUFFIX}"


# Dash grid is 24 columns. Selectors sit in a top row (6 wide, 2 tall, 4 per row); charts
# are packed below it. Chart tiles are sized per viz + the IR layout_hint (see _chart_tile),
# NOT a flat size — a fixed h=4 left big_numbers/charts too short and tables with no visible
# rows on the stand.
_DL_GRID_COLS = 24
_CTRL_W = 6
_CTRL_H = 2
_CTRLS_PER_ROW = 4

# Width: the IR layout_hint is on a 12-col scale (mirrors Superset / propose.py); map it to
# the 24-col dash grid so DataLens and Superset place charts consistently (×2).
_HINT_W_SCALE = 2

# Height (dash grid rows): a per-viz readability floor — tables/pivots need rows visible,
# charts need a real plot area, KPIs stay compact. The author's layout_hint.h only raises it
# (an explicit taller-than-default hint adds rows); it never shrinks a widget below its floor.
_VIZ_MIN_H: dict[Viz, int] = {
    Viz.BIG_NUMBER: 6,
    Viz.TABLE: 12,
    Viz.PIVOT: 12,
    Viz.HEATMAP: 10,
    Viz.PIE: 9,
    Viz.LINE: 9,
    Viz.AREA: 9,
    Viz.BAR: 9,
    Viz.STACKED_BAR: 9,
    Viz.HISTOGRAM: 9,
}
_DEFAULT_MIN_H = 9
_HINT_DEFAULT_H = LayoutHint().h  # hint.h above this default bumps the floor up
_HINT_H_SCALE = 2  # each extra hint-row -> this many dash rows


def _chart_tile(chart: ChartSpec) -> tuple[int, int]:
    """(width_cols, height_rows) of a chart widget on the 24-col dash grid — auto-scaled.

    Width honors the author's `layout_hint.w` (12-col scale -> 24, ×2) so charts land where
    Superset would put them. Height is the larger of a per-viz readability floor and the
    author's hint scaled up, so a KPI stays compact, a line/bar chart gets a real plot area,
    and a table/pivot is tall enough to show rows — replacing the old flat h=4 that cut
    content off (KPI showed no figure, table showed no rows). Width is clamped to the grid."""
    w = max(1, min(chart.layout_hint.w * _HINT_W_SCALE, _DL_GRID_COLS))
    extra = max(0, chart.layout_hint.h - _HINT_DEFAULT_H)  # author asked for taller than default
    h = _VIZ_MIN_H.get(chart.viz, _DEFAULT_MIN_H) + extra * _HINT_H_SCALE
    return w, h


# DashboardFilter.default period phrase -> DataLens relative-interval token (B5).
# Token grammar reversed from datalens-ui 0.3831.0: `shared/modules/charts-shared.js`
# (`resolveIntervalDate` accepts `__interval_<from>_<to>` where each endpoint may be a
# relative `__relative_[+-]N(y|Q|M|w|d|h|m|s|ms)`), and the dash control plugin
# (`plugins/control/js/index.js`) resolves a range-date selector's string
# `source.defaultValue` through `ChartEditor.resolveInterval` into the dashboard param.
_RELATIVE_UNIT = {"day": "d", "week": "w", "month": "M", "quarter": "Q", "year": "y"}
_LAST_N_RE = re.compile(r"^last\s+(?:(\d+)\s+)?(day|week|month|quarter|year)s?$", re.IGNORECASE)
_ISO_RANGE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s*:\s*(\d{4}-\d{2}-\d{2})$")


def datalens_interval_default(default: str) -> str:
    """Normalize a DashboardFilter.default period phrase to a DataLens interval token.

    "last 12 months" -> "__interval___relative_-12M___relative_+0d" (a relative range the
    engine re-resolves on every dashboard open, so the preset stays current); an ISO range
    "2026-01-01 : 2026-06-30" passes through as a fixed "__interval_<from>_<to>". Empty or
    unrecognized -> "" (no preset — the selector opens unset, the previous behavior)."""
    s = default.strip()
    if not s:
        return ""
    iso = _ISO_RANGE_RE.match(s)
    if iso:
        return f"__interval_{iso.group(1)}_{iso.group(2)}"
    last = _LAST_N_RE.match(s)
    if last:
        n = int(last.group(1) or 1)
        unit = _RELATIVE_UNIT[last.group(2).lower()]
        return f"__interval___relative_-{n}{unit}___relative_+0d"
    return ""


def _filter_is_time(filter_: DashboardFilter, model: SemanticModel) -> bool:
    table_name, _, col = filter_.column.rpartition(".")
    table = model.table(table_name)
    column = table.column(col) if table else None
    return column is not None and column.role == ColumnRole.TIME


def _filter_label(filter_: DashboardFilter, model: SemanticModel) -> str:
    """Readable selector title: the column's model description, else its bare alias."""
    table_name, _, col = filter_.column.rpartition(".")
    table = model.table(table_name)
    column = table.column(col) if table else None
    if column is not None and column.description.strip():
        return column.description.strip()
    return column_alias(filter_.column)


def build_selectors(
    spec: DashboardSpec,
    placements: list[Placement],
    fields_by_dataset: dict[str, dict[str, dict]],
    model: SemanticModel,
) -> tuple[list[dict], list[list[str]], list[tuple[DashboardFilter, list[str], list[str]]]]:
    """Compile spec.filters -> (control items, alias groups, applied log).

    Scope-to-applicable (mirrors superset.native_filters): a filter applies to a chart
    only if the column is in that chart's grain (group_columns). DataLens links selectors
    POSITIVELY by default (same dataset, or fields tied by `aliases`) — the one connection
    kind is "ignore" (exclusion) — so an out-of-scope chart, whose dataset has no such
    field, is simply never affected; no negative wiring is needed. Each chart is its own
    dataset, so the column's per-dataset field guids are grouped into one alias so the
    selector's value propagates to every in-scope chart. A control binds to the first
    in-scope dataset's field; TIME columns become a date(range) selector, others a select.
    """
    controls: list[dict] = []
    alias_groups: list[list[str]] = []
    applied: list[tuple[DashboardFilter, list[str], list[str]]] = []
    all_ids = [chart.id for chart, _, _ in placements]

    for filter_ in spec.filters:
        alias = column_alias(filter_.column)
        in_scope = [
            (chart, ds_id)
            for chart, _, ds_id in placements
            if alias in {column_alias(c) for c in chart.query.group_columns()}
            and alias in fields_by_dataset.get(ds_id, {})
        ]
        if not in_scope:
            continue
        guids = [fields_by_dataset[ds_id][alias]["guid"] for _, ds_id in in_scope]
        _, first_ds = in_scope[0]  # control binds to the first in-scope dataset's field
        field0 = fields_by_dataset[first_ds][alias]
        is_time = _filter_is_time(filter_, model)
        digest = hashlib.sha1(filter_.column.encode()).hexdigest()[:6]
        control_id = f"auto_bi_sel_{alias}_{digest}"
        # B5 preset default: a period phrase becomes a relative-interval token the control
        # resolves on open ("last 12 months" -> last 12 months of DATA, re-scoping the
        # in-scope charts, not just a badge); a categorical default becomes the initial
        # selection list. `defaults` (the dash item's initial params) must mirror
        # `source.defaultValue`, so the charts are narrowed on first render, before the
        # control itself has run. Empty/unparseable default => "" (unset, as before).
        if is_time:
            preset: str | list[str] = datalens_interval_default(filter_.default)
        else:
            preset = [filter_.default.strip()] if filter_.default.strip() else ""
        source = {
            "datasetId": first_ds,
            "datasetFieldId": field0["guid"],
            "fieldType": field0["data_type"],
            "datasetFieldType": field0["type"],
            "showTitle": True,
            "elementType": "date" if is_time else "select",
            "defaultValue": preset,
        }
        if is_time:
            source["isRange"] = True
        else:
            source["multiselectable"] = True
        controls.append(
            {
                "id": control_id,
                "namespace": "default",
                "type": "control",
                "data": {
                    "id": control_id,
                    "namespace": "default",
                    "title": _filter_label(filter_, model),
                    "sourceType": "dataset",
                    "source": source,
                },
                "defaults": {field0["guid"]: preset},
            }
        )
        if len(guids) > 1:  # tie the column's field across the in-scope datasets
            alias_groups.append(guids)
        scoped = {chart.id for chart, _ in in_scope}
        applied.append((filter_, list(scoped), [cid for cid in all_ids if cid not in scoped]))
    return controls, alias_groups, applied


def build_dashboard_data(
    spec: DashboardSpec,
    widget_ids: list[str],
    controls: list[dict] | None = None,
    alias_groups: list[list[str]] | None = None,
) -> dict:
    """US dash-entry `data` blob (reversal §5.3), shaped for the `mix/createDashboardV1`
    gateway action (zod `dataSchema` minus `schemeVersion`, which the action injects).

    One tab. `controls` (selectors) are laid out in a top row, charts below in a 2-column
    grid; each chart is a `widget` item whose inner tab binds to its US entryId.
    `validateData` (server-side) requires every item to have exactly one layout entry keyed
    by its id, and all ids unique — both hold (control ids, item ids, inner widget-tab ids
    and the tab id are distinct). `alias_groups` tie a filter column's per-dataset field
    guids so a selector propagates across the in-scope charts' datasets. `widget_ids` align
    with spec.charts.
    """
    if len(widget_ids) != len(spec.charts):
        raise ValueError(f"got {len(widget_ids)} widget ids for {len(spec.charts)} charts")
    controls = controls or []
    alias_groups = alias_groups or []
    # Deterministic, non-empty salt; ids are explicit so the salt is not used to derive them.
    salt = uuid.uuid5(uuid.NAMESPACE_URL, f"auto_bi_dash:{spec.title}").hex
    tab_id = f"auto_bi_tab_{salt[:8]}"
    items: list[dict] = []
    layout: list[dict] = []

    # controls fill their row evenly (a lone period selector spans the full width) so the top
    # row aligns to the same grid as the KPI / chart rows below — uniform tile widths per row
    per_row = min(len(controls), _CTRLS_PER_ROW) or 1
    ctrl_w = max(_CTRL_W, _DL_GRID_COLS // per_row)
    for j, control in enumerate(controls):
        items.append(control)
        layout.append(
            {
                "i": control["id"],
                "x": (j % _CTRLS_PER_ROW) * ctrl_w,
                "y": (j // _CTRLS_PER_ROW) * _CTRL_H,
                "w": ctrl_w,
                "h": _CTRL_H,
            }
        )
    y0 = -(-len(controls) // _CTRLS_PER_ROW) * _CTRL_H  # rows of controls, ceil-divided

    # Shelf-pack the auto-scaled chart tiles below the controls (mirrors Superset _pack_rows):
    # place left-to-right, wrapping to a new shelf when the next tile would overflow the 24-col
    # grid or when layout_hint.row changes (author's row grouping). Each shelf's y advances by
    # the tallest tile on it, so variable heights never overlap.
    cx = 0
    cy = y0
    shelf_h = 0
    prev_row_hint: int | None = None
    for chart, wid in zip(spec.charts, widget_ids, strict=True):
        item_id = f"auto_bi_item_{chart.id}"
        items.append(
            {
                "id": item_id,
                "namespace": "default",
                "type": "widget",
                "data": {
                    "hideTitle": False,
                    "tabs": [
                        {
                            "id": f"auto_bi_wt_{chart.id}",
                            "title": chart.title,
                            "description": "",
                            "chartId": wid,
                            "isDefault": True,
                            "params": {},
                        }
                    ],
                },
            }
        )
        w, h = _chart_tile(chart)
        new_group = prev_row_hint is not None and chart.layout_hint.row != prev_row_hint
        if cx > 0 and (new_group or cx + w > _DL_GRID_COLS):
            cx = 0
            cy += shelf_h
            shelf_h = 0
        layout.append({"i": item_id, "x": cx, "y": cy, "w": w, "h": h})
        cx += w
        shelf_h = max(shelf_h, h)
        prev_row_hint = chart.layout_hint.row
    # counter = next free hashid index (tab + per-widget item & inner-tab ids + controls), >= 1.
    counter = 1 + 2 * len(spec.charts) + len(controls)
    return {
        "salt": salt,
        "counter": counter,
        "tabs": [
            {
                "id": tab_id,
                "title": spec.title,
                "items": items,
                "layout": layout,
                "connections": [],
                "aliases": {"default": alias_groups} if alias_groups else {},
            }
        ],
        "settings": {
            "autoupdateInterval": None,
            "maxConcurrentRequests": None,
            "silentLoading": False,
            "dependentSelectors": True,
            "hideTabs": False,
            "hideDashTitle": False,
            "expandTOC": False,
        },
    }


class DataLensAdapter:
    def __init__(
        self,
        client: DataLensClient,
        dwh: DWHConfig,
        model: SemanticModel,
        workbook_id: str,
    ) -> None:
        self._client = client
        self._dwh = dwh
        self._model = model
        self._workbook_id = workbook_id
        self._connection_id: str | None = None
        # dataset id -> (name, fields_by_alias) for binding charts to dataset fields
        self._datasets: dict[str, tuple[str, dict[str, dict]]] = {}

    # --- BIAdapter ----------------------------------------------------------

    def healthcheck(self) -> AdapterHealth:
        # `/ping` only proves the UI process is up; it answers without a valid session,
        # gateway forwarding, or workbook access (F6). Confirm the full happy-path that
        # `build` relies on with one cheap *authorized* call — an empty `getWorkbookEntries`
        # scoped to the target workbook (also exercises auth cookie + gateway forward +
        # workbook reachability), mirroring how compile_and_build gates on healthcheck().ok.
        if not self._client.health():
            return AdapterHealth(ok=False, message="ping failed (DataLens UI unreachable)")
        try:
            self._client.gateway(
                "us",
                "getWorkbookEntries",
                {"workbookId": self._workbook_id, "scope": "connection"},
            )
        except DataLensAPIError as exc:
            return AdapterHealth(ok=False, message=f"authorized check failed: {exc}")
        return AdapterHealth(ok=True, message="")

    def ensure_database(self, dwh: DWHConfig | None = None) -> DatabaseRef:
        dwh = dwh or self._dwh
        name = connection_name(dwh.engine)
        # idempotent by name (mirror SupersetAdapter): DataLens enforces unique entry keys
        # per workbook, and the connection name is deterministic per engine, so a second
        # build would otherwise fail with US "entity already exists". Reuse the existing
        # connection if present.
        existing = self._find_entry_id("connection", name)
        if existing is not None:
            self._connection_id = existing
            logger.info("datalens connection reused: id=%s", existing)
            return DatabaseRef(id=existing, name=name)
        body = build_connection_payload(dwh, name=name, workbook_id=self._workbook_id)
        created = self._client.gateway("bi", "createConnection", body)
        self._connection_id = created["id"]
        logger.info("datalens connection created: id=%s", self._connection_id)
        return DatabaseRef(id=self._connection_id, name=name)

    def _find_entry_id(self, scope: str, name: str) -> str | None:
        """Encoded entryId of a workbook entry with this exact name and scope, or None.

        Uses `us/getWorkbookEntries` (reversal §5.4): the server `filters.name` narrows the
        page (live-verified to work for Cyrillic names too), and the exact name is matched
        on the `key` tail (`<id>/<name>`) case-folded — the name's case is preserved in the
        key, so a substring filter alone is not authoritative.
        """
        res = self._client.gateway(
            "us",
            "getWorkbookEntries",
            {"workbookId": self._workbook_id, "scope": scope, "filters": {"name": name}},
        )
        target = name.casefold()
        for entry in res.get("entries", []):
            key = entry.get("key") or ""
            if key.rsplit("/", 1)[-1].casefold() == target:
                return entry["entryId"]
        return None

    def _delete_if_exists(self, scope: str, name: str) -> None:
        """Delete an existing workbook entry with this exact name+scope so a re-build can
        create it fresh (idempotency, reversal §5.5).

        DataLens (US) enforces unique entry keys per workbook+scope, so a second create of
        the same name 400s with "entity already exists" — and unlike Superset (where chart
        and dashboard names may duplicate, only the dataset is reused) the dataset, chart
        AND dashboard all collide. delete+create is used uniformly rather than update-in-
        place: the only generic delete, `mix/deleteEntry`, is exposed via the cookie gateway
        and routes per scope (dataset -> bi.deleteDataset, others -> US delete), whereas the
        private `us/_deleteUSEntry` is not reachable through the gateway (404) and the
        update actions differ per service. The fresh entry fully reflects the current spec
        (refreshed SQL / chart shared / dashboard layout); its id changes, which is
        invisible — datasets and charts are internal, and the dashboard URL already changes
        per build (as in Superset). Live-verified 2026-06-14 (dataset + widget delete).

        Used two ways: (1) the standalone idempotent create path of the public methods
        (ensure_dataset/create_chart/assemble_dashboard called directly, e.g. contract tests)
        — there delete precedes create on the canonical name, NOT atomic; (2) the
        `_promote_to_canonical` reconcile of `build`, which deletes the stale canonical entry
        right before renaming the freshly-built temp entry onto it. `build` itself IS atomic
        on rebuild (F2): it creates under temp names first and never deletes a canonical entry
        until the whole build has succeeded — see `build`/`_promote_to_canonical`.
        """
        existing = self._find_entry_id(scope, name)
        if existing is not None:
            self._client.gateway("mix", "deleteEntry", {"entryId": existing, "scope": scope})
            logger.info(
                "datalens %s stale entry deleted (replaced): id=%s name=%s",
                scope,
                existing,
                name,
            )

    def ensure_dataset(
        self,
        query: ChartQuery,
        name: str | None = None,
        *,
        apply_limit: bool = True,
        measure_scale: tuple[float, list[str]] | None = None,
    ) -> DatasetRef:
        if self._connection_id is None:
            self.ensure_database()
        connection_id = self._connection_id
        assert connection_id is not None  # ensure_database sets it
        ds_name = name or dataset_name(query.table, query.table)
        self._delete_if_exists("dataset", ds_name)  # idempotency: rebuild replaces in place
        payload = build_dataset_payload(
            query,
            self._model,
            workbook_id=self._workbook_id,
            connection_id=connection_id,
            name=ds_name,
            apply_limit=apply_limit,
            measure_scale=measure_scale,
        )
        created = self._client.gateway("bi", "createDataset", payload)
        ds_id = created["id"]
        # createDataset preserves our supplied guids/avatar_id, so the chart binds to the
        # payload's result_schema fields directly (live-verified 2026-06-14). Key by `source`
        # (the bare SQL alias) — the field `title` now carries the human display name, so keying
        # by title would no longer match chart_config's alias lookups.
        fields = {f["source"]: f for f in payload["dataset"]["result_schema"]}
        self._datasets[ds_id] = (ds_name, fields)
        logger.info("datalens dataset created: id=%s name=%s", ds_id, ds_name)
        return DatasetRef(id=ds_id, name=ds_name)

    def create_chart(
        self,
        chart: ChartSpec,
        ds: DatasetRef,
        *,
        name: str | None = None,
        kpi_unit: str | None = None,
        kpi_precision: int = 0,
        axis_unit: str | None = None,
    ) -> ChartRef:
        ds_name, fields = self._datasets[str(ds.id)]
        horizontal = self._model is not None and is_horizontal_bar(chart, self._model)
        shared = build_chart_shared(
            chart,
            str(ds.id),
            ds_name,
            fields,
            horizontal=horizontal,
            kpi_unit=kpi_unit,
            kpi_precision=kpi_precision,
            axis_unit=axis_unit,
        )
        if chart.viz in DEGRADED:
            logger.warning("chart %r: %s", chart.title, DEGRADED[chart.viz])
        # charts-engine validates the entry-name charset. `name` (a pre-sanitized temp name)
        # is passed by the atomic build path; standalone callers create under the canonical.
        name = name if name is not None else safe_entry_name(chart.title)
        self._delete_if_exists("widget", name)  # idempotency: rebuild replaces in place
        created = self._client.post(
            "/api/charts/v1/charts",
            {
                "data": shared,
                "template": "datalens",
                "workbookId": self._workbook_id,
                "name": name,
            },
        )
        chart_id = created["entryId"]
        logger.info("datalens chart created: id=%s viz=%s", chart_id, chart.viz.value)
        return ChartRef(id=chart_id, name=name)

    def assemble_dashboard(
        self,
        spec: DashboardSpec,
        charts: list[ChartRef],
        placements: list[Placement] | None = None,
        *,
        name: str | None = None,
    ) -> DashboardRef:
        # Mirror SupersetAdapter.assemble_dashboard: fail early and clearly on a chart/spec
        # mismatch (F5). build_dashboard_data also guards this, but only for widget_ids; a
        # direct call with mis-synced placements/charts would otherwise fail later in
        # build_selectors' strict zip with a worse diagnostic.
        if len(charts) != len(spec.charts):
            raise ValueError(f"got {len(charts)} chart refs for {len(spec.charts)} spec charts")
        # `mix/createDashboardV1` injects schemeVersion, gathers chart links and runs
        # Dash.validateData server-side, then us._createEntry (scope=Dash). The `data` blob
        # must omit schemeVersion (reversal §5.3); workbook entries take workbookId+name.
        # With `placements` (chart -> widget id -> dataset id) and spec.filters, compile
        # dashboard selectors; without them the dashboard is built filterless.
        controls: list[dict] = []
        alias_groups: list[list[str]] = []
        if placements and spec.filters:
            fields_by_dataset = {
                ds_id: self._datasets[ds_id][1]
                for _, _, ds_id in placements
                if ds_id in self._datasets
            }
            controls, alias_groups, applied = build_selectors(
                spec, placements, fields_by_dataset, self._model
            )
            for filter_, scoped, excluded in applied:
                logger.info(
                    "datalens selector %s -> charts %s (excluded %s)",
                    filter_.column,
                    scoped,
                    excluded,
                )
        data = build_dashboard_data(spec, [str(c.id) for c in charts], controls, alias_groups)
        # US validates the dashboard entry-name charset. `name` (pre-sanitized temp name) is
        # passed by the atomic build path; standalone callers create under the canonical.
        name = name if name is not None else safe_entry_name(spec.title)
        self._delete_if_exists("dash", name)  # idempotency: rebuild replaces in place
        created = self._client.gateway(
            "mix",
            "createDashboardV1",
            {
                "entry": {
                    "data": data,
                    "meta": None,
                    "workbookId": self._workbook_id,
                    "name": name,
                },
                "mode": "publish",
            },
        )
        dash_id = created["entry"]["entryId"]
        url = f"/{dash_id}"  # DataLens serves entries at GET /:entryId
        logger.info("datalens dashboard created: id=%s url=%s charts=%d", dash_id, url, len(charts))
        return DashboardRef(id=dash_id, title=spec.title, url=url)

    def _promote_to_canonical(self, entries: list[tuple[str, str, str]]) -> None:
        """Atomic-rebuild reconcile (F2): the whole build already succeeded under temp names,
        so the previous working entries are still intact. Promote each freshly-built temp
        entry to its canonical name — delete the stale canonical entry (the old working
        version) and rename the temp one onto it via `us/renameEntry {entryId, name}`
        (live-verified 2026-06-14; the direct `/v1/entries/:id/rename` REST route is not
        exposed through the UI gateway, the `us` gateway action is).

        The entryId is unchanged by a rename, so the dashboard's chart links (by entryId) and
        its URL (`/{entryId}`, already returned by build) stay valid.

        NOT atomic *across* the entries (only across the build-vs-promote boundary): this is a
        sequential per-entry `delete(stale canonical) -> rename(temp -> canonical)` loop. A
        crash mid-loop — between an entry's delete and its rename, or after promoting some
        entries but not the rest — leaves a partially-promoted state: the old dashboard may
        then reference an already-deleted dataset id. The window is far smaller than a full
        rebuild (two quick US calls per entry, never the charts-engine) and self-heals on the
        next build (the missing canonical is re-created, the leftover `__wip` swept by the
        temp-name delete-then-create), but it is not zero. Phase-4 F2 audit P3: a fully atomic
        promote would need either a server-side multi-entry transaction (US has none) or an
        all-or-nothing rename ordering — backlogged."""
        for scope, canonical, temp_id in entries:
            self._delete_if_exists(scope, canonical)
            self._client.gateway("us", "renameEntry", {"entryId": temp_id, "name": canonical})
            logger.info(
                "datalens %s promoted to canonical: name=%s id=%s", scope, canonical, temp_id
            )

    # --- N2: RU magnitude units on a large KPI headline / value axis ---------

    def _measure_currency(self, measure: Measure, table: str) -> str:
        """ "₽" when the model marks the measure's column as money, else "" (mirrors
        SupersetAdapter._measure_currency)."""
        tbl = self._model.table(table)
        col = tbl.column(measure.column.rpartition(".")[2]) if tbl else None
        text = f"{col.description if col else ''} {measure.label or ''}".lower()
        return "₽" if any(m in text for m in _MONEY_MARKERS) else ""

    def _measure_magnitude(self, measure: Measure, ds: DatasetRef, *, agg: str) -> float | None:
        """The measure's peak aggregated value over the chart's dataset, measured live via an
        inline (unsaved) /api/run of a metric probe config — the DataLens analogue of
        SupersetAdapter._measure_magnitude (`/api/v1/chart/data`). `agg` re-aggregates the
        pre-grouped subselect rows: "sum" for a one-row KPI dataset (the identity = the
        scalar), "max" for a grouped line/bar dataset (= the tallest series point).
        Best-effort: any failure returns None and the chart keeps the default format — a
        display nicety must never break a build."""
        try:
            ds_name, fields = self._datasets[str(ds.id)]
            probe = ChartSpec(
                id="auto_bi_magnitude_probe",
                title="auto_bi magnitude probe",
                viz=Viz.BIG_NUMBER,
                query=ChartQuery(table="probe", measures=[measure]),
            )
            shared = build_chart_shared(probe, str(ds.id), ds_name, fields)
            shared["visualization"]["placeholders"][0]["items"][0]["aggregation"] = agg
            run = self._client.post(
                "/api/run",
                {
                    "config": {
                        "data": {"shared": json.dumps(shared, ensure_ascii=False)},
                        "meta": {"stype": _METRIC_STYPE},
                    },
                    "workbookId": self._workbook_id,
                },
            )
            # metric run data: [{"content": {"current": {"value": <scalar>, ...}}, ...}]
            value = run["data"][0]["content"]["current"]["value"]
            return abs(float(value))
        except Exception:
            logger.warning("magnitude probe failed; keeping default format", exc_info=True)
            return None

    def _ru_scale(
        self, chart: ChartSpec, ds: DatasetRef, *, agg: str
    ) -> tuple[float, str, float] | None:
        """(divisor, RU unit line, scaled magnitude) for the chart's primary measure, measured
        live, or None to keep the default format. Same tiers/rules as the Superset adapter
        (`_ru_scale`): only compact (additive, non-percent) measures with a magnitude >= 1e3
        scale; the unit line is "млрд ₽" for money, just "млрд" for a count. The scaled
        magnitude (1 ≤ x < 1000) drives the headline precision in the 1–10 band (L-1)."""
        measure = chart.query.measures[0]
        if not is_compact_number(measure):
            return None
        magnitude = self._measure_magnitude(measure, ds, agg=agg)
        if magnitude is None:
            return None
        divisor, unit = ru_kpi_scale(magnitude)
        if divisor <= 1:
            return None
        currency = self._measure_currency(measure, chart.query.table)
        return divisor, f"{unit} {currency}".strip(), magnitude / divisor

    def _kpi_ru_scale(self, chart: ChartSpec, ds: DatasetRef) -> tuple[float, str, float] | None:
        """(divisor, RU unit line, scaled magnitude) for a large additive big_number headline
        ("236 млрд ₽" instead of the SI "236B"), or None. One row -> "sum" identity."""
        if chart.viz != Viz.BIG_NUMBER or not chart.query.measures:
            return None
        return self._ru_scale(chart, ds, agg="sum")

    def _axis_ru_scale(self, chart: ChartSpec, ds: DatasetRef) -> tuple[float, str, float] | None:
        """(divisor, RU unit line, scaled magnitude) for a large-magnitude line/bar/area VALUE
        axis, or None to keep the SI default. Mirrors SupersetAdapter._axis_scale: the axis
        unit tier comes from the tallest series point ("max" over the grouped subselect rows),
        the scaled metric reads "15" against an axis titled "млрд ₽" instead of "15B".

        Single-measure charts only (mirrors the Superset guard): one measure's divisor would
        divide every compact co-measure, rendering it in the wrong units."""
        if chart.viz not in _AXIS_SCALE_VIZ or len(chart.query.measures) != 1:
            return None
        return self._ru_scale(chart, ds, agg="max")

    # --- happy path ----------------------------------------------------------

    def build(self, spec: DashboardSpec) -> DashboardRef:
        """Full compile: connection -> per-chart datasets -> charts -> dashboard entry
        (mirrors SupersetAdapter.build, ARCHITECTURE §3.5).

        Charts in a dashboard selector's scope drop their SQL top-N LIMIT so the selector
        re-ranks after filtering (computable from the spec via participating_chart_ids).

        Atomic at the build/promote boundary (F2): every entry is first created under a temp
        `__wip` name, so the existing canonical entries (the last working dashboard) are never
        touched until the whole build has succeeded. Only then does `_promote_to_canonical`
        delete each stale canonical entry and rename its temp replacement onto it. So a
        mid-build failure (e.g. a transient charts-engine 5xx) propagates with the previous
        working version fully intact — the exception bubbles up (the session is marked failed,
        then retried), and build never returns or leaves a half-built dashboard. The promote
        loop itself is NOT atomic across the three entries (see `_promote_to_canonical`): a
        crash inside it leaves a narrowed inconsistency window that self-heals on the next
        build. Writes only to the dedicated Auto_BI workbook (F3). A failed build cleans up
        the temp `__wip` entries it created (`_cleanup_wip` in the except branch), so they do
        not linger as orphans even if the next attempt's title/chart set differs (F2 audit
        P3)."""
        self.ensure_database()
        in_filter_scope = participating_chart_ids(spec, self._model)
        placements: list[Placement] = []
        refs: list[ChartRef] = []
        # (scope, canonical_name, temp_entry_id) promoted only after a fully successful build
        to_promote: list[tuple[str, str, str]] = []
        # (scope, wip_name) recorded before each create so a failed build can sweep its own
        # temp entries (F2 audit P3); recorded pre-create is safe — cleanup is idempotent.
        wip_created: list[tuple[str, str]] = []
        try:
            for chart in spec.charts:
                ds_canonical = dataset_name(spec.title, chart.id)
                wip_created.append(("dataset", _wip_name(ds_canonical)))
                apply_limit = chart.id not in in_filter_scope
                ds = self.ensure_dataset(
                    chart.query,
                    name=_wip_name(ds_canonical),
                    apply_limit=apply_limit,
                )
                # N2: RU magnitude units for large additive measures — a KPI reads
                # "236 млрд ₽" (unit glued to the figure), a line/bar value axis reads "15"
                # ticks against a "млрд ₽" axis title, instead of the SI "236B"/"15B".
                # Measure the magnitude live, then rebuild the dataset (same wip name) with
                # the compact measures scaled.
                kpi_unit: str | None = None
                kpi_precision = 0
                axis_unit: str | None = None
                scale = self._kpi_ru_scale(chart, ds)
                if scale is not None:
                    kpi_unit = scale[1]
                    # L-1: in the 1–10 band a whole-number headline loses up to a third of
                    # the figure ("1,5 млрд" -> "2 млрд") -> keep one decimal there
                    kpi_precision = 1 if scale[2] < 10 else 0
                else:
                    scale = self._axis_ru_scale(chart, ds)
                    if scale is not None:
                        axis_unit = scale[1]
                if scale is not None:
                    # a scaled chart is single-measure by construction (big_number by
                    # validation, the axis path by the _axis_ru_scale guard), so this scales
                    # exactly the measure the divisor was tiered from; a percent/ratio
                    # measure keeps its raw 0..1 value
                    aliases = [
                        measure_alias(m) for m in chart.query.measures if is_compact_number(m)
                    ]
                    ds = self.ensure_dataset(
                        chart.query,
                        name=_wip_name(ds_canonical),
                        apply_limit=apply_limit,
                        measure_scale=(scale[0], aliases),
                    )
                to_promote.append(("dataset", ds_canonical, str(ds.id)))
                chart_canonical = safe_entry_name(chart.title)
                wip_created.append(("widget", _wip_name(chart_canonical)))
                ref = self.create_chart(
                    chart,
                    ds,
                    name=_wip_name(chart_canonical),
                    kpi_unit=kpi_unit,
                    kpi_precision=kpi_precision,
                    axis_unit=axis_unit,
                )
                to_promote.append(("widget", chart_canonical, str(ref.id)))
                refs.append(ref)
                placements.append((chart, str(ref.id), str(ds.id)))
            dash_canonical = safe_entry_name(spec.title)
            wip_created.append(("dash", _wip_name(dash_canonical)))
            dash = self.assemble_dashboard(
                spec, refs, placements=placements, name=_wip_name(dash_canonical)
            )
            to_promote.append(("dash", dash_canonical, str(dash.id)))
            # Build fully succeeded under temp names -> promote them to canonical (delete stale
            # + rename). Reached only on success, so the old version survives any earlier failure.
            self._promote_to_canonical(to_promote)
            return dash
        except Exception:
            self._cleanup_wip(wip_created)  # don't leave temp entries behind on failure
            raise

    def _cleanup_wip(self, wip_entries: list[tuple[str, str]]) -> None:
        """Best-effort removal of temp `__wip` entries created by a failed build (F2 audit P3).
        Promoted entries were already renamed off their `__wip` name, so the lookup misses them;
        only the not-yet-promoted temps of this build are deleted. Errors are swallowed so a
        cleanup failure never masks the original build exception that is about to re-raise."""
        for scope, wip_name in wip_entries:
            try:
                self._delete_if_exists(scope, wip_name)
            except Exception:  # cleanup must never mask the original build exception
                logger.warning("datalens __wip cleanup failed for %s %s", scope, wip_name)
