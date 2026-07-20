"""Agent state machine (1.4) + advisor narration (1.7) + reasoning policy (1.12)
on a scripted LLM: phase transitions, clarify policy, word edits, store linkage."""

import pytest

from auto_bi.advisor.core import Advisor
from auto_bi.advisor.findings import Finding, Severity, VerdictClass
from auto_bi.advisor.narrate import narrate_findings, worst_verdicts
from auto_bi.agent.grounding import GroundingReport, clarify_questions, ground
from auto_bi.agent.machine import MAX_CLARIFY_ROUNDS, AgentPhase, AgentSession, spec_summary
from auto_bi.ir.spec import DashboardSpec
from auto_bi.llm.base import LLMError
from auto_bi.store import Store
from tests.test_propose import GOOD_SPEC

CLEAR_REPORT = {
    "tables": ["dm.sales_daily"],
    "matched": [{"phrase": "выручка", "candidates": ["dm.sales_daily.revenue"]}],
    "ambiguous": [],
    "unmatched": [],
}

AMBIGUOUS_REPORT = {
    "tables": ["dm.sales_daily"],
    "matched": [],
    "ambiguous": [
        {
            "phrase": "продажи",
            "candidates": ["dm.sales_daily.revenue", "dm.sales_daily.orders"],
        }
    ],
    "unmatched": ["маржа"],
}

PATCHED_SPEC = {
    **GOOD_SPEC,
    "title": "Продажи (обновлено)",
}


class ScriptedLLM:
    """Returns queued payloads in order; records (schema, reasoning) per call."""

    def __init__(self, responses: list[dict]) -> None:
        self._queue = list(responses)
        self.calls: list[tuple[str, str, bool]] = []  # (schema name, prompt, reasoning)

    def complete(self, prompt, schema, *, reasoning=False, session_id=None, step=""):
        self.calls.append((schema.__name__, prompt, reasoning))
        return schema.model_validate(self._queue.pop(0))


# --- grounding / clarify policy ------------------------------------------------


def test_clear_request_goes_straight_to_approve(demo_model) -> None:
    llm = ScriptedLLM([CLEAR_REPORT, GOOD_SPEC])
    agent = AgentSession(demo_model, llm)
    turn = agent.start("выручка по дням")
    assert turn.phase == AgentPhase.APPROVE
    assert turn.questions == []
    assert turn.spec is not None
    assert "Выручка по дням" in turn.message


def test_ambiguous_request_asks_then_proposes(demo_model) -> None:
    llm = ScriptedLLM([AMBIGUOUS_REPORT, CLEAR_REPORT, GOOD_SPEC])
    agent = AgentSession(demo_model, llm)
    turn = agent.start("продажи и маржа")
    assert turn.phase == AgentPhase.CLARIFY
    assert len(turn.questions) == 2
    assert "продажи" in turn.questions[0]
    turn = agent.reply("revenue; маржу убери")
    assert turn.phase == AgentPhase.APPROVE
    # the clarification answer is folded into the re-grounding prompt
    assert "маржу убери" in llm.calls[1][1]


def test_clarify_rounds_capped(demo_model) -> None:
    llm = ScriptedLLM([AMBIGUOUS_REPORT] * (MAX_CLARIFY_ROUNDS + 1) + [GOOD_SPEC])
    agent = AgentSession(demo_model, llm)
    turn = agent.start("продажи")
    rounds = 0
    while turn.phase == AgentPhase.CLARIFY:
        rounds += 1
        turn = agent.reply("не знаю")
    assert rounds == MAX_CLARIFY_ROUNDS
    assert turn.phase == AgentPhase.APPROVE  # proposes with what it has, not interrogates


def test_word_edit_patches_spec(demo_model) -> None:
    llm = ScriptedLLM([CLEAR_REPORT, GOOD_SPEC, PATCHED_SPEC])
    agent = AgentSession(demo_model, llm)
    agent.start("выручка по дням")
    turn = agent.reply("переименуй дашборд")
    assert turn.phase == AgentPhase.APPROVE
    assert turn.spec.title == "Продажи (обновлено)"
    # the patch prompt carries the current spec and the edit
    _schema_name, prompt, reasoning = llm.calls[-1]
    assert "переименуй дашборд" in prompt
    assert "Продажи" in prompt
    assert reasoning is True  # patch_spec designs -> thinking on


