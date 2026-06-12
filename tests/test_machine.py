"""Agent state machine (1.4) + advisor narration (1.7) + reasoning policy (1.12)
on a scripted LLM: phase transitions, clarify policy, word edits, store linkage."""

import pytest

from auto_bi.advisor.core import Advisor
from auto_bi.advisor.findings import Finding, Severity, VerdictClass
from auto_bi.advisor.narrate import narrate_findings, worst_verdicts
from auto_bi.agent.grounding import GroundingReport, clarify_questions, ground
from auto_bi.agent.machine import MAX_CLARIFY_ROUNDS, AgentPhase, AgentSession
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

    def complete(self, prompt, schema, *, reasoning=False, session_id=None):
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
