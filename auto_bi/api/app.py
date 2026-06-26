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
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from auto_bi.adapters.base import DashboardRef
from auto_bi.advisor.core import Advisor
from auto_bi.agent.autospec import build_auto_spec
from auto_bi.agent.insights import analyze_spec
from auto_bi.agent.machine import AgentPhase, AgentTurn
from auto_bi.agent.propose import SpecValidationError
from auto_bi.agent.seed import validate_seed
from auto_bi.api.schemas import (
    AutoSessionRequest,
    BuildEvent,
    ColumnUpdate,
    DCRStatusUpdate,
    LoginRequest,
    ReplyRequest,
    SessionState,
    StartSessionRequest,
    TableUpdate,
    TurnResponse,
)
from auto_bi.api.sessions import ManagedSession, SessionManager, UnknownSession
from auto_bi.auth import (
    ANONYMOUS_ADMIN,
    AuthUser,
    filter_model_by_schemas,
    forbidden_tables,
    is_table_allowed,
    new_token,
    verify_password,
)
from auto_bi.dmcr import DCR_STATUSES, render_dm_change_request
from auto_bi.introspect.base import RunQuery
from auto_bi.introspect.gaps import find_gaps
from auto_bi.ir.spec import DashboardSpec, TargetBI
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
    run_query: RunQuery | None = None,  # read-only seam for the "Что видно" insight layer
    store: Store | None = None,
    builder: Builder | None = None,
    include_samples: bool = True,
    model_path: str | Path | None = None,  # enables enrichment writes (task 2.7)
    auth_enabled: bool = False,  # Phase 4 auth/RBAC, opt-in (default: open, single-user)
    auth_token_ttl_hours: int = 24,
) -> FastAPI:
    manager = SessionManager(
        model=model,
        llm=llm,
        advisor=advisor,
        store=store,
        include_samples=include_samples,
    )
    app = FastAPI(title="Auto_BI API", version="0.1.0")

    # paths reachable without a token even when auth is on (login issues the token)
    _open_paths = {"/api/v1/health", "/api/v1/auth/login"}

    def _bearer(header: str | None) -> str | None:
        if header and header.lower().startswith("bearer "):
            return header[7:].strip()
        return None

    def _resolve_user(request: Request) -> AuthUser | None:
        # bearer header for API/CLI clients; cookie for the browser UI (EventSource/SSE
        # cannot set headers, so the login cookie rides automatically on every request)
        token = _bearer(request.headers.get("authorization")) or request.cookies.get("auth_token")
        row = store.token_user(token) if (store is not None and token) else None
        if row is None:
            return None
        return AuthUser(
            username=row["username"], role=row["role"], allowed_schemas=row["allowed_schemas"]
        )

    def _user(request: Request) -> AuthUser:
        return getattr(request.state, "user", ANONYMOUS_ADMIN)

    @app.middleware("http")
    async def gate(request: Request, call_next):
        # CSRF guard: browsers attach Origin to mutating requests, curl/CLI/SSE GETs carry
        # none and pass (F5). Only stops drive-by mutations from other sites.
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.headers.get("host", ""):
                return JSONResponse(
                    status_code=403, content={"detail": "cross-origin mutation rejected"}
                )
        # auth (Phase 4, opt-in). auth OFF -> every caller is the anonymous admin with
        # full access, so behaviour is unchanged. auth ON -> /api/v1/* (except health and
        # login) needs a valid bearer token; the resolved user rides on request.state.
        request.state.user = ANONYMOUS_ADMIN
        if auth_enabled:
            path = request.url.path
            if path.startswith("/api/v1/") and path not in _open_paths:
                user = _resolve_user(request)
                if user is None:
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "authentication required"},
                        headers={"WWW-Authenticate": "Bearer"},
                    )
                request.state.user = user
        return await call_next(request)

    def _apply_target_bi(managed: ManagedSession) -> None:
        # The UI BI selector (F8) fixes the build target per session; the agent's spec is
        # BI-agnostic and the LLM patch resets target_bi to its default each turn, so the
        # session choice is (re-)stamped onto the current spec. turn.spec is the same object
        # the agent holds, so the response and the later approve both see it.
        if managed.agent.spec is not None:
            managed.agent.spec.target_bi = managed.target_bi

    def _turn(managed: ManagedSession, turn: AgentTurn, error: str = "") -> TurnResponse:
        return TurnResponse(session_id=managed.session_id, error=error, **turn.model_dump())

    def _get(session_id: str) -> ManagedSession:
        try:
            return manager.get(session_id)
        except UnknownSession:
            raise HTTPException(status_code=404, detail=f"unknown session {session_id!r}") from None

    def _owned(session_id: str, request: Request) -> ManagedSession:
        # session-scoped access: only the owner or an admin may address a session. auth off
        # -> owner is None and the caller is the anonymous admin, so this is a no-op. A
        # mismatch returns 404 (not 403) so a foreign session id can't be probed for existence.
        managed = _get(session_id)
        user = _user(request)
        if auth_enabled and not user.is_admin and managed.owner != user.username:
            raise HTTPException(status_code=404, detail=f"unknown session {session_id!r}")
        return managed

    @app.get("/api/v1/health")
    def health() -> dict:
        return {"ok": True, "auth": auth_enabled}

    # --- auth (Phase 4, opt-in) ----------------------------------------------------

    def _user_public(user: AuthUser) -> dict:
        return {"username": user.username, "role": user.role, "schemas": user.allowed_schemas}

    @app.post("/api/v1/auth/login")
    def login(body: LoginRequest, response: Response) -> dict:
        if not auth_enabled:
            raise HTTPException(status_code=404, detail="auth is disabled")
        row = _store().get_user(body.username)
        # one opaque 401 whether the username or the password is wrong (don't leak which)
        if row is None or not verify_password(body.password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="invalid username or password")
        token = _store().create_token(new_token(), row["id"], auth_token_ttl_hours)
        # cookie for the browser (HttpOnly so JS can't read it; SameSite=Lax + the CSRF
        # Origin guard mitigate cross-site use). The token is also returned for CLI clients.
        response.set_cookie(
            "auth_token",
            token,
            max_age=auth_token_ttl_hours * 3600,
            httponly=True,
            samesite="lax",
        )
        user = AuthUser(
            username=row["username"], role=row["role"], allowed_schemas=row["allowed_schemas"]
        )
        return {"token": token, "user": _user_public(user)}

    @app.post("/api/v1/auth/logout", status_code=204)
    def logout(request: Request, response: Response) -> None:
        token = _bearer(request.headers.get("authorization")) or request.cookies.get("auth_token")
        if token and store is not None:
            store.delete_token(token)
        response.delete_cookie("auth_token")

    @app.get("/api/v1/auth/me")
    def auth_me(request: Request) -> dict:
        return _user_public(_user(request))

    @app.post("/api/v1/sessions", response_model=TurnResponse, response_model_exclude_none=True)
    def start_session(body: StartSessionRequest, request: Request) -> TurnResponse:
        # RBAC: the agent grounds only on the caller's allowed schemas (auth off -> all)
        scoped_model = filter_model_by_schemas(model, _user(request).allowed_schemas)
        if body.seed is not None:
            # the UI builds its field panel from GET /model/fields, so an unknown
            # field is protocol misuse (422), not an ambiguity for CLARIFY. Validate
            # against the scoped model so a forbidden field is rejected too.
            errors = validate_seed(body.seed, scoped_model)
            if errors:
                raise HTTPException(status_code=422, detail="; ".join(errors))
        try:
            managed, turn = manager.start(
                body.request,
                seed=body.seed,
                target_bi=body.target_bi or TargetBI.SUPERSET,
                model=scoped_model,
                owner=_user(request).username if auth_enabled else None,
            )
        except LLMError as exc:
            # nothing was registered (F2): tell the client plainly instead of a bare 500
            raise HTTPException(status_code=502, detail=f"LLM failed to start: {exc}") from None
        _apply_target_bi(managed)
        return _turn(managed, turn)

    @app.post(
        "/api/v1/sessions/auto", response_model=TurnResponse, response_model_exclude_none=True
    )
    def start_auto_session(body: AutoSessionRequest, request: Request) -> TurnResponse:
        # Auto-overview: a curated dashboard built deterministically from one datamart
        # (no text, no LLM). RBAC: scope to allowed schemas, then hard-gate the table the
        # build runs over (auto builds over exactly this table; auth off -> always allowed).
        scoped_model = filter_model_by_schemas(model, _user(request).allowed_schemas)
        if scoped_model.table(body.table) is None:
            raise HTTPException(status_code=404, detail=f"unknown table {body.table!r}")
        if not is_table_allowed(body.table, _user(request).allowed_schemas):
            raise HTTPException(status_code=403, detail=f"not allowed: {body.table!r}")
        target = body.target_bi or TargetBI.SUPERSET
        try:
            spec = build_auto_spec(
                scoped_model, body.table, max_charts=body.max_charts, target_bi=target
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        managed, turn = manager.start_auto(
            spec,
            target_bi=target,
            model=scoped_model,
            owner=_user(request).username if auth_enabled else None,
        )
        return _turn(managed, turn)

    @app.delete("/api/v1/sessions/{session_id}", status_code=204)
    def delete_session(session_id: str, request: Request) -> None:
        managed = _owned(session_id, request)
        if managed.build_status == "building":
            raise HTTPException(status_code=409, detail="build is running")
        manager.remove(session_id)  # the durable record stays in Store

    # --- enrichment (task 2.7): gaps -> inline edits -> commit model.yaml ----------
    # one lock serializes model mutation + dump; agent sessions read the same object,
    # so an enrichment edit becomes visible to the NEXT grounding call — by design
    model_write_lock = threading.Lock()

    @app.get("/api/v1/model/gaps")
    def model_gaps(request: Request) -> dict:
        # offline checks only: live time-grain probes stay in `auto_bi gaps` (CLI).
        # RBAC: scope to the caller's allowed schemas (auth off -> full model).
        scoped = filter_model_by_schemas(model, _user(request).allowed_schemas)
        return find_gaps(scoped, None).model_dump(mode="json")

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

    def _require_table_access(table_name: str, request: Request) -> None:
        # RBAC: enrichment edits mutate the shared model.yaml globally, so a user may only
        # edit tables in their own schemas (auth off -> ['*'] -> always allowed)
        if not is_table_allowed(table_name, _user(request).allowed_schemas):
            raise HTTPException(status_code=403, detail=f"not allowed to edit table {table_name!r}")

    @app.patch("/api/v1/model/tables/{table_name}")
    def update_table(table_name: str, body: TableUpdate, request: Request) -> dict:
        _require_table_access(table_name, request)  # RBAC before anything else (403 > 503)
        path = _model_path()
        with model_write_lock:
            table = _get_table(table_name)
            table.description = body.description.strip()
            model.dump(path)
        return {"table": table_name, "description": table.description}

    @app.patch("/api/v1/model/tables/{table_name}/columns/{column_name}")
    def update_column(
        table_name: str, column_name: str, body: ColumnUpdate, request: Request
    ) -> dict:
        _require_table_access(table_name, request)  # RBAC before anything else (403 > 503)
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
    def model_fields(request: Request) -> list[dict]:
        """Field panel for the fields-first mode: the semantic model as the UI sees it.
        RBAC: only the caller's allowed-schema tables (auth off -> all tables)."""
        scoped = filter_model_by_schemas(model, _user(request).allowed_schemas)
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
            for t in scoped.tables
        ]

    @app.post(
        "/api/v1/sessions/{session_id}/reply",
        response_model=TurnResponse,
        response_model_exclude_none=True,
    )
    def reply(session_id: str, body: ReplyRequest, request: Request) -> TurnResponse:
        managed = _owned(session_id, request)
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
            _apply_target_bi(managed)  # the patch reset spec.target_bi -> re-stamp the choice
            return _turn(managed, turn)

    @app.post("/api/v1/sessions/{session_id}/approve", status_code=202)
    def approve(session_id: str, request: Request) -> dict:
        if builder is None:
            raise HTTPException(status_code=503, detail="build is not wired (no BI configured)")
        managed = _owned(session_id, request)
        with managed.lock:
            if managed.build_status == "building":
                raise HTTPException(status_code=409, detail="build already running")
            _apply_target_bi(managed)  # the build dispatches on spec.target_bi (F8/F1)
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
                # RBAC hard gate (checked on the spec to be built, BEFORE the machine
                # transition so a denied approve has no side effect): never build over a
                # table outside the caller's schemas. Grounding was already scoped, so this
                # is defense in depth; auth off -> allowed_schemas ['*'] -> always empty.
                denied = forbidden_tables(current, _user(request).allowed_schemas)
                if denied:
                    raise HTTPException(
                        status_code=403,
                        detail=f"not allowed to build over tables outside your schemas: {denied}",
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

        def _trace_build(
            kind: str, *, status: str = "ok", latency_ms: int = 0, detail: str = ""
        ) -> None:
            if store is None:
                return
            try:
                store.add_trace_event(
                    managed.session_id,
                    kind=kind,
                    status=status,
                    latency_ms=latency_ms,
                    detail=detail,
                )
            except Exception:  # tracing must never kill the build
                logger.exception("failed to record build trace event")

        def _build() -> None:
            started = time.monotonic()
            _trace_build("build_start", detail=spec.title)
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
                _trace_build(
                    "build_error",
                    status="error",
                    latency_ms=round((time.monotonic() - started) * 1000),
                    detail=str(exc)[:200],
                )
                return
            managed.build_status = "built"
            managed.dashboard_url = ref.url
            managed.add_event(BuildEvent(kind="done", text=ref.title, url=ref.url))
            _trace_build(
                "build_done",
                latency_ms=round((time.monotonic() - started) * 1000),
                detail=ref.title,
            )

        threading.Thread(target=_build, name=f"build-{managed.session_id}", daemon=True).start()
        return {"session_id": managed.session_id, "status": "building"}

    @app.get("/api/v1/sessions/{session_id}", response_model=SessionState)
    def session_state(session_id: str, request: Request) -> SessionState:
        managed = _owned(session_id, request)
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
        _store().set_dm_change_request_status(request_id, body.status)
        return {"id": request_id, "status": body.status}

    # --- observability (Phase 4): per-session trace + LLM-usage dashboard ----------

    @app.get("/api/v1/sessions/{session_id}/trace")
    def session_trace(session_id: str, request: Request) -> dict:
        """Durable per-session timeline: agent/build steps + the LLM calls they made.
        Reads the store directly (survives registry eviction); unknown id -> empty.
        When auth is on, the session must be owned by the caller (admin sees all);
        auth off keeps the eviction-surviving direct read unchanged."""
        if auth_enabled:
            _owned(session_id, request)  # 404 for a session the caller doesn't own
        s = _store()
        return {
            "session_id": session_id,
            "events": s.trace_events(session_id),
            "llm_calls": s.llm_calls(session_id),
            "llm_usage": s.llm_usage_summary(session_id),
        }

    @app.get("/api/v1/observability/llm")
    def observability_llm() -> dict:
        """Global LLM-usage aggregates. GraceKelly exposes no token/cost usage, so
        char volumes are size proxies (not tokens or money) — see the schema docs."""
        return _store().llm_usage_summary()

    @app.get("/api/v1/sessions/{session_id}/insights")
    def session_insights(session_id: str, request: Request) -> dict:
        """Deterministic 'Что видно' observations over the session's current spec.

        Runs each chart read-only and reports trend / reversal or change of pace /
        seasonality / spike or dip / leader+concentration (or even spread) / largest share
        (auto_bi.agent.insights).
        A separate surface from the dashboard, best-effort: a chart that fails to run is
        skipped. 503 when no DWH connection is configured; empty list when the session has
        no spec yet."""
        managed = _owned(session_id, request)
        spec = managed.agent.spec
        if spec is None:
            return {"session_id": session_id, "observations": []}
        if run_query is None:
            raise HTTPException(status_code=503, detail="insights need a DWH connection")
        ins = analyze_spec(spec, model, run_query)
        return {
            "session_id": session_id,
            "table": ins.table,
            "observations": [
                {
                    "chart_id": o.chart_id,
                    "kind": o.kind,
                    "text": o.text,
                    "value": o.value,
                    "subject": o.subject,
                }
                for o in ins.observations
            ],
        }

    @app.get("/api/v1/sessions/{session_id}/events")
    def build_events(session_id: str, request: Request) -> StreamingResponse:
        managed = _owned(session_id, request)
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
