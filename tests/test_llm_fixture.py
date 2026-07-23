"""Record/replay for golden-eval LLM calls (S11 + plan_sol step 9 fingerprints)."""

from __future__ import annotations

import json

import pytest

from auto_bi.agent.grounding import GroundingReport
from auto_bi.ir.spec import DashboardSpec
from auto_bi.llm.fixture import (
    FIXTURE_FORMAT_VERSION,
    FixtureLLMClient,
    FixtureMissingError,
    FixtureStaleError,
    RecordingLLMClient,
    compute_schema_version,
    compute_template_version,
    current_contract_meta,
    hash_prompt,
    normalize_prompt,
)
from tests.test_machine import CLEAR_REPORT, GOOD_SPEC, ScriptedLLM


def _write_fixture(tmp_path, case_id: str, calls: list[dict], **meta: object) -> None:
    payload = {
        "case_id": case_id,
        **current_contract_meta(provider="test", model_id="test"),
        **meta,
        "calls": calls,
    }
    (tmp_path / f"{case_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _grounding_call(response: dict, prompt: str = "prompt") -> dict:
    return {
        "step": "grounding",
        "schema": "GroundingReport",
        "prompt_sha256": hash_prompt(prompt),
        "response": response,
    }


def _propose_call(response: dict, prompt: str = "prompt") -> dict:
    return {
        "step": "propose_spec",
        "schema": "DashboardSpec",
        "prompt_sha256": hash_prompt(prompt),
        "response": response,
    }


# --- normalize / contract meta -----------------------------------------------------


def test_normalize_prompt_collapses_whitespace() -> None:
    assert normalize_prompt("  a \t  b\n\n\nc  ") == "a b\n\nc"
    assert hash_prompt("x") == hash_prompt("x\n")
    assert hash_prompt("a  b") == hash_prompt("a b")


def test_template_and_schema_version_are_stable_hex() -> None:
    t1, t2 = compute_template_version(), compute_template_version()
    s1, s2 = compute_schema_version(), compute_schema_version()
    assert t1 == t2 and len(t1) == 16
    assert s1 == s2 and len(s1) == 16
    assert t1 != s1


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
    llm.end_case()  # no-op in pure replay


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

    llm.begin_case("g2")
    llm.complete("prompt", GroundingReport, step="grounding")
    llm.complete("prompt", DashboardSpec, step="propose_spec")


def test_replay_stale_on_prompt_hash_mismatch(tmp_path) -> None:
    _write_fixture(tmp_path, "g1", [_grounding_call(CLEAR_REPORT, prompt="old prompt")])
    llm = FixtureLLMClient(tmp_path)
    llm.begin_case("g1")
    with pytest.raises(FixtureStaleError, match="prompt_sha256"):
        llm.complete("brand new prompt text", GroundingReport, step="grounding")


def test_replay_stale_on_legacy_format_without_fingerprints(tmp_path) -> None:
    (tmp_path / "g1.json").write_text(
        json.dumps(
            {
                "case_id": "g1",
                "calls": [
                    {
                        "step": "grounding",
                        "schema": "GroundingReport",
                        "response": CLEAR_REPORT,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    llm = FixtureLLMClient(tmp_path)
    with pytest.raises(FixtureStaleError, match="format_version"):
        llm.begin_case("g1")


def test_replay_stale_on_template_version_mismatch(tmp_path) -> None:
    _write_fixture(
        tmp_path,
        "g1",
        [_grounding_call(CLEAR_REPORT)],
        template_version="deadbeefdeadbeef",
    )
    llm = FixtureLLMClient(tmp_path)
    with pytest.raises(FixtureStaleError, match="template_version"):
        llm.begin_case("g1")


def test_refresh_fingerprints_rewrites_hashes_and_meta(tmp_path) -> None:
    # legacy-shaped calls (no hashes) + wrong meta — refresh stamps current contract
    (tmp_path / "g1.json").write_text(
        json.dumps(
            {
                "case_id": "g1",
                "format_version": 1,
                "template_version": "old",
                "schema_version": "old",
                "calls": [
                    {
                        "step": "grounding",
                        "schema": "GroundingReport",
                        "response": CLEAR_REPORT,
                    },
                    {
                        "step": "propose_spec",
                        "schema": "DashboardSpec",
                        "response": GOOD_SPEC,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    llm = FixtureLLMClient(tmp_path, refresh_fingerprints=True)
    llm.begin_case("g1")
    llm.complete("prompt-a", GroundingReport, step="grounding")
    llm.complete("prompt-b", DashboardSpec, step="propose_spec")
    llm.end_case()

    written = json.loads((tmp_path / "g1.json").read_text(encoding="utf-8"))
    assert written["format_version"] == FIXTURE_FORMAT_VERSION
    assert written["template_version"] == compute_template_version()
    assert written["schema_version"] == compute_schema_version()
    assert written["provider"] == "fixture-refresh"
    assert written["calls"][0]["prompt_sha256"] == hash_prompt("prompt-a")
    assert written["calls"][1]["prompt_sha256"] == hash_prompt("prompt-b")

    # after refresh, strict replay accepts the same prompts
    replay = FixtureLLMClient(tmp_path)
    replay.begin_case("g1")
    replay.complete("prompt-a", GroundingReport, step="grounding")
    replay.complete("prompt-b", DashboardSpec, step="propose_spec")


# --- RecordingLLMClient -------------------------------------------------------------


def test_recording_passes_through_and_writes_fixture_on_end_case(tmp_path) -> None:
    inner = ScriptedLLM([CLEAR_REPORT, GOOD_SPEC])
    llm = RecordingLLMClient(inner, tmp_path, provider="test", model_id="scripted")

    llm.begin_case("g1")
    report = llm.complete("prompt-1", GroundingReport, step="grounding")
    spec = llm.complete("prompt-2", DashboardSpec, step="propose_spec")
    llm.end_case()

    assert isinstance(report, GroundingReport)
    assert isinstance(spec, DashboardSpec)
    assert inner.calls == [
        ("GroundingReport", "prompt-1", False),
        ("DashboardSpec", "prompt-2", False),
    ]

    written = json.loads((tmp_path / "g1.json").read_text(encoding="utf-8"))
    assert written["case_id"] == "g1"
    assert written["format_version"] == FIXTURE_FORMAT_VERSION
    assert written["provider"] == "test"
    assert written["model_id"] == "scripted"
    assert written["template_version"] == compute_template_version()
    assert [c["step"] for c in written["calls"]] == ["grounding", "propose_spec"]
    assert written["calls"][0]["prompt_sha256"] == hash_prompt("prompt-1")
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
    report = replay.complete("p", GroundingReport, step="grounding")
    spec = replay.complete("p", DashboardSpec, step="propose_spec")
    assert isinstance(report, GroundingReport)
    assert isinstance(spec, DashboardSpec)


def test_recording_end_case_without_begin_is_a_noop(tmp_path) -> None:
    inner = ScriptedLLM([])
    RecordingLLMClient(inner, tmp_path).end_case()
    assert list(tmp_path.iterdir()) == []
