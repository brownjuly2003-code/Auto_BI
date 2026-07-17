"""P1-6 semantic governance: rates/non-additive columns must not be summed, and the
advisor's scan fraction must not divide live evidence by a stale modeled row count."""

import re

import pytest

from auto_bi.advisor import Advisor
from auto_bi.agent.autospec import build_auto_spec
from auto_bi.introspect.base import rate_like
from auto_bi.introspect.clickhouse import ClickHouseIntrospector
from auto_bi.introspect.greenplum import _role_for as gp_role_for
from auto_bi.ir.spec import ChartQuery, ChartSpec, DashboardSpec, Measure, MeasureTransform, Viz
from auto_bi.ir.validate import validate_spec
from auto_bi.semantic.model import (
    Additivity,
    Aggregation,
    Column,
    ColumnRole,
    Physical,
    SemanticModel,
    Table,
)
from auto_bi.semantic.render import render_table
from tests.test_api import make_client
from tests.test_machine import ScriptedLLM

# --- the shared name heuristic -----------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "return_rate",
        "rate",
        "effective_tax_rate",
        "pct_returned",
        "conversion_ratio",
        "market_share",
        "percent_female",
        "price",
        "unit_price",
        "Return_Rate",
    ],
)
def test_rate_like_matches_whole_tokens(name) -> None:
    assert rate_like(name)


@pytest.mark.parametrize(
    "name",
    ["revenue", "rated_power", "priceless", "total_price", "orders", "shares_outstanding"],
)
def test_rate_like_rejects_lookalikes(name) -> None:
    assert not rate_like(name)


# --- introspectors: draft marks rates non-additive + stamps stats ------------------------

_TABLES = [
    {
        "name": "returns_daily",
        "engine": "MergeTree",
        "sorting_key": "id",
        "partition_key": "",
        "total_rows": 100,
        "total_bytes": 1000,
        "comment": "",
    }
]
_COLUMNS = [
    {"table": "returns_daily", "name": "id", "type": "UInt32", "comment": ""},
    {"table": "returns_daily", "name": "revenue", "type": "Decimal(18, 2)", "comment": ""},
    {"table": "returns_daily", "name": "return_rate", "type": "Float64", "comment": ""},
]


def _ch_run_query(sql: str) -> list[dict]:
    if "system.tables" in sql:
        return _TABLES
    if "system.columns" in sql:
        return _COLUMNS
    if "uniqCombined" in sql:
        return [{"id": 100}]
    raise AssertionError(f"unexpected query: {sql}")


def test_ch_introspector_rate_column_defaults_to_avg_non_additive() -> None:
    table = ClickHouseIntrospector(_ch_run_query).introspect("dm").table("dm.returns_daily")
    rate = table.column("return_rate")
    assert (rate.agg, rate.additivity) == (Aggregation.AVG, Additivity.NON_ADDITIVE)
    revenue = table.column("revenue")
    assert (revenue.agg, revenue.additivity) == (Aggregation.SUM, None)


def test_ch_introspector_stamps_captured_at() -> None:
    table = ClickHouseIntrospector(_ch_run_query).introspect("dm").table("dm.returns_daily")
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", table.physical.captured_at)


def test_gp_role_heuristic_mirrors_ch() -> None:
    assert gp_role_for("return_rate", "numeric(5,4)") == (
        ColumnRole.MEASURE,
        Aggregation.AVG,
        Additivity.NON_ADDITIVE,
    )
    assert gp_role_for("revenue", "numeric(12,2)") == (
        ColumnRole.MEASURE,
        Aggregation.SUM,
        None,
    )


# --- model round-trip --------------------------------------------------------------------


def test_additivity_survives_yaml_round_trip(tmp_path) -> None:
    model = SemanticModel(
        tables=[
            Table(
                name="dm.t",
                columns=[
                    Column(
                        name="return_rate",
                        type="Float64",
                        role=ColumnRole.MEASURE,
                        agg=Aggregation.AVG,
                        additivity=Additivity.NON_ADDITIVE,
                    ),
                    Column(
                        name="revenue",
                        type="Float64",
                        role=ColumnRole.MEASURE,
                        agg=Aggregation.SUM,
                    ),
                ],
            )
        ]
    )
    path = tmp_path / "model.yaml"
    model.dump(path)
    text = path.read_text(encoding="utf-8")
    assert "additivity: non_additive" in text
    assert text.count("additivity") == 1  # unset stays out of the yaml (hand-editable)
    reloaded = SemanticModel.load(path)
    assert reloaded.table("dm.t").column("return_rate").additivity == Additivity.NON_ADDITIVE
    assert reloaded.table("dm.t").column("revenue").additivity is None


