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
import logging
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
from auto_bi.adapters.superset.native_filters import participating_chart_ids
from auto_bi.ir.spec import ChartQuery, ChartSpec, DashboardFilter, DashboardSpec, column_alias
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


# Atomic-rebuild temp-name suffix (F2): a fully-built entry lives under <canonical>__wip
# until the whole build succeeds, then is renamed to its canonical name. `_` is allowed
# anywhere in a DataLens entry name (charset, see safe_entry_name), so appending it keeps a
# sanitized canonical name valid.
_WIP_SUFFIX = "__wip"


def _wip_name(canonical: str) -> str:
    return f"{canonical}{_WIP_SUFFIX}"


# Grid (24 cols): selectors in a top row (6 wide, 2 tall), charts below in 2 columns.
_GRID_W = 12
_GRID_H = 4
_CTRL_W = 6
_CTRL_H = 2
_CTRLS_PER_ROW = 4


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
        source = {
            "datasetId": first_ds,
            "datasetFieldId": field0["guid"],
            "fieldType": field0["data_type"],
            "datasetFieldType": field0["type"],
            "showTitle": True,
            "elementType": "date" if is_time else "select",
            "defaultValue": "",
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
                "defaults": {field0["guid"]: ""},
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

    for j, control in enumerate(controls):
        items.append(control)
        layout.append(
            {
                "i": control["id"],
                "x": (j % _CTRLS_PER_ROW) * _CTRL_W,
                "y": (j // _CTRLS_PER_ROW) * _CTRL_H,
                "w": _CTRL_W,
                "h": _CTRL_H,
            }
        )
    y0 = -(-len(controls) // _CTRLS_PER_ROW) * _CTRL_H  # rows of controls, ceil-divided

    for i, (chart, wid) in enumerate(zip(spec.charts, widget_ids, strict=True)):
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
        layout.append(
            {
                "i": item_id,
                "x": (i % 2) * _GRID_W,
                "y": y0 + (i // 2) * _GRID_H,
                "w": _GRID_W,
                "h": _GRID_H,
            }
        )
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
        self, query: ChartQuery, name: str | None = None, *, apply_limit: bool = True
    ) -> DatasetRef:
        if self._connection_id is None:
            self.ensure_database()
        ds_name = name or dataset_name(query.table, query.table)
        self._delete_if_exists("dataset", ds_name)  # idempotency: rebuild replaces in place
        payload = build_dataset_payload(
            query,
            self._model,
            workbook_id=self._workbook_id,
            connection_id=self._connection_id,
            name=ds_name,
            apply_limit=apply_limit,
        )
        created = self._client.gateway("bi", "createDataset", payload)
        ds_id = created["id"]
        # createDataset preserves our supplied guids/avatar_id, so the chart binds to the
        # payload's result_schema fields directly (live-verified 2026-06-14)
        fields = {f["title"]: f for f in payload["dataset"]["result_schema"]}
        self._datasets[ds_id] = (ds_name, fields)
        logger.info("datalens dataset created: id=%s name=%s", ds_id, ds_name)
        return DatasetRef(id=ds_id, name=ds_name)

    def create_chart(
        self, chart: ChartSpec, ds: DatasetRef, *, name: str | None = None
    ) -> ChartRef:
        ds_name, fields = self._datasets[str(ds.id)]
        shared = build_chart_shared(chart, str(ds.id), ds_name, fields)
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
        its URL (`/{entryId}`, already returned by build) stay valid. Per entry this is
        delete-then-rename — two quick US calls, never the charts-engine — so the residual
        non-canonical window is tiny compared to a full rebuild, and a crash between the two
        self-heals on the next build (the missing canonical is simply re-created)."""
        for scope, canonical, temp_id in entries:
            self._delete_if_exists(scope, canonical)
            self._client.gateway("us", "renameEntry", {"entryId": temp_id, "name": canonical})
            logger.info(
                "datalens %s promoted to canonical: name=%s id=%s", scope, canonical, temp_id
            )

    # --- happy path ----------------------------------------------------------

    def build(self, spec: DashboardSpec) -> DashboardRef:
        """Full compile: connection -> per-chart datasets -> charts -> dashboard entry
        (mirrors SupersetAdapter.build, ARCHITECTURE §3.5).

        Charts in a dashboard selector's scope drop their SQL top-N LIMIT so the selector
        re-ranks after filtering (computable from the spec via participating_chart_ids).

        Atomic on rebuild (F2): every entry is first created under a temp `__wip` name, so the
        existing canonical entries (the last working dashboard) are never touched until the
        whole build has succeeded. Only then does `_promote_to_canonical` delete each stale
        canonical entry and rename its temp replacement onto it. A mid-build failure (e.g. a
        transient charts-engine 5xx) therefore propagates with the previous working version
        fully intact — the exception bubbles up (the session is marked failed, then retried),
        and build never returns or leaves a half-built dashboard. Writes only to the dedicated
        Auto_BI workbook (F3). A re-build's leftover temp entries (from a prior failure) are
        cleaned by the temp-name delete-then-create on the next build."""
        self.ensure_database()
        in_filter_scope = participating_chart_ids(spec, self._model)
        placements: list[Placement] = []
        refs: list[ChartRef] = []
        # (scope, canonical_name, temp_entry_id) promoted only after a fully successful build
        to_promote: list[tuple[str, str, str]] = []
        for chart in spec.charts:
            ds_canonical = dataset_name(spec.title, chart.id)
            ds = self.ensure_dataset(
                chart.query,
                name=_wip_name(ds_canonical),
                apply_limit=chart.id not in in_filter_scope,
            )
            to_promote.append(("dataset", ds_canonical, str(ds.id)))
            chart_canonical = safe_entry_name(chart.title)
            ref = self.create_chart(chart, ds, name=_wip_name(chart_canonical))
            to_promote.append(("widget", chart_canonical, str(ref.id)))
            refs.append(ref)
            placements.append((chart, str(ref.id), str(ds.id)))
        dash_canonical = safe_entry_name(spec.title)
        dash = self.assemble_dashboard(
            spec, refs, placements=placements, name=_wip_name(dash_canonical)
        )
        to_promote.append(("dash", dash_canonical, str(dash.id)))
        # Build fully succeeded under temp names -> promote them to canonical (delete stale +
        # rename). Reached only on success, so the old version survives any earlier failure.
        self._promote_to_canonical(to_promote)
        return dash
