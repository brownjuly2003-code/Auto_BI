"""Semantic model: pydantic schema for model.yaml (ARCHITECTURE §3.2).

model.yaml is the single source of truth the agent grounds against; it lives in git
and is hand-edited after auto-generation by the introspector.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class ColumnRole(StrEnum):
    TIME = "time"
    DIMENSION = "dimension"
    MEASURE = "measure"


class Aggregation(StrEnum):
    SUM = "sum"
    AVG = "avg"
    MIN = "min"
    MAX = "max"
    COUNT = "count"
    COUNT_DISTINCT = "count_distinct"


class Column(BaseModel):
    name: str
    type: str
    role: ColumnRole
    description: str = ""
    agg: Aggregation | None = None  # default aggregation for measures
    fk: str | None = None  # "schema.table.column" the dimension points to
    top_values: list[str] = Field(default_factory=list)  # low-cardinality samples for grounding


class Physical(BaseModel):
    """Engine-level facts: formal definition of what the DM is designed for (advisor fuel)."""

    engine: str  # required: never dropped from yaml by exclude_defaults
    table_engine: str = ""
    sorting_key: list[str] = Field(default_factory=list)  # ClickHouse ORDER BY key
    distribution_key: list[str] = Field(default_factory=list)  # Greenplum/Greengage DISTRIBUTED BY
    partition_key: str = ""
    rows: int = 0
    bytes: int = 0
    cardinality: dict[str, int] = Field(default_factory=dict)


class Table(BaseModel):
    name: str  # fully qualified: "dm.sales_daily"
    description: str = ""
    grain: list[str] = Field(default_factory=list)
    columns: list[Column] = Field(default_factory=list)
    physical: Physical | None = None

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)


class Join(BaseModel):
    left: str  # "dm.sales_daily.store_id"
    right: str  # "dm.stores.id"
    type: str = "many_to_one"


class Metric(BaseModel):
    name: str
    sql: str
    description: str = ""


class SemanticModel(BaseModel):
    tables: list[Table] = Field(default_factory=list)
    joins: list[Join] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)

    def table(self, name: str) -> Table | None:
        return next((t for t in self.tables if t.name == name), None)

    @classmethod
    def load(cls, path: str | Path) -> SemanticModel:
        with open(path, encoding="utf-8") as f:
            return cls.model_validate(yaml.safe_load(f) or {})

    def dump(self, path: str | Path) -> None:
        # required fields (name/type/role/engine) survive exclude_defaults; optional
        # empties (description="", fk=None, ...) are dropped to keep the yaml hand-editable
        data = self.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, width=120)
