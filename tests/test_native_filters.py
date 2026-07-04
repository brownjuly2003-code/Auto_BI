"""Native dashboard filter compilation (scope-to-applicable).

The live round-trip against the pinned 4.1 stand lives in test_superset_contract.py
(native_filter_configuration); these are the offline shape/scope assertions.
"""

from auto_bi.adapters.superset.native_filters import (
    _select_default_mask,
    _time_default_mask,
    build_native_filter_configuration,
    participating_chart_ids,
    superset_time_range,
)
from auto_bi.agent.sqlgen import generate_chart_sql
from auto_bi.ir.spec import (
    ChartQuery,
    ChartSpec,
    DashboardFilter,
    DashboardSpec,
    Measure,
    OrderBy,
    Viz,
)
from auto_bi.semantic.model import Aggregation, SemanticModel

MODEL = SemanticModel.load("semantic/model.yaml")

REVENUE = Measure(column="revenue", agg=Aggregation.SUM)


def _spec(filters: list[DashboardFilter]) -> DashboardSpec:
    return DashboardSpec(
        title="t",
        filters=filters,
        charts=[
            ChartSpec(
                id="kpi",
                title="Итог",
                viz=Viz.BIG_NUMBER,
                query=ChartQuery(table="dm.sales_daily", measures=[REVENUE]),
            ),
            ChartSpec(
                id="by_store",
                title="По магазинам",
                viz=Viz.BAR,
                query=ChartQuery(
                    table="dm.sales_daily",
                    dimensions=["store_id"],
                    measures=[REVENUE],
                    order_by=[OrderBy(by="revenue", dir="desc")],
                    limit=10,
                ),
            ),
            ChartSpec(
                id="by_day",
                title="По дням",
                viz=Viz.LINE,
                query=ChartQuery(table="dm.sales_daily", dimensions=["date"], measures=[REVENUE]),
            ),
        ],
    )


# slice id, dataset id per chart (kpi, by_store, by_day)
def _placements(spec: DashboardSpec) -> list[tuple]:
    ids = {"kpi": (101, 11), "by_store": (102, 12), "by_day": (103, 13)}
    return [(c, *ids[c.id]) for c in spec.charts]


def test_select_filter_scopes_to_charts_exposing_the_column() -> None:
    spec = _spec([DashboardFilter(column="dm.sales_daily.store_id", type="value")])
    config, applied = build_native_filter_configuration(spec, _placements(spec), MODEL)

    assert len(config) == 1
    f = config[0]
    assert f["filterType"] == "filter_select"
    assert f["name"] == "ID магазина (dm.stores.id)"  # from the model description
    assert f["targets"] == [{"datasetId": 12, "column": {"name": "store_id"}}]
    assert f["chartsInScope"] == [102]  # only the by_store chart has store_id in grain
    assert sorted(f["scope"]["excluded"]) == [101, 103]  # kpi + by_day excluded
    assert applied[0][1] == [102]


def test_time_filter_uses_filter_time_and_empty_target() -> None:
    spec = _spec([DashboardFilter(column="dm.sales_daily.date", type="time_range")])
    config, _ = build_native_filter_configuration(spec, _placements(spec), MODEL)

    assert len(config) == 1
    f = config[0]
    assert f["filterType"] == "filter_time"  # role=time -> time filter, ignores df.type
    assert f["name"] == "День продажи"
    assert f["targets"] == [{}]
    assert f["chartsInScope"] == [103]  # only the line chart groups by date
    assert sorted(f["scope"]["excluded"]) == [101, 102]


def test_filter_not_in_any_grain_is_skipped() -> None:
    # manager_id is in no chart's grain -> cannot be wired as a native filter
    spec = _spec([DashboardFilter(column="dm.sales_daily.manager_id", type="value")])
    config, applied = build_native_filter_configuration(spec, _placements(spec), MODEL)
    assert config == []
    assert applied == []


def test_participating_charts_are_those_in_some_filter_scope() -> None:
    spec = _spec(
        [
            DashboardFilter(column="dm.sales_daily.store_id", type="value"),
            DashboardFilter(column="dm.sales_daily.date", type="time_range"),
        ]
    )
    assert participating_chart_ids(spec, MODEL) == {"by_store", "by_day"}


# --- B5: preconfigured period / default value (defaultDataMask) ---------------


def test_superset_time_range_titlecases_only_leading_last() -> None:
    # the LLM/CLI emits relative tokens lower-cased; Superset wants "Last …" title-cased
    assert superset_time_range("last quarter") == "Last quarter"
    assert superset_time_range("last 90 days") == "Last 90 days"
    # an already-valid token or an ISO range passes through untouched
    assert superset_time_range("Last quarter") == "Last quarter"
    assert superset_time_range("2026-01-01 : 2026-06-30") == "2026-01-01 : 2026-06-30"
    assert superset_time_range("  last month  ") == "Last month"


def test_time_default_mask_empty_is_neutral() -> None:
    # no default => the same empty mask as before (no preset, unchanged behavior)
    assert _time_default_mask("") == {"filterState": {}, "extraFormData": {}}
    assert _time_default_mask("   ") == {"filterState": {}, "extraFormData": {}}


def test_time_default_mask_preset_seeds_range_and_control() -> None:
    mask = _time_default_mask("last quarter")
    # extraFormData.time_range actually re-scopes the queries; filterState.value shows selected
    assert mask["extraFormData"] == {"time_range": "Last quarter"}
    assert mask["filterState"] == {"value": "Last quarter"}


def test_select_default_mask_empty_is_neutral() -> None:
    assert _select_default_mask("", "store_id") == {"filterState": {}, "extraFormData": {}}


def test_select_default_mask_preset_pins_single_value() -> None:
    mask = _select_default_mask("12", "store_id")
    assert mask["extraFormData"] == {"filters": [{"col": "store_id", "op": "IN", "val": ["12"]}]}
    assert mask["filterState"] == {"value": ["12"]}


def test_time_filter_default_populates_default_mask_end_to_end() -> None:
    # a DashboardFilter.default flows through build_native_filter_configuration into the wired
    # time filter's defaultDataMask (was an empty {} before B5)
    spec = _spec(
        [DashboardFilter(column="dm.sales_daily.date", type="time_range", default="last quarter")]
    )
    config, _ = build_native_filter_configuration(spec, _placements(spec), MODEL)
    assert config[0]["defaultDataMask"] == {
        "extraFormData": {"time_range": "Last quarter"},
        "filterState": {"value": "Last quarter"},
    }


def test_select_filter_default_populates_default_mask_end_to_end() -> None:
    spec = _spec([DashboardFilter(column="dm.sales_daily.store_id", type="value", default="12")])
    config, _ = build_native_filter_configuration(spec, _placements(spec), MODEL)
    assert config[0]["defaultDataMask"]["extraFormData"] == {
        "filters": [{"col": "store_id", "op": "IN", "val": ["12"]}]
    }


def test_apply_limit_false_drops_trailing_limit() -> None:
    query = ChartQuery(
        table="dm.sales_daily",
        dimensions=["store_id"],
        measures=[REVENUE],
        order_by=[OrderBy(by="revenue", dir="desc")],
        limit=10,
    )
    assert "LIMIT 10" in generate_chart_sql(query)
    assert "LIMIT" not in generate_chart_sql(query, apply_limit=False).upper()
