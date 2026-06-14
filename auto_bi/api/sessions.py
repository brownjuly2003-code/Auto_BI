"""In-memory session registry for the HTTP API (task 2.1).

One ManagedSession per dialogue: the AgentSession itself, a per-session lock that
serializes turns (LLM calls are long; two concurrent replies on one session would
corrupt the machine), and the build event buffer. Events are buffered, not only
streamed: an SSE client that connects after the build started replays the history
and then follows live. Process-local by design — the durable record lives in Store.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator

from auto_bi.advisor.core import Advisor
from auto_bi.agent.machine import AgentSession, AgentTurn
from auto_bi.agent.seed import FieldsSeed
from auto_bi.api.schemas import BuildEvent
from auto_bi.ir.spec import TargetBI
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
    ) -> None:
        self.session_id = session_id
        self.agent = agent
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
    ) -> None:
        self._model = model
        self._llm = llm
        self._advisor = advisor
        self._store = store
        self._include_samples = include_samples
        self._sessions: dict[str, ManagedSession] = {}
        self._registry_lock = threading.Lock()

    def start(
        self,
        request: str,
        seed: FieldsSeed | None = None,
        target_bi: TargetBI = TargetBI.SUPERSET,
        model: SemanticModel | None = None,
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
            session_id = self._store.create_session(label)
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
        managed = ManagedSession(session_id, agent, target_bi=target_bi)
        # start BEFORE registering (F2): a failed LLM call must not leave a zombie
        # in the registry, and nobody can race a reply while grounding still runs —
        # the session id simply does not resolve yet
        with managed.lock:
            turn = agent.start(request, seed=seed)
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
            try:
                return self._sessions[session_id]
            except KeyError:
                raise UnknownSession(session_id) from None

    def remove(self, session_id: str) -> bool:
        with self._registry_lock:
            return self._sessions.pop(session_id, None) is not None
