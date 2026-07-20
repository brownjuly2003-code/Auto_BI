"""Deterministic insight layer over a built dashboard — the "Что видно" surface.

A read-only pass that runs each chart's SQL once and turns the *real* aggregates into a
few plain observations: a time series' trend (% change over the period), how its second half
differs from its first (a reversal, or a change of pace — accelerating / decelerating), a
standing day-of-week pattern, and its single most extreme spike or dip; a ranking's leader
and either its top-3 concentration or — the complement — its even spread; a structure
chart's largest share. It answers "what does this dashboard actually say?" without the
reader having to eyeball every chart.

It is a SEPARATE surface from the dashboard, never rendered inside it — an operational
dashboard shows the numbers and the filters; the narrative belongs on its own layer
(dashboard-not-presentation). The CLI prints it under the build; the API exposes it.

No LLM: the facts are computed in code, and the RU prose is formatted deterministically
from those numbers (so there is no GraceKelly dependency, no prompt-eval gate, and the
output is reproducible). This mirrors the Advisor — code decides, the text only states
the decision (invariant 5 / D9).

Best-effort and advisory, like the Advisor: a chart whose query fails to run degrades to
"no observation" for that chart, never an error. The pass touches no invariant (it reads
the same normalized spec the dashboard is built from and runs read-only SELECTs).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from statistics import fmean, median, pstdev

from auto_bi.agent.normalize import apply_chart_defaults, apply_label_joins
from auto_bi.agent.query_plan import PlanCache
from auto_bi.agent.sqlgen import generate_chart_sql
from auto_bi.engine import CLICKHOUSE, sqlglot_dialect
from auto_bi.introspect.base import RunQuery
from auto_bi.ir.spec import (
    ChartSpec,
    DashboardSpec,
    MeasureTransform,
    Viz,
    column_alias,
    is_percent_measure,
    measure_alias,
)
from auto_bi.semantic.model import SemanticModel

# a "concentration" observation is only worth stating when the top few categories really
# dominate; below this the ranking is diffuse and "top-3 = 38%" is noise, not a finding
_CONCENTRATION_MIN_PCT = 50.0

# the complement of concentration: a ranking reads as genuinely "ровное" when even the top-3
# carry little AND there are enough categories for "even" to mean something. The 40–50% band
# is a deliberate dead zone so a ranking is never called both concentrated and diffuse.
_SPREAD_MAX_PCT = 40.0
_SPREAD_MIN_CATEGORIES = 5

# an anomaly needs both enough points to have a stable mean and a genuinely extreme peak/dip
_ANOMALY_MIN_POINTS = 8
_ANOMALY_SIGMA = 3.0  # the extreme must clear mean ± 3σ ...
_ANOMALY_MIN_RATIO = 2.0  # ... and be ≥2× / ≤½ the mean to read as a real spike or dip

# the "second-half story" (a reversal, or a change of pace) needs enough points for two
# stable halves and a real move within each half
_HALF_MIN_POINTS = 8
_HALF_MIN_PCT = 8.0
# a change of pace is only worth stating when the slope genuinely changed: one half's
# absolute move must be at least this many times the other's (below it the trend is steady
# and the trend line already says so). Judged on slope, NOT percent — a linear climb shows a
# falling percent each half merely because the base grows, and is not a deceleration.
_MOMENTUM_MIN_RATIO = 1.5

# a weekday-seasonality observation needs many weeks so each weekday's MEDIAN is stable (a
# median over few samples can still be shifted a rank by one spike); below this the weekly
# profile is read off too little data to trust. The peak/trough weekday must clear this gap
# from the overall median to be worth stating.
_SEASON_MIN_SAMPLES = 6  # ≥6 of each weekday → ~6+ weeks of daily coverage
_SEASON_MIN_WEEKDAYS = 5  # enough distinct weekdays for a "profile" to mean anything
_SEASON_MIN_PCT = 12.0  # a weekday's median gap from the overall median to count as a pattern

_WEEKDAYS_RU = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)


@dataclass(frozen=True)
class Observation:
    """One deterministic finding about a chart, ready to render and to assert on.

    `text` is the RU sentence; `value`/`subject` carry the headline number and the
    category/time bucket it refers to (for tests and any downstream consumer).
    """

    chart_id: str
    # trend | reversal | momentum | seasonality | anomaly | leader | concentration | spread
    # | share_lead
    kind: str
    text: str
    value: float | None = None
    subject: str | None = None


@dataclass(frozen=True)
class Insights:
    table: str
    observations: list[Observation]

    @property
    def is_empty(self) -> bool:
        return not self.observations

    def render(self) -> str:
        """A plain-text 'Что видно' block for the CLI/preview; '' when there is nothing."""
        if not self.observations:
            return ""
        lines = ["Что видно (детерминированно, по реальным данным):"]
        lines += [f"  — {o.text}" for o in self.observations]
        return "\n".join(lines)


def analyze_spec(
    spec: DashboardSpec,
    model: SemanticModel,
    run_query: RunQuery,
    *,
    max_per_chart: int = 4,
    plans: PlanCache | None = None,
) -> Insights:
    """Run each chart of `spec` read-only and collect deterministic observations.

    The spec is normalized first (label joins + chart defaults — both pure and idempotent)
    so the SQL we run is byte-for-byte the SQL the dashboard shows. Never raises: a chart
    that errors is skipped.

    `plans` (D-2 §5, CLI one-shot path only): when a complete LIMIT-trial was recorded for
    a chart's exact SQL during the same build, reuse those rows instead of `run_query`.
    A miss (truncated, SOURCE chart never gated per-chart, different dialect text) falls
    through to a full run. serve `/insights` does not pass a cache (separate request).
    """
    normalized = apply_chart_defaults(apply_label_joins(spec, model), model)
    dialect = sqlglot_dialect(_engine_of(model))
    out: list[Observation] = []
    for chart in normalized.charts:
        try:
            out.extend(_observe_chart(chart, run_query, dialect, plans=plans)[:max_per_chart])
        except Exception:  # advisory only: one bad chart never sinks the pass
            continue
    return Insights(table=spec.charts[0].query.table if spec.charts else "", observations=out)


def _engine_of(model: SemanticModel) -> str:
    return next((t.physical.engine for t in model.tables if t.physical), CLICKHOUSE)


def _observe_chart(
    chart: ChartSpec,
    run_query: RunQuery,
    dialect: str,
    *,
    plans: PlanCache | None = None,
) -> list[Observation]:
    q = chart.query
    primary = q.measures[0]
    m_alias = measure_alias(primary)
    sql = generate_chart_sql(q, dialect=dialect)
    rows: list[dict] | None = None
    if plans is not None:
        trial = plans.get_trial(sql)
        if trial is not None and trial.complete:
            rows = list(trial.rows)
    if rows is None:
        rows = run_query(sql)
    if not rows or not q.dimensions:
        return []  # KPIs (no dimension) and empty results carry no trend/ranking story
    d_alias = column_alias(q.dimensions[0])

    if chart.viz in (Viz.LINE, Viz.AREA):
        if is_percent_measure(primary):
            # a percent line (yoy_pct / pop_pct) is already a derived rate; the trend / anomaly
            # machinery reads a LEVEL series and formats magnitudes (rubles), not percents, so
            # narrating "the trend of a year-over-year rate" would be muddled — that chart is
            # itself the insight. (A share BAR differs: its single largest part is a clean lead.)
            return []
        return _observe_line(chart, rows, m_alias, d_alias)
    if chart.viz in (Viz.BAR, Viz.STACKED_BAR, Viz.PIE):
        if primary.transform == MeasureTransform.RUNNING_SHARE:
            # a running_share bar is a cumulative Pareto ranking: its values rise to ~100% at the
            # SMALLEST category, so "the largest part" (what _observe_share reports) is always the
            # tail at ~100% — meaningless. The chart itself is the Pareto insight; emit nothing
            # rather than a confidently-wrong "largest share" line (mirrors the percent-line skip).
            return []
        if is_percent_measure(primary):
            return _observe_share(chart, rows, m_alias, d_alias)
        return _observe_bar(chart, rows, m_alias, d_alias)
    return []  # table / pivot / heatmap: a detail grid, not a single headline


def _observe_line(
    chart: ChartSpec, rows: list[dict], m_alias: str, t_alias: str
) -> list[Observation]:
    """The headline story of a time series: its trend, how its second half differs from the
    first (a reversal of that trend, or a change of pace — accelerating / decelerating), a
    standing day-of-week pattern, and the single most extreme spike or dip — ordered by
    importance (trend, second-half story, seasonality, then extremes) so the per-chart cap
    keeps the headlines.

    Rows arrive ordered by time ascending. Every comparison is the mean of a small window
    (a tenth of the series), not a single endpoint, so one noisy day never drives it.
    """
    pts = [(_label(r.get(t_alias)), v) for r in rows if (v := _num(r.get(m_alias))) is not None]
    if len(pts) < 2:
        return []
    vals = [v for _, v in pts]
    n = len(vals)
    k = max(1, n // 10)
    head = fmean(vals[:k])
    tail = fmean(vals[-k:])
    out: list[Observation] = []

    if head != 0:
        pct = (tail - head) / head * 100.0
        direction = "рост" if pct >= 1 else "снижение" if pct <= -1 else "почти без изменений"
        out.append(
            Observation(
                chart.id,
                "trend",
                f"«{chart.title}» — {direction} {_signed_pct(pct)} за период "
                f"(с {_compact(head)} до {_compact(tail)} в среднем)",
                value=round(pct, 1),
            )
        )

    if n >= _HALF_MIN_POINTS:
        out.extend(_second_half_story(chart, vals, n, k))

    out.extend(_seasonality(chart, rows, m_alias, t_alias))

    if n >= _ANOMALY_MIN_POINTS:
        out.extend(_extreme(chart, pts, vals))
    return out


def _half_changes(vals: list[float], n: int, k: int) -> tuple[float, float, float, float] | None:
    """Each half's net move, as both an absolute delta (slope proxy) and a percent.

    Measured between the boundary windows of width `k` at the start, middle and end. The two
    halves span the same number of points, so their deltas are directly comparable as slopes.
    Returns (first Δ, second Δ, first %, second %), or None when a base is zero.
    """
    mid = n // 2
    first_base = fmean(vals[:k])
    second_base = fmean(vals[mid : mid + k])
    if first_base == 0 or second_base == 0:
        return None
    first_delta = fmean(vals[mid - k : mid]) - first_base
    second_delta = fmean(vals[-k:]) - second_base
    return (
        first_delta,
        second_delta,
        first_delta / first_base * 100.0,
        second_delta / second_base * 100.0,
    )


def _second_half_story(chart: ChartSpec, vals: list[float], n: int, k: int) -> list[Observation]:
    """How the second half differs from the first — a reversal, or a change of pace. The two
    are mutually exclusive (opposite vs same direction), so a line reports at most one.

    Reversal: the halves move in opposite directions (an inflection the overall trend hides).
    Change of pace: same direction, but one half is clearly steeper — growth/decline that is
    accelerating or decelerating. Pace is judged by SLOPE (the absolute delta of each half),
    not by percent: a steady linear climb shows a *falling* percent each half only because the
    base grows, and must never be mislabeled "замедляется". Materiality and the human evidence
    use percent; both halves must clear `_HALF_MIN_PCT` so a flat half is never a "story".
    """
    cuts = _half_changes(vals, n, k)
    if cuts is None:
        return []
    first_delta, second_delta, first_pct, second_pct = cuts
    if abs(first_pct) < _HALF_MIN_PCT or abs(second_pct) < _HALF_MIN_PCT:
        return []
    halves = f"первая половина {_signed_pct(first_pct)}, вторая {_signed_pct(second_pct)}"

    if (first_delta > 0) != (second_delta > 0):
        return [
            Observation(
                chart.id,
                "reversal",
                f"«{chart.title}» — разворот: {halves}",
                value=round(second_pct, 1),
            )
        ]

    faster, slower = max(abs(first_delta), abs(second_delta)), min(
        abs(first_delta), abs(second_delta)
    )
    if slower == 0 or faster < _MOMENTUM_MIN_RATIO * slower:
        return []  # same direction at a steady slope — the trend line already says so
    direction = "рост" if second_delta > 0 else "снижение"
    pace = "ускоряется" if abs(second_delta) > abs(first_delta) else "замедляется"
    return [
        Observation(
            chart.id,
            "momentum",
            f"«{chart.title}» — {direction} {pace}: {halves}",
            value=round(second_pct, 1),
        )
    ]


def _parse_date(value: object) -> date | None:
    """Best-effort parse of a time-dimension value into a date, for weekday grouping.

    Accepts date/datetime objects (as ClickHouse/psycopg hand back Date/DateTime columns)
    and ISO strings ('2026-01-15' or '2026-01-15 00:00:00'); anything else → None. `datetime`
    is checked first because it subclasses `date`.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _seasonality(
    chart: ChartSpec, rows: list[dict], m_alias: str, t_alias: str
) -> list[Observation]:
    """A standing day-of-week pattern: the weekday whose MEDIAN most exceeds the overall
    median (e.g. weekends run higher), plus the weakest weekday when it too is material.

    Robust by construction: a median per weekday over many weeks shrugs off a single spike,
    and roughly uniform weekday sampling across the period keeps the trend from biasing the
    profile. Silent unless every row carries a parseable date, enough weeks cover each
    weekday, and the gap clears `_SEASON_MIN_PCT` — otherwise the weekly read is noise.
    """
    by_wd: dict[int, list[float]] = {}
    for r in rows:
        v = _num(r.get(m_alias))
        d = _parse_date(r.get(t_alias))
        if v is None or d is None:
            return []  # a non-date dimension or an unreadable value → no weekly story
        by_wd.setdefault(d.weekday(), []).append(v)
    kept = {wd: vals for wd, vals in by_wd.items() if len(vals) >= _SEASON_MIN_SAMPLES}
    if len(kept) < _SEASON_MIN_WEEKDAYS:
        return []
    overall = median([v for vals in kept.values() for v in vals])
    if overall <= 0:
        return []
    meds = {wd: median(vals) for wd, vals in kept.items()}
    peak_wd = max(meds, key=lambda wd: meds[wd])
    peak_pct = (meds[peak_wd] - overall) / overall * 100.0
    if peak_pct < _SEASON_MIN_PCT:
        return []  # no weekday stands out enough to call it a pattern
    trough_wd = min(meds, key=lambda wd: meds[wd])
    trough_pct = (meds[trough_wd] - overall) / overall * 100.0
    text = (
        f"«{chart.title}» — по дням недели: выше всего {_WEEKDAYS_RU[peak_wd]} "
        f"({_signed_pct(peak_pct)} к медианному дню)"
    )
    if trough_pct <= -_SEASON_MIN_PCT:
        text += f", ниже всего {_WEEKDAYS_RU[trough_wd]} ({_signed_pct(trough_pct)})"
    return [
        Observation(
            chart.id,
            "seasonality",
            text,
            value=round(peak_pct, 1),
            subject=_WEEKDAYS_RU[peak_wd],
        )
    ]