def test_word_edit_returning_same_spec_is_reported_as_noop(demo_model) -> None:
    # the patch contract forces a full spec back: an edit the IR cannot express
    # (e.g. a joined-table field) comes back unchanged and must not be announced
    # as a new proposal
    llm = ScriptedLLM([CLEAR_REPORT, GOOD_SPEC, GOOD_SPEC])
    agent = AgentSession(demo_model, llm)
    agent.start("выручка по дням")
    turn = agent.reply("замени магазины на города из смежной таблицы")
    assert turn.noop is True
    assert turn.phase == AgentPhase.APPROVE
    assert "не изменила" in turn.message
    assert turn.spec is not None  # current spec stays addressable for approve


def test_approve_returns_spec_and_finishes(demo_model) -> None:
    llm = ScriptedLLM([CLEAR_REPORT, GOOD_SPEC])
    agent = AgentSession(demo_model, llm)
    agent.start("выручка по дням")
    spec = agent.approve()
    assert isinstance(spec, DashboardSpec)
    assert agent.phase == AgentPhase.APPROVED
    with pytest.raises(RuntimeError):
        agent.approve()


def test_reasoning_policy_flags(demo_model) -> None:
    llm = ScriptedLLM([CLEAR_REPORT, GOOD_SPEC])
    agent = AgentSession(demo_model, llm)
    agent.start("выручка по дням")
    flags = {(name, reasoning) for name, _, reasoning in llm.calls}
    assert ("GroundingReport", True) in flags  # grounding thinks
    assert ("DashboardSpec", True) in flags  # propose thinks


def test_ground_drops_hallucinated_candidates(demo_model) -> None:
    fake = {
        "tables": ["dm.sales_daily"],
        "matched": [],
        "ambiguous": [
            {
                "phrase": "выручка",
                "candidates": ["dm.sales_daily.revenue", "dm.sales_daily.fake_column"],
            }
        ],
        "unmatched": [],
    }
    llm = ScriptedLLM([fake])
    report = ground(llm, demo_model, "выручка")
    # one candidate was hallucinated -> not ambiguous anymore, no question generated
    assert report.ambiguous == []
    assert clarify_questions(report) == []
    assert report.matched[0].candidates == ["dm.sales_daily.revenue"]


def test_clarify_questions_capped_at_three() -> None:
    report = GroundingReport(unmatched=["a", "b", "c", "d", "e"])
    assert len(clarify_questions(report)) == 3


def test_grounding_prompt_teaches_ratio_metrics() -> None:
    # drift guard (S01): the IR expresses ratio measures since cont.9 — grounding must
    # match a derived metric whose parts exist («средний чек» = revenue/orders), not
    # flag it unmatched with the stale «дашборд не умеет» claim
    from auto_bi.agent.grounding import GROUNDING_PROMPT

    assert "средний чек" in GROUNDING_PROMPT
    assert "делить одну меру на другую" in GROUNDING_PROMPT
    assert "не умеет" not in GROUNDING_PROMPT
    # analytics phrasing is presentation, not a data entity — no stray unmatched
    for phrase in ("год к году", "Парето", "распределение", "нарастающим итогом"):
        assert phrase in GROUNDING_PROMPT


# --- advisor narration (1.7) ------------------------------------------------------


def _finding(chart_id="c1", severity=Severity.WARN, vc=VerdictClass.SPEC_ADJUSTMENT, **kw):
    return Finding(
        rule=kw.get("rule", "filter_not_in_sorting_key_prefix"),
        severity=severity,
        verdict_class=vc,
        chart_id=chart_id,
        title=kw.get("title", "фильтр мимо префикса ключа сортировки"),
        evidence=kw.get("evidence", {"scan_fraction": 0.96}),
        suggestions=kw.get("suggestions", ["добавить фильтр по date"]),
        remediation=kw.get("remediation"),
    )


