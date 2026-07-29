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
from typing import Any
from urllib.parse import urlparse

from auto_bi.adapters.artifacts import BuildArtifact, dataset_table_name
from auto_bi.adapters.base import (
    AdapterHealth,
    BuildAttempt,
    BuildContext,
    BuildReconcileResult,
    BuildResult,
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
from auto_bi.agent.dataset_plan import (
    DatasetPlan,
    DatasetRole,
    plan_datasets,
    source_dataset_inputs,
)
from auto_bi.agent.normalize import is_horizontal_bar
from auto_bi.agent.query_plan import PlanCache
from auto_bi.agent.sqlgen import generate_chart_sql, generate_source_sql
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

# Full attempt token embedded at create time. Human titles only narrow a list query;
# this exact marker is the authority for chart/dashboard crash cleanup.
_BUILD_TOKEN_KEY = "auto_bi_build_token"
# Superset 4.1.2 strips unknown top-level json_metadata keys on save, so dashboard
# ownership is an invisible CSS comment: /*auto_bi_bt:<utf-8-hex>*/ (not raw token).
_BUILD_TOKEN_CSS_RE = re.compile(r"/\*auto_bi_bt:([0-9a-f]+)\*/")


def _build_token_css_marker(namespace: str) -> str:
    """Invisible ownership comment; hex so the raw namespace never enters CSS."""
    return f"/*auto_bi_bt:{namespace.encode('utf-8').hex()}*/"


def _dashboard_css(namespace: str) -> str:
    """KPI_CENTER_CSS alone when namespace is empty; else append the ownership marker."""
    if not namespace:
        return KPI_CENTER_CSS
    return f"{KPI_CENTER_CSS}{_build_token_css_marker(namespace)}"


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
        self,
        client: SupersetClient,
        dwh: DWHConfig,
        model: SemanticModel | None = None,
        *,
        strict_connection: bool = False,
    ) -> None:
        self._client = client
        self._dwh = dwh
        # `model` (constructor-injected, mirrors DataLensAdapter) lets build() scope native
        # filters by column role/grain; without it filters degrade to the documented warning.
        self._model = model
        # C-6: refuse (instead of warn) when the reused BI connection's fingerprint
        # does not match the current DWH config (AUTO_BI_BI_CONNECTION_STRICT).
        self._strict_connection = strict_connection
        self._database: DatabaseRef | None = None
        # P0-2: set via set_artifact_namespace() before build(); empty = legacy single-user.
        self._artifact_namespace: str = ""
        # Ownership ledger (P0-2 criterion 4): build() accumulates the BI entities it creates
        # here; the orchestrator drains them after a successful build via drain_build_artifacts.
        self._build_artifacts: list[BuildArtifact] = []
        # D-2 §5: set via set_query_plans() before build(); None = no trial reuse (probe only).
        self._query_plans: PlanCache | None = None

    # --- BIAdapter ----------------------------------------------------------

    def set_artifact_namespace(self, namespace: str) -> None:
        """Deprecated: prefer `BuildContext.namespace` on `build(spec, ctx)`.

        Kept for unit tests that stage namespace before a bare `build(spec)`.
        """
        self._artifact_namespace = (namespace or "").strip()

    def set_query_plans(self, plans: PlanCache | None) -> None:
        """Deprecated: prefer `BuildContext.plans` on `build(spec, ctx)`.

        Kept for unit tests that stage PlanCache before a bare `build(spec)`.
        """
        self._query_plans = plans

    def drain_build_artifacts(self) -> list[BuildArtifact]:
        """Deprecated: prefer `BuildResult.artifacts` from `build()`.

        Kept for tests that drain after a partial path; a successful `build()` already
        clears the buffer into BuildResult.
        """
        drained = list(self._build_artifacts)
        self._build_artifacts = []
        return drained

    def delete_artifact(self, kind: str, native_id: str) -> None:
        """Delete one owned BI entity by native id (ownership ledger live-cleanup).

        Required BIAdapter method (plan_sol step 7). Returns normally when the entity was
        deleted OR was already gone (404); raises on any other failure so the caller keeps
        the ledger row 'live' and retries on a later prune. Never accepts a shared kind.
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

    def reconcile_build_attempt(self, attempt: BuildAttempt) -> BuildReconcileResult:
        """Delete only artifacts provably owned by an interrupted attempt.

        Charts require an exact full-token key in stored params JSON; dashboards require
        a complete CSS hex marker. Datasets use exact attempt-namespaced technical names.
        List searches paginate; an incomplete/provider-failed scan raises and leaves
        Store CLEANUP_REQUIRED.
        """
        self._artifact_namespace = attempt.build_token.strip()
        owned: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()

        for title in {chart.title for chart in attempt.spec.charts}:
            for item in self._list_exact(
                "/api/v1/chart/",
                column="slice_name",
                value=title,
            ):
                native_id = str(item["id"])
                try:
                    detail = self._client.get(f"/api/v1/chart/{_int_id(native_id)}")
                except SupersetAPIError as exc:
                    if exc.status_code == 404:
                        continue
                    raise
                if self._detail_has_build_token(detail, "params", attempt.build_token):
                    key = ("chart", native_id)
                    if key not in seen:
                        seen.add(key)
                        owned.append(key)

        for item in self._list_exact(
            "/api/v1/dashboard/",
            column="dashboard_title",
            value=attempt.spec.title,
        ):
            native_id = str(item["id"])
            try:
                detail = self._client.get(f"/api/v1/dashboard/{_int_id(native_id)}")
            except SupersetAPIError as exc:
                if exc.status_code == 404:
                    continue
                raise
            if self._detail_css_has_build_token(detail, attempt.build_token):
                key = ("dashboard", native_id)
                if key not in seen:
                    seen.add(key)
                    owned.append(key)

        for name in self._expected_dataset_names(attempt.spec):
            for item in self._list_exact(
                "/api/v1/dataset/",
                column="table_name",
                value=name,
            ):
                key = ("dataset", str(item["id"]))
                if key not in seen:
                    seen.add(key)
                    owned.append(key)

        for kind, native_id in owned:
            self.delete_artifact(kind, native_id)
        return BuildReconcileResult(discovered=len(owned), deleted=len(owned))

    def _list_exact(self, path: str, *, column: str, value: str) -> list[dict[str, Any]]:
        """Exhaustive exact-filter list; never trust a title substring as ownership."""
        page_size = 100
        page = 0
        result: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        while True:
            payload = self._client.get(
                path,
                params={"q": rison_eq_filter(column, value, page_size, page=page)},
            )
            raw_items = payload.get("result", [])
            if not isinstance(raw_items, list):
                raise SupersetAPIError(f"GET {path} returned an invalid result list")
            for item in raw_items:
                if not isinstance(item, dict) or str(item.get(column) or "") != value:
                    continue
                native_id = str(item.get("id") or "")
                if native_id and native_id not in seen_ids:
                    seen_ids.add(native_id)
                    result.append(item)
            raw_count = payload.get("count")
            if raw_count is None:
                break
            try:
                count = int(raw_count)
            except (TypeError, ValueError) as exc:
                raise SupersetAPIError(f"GET {path} returned an invalid count") from exc
            if len(result) >= count:
                break
            if not raw_items:
                raise SupersetAPIError(f"GET {path} pagination ended before count={count}")
            page += 1
        return result

    @staticmethod
    def _detail_has_build_token(
        detail: dict[str, Any],
        field: str,
        build_token: str,
    ) -> bool:
        """Chart ownership: exact full-token equality in stored JSON params."""
        body = detail.get("result", detail)
        if not isinstance(body, dict):
            return False
        raw = body.get(field)
        if isinstance(raw, dict):
            metadata = raw
        elif isinstance(raw, str):
            try:
                metadata = json.loads(raw)
            except (TypeError, ValueError):
                return False
        else:
            return False
        return isinstance(metadata, dict) and metadata.get(_BUILD_TOKEN_KEY) == build_token

    @staticmethod
    def _detail_css_has_build_token(detail: dict[str, Any], build_token: str) -> bool:
        """Dashboard ownership: complete CSS marker whose hex equals the token encoding."""
        body = detail.get("result", detail)
        if not isinstance(body, dict):
            return False
        css = body.get("css")
        if not isinstance(css, str):
            return False
        expected = build_token.encode("utf-8").hex()
        return any(match.group(1) == expected for match in _BUILD_TOKEN_CSS_RE.finditer(css))

    def _expected_dataset_names(self, spec: DashboardSpec) -> set[str]:
        plan = plan_datasets(spec) if self._model is not None else None
        names: set[str] = set()
        if plan is not None:
            for table in plan.source_tables:
                names.add(_dataset_name(spec.title, f"source:{table}", self._artifact_namespace))
        for chart in spec.charts:
            if plan is None or plan.chart(chart.id).role is DatasetRole.OWN:
                names.add(_dataset_name(spec.title, chart.id, self._artifact_namespace))
        return names

    def close(self) -> None:
        """Release the client's HTTP pool (D-2 lifecycle; required BIAdapter method)."""
        self._client.close()

    def healthcheck(self) -> AdapterHealth:
        ok = self._client.health()
        return AdapterHealth(ok=ok, message="" if ok else "GET /health failed")

    def ensure_database(self, dwh: DWHConfig | None = None) -> DatabaseRef:
        dwh = dwh or self._dwh
        existing = self._client.get(
            "/api/v1/database/", params={"q": rison_eq_filter("database_name", DATABASE_NAME)}
        )
        for item in existing.get("result", []):
            self._verify_connection_fingerprint(item["id"], dwh)
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

    def _verify_connection_fingerprint(self, database_id: int, dwh: DWHConfig) -> None:
        """C-6: a reused connection must point at the CURRENT DWH.

        `ensure_database` reuses the connection by NAME, so a stale entry (stand moved,
        port changed, different database) would silently feed every dashboard from the
        wrong DWH. Compare host/port/database parsed from the existing sqlalchemy_uri
        (password is masked by Superset — не сравнивается) against DWHConfig. Best-effort:
        an unreadable fingerprint only logs debug; a POSITIVE mismatch warns, or raises
        when `strict_connection` (AUTO_BI_BI_CONNECTION_STRICT=true).
        """
        try:
            detail = self._client.get(f"/api/v1/database/{database_id}").get("result") or {}
            uri = urlparse(detail.get("sqlalchemy_uri") or "")
        except Exception as exc:  # fingerprint is advisory — never break the build path
            logger.debug("connection fingerprint unavailable for id=%s: %s", database_id, exc)
            return
        if not uri.scheme:  # no uri in the payload -> cannot verify
            return
        mismatches: list[str] = []
        if (uri.hostname or "").lower() != dwh.host.lower():
            mismatches.append(f"host {uri.hostname!r} != {dwh.host!r}")
        if uri.port is not None and uri.port != dwh.port:
            mismatches.append(f"port {uri.port} != {dwh.port}")
        existing_db = (uri.path or "").lstrip("/")
        if existing_db and existing_db != dwh.database:
            mismatches.append(f"database {existing_db!r} != {dwh.database!r}")
        if not mismatches:
            return
        msg = (
            f"reused Superset connection {DATABASE_NAME!r} (id={database_id}) does not "
            f"match the current DWH config: " + "; ".join(mismatches)
        )
        if self._strict_connection:
            raise SupersetAPIError("stale BI connection refused: " + msg)
        logger.warning("%s — proceeding (AUTO_BI_BI_CONNECTION_STRICT=true to refuse)", msg)

    def ensure_dataset(
        self, query: ChartQuery, name: str | None = None, *, apply_limit: bool = True
    ) -> DatasetRef:
        """OWN per-chart virtual dataset from `generate_chart_sql` (Protocol seam)."""
        sql = generate_chart_sql(query, apply_limit=apply_limit)
        table_name = name or f"auto_bi__{_slug(query.table)}"
        return self._ensure_sql_dataset(sql, table_name)

    def _ensure_sql_dataset(self, sql: str, table_name: str) -> DatasetRef:
        """Idempotent virtual dataset from validated SQL (shared source or OWN).

        Same namespace/ownership conventions and PUT-on-exists behaviour as the historical
        ensure_dataset path; the SQL is assumed already gated (guard + EXPLAIN + LIMIT).
        """
        db = self._database or self.ensure_database()
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

    def _measure_magnitude(
        self,
        ds: DatasetRef,
        measure: Measure,
        *,
        from_source: bool = False,
        chart: ChartSpec | None = None,
    ) -> float | None:
        """The measure's peak aggregated value, measured live so its RU magnitude unit
        (млрд/млн/тыс) can be chosen.

        OWN dataset: already aggregated — ``MAX("sum_revenue")`` is the tallest series
        point (or the scalar for a one-row KPI). When a complete LIMIT-trial was recorded
        for this chart's exact SQL (D-2 §5), reuse max of the measure-alias column and
        skip the `/api/v1/chart/data` probe. Incomplete (full-limit) trials fall through.

        SOURCE dataset: raw mart rows — probe must re-aggregate with the IR aggregation
        (``SUM("revenue")``, not ``MAX("sum_revenue")`` which does not exist). For a
        grouped chart, group by the chart dims, order by the metric desc, row_limit 1
        so the value is still the tallest series point. Trial rows are never used for
        SOURCE (raw mart rows are not the aggregated magnitude).

        Best-effort: any failure returns None and the chart falls back to the default
        format — a display nicety must never break a build.
        """
        try:
            reused = self._magnitude_from_trial(measure, from_source=from_source, chart=chart)
            if reused is not None:
                return reused
            if from_source:
                metric = _adhoc_metric(measure, "kpimag", 0, from_source=True)
            else:
                metric = _adhoc_metric(measure, "kpimag", 0, agg="MAX")
            query: dict[str, Any] = {
                "metrics": [metric],
                "row_limit": 1,
            }
            if from_source and chart is not None:
                dims = list(chart.query.group_columns())
                if dims:
                    from auto_bi.agent.dataset_plan import source_column_alias

                    query["groupby"] = [source_column_alias(c, chart.query.table) for c in dims]
                    # tallest series point: order by the metric descending
                    query["orderby"] = [[metric, False]]
            result = self._client.post(
                "/api/v1/chart/data",
                json={
                    "datasource": {"id": _int_id(ds.id), "type": "table"},
                    "force": True,
                    "queries": [query],
                    "result_format": "json",
                    "result_type": "full",
                },
            )
            rows = result["result"][0]["data"]
            if not rows:
                return None
            # adhoc label is the measure alias (or human label when set)
            label = metric.get("label") or measure_alias(measure)
            value = rows[0].get(label)
            return float(value) if value is not None else None
        except (SupersetAPIError, KeyError, IndexError, TypeError, ValueError):
            return None

    def _magnitude_from_trial(
        self,
        measure: Measure,
        *,
        from_source: bool,
        chart: ChartSpec | None,
    ) -> float | None:
        """D-2 §5: max of measure-alias values from a complete OWN-chart trial, else None.

        All conditions required; any miss falls through to the live probe. Never raises.
        """
        if from_source or chart is None or self._query_plans is None:
            return None
        # Same key the pipeline used when gating OWN SQL (generate_chart_sql, default args).
        trial = self._query_plans.get_trial(generate_chart_sql(chart.query))
        if trial is None or not trial.complete:
            return None
        alias = measure_alias(measure)
        values: list[float] = []
        for row in trial.rows:
            if alias not in row:
                continue
            raw = row[alias]
            if raw is None:
                continue
            try:
                values.append(float(raw))
            except (TypeError, ValueError):
                continue
        return max(values) if values else None

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
        self,
        measure: Measure,
        table: str,
        ds: DatasetRef,
        *,
        from_source: bool = False,
        chart: ChartSpec | None = None,
    ) -> tuple[float, str, float] | None:
        """(divisor, RU unit line, scaled magnitude) for a large compact measure, measured
        live, or None to keep the default format. Only additive aggregates (is_compact_number)
        with a magnitude ≥ 1e3 scale; the unit line is 'млрд ₽' for money, just 'млрд' for a
        count. The scaled magnitude (1 ≤ x < 1000) lets the formatter keep a decimal in the
        1–10 band ("1,5 млрд", not "2 млрд" — L-1). Shared by the KPI headline (_kpi_scale)
        and the cartesian value axis (_axis_scale)."""
        if not is_compact_number(measure):
            return None
        magnitude = self._measure_magnitude(ds, measure, from_source=from_source, chart=chart)
        if magnitude is None:
            return None
        divisor, unit = ru_kpi_scale(magnitude)
        if divisor <= 1:
            return None
        currency = self._measure_currency(measure, table)
        return divisor, f"{unit} {currency}".strip(), magnitude / divisor

    def _kpi_scale(
        self, chart: ChartSpec, ds: DatasetRef, *, from_source: bool = False
    ) -> tuple[float, str, float] | None:
        """(divisor, RU unit line, scaled magnitude) for a large ruble big_number headline,
        or None (default fmt)."""
        if chart.viz != Viz.BIG_NUMBER:
            return None
        return self._ru_scale(
            chart.query.measures[0],
            chart.query.table,
            ds,
            from_source=from_source,
            chart=chart,
        )

    def _axis_scale(
        self, chart: ChartSpec, ds: DatasetRef, *, from_source: bool = False
    ) -> tuple[float, str, float] | None:
        """(divisor, RU unit line, scaled magnitude) for a large-magnitude line/bar/area value
        axis, or None to keep d3 SI. Same rule as the KPI: d3's SI axis format only speaks
        k/M/G/T, so RU units ("15 млрд ₽" vs "15G") need the metric scaled and the unit on the
        value-axis title.

        Single-measure charts only: the divisor comes from one measure but would divide every
        metric on the chart, so on "revenue + order count" the second measure would render in
        the first one's units (billions) — off by orders of magnitude."""
        if chart.viz not in _AXIS_SCALE_VIZ or len(chart.query.measures) != 1:
            return None
        return self._ru_scale(
            chart.query.measures[0],
            chart.query.table,
            ds,
            from_source=from_source,
            chart=chart,
        )

    def _temporal_alias(self, query: ChartQuery, *, from_source: bool = False) -> str | None:
        """Alias of the temporal column for granularity_sqla, else None.

        Prefer a TIME column in the chart's grain (line/bar x-axis). On a SOURCE dataset the
        mart still carries its TIME column even when the chart does not GROUP BY it (KPI),
        so a dashboard native time filter can re-scope the multi-row aggregate — passed to
        build_form_data as granularity_sqla. Without it the ECharts/big_number query names no
        time column and the preset period (B5) silently fails to re-scope the chart.
        """
        if self._model is None:
            return None
        for col in query.group_columns():
            table_name, _, name = col.rpartition(".")
            table = self._model.table(table_name or query.table)
            column = table.column(name) if table else None
            if column is not None and column.role == ColumnRole.TIME:
                return column_alias(col)
        if from_source:
            table = self._model.table(query.table)
            if table is not None:
                for column in table.columns:
                    if column.role == ColumnRole.TIME:
                        return column.name
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

    def create_chart(
        self, chart: ChartSpec, ds: DatasetRef, *, from_source: bool = False
    ) -> ChartRef:
        horizontal = self._model is not None and is_horizontal_bar(chart, self._model)
        form_data = build_form_data(
            chart,
            _int_id(ds.id),
            horizontal=horizontal,
            kpi_scale=self._kpi_scale(chart, ds, from_source=from_source),
            axis_scale=self._axis_scale(chart, ds, from_source=from_source),
            metric_labels=self._metric_labels(chart),
            time_column=self._temporal_alias(chart.query, from_source=from_source),
            heatmap_y_pad=self._heatmap_y_pad(chart),
            from_source=from_source,
        )
        if self._artifact_namespace:
            form_data[_BUILD_TOKEN_KEY] = self._artifact_namespace
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
        plan: DatasetPlan | None = None,
    ) -> DashboardRef:
        if len(charts) != len(spec.charts):
            raise ValueError(f"got {len(charts)} chart refs for {len(spec.charts)} spec charts")

        native_filters: list[dict[str, Any]] = []
        if spec.filters:
            if datasets is not None and model is not None:
                placements = [
                    (chart, _int_id(ref.id), _int_id(ds.id))
                    for chart, ref, ds in zip(spec.charts, charts, datasets, strict=True)
                ]
                native_filters, applied = build_native_filter_configuration(
                    spec, placements, model, plan=plan
                )
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
        # Only supported json_metadata keys: Superset 4.1.2 drops unknown top-level
        # entries (incl. auto_bi_build_token). Ownership lives in dashboard css instead.
        json_metadata: dict[str, Any] = {"chart_configuration": {}}
        if native_filters:
            json_metadata["native_filter_configuration"] = native_filters
        created = self._client.post(
            "/api/v1/dashboard/",
            json={
                "dashboard_title": spec.title,
                "position_json": json.dumps(position, ensure_ascii=False),
                "json_metadata": json.dumps(json_metadata, ensure_ascii=False),
                "css": _dashboard_css(self._artifact_namespace),
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

    def build(self, spec: DashboardSpec, ctx: BuildContext | None = None) -> BuildResult:
        """Full compile: database -> datasets -> charts -> dashboard.

        D-1 (variant A): with a model, `plan_datasets` picks one shared semantic-grain
        source dataset per mart for expressible (SOURCE) charts; inexpressible (OWN)
        charts keep today's per-chart aggregated dataset. Without a model the legacy
        one-dataset-per-chart path is kept (bare-protocol / unit tests).

        The constructor-injected model also wires native filters (scope = SOURCE charts
        on that mart, plus OWN charts whose grain exposes the column). Signature mirrors
        DataLensAdapter.build so the pipeline can dispatch by `spec.target_bi` (Phase 4 F1).

        `ctx` (plan_sol step 7) supplies namespace + PlanCache; when omitted, any values
        staged via the deprecated set_* helpers still apply (unit tests).
        """
        if ctx is not None:
            if ctx.namespace:
                self._artifact_namespace = ctx.namespace.strip()
            self._query_plans = ctx.plans
        model = self._model
        # Ownership ledger (P0-2 criterion 4): reset the buffer, then record each entity as it
        # is created; returned via BuildResult.artifacts (no post-build drain getattr).
        self._build_artifacts = []
        db = self.ensure_database()
        self._build_artifacts.append(BuildArtifact("database", str(db.id), db.name))

        plan = plan_datasets(spec) if model is not None else None
        # charts in a dashboard filter's scope drop the SQL top-N LIMIT (it moves to
        # form_data) so the filter re-ranks after filtering — OWN charts only; SOURCE SQL
        # has no LIMIT by construction
        in_filter_scope = participating_chart_ids(spec, model) if model is not None else set()

        # one shared source dataset per mart that has at least one SOURCE chart
        source_datasets: dict[str, DatasetRef] = {}
        if plan is not None and model is not None:
            for table in plan.source_tables:
                inputs = source_dataset_inputs(spec, plan, model, table)
                sql = generate_source_sql(
                    inputs.table,
                    list(inputs.columns),
                    list(inputs.joins),
                    inputs.joined_refs,
                )
                name = _dataset_name(spec.title, f"source:{table}", self._artifact_namespace)
                ds = self._ensure_sql_dataset(sql, name)
                source_datasets[table] = ds
                self._build_artifacts.append(BuildArtifact("dataset", str(ds.id), ds.name, table))
                logger.info("source dataset for %s: id=%s name=%s", table, ds.id, ds.name)

        refs: list[ChartRef] = []
        datasets: list[DatasetRef] = []
        for chart in spec.charts:
            role = plan.chart(chart.id).role if plan is not None else DatasetRole.OWN
            if role is DatasetRole.SOURCE:
                ds = source_datasets[chart.query.table]
                from_source = True
            else:
                ds = self.ensure_dataset(
                    chart.query,
                    name=_dataset_name(spec.title, chart.id, self._artifact_namespace),
                    apply_limit=chart.id not in in_filter_scope,
                )
                self._build_artifacts.append(
                    BuildArtifact("dataset", str(ds.id), ds.name, chart.query.table)
                )
                from_source = False
            datasets.append(ds)
            ref = self.create_chart(chart, ds, from_source=from_source)
            self._build_artifacts.append(
                BuildArtifact("chart", str(ref.id), ref.name, chart.query.table)
            )
            refs.append(ref)
        dash = self.assemble_dashboard(spec, refs, datasets=datasets, model=model, plan=plan)
        self._build_artifacts.append(BuildArtifact("dashboard", str(dash.id), dash.title))
        arts = tuple(self._build_artifacts)
        self._build_artifacts = []
        return BuildResult(dashboard=dash, artifacts=arts)
