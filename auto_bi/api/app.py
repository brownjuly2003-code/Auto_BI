"""FastAPI app over the agent core (task 2.1).

Everything is injected (model, llm, advisor, store, builder) so tests run on a
scripted LLM and a fake builder; the production wiring lives in `auto_bi serve`.
The agent core stays HTTP-free: this module only translates AgentTurn <-> JSON
and pumps compile_and_build log lines into the SSE event buffer.

Contract notes:
- a failed word edit returns 200 with `error` set and the previous spec intact
  (the F6 rule: an edit must never lose the session) — 4xx is reserved for
  protocol misuse (unknown session, wrong phase);
- approve returns 202 immediately; the build runs in a thread and reports through
  GET .../events (SSE: `log` lines, then terminal `done`/`error`).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from auto_bi.adapters.base import DashboardRef
from auto_bi.advisor.core import Advisor
from auto_bi.agent.machine import AgentPhase, AgentTurn
from auto_bi.agent.propose import SpecValidationError
from auto_bi.api.schemas import (
    BuildEvent,
    DCRStatusUpdate,
    ReplyRequest,
    SessionState,
    StartSessionRequest,
    TurnResponse,
)
from auto_bi.api.sessions import ManagedSession, SessionManager, UnknownSession
from auto_bi.dmcr import DCR_STATUSES, render_dm_change_request
from auto_bi.ir.spec import DashboardSpec
from auto_bi.llm.base import LLMClient, LLMError
from auto_bi.semantic.model import SemanticModel
from auto_bi.store import Store

logger = logging.getLogger(__name__)

# builder(spec, log, session_id) -> DashboardRef; production wraps compile_and_build
Builder = Callable[[DashboardSpec, Callable[[str], None], str], DashboardRef]


def create_app(
    *,
    model: SemanticModel,
    llm: LLMClient,
    advisor: Advisor | None = None,
    store: Store | None = None,
    builder: Builder | None = None,
    include_samples: bool = True,
) -> FastAPI:
    manager = SessionManager(
        model=model,
        llm=llm,
        advisor=advisor,
        store=store,
        include_samples=include_samples,
    )
    app = FastAPI(title="Auto_BI API", version="0.1.0")

    def _turn(managed: ManagedSession, turn: AgentTurn, error: str = "") -> TurnResponse:
        return TurnResponse(session_id=managed.session_id, error=error, **turn.model_dump())

    def _get(session_id: str) -> ManagedSession:
        try:
            return manager.get(session_id)
        except UnknownSession:
            raise HTTPException(status_code=404, detail=f"unknown session {session_id!r}") from None

    @app.get("/api/v1/health")
    def health() -> dict:
        return {"ok": True}

    @app.post("/api/v1/sessions", response_model=TurnResponse, response_model_exclude_none=True)
    def start_session(body: StartSessionRequest) -> TurnResponse:
        managed, turn = manager.start(body.request)
        return _turn(managed, turn)

    @app.post(
        "/api/v1/sessions/{session_id}/reply",
        response_model=TurnResponse,
        response_model_exclude_none=True,
    )
    def reply(session_id: str, body: ReplyRequest) -> TurnResponse:
        managed = _get(session_id)
        with managed.lock:
            agent = managed.agent
            try:
                turn = agent.reply(body.text)
            except (SpecValidationError, LLMError) as exc:
                # the machine kept the previous valid spec and stayed in its phase
                current = AgentTurn(phase=agent.phase, spec=agent.spec, verdicts=agent.verdicts)
                return _turn(managed, current, error=str(exc))
            except RuntimeError as exc:  # no user turn expected in this phase
                raise HTTPException(status_code=409, detail=str(exc)) from None
            return _turn(managed, turn)

    @app.post("/api/v1/sessions/{session_id}/approve", status_code=202)
    def approve(session_id: str) -> dict:
        if builder is None:
            raise HTTPException(status_code=503, detail="build is not wired (no BI configured)")
        managed = _get(session_id)
        with managed.lock:
            if managed.build_status == "building":
                raise HTTPException(status_code=409, detail="build already running")
            try:
                spec = managed.agent.approve()
            except RuntimeError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from None
            managed.build_status = "building"

        def _build() -> None:
            try:
                ref = builder(
                    spec,
                    lambda line: managed.add_event(BuildEvent(kind="log", text=line)),
                    managed.session_id,
                )
            except Exception as exc:
                logger.exception("build failed for session %s", managed.session_id)
                managed.build_status = "failed"
                managed.add_event(BuildEvent(kind="error", text=str(exc)))
                return
            managed.build_status = "built"
            managed.dashboard_url = ref.url
            managed.add_event(BuildEvent(kind="done", text=ref.title, url=ref.url))

        threading.Thread(target=_build, name=f"build-{managed.session_id}", daemon=True).start()
        return {"session_id": managed.session_id, "status": "building"}

    @app.get("/api/v1/sessions/{session_id}", response_model=SessionState)
    def session_state(session_id: str) -> SessionState:
        managed = _get(session_id)
        return SessionState(
            session_id=managed.session_id,
            phase=managed.agent.phase.value,
            build_status=managed.build_status,
            dashboard_url=managed.dashboard_url,
        )

    def _store() -> Store:
        if store is None:
            raise HTTPException(status_code=503, detail="store is not configured")
        return store

    @app.get("/api/v1/dm-change-requests")
    def list_dm_change_requests(status: str | None = None) -> list[dict]:
        return _store().dm_change_requests(status)

    @app.get("/api/v1/dm-change-requests/{request_id}")
    def dm_change_request(request_id: int) -> dict:
        row = _store().dm_change_request(request_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"unknown dm_change_request {request_id}")
        return {**row, "markdown": render_dm_change_request(row)}

    @app.patch("/api/v1/dm-change-requests/{request_id}")
    def update_dm_change_request(request_id: int, body: DCRStatusUpdate) -> dict:
        if body.status not in DCR_STATUSES:
            raise HTTPException(
                status_code=422,
                detail=f"status must be one of {DCR_STATUSES}, got {body.status!r}",
            )
        if _store().dm_change_request(request_id) is None:
            raise HTTPException(status_code=404, detail=f"unknown dm_change_request {request_id}")
        store.set_dm_change_request_status(request_id, body.status)
        return {"id": request_id, "status": body.status}

    @app.get("/api/v1/sessions/{session_id}/events")
    def build_events(session_id: str) -> StreamingResponse:
        managed = _get(session_id)
        if managed.agent.phase != AgentPhase.APPROVED and managed.build_status == "idle":
            raise HTTPException(status_code=409, detail="no build to stream: approve first")
        return StreamingResponse(
            (event.sse() for event in managed.stream_events()),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    return app