def _extreme(
    chart: ChartSpec, pts: list[tuple[str, float]], vals: list[float]
) -> list[Observation]:
    """The most extreme spike and/or dip — each guarded by mean ± 3σ and a 2× ratio so the
    natural spread of a trending series is never flagged. Both are returned when both qualify,
    ordered by how extreme they are (the per-chart cap keeps the stronger one)."""
    mu = fmean(vals)
    sigma = pstdev(vals)
    if mu <= 0 or sigma <= 0:
        return []
    hi_label, hi = max(pts, key=lambda p: p[1])
    lo_label, lo = min(pts, key=lambda p: p[1])
    cands: list[tuple[float, Observation]] = []
    if hi > mu + _ANOMALY_SIGMA * sigma and hi >= mu * _ANOMALY_MIN_RATIO:
        cands.append(
            (
                hi / mu,
                Observation(
                    chart.id,
                    "anomaly",
                    f"«{chart.title}» — аномальный пик {hi_label}: {_compact(hi)} "
                    f"(×{_ratio(hi / mu)} к среднему)",
                    value=round(hi, 1),
                    subject=hi_label,
                ),
            )
        )
    if 0 < lo < mu - _ANOMALY_SIGMA * sigma and lo * _ANOMALY_MIN_RATIO <= mu:
        cands.append(
            (
                mu / lo,
                Observation(
                    chart.id,
                    "anomaly",
                    f"«{chart.title}» — аномальный провал {lo_label}: {_compact(lo)} "
                    f"(×{_ratio(mu / lo)} ниже среднего)",
                    value=round(lo, 1),
                    subject=lo_label,
                ),
            )
        )
    cands.sort(key=lambda c: c[0], reverse=True)
    return [o for _, o in cands]


