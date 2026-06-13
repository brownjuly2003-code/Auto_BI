"""Agent state machine (task 1.4): INTAKE -> GROUNDING -> CLARIFY* -> PROPOSE_SPEC ->
APPROVE (правки словами) -> APPROVED.

Plain class, no frameworks (D6). The session is UI-agnostic: every step returns an
AgentTurn the caller renders; BUILD stays with the caller (pipeline/adapter) so the
machine never touches the BI. Clarify policy is mechanical: questions exist iff the
grounding report has ambiguous/unmatched entries (invariant 4), rounds are capped —
after MAX_CLARIFY_ROUNDS the agent proposes with what it has instead of interrogating.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from auto_bi.advisor.core import Advisor
from auto_bi.advisor.narrate import ChartVerdict, narrate_findings
from auto_bi.agent.grounding import GroundingReport, clarify_questions, ground
from auto_bi.agent.propose import patch_spec, propose_spec
from auto_bi.agent.seed import FieldsSeed, render_seed_request, seed_analysis, seed_tables
from auto_bi.ir.spec import DashboardSpec
from auto_bi.llm.base import LLMClient
from auto_bi.semantic.model import SemanticModel
from auto_bi.store import Store

MAX_CLARIFY_ROUNDS = 2


class AgentPhase(StrEnum):
    INTAKE = "intake"
    CLARIFY = "clarify"  # questions asked, waiting for answers
    APPROVE = "approve"  # spec proposed, waiting for "да" or word edits
    APPROVED = "approved"  # user confirmed; the caller builds
    FAILED = "failed"


class AgentTurn(BaseModel):
    phase: AgentPhase
    message: str = ""
    questions: list[str] = Field(default_factory=list)
    spec: DashboardSpec | None = None
    verdicts: list[ChartVerdict] = Field(default_factory=list)
    # fields-first: детерминированный анализ раскладки (seed vs spec)
    notes: list[str] = Field(default_factory=list)
    # word edit came back as the same spec — surfaced instead of pretending it applied
    noop: bool = False


def spec_summary(spec: DashboardSpec) -> str:
    lines = [f"«{spec.title}» — {len(spec.charts)} чартов:"]
    for chart in spec.charts:
        q = chart.query
        dims = ", ".join(q.group_columns()) or "—"
        measures = ", ".join(m.label or m.column for m in q.measures)
        lines.append(f"  • [{chart.viz.value}] {chart.title} ({q.table}: {dims} × {measures})")
    if spec.filters:
        # the Superset adapter does not compile dashboard filters yet: say so HERE,
        # at approval time — the built dashboard must not silently differ from the preview
        described = ", ".join(
            f.column + (f" = {f.default}" if f.default else "") for f in spec.filters
        )
        lines.append(
            f"  ⚠ фильтры дашборда ({described}) пока не переносятся в Superset — "
            "задайте период фильтром чарта или примите дашборд без них"
        )
    return "\n".join(lines)


class AgentSession:
    def __init__(
        self,
        model: SemanticModel,
        llm: LLMClient,
        advisor: Advisor | None = None,
        *,
        store: Store | None = None,
        session_id: str | None = None,
        include_samples: bool = True,
    ) -> None:
        self._model = model
        self._llm = llm
        self._advisor = advisor
        self._store = store
        self._session_id = session_id
        self._include_samples = include_samples

        self.phase = AgentPhase.INTAKE
        self.report: GroundingReport | None = None
        self.spec: DashboardSpec | None = None
        self.verdicts: list[ChartVerdict] = []
        self._request = ""
        self._seed: FieldsSeed | None = None
        self._pinned: set[str] = set()
        self._clarifications: list[str] = []
        self._clarify_rounds = 0
        self._spec_row_id: int | None = None
        self._dcr_logged: set[tuple[str, str]] = set()  # (table, rules) already stored

    # --- steps -----------------------------------------------------------------

    def start(self, request: str = "", *, seed: FieldsSeed | None = None) -> AgentTurn:
        """Text-first (request) or fields-first (seed) entry — same pipeline (invariant 6).

        The seed renders into a textual request GROUNDING consumes unchanged; its
        tables are pinned in context selection (the layout IS the request, dropping
        its tables would invalidate it). Seed validation against the model is the
        caller's job (API/UI built the panel from the model) — by this point an
        unknown field is a bug, not a clarification.
        """
        if self.phase != AgentPhase.INTAKE:
            raise RuntimeError(f"session already started (phase={self.phase})")
        if seed is None and not request.strip():
            raise ValueError("either request text or a fields seed is required")
        if seed is not None:
            self._seed = seed
            self._pinned = seed_tables(seed)
            rendered = render_seed_request(seed, self._model)
            self._request = f"{rendered}\n\n{request}" if request.strip() else rendered
        else:
            self._request = request
        self._record("user", self._request)
        return self._ground_then_propose()

    def reply(self, text: str) -> AgentTurn:
        """User's free-text turn: clarify answers in CLARIFY, word edits in APPROVE.

        APPROVED also accepts edits (task 2.4, iterations): «добавь фильтр» after a
        build patches the approved spec and returns the session to APPROVE — the
        caller rebuilds on the next approve. Spec history stays in the store (a new
        `proposed` row per edit), so iterating never rewrites what was built.
        """
        self._record("user", text)
        if self.phase == AgentPhase.CLARIFY:
            self._clarifications.append(text)
            return self._ground_then_propose()
        if self.phase in (AgentPhase.APPROVE, AgentPhase.APPROVED):
            assert self.spec is not None
            patched = patch_spec(
                self._llm,
                self._model,
                self.spec,
                text,
                session_id=self._session_id,
                include_samples=self._include_samples,
            )
            if patched.model_dump(mode="json") == self.spec.model_dump(mode="json"):
                # the patch contract forces a full spec back, so an edit the model cannot
                # express returns unchanged — say so instead of announcing a "new"
                # proposal; phase/spec/history keep as is
                message = (
                    "Правка не изменила спецификацию: похоже, её нельзя выразить "
                    "текущей моделью данных (нет нужного поля или связи между "
                    "таблицами). Сформулируйте иначе или соберите как есть."
                )
                self._record("agent", message)
                return AgentTurn(
                    phase=self.phase,
                    message=message,
                    spec=self.spec,
                    verdicts=self.verdicts,
                    noop=True,
                )
            self.spec = patched
            return self._propose_turn()
        raise RuntimeError(f"no user turn expected in phase {self.phase}")

    def approve(self) -> DashboardSpec:
        if self.phase != AgentPhase.APPROVE or self.spec is None:
            raise RuntimeError(f"nothing to approve in phase {self.phase}")
        self.phase = AgentPhase.APPROVED
        if self._store is not None and self._spec_row_id is not None:
            self._store.set_spec_status(self._spec_row_id, "approved")
        return self.spec

    # --- internals -------------------------------------------------------------

    def _full_request(self) -> str:
        if not self._clarifications:
            return self._request
        answers = "\n".join(f"- {a}" for a in self._clarifications)
        return f"{self._request}\n\nУточнения пользователя:\n{answers}"

    def _ground_then_propose(self) -> AgentTurn:
        self.report = ground(
            self._llm,
            self._model,
            self._full_request(),
            session_id=self._session_id,
            include_samples=self._include_samples,
            pinned=self._pinned,
        )
        questions = clarify_questions(self.report)
        if questions and self._clarify_rounds < MAX_CLARIFY_ROUNDS:
            self._clarify_rounds += 1
            self.phase = AgentPhase.CLARIFY
            message = "Нужны уточнения:"
            self._record("agent", message + " " + " | ".join(questions))
            return AgentTurn(phase=self.phase, message=message, questions=questions)
        return self._propose()

    def _propose(self) -> AgentTurn:
        self.spec = propose_spec(
            self._llm,
            self._model,
            self._full_request(),
            session_id=self._session_id,
            include_samples=self._include_samples,
            pinned=self._pinned or None,
        )
        # layout analysis only on the initial proposal: word edits deliberately
        # diverge from the seed, re-reporting the diff would flag the user's own edits
        notes = seed_analysis(self._seed, self.spec) if self._seed is not None else []
        return self._propose_turn(notes=notes)

    def _propose_turn(self, notes: list[str] | None = None) -> AgentTurn:
        assert self.spec is not None
        self.verdicts = []
        if self._advisor is not None:
            findings = self._advisor.review(self.spec)
            self.verdicts = narrate_findings(
                self._llm, self.spec, findings, session_id=self._session_id
            )
        self.phase = AgentPhase.APPROVE
        message = spec_summary(self.spec)
        if notes:
            message += "\n" + "\n".join(f"  ◦ анализ раскладки: {n}" for n in notes)
        self._record("agent", message)
        if self._store is not None and self._session_id is not None:
            self._spec_row_id = self._store.save_spec(
                self._session_id, self.spec.model_dump(mode="json")
            )
            for v in self.verdicts:
                if v.verdict_class.value == "dm_change_request":
                    chart = next((c for c in self.spec.charts if c.id == v.chart_id), None)
                    key = (chart.query.table if chart else "", ", ".join(v.rules))
                    if key in self._dcr_logged:
                        continue  # word edits re-run the advisor: one request per finding
                    self._dcr_logged.add(key)
                    self._store.add_dm_change_request(
                        self._session_id,
                        table_name=key[0],
                        rule=key[1],
                        severity=v.severity.value,
                        narrative=v.text,
                    )
        return AgentTurn(
            phase=self.phase,
            message=message,
            spec=self.spec,
            verdicts=self.verdicts,
            notes=notes or [],
        )

    def _record(self, role: str, content: str) -> None:
        if self._store is not None and self._session_id is not None:
            self._store.add_message(self._session_id, role, content)
