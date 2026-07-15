"""Deterministic insight layer ("Что видно") over a built overview — offline, no DWH.

The whole pass is deterministic, so a fake RunQuery returning crafted rows fully exercises
it: end-to-end on the committed demo model plus direct unit tests for each observation
branch and the RU number formatting. No stand needed.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from auto_bi.agent import insights as I
from auto_bi.agent.autospec import build_auto_spec
from auto_bi.agent.insights import Observation, analyze_spec
from auto_bi.ir.spec import (
    Aggregation,
    ChartQuery,
    ChartSpec,
    DashboardSpec,
    Measure,
    MeasureTransform,
    Viz,
)
from auto_bi.semantic.model import SemanticModel

MODEL = "semantic/model.yaml"  # committed demo model (repo root is pytest cwd)


def _fake_run_query(sql: str) -> list[dict]:
    """Route a chart's generated SQL to crafted rows by the column it selects.

    A rising 30-day revenue series with a single mid-series spike (day 15); a concentrated
    region ranking; a diffuse (flat) category ranking; a 3-row city ranking; a 3-way format
    share. KPI SQL (no dimension token) falls through to no rows.

    Dimension tokens are matched BEFORE the period-baked `"date"` WHERE (P1-1): every
    overview chart now carries `date >= addMonths(today(), -12)`, so a date-first check
    would steal region/category/city rankings and return an empty leader ("—").
    """
    if "share_of_total" in sql:
        return [
            {"format": "магазин у дома", "share_of_total_sum_revenue": 0.41},
            {"format": "супермаркет", "share_of_total_sum_revenue": 0.35},
            {"format": "гипермаркет", "share_of_total_sum_revenue": 0.24},
        ]
    if "region" in sql:
        return [
            {"region": r, "sum_revenue": float(v)}
            for r, v in [("Центр", 500), ("Юг", 100), ("Урал", 80), ("Сибирь", 60), ("СЗ", 40)]
        ]
    if "category" in sql:
        return [{"category": f"cat{i}", "sum_revenue": 10.0} for i in range(1, 11)]
    if "city" in sql:
        return [
            {"city": c, "sum_revenue": float(v)}
            for c, v in [("Самара", 300), ("Пермь", 200), ("Омск", 100)]
        ]
    if '"date"' in sql:
        return [
            {"date": f"2026-01-{i:02d}", "sum_revenue": (2000.0 if i == 15 else float(100 + 5 * i))}
            for i in range(1, 31)
        ]
    return []  # KPIs and anything else


def _by_chart(obs: list[Observation]) -> dict[str, list[Observation]]:
    out: dict[str, list[Observation]] = {}
    for o in obs:
        out.setdefault(o.chart_id, []).append(o)
    return out


# --- end-to-end on the real auto-overview ---------------------------------------------


def test_analyze_real_auto_overview_produces_expected_observations() -> None:
    model = SemanticModel.load(MODEL)
    spec = build_auto_spec(model, "dm.sales_daily")
    ins = analyze_spec(spec, model, _fake_run_query)

    per = _by_chart(ins.observations)

    # KPIs (auto1..auto4, big_number, no dimension — incl. the hero yoy KPI auto2) carry no
    # trend/ranking story
    for kpi in ("auto1", "auto2", "auto3", "auto4"):
        assert kpi not in per

    # the absolute dynamics line (auto5): a smoothed rising trend + the mid-series spike anomaly
    line = per["auto5"]
    trend = next(o for o in line if o.kind == "trend")
    assert trend.value == pytest.approx(122.7, abs=0.1) and "рост" in trend.text
    assert "+" in trend.text
    anomaly = next(o for o in line if o.kind == "anomaly")
    assert anomaly.subject == "2026-01-15" and anomaly.value == pytest.approx(2000.0)

    # the year-over-year view is now the compact hero KPI (auto2, a big_number skipped above), not
    # a percent line — so the layer never adds a muddled "trend of a rate" observation for it

    # concentrated ranking (region, auto6): leader + a top-3 concentration line
    region = per["auto6"]
    leader = next(o for o in region if o.kind == "leader")
    assert leader.subject == "Центр" and leader.value == pytest.approx(500.0)
    assert "64,1% видимой суммы" in leader.text
    assert any(
        o.kind == "concentration" and o.value == pytest.approx(87.2, abs=0.1) for o in region
    )


def test_diffuse_ranking_emits_leader_and_spread_not_concentration() -> None:
    # category is ten equal rows: top-3 = 30% (< 50%, > nothing) -> not "concentration"
    # but a genuine "spread" finding (the complement: no category dominates)
    model = SemanticModel.load(MODEL)
    spec = build_auto_spec(model, "dm.sales_daily")
    per = _by_chart(analyze_spec(spec, model, _fake_run_query).observations)

    category = per["auto7"]
    assert [o.kind for o in category] == ["leader", "spread"]
    assert category[0].subject == "cat1"
    assert "распределение ровное" in category[1].text and "30%" in category[1].text


def test_structure_chart_reports_largest_share() -> None:
    model = SemanticModel.load(MODEL)
    spec = build_auto_spec(model, "dm.sales_daily")
    per = _by_chart(analyze_spec(spec, model, _fake_run_query).observations)

    share = per["auto8"]
    assert [o.kind for o in share] == ["share_lead"]
    assert share[0].subject == "магазин у дома" and share[0].value == pytest.approx(41.0)
    assert "41%" in share[0].text


def test_running_share_bar_emits_no_share_lead() -> None:
    # a running_share bar is a CUMULATIVE Pareto: its max value is ~1.0 at the SMALLEST category,
    # so _observe_share would report a confidently-wrong "largest share: <smallest>, 100%". The
    # dispatcher skips it — the chart is itself the Pareto insight (audit LOW).
    model = SemanticModel.load(MODEL)
    chart = ChartSpec(
        id="rs",
        title="Парето по магазинам",
        viz=Viz.BAR,
        query=ChartQuery(
            table="dm.sales_daily",
            dimensions=["store_id"],
            measures=[
                Measure(
                    column="revenue", agg=Aggregation.SUM, transform=MeasureTransform.RUNNING_SHARE
                )
            ],
        ),
    )

    def rq(sql: str) -> list[dict]:
        # non-empty so the dispatch is reached; cumulative share rising to 1.0 at the smallest
        return [
            {"store_id": s, "running_share_sum_revenue": v}
            for s, v in [(1, 0.6), (2, 0.85), (3, 1.0)]
        ]

    ins = analyze_spec(DashboardSpec(title="d", charts=[chart]), model, rq)
    assert all(o.chart_id != "rs" for o in ins.observations)


def test_render_lists_each_observation_under_a_header() -> None:
    model = SemanticModel.load(MODEL)
    spec = build_auto_spec(model, "dm.sales_daily")
    ins = analyze_spec(spec, model, _fake_run_query)

    text = ins.render()
    assert text.splitlines()[0].startswith("Что видно")
    assert text.count("\n  — ") == len(ins.observations)
    assert not ins.is_empty


def test_pass_is_best_effort_a_failing_query_yields_no_observations() -> None:
    def boom(sql: str) -> list[dict]:
        raise RuntimeError("DWH down")

    model = SemanticModel.load(MODEL)
    spec = build_auto_spec(model, "dm.sales_daily")
    ins = analyze_spec(spec, model, boom)  # must not raise — advisory only

    assert ins.is_empty and ins.render() == ""


# --- direct branch coverage of the observation rules ----------------------------------


def _line(rows: list[dict]) -> list[Observation]:
    chart = ChartSpec(
        id="c",
        title="Динамика",
        viz=Viz.LINE,
        query=ChartQuery(
            table="t", dimensions=["d"], measures=[Measure(column="x", agg=Aggregation.SUM)]
        ),
    )
    return I._observe_line(chart, rows, "sum_x", "d")


def test_line_trend_falls_and_flat() -> None:
    falling = [{"d": i, "sum_x": float(300 - i)} for i in range(30)]
    obs = _line(falling)
    trend = next(o for o in obs if o.kind == "trend")
    assert (
        trend.value is not None
        and trend.value < 0
        and "снижение" in trend.text
        and "−" in trend.text
    )

    flat = [{"d": i, "sum_x": 100.0} for i in range(30)]
    trend2 = next(o for o in _line(flat) if o.kind == "trend")
    assert trend2.value == pytest.approx(0.0) and "почти без изменений" in trend2.text


def test_line_too_short_and_no_anomaly() -> None:
    assert _line([{"d": 0, "sum_x": 5.0}]) == []  # < 2 points
    # a clean monotone series has no point past mean+3σ -> no anomaly, just a trend
    obs = _line([{"d": i, "sum_x": float(100 + i)} for i in range(30)])
    assert [o.kind for o in obs] == ["trend"]


def test_line_reports_a_deep_dip_as_an_anomaly() -> None:
    # a flat ~200 series with one collapse day -> a "провал" anomaly (symmetric to the spike)
    rows = [{"d": i, "sum_x": (5.0 if i == 10 else 200.0)} for i in range(30)]
    obs = _line(rows)
    dip = next(o for o in obs if o.kind == "anomaly")
    assert dip.subject == "10" and dip.value == pytest.approx(5.0)
    assert "провал" in dip.text and "ниже среднего" in dip.text


def test_line_reports_a_reversal_when_the_second_half_turns() -> None:
    # rises for 15 days then falls back: the overall trend is ~flat, but the inflection is
    # the real story -> a "разворот" with a positive first half and a negative second half
    rows = [
        {"d": i, "sum_x": float(100 + 10 * i if i < 15 else 240 - 10 * (i - 15))} for i in range(30)
    ]
    obs = _line(rows)
    rev = next(o for o in obs if o.kind == "reversal")
    assert rev.value is not None and rev.value < 0  # second half is falling
    assert "разворот" in rev.text and "+" in rev.text and "−" in rev.text


def test_line_no_reversal_when_both_halves_move_together() -> None:
    # a steady climb has no opposite-direction halves -> trend only, no reversal
    obs = _line([{"d": i, "sum_x": float(100 + 5 * i)} for i in range(30)])
    assert "reversal" not in [o.kind for o in obs]


# --- momentum (change of pace: same direction, but the slope changes) ------------------


def test_line_steady_linear_growth_is_not_decelerating() -> None:
    # THE correctness guard: a linear climb has a CONSTANT slope but a falling percent each
    # half (the base grows). Pace is judged on slope, so this must read as a steady trend,
    # never as "рост замедляется".
    obs = _line([{"d": i, "sum_x": float(100 + 5 * i)} for i in range(30)])
    assert [o.kind for o in obs] == ["trend"]


def test_line_reports_decelerating_growth_as_momentum() -> None:
    # a steep climb (slope 20) then a gentle one (slope 8): same direction, but the pace drops
    rows = [
        {"d": i, "sum_x": float(100 + 20 * i if i < 15 else 400 + 8 * (i - 15))} for i in range(30)
    ]
    obs = _line(rows)
    assert "reversal" not in [o.kind for o in obs]  # same direction -> never a reversal
    m = next(o for o in obs if o.kind == "momentum")
    assert "рост замедляется" in m.text
    assert "+" in m.text and m.value is not None and m.value > 0


def test_line_reports_accelerating_growth_as_momentum() -> None:
    # a gentle climb (slope 2) then a steep one (slope 20): growth speeds up
    rows = [
        {"d": i, "sum_x": float(100 + 2 * i if i < 15 else 130 + 20 * (i - 15))} for i in range(30)
    ]
    m = next(o for o in _line(rows) if o.kind == "momentum")
    assert "рост ускоряется" in m.text


def test_line_reports_accelerating_decline_as_momentum() -> None:
    # a gentle fall (slope -8) then a steep one (slope -20): both halves down, decline speeds up
    rows = [
        {"d": i, "sum_x": float(1000 - 8 * i if i < 15 else 880 - 20 * (i - 15))} for i in range(30)
    ]
    m = next(o for o in _line(rows) if o.kind == "momentum")
    assert "снижение ускоряется" in m.text
    assert m.value is not None and m.value < 0


def test_line_no_momentum_when_a_half_is_flat() -> None:
    # the first half is flat (< the materiality floor): there is no two-paced story to tell
    rows = [{"d": i, "sum_x": (100.0 if i < 15 else float(100 + 30 * (i - 15)))} for i in range(30)]
    assert "momentum" not in [o.kind for o in _line(rows)]


# --- day-of-week seasonality ----------------------------------------------------------

_MONDAY = date(2026, 1, 5)  # twelve whole weeks from here → 12 samples per weekday


def _weekly(value, *, days: int = 84) -> list[dict]:
    """A dated daily series; `value(d)` maps each date to its measure."""
    dates = [_MONDAY + timedelta(days=i) for i in range(days)]
    return [{"d": d.isoformat(), "sum_x": value(d)} for d in dates]


def test_line_reports_weekday_seasonality_with_a_weekend_lift() -> None:
    # weekends run 40% above weekdays over twelve weeks -> a "сезонность" naming the peak day
    rows = _weekly(lambda d: 280.0 if d.weekday() >= 5 else 200.0)
    season = next(o for o in _line(rows) if o.kind == "seasonality")
    assert season.subject in ("суббота", "воскресенье")
    assert season.value is not None and season.value > 0
    assert "по дням недели" in season.text and "выше всего" in season.text


def test_line_seasonality_names_the_weakest_weekday_when_material() -> None:
    # weekends high, Monday low -> the peak AND a material trough are both named
    def value(d: date) -> float:
        if d.weekday() >= 5:
            return 300.0
        return 120.0 if d.weekday() == 0 else 200.0

    season = next(o for o in _line(_weekly(value)) if o.kind == "seasonality")
    assert "суббота" in season.text and "понедельник" in season.text
    assert "ниже всего" in season.text


def test_line_no_seasonality_without_a_weekday_signal() -> None:
    # a flat series has no weekday that stands out -> no seasonality
    assert "seasonality" not in [o.kind for o in _line(_weekly(lambda d: 200.0))]


def test_line_seasonality_is_robust_to_a_single_spike() -> None:
    # one huge spike day cannot manufacture a weekly pattern: the median over twelve weeks
    # ignores it (the spike still surfaces as an anomaly, just not as seasonality)
    rows = [
        {"d": (_MONDAY + timedelta(days=i)).isoformat(), "sum_x": (9000.0 if i == 20 else 200.0)}
        for i in range(84)
    ]
    assert "seasonality" not in [o.kind for o in _line(rows)]


def test_line_no_seasonality_when_too_few_weeks() -> None:
    # four weeks gives each weekday only four samples (< the stability floor) -> silent
    rows = _weekly(lambda d: 280.0 if d.weekday() >= 5 else 200.0, days=28)
    assert "seasonality" not in [o.kind for o in _line(rows)]


def test_line_no_seasonality_for_a_non_date_dimension() -> None:
    # integer x-labels are not dates -> the weekly story cannot be read, no seasonality
    rows = [{"d": i, "sum_x": float(200 + (80 if i % 7 >= 5 else 0))} for i in range(84)]
    assert "seasonality" not in [o.kind for o in _line(rows)]


def test_parse_date_accepts_dates_datetimes_and_iso_strings() -> None:
    assert I._parse_date(date(2026, 1, 15)).weekday() == 3  # a Thursday
    assert I._parse_date(datetime(2026, 1, 15, 9, 30)) == date(2026, 1, 15)
    assert I._parse_date("2026-01-15") == date(2026, 1, 15)
    assert I._parse_date("2026-01-15 00:00:00") == date(2026, 1, 15)  # trailing time tolerated
    assert I._parse_date(15) is None
    assert I._parse_date("nope") is None
    assert I._parse_date(None) is None


def _bar(rows: list[dict]) -> list[Observation]:
    chart = ChartSpec(
        id="c",
        title="Разрез",
        viz=Viz.BAR,
        query=ChartQuery(
            table="t", dimensions=["d"], measures=[Measure(column="x", agg=Aggregation.SUM)]
        ),
    )
    return I._observe_bar(chart, rows, "sum_x", "d")


def test_bar_total_non_positive_skips_share() -> None:
    obs = _bar([{"d": "a", "sum_x": 0.0}, {"d": "b", "sum_x": 0.0}])
    assert [o.kind for o in obs] == ["leader"] and "видимой суммы" not in obs[0].text


def test_bar_diffuse_distribution_reports_spread() -> None:
    # eight equal categories: top-3 = 37.5% (< 40%, ≥ 5 categories) -> "spread", not concentration
    obs = _bar([{"d": f"c{i}", "sum_x": 10.0} for i in range(8)])
    assert [o.kind for o in obs] == ["leader", "spread"]
    assert "распределение ровное" in obs[1].text and "37,5%" in obs[1].text


def test_bar_concentrated_distribution_reports_concentration_not_spread() -> None:
    # one category carries most of the total -> top-3 ≥ 50% -> "concentration"
    obs = _bar([{"d": "a", "sum_x": 90.0}] + [{"d": f"c{i}", "sum_x": 2.0} for i in range(5)])
    assert [o.kind for o in obs] == ["leader", "concentration"]
    assert "spread" not in [o.kind for o in obs]


def test_bar_few_categories_emit_neither_spread_nor_concentration() -> None:
    # three equal rows: below the 4-row floor for concentration and the 5-row floor for spread
    obs = _bar([{"d": "a", "sum_x": 10.0}, {"d": "b", "sum_x": 10.0}, {"d": "c", "sum_x": 10.0}])
    assert [o.kind for o in obs] == ["leader"]


def test_observe_chart_skips_table_viz() -> None:
    chart = ChartSpec(
        id="c",
        title="Детализация",
        viz=Viz.TABLE,
        query=ChartQuery(
            table="t", dimensions=["d"], measures=[Measure(column="x", agg=Aggregation.SUM)]
        ),
    )
    # a table is a detail grid, not a single headline -> no observation even with rows
    assert I._observe_chart(chart, lambda sql: [{"d": "a", "sum_x": 1.0}], "clickhouse") == []


def test_percent_measure_routes_to_share_branch() -> None:
    share_m = Measure(column="x", agg=Aggregation.SUM, transform=MeasureTransform.SHARE_OF_TOTAL)
    chart = ChartSpec(
        id="c",
        title="Доля",
        viz=Viz.BAR,
        query=ChartQuery(table="t", dimensions=["d"], measures=[share_m]),
    )
    rows = [{"d": "a", "share_of_total_sum_x": 0.6}, {"d": "b", "share_of_total_sum_x": 0.4}]
    obs = I._observe_chart(chart, lambda sql: rows, "clickhouse")
    assert [o.kind for o in obs] == ["share_lead"] and obs[0].subject == "a"


# --- RU number formatting -------------------------------------------------------------


def test_compact_magnitudes() -> None:
    assert I._compact(236.149e9) == f"236,1{I._SEP}млрд"
    assert I._compact(115e6) == f"115{I._SEP}млн"  # trailing ,0 stripped
    assert I._compact(3614) == f"3,6{I._SEP}тыс"
    assert I._compact(842) == "842"


def test_percent_and_sign() -> None:
    assert I._pct(40.25) == "40,2%"
    assert I._pct(12.0) == "12%"  # no stray ,0
    assert I._signed_pct(122.7) == "+122,7%"
    assert I._signed_pct(-4.0) == "−4%"
    assert I._signed_pct(0.0) == "0%"


def test_num_coercion_rejects_non_numeric_and_bool() -> None:
    assert I._num(None) is None
    assert I._num(True) is None  # a bool is not a measurement
    assert I._num("nope") is None
    assert I._num("3.5") == pytest.approx(3.5)
    assert I._num(7) == pytest.approx(7.0)