def _observe_bar(
    chart: ChartSpec, rows: list[dict], m_alias: str, d_alias: str
) -> list[Observation]:
    """The ranking's leader (share of the visible total) and, if the top dominates, its
    top-3 concentration. Rows arrive ordered by the measure descending (the chart is top-N
    capped, so the total is honestly "видимой суммы" — of the shown bars)."""
    pts = [(_label(r.get(d_alias)), v) for r in rows if (v := _num(r.get(m_alias))) is not None]
    if not pts:
        return []
    total = sum(v for _, v in pts)
    lead_label, lead_val = pts[0]
    out: list[Observation] = []
    if total > 0:
        share = lead_val / total * 100.0
        out.append(
            Observation(
                chart.id,
                "leader",
                f"«{chart.title}» — лидер: {lead_label}, {_compact(lead_val)} "
                f"({_pct(share)} видимой суммы)",
                value=round(lead_val, 1),
                subject=lead_label,
            )
        )
        if len(pts) >= 4:
            top3 = sum(v for _, v in pts[:3]) / total * 100.0
            if top3 >= _CONCENTRATION_MIN_PCT:
                out.append(
                    Observation(
                        chart.id,
                        "concentration",
                        f"«{chart.title}» — топ-3 дают {_pct(top3)} видимой суммы",
                        value=round(top3, 1),
                    )
                )
            elif len(pts) >= _SPREAD_MIN_CATEGORIES and top3 <= _SPREAD_MAX_PCT:
                out.append(
                    Observation(
                        chart.id,
                        "spread",
                        f"«{chart.title}» — распределение ровное: топ-3 лишь {_pct(top3)} "
                        f"видимой суммы",
                        value=round(top3, 1),
                    )
                )
    else:
        out.append(
            Observation(
                chart.id,
                "leader",
                f"«{chart.title}» — лидер: {lead_label} ({_compact(lead_val)})",
                value=round(lead_val, 1),
                subject=lead_label,
            )
        )
    return out


