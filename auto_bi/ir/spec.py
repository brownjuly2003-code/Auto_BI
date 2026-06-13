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


class FilterOp(StrEnum):
    EQ = "="
    NEQ = "!="
    IN = "in"
    GTE = ">="
    LTE = "<="


class QueryFilter(BaseModel):
    column: str
    op: FilterOp
    value: str | int | float | list[str] | list[int] | list[float]


class Measure(BaseModel):
    column: str
    agg: Aggregation
    label: str = ""


def measure_alias(measure: Measure) -> str:
    """Canonical SELECT alias of a measure (label if set, else `<agg>_<column>`).

    Single source of truth shared by SQL_GEN, the adapters, and validation so the
    alias a chart is ordered/aggregated by always matches the column SQL_GEN emits.
    """
    return measure.label or f"{measure.agg.value}_{measure.column}"


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
