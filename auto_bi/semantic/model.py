"""Semantic model: pydantic schema for model.yaml (ARCHITECTURE §3.2).

model.yaml is the single source of truth the agent grounds against; it lives in git
and is hand-edited after auto-generation by the introspector.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import Field

from auto_bi.strict import StrictModel


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


class Additivity(StrEnum):
    """How a measure column behaves under summation (semantic governance, P1-6).

    `non_additive` — a rate/ratio/price: a row-wise SUM is business-meaningless, so spec
    validation rejects `agg: sum` over it (avg or a numerator/denominator ratio instead).
    `semi_additive` — additive over some dimensions but not others (a distinct-count
    snapshot, a balance): recorded as modeling intent, not enforced in v1 — enforcement
    needs to know the non-additive axis. `additive` — explicit "sum is fine".
    Unset (None) — unknown; no constraint, same as before the field existed.
    """

    ADDITIVE = "additive"
    SEMI_ADDITIVE = "semi_additive"
    NON_ADDITIVE = "non_additive"


class Column(StrictModel):
    name: str
    type: str
    role: ColumnRole
    description: str = ""
    agg: Aggregation | None = None  # default aggregation for measures
    additivity: Additivity | None = None  # summation semantics of a measure (see Additivity)
    fk: str | None = None  # "schema.table.column" the dimension points to
    top_values: list[str] = Field(default_factory=list)  # low-cardinality samples for grounding
    # hand-authored alternate names ("удержание" for a retention column): rendered into
    # LLM prompts and scored by context selection, so a request phrased in the user's own
    # words still finds the column (X-3). NOT auto-introspected — vocabulary is a modeling
    # decision, like description.
    synonyms: list[str] = Field(default_factory=list)


class Physical(StrictModel):
    """Engine-level facts: formal definition of what the DM is designed for (advisor fuel)."""

    engine: str  # required: never dropped from yaml by exclude_defaults
    table_engine: str = ""
    sorting_key: list[str] = Field(default_factory=list)  # ClickHouse ORDER BY key
    distribution_key: list[str] = Field(default_factory=list)  # Greenplum/Greengage DISTRIBUTED BY
    partition_key: str = ""
    rows: int = 0
    bytes: int = 0
    cardinality: dict[str, int] = Field(default_factory=dict)
    # when these stats were captured (UTC ISO-8601, introspector-stamped). Stats live in git
    # while the DWH keeps growing and every environment differs (demo 1M / stand 20M / prod
    # 100M), so consumers that can measure live (advisor scan fraction) must not trust `rows`
    # as "now" — this stamp is what makes the staleness visible instead of implicit.
    captured_at: str = ""


class Table(StrictModel):
    name: str  # fully qualified: "dm.sales_daily"
    description: str = ""
    grain: list[str] = Field(default_factory=list)
    columns: list[Column] = Field(default_factory=list)
    physical: Physical | None = None
    # alternate names for the whole mart ("удержание"/"retention" for dm.cohort_retention);
    # same contract as Column.synonyms — see there
    synonyms: list[str] = Field(default_factory=list)

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)


class Join(StrictModel):
    left: str  # "dm.sales_daily.store_id"
    right: str  # "dm.stores.id"
    type: str = "many_to_one"


class Metric(StrictModel):
    name: str
    sql: str
    description: str = ""


class SemanticModel(StrictModel):
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