def test_worst_verdicts_takes_worst_class_and_severity() -> None:
    findings = [
        _finding(severity=Severity.WARN, vc=VerdictClass.SPEC_ADJUSTMENT),
        _finding(
            severity=Severity.CRITICAL,
            vc=VerdictClass.DM_CHANGE_REQUEST,
            rule="partition_misaligned_filter",
            title="фильтр мимо партиций",
            suggestions=["новая витрина"],
        ),
    ]
    (verdict,) = worst_verdicts(findings).values()
    assert verdict.severity == Severity.CRITICAL
    assert verdict.verdict_class == VerdictClass.DM_CHANGE_REQUEST
    assert set(verdict.rules) == {"filter_not_in_sorting_key_prefix", "partition_misaligned_filter"}
    assert verdict.suggestions == ["добавить фильтр по date", "новая витрина"]


def test_worst_verdicts_collects_remediations() -> None:
    from auto_bi.advisor.findings import Remediation

    rem = Remediation(kind="ch_projection", summary="проекция", ddl="ALTER TABLE ...")
    findings = [
        _finding(severity=Severity.WARN),  # no remediation
        _finding(vc=VerdictClass.DM_CHANGE_REQUEST, rule="r2", remediation=rem),
    ]
    (verdict,) = worst_verdicts(findings).values()
    assert verdict.remediations == [rem]  # carried through verbatim, only the ones present


def test_narrate_uses_llm_text_and_keeps_code_verdict(demo_model) -> None:
    spec = DashboardSpec.model_validate(GOOD_SPEC)
    llm = ScriptedLLM(
        [{"verdicts": [{"chart_id": "c1", "text": "Фильтр идёт мимо ключа — скан 96%."}]}]
    )
    (verdict,) = narrate_findings(llm, spec, [_finding()])
    assert verdict.text == "Фильтр идёт мимо ключа — скан 96%."
    assert verdict.verdict_class == VerdictClass.SPEC_ADJUSTMENT  # decided by code, not LLM
    _schema_name, prompt, reasoning = llm.calls[0]
    assert reasoning is False  # narration is mechanical (1.12)
    assert "scan_fraction" in prompt  # measured evidence reaches the prompt


def test_narrate_falls_back_to_titles_on_llm_error(demo_model) -> None:
    class BrokenLLM:
        def complete(self, *a, **kw):
            raise LLMError("down")

    spec = DashboardSpec.model_validate(GOOD_SPEC)
    (verdict,) = narrate_findings(BrokenLLM(), spec, [_finding()])
    assert "ключа сортировки" in verdict.text  # mechanical title survives


def test_narrate_silent_on_clean_spec(demo_model) -> None:
    spec = DashboardSpec.model_validate(GOOD_SPEC)

    class NoCallLLM:
        def complete(self, *a, **kw):
            raise AssertionError("LLM must not be called without findings")

    assert narrate_findings(NoCallLLM(), spec, []) == []


def test_spec_summary_names_charts_a_filter_applies_to() -> None:
    # the date column IS in the line chart's grain -> the native filter reaches it;
    # the preview must name the chart so the built dashboard matches the preview
    spec = DashboardSpec.model_validate(
        {
            **GOOD_SPEC,
            "filters": [
                {"column": "dm.sales_daily.date", "type": "time_range", "default": "last 90 days"}
            ],
        }
    )
    summary = spec_summary(spec)
    assert "dm.sales_daily.date" in summary
    assert "применяется к" in summary
    assert "Выручка по дням" in summary
    assert "не применим" not in summary


def test_spec_summary_warns_when_filter_reaches_no_chart() -> None:
    # control on a mart no chart reads -> cannot be wired; preview must say so
    spec = DashboardSpec.model_validate(
        {**GOOD_SPEC, "filters": [{"column": "dm.other_fact.region_id", "type": "value"}]}
    )
    summary = spec_summary(spec)
    assert "не применим ни к одному чарту" in summary
    assert "⚠" in summary


def test_spec_summary_source_chart_receives_mart_filter() -> None:
    # D-1: a plain line is SOURCE — store_id on the same mart reaches it even without
    # store_id in the grain (shared semantic-grain dataset carries all columns)
    spec = DashboardSpec.model_validate(
        {**GOOD_SPEC, "filters": [{"column": "dm.sales_daily.store_id", "type": "value"}]}
    )
    summary = spec_summary(spec)
    assert "применяется к" in summary
    assert "Выручка по дням" in summary
    assert "не применим" not in summary


def test_spec_summary_silent_without_filters() -> None:
    assert "фильтры" not in spec_summary(DashboardSpec.model_validate(GOOD_SPEC))


