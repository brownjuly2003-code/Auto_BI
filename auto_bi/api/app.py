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
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from auto_bi.adapters.base import DashboardRef
from auto_bi.advisor.core import Advisor
from auto_bi.agent.machine import AgentPhase, AgentTurn
from auto_bi.agent.propose import SpecValidationError
from auto_bi.agent.seed import validate_seed
from auto_bi.api.schemas import (
    BuildEvent,
    ColumnUpdate,
    DCRStatusUpdate,
    ReplyRequest,
    SessionState,
    StartSessionRequest,
    TableUpdate,
    TurnResponse,
)
from auto_bi.api.sessions import ManagedSession, SessionManager, UnknownSession
from auto_bi.dmcr import DCR_STATUSES, render_dm_change_request
from auto_bi.introspect.gaps import find_gaps
from auto_bi.ir.spec import DashboardSpec
from auto_bi.ir.validate import validate_spec
from auto_bi.llm.base import LLMClient, LLMError
from auto_bi.semantic.model import Aggregation, ColumnRole, SemanticModel
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
    model_path: str | Path | None = None,  # enables enrichment writes (task 2.7)
) -> FastAPI:
    manager = SessionManager(
        model=model,
        llm=llm,
        advisor=advisor,
        store=store,
        include_samples=include_samples,
    )
    app = FastAPI(title="Auto_BI API", version="0.1.0")

    @app.middleware("http")
    async def reject_cross_origin_mutations(request, call_next):
        # the API is unauthenticated by design (localhost, single user — §2.1);
        # this only stops drive-by CSRF from other sites: browsers attach Origin
        # to mutating requests, curl/CLI/SSE GETs carry none and pass (F5)
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.headers.get("host", ""):
                return JSONResponse(
                    status_code=403, content={"detail": "cross-origin mutation rejected"}
                )
        return await call_next(request)

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
        if body.seed is not None:
            # the UI builds its field panel from GET /model/fields, so an unknown
            # field is protocol misuse (422), not an ambiguity for CLARIFY
            errors = validate_seed(body.seed, model)
            if errors:
                raise HTTPException(status_code=422, detail="; ".join(errors))
        try:
            managed, turn = manager.start(body.request, seed=body.seed)
        except LLMError as exc:
            # nothing was registered (F2): tell the client plainly instead of a bare 500
            raise HTTPException(status_code=502, detail=f"LLM failed to start: {exc}") from None
        return _turn(managed, turn)

    @app.delete("/api/v1/sessions/{session_id}", status_code=204)
    def delete_session(session_id: str) -> None:
        managed = _get(session_id)
        if managed.build_status == "building":
            raise HTTPException(status_code=409, detail="build is running")
        manager.remove(session_id)  # the durable record stays in Store

    # --- enrichment (task 2.7): gaps -> inline edits -> commit model.yaml ----------
    # one lock serializes model mutation + dump; agent sessions read the same object,
    # so an enrichment edit becomes visible to the NEXT grounding call — by design
    model_write_lock = threading.Lock()

    @app.get("/api/v1/model/gaps")
    def model_gaps() -> dict:
        # offline checks only: live time-grain probes stay in `auto_bi gaps` (CLI)
        return find_gaps(model, None).model_dump(mode="json")

    def _model_path() -> Path:
        if model_path is None:
            raise HTTPException(
                status_code=503, detail="model editing is not wired (no model_path)"
            )
        return Path(model_path)

    def _get_table(table_name: str):
        table = model.table(table_name)
        if table is None:
            raise HTTPException(status_code=404, detail=f"unknown table {table_name!r}")
        return table

    @app.patch("/api/v1/model/tables/{table_name}")
    def update_table(table_name: str, body: TableUpdate) -> dict:
        path = _model_path()
        with model_write_lock:
            table = _get_table(table_name)
            table.description = body.description.strip()
            model.dump(path)
        return {"table": table_name, "description": table.description}

    @app.patch("/api/v1/model/tables/{table_name}/columns/{column_name}")
    def update_column(table_name: str, column_name: str, body: ColumnUpdate) -> dict:
        path = _model_path()
        with model_write_lock:
            table = _get_table(table_name)
            column = table.column(column_name)
            if column is None:
                raise HTTPException(
                    status_code=404, detail=f"unknown column {table_name}.{column_name}"
                )
            role = column.role
            if body.role is not None:
                try:
                    role = ColumnRole(body.role)
                except ValueError:
                    raise HTTPException(
                        status_code=422,
                        detail=f"role must be one of {[r.value for r in ColumnRole]}",
                    ) from None
            agg = column.agg
            if body.agg is not None:
                try:
                    agg = Aggregation(body.agg) if body.agg else None
                except ValueError:
                    raise HTTPException(
                        status_code=422,
                        detail=f"agg must be one of {[a.value for a in Aggregation]} or empty",
                    ) from None
            if role != ColumnRole.MEASURE:
                if body.agg:  # explicit agg on a non-measure is a contradiction (F9)
                    raise HTTPException(
                        status_code=422, detail="agg is only valid for role=measure"
                    )
                agg = None  # leaving the measure role drops the default aggregation
            column.role = role
            column.agg = agg
            if body.description is not None:
                column.description = body.description.strip()
            model.dump(path)
        return {
            "table": table_name,
            "column": column_name,
            "description": column.description,
            "role": column.role.value,
            "agg": column.agg.value if column.agg else None,
        }

    @app.get("/api/v1/model/fields")
    def model_fields() -> list[dict]:
        """Field panel for the fields-first mode: the semantic model as the UI sees it."""
        return [
            {
                "table": t.name,
                "description": t.description,
                "columns": [
                    {
                        "name": c.name,
                        "role": c.role.value,
                        "type": c.type,
                        "description": c.description,
                        "agg": c.agg.value if c.agg else None,
                    }
                    for c in t.columns
                ],
            }
            for t in model.tables
        ]

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
            current = managed.agent.spec
            if current is not None:
                # the model is shared and mutable (enrichment PATCH, task 2.7): a role
                # edit can invalidate a spec proposed earlier — fail HERE with a clear
                # message, not minutes later inside the build thread (F6)
                problems = validate_spec(current, model)
                if problems:
                    raise HTTPException(
                        status_code=409,
                        detail="spec no longer valid against the model "
                        f"(edited since the proposal?): {'; '.join(problems)}",
                    )
            if managed.build_status == "failed" and managed.agent.phase == AgentPhase.APPROVED:
                # a failed build leaves the machine in APPROVED with no pending edit:
                # retry must rebuild the same approved spec, not dead-end on 409
                spec = managed.agent.spec
                assert spec is not None
            else:
                try:
                    spec = managed.agent.approve()
                except RuntimeError as exc:
                    raise HTTPException(status_code=409, detail=str(exc)) from None
            managed.reset_events()  # iteration re-approve: fresh stream for this build
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
            # None = idle heartbeat: an SSE comment clients ignore, but writing it
            # surfaces a dropped connection and frees the worker thread (F4)
            (": ping\n\n" if event is None else event.sse() for event in managed.stream_events()),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    return app
