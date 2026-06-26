"""Deterministic insight layer over a built dashboard — the "Что видно" surface.

A read-only pass that runs each chart's SQL once and turns the *real* aggregates into a
few plain observations: a time series' trend (% change over the period), a ranking's
leader and concentration (top-3 share), a structure chart's largest share, and a genuine
anomaly (a spike far above the mean). It answers "what does this dashboard actually say?"
without the reader having to eyeball every chart.

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
from decimal import Decimal
from statistics import fmean, pstdev

from auto_bi.agent.normalize import apply_chart_defaults, apply_label_joins
from auto_bi.agent.sqlgen import generate_chart_sql
from auto_bi.engine import CLICKHOUSE, sqlglot_dialect
from auto_bi.introspect.base import RunQuery
from auto_bi.ir.spec import (
    ChartSpec,
    DashboardSpec,
    Viz,
    column_alias,
    is_percent_measure,
    measure_alias,
)
from auto_bi.semantic.model import SemanticModel

# a "concentration" observation is only worth stating when the top few categories really
# dominate; below this the ranking is diffuse and "top-3 = 38%" is noise, not a finding
_CONCENTRATION_MIN_PCT = 50.0

# an anomaly needs both enough points to have a stable mean and a genuinely extreme peak
_ANOMALY_MIN_POINTS = 8
_ANOMALY_SIGMA = 3.0  # peak must clear mean + 3σ ...
_ANOMALY_MIN_RATIO = 2.0  # ... and be at least 2× the mean to read as a spike


@dataclass(frozen=True)
class Observation:
    """One deterministic finding about a chart, ready to render and to assert on.

    `text` is the RU sentence; `value`/`subject` carry the headline number and the
    category/time bucket it refers to (for tests and any downstream consumer).
    """

    chart_id: str
    kind: str  # trend | anomaly | leader | concentration | share_lead
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
    max_per_chart: int = 2,
) -> Insights:
    """Run each chart of `spec` read-only and collect deterministic observations.

    The spec is normalized first (label joins + chart defaults — both pure and idempotent)
    so the SQL we run is byte-for-byte the SQL the dashboard shows. Never raises: a chart
    that errors is skipped.
    """
    normalized = apply_chart_defaults(apply_label_joins(spec, model), model)
    dialect = sqlglot_dialect(_engine_of(model))
    out: list[Observation] = []
    for chart in normalized.charts:
        try:
            out.extend(_observe_chart(chart, run_query, dialect)[:max_per_chart])
        except Exception:  # advisory only: one bad chart never sinks the pass
            continue
    return Insights(table=spec.charts[0].query.table if spec.charts else "", observations=out)


def _engine_of(model: SemanticModel) -> str:
    return next((t.physical.engine for t in model.tables if t.physical), CLICKHOUSE)


def _observe_chart(chart: ChartSpec, run_query: RunQuery, dialect: str) -> list[Observation]:
    q = chart.query
    primary = q.measures[0]
    m_alias = measure_alias(primary)
    rows = run_query(generate_chart_sql(q, dialect=dialect))
    if not rows or not q.dimensions:
        return []  # KPIs (no dimension) and empty results carry no trend/ranking story
    d_alias = column_alias(q.dimensions[0])

    if chart.viz in (Viz.LINE, Viz.AREA):
        return _observe_line(chart, rows, m_alias, d_alias)
    if chart.viz in (Viz.BAR, Viz.STACKED_BAR, Viz.PIE):
        if is_percent_measure(primary):
            return _observe_share(chart, rows, m_alias, d_alias)
        return _observe_bar(chart, rows, m_alias, d_alias)
    return []  # table / pivot / heatmap: a detail grid, not a single headline


def _observe_line(
    chart: ChartSpec, rows: list[dict], m_alias: str, t_alias: str
) -> list[Observation]:
    """Trend over the period (smoothed first-vs-last) plus a genuine spike, if any.

    Rows arrive ordered by time ascending. The trend compares the mean of the first and
    last tenth of the series, not single endpoints, so one noisy day does not flip it.
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
    if n >= _ANOMALY_MIN_POINTS:
        mu = fmean(vals)
        sigma = pstdev(vals)
        label, peak = max(pts, key=lambda p: p[1])
        extreme = mu > 0 and sigma > 0 and peak > mu + _ANOMALY_SIGMA * sigma
        if extreme and peak / mu >= _ANOMALY_MIN_RATIO:
            out.append(
                Observation(
                    chart.id,
                    "anomaly",
                    f"«{chart.title}» — аномальный пик {label}: {_compact(peak)} "
                    f"(×{_ratio(peak / mu)} к среднему)",
                    value=round(peak, 1),
                    subject=label,
                )
            )
    return out


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
    if isinstance(value, (int, float, Decimal)):
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