# --- spec validation: the model now protects business correctness ------------------------


def _rate_model(*, additivity: Additivity | None = Additivity.NON_ADDITIVE) -> SemanticModel:
    return SemanticModel(
        tables=[
            Table(
                name="dm.returns",
                grain=["date"],
                columns=[
                    Column(name="date", type="Date", role=ColumnRole.TIME),
                    Column(name="city", type="String", role=ColumnRole.DIMENSION),
                    Column(
                        name="return_rate",
                        type="Float64",
                        role=ColumnRole.MEASURE,
                        agg=Aggregation.AVG,
                        additivity=additivity,
                    ),
                    Column(
                        name="orders",
                        type="UInt32",
                        role=ColumnRole.MEASURE,
                        agg=Aggregation.SUM,
                    ),
                ],
                physical=Physical(engine="clickhouse", rows=1000),
            )
        ]
    )


def _spec(measure: Measure) -> DashboardSpec:
    return DashboardSpec(
        title="d",
        charts=[
            ChartSpec(
                id="c1",
                title="t",
                viz=Viz.BAR,
                query=ChartQuery(table="dm.returns", dimensions=["city"], measures=[measure]),
            )
        ],
    )


def test_sum_over_non_additive_column_is_rejected() -> None:
    errors = validate_spec(_spec(Measure(column="return_rate", agg=Aggregation.SUM)), _rate_model())
    assert len(errors) == 1
    assert "неаддитивной" in errors[0] and "return_rate" in errors[0]


@pytest.mark.parametrize("agg", [Aggregation.AVG, Aggregation.MIN, Aggregation.MAX])
def test_non_sum_aggs_over_non_additive_are_fine(agg) -> None:
    assert validate_spec(_spec(Measure(column="return_rate", agg=agg)), _rate_model()) == []


def test_sum_over_unannotated_column_is_untouched() -> None:
    # additivity unset => no constraint: pre-P1-6 models keep validating as before
    errors = validate_spec(
        _spec(Measure(column="return_rate", agg=Aggregation.SUM)), _rate_model(additivity=None)
    )
    assert errors == []


def test_ratio_denominator_gets_the_same_governance() -> None:
    ratio = Measure(
        column="orders",
        agg=Aggregation.SUM,
        denominator=Measure(column="return_rate", agg=Aggregation.SUM),
    )
    errors = validate_spec(_spec(ratio), _rate_model())
    assert len(errors) == 1 and "return_rate" in errors[0]


def test_semi_additive_is_recorded_not_enforced() -> None:
    errors = validate_spec(
        _spec(Measure(column="return_rate", agg=Aggregation.SUM)),
        _rate_model(additivity=Additivity.SEMI_ADDITIVE),
    )
    assert errors == []  # v1: enforcement needs the non-additive axis; intent only


# --- autospec: overview of a non-additive mart stays meaningful --------------------------


def _price_model() -> SemanticModel:
    return SemanticModel(
        tables=[
            Table(
                name="dm.products",
                grain=["id"],
                columns=[
                    Column(name="id", type="UInt32", role=ColumnRole.DIMENSION),
                    Column(name="category", type="String", role=ColumnRole.DIMENSION),
                    Column(
                        name="price",
                        type="Decimal(18, 2)",
                        role=ColumnRole.MEASURE,
                        additivity=Additivity.NON_ADDITIVE,  # deliberately no modeled agg
                    ),
                ],
                physical=Physical(
                    engine="clickhouse", rows=2000, cardinality={"id": 2000, "category": 12}
                ),
            )
        ]
    )


