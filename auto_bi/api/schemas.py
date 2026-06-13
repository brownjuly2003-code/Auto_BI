"""API request/response models (task 2.1).

TurnResponse mirrors AgentTurn (the machine stays UI-agnostic) plus the HTTP-level
fields: session_id so the client can address the session, and error for a failed
word edit that must NOT lose the session (F6 contract, now over HTTP).
"""

from __future__ import annotations

from pydantic import BaseModel, model_validator

from auto_bi.agent.machine import AgentTurn
from auto_bi.agent.seed import FieldsSeed


class StartSessionRequest(BaseModel):
    request: str = ""
    seed: FieldsSeed | None = None  # fields-first entry (task 2.3)

    @model_validator(mode="after")
    def _at_least_one_input(self) -> StartSessionRequest:
        if not self.request.strip() and self.seed is None:
            raise ValueError("either request text or a fields seed is required")
        return self


class ReplyRequest(BaseModel):
    text: str


class TurnResponse(AgentTurn):
    session_id: str
    error: str = ""


class SessionState(BaseModel):
    session_id: str
    phase: str
    build_status: str  # idle | building | built | failed
    dashboard_url: str = ""


class DCRStatusUpdate(BaseModel):
    status: str  # validated against dmcr.DCR_STATUSES in the handler


class TableUpdate(BaseModel):
    """Enrichment (task 2.7): table-level edits; only description is editable."""

    description: str


class ColumnUpdate(BaseModel):
    """Enrichment (task 2.7): column edits; None = leave as is.

    role/agg are validated against the model enums in the handler (422 on
    unknown values; agg is only meaningful on measures — mirror of F9)."""

    description: str | None = None
    role: str | None = None
    agg: str | None = None


class BuildEvent(BaseModel):
    kind: str  # log | done | error
    text: str = ""
    url: str = ""

    def sse(self) -> str:
        return f"event: {self.kind}\ndata: {self.model_dump_json()}\n\n"