# --- store linkage -------------------------------------------------------------


def test_session_records_messages_specs_and_dm_change_requests(demo_model, tmp_path) -> None:
    store = Store(tmp_path / "s.sqlite")
    sid = store.create_session("выручка")

    critical = _finding(
        severity=Severity.CRITICAL,
        vc=VerdictClass.DM_CHANGE_REQUEST,
        rule="no_filter_on_large_fact",
        title="запрос не предусмотрен витриной",
    )

    class OneFindingAdvisor(Advisor):
        def __init__(self) -> None:  # bypass model/run_query wiring
            pass

        def review(self, spec):
            return [critical]

    llm = ScriptedLLM(
        [
            CLEAR_REPORT,
            GOOD_SPEC,
            {"verdicts": [{"chart_id": "c1", "text": "Нужна другая витрина."}]},
        ]
    )
    agent = AgentSession(demo_model, llm, OneFindingAdvisor(), store=store, session_id=sid)
    turn = agent.start("выручка по дням")
    assert turn.verdicts[0].text == "Нужна другая витрина."

    roles = [m["role"] for m in store.messages(sid)]
    assert roles == ["user", "agent"]
    (req,) = store.dm_change_requests("open")
    assert req["table_name"] == "dm.sales_daily"
    assert req["rule"] == "no_filter_on_large_fact"

    agent.approve()
    (spec_row,) = store.specs(sid)
    assert spec_row["status"] == "approved"
    store.close()


def test_dm_change_request_not_duplicated_by_word_edits(demo_model, tmp_path) -> None:
    store = Store(tmp_path / "s.sqlite")
    sid = store.create_session("выручка")

    critical = _finding(
        severity=Severity.CRITICAL,
        vc=VerdictClass.DM_CHANGE_REQUEST,
        rule="no_filter_on_large_fact",
        title="запрос не предусмотрен витриной",
    )

    class OneFindingAdvisor(Advisor):
        def __init__(self) -> None:
            pass

        def review(self, spec):
            return [critical]

    narration = {"verdicts": [{"chart_id": "c1", "text": "Нужна другая витрина."}]}
    llm = ScriptedLLM([CLEAR_REPORT, GOOD_SPEC, narration, PATCHED_SPEC, narration])
    agent = AgentSession(demo_model, llm, OneFindingAdvisor(), store=store, session_id=sid)
    agent.start("выручка по дням")
    agent.reply("переименуй дашборд")  # finding is still alive after the edit
    # the word edit re-ran the advisor, but the same (table, rule) is stored once
    assert len(store.dm_change_requests("open")) == 1
    store.close()


def test_iteration_edit_after_build_reenters_approve(demo_model, tmp_path) -> None:
    # task 2.4: APPROVED is not terminal — a word edit patches the built spec,
    # the session returns to APPROVE and can be approved (rebuilt) again
    store = Store(tmp_path / "s.sqlite")
    sid = store.create_session("выручка")
    llm = ScriptedLLM([CLEAR_REPORT, GOOD_SPEC, PATCHED_SPEC])
    agent = AgentSession(demo_model, llm, store=store, session_id=sid)
    agent.start("выручка по дням")
    agent.approve()
    assert agent.phase == AgentPhase.APPROVED

    turn = agent.reply("переименуй дашборд")
    assert turn.phase == AgentPhase.APPROVE
    assert turn.spec.title == "Продажи (обновлено)"

    spec = agent.approve()
    assert spec.title == "Продажи (обновлено)"
    # spec history is append-only: v1 approved, v2 proposed -> approved
    statuses = [s["status"] for s in store.specs(sid)]
    assert statuses == ["approved", "approved"]
    store.close()


def test_trace_records_agent_steps(demo_model, tmp_path) -> None:
    # observability (Phase 4): each agent step lands one ordered trace event
    class OneFindingAdvisor(Advisor):
        def __init__(self) -> None:
            pass

        def review(self, spec):
            return []

    store = Store(tmp_path / "s.sqlite")
    sid = store.create_session("выручка")
    # advisor returns no findings -> narrate short-circuits without an LLM call, so the
    # queue only feeds grounding/propose/patch; the advisor STEP still traces (review ran)
    llm = ScriptedLLM([CLEAR_REPORT, GOOD_SPEC, PATCHED_SPEC])
    agent = AgentSession(demo_model, llm, OneFindingAdvisor(), store=store, session_id=sid)
    agent.start("выручка по дням")
    agent.approve()
    agent.reply("переименуй дашборд")

    events = store.trace_events(sid)
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))  # contiguous
    assert [e["kind"] for e in events] == [
        "grounding",
        "propose",
        "advisor",
        "approve",
        "patch",
        "advisor",
    ]
    assert all(e["status"] == "ok" for e in events)
    grounding = events[0]
    assert "совпадений" in grounding["detail"]
    store.close()


