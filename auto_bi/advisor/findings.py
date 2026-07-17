"""Feasibility Advisor findings (ARCHITECTURE §3.3, D9).

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


class Remediation(BaseModel):
    """A concrete, ready-to-hand artifact that resolves a ``dm_change_request`` finding.

    Generated DETERMINISTICALLY by the rule from physical metadata — like the verdict
    itself, never by the LLM (D9). It turns the change request from "this access path
    isn't in the DM design" into "here is the DDL / denormalising mart to make it one",
    so the DM owner can act without re-deriving the fix. Advisory-only: a remediation is
    a suggested artifact for a human to review, not something the agent applies.
    """

    kind: str  # "ch_projection" | "denormalised_mart" | "gp_redistribute" | ...
    summary: str  # one-line human description ("ClickHouse projection ordered by manager_id")
    ddl: str  # the actual artifact: DDL / dbt model SQL the DM owner can run
    rationale: str = ""  # why this addresses the finding (deterministic, not an LLM claim)


class Finding(BaseModel):
    rule: str  # stable rule id, e.g. "filter_not_in_sorting_key_prefix"
    severity: Severity
    verdict_class: VerdictClass
    chart_id: str
    title: str  # short machine summary; the LLM turns this into a verdict in 1.7
    evidence: dict = Field(default_factory=dict)  # measured/derived facts behind the verdict
    suggestions: list[str] = Field(default_factory=list)  # mechanical alternatives
    # a concrete fix artifact for dm_change_request verdicts (None for spec_adjustment,
    # where the fix is "change the query", already spelled out in `suggestions`)
    remediation: Remediation | None = None
