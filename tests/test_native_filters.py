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


def test_select_filter_scopes_to_source_charts() -> None:
    # D-1: all three charts are plain aggregates -> SOURCE on one shared dataset, so a
    # store_id control reaches every chart (the source carries all mart columns)
    spec = _spec([DashboardFilter(column="dm.sales_daily.store_id", type="value")])
    config, applied = build_native_filter_configuration(spec, _placements(spec), MODEL)

    assert len(config) == 1
    f = config[0]
    assert f["filterType"] == "filter_select"
    assert f["name"] == "ID магазина (dm.stores.id)"  # from the model description
    # first in-scope placement is the KPI (dataset 11) — shared source in a real build
    assert f["targets"] == [{"datasetId": 11, "column": {"name": "store_id"}}]
    assert f["chartsInScope"] == [101, 102, 103]
    assert f["scope"]["excluded"] == []
    assert applied[0][1] == [101, 102, 103]


def test_time_filter_uses_filter_time_and_empty_target() -> None:
    spec = _spec([DashboardFilter(column="dm.sales_daily.date", type="time_range")])
    config, _ = build_native_filter_configuration(spec, _placements(spec), MODEL)

    assert len(config) == 1
    f = config[0]
    assert f["filterType"] == "filter_time"  # role=time -> time filter, ignores df.type
    assert f["name"] == "День продажи"
    assert f["targets"] == [{}]
    assert f["chartsInScope"] == [101, 102, 103]  # all SOURCE charts on the mart
    assert f["scope"]["excluded"] == []


def test_filter_on_other_mart_is_skipped() -> None:
    # a control on a mart no chart reads cannot be wired (nothing exposes the column)
    spec = _spec([DashboardFilter(column="dm.other_fact.region_id", type="value")])
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
    # D-1: SOURCE charts on the mart all participate
    assert participating_chart_ids(spec, MODEL) == {"kpi", "by_store", "by_day"}


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


def test_qualified_filter_scope_does_not_confuse_same_bare_name() -> None:
    """Audit 19.07 finding #1: dm.products.name must not scope a chart on dm.stores.name.

    Both alias to bare `name`; pre-D-1 compared column_alias only and wrongly included
    the stores chart in a products filter's scope.
    """
    from auto_bi.adapters.superset.native_filters import grain_exposes_column
    from auto_bi.ir.spec import JoinSpec, MeasureTransform

    # OWN chart: window transform forces OWN role; grain is dm.stores.name only
    own = ChartSpec(
        id="by_store_name",
        title="По магазинам",
        viz=Viz.BAR,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["dm.stores.name"],
            measures=[
                Measure(
                    column="revenue",
                    agg=Aggregation.SUM,
                    transform=MeasureTransform.SHARE_OF_TOTAL,
                )
            ],
            joins=[
                JoinSpec(
                    table="dm.stores",
                    on_left="dm.sales_daily.store_id",
                    on_right="dm.stores.id",
                )
            ],
        ),
    )
    assert grain_exposes_column(own, "dm.stores.name") is True
    assert grain_exposes_column(own, "dm.products.name") is False

    # SOURCE chart with the same grain still must not match products.name via bare alias
    source = ChartSpec(
        id="by_store_plain",
        title="Магазины",
        viz=Viz.BAR,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["dm.stores.name"],
            measures=[REVENUE],
            joins=[
                JoinSpec(
                    table="dm.stores",
                    on_left="dm.sales_daily.store_id",
                    on_right="dm.stores.id",
                )
            ],
        ),
    )
    spec = DashboardSpec(
        title="t",
        filters=[DashboardFilter(column="dm.products.name", type="value")],
        charts=[source, own],
    )
    # products.name is not on the sales_daily source dataset (no products join) and not
    # in either chart's grain as a qualified match -> filter is skipped entirely
    config, applied = build_native_filter_configuration(spec, _placements_custom(spec), MODEL)
    assert config == []
    assert applied == []


def _placements_custom(spec: DashboardSpec) -> list[tuple]:
    return [(c, 200 + i, 50 + i) for i, c in enumerate(spec.charts)]


