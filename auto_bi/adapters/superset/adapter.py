"""SupersetAdapter: deterministic IR compiler -> Superset REST API (v1 target).

Flow per ARCHITECTURE §3.5: ensure_database (connection inside BI, idempotent by
name) -> ensure_dataset (virtual dataset per chart with our validated SQL,
idempotent by table_name) -> create_chart (form_data template) ->
assemble_dashboard (position_json grid + chart linkage).
"""

from __future__ import annotations

import json
import logging
import re

from auto_bi.adapters.artifacts import BuildArtifact, dataset_table_name
from auto_bi.adapters.base import (
    AdapterHealth,
    ChartRef,
    DashboardRef,
    DatabaseRef,
    DatasetRef,
    DWHConfig,
)
from auto_bi.adapters.superset.client import SupersetAPIError, SupersetClient, rison_eq_filter
from auto_bi.adapters.superset.form_data import (
    VIZ_TYPE,
    _adhoc_metric,
    build_form_data,
    build_position_json,
    ru_kpi_scale,
)
from auto_bi.adapters.superset.native_filters import (
    build_native_filter_configuration,
    participating_chart_ids,
)
from auto_bi.agent.normalize import is_horizontal_bar
from auto_bi.agent.sqlgen import generate_chart_sql
from auto_bi.ir.spec import (
    ChartQuery,
    ChartSpec,
    DashboardSpec,
    Measure,
    Viz,
    column_alias,
    is_compact_number,
    measure_alias,
)
from auto_bi.semantic.model import ColumnRole, SemanticModel

logger = logging.getLogger(__name__)

DATABASE_NAME = "Auto_BI ClickHouse"

# Dashboard-level CSS: KPI tiles center as one visual row (same baseline both axes).
# big_number_total renders the value left/top-anchored and, with no unit line, drifts
# vertically relative to its neighbours; there is no form_data knob for alignment, so
# this is the deterministic native-format seam (invariant 1), same as position_json.
# Verified against the pinned Superset 4.1 DOM (.superset-legacy-chart-big-number).
KPI_CENTER_CSS = """
.superset-legacy-chart-big-number {
  display: flex; flex-direction: column;
  align-items: center; justify-content: center; text-align: center;
}
.superset-legacy-chart-big-number .header-line { justify-content: center; }
"""

# a measure is money (KPI unit gets a "₽") when its model description says so — kept as
# markers, not a hard-coded currency, so a count/qty KPI never gets a spurious ruble sign.
_MONEY_MARKERS = ("руб", "₽", "rub")

# a measure's human legend name is the short form of its column description: text up to the
# first of these separators ("Выручка, руб" -> "Выручка"), mirroring autospec._short.
_LABEL_SEPS = (",", "(", ":", " —", " -")

# cartesian charts whose value axis gets RU magnitude units (scaled metric + unit on the axis
# title) instead of the d3 SI "15G" — big_number scales its headline separately (_kpi_scale).
_AXIS_SCALE_VIZ = (Viz.LINE, Viz.BAR, Viz.STACKED_BAR, Viz.AREA)

# kind -> DELETE endpoint for the ownership-based live-cleanup. `database` is DELIBERATELY
# absent: the connection is shared across builds (SHARED_BI_KINDS) and deleting it live was
# proven to break every dashboard on it — the ledger selection already excludes it, and
# this map is the adapter-level second belt.
_DELETE_PATHS = {
    "chart": "/api/v1/chart/",
    "dashboard": "/api/v1/dashboard/",
    "dataset": "/api/v1/dataset/",
}


def _slug(text: str, max_len: int = 40) -> str:
    return re.sub(r"\W+", "_", text.lower()).strip("_")[:max_len] or "dataset"


def _int_id(ref_id: int | str) -> int:
    """Superset entity ids are ints; refs type them `int | str` only to share the BIAdapter
    Protocol with DataLens (string entry ids, see base.py). Narrow back at the Superset
    boundary where the REST API and form_data/position helpers genuinely require ints."""
    return int(ref_id)


def _dataset_name(title: str, chart_id: str, namespace: str = "") -> str:
    """Readable, collision-free dataset name (audit P0-2).

    Slugs can truncate-collide, so a short hash of chart_id (+ optional build/session
    namespace) keeps charts and independent sessions on distinct datasets. Without a
    namespace two sessions with the same title/chart ids would PUT the same virtual
    dataset and silently rewrite each other's SQL — see ARCHITECTURE §artifact-namespace.
    """
    return dataset_table_name(title, chart_id, namespace)