def _observe_share(
    chart: ChartSpec, rows: list[dict], m_alias: str, d_alias: str
) -> list[Observation]:
    """The structure chart's largest part (a share_of_total measure: values are fractions)."""
    pts = [(_label(r.get(d_alias)), v) for r in rows if (v := _num(r.get(m_alias))) is not None]
    if not pts:
        return []
    lead_label, lead_frac = max(pts, key=lambda p: p[1])
    return [
        Observation(
            chart.id,
            "share_lead",
            f"«{chart.title}» — наибольшая доля: {lead_label}, {_pct(lead_frac * 100.0)}",
            value=round(lead_frac * 100.0, 1),
            subject=lead_label,
        )
    ]


# --- deterministic RU formatting ------------------------------------------------------
# A space groups thousands and separates a unit; the decimal mark is a comma (RU prose).
# comma. Trailing ",0" is always stripped (a stray decimal on a round number reads as
# noise — the analyst's standing note "десятая лишняя").

_SEP = " "


def _num(value: object) -> float | None:
    if isinstance(value, bool):
        return None  # a bool is a flag, not a measurement (and is a subclass of int)
    if isinstance(value, int | float | Decimal):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None  # date / None / anything else carries no number


def _label(value: object) -> str:
    return "—" if value is None else str(value)


def _trim(text: str) -> str:
    """'236.1' -> '236,1'; '115.0' -> '115' (strip a trailing zero decimal)."""
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",")


def _group(value: float) -> str:
    return f"{round(value):,}".replace(",", _SEP)


def _compact(value: float) -> str:
    """A compact magnitude: 236,1 млрд / 115 млн / 3,6 тыс / 842 (grouped)."""
    a = abs(value)
    if a >= 1e9:
        return f"{_trim(f'{value / 1e9:.1f}')}{_SEP}млрд"
    if a >= 1e6:
        return f"{_trim(f'{value / 1e6:.1f}')}{_SEP}млн"
    if a >= 1e3:
        return f"{_trim(f'{value / 1e3:.1f}')}{_SEP}тыс"
    return _group(value)


def _pct(value: float) -> str:
    return f"{_trim(f'{value:.1f}')}%"


def _signed_pct(value: float) -> str:
    sign = "+" if value > 0 else "−" if value < 0 else ""
    return f"{sign}{_pct(abs(value))}"


def _ratio(value: float) -> str:
    return _trim(f"{value:.1f}")