def test_own_chart_stays_out_of_source_filter_scope() -> None:
    """An OWN (window) chart is scoped only by its grain, not by the shared source."""
    from auto_bi.ir.spec import MeasureTransform

    own = ChartSpec(
        id="share",
        title="Доля",
        viz=Viz.BAR,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["store_id"],
            measures=[
                Measure(
                    column="revenue",
                    agg=Aggregation.SUM,
                    transform=MeasureTransform.SHARE_OF_TOTAL,
                )
            ],
        ),
    )
    source_kpi = ChartSpec(
        id="kpi",
        title="Итог",
        viz=Viz.BIG_NUMBER,
        query=ChartQuery(table="dm.sales_daily", measures=[REVENUE]),
    )
    spec = DashboardSpec(
        title="t",
        filters=[DashboardFilter(column="dm.sales_daily.date", type="time_range")],
        charts=[source_kpi, own],
    )
    placements = [(source_kpi, 101, 11), (own, 102, 12)]
    config, _ = build_native_filter_configuration(spec, placements, MODEL)
    assert config[0]["chartsInScope"] == [101]  # KPI only; OWN bar has no date in grain
    assert config[0]["scope"]["excluded"] == [102]


def _flagship_joined_filter_spec() -> DashboardSpec:
    """Finding 1 repro: filter on joined label column with mixed SOURCE charts."""
    from auto_bi.ir.spec import JoinSpec

    stores_join = JoinSpec(
        table="dm.stores", on_left="dm.sales_daily.store_id", on_right="dm.stores.id"
    )
    return DashboardSpec(
        title="t",
        filters=[DashboardFilter(column="dm.stores.name", type="value")],
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
                    dimensions=["dm.stores.name"],
                    measures=[REVENUE],
                    joins=[stores_join],
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


def test_joined_filter_scopes_all_source_charts_and_binds_unique_alias() -> None:
    """Finding 1+2: joined filter wires every SOURCE chart; binds stores_name not name."""
    from auto_bi.agent.dataset_plan import chart_accepts_filter, plan_datasets
    from auto_bi.agent.machine import spec_summary

    spec = _flagship_joined_filter_spec()
    plan = plan_datasets(spec)
    filt = spec.filters[0]
    # every chart is SOURCE and the source dataset exposes stores.name via the join
    for c in spec.charts:
        assert chart_accepts_filter(c, filt, spec, plan, MODEL) is True

    placements = _placements_custom(spec)
    config, applied = build_native_filter_configuration(spec, placements, MODEL)
    assert len(config) == 1
    assert config[0]["chartsInScope"] == [sid for _, sid, _ in placements]
    assert config[0]["targets"][0]["column"]["name"] == "stores_name"
    assert applied[0][1] == config[0]["chartsInScope"]

    # preview names exactly the same charts the wiring scopes
    summary = spec_summary(spec, MODEL)
    assert "не применим" not in summary
    assert "применяется к" in summary
    for title in ("Итог", "По магазинам", "По дням"):
        assert title in summary.split("применяется к")[1].split("\n")[0]


def test_preview_matches_wiring_mart_joined_and_nothing() -> None:
    """Finding 1 acceptance: (a) mart filter (b) joined filter (c) reaches nothing."""
    from auto_bi.agent.dataset_plan import chart_accepts_filter, plan_datasets
    from auto_bi.agent.machine import spec_summary

    model = MODEL

    def scoped_titles(spec: DashboardSpec) -> list[str]:
        plan = plan_datasets(spec)
        filt = spec.filters[0]
        return [c.title for c in spec.charts if chart_accepts_filter(c, filt, spec, plan, model)]

    def preview_applies(spec: DashboardSpec) -> list[str] | None:
        summary = spec_summary(spec, model)
        if "не применим ни к одному чарту" in summary:
            return None
        # line like:  ⛃ фильтр … → применяется к: A, B; не затрагивает: C
        for line in summary.splitlines():
            if "применяется к:" in line:
                part = line.split("применяется к:")[1]
                applies = part.split(";")[0]
                return [t.strip() for t in applies.split(",") if t.strip()]
        return []

    # (a) mart-column filter on all-SOURCE charts
    mart = _spec([DashboardFilter(column="dm.sales_daily.store_id", type="value")])
    assert preview_applies(mart) == scoped_titles(mart) == ["Итог", "По магазинам", "По дням"]
    config, _ = build_native_filter_configuration(mart, _placements(mart), model)
    assert config[0]["chartsInScope"] == [101, 102, 103]
    assert config[0]["targets"][0]["column"]["name"] == "store_id"

    # (b) joined-column filter
    joined = _flagship_joined_filter_spec()
    assert preview_applies(joined) == scoped_titles(joined)
    assert set(scoped_titles(joined)) == {"Итог", "По магазинам", "По дням"}

    # (c) filter reaches nothing
    nothing = _spec([DashboardFilter(column="dm.other_fact.region_id", type="value")])
    assert preview_applies(nothing) is None
    assert scoped_titles(nothing) == []
    config, applied = build_native_filter_configuration(nothing, _placements(nothing), model)
    assert config == [] and applied == []


def test_own_joined_grain_excluded_when_alias_mismatches_bound() -> None:
    """Finding 2 trap: OWN chart groups by dm.stores.name (dataset col ``name``) but
    the filter binds ``stores_name`` on the source dataset — OWN is excluded.
    Mart-column OWN grain still accepts (alias matches)."""
    from auto_bi.agent.dataset_plan import (
        chart_accepts_filter,
        filter_preview_notes,
        plan_datasets,
    )
    from auto_bi.ir.spec import JoinSpec, MeasureTransform

    stores_join = JoinSpec(
        table="dm.stores", on_left="dm.sales_daily.store_id", on_right="dm.stores.id"
    )
    own_joined = ChartSpec(
        id="own_stores",
        title="Доля по магазинам",
        viz=Viz.BAR,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["dm.stores.name"],
            measures=[
                Measure(
                    column="revenue",
                    agg=Aggregation.SUM,
                    transform=MeasureTransform.SHARE_OF_TOTAL,
                )
            ],
            joins=[stores_join],
        ),
    )
    own_mart = ChartSpec(
        id="own_store_id",
        title="Доля по store_id",
        viz=Viz.BAR,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["store_id"],
            measures=[
                Measure(
                    column="revenue",
                    agg=Aggregation.SUM,
                    transform=MeasureTransform.SHARE_OF_TOTAL,
                )
            ],
        ),
    )
    source = ChartSpec(
        id="by_store",
        title="Магазины",
        viz=Viz.BAR,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["dm.stores.name"],
            measures=[REVENUE],
            joins=[stores_join],
        ),
    )

    # direction 1: joined filter — OWN grain matches but alias diverges
    joined_filt = DashboardFilter(column="dm.stores.name", type="value")
    joined_spec = DashboardSpec(
        title="t", filters=[joined_filt], charts=[source, own_joined, own_mart]
    )
    plan = plan_datasets(joined_spec)
    assert chart_accepts_filter(source, joined_filt, joined_spec, plan, MODEL) is True
    assert chart_accepts_filter(own_joined, joined_filt, joined_spec, plan, MODEL) is False
    assert chart_accepts_filter(own_mart, joined_filt, joined_spec, plan, MODEL) is False
    placements = [
        (source, 201, 51),
        (own_joined, 202, 52),
        (own_mart, 203, 53),
    ]
    config, _ = build_native_filter_configuration(joined_spec, placements, MODEL)
    assert config[0]["chartsInScope"] == [201]
    assert config[0]["targets"][0]["column"]["name"] == "stores_name"
    notes = filter_preview_notes(joined_spec, MODEL)
    assert any("stores_name" in n and "Доля по магазинам" in n for n in notes)

    # direction 2: mart filter — OWN with store_id grain accepts (aliases match)
    mart_filt = DashboardFilter(column="dm.sales_daily.store_id", type="value")
    mart_spec = DashboardSpec(title="t", filters=[mart_filt], charts=[source, own_joined, own_mart])
    plan = plan_datasets(mart_spec)
    assert chart_accepts_filter(source, mart_filt, mart_spec, plan, MODEL) is True
    assert chart_accepts_filter(own_joined, mart_filt, mart_spec, plan, MODEL) is False
    assert chart_accepts_filter(own_mart, mart_filt, mart_spec, plan, MODEL) is True
    config, _ = build_native_filter_configuration(mart_spec, placements, MODEL)
    assert set(config[0]["chartsInScope"]) == {201, 203}
    assert config[0]["targets"][0]["column"]["name"] == "store_id"