class SupersetAdapter:
    def __init__(
        self, client: SupersetClient, dwh: DWHConfig, model: SemanticModel | None = None
    ) -> None:
        self._client = client
        self._dwh = dwh
        # `model` (constructor-injected, mirrors DataLensAdapter) lets build() scope native
        # filters by column role/grain; without it filters degrade to the documented warning.
        self._model = model
        self._database: DatabaseRef | None = None
        # P0-2: set via set_artifact_namespace() before build(); empty = legacy single-user.
        self._artifact_namespace: str = ""
        # Ownership ledger (P0-2 criterion 4): build() accumulates the BI entities it creates
        # here; the orchestrator drains them after a successful build via drain_build_artifacts.
        self._build_artifacts: list[BuildArtifact] = []

    # --- BIAdapter ----------------------------------------------------------

    def set_artifact_namespace(self, namespace: str) -> None:
        """P0-2: pin this build's technical names to a session/build namespace.

        Not part of the BIAdapter Protocol (optional concrete helper); the pipeline
        calls it when present so two sessions never share dataset table_names.
        """
        self._artifact_namespace = (namespace or "").strip()

    def drain_build_artifacts(self) -> list[BuildArtifact]:
        """Return and clear the BI artifacts the last build() created (P0-2 criterion 4).

        Concrete helper, NOT part of the BIAdapter Protocol (like set_artifact_namespace):
        the orchestrator (compile_and_build) drains after a successful build() and records the
        rows in Store.bi_artifacts (the ownership ledger). Draining clears the buffer so a
        reused adapter never double-reports. See docs/ARCHITECTURE §3.5 (artifact identity)."""
        drained = list(self._build_artifacts)
        self._build_artifacts = []
        return drained

    def delete_artifact(self, kind: str, native_id: str) -> None:
        """Delete one owned BI entity by native id (ownership ledger live-cleanup).

        Concrete helper, NOT part of the BIAdapter Protocol (like drain_build_artifacts).
        Returns normally when the entity was deleted OR was already gone (404 — e.g. removed
        by hand between builds); raises on any other failure so the caller keeps the ledger
        row 'live' and retries on a later prune. Never accepts a shared kind.
        """
        path = _DELETE_PATHS.get(kind)
        if path is None:
            raise ValueError(f"refusing to delete shared/unknown BI artifact kind: {kind!r}")
        try:
            self._client.delete(f"{path}{_int_id(native_id)}")
        except SupersetAPIError as exc:
            if exc.status_code == 404:
                logger.info("superset %s %s already gone (404)", kind, native_id)
                return
            raise
        logger.info("superset %s %s deleted (live-cleanup)", kind, native_id)

    def healthcheck(self) -> AdapterHealth:
        ok = self._client.health()
        return AdapterHealth(ok=ok, message="" if ok else "GET /health failed")

    def ensure_database(self, dwh: DWHConfig | None = None) -> DatabaseRef:
        dwh = dwh or self._dwh
        existing = self._client.get(
            "/api/v1/database/", params={"q": rison_eq_filter("database_name", DATABASE_NAME)}
        )
        for item in existing.get("result", []):
            self._database = DatabaseRef(id=item["id"], name=DATABASE_NAME)
            logger.info("database already in superset: id=%s", item["id"])
            return self._database

        uri = f"clickhousedb://{dwh.user}:{dwh.password}@{dwh.host}:{dwh.port}/{dwh.database}"
        created = self._client.post(
            "/api/v1/database/",
            json={"database_name": DATABASE_NAME, "sqlalchemy_uri": uri},
        )
        self._database = DatabaseRef(id=created["id"], name=DATABASE_NAME)
        logger.info("database created in superset: id=%s", created["id"])
        return self._database

    def ensure_dataset(
        self, query: ChartQuery, name: str | None = None, *, apply_limit: bool = True
    ) -> DatasetRef:
        db = self._database or self.ensure_database()
        sql = generate_chart_sql(query, apply_limit=apply_limit)
        table_name = name or f"auto_bi__{_slug(query.table)}"

        existing = self._client.get(
            "/api/v1/dataset/", params={"q": rison_eq_filter("table_name", table_name)}
        )
        for item in existing.get("result", []):
            self._client.put(f"/api/v1/dataset/{item['id']}", json={"sql": sql})
            logger.info("dataset %s updated: id=%s", table_name, item["id"])
            return DatasetRef(id=item["id"], name=table_name)

        created = self._client.post(
            "/api/v1/dataset/",
            json={
                "database": db.id,
                "table_name": table_name,
                "sql": sql,
                "schema": self._dwh.database,
            },
        )
        logger.info("dataset %s created: id=%s", table_name, created["id"])
        return DatasetRef(id=created["id"], name=table_name)

    def _measure_magnitude(self, ds: DatasetRef, measure: Measure) -> float | None:
        """The measure's peak aggregated value, measured live so its RU magnitude unit
        (млрд/млн/тыс) can be chosen: for a big_number the dataset is one row (MAX = the scalar),
        for a line/bar it is grouped (MAX = the tallest series point). Best-effort: any failure
        returns None and the chart falls back to the default format — a display nicety must never
        break a build."""
        try:
            result = self._client.post(
                "/api/v1/chart/data",
                json={
                    "datasource": {"id": _int_id(ds.id), "type": "table"},
                    "force": True,
                    "queries": [
                        {
                            "metrics": [_adhoc_metric(measure, "kpimag", 0, agg="MAX")],
                            "row_limit": 1,
                        }
                    ],
                    "result_format": "json",
                    "result_type": "full",
                },
            )
            rows = result["result"][0]["data"]
            if not rows:
                return None
            value = rows[0].get(measure_alias(measure))
            return float(value) if value is not None else None
        except (SupersetAPIError, KeyError, IndexError, TypeError, ValueError):
            return None

    def _human_label(self, measure: Measure, table: str) -> str | None:
        """Human display name for a measure's legend/tooltip: its explicit label, else the short
        form of the model column's description ("Выручка, руб" -> "Выручка"), else None (the
        adapter then falls back to the raw alias). Autospec deliberately leaves measure.label
        empty (technical SQL alias) — this recovers the human name for display only."""
        if measure.label:
            return measure.label
        if self._model is None:
            return None
        tbl = self._model.table(table)
        col = tbl.column(measure.column.rpartition(".")[2]) if tbl else None
        desc = col.description.strip() if col and col.description else ""
        if not desc:
            return None
        for sep in _LABEL_SEPS:
            idx = desc.find(sep)
            if idx > 0:
                desc = desc[:idx]
        return desc.strip() or None

    def _metric_labels(self, chart: ChartSpec) -> dict[str, str]:
        """alias -> human legend name for each measure that resolves one (see `_human_label`)."""
        out: dict[str, str] = {}
        for m in chart.query.measures:
            human = self._human_label(m, chart.query.table)
            if human:
                out[measure_alias(m)] = human
        return out

    def _measure_currency(self, measure: Measure, table: str) -> str:
        """'₽' when the measure reads as money in the model (its column description mentions
        rubles), else '' — so a count/qty KPI ('236 млн') gets no spurious currency. No model
        (bare protocol) => no currency."""
        if self._model is None:
            return ""
        tbl = self._model.table(table)
        col = tbl.column(measure.column.rpartition(".")[2]) if tbl else None
        text = f"{col.description if col else ''} {measure.label or ''}".lower()
        return "₽" if any(m in text for m in _MONEY_MARKERS) else ""

    def _ru_scale(
        self, measure: Measure, table: str, ds: DatasetRef
    ) -> tuple[float, str, float] | None:
        """(divisor, RU unit line, scaled magnitude) for a large compact measure, measured
        live, or None to keep the default format. Only additive aggregates (is_compact_number)
        with a magnitude ≥ 1e3 scale; the unit line is 'млрд ₽' for money, just 'млрд' for a
        count. The scaled magnitude (1 ≤ x < 1000) lets the formatter keep a decimal in the
        1–10 band ("1,5 млрд", not "2 млрд" — L-1). Shared by the KPI headline (_kpi_scale)
        and the cartesian value axis (_axis_scale)."""
        if not is_compact_number(measure):
            return None
        magnitude = self._measure_magnitude(ds, measure)
        if magnitude is None:
            return None
        divisor, unit = ru_kpi_scale(magnitude)
        if divisor <= 1:
            return None
        currency = self._measure_currency(measure, table)
        return divisor, f"{unit} {currency}".strip(), magnitude / divisor

    def _kpi_scale(self, chart: ChartSpec, ds: DatasetRef) -> tuple[float, str, float] | None:
        """(divisor, RU unit line, scaled magnitude) for a large ruble big_number headline,
        or None (default fmt)."""
        if chart.viz != Viz.BIG_NUMBER:
            return None
        return self._ru_scale(chart.query.measures[0], chart.query.table, ds)

    def _axis_scale(self, chart: ChartSpec, ds: DatasetRef) -> tuple[float, str, float] | None:
        """(divisor, RU unit line, scaled magnitude) for a large-magnitude line/bar/area value
        axis, or None to keep d3 SI. Same rule as the KPI: d3's SI axis format only speaks
        k/M/G/T, so RU units ("15 млрд ₽" vs "15G") need the metric scaled and the unit on the
        value-axis title.

        Single-measure charts only: the divisor comes from one measure but would divide every
        metric on the chart, so on "revenue + order count" the second measure would render in
        the first one's units (billions) — off by orders of magnitude."""
        if chart.viz not in _AXIS_SCALE_VIZ or len(chart.query.measures) != 1:
            return None
        return self._ru_scale(chart.query.measures[0], chart.query.table, ds)

    def _temporal_alias(self, query: ChartQuery) -> str | None:
        """Alias of the query's temporal group column (model role=TIME), else None. Passed to
        build_form_data as granularity_sqla so a dashboard native time filter's time_range binds
        to it — the ECharts query names no time column on its own, so the preset period (B5)
        would otherwise not re-scope the chart."""
        if self._model is None:
            return None
        for col in query.group_columns():
            table_name, _, name = col.rpartition(".")
            table = self._model.table(table_name or query.table)
            column = table.column(name) if table else None
            if column is not None and column.role == ColumnRole.TIME:
                return column_alias(col)
        return None

    _HEATMAP_PAD_MAX_CARD = 100  # ordinal periods (cohort months/weeks), not id-like axes

    def _heatmap_y_pad(self, chart: ChartSpec) -> int | None:
        """Zero-pad width for a heatmap's numeric ordinal y-axis, else None.

        heatmap_v2 renders a numeric 0 as `<NULL>` on the axis (upstream #33105) and
        alpha-sorts numeric keys (#31318); padding the value to a fixed width fixes both.
        Applied only to a small-cardinality numeric DIMENSION (cohort periods 0..N, where
        a zero row is the norm and short labels stay short) — an id-like axis (store_id,
        cardinality in the thousands) keeps its natural labels: it has no zero row to hit
        the bug, and "0001" would be strictly worse to read. Width = digits of the largest
        expected value (cardinality - 1, ordinals are dense from 0), min 2.
        """
        if chart.viz != Viz.HEATMAP or self._model is None or len(chart.query.dimensions) != 2:
            return None
        y = chart.query.dimensions[1]
        table_name, _, name = y.rpartition(".")
        table = self._model.table(table_name or chart.query.table)
        column = table.column(name) if table else None
        if column is None or column.role != ColumnRole.DIMENSION:
            return None
        if not re.search(r"int|float|decimal|numeric|double", column.type, re.IGNORECASE):
            return None
        card = (table.physical.cardinality.get(name, 0) if table and table.physical else 0) or 0
        if not 0 < card <= self._HEATMAP_PAD_MAX_CARD:
            return None
        return max(2, len(str(card - 1)))

    def create_chart(self, chart: ChartSpec, ds: DatasetRef) -> ChartRef:
        horizontal = self._model is not None and is_horizontal_bar(chart, self._model)
        form_data = build_form_data(
            chart,
            _int_id(ds.id),
            horizontal=horizontal,
            kpi_scale=self._kpi_scale(chart, ds),
            axis_scale=self._axis_scale(chart, ds),
            metric_labels=self._metric_labels(chart),
            time_column=self._temporal_alias(chart.query),
            heatmap_y_pad=self._heatmap_y_pad(chart),
        )
        created = self._client.post(
            "/api/v1/chart/",
            json={
                "slice_name": chart.title,
                "viz_type": VIZ_TYPE[chart.viz],
                "datasource_id": ds.id,
                "datasource_type": "table",
                "params": json.dumps(form_data, ensure_ascii=False),
            },
        )
        logger.info("chart %r created: id=%s", chart.title, created["id"])
        return ChartRef(id=created["id"], name=chart.title)

    def assemble_dashboard(
        self,
        spec: DashboardSpec,
        charts: list[ChartRef],
        datasets: list[DatasetRef] | None = None,
        model: SemanticModel | None = None,
    ) -> DashboardRef:
        if len(charts) != len(spec.charts):
            raise ValueError(f"got {len(charts)} chart refs for {len(spec.charts)} spec charts")

        native_filters: list[dict] = []
        if spec.filters:
            if datasets is not None and model is not None:
                placements = [
                    (chart, _int_id(ref.id), _int_id(ds.id))
                    for chart, ref, ds in zip(spec.charts, charts, datasets, strict=True)
                ]
                native_filters, applied = build_native_filter_configuration(spec, placements, model)
                for f, in_scope, excluded in applied:
                    logger.info(
                        "native filter %s wired: scope=%s excluded=%s",
                        f.column,
                        in_scope,
                        excluded,
                    )
                wired = {f.column for f, _, _ in applied}
                for f in spec.filters:
                    if f.column not in wired:
                        # no chart exposes the column in its grain -> can't be a native
                        # filter; the baked query.filters still constrain the data
                        logger.warning(
                            "dashboard filter %s not applicable to any chart's grain, skipped",
                            f.column,
                        )
            else:  # build() always supplies datasets+model; this is the bare-protocol path
                logger.warning(
                    "dashboard filters skipped (no model/datasets passed to assemble): %s",
                    [f.column for f in spec.filters],
                )

        placed = list(zip(spec.charts, [_int_id(c.id) for c in charts], strict=True))
        position = build_position_json(spec, placed)
        json_metadata: dict = {"chart_configuration": {}}
        if native_filters:
            json_metadata["native_filter_configuration"] = native_filters
        created = self._client.post(
            "/api/v1/dashboard/",
            json={
                "dashboard_title": spec.title,
                "position_json": json.dumps(position, ensure_ascii=False),
                "json_metadata": json.dumps(json_metadata, ensure_ascii=False),
                "css": KPI_CENTER_CSS,
                "published": True,
            },
        )
        dashboard_id = created["id"]
        for ref in charts:  # link charts to the dashboard
            self._client.put(f"/api/v1/chart/{ref.id}", json={"dashboards": [dashboard_id]})

        url = f"/superset/dashboard/{dashboard_id}/"
        logger.info("dashboard %r assembled: id=%s url=%s", spec.title, dashboard_id, url)
        return DashboardRef(id=dashboard_id, title=spec.title, url=url)

    # --- happy path ----------------------------------------------------------

    def build(self, spec: DashboardSpec) -> DashboardRef:
        """Full compile: database -> per-chart datasets -> charts -> dashboard.

        The constructor-injected model lets the dashboard wire native filters (scope by
        column role/grain); without it the filters degrade to the documented "skipped"
        warning. Signature mirrors DataLensAdapter.build so the pipeline can dispatch by
        `spec.target_bi` (Phase 4 F1).
        """
        model = self._model
        # Ownership ledger (P0-2 criterion 4): reset the buffer, then record each entity as it
        # is created so the orchestrator can drain a complete set after a successful build.
        self._build_artifacts = []
        db = self.ensure_database()
        self._build_artifacts.append(BuildArtifact("database", str(db.id), db.name))
        # charts in a dashboard filter's scope drop the SQL top-N LIMIT (it moves to
        # form_data) so the filter re-ranks after filtering — computable from the spec
        in_filter_scope = participating_chart_ids(spec, model) if model is not None else set()
        refs: list[ChartRef] = []
        datasets: list[DatasetRef] = []
        for chart in spec.charts:
            ds = self.ensure_dataset(
                chart.query,
                name=_dataset_name(spec.title, chart.id, self._artifact_namespace),
                apply_limit=chart.id not in in_filter_scope,
            )
            datasets.append(ds)
            # schema_set = the DWH schema.table this dataset/chart reads (RBAC scoping)
            self._build_artifacts.append(
                BuildArtifact("dataset", str(ds.id), ds.name, chart.query.table)
            )
            ref = self.create_chart(chart, ds)
            self._build_artifacts.append(
                BuildArtifact("chart", str(ref.id), ref.name, chart.query.table)
            )
            refs.append(ref)
        dash = self.assemble_dashboard(spec, refs, datasets=datasets, model=model)
        self._build_artifacts.append(BuildArtifact("dashboard", str(dash.id), dash.title))
        return dash
