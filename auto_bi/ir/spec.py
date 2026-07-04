"""DashboardSpec — BI-agnostic IR (ARCHITECTURE §3.4).

The LLM generates ONLY this spec (invariant 1); native BI formats are produced by
deterministic adapters. Phase 1 viz set: the full 9 types from ARCHITECTURE §3.4.

Dimension-like roles (ARCHITECTURE §3.4, "rich roles"):
- `dimensions` — primary grouping (x-axis for line/bar/area, slices for pie, the two
  axes for heatmap, listed columns for table);
- `series`   — breakdown/stack dimension(s) for stacked_bar / area;
- `rows` / `columns` — pivot row- and column-dimensions.
SQL_GEN groups by the union of all four; adapters read each role to lay out the chart.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from auto_bi.semantic.model import Aggregation


class TargetBI(StrEnum):
    SUPERSET = "superset"
    DATALENS = "datalens"


class Viz(StrEnum):
    BIG_NUMBER = "big_number"
    LINE = "line"
    BAR = "bar"
    STACKED_BAR = "stacked_bar"
    AREA = "area"
    PIE = "pie"
    TABLE = "table"
    PIVOT = "pivot"
    HEATMAP = "heatmap"
    HISTOGRAM = "histogram"


class FilterOp(StrEnum):
    EQ = "="
    NEQ = "!="
    IN = "in"
    GTE = ">="
    LTE = "<="


class MeasureTransform(StrEnum):
    """Analytical transform applied to a base aggregate via a window function (SQL_GEN).

    The base aggregate (`agg(column)`) is computed in an inner GROUP BY; the transform is a
    window over that result in an outer SELECT (deterministic, no LLM — invariant 1/D5):
    - `pop_abs`  — period-over-period absolute change: `agg - lag(agg) OVER (ORDER BY time)`;
    - `pop_pct`  — period-over-period relative change: `(agg - lag(agg)) / lag(agg)`;
    - `yoy_pct`  — year-over-year relative change vs the same period a year back: lags by the
      number of periods in a year for the chart's `time_grain` (month=12, quarter=4, week=52,
      year=1), so it REQUIRES a non-day `time_grain`. (mom = `pop_pct` at month grain — no
      separate transform needed.)
    - `share_of_total` — share of the column total: `agg / sum(agg) OVER ()`;
    - `running_total`  — cumulative sum over time: `sum(agg) OVER (ORDER BY time ROWS …)`.
    - `running_share`  — Pareto / ABC cumulative share: categories ranked by the measure
      descending, the cumulative share of the grand total — `sum(agg) OVER (ORDER BY agg DESC
      ROWS …) / sum(agg) OVER ()`. Unlike the other ordered transforms it orders by the AGGREGATE
      VALUE, not a time axis, so it needs a (categorical) dimension but NOT a time one.

    pop_* / yoy_pct / running_total order by the chart's first (time) dimension; share_of_total /
    running_share need a dimension but no time axis. Validation enforces the required shape (a time
    x-axis for the time-ordered transforms; a non-day time_grain for yoy_pct; ≥1 dimension for the
    shares).
    """

    POP_ABS = "pop_abs"
    POP_PCT = "pop_pct"
    YOY_PCT = "yoy_pct"
    SHARE_OF_TOTAL = "share_of_total"
    RUNNING_TOTAL = "running_total"
    RUNNING_SHARE = "running_share"


class TimeGrain(StrEnum):
    """Truncation grain for the chart's time x-axis (`ChartQuery.time_grain`).

    Buckets a date/datetime dimension to a coarser period so a long daily series reads as a
    trend instead of noise (730 days -> 24 months). Domain-neutral and deterministic (SQL_GEN,
    no LLM). `day` is the raw column (no truncation); the rest compile per-dialect — ClickHouse
    `toStartOf*` / Postgres `date_trunc` — with weeks starting Monday in both.
    """

    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


class ScalarCompareKind(StrEnum):
    """Direction of a scalar period-compare KPI (`Measure.compare`).

    - `yoy` — the latest period vs the SAME period one year back (year-over-year);
    - `pop` — the latest period vs the immediately PREVIOUS period (period-over-period).
    """

    YOY = "yoy"
    POP = "pop"


class ScalarCompareOutput(StrEnum):
    """What a scalar period-compare KPI DISPLAYS.

    - `pct` — the relative change `(current - prior) / prior` (a percent — `is_percent_measure`);
    - `abs` — the absolute change `current - prior` (keeps the base aggregate's number family).
    """

    PCT = "pct"
    ABS = "abs"


class ScalarCompare(BaseModel):
    """A scalar period-compare KPI: one number = how the measure changed vs a period back.

    Unlike `MeasureTransform.YOY_PCT` (a windowed SERIES, one point per period along a time axis),
    this reduces to a SINGLE scalar for a `big_number` tile — the latest period's value against the
    same period a year back (`yoy`) or the previous period (`pop`). SQL_GEN computes it by
    conditional aggregation over two buckets (no window, no displayed dimension), so a big_number
    stays a true scalar (one row, zero dimensions).

    `column` is the chart table's TIME column; `grain` defines "the period" (week/month/quarter/
    year — not day); "the latest period" is the latest bucket PRESENT in the (filtered) data, and
    the compare bucket is the matching one a year / a period back (a missing compare bucket yields
    NULL, never a crash). Only valid on `big_number` with exactly one measure; mutually exclusive
    with `transform` / `denominator` — enforced by validation.
    """

    column: str
    grain: TimeGrain
    kind: ScalarCompareKind = ScalarCompareKind.YOY
    output: ScalarCompareOutput = ScalarCompareOutput.PCT


class QueryFilter(BaseModel):
    column: str
    op: FilterOp
    value: str | int | float | list[str] | list[int] | list[float]


class Measure(BaseModel):
    column: str
    agg: Aggregation
    label: str = ""
    # optional analytical transform (period-over-period, share, running total) computed
    # as a window over the base aggregate; None => a plain aggregate (the common case)
    transform: MeasureTransform | None = None
    # optional ratio: this measure becomes agg(column) / denominator.agg(denominator.column),
    # both aggregated in the same GROUP BY and divided in floating point (SQL_GEN). A
    # domain-neutral primitive — margin = profit/revenue, conversion = converted/sessions,
    # defect rate = defects/output, error rate = errors/requests, avg duration = sum/count.
    # None => not a ratio. Mutually exclusive with `transform`; the denominator is itself a
    # plain Measure (no nested denominator, no transform) — enforced by validation.
    denominator: Measure | None = None
    # optional lag for a period-over-period transform: pop_abs/pop_pct compare against the value
    # `lag_periods` periods back instead of the adjacent one (e.g. lag_periods=3 at month grain =
    # "vs 3 months ago"). Generalises the fixed year lag of yoy_pct to an arbitrary offset. None
    # => 1 (adjacent period; SQL byte-for-byte unchanged). Only meaningful with pop_abs/pop_pct —
    # yoy_pct derives its own year lag, share_of_total/running_total have no period offset, and a
    # plain measure has no lag — enforced by validation. The window machinery already lags by k
    # rows (SQL_GEN `_window_expr`), so this only routes a different k.
    lag_periods: int | None = Field(default=None, ge=1)
    # optional scalar period-compare KPI: this measure becomes a SINGLE number — the latest period
    # vs a period back (yoy/pop), as a percent or absolute change (see ScalarCompare). Only for a
    # big_number tile; mutually exclusive with `transform`/`denominator` — enforced by validation.
    # Unlike the yoy_pct transform (a windowed series) this reduces to one row via conditional
    # aggregation (SQL_GEN `_generate_compare_kpi_sql`), so the big_number stays a true scalar.
    compare: ScalarCompare | None = None


def measure_alias(measure: Measure) -> str:
    """Canonical SELECT alias of a measure (label if set, else `<agg>_<column>`).

    Single source of truth shared by SQL_GEN, the adapters, and validation so the
    alias a chart is ordered/aggregated by always matches the column SQL_GEN emits.
    A transformed measure with no explicit label gets the transform in its default alias
    (`pop_pct_sum_revenue`); a non-adjacent lag adds `_lag<N>` (`pop_pct_sum_revenue_lag3`); a
    ratio gets `_per_<den>` (`sum_revenue_per_count_orders`) — so none collides with the same
    chart's plain base aggregate (or its adjacent-period counterpart).
    """
    if measure.label:
        return measure.label
    base = f"{measure.agg.value}_{measure.column}"
    if measure.compare is not None:
        # a scalar period-compare KPI: yoy_sum_revenue / pop_sum_revenue — distinct from the plain
        # base aggregate so a level KPI and its yoy KPI never share a dataset column
        return f"{measure.compare.kind.value}_{base}"
    if measure.transform is not None:
        alias = f"{measure.transform.value}_{base}"
        if measure.lag_periods is not None:
            alias = f"{alias}_lag{measure.lag_periods}"
        return alias
    if measure.denominator is not None:
        d = measure.denominator
        return f"{base}_per_{d.agg.value}_{d.column}"
    return base


def is_percent_measure(measure: Measure) -> bool:
    """Whether a measure's value is a ratio/share to DISPLAY as a percent (pop_pct, yoy_pct,
    share_of_total, running_share).

    pop_abs / running_total keep the base measure's number family (a cumulative sum is still
    rubles), so they are not percents; running_share IS a percent (a cumulative share of the
    total). A scalar period-compare KPI is a percent when its output is `pct` (the abs delta keeps
    the base family). The adapters map this to the native percent format (Superset d3 `.1%`,
    DataLens `formatting` percent)."""
    if measure.compare is not None:
        return measure.compare.output == ScalarCompareOutput.PCT
    return measure.transform in (
        MeasureTransform.POP_PCT,
        MeasureTransform.YOY_PCT,
        MeasureTransform.SHARE_OF_TOTAL,
        MeasureTransform.RUNNING_SHARE,
    )


def is_ratio_measure(measure: Measure) -> bool:
    """Whether a measure is a ratio of two aggregates (`num / den`) — see `Measure.denominator`."""
    return measure.denominator is not None


def is_compact_number(measure: Measure) -> bool:
    """Whether a measure should DISPLAY abbreviated (236G / 236,1 млрд) rather than in full
    (236149963687). Additive aggregates over a fact table reach millions/billions and overflow
    a KPI tile / collide on an axis; averages and extrema stay small and keep full precision
    (an average check is 3614, not '3.6k'). Display hint only — SQL and values are unchanged.
    The adapters map it to the native format (Superset d3 `~s`, DataLens compact `formatting`).

    A percent transform (pop_pct, share) is never compact — it renders as a percent, not an
    SI-abbreviated count. pop_abs / running_total keep the underlying aggregate's rule. A ratio
    measure (num/den) is a small exact figure (a rate, an average), never compact.
    """
    if is_percent_measure(measure) or is_ratio_measure(measure):
        return False
    return measure.agg in (Aggregation.SUM, Aggregation.COUNT, Aggregation.COUNT_DISTINCT)


class OrderBy(BaseModel):
    by: str  # dimension column or measure label/column
    dir: str = Field(default="asc", pattern="^(asc|desc)$")


class JoinSpec(BaseModel):
    """One LEFT JOIN of the chart's base table to a related dimension table.

    The LLM declares the join explicitly, but validation only accepts pairs that
    exist as edges in the semantic model (invariant 2) — join conditions cannot
    be invented. Measures stay on the base table; joined tables contribute
    dimension-like columns referenced by their fully qualified names.
    """

    table: str  # joined table, fully qualified: "dm.stores"
    on_left: str  # column on the chart's base table: "dm.sales_daily.store_id"
    on_right: str  # column on the joined table: "dm.stores.id"


def column_alias(col: str) -> str:
    """Bare SELECT alias of a dimension-like reference ('dm.stores.city' -> 'city').

    SQL_GEN aliases joined columns to their bare names, so adapters and form_data
    always address dataset columns the same way regardless of the source table.
    """
    return col.rpartition(".")[2]


class ChartQuery(BaseModel):
    table: str  # fully qualified: "dm.sales_daily"
    dimensions: list[str] = Field(default_factory=list)
    series: list[str] = Field(default_factory=list)  # stack/breakdown for stacked_bar, area
    rows: list[str] = Field(default_factory=list)  # pivot row dimensions
    columns: list[str] = Field(default_factory=list)  # pivot column dimensions
    measures: list[Measure] = Field(min_length=1)
    filters: list[QueryFilter] = Field(default_factory=list)
    joins: list[JoinSpec] = Field(default_factory=list)
    order_by: list[OrderBy] = Field(default_factory=list)
    limit: int = Field(default=5000, ge=1, le=50000)
    # optional truncation of the time x-axis (the first dimension): buckets a date series to
    # week/month/quarter/year so a long daily run reads as a trend. None => raw dimension.
    time_grain: TimeGrain | None = None
    # optional histogram: bins the numeric x-dimension (the first dimension) into this many
    # equal-width buckets and counts rows per bucket (a distribution view). When set, SQL_GEN
    # takes the histogram path: the x-dimension is replaced by each bucket's lower bound and the
    # single measure becomes the per-bucket count. None => not a histogram (the common case).
    # Used only with viz=HISTOGRAM (enforced by validation).
    bins: int | None = Field(default=None, ge=2, le=200)

    def group_columns(self) -> list[str]:
        """All dimension-like columns to GROUP BY, deduped, order preserved.

        Joined columns keep their fully qualified form here (validation and
        SQL_GEN need the table part); adapters use `column_alias` for the bare
        dataset-facing name.
        """
        seen: dict[str, None] = {}
        for col in (*self.dimensions, *self.series, *self.rows, *self.columns):
            seen.setdefault(col, None)
        return list(seen)


class LayoutHint(BaseModel):
    w: int = Field(default=6, ge=1, le=12)
    h: int = Field(default=4, ge=1, le=12)
    row: int = Field(default=0, ge=0)


class ChartSpec(BaseModel):
    id: str
    title: str
    viz: Viz
    query: ChartQuery
    layout_hint: LayoutHint = Field(default_factory=LayoutHint)


class DashboardFilter(BaseModel):
    column: str  # fully qualified: "dm.sales_daily.date"
    type: str = "time_range"
    default: str = ""  # e.g. "last 90 days"


class DashboardSpec(BaseModel):
    title: str
    target_bi: TargetBI = TargetBI.SUPERSET
    filters: list[DashboardFilter] = Field(default_factory=list)
    charts: list[ChartSpec] = Field(min_length=1, max_length=12)