def _one_finding_advisor(finding):
    class OneFindingAdvisor(Advisor):
        def __init__(self) -> None:  # bypass model/run_query wiring
            pass

        def review(self, spec):
            return [finding]

    return OneFindingAdvisor()


def test_auto_path_advises_without_the_llm(demo_model, tmp_path) -> None:
    # P1-2: the auto path used to adopt a spec with verdicts=[] — the advisor never ran, so a
    # real finding was silently dropped. Only the wording needs the LLM; the verdict is code's.
    store = Store(tmp_path / "s.sqlite")
    sid = store.create_session("авто-обзор: dm.sales_daily")
    spec = DashboardSpec.model_validate(GOOD_SPEC)

    llm = ScriptedLLM([])  # any LLM call here would raise -> narration must not be attempted
    agent = AgentSession(
        demo_model, llm, _one_finding_advisor(_finding()), store=store, session_id=sid
    )
    turn = agent.adopt_spec(spec)

    assert turn.phase == AgentPhase.APPROVE
    (verdict,) = turn.verdicts
    assert verdict.text == "фильтр мимо префикса ключа сортировки"  # the rule's own text
    assert verdict.suggestions == ["добавить фильтр по date"]
    store.close()


def test_auto_path_logs_dm_change_requests(demo_model, tmp_path) -> None:
    # a change request is decided by the rules, so the auto path must record it too
    store = Store(tmp_path / "s.sqlite")
    sid = store.create_session("авто-обзор: dm.sales_daily")
    critical = _finding(
        severity=Severity.CRITICAL,
        vc=VerdictClass.DM_CHANGE_REQUEST,
        rule="no_filter_on_large_fact",
        title="запрос не предусмотрен витриной",
    )
    agent = AgentSession(
        demo_model, ScriptedLLM([]), _one_finding_advisor(critical), store=store, session_id=sid
    )
    agent.adopt_spec(DashboardSpec.model_validate(GOOD_SPEC))

    (req,) = store.dm_change_requests("open")
    assert req["table_name"] == "dm.sales_daily"
    assert req["rule"] == "no_filter_on_large_fact"
    store.close()


def test_auto_path_stays_silent_on_a_clean_spec(demo_model, tmp_path) -> None:
    store = Store(tmp_path / "s.sqlite")
    sid = store.create_session("авто-обзор: dm.sales_daily")
    agent = AgentSession(demo_model, ScriptedLLM([]), advisor=None, store=store, session_id=sid)
    turn = agent.adopt_spec(DashboardSpec.model_validate(GOOD_SPEC))
    assert turn.verdicts == []  # no advisor wired -> unchanged behaviour
    store.close()


def test_trace_records_clarify_and_grounding_error(demo_model, tmp_path) -> None:
    store = Store(tmp_path / "s.sqlite")
    sid = store.create_session("неоднозначно")
    # ambiguous grounding -> a clarify event before any propose
    llm = ScriptedLLM([AMBIGUOUS_REPORT])
    agent = AgentSession(demo_model, llm, store=store, session_id=sid)
    agent.start("выручка")
    kinds = [e["kind"] for e in store.trace_events(sid)]
    assert kinds == ["grounding", "clarify"]

    # a failed LLM call on grounding records an error event and propagates
    class BoomLLM:
        def complete(self, *a, **kw):
            raise RuntimeError("llm down")

    sid2 = store.create_session("err")
    agent2 = AgentSession(demo_model, BoomLLM(), store=store, session_id=sid2)
    with pytest.raises(RuntimeError):
        agent2.start("выручка")
    (event,) = store.trace_events(sid2)
    assert event["kind"] == "grounding" and event["status"] == "error"
    assert "llm down" in event["detail"]
    store.close()
