"""Feasibility Advisor findings (ARCHITECTURE §3.6, D9).

The verdict is decided by CODE (deterministic rules + measured EXPLAIN evidence);
the LLM only narrates these findings later (task 1.7). The advisor is advisory-only —
it never blocks a build (CLAUDE.md invariant 5).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Severity(StrEnum):
    INFO = "info"
    WARN = "warn"
    CRITICAL = "critical"


class VerdictClass(StrEnum):
    OK = "ok"
    SPEC_ADJUSTMENT = "spec_adjustment"  # fix the query/filters/grain
    DM_CHANGE_REQUEST = "dm_change_request"  # the DM isn't designed for this access path


class Finding(BaseModel):
    rule: str  # stable rule id, e.g. "filter_not_in_sorting_key_prefix"
    severity: Severity
    verdict_class: VerdictClass
    chart_id: str
    title: str  # short machine summary; the LLM turns this into a verdict in 1.7
    evidence: dict = Field(default_factory=dict)  # measured/derived facts behind the verdict
    suggestions: list[str] = Field(default_factory=list)  # mechanical alternatives
