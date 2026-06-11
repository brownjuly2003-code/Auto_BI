"""DashboardSpec — BI-agnostic IR (ARCHITECTURE §3.4), Phase 0 subset.

The LLM generates ONLY this spec (invariant 1); native BI formats are produced by
deterministic adapters. Phase 0 viz subset: big_number, line, bar — the enum grows
to 9 types in Phase 1.
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


class OrderBy(BaseModel):
    by: str  # dimension column or measure label/column
    dir: str = Field(default="asc", pattern="^(asc|desc)$")


class ChartQuery(BaseModel):
    table: str  # fully qualified: "dm.sales_daily"
    dimensions: list[str] = Field(default_factory=list)
    measures: list[Measure] = Field(min_length=1)
    filters: list[QueryFilter] = Field(default_factory=list)
    order_by: list[OrderBy] = Field(default_factory=list)
    limit: int = Field(default=5000, ge=1, le=50000)


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