def test_autospec_non_additive_measure_defaults_to_avg_and_skips_share() -> None:
    spec = build_auto_spec(_price_model(), "dm.products")
    model = _price_model()
    assert validate_spec(spec, model) == []
    price_measures = [m for c in spec.charts for m in c.query.measures if m.column == "price"]
    assert price_measures and all(m.agg == Aggregation.AVG for m in price_measures)
    # a share of avg(price) divides by a sum of averages — no structure view for it
    assert not [
        m
        for c in spec.charts
        for m in c.query.measures
        if m.transform == MeasureTransform.SHARE_OF_TOTAL
    ]


# --- prompt render carries the flag ------------------------------------------------------


def test_render_marks_non_additive() -> None:
    line = render_table(_rate_model().table("dm.returns"))
    assert "return_rate (Float64, measure, avg, non_additive)" in line


# --- advisor: live denominator beats the git-frozen one ----------------------------------


def _stale_fact_model(rows: int = 20_000_000) -> SemanticModel:
    return SemanticModel(
        tables=[
            Table(
                name="dm.sales_daily",
                columns=[
                    Column(name="date", type="Date", role=ColumnRole.TIME),
                    Column(
                        name="revenue",
                        type="Decimal(18, 2)",
                        role=ColumnRole.MEASURE,
                        agg=Aggregation.SUM,
                    ),
                ],
                physical=Physical(
                    engine="clickhouse",
                    table_engine="MergeTree",
                    sorting_key=["date"],
                    rows=rows,  # the committed snapshot; the live table is 1M (demo drift)
                ),
            )
        ]
    )


def _kpi() -> ChartSpec:
    return ChartSpec(
        id="c1",
        title="t",
        viz=Viz.BIG_NUMBER,
        query=ChartQuery(
            table="dm.sales_daily",
            measures=[Measure(column="revenue", agg=Aggregation.SUM)],
        ),
    )


def test_scan_fraction_uses_live_row_count_over_stale_model() -> None:
    def run_query(sql: str) -> list[dict]:
        if sql.startswith("EXPLAIN ESTIMATE "):
            return [{"rows": 900_000, "marks": 10, "parts": 1}]
        assert "system.tables" in sql and "'dm'" in sql and "'sales_daily'" in sql
        return [{"total_rows": 1_000_000}]

    findings = Advisor(_stale_fact_model(), run_query=run_query).review_chart(_kpi())
    f = next(x for x in findings if x.rule == "explain_high_scan_fraction")
    # against the stale 20M the fraction would be 0.045 and the rule would stay silent
    assert f.evidence["total_rows"] == 1_000_000
    assert f.evidence["total_rows_source"] == "live"
    assert f.evidence["scan_fraction"] == 0.9


def test_scan_fraction_falls_back_to_model_rows() -> None:
    def run_query(sql: str) -> list[dict]:
        if sql.startswith("EXPLAIN ESTIMATE "):
            return [{"rows": 18_000_000, "marks": 10, "parts": 1}]
        raise RuntimeError("catalog unavailable")  # live count fails -> model fallback

    findings = Advisor(_stale_fact_model(), run_query=run_query).review_chart(_kpi())
    f = next(x for x in findings if x.rule == "explain_high_scan_fraction")
    assert f.evidence["total_rows"] == 20_000_000
    assert f.evidence["total_rows_source"] == "model"


def test_live_zero_rows_is_no_signal() -> None:
    # an empty/detached live table must not zero the denominator; fall back to the model
    def run_query(sql: str) -> list[dict]:
        if sql.startswith("EXPLAIN ESTIMATE "):
            return [{"rows": 18_000_000, "marks": 10, "parts": 1}]
        return [{"total_rows": 0}]

    findings = Advisor(_stale_fact_model(), run_query=run_query).review_chart(_kpi())
    f = next(x for x in findings if x.rule == "explain_high_scan_fraction")
    assert f.evidence["total_rows_source"] == "model"


# --- enrichment API refuses to hand-author the same mistake ------------------------------


def test_enrich_sum_on_non_additive_is_422(tmp_path) -> None:
    client = make_client(ScriptedLLM([]), _rate_model(), model_path=tmp_path / "model.yaml")
    url = "/api/v1/model/tables/dm.returns/columns/return_rate"
    denied = client.patch(url, json={"agg": "sum"})
    assert denied.status_code == 422
    assert "non_additive" in denied.json()["detail"]
    ok = client.patch(url, json={"agg": "avg"})
    assert ok.status_code == 200
    assert ok.json()["additivity"] == "non_additive"
