"""In-memory session registry for the HTTP API (task 2.1).

One ManagedSession per dialogue: the AgentSession itself, a per-session lock that
serializes turns (LLM calls are long; two concurrent replies on one session would
corrupt the machine), and the build event buffer. Events are buffered, not only
streamed: an SSE client that connects after the build started replays the history
and then follows live. Process-local, but no longer process-bound (X-4): a registry
miss falls back to rehydrating the session from its durable Store record, so a server
restart (or eviction past MAX_SESSIONS) does not lose dialogues — see `get`/`_hydrate`.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator, Mapping

from auto_bi.advisor.core import Advisor
from auto_bi.agent.machine import AgentPhase, AgentSession, AgentTurn
from auto_bi.agent.seed import FieldsSeed, seed_tables
from auto_bi.api.schemas import BuildEvent
from auto_bi.auth import filter_model_by_schemas
from auto_bi.ir.spec import DashboardSpec, TargetBI
from auto_bi.llm.base import LLMClient
from auto_bi.semantic.model import SemanticModel
from auto_bi.store import Store

TERMINAL_EVENTS = ("done", "error")
MAX_SESSIONS = 200  # registry cap: oldest idle sessions are evicted past this (F3)


class UnknownSession(KeyError):
    pass


class ManagedSession:
    def __init__(
        self,
        session_id: str,
        agent: AgentSession,
        target_bi: TargetBI = TargetBI.SUPERSET,
        owner: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.agent = agent
        # owner username when auth is on (RBAC: only the owner or an admin may address
        # this session); None when auth is off — every caller is the anonymous admin
        self.owner = owner
        # BI target chosen by the UI selector (F8), fixed for the session like the
        # text/fields mode. The IR is BI-agnostic (invariant 1), so this matters only at
        # build; the API re-applies it to the spec after each turn (the LLM patch resets
        # spec.target_bi to its default).
        self.target_bi = target_bi
        self.lock = threading.Lock()
        self.build_status = "idle"
        self.dashboard_url = ""
        self._events: list[BuildEvent] = []
        self._events_cond = threading.Condition()

    def add_event(self, event: BuildEvent) -> None:
        with self._events_cond:
            self._events.append(event)
            self._events_cond.notify_all()

    def reset_events(self) -> None:
        """New build (iteration re-approve) = fresh buffer; the previous build's
        stream already ended on its terminal event, late readers of the old list
        keep their own reference."""
        with self._events_cond:
            self._events = []

    def stream_events(self, poll_seconds: float = 15.0) -> Iterator[BuildEvent | None]:
        """Replay buffered events, then follow live until a terminal event.

        Yields None as an idle heartbeat every poll_seconds: the HTTP layer turns
        it into an SSE comment, so a vanished client fails the next write and the
        worker thread serving the stream is released instead of waiting forever
        (F4, phase-2 audit).
        """
        with self._events_cond:
            events = self._events  # pin THIS build's buffer: reset swaps the list object
        index = 0
        while True:
            with self._events_cond:
                if index >= len(events):
                    self._events_cond.wait(poll_seconds)
                batch = events[index:]
                index = len(events)
            if not batch:
                yield None
                continue
            for event in batch:
                yield event
                if event.kind in TERMINAL_EVENTS:
                    return


class SessionManager:
    def __init__(
        self,
        *,
        model: SemanticModel,
        llm: LLMClient,
        advisor: Advisor | None = None,
        store: Store | None = None,
        include_samples: bool = True,
        # target -> BI host (same mapping create_app gets, F-1): hydration re-absolutizes
        # the dashboard url stored by the build pipeline, which is BI-relative
        bi_base_urls: Mapping[TargetBI, str] | None = None,
    ) -> None:
        self._model = model
        self._llm = llm
        self._advisor = advisor
        self._store = store
        self._include_samples = include_samples
        self._bi_base_urls = bi_base_urls or {}
        self._sessions: dict[str, ManagedSession] = {}
        self._registry_lock = threading.Lock()

    def start(
        self,
        request: str,
        seed: FieldsSeed | None = None,
        target_bi: TargetBI = TargetBI.SUPERSET,
        model: SemanticModel | None = None,
        owner: str | None = None,
    ) -> tuple[ManagedSession, AgentTurn]:
        # `model` overrides the app-wide model for this session — the API passes an
        # RBAC-filtered view so the agent grounds only on the caller's allowed schemas
        # (auto_bi.auth.filter_model_by_schemas). None -> the full app model (default).
        if self._store is not None:
            # the durable per-message record gets the full rendered seed; the session
            # row keeps a short human label so the list view stays scannable
            label = request
            if not label and seed is not None:
                label = f"[fields-first] групп: {len(seed.groups)}"
            session_id = self._store.create_session(
                label,
                owner=owner,
                target_bi=target_bi.value,
                pinned=seed_tables(seed) if seed is not None else (),
            )
        else:
            session_id = uuid.uuid4().hex
        agent = AgentSession(
            model or self._model,
            self._llm,
            self._advisor,
            store=self._store,
            session_id=session_id,
            include_samples=self._include_samples,
        )
        managed = ManagedSession(session_id, agent, target_bi=target_bi, owner=owner)
        # start BEFORE registering (F2): a failed LLM call must not leave a zombie
        # in the registry, and nobody can race a reply while grounding still runs —
        # the session id simply does not resolve yet
        with managed.lock:
            turn = agent.start(request, seed=seed)
        with self._registry_lock:
            self._evict_idle_locked()
            self._sessions[session_id] = managed
        return managed, turn

    def start_auto(
        self,
        spec: DashboardSpec,
        target_bi: TargetBI = TargetBI.SUPERSET,
        model: SemanticModel | None = None,
        owner: str | None = None,
    ) -> tuple[ManagedSession, AgentTurn]:
        """Register a session whose spec is built deterministically (auto-overview mode).

        Mirrors `start`, but adopts a pre-built spec straight into APPROVE instead of
        running the LLM (no GROUNDING/PROPOSE). The same approve/build/iterate path applies.
        """
        if self._store is not None:
            session_id = self._store.create_session(
                f"авто-обзор: {spec.title}", owner=owner, target_bi=target_bi.value
            )
        else:
            session_id = uuid.uuid4().hex
        agent = AgentSession(
            model or self._model,
            self._llm,
            self._advisor,
            store=self._store,
            session_id=session_id,
            include_samples=self._include_samples,
        )
        managed = ManagedSession(session_id, agent, target_bi=target_bi, owner=owner)
        with managed.lock:
            turn = agent.adopt_spec(spec)
        with self._registry_lock:
            self._evict_idle_locked()
            self._sessions[session_id] = managed
        return managed, turn

    def _evict_idle_locked(self) -> None:
        """Drop oldest idle sessions past MAX_SESSIONS (dict keeps insertion order).

        Building or locked sessions are never evicted; if everything is busy the
        registry temporarily grows over the cap rather than killing live work.
        The durable record stays in Store either way.
        """
        while len(self._sessions) >= MAX_SESSIONS:
            victim = next(
                (
                    sid
                    for sid, m in self._sessions.items()
                    if m.build_status != "building" and not m.lock.locked()
                ),
                None,
            )
            if victim is None:
                return
            del self._sessions[victim]

    def get(self, session_id: str) -> ManagedSession:
        with self._registry_lock:
            managed = self._sessions.get(session_id)
        if managed is not None:
            return managed
        # X-4: registry miss -> rehydrate from the durable record (server restart or
        # eviction past MAX_SESSIONS). Store reads run outside the registry lock; the
        # double-check below keeps the winner if two callers hydrated the same id
        # concurrently (the loser's copy is dropped before anyone could lock it).
        managed = self._hydrate(session_id)
        with self._registry_lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                return existing
            self._evict_idle_locked()
            self._sessions[session_id] = managed
        return managed

    def _hydrate(self, session_id: str) -> ManagedSession:
        """Rebuild a ManagedSession from Store rows (X-4); raises UnknownSession when
        there is nothing meaningful to resume.

        Phase mapping: the latest spec row decides — 'approved' -> APPROVED, any other
        status -> APPROVE (word edits keep appending 'proposed' rows, so the latest row
        IS the current spec). No spec rows -> the dialogue was still clarifying: resume
        as CLARIFY only if at least one clarify round actually reached the user
        (trace_events); a session that died before its first agent output never returned
        its id to the client, so it is not resumable — UnknownSession.

        Deliberately not restored (regenerated by the next turn): grounding report,
        advisor verdicts, seed layout-analysis. Clarification answers are restored only
        for CLARIFY sessions — in APPROVE/APPROVED every reply goes down the patch path,
        which never re-reads them.
        """
        if self._store is None:
            raise UnknownSession(session_id)
        row = self._store.session_row(session_id)
        if row is None or row["status"] == "deleted":
            raise UnknownSession(session_id)
        user_texts = [m["content"] for m in self._store.messages(session_id) if m["role"] == "user"]

        specs = self._store.specs(session_id)
        spec: DashboardSpec | None = None
        spec_row_id: int | None = None
        clarifications: list[str] = []
        clarify_rounds = 0
        if specs:
            spec = DashboardSpec.model_validate(specs[-1]["spec_json"])
            spec_row_id = specs[-1]["id"]
            phase = AgentPhase.APPROVED if specs[-1]["status"] == "approved" else AgentPhase.APPROVE
            # auto-overview sessions have no user message at all (adopt_spec records only
            # the agent's summary) — the session label stands in; _request is never
            # re-read on the patch path anyway
            request = user_texts[0] if user_texts else row["request"]
        else:
            if not user_texts:
                raise UnknownSession(session_id)
            clarify_rounds = sum(
                1 for e in self._store.trace_events(session_id) if e["kind"] == "clarify"
            )
            if clarify_rounds == 0:
                raise UnknownSession(session_id)
            phase = AgentPhase.CLARIFY
            request = user_texts[0]
            # no spec was ever proposed, so every user message after the first is a
            # clarification answer (word edits only exist once a spec does)
            clarifications = user_texts[1:]

        # RBAC: ground/patch on the owner's schema scope, exactly like the live session
        # did (app.py filtered the model at start). Owner gone from the users file ->
        # fall back to the full model; the approve-time forbidden_tables gate still holds.
        model = self._model
        owner = row.get("owner")
        if owner:
            user = self._store.get_user(owner)
            if user is not None:
                model = filter_model_by_schemas(self._model, user["allowed_schemas"])

        agent = AgentSession.restore(
            model,
            self._llm,
            self._advisor,
            store=self._store,
            session_id=session_id,
            include_samples=self._include_samples,
            phase=phase,
            request=request,
            clarifications=clarifications,
            clarify_rounds=clarify_rounds,
            pinned=set(row.get("pinned") or ()),
            spec=spec,
            spec_row_id=spec_row_id,
            dcr_logged={
                (r["table_name"], r["rule"])
                for r in self._store.dm_change_requests_for_session(session_id)
            },
        )
        try:
            target = TargetBI(row.get("target_bi") or "superset")
        except ValueError:  # a legacy/foreign value never blocks resume
            target = TargetBI.SUPERSET
        managed = ManagedSession(session_id, agent, target_bi=target, owner=owner)

        builds = self._store.builds(session_id)
        if builds:
            last = builds[-1]
            if last["status"] == "ok":
                managed.build_status = "built"
                url = last["url"] or ""
                base = self._bi_base_urls.get(target, "").rstrip("/")
                if base and url and not url.startswith(("http://", "https://")):
                    url = base + url  # pipeline stores the BI-relative url (F-1 convention)
                managed.dashboard_url = url
                # seed the SSE buffer with a synthetic terminal event: a late stream
                # reader must get closure, not heartbeats forever on an empty buffer
                managed.add_event(BuildEvent(kind="done", text=spec.title if spec else "", url=url))
            else:
                managed.build_status = "failed"
                managed.add_event(BuildEvent(kind="error", text=last["error"] or "build failed"))
        elif phase is AgentPhase.APPROVED:
            # approved but no build row: the process died in the approve->build window
            # (reap_stuck_builds covers the mid-build case). Mark failed so the approve
            # endpoint's retry path rebuilds the same approved spec instead of 409.
            managed.build_status = "failed"
            managed.add_event(
                BuildEvent(kind="error", text="build was interrupted by a server restart")
            )
        return managed

    def remove(self, session_id: str) -> bool:
        """Forget the session and tombstone its durable record (status='deleted').

        Without the tombstone, lazy hydration would resurrect a DELETEd session on the
        next GET, making delete a no-op. The rows themselves stay — delete makes the
        session unaddressable, not unrecorded.

        Known non-atomicity: a get() racing this remove can re-insert the session into
        the registry after the pop but before (or despite) the tombstone — the durable
        record is 'deleted' either way, so the resurrection lives only until the next
        restart/eviction. Accepted: it needs the same client to DELETE and address one
        session concurrently, and the tombstone is what actually owns the semantics.
        """
        with self._registry_lock:
            removed = self._sessions.pop(session_id, None) is not None
        if self._store is not None and self._store.session_status(session_id) is not None:
            self._store.set_session_status(session_id, "deleted")
            return True
        return removed
