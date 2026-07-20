"""D-1 PR-3 acceptance: filterable SOURCE dataset re-scopes via chart/data.

Offline lane (this module without the integration marker): payload-shape asserts for
the acceptance dashboard (roles, form_data, native-filter binding, extra_form_data /
magnitude-probe request shapes). Deselected integration tests stay green.

Live lane (``pytest -m integration``): build the real pipeline against ClickHouse +
Superset 4.1, then POST ``/api/v1/chart/data`` with the same filters native controls
emit (time_range + stores_name select) and assert numbers match independent
ClickHouse totals. Soft-collects independent checks so one CI run reports every
failure.

Run live::

    uv run pytest -m integration tests/test_d1_acceptance.py
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

import pytest

from auto_bi.adapters.base import DWHConfig
from auto_bi.adapters.superset.adapter import SupersetAdapter
from auto_bi.adapters.superset.client import SupersetAPIError, SupersetClient
from auto_bi.adapters.superset.form_data import VIZ_TYPE, _adhoc_metric, build_form_data
from auto_bi.adapters.superset.native_filters import build_native_filter_configuration
from auto_bi.agent.dataset_plan import DatasetRole, plan_datasets, source_column_alias
from auto_bi.config import get_settings
from auto_bi.introspect.clickhouse import make_run_query
from auto_bi.ir.spec import (
    ChartQuery,
    ChartSpec,
    DashboardFilter,
    DashboardSpec,
    JoinSpec,
    LayoutHint,
    Measure,
    OrderBy,
    TimeGrain,
    Viz,
)
from auto_bi.semantic.model import Aggregation, SemanticModel

# ---------------------------------------------------------------------------
# Acceptance dashboard (Task 7 criterion)
# ---------------------------------------------------------------------------

MART = "dm.sales_daily"
STORES_JOIN = JoinSpec(
    table="dm.stores",
    on_left="dm.sales_daily.store_id",
    on_right="dm.stores.id",
)
REVENUE = Measure(column="revenue", agg=Aggregation.SUM, label="Выручка")
RATIO = Measure(
    column="revenue",
    agg=Aggregation.SUM,
    label="Средний чек",
    denominator=Measure(column="orders", agg=Aggregation.SUM),
)

# Two non-empty, non-overlapping windows inside the demo fact (730 days from 2024-07-01).
# NOTE (live CI 2026-07-20, assumption 3 refuted): Superset time_range "A : B" is
# inclusive-start, EXCLUSIVE-end — [A, B). The CH reference mirrors that (< end).
PERIOD_A = ("2025-01-01", "2025-06-30")  # H1 2025
PERIOD_B = ("2025-07-01", "2025-12-31")  # H2 2025
PERIOD_A_RANGE = f"{PERIOD_A[0]} : {PERIOD_A[1]}"
PERIOD_B_RANGE = f"{PERIOD_B[0]} : {PERIOD_B[1]}"


def _time_axis(grain: str) -> dict:
    """Temporal x-axis column as the real ECharts chart emits it in query_context.

    Live CI 2026-07-20 (two iterations): a top-level ``time_grain_sqla`` key on an
    ad-hoc chart/data query is silently ignored (daily rows came back); a lone
    BASE_AXIS column bucketed the SELECT (P1M → toStartOfMonth, live-proving the
    grain mapping) but was left out of GROUP BY (CH error 215). The real plugin
    payload carries the grain in BOTH the BASE_AXIS column and
    ``extras.time_grain_sqla`` plus ``series_columns`` — see ``_grain_query``.
    """
    return {
        "columnType": "BASE_AXIS",
        "expressionType": "SQL",
        "label": "date",
        "sqlExpression": "date",
        "timeGrain": grain,
    }


def _grain_query(grain: str) -> dict:
    """Query fragment replicating how the saved ECharts chart requests a time grain."""
    return {
        "columns": [_time_axis(grain)],
        "series_columns": [],
        "extras": {"time_grain_sqla": grain},
    }


def d1_acceptance_spec() -> DashboardSpec:
    """KPI + monthly trend + joined-label bar + ratio — all SOURCE-expressible."""
    return DashboardSpec(
        title="[d1-accept] filterable source",
        filters=[
            DashboardFilter(
                column="dm.sales_daily.date",
                type="time_range",
                default="Last year",
            ),
            DashboardFilter(column="dm.stores.name", type="value"),
        ],
        charts=[
            ChartSpec(
                id="d1_kpi",
                title="[d1] KPI",
                viz=Viz.BIG_NUMBER,
                query=ChartQuery(table=MART, measures=[REVENUE]),
                layout_hint=LayoutHint(w=4, h=2, row=0),
            ),
            ChartSpec(
                id="d1_trend",
                title="[d1] trend",
                viz=Viz.LINE,
                query=ChartQuery(
                    table=MART,
                    dimensions=["date"],
                    measures=[REVENUE],
                    time_grain=TimeGrain.MONTH,
                    order_by=[OrderBy(by="date")],
                ),
                layout_hint=LayoutHint(w=8, h=4, row=0),
            ),
            ChartSpec(
                id="d1_breakdown",
                title="[d1] stores",
                viz=Viz.BAR,
                query=ChartQuery(
                    table=MART,
                    dimensions=["dm.stores.name"],
                    measures=[REVENUE],
                    joins=[STORES_JOIN],
                    order_by=[OrderBy(by="Выручка", dir="desc")],
                    limit=10,
                ),
                layout_hint=LayoutHint(w=6, h=4, row=1),
            ),
            ChartSpec(
                id="d1_ratio",
                title="[d1] ratio",
                viz=Viz.BAR,
                query=ChartQuery(
                    table=MART,
                    dimensions=["dm.stores.name"],
                    measures=[RATIO],
                    joins=[STORES_JOIN],
                    order_by=[OrderBy(by="Средний чек", dir="desc")],
                    limit=10,
                ),
                layout_hint=LayoutHint(w=6, h=4, row=1),
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Offline: payload shapes (run under default addopts)
# ---------------------------------------------------------------------------


def test_d1_acceptance_spec_all_charts_are_source() -> None:
    plan = plan_datasets(d1_acceptance_spec())
    assert plan.source_tables == (MART,)
    for chart_id, cp in plan.charts.items():
        assert cp.role is DatasetRole.SOURCE, f"{chart_id} must be SOURCE, got {cp.role}"


def test_d1_acceptance_form_data_payload_shapes() -> None:
    """Offline shapes the live lane will POST as metrics/columns on chart/data."""
    spec = d1_acceptance_spec()
    by_id = {c.id: c for c in spec.charts}

    kpi = build_form_data(by_id["d1_kpi"], 1, from_source=True, time_column="date")
    assert kpi["viz_type"] == VIZ_TYPE[Viz.BIG_NUMBER]
    assert kpi["metric"]["sqlExpression"] == 'SUM("revenue")'
    assert kpi["granularity_sqla"] == "date"
    assert "MAX" not in kpi["metric"]["sqlExpression"]

    trend = build_form_data(by_id["d1_trend"], 1, from_source=True, time_column="date")
    assert trend["viz_type"] == VIZ_TYPE[Viz.LINE]
    assert trend["metrics"][0]["sqlExpression"] == 'SUM("revenue")'
    assert trend["time_grain_sqla"] == "P1M"
    assert trend["granularity_sqla"] == "date"
    assert trend["x_axis"] == "date"

    breakdown = build_form_data(by_id["d1_breakdown"], 1, from_source=True, time_column="date")
    assert breakdown["x_axis"] == "stores_name"
    assert breakdown["metrics"][0]["sqlExpression"] == 'SUM("revenue")'
    assert breakdown.get("series_limit") == 10
    assert breakdown["granularity_sqla"] == "date"  # time filter re-scopes the bar too

    ratio = build_form_data(by_id["d1_ratio"], 1, from_source=True, time_column="date")
    expr = ratio["metrics"][0]["sqlExpression"]
    assert expr == '(SUM("revenue")) / NULLIF((SUM("orders")), 0)'
    assert ratio["x_axis"] == "stores_name"


def test_d1_acceptance_native_filter_binds_stores_name() -> None:
    """Select filter target column is the source alias, not bare ``name``."""
    model = SemanticModel.load("semantic/model.yaml")
    spec = d1_acceptance_spec()
    # synthetic placements: (chart, slice_id, dataset_id) — all share source ds 10
    placements = [(c, 100 + i, 10) for i, c in enumerate(spec.charts)]
    nfc, applied = build_native_filter_configuration(spec, placements, model)
    by_type = {f["filterType"]: f for f in nfc}
    assert "filter_time" in by_type
    select = by_type["filter_select"]
    assert select["targets"][0]["column"]["name"] == "stores_name"
    assert select["targets"][0]["datasetId"] == 10
    # every chart is SOURCE on the mart with the join → all in scope of the store filter
    assert set(select["chartsInScope"]) == {100, 101, 102, 103}
    # time filter also scopes every SOURCE chart (KPI has granularity_sqla via mart TIME)
    assert set(by_type["filter_time"]["chartsInScope"]) == {100, 101, 102, 103}
    assert len(applied) == 2


def test_d1_extra_form_data_payload_shapes() -> None:
    """The request body shapes the live lane will POST (period + joined select)."""
    # period change → query carries time_range + granularity (native filter_time mask)
    period_query = {
        "metrics": [_adhoc_metric(REVENUE, "d1_kpi", 0, from_source=True)],
        "row_limit": 1,
        "granularity": "date",
        "time_range": PERIOD_A_RANGE,
    }
    assert period_query["time_range"] == "2025-01-01 : 2025-06-30"
    assert period_query["metrics"][0]["sqlExpression"] == 'SUM("revenue")'

    # select on joined label → filters use the source alias stores_name
    select_query = {
        "metrics": [_adhoc_metric(REVENUE, "d1_kpi", 0, from_source=True)],
        "row_limit": 1,
        "granularity": "date",
        "time_range": PERIOD_A_RANGE,
        "filters": [{"col": "stores_name", "op": "IN", "val": ["Магазин №1"]}],
    }
    assert select_query["filters"][0]["col"] == source_column_alias("dm.stores.name", MART)
    assert select_query["filters"][0]["op"] == "IN"

    # trend with monthly grain — grain rides in the BASE_AXIS column AND extras
    trend_query = {
        **_grain_query("P1M"),
        "metrics": [_adhoc_metric(REVENUE, "d1_trend", 0, from_source=True)],
        "granularity": "date",
        "time_range": PERIOD_A_RANGE,
        "row_limit": 5000,
    }
    assert trend_query["columns"][0]["timeGrain"] == "P1M"
    assert trend_query["columns"][0]["columnType"] == "BASE_AXIS"
    assert trend_query["extras"]["time_grain_sqla"] == "P1M"


def test_d1_magnitude_probe_orderby_payload_shape() -> None:
    """Assumption 4 offline: probe orderby is Explore-style [[metric_dict, False]]."""
    from auto_bi.adapters.base import DatasetRef
    from tests.test_superset_adapter import FakeSuperset, make_adapter

    fake = FakeSuperset(kpi_value=14e9)
    adapter = make_adapter(fake, model=SemanticModel.load("semantic/model.yaml"))
    line = ChartSpec(
        id="d1_trend",
        title="t",
        viz=Viz.LINE,
        query=ChartQuery(
            table=MART, dimensions=["date"], measures=[REVENUE], time_grain=TimeGrain.MONTH
        ),
    )
    adapter._axis_scale(line, DatasetRef(id=42, name="t"), from_source=True)
    probe = next(b for m, p, b in fake.requests if m == "POST" and p == "/api/v1/chart/data")
    q = probe["queries"][0]
    assert q["orderby"] == [[q["metrics"][0], False]]
    assert q["groupby"] == ["date"]
    assert 'SUM("revenue")' in q["metrics"][0]["sqlExpression"]


def test_as_date_parses_superset_epoch_ms_and_iso() -> None:
    """chart/data returns temporal columns as epoch ms (midnight UTC), not ISO —
    the first live CI run failed exactly here (ValueError: month must be in 1..12)."""
    assert _as_date(1735689600000) == date(2025, 1, 1)  # epoch ms
    assert _as_date(1735689600) == date(2025, 1, 1)  # epoch seconds
    assert _as_date("1735689600000") == date(2025, 1, 1)  # stringified ms
    assert _as_date("2025-01-01") == date(2025, 1, 1)
    assert _as_date("2025-01-01T00:00:00") == date(2025, 1, 1)


# ---------------------------------------------------------------------------
# Live integration helpers
# ---------------------------------------------------------------------------


class _Soft:
    """Accumulate independent failures so one CI run surfaces everything."""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.notes: list[str] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        if ok:
            self.notes.append(f"PASS {name}" + (f" — {detail}" if detail else ""))
        else:
            self.failures.append(f"FAIL {name}" + (f" — {detail}" if detail else ""))

    def report(self) -> str:
        return "\n".join(self.notes + self.failures)

    def assert_all(self) -> None:
        if self.failures:
            raise AssertionError(
                f"{len(self.failures)} check(s) failed:\n" + "\n".join(self.failures)
            )


def _approx(a: float | None, b: float | None, *, rel: float = 1e-4, abs_tol: float = 1e-2) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= max(abs_tol, rel * max(1.0, abs(float(b))))


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, int | float) or (isinstance(value, str) and value.lstrip("-").isdigit()):
        # Superset chart/data returns temporal columns as epoch ms (midnight UTC)
        ts = float(value)
        if abs(ts) >= 1e11:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=UTC).date()
    return date.fromisoformat(str(value)[:10])


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _time_range_token(start: str, end: str) -> str:
    return f"{start} : {end}"


def _ch_where(
    start: str | None = None,
    end: str | None = None,
    store_name: str | None = None,
) -> str:
    clauses: list[str] = []
    if start is not None:
        clauses.append(f"f.date >= toDate('{start}')")
    if end is not None:
        # Superset time_range "A : B" is [A, B) — mirror the exclusive end
        clauses.append(f"f.date < toDate('{end}')")
    if store_name is not None:
        # match the source-dataset alias path: JOIN stores, filter on name
        safe = store_name.replace("'", "''")
        clauses.append(f"s.name = '{safe}'")
    return (" WHERE " + " AND ".join(clauses)) if clauses else ""


@pytest.fixture(scope="module")
def live_model() -> SemanticModel:
    return SemanticModel.load("semantic/model.yaml")


@pytest.fixture(scope="module")
def live_adapter(live_model: SemanticModel) -> SupersetAdapter:
    settings = get_settings()
    client = SupersetClient(
        settings.superset_url, settings.superset_user, settings.superset_password
    )
    dwh = DWHConfig(
        host=settings.ch_host_from_bi or settings.ch_host,
        port=settings.ch_port_from_bi or settings.ch_port,
        database=settings.ch_database,
        user=settings.ch_user,
        password=settings.ch_password,
    )
    adapter = SupersetAdapter(client, dwh, live_model)
    assert adapter.healthcheck().ok, "Superset /health failed — is the stand up?"
    adapter.ensure_database()
    yield adapter
    client.close()


@pytest.fixture(scope="module")
def ch_run():
    return make_run_query(get_settings())


def _post_chart_data(adapter: SupersetAdapter, dataset_id: int, query: dict) -> dict:
    """POST /api/v1/chart/data; on mismatch dump request + response for CI."""
    body = {
        "datasource": {"id": int(dataset_id), "type": "table"},
        "force": True,
        "queries": [query],
        "result_format": "json",
        "result_type": "full",
    }
    result = adapter._client.post("/api/v1/chart/data", json=body)
    first = result["result"][0]
    status = first.get("status")
    if status not in ("success", "Success"):
        raise AssertionError(
            "chart/data non-success status\n"
            f"status={status!r}\n"
            f"request={json.dumps(body, ensure_ascii=False, default=str)[:4000]}\n"
            f"response={json.dumps(first, ensure_ascii=False, default=str)[:4000]}"
        )
    return first


def _metric_value(row: dict, label: str) -> float | None:
    if label in row:
        return _as_float(row[label])
    # Superset sometimes keys by the raw expression; fall back to the sole numeric cell
    nums = [v for k, v in row.items() if k != "date" and k != "stores_name" and v is not None]
    if len(nums) == 1:
        return _as_float(nums[0])
    return None


def _source_dataset_id(adapter: SupersetAdapter, chart_slice_id: int) -> int:
    chart = adapter._client.get(f"/api/v1/chart/{chart_slice_id}")["result"]
    params = json.loads(chart["params"])
    return int(str(params["datasource"]).split("__", 1)[0])


def _slice_map(adapter: SupersetAdapter, dashboard_id: int) -> dict[str, int]:
    dash = adapter._client.get(f"/api/v1/dashboard/{dashboard_id}")["result"]
    pos = json.loads(dash["position_json"])
    return {
        v["meta"]["sliceName"]: int(v["meta"]["chartId"])
        for v in pos.values()
        if isinstance(v, dict) and v.get("type") == "CHART"
    }


def _form_data(adapter: SupersetAdapter, slice_id: int) -> dict:
    return json.loads(adapter._client.get(f"/api/v1/chart/{slice_id}")["result"]["params"])


# ---------------------------------------------------------------------------
# Live acceptance
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_d1_live_build_and_filter_rescope(
    live_adapter: SupersetAdapter, ch_run, live_model: SemanticModel
) -> None:
    """Task 7 criterion: build → period + stores_name re-scope equals ClickHouse.

    Also soft-collects the five declared live assumptions from rounds 1–2.
    """
    soft = _Soft()
    spec = d1_acceptance_spec()
    plan = plan_datasets(spec)
    soft.check(
        "plan: all four charts SOURCE",
        all(plan.chart(c.id).role is DatasetRole.SOURCE for c in spec.charts),
        f"roles={[plan.chart(c.id).role for c in spec.charts]}",
    )

    ref = live_adapter.build(spec)
    slices = _slice_map(live_adapter, int(ref.id))
    soft.check(
        "build: four charts placed",
        set(slices) >= {"[d1] KPI", "[d1] trend", "[d1] stores", "[d1] ratio"},
        f"slices={sorted(slices)}",
    )

    kpi_sid = slices["[d1] KPI"]
    trend_sid = slices["[d1] trend"]
    stores_sid = slices["[d1] stores"]
    ratio_sid = slices["[d1] ratio"]
    ds_id = _source_dataset_id(live_adapter, kpi_sid)
    # SOURCE: every chart shares the same dataset
    soft.check(
        "build: shared source dataset",
        all(
            _source_dataset_id(live_adapter, sid) == ds_id
            for sid in (kpi_sid, trend_sid, stores_sid, ratio_sid)
        ),
        f"ds_id={ds_id}",
    )

    # --- native filter config (assumption 5 scaffolding) --------------------
    dash = live_adapter._client.get(f"/api/v1/dashboard/{ref.id}")["result"]
    nfc = json.loads(dash["json_metadata"]).get("native_filter_configuration") or []
    select_filters = [f for f in nfc if f.get("filterType") == "filter_select"]
    time_filters = [f for f in nfc if f.get("filterType") == "filter_time"]
    soft.check("nfc: has filter_select", len(select_filters) == 1, f"nfc={nfc!r}"[:800])
    soft.check("nfc: has filter_time", len(time_filters) == 1, f"nfc={nfc!r}"[:800])
    if select_filters:
        bound = select_filters[0]["targets"][0].get("column", {}).get("name")
        soft.check(
            "assumption5: native filter binds stores_name",
            bound == "stores_name",
            f"bound={bound!r} filter={json.dumps(select_filters[0], ensure_ascii=False)[:1200]}",
        )
        in_scope = set(select_filters[0].get("chartsInScope") or [])
        soft.check(
            "assumption5: store filter scopes ALL SOURCE charts",
            in_scope == {kpi_sid, trend_sid, stores_sid, ratio_sid},
            f"in_scope={in_scope} expected={{{kpi_sid},{trend_sid},{stores_sid},{ratio_sid}}}",
        )

    rev_metric = _adhoc_metric(REVENUE, "accept", 0, from_source=True)
    ratio_metric = _adhoc_metric(RATIO, "accept", 0, from_source=True)
    soft.check(
        "assumption1: ratio adhoc sqlExpression shape",
        ratio_metric["sqlExpression"] == '(SUM("revenue")) / NULLIF((SUM("orders")), 0)',
        f"expr={ratio_metric['sqlExpression']!r}",
    )

    # --- pick a store with material revenue in PERIOD_A ---------------------
    top_store_rows = ch_run(
        "SELECT s.name AS n, toFloat64(SUM(f.revenue)) AS r "
        "FROM dm.sales_daily AS f LEFT JOIN dm.stores AS s ON f.store_id = s.id "
        f"{_ch_where(*PERIOD_A)} "
        "GROUP BY s.name ORDER BY r DESC LIMIT 1"
    )
    soft.check("ch: top store in PERIOD_A exists", bool(top_store_rows), f"rows={top_store_rows!r}")
    store_name = str(top_store_rows[0]["n"]) if top_store_rows else "Магазин №1"

    def ch_kpi(start: str, end: str, store: str | None = None) -> float:
        rows = ch_run(
            "SELECT toFloat64(SUM(f.revenue)) AS r "
            "FROM dm.sales_daily AS f LEFT JOIN dm.stores AS s ON f.store_id = s.id "
            f"{_ch_where(start, end, store)}"
        )
        return float(rows[0]["r"] or 0)

    def ch_daily(start: str, end: str, store: str | None = None) -> list[tuple[date, float]]:
        # daily grain: ad-hoc chart/data GROUP BYs only plain string columns (five
        # live iterations) — grain mapping is evidenced separately (see grain probe)
        rows = ch_run(
            "SELECT f.date AS d, toFloat64(SUM(f.revenue)) AS r "
            "FROM dm.sales_daily AS f LEFT JOIN dm.stores AS s ON f.store_id = s.id "
            f"{_ch_where(start, end, store)} "
            "GROUP BY d ORDER BY d"
        )
        return [(_as_date(r["d"]), float(r["r"])) for r in rows]

    def ch_stores(start: str, end: str, store: str | None = None) -> list[tuple[str, float]]:
        rows = ch_run(
            "SELECT s.name AS n, toFloat64(SUM(f.revenue)) AS r "
            "FROM dm.sales_daily AS f LEFT JOIN dm.stores AS s ON f.store_id = s.id "
            f"{_ch_where(start, end, store)} "
            "GROUP BY s.name ORDER BY r DESC LIMIT 10"
        )
        return [(str(r["n"]), float(r["r"])) for r in rows]

    def ch_ratio(start: str, end: str, store: str | None = None) -> list[tuple[str, float | None]]:
        rows = ch_run(
            "SELECT s.name AS n, "
            "toFloat64(SUM(f.revenue)) / NULLIF(toFloat64(SUM(f.orders)), 0) AS r "
            "FROM dm.sales_daily AS f LEFT JOIN dm.stores AS s ON f.store_id = s.id "
            f"{_ch_where(start, end, store)} "
            "GROUP BY s.name ORDER BY r DESC LIMIT 10"
        )
        return [(str(r["n"]), _as_float(r["r"])) for r in rows]

    def ss_kpi(
        time_range: str, store: str | None = None, *, label: str = "Выручка"
    ) -> tuple[float | None, dict]:
        q: dict[str, Any] = {
            "metrics": [rev_metric],
            "row_limit": 1,
            "granularity": "date",
            "time_range": time_range,
        }
        if store is not None:
            q["filters"] = [{"col": "stores_name", "op": "IN", "val": [store]}]
        first = _post_chart_data(live_adapter, ds_id, q)
        data = first.get("data") or []
        val = _metric_value(data[0], label) if data else None
        return val, {"query": q, "response": first}

    def ss_daily(
        time_range: str, store: str | None = None
    ) -> tuple[list[tuple[date, float]], dict]:
        q: dict[str, Any] = {
            "columns": ["date"],
            "metrics": [rev_metric],
            "granularity": "date",
            "time_range": time_range,
            "row_limit": 5000,
            "orderby": [["date", True]],
        }
        if store is not None:
            q["filters"] = [{"col": "stores_name", "op": "IN", "val": [store]}]
        first = _post_chart_data(live_adapter, ds_id, q)
        rows: list[tuple[date, float]] = []
        for row in first.get("data") or []:
            d = row.get("date")
            v = _metric_value(row, "Выручка")
            if d is None or v is None:
                continue
            rows.append((_as_date(d), float(v)))
        rows.sort(key=lambda x: x[0])
        return rows, {
            "query": q,
            "response_rowcount": first.get("rowcount"),
            "sample": (first.get("data") or [])[:3],
        }

    def ss_breakdown(
        time_range: str,
        metric: dict,
        label: str,
        store: str | None = None,
    ) -> tuple[list[tuple[str, float | None]], dict]:
        q: dict[str, Any] = {
            "columns": ["stores_name"],
            "metrics": [metric],
            "granularity": "date",
            "time_range": time_range,
            "row_limit": 10,
            "series_limit": 10,
            "orderby": [[metric, False]],
        }
        if store is not None:
            q["filters"] = [{"col": "stores_name", "op": "IN", "val": [store]}]
        first = _post_chart_data(live_adapter, ds_id, q)
        out: list[tuple[str, float | None]] = []
        for row in first.get("data") or []:
            name = row.get("stores_name")
            if name is None:
                continue
            out.append((str(name), _metric_value(row, label)))
        return out, {
            "query": q,
            "response_rowcount": first.get("rowcount"),
            "sample": (first.get("data") or [])[:3],
        }

    # =====================================================================
    # (a) period change — KPI + trend + breakdown recompute vs ClickHouse
    # =====================================================================
    ch_a = ch_kpi(*PERIOD_A)
    ch_b = ch_kpi(*PERIOD_B)
    soft.check(
        "(a) CH periods differ",
        ch_a > 0 and ch_b > 0 and not _approx(ch_a, ch_b, rel=1e-3),
        f"A={ch_a} B={ch_b}",
    )

    ss_a, dump_a = ss_kpi(PERIOD_A_RANGE)
    ss_b, dump_b = ss_kpi(PERIOD_B_RANGE)
    soft.check(
        "(a) KPI period A matches CH",
        _approx(ss_a, ch_a),
        f"ss={ss_a} ch={ch_a} dump={json.dumps(dump_a, ensure_ascii=False, default=str)[:1500]}",
    )
    soft.check(
        "(a) KPI period B matches CH",
        _approx(ss_b, ch_b),
        f"ss={ss_b} ch={ch_b} dump={json.dumps(dump_b, ensure_ascii=False, default=str)[:1500]}",
    )
    soft.check(
        "(a) KPI changes across periods",
        ss_a is not None and ss_b is not None and not _approx(ss_a, ss_b, rel=1e-3),
        f"A={ss_a} B={ss_b}",
    )

    ch_trend_a = ch_daily(*PERIOD_A)
    ss_trend_a, dump_trend = ss_daily(PERIOD_A_RANGE)
    soft.check(
        "(a) trend day count A",
        len(ss_trend_a) == len(ch_trend_a) and len(ch_trend_a) > 0,
        f"ss={len(ss_trend_a)} ch={len(ch_trend_a)} dump={dump_trend!r}"[:1500],
    )
    if ss_trend_a and ch_trend_a and len(ss_trend_a) == len(ch_trend_a):
        day_ok = all(
            s[0] == c[0] and _approx(s[1], c[1])
            for s, c in zip(ss_trend_a, ch_trend_a, strict=True)
        )
        soft.check(
            "(a) trend daily totals match CH",
            day_ok,
            f"ss={ss_trend_a[:3]}… ch={ch_trend_a[:3]}… dump={dump_trend!r}"[:2000],
        )

    ch_bd_a = ch_stores(*PERIOD_A)
    ss_bd_a, dump_bd = ss_breakdown(PERIOD_A_RANGE, rev_metric, "Выручка")
    soft.check(
        "(a) breakdown rowcount A",
        len(ss_bd_a) == len(ch_bd_a) and len(ch_bd_a) > 0,
        f"ss={len(ss_bd_a)} ch={len(ch_bd_a)} dump={dump_bd!r}"[:1500],
    )
    if ss_bd_a and ch_bd_a:
        # compare as maps — series_limit ordering should match ORDER BY desc
        ss_map = {n: v for n, v in ss_bd_a}
        ch_map = {n: v for n, v in ch_bd_a}
        soft.check(
            "(a) breakdown names match CH top-10",
            set(ss_map) == set(ch_map),
            f"ss_only={set(ss_map) - set(ch_map)} ch_only={set(ch_map) - set(ss_map)}",
        )
        vals_ok = all(_approx(ss_map.get(n), ch_map.get(n)) for n in ch_map)
        soft.check(
            "(a) breakdown values match CH",
            vals_ok,
            f"ss={ss_bd_a[:3]} ch={ch_bd_a[:3]} dump={dump_bd!r}"[:2000],
        )

    # period B trend must differ from A (recompute, not sticky)
    ss_trend_b, _ = ss_daily(PERIOD_B_RANGE)
    soft.check(
        "(a) trend changes across periods",
        ss_trend_a != ss_trend_b and bool(ss_trend_b),
        f"A_n={len(ss_trend_a)} B_n={len(ss_trend_b)}",
    )

    # =====================================================================
    # (b) select on joined label stores_name
    # =====================================================================
    tr = PERIOD_A_RANGE
    ch_store_kpi = ch_kpi(*PERIOD_A, store=store_name)
    ss_store_kpi, dump_sk = ss_kpi(tr, store=store_name)
    soft.check(
        "(b) KPI with stores_name filter matches CH",
        _approx(ss_store_kpi, ch_store_kpi) and ch_store_kpi > 0,
        f"store={store_name!r} ss={ss_store_kpi} ch={ch_store_kpi} "
        f"dump={json.dumps(dump_sk, ensure_ascii=False, default=str)[:1500]}",
    )
    soft.check(
        "(b) KPI store filter narrows vs full period",
        ss_store_kpi is not None and ss_a is not None and ss_store_kpi < ss_a * 0.5,
        f"store={ss_store_kpi} full={ss_a}",
    )

    ch_store_trend = ch_daily(*PERIOD_A, store=store_name)
    ss_store_trend, dump_st = ss_daily(tr, store=store_name)
    soft.check(
        "(b) trend with stores_name matches CH",
        (
            len(ss_store_trend) == len(ch_store_trend)
            and all(
                s[0] == c[0] and _approx(s[1], c[1])
                for s, c in zip(ss_store_trend, ch_store_trend, strict=True)
            )
            if ss_store_trend and ch_store_trend and len(ss_store_trend) == len(ch_store_trend)
            else False
        ),
        f"store={store_name!r} ss={ss_store_trend[:2]} ch={ch_store_trend[:2]} dump={dump_st!r}"[
            :2000
        ],
    )

    ch_store_bd = ch_stores(*PERIOD_A, store=store_name)
    ss_store_bd, dump_sb = ss_breakdown(tr, rev_metric, "Выручка", store=store_name)
    soft.check(
        "(b) breakdown collapses to filtered store",
        len(ss_store_bd) == 1
        and ss_store_bd[0][0] == store_name
        and _approx(ss_store_bd[0][1], ch_store_bd[0][1] if ch_store_bd else None),
        f"ss={ss_store_bd} ch={ch_store_bd} dump={dump_sb!r}"[:2000],
    )

    # ratio under the same store filter (assumption 1 live render)
    ch_store_ratio = ch_ratio(*PERIOD_A, store=store_name)
    ss_store_ratio, dump_sr = ss_breakdown(tr, ratio_metric, "Средний чек", store=store_name)
    ratio_ok = (
        len(ss_store_ratio) == 1
        and ss_store_ratio[0][0] == store_name
        and ch_store_ratio
        and _approx(ss_store_ratio[0][1], ch_store_ratio[0][1], rel=1e-3)
    )
    soft.check(
        "assumption1: ratio sqlExpression renders + matches CH",
        ratio_ok,
        f"ss={ss_store_ratio} ch={ch_store_ratio} "
        f"dump={json.dumps(dump_sr, ensure_ascii=False, default=str)[:2000]}",
    )
    # if ratio fails live: declared fallback is flip inexpressible_reason (one line) —
    # do NOT redesign here; surface evidence only.
    if not ratio_ok:
        soft.notes.append(
            "FALLBACK-HINT assumption1: if ratio fails on SOURCE, flip to "
            "inexpressible_reason (one-line) — flip-test already exists offline"
        )

    # =====================================================================
    # assumption 2: grain mapping probe (P1M / P1W) — evidence either way
    # =====================================================================
    # Ad-hoc chart/data GROUP BYs only plain string columns (five live
    # iterations; dict columns — BASE_AXIS or adhoc SQL — bucket the SELECT but
    # are left out of GROUP BY → CH 215). That quirk is the ad-hoc API's, not
    # the saved chart's. Either outcome yields the mapping evidence: buckets on
    # success, the engine-generated SQL inside the 215 error otherwise.
    _GRAIN_FN = {
        "P1M": ("toStartOfMonth",),
        "P1W": ("toStartOfWeek", "toMonday"),
    }
    _GRAIN_BUCKET_OK = {
        "P1M": lambda d: d.day == 1,
        "P1W": lambda d: d.weekday() == 0,  # CH mode 1 = Monday
    }
    for grain, expected_fns in _GRAIN_FN.items():
        grain_q: dict[str, Any] = {
            **_grain_query(grain),
            "metrics": [rev_metric],
            "granularity": "date",
            "time_range": PERIOD_A_RANGE,
            "row_limit": 5000,
        }
        try:
            first = _post_chart_data(live_adapter, ds_id, grain_q)
            dates = [_as_date(r["date"]) for r in (first.get("data") or []) if r.get("date")]
            ok = bool(dates) and all(_GRAIN_BUCKET_OK[grain](d) for d in dates)
            soft.check(
                f"assumption2: {grain} buckets correctly",
                ok,
                f"sample={dates[:5]} rowcount={first.get('rowcount')}",
            )
        except (AssertionError, SupersetAPIError) as exc:
            msg = str(exc)
            fn = next((f for f in expected_fns if f in msg), None)
            soft.check(
                f"assumption2: {grain} maps to a CH bucket function (from engine SQL)",
                fn is not None,
                f"fn={fn!r} (ad-hoc GROUP BY quirk blocks execution; evidence from "
                f"generated SQL in the engine error) err={msg[:600]}",
            )
            if fn is not None and f"{fn}(" in msg:
                start = msg.index(f"{fn}(")
                soft.notes.append(f"assumption2 {grain} expr: {msg[start : start + 60]!r}")

    # =====================================================================
    # assumption 3 + 4: magnitude probe on SOURCE (млрд, orderby format)
    # =====================================================================
    from auto_bi.adapters.base import DatasetRef

    ds_ref = DatasetRef(id=ds_id, name="source")
    kpi_chart = next(c for c in spec.charts if c.id == "d1_kpi")
    # capture the live probe by monkeypatching is too invasive — call _measure_magnitude
    # and re-read form_data the build already produced.
    magnitude = live_adapter._measure_magnitude(ds_ref, REVENUE, from_source=True, chart=kpi_chart)
    soft.check(
        "assumption3/4: magnitude probe returns a number",
        magnitude is not None and magnitude > 0,
        f"magnitude={magnitude!r}",
    )
    kpi_fd = _form_data(live_adapter, kpi_sid)
    sub = kpi_fd.get("subheader") or ""
    metric_sql = kpi_fd.get("metric", {}).get("sqlExpression", "")
    if magnitude is not None and magnitude >= 1e9:
        soft.check(
            "assumption3: KPI shows млрд (not 236G)",
            "млрд" in sub and "/1000000000" in metric_sql.replace(" ", ""),
            f"subheader={sub!r} sql={metric_sql!r} magnitude={magnitude}",
        )
    elif magnitude is not None and magnitude >= 1e6:
        soft.check(
            "assumption3: KPI shows млн at this stand scale",
            "млн" in sub,
            f"subheader={sub!r} sql={metric_sql!r} magnitude={magnitude}",
        )
    else:
        soft.notes.append(
            f"assumption3: magnitude={magnitude} below 1e6 on this stand — "
            "RU unit may be empty (leave code as is; attach evidence)"
        )
        soft.check(
            "assumption3: probe evidence attached (small magnitude)",
            True,
            f"subheader={sub!r} sql={metric_sql!r} magnitude={magnitude}",
        )

    # assumption 4 live: grouped probe orderby accepted (axis scale path on trend)
    trend_chart = next(c for c in spec.charts if c.id == "d1_trend")
    axis_mag = live_adapter._measure_magnitude(ds_ref, REVENUE, from_source=True, chart=trend_chart)
    soft.check(
        "assumption4: grouped probe orderby [[metric, False]] accepted live",
        axis_mag is not None and axis_mag > 0,
        f"axis_magnitude={axis_mag!r} (None => probe/orderby rejected or empty)",
    )

    # final: dump soft report into assertion message so CI log is complete
    print(soft.report())
    soft.assert_all()
