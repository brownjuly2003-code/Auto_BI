"""API request/response models (task 2.1).

TurnResponse mirrors AgentTurn (the machine stays UI-agnostic) plus the HTTP-level
fields: session_id so the client can address the session, and error for a failed
word edit that must NOT lose the session (F6 contract, now over HTTP).
"""

from __future__ import annotations

from pydantic import BaseModel

from auto_bi.agent.machine import AgentTurn


class StartSessionRequest(BaseModel):
    request: str


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


class BuildEvent(BaseModel):
    kind: str  # log | done | error
    text: str = ""
    url: str = ""

    def sse(self) -> str:
        return f"event: {self.kind}\ndata: {self.model_dump_json()}\n\n"
