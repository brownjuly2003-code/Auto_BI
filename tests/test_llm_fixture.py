"""Record/replay for golden-eval LLM calls (S11 — golden-eval in CI, T-2)."""

from __future__ import annotations

import json

import pytest

from auto_bi.agent.grounding import GroundingReport
from auto_bi.ir.spec import DashboardSpec
from auto_bi.llm.fixture import FixtureLLMClient, FixtureMissingError, RecordingLLMClient
from tests.test_machine import CLEAR_REPORT, GOOD_SPEC, ScriptedLLM


def _write_fixture(tmp_path, case_id: str, calls: list[dict]) -> None:
    (tmp_path / f"{case_id}.json").write_text(
        json.dumps({"case_id": case_id, "calls": calls}), encoding="utf-8"
    )


def _grounding_call(response: dict) -> dict:
    return {"step": "grounding", "schema": "GroundingReport", "response": response}


def _propose_call(response: dict) -> dict:
    return {"step": "propose_spec", "schema": "DashboardSpec", "response": response}


# --- FixtureLLMClient (replay) -----------------------------------------------------


def test_replay_returns_recorded_calls_in_order(tmp_path) -> None:
    _write_fixture(tmp_path, "g1", [_grounding_call(CLEAR_REPORT), _propose_call(GOOD_SPEC)])
    llm = FixtureLLMClient(tmp_path)
    llm.begin_case("g1")

    report = llm.complete("prompt", GroundingReport, step="grounding")
    assert isinstance(report, GroundingReport)
    assert report.tables == CLEAR_REPORT["tables"]

    spec = llm.complete("prompt", DashboardSpec, step="propose_spec")
    assert isinstance(spec, DashboardSpec)
    llm.end_case()  # no-op, must not raise


def test_replay_missing_fixture_file_raises_on_first_call(tmp_path) -> None:
    llm = FixtureLLMClient(tmp_path)
    llm.begin_case("never_recorded")
    with pytest.raises(FixtureMissingError, match="never_recorded"):
        llm.complete("prompt", GroundingReport, step="grounding")


def test_replay_extra_call_beyond_recorded_raises(tmp_path) -> None:
    _write_fixture(tmp_path, "g1", [_grounding_call(CLEAR_REPORT)])
    llm = FixtureLLMClient(tmp_path)
    llm.begin_case("g1")
    llm.complete("prompt", GroundingReport, step="grounding")
    with pytest.raises(FixtureMissingError, match="no recorded call"):
        llm.complete("prompt", DashboardSpec, step="propose_spec")


def test_replay_step_mismatch_raises_instead_of_silently_reusing(tmp_path) -> None:
    # fixture recorded a grounding call; the agent instead asks for propose_spec first
    _write_fixture(tmp_path, "g1", [_grounding_call(CLEAR_REPORT)])
    llm = FixtureLLMClient(tmp_path)
    llm.begin_case("g1")
    with pytest.raises(FixtureMissingError, match="call sequence changed"):
        llm.complete("prompt", DashboardSpec, step="propose_spec")


def test_replay_before_begin_case_raises(tmp_path) -> None:
    llm = FixtureLLMClient(tmp_path)
    with pytest.raises(FixtureMissingError, match="begin_case"):
        llm.complete("prompt", GroundingReport, step="grounding")


def test_replay_switches_cases_independently(tmp_path) -> None:
    _write_fixture(tmp_path, "g1", [_grounding_call(CLEAR_REPORT)])
    _write_fixture(tmp_path, "g2", [_grounding_call(CLEAR_REPORT), _propose_call(GOOD_SPEC)])
    llm = FixtureLLMClient(tmp_path)

    llm.begin_case("g1")
    llm.complete("prompt", GroundingReport, step="grounding")

    llm.begin_case("g2")  # position resets even though g1 only consumed 1/1 calls
    llm.complete("prompt", GroundingReport, step="grounding")
    llm.complete("prompt", DashboardSpec, step="propose_spec")


# --- RecordingLLMClient -------------------------------------------------------------


def test_recording_passes_through_and_writes_fixture_on_end_case(tmp_path) -> None:
    inner = ScriptedLLM([CLEAR_REPORT, GOOD_SPEC])
    llm = RecordingLLMClient(inner, tmp_path)

    llm.begin_case("g1")
    report = llm.complete("prompt-1", GroundingReport, step="grounding")
    spec = llm.complete("prompt-2", DashboardSpec, step="propose_spec")
    llm.end_case()

    assert isinstance(report, GroundingReport)  # pass-through still validates
    assert isinstance(spec, DashboardSpec)
    assert inner.calls == [
        ("GroundingReport", "prompt-1", False),
        ("DashboardSpec", "prompt-2", False),
    ]

    written = json.loads((tmp_path / "g1.json").read_text(encoding="utf-8"))
    assert written["case_id"] == "g1"
    assert [c["step"] for c in written["calls"]] == ["grounding", "propose_spec"]
    assert written["calls"][0]["schema"] == "GroundingReport"
    assert written["calls"][0]["response"]["tables"] == CLEAR_REPORT["tables"]


def test_recording_then_replay_round_trips(tmp_path) -> None:
    inner = ScriptedLLM([CLEAR_REPORT, GOOD_SPEC])
    recorder = RecordingLLMClient(inner, tmp_path)
    recorder.begin_case("g1")
    recorder.complete("p", GroundingReport, step="grounding")
    recorder.complete("p", DashboardSpec, step="propose_spec")
    recorder.end_case()

    replay = FixtureLLMClient(tmp_path)
    replay.begin_case("g1")
    report = replay.complete("different prompt now", GroundingReport, step="grounding")
    spec = replay.complete("different prompt now", DashboardSpec, step="propose_spec")
    assert isinstance(report, GroundingReport)
    assert isinstance(spec, DashboardSpec)


def test_recording_end_case_without_begin_is_a_noop(tmp_path) -> None:
    inner = ScriptedLLM([])
    RecordingLLMClient(inner, tmp_path).end_case()  # must not raise, must not write anything
    assert list(tmp_path.iterdir()) == []
