"""AnthropicClient tests with a fake `create` callable (no SDK, no network)."""

import importlib.util
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from auto_bi.config import Settings
from auto_bi.llm.anthropic import AnthropicClient
from auto_bi.llm.base import LLMError

ANTHROPIC_INSTALLED = importlib.util.find_spec("anthropic") is not None


class Answer(BaseModel):
    title: str
    count: int


def fake_response(
    text: str | None,
    stop_reason: str = "end_turn",
    usage: SimpleNamespace | None = None,
) -> SimpleNamespace:
    blocks = [] if text is None else [SimpleNamespace(type="text", text=text)]
    ns = SimpleNamespace(content=blocks, stop_reason=stop_reason)
    if usage is not None:  # the real SDK always attaches usage; omit it to test the None path
        ns.usage = usage
    return ns


def make_client(create, tmp_path, store=None) -> AnthropicClient:
    return AnthropicClient(
        Settings(_env_file=None),
        create=create,
        log_path=tmp_path / "llm_calls.jsonl",
        store=store,
    )


def test_complete_happy_path(tmp_path) -> None:
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return fake_response('```json\n{"title": "ok", "count": 5}\n```')

    result = make_client(create, tmp_path).complete("сделай", Answer)
    assert result == Answer(title="ok", count=5)
    # transport shape: single user message, configured model, thinking off by default
    assert calls[0]["model"] == "claude-sonnet-4-6"
    assert calls[0]["messages"] == [{"role": "user", "content": "сделай"}]
    assert calls[0]["thinking"] == {"type": "disabled"}


def test_reasoning_enables_adaptive_thinking(tmp_path) -> None:
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return fake_response('{"title": "ok", "count": 1}')

    make_client(create, tmp_path).complete("сделай", Answer, reasoning=True)
    assert calls[0]["thinking"] == {"type": "adaptive"}


def test_complete_repair_loop(tmp_path) -> None:
    prompts = []

    def create(**kwargs):
        prompts.append(kwargs["messages"][0]["content"])
        if len(prompts) == 1:
            return fake_response('{"title": "ok", "count": "не число"}')
        return fake_response('{"title": "ok", "count": 7}')

    result = make_client(create, tmp_path).complete("сделай", Answer)
    assert result.count == 7
    assert len(prompts) == 2
    assert "не прошёл валидацию" in prompts[1]


def test_refusal_raises(tmp_path) -> None:
    def create(**kwargs):
        return fake_response("anything", stop_reason="refusal")

    with pytest.raises(LLMError, match="refusal"):
        make_client(create, tmp_path).complete("сделай", Answer)


def test_empty_text_raises(tmp_path) -> None:
    def create(**kwargs):
        return fake_response("", stop_reason="end_turn")

    with pytest.raises(LLMError, match="no text"):
        make_client(create, tmp_path).complete("сделай", Answer)


def test_transport_error_becomes_llm_error(tmp_path) -> None:
    def create(**kwargs):
        raise RuntimeError("boom")

    with pytest.raises(LLMError, match="transport error"):
        make_client(create, tmp_path).complete("сделай", Answer)


def test_logs_step_and_completion_chars_to_store(tmp_path) -> None:
    from auto_bi.store import Store

    output = '{"title": "ok", "count": 5}'
    store = Store(tmp_path / "s.sqlite")
    sid = store.create_session("r")
    client = make_client(lambda **kw: fake_response(output), tmp_path, store=store)
    client.complete("сделай", Answer, session_id=sid, step="propose_spec")
    (call,) = store.llm_calls(sid)
    assert call["step"] == "propose_spec"
    assert call["completion_chars"] == len(output)
    assert call["model"] == "claude-sonnet-4-6"
    store.close()


def test_captures_token_usage_to_store_and_log(tmp_path) -> None:
    import json

    from auto_bi.store import Store

    output = '{"title": "ok", "count": 5}'
    usage = SimpleNamespace(input_tokens=120, output_tokens=45)
    store = Store(tmp_path / "s.sqlite")
    sid = store.create_session("r")
    client = make_client(lambda **kw: fake_response(output, usage=usage), tmp_path, store=store)
    client.complete("сделай", Answer, session_id=sid, step="propose_spec")
    (call,) = store.llm_calls(sid)
    assert call["input_tokens"] == 120 and call["output_tokens"] == 45
    # the jsonl log carries the same real usage
    record = json.loads((tmp_path / "llm_calls.jsonl").read_text(encoding="utf-8").strip())
    assert record["input_tokens"] == 120 and record["output_tokens"] == 45
    store.close()


def test_missing_usage_records_null_tokens(tmp_path) -> None:
    from auto_bi.store import Store

    # a response without a usage attribute (older SDK / fake) stores NULL, not a fake 0
    store = Store(tmp_path / "s.sqlite")
    sid = store.create_session("r")
    client = make_client(
        lambda **kw: fake_response('{"title": "ok", "count": 1}'), tmp_path, store=store
    )
    client.complete("сделай", Answer, session_id=sid)
    (call,) = store.llm_calls(sid)
    assert call["input_tokens"] is None and call["output_tokens"] is None
    store.close()


def test_calls_are_logged_without_prompt_content(tmp_path) -> None:
    import json

    client = make_client(lambda **kw: fake_response('{"title": "ok", "count": 1}'), tmp_path)
    client.complete("секретный запрос", Answer)
    record = json.loads((tmp_path / "llm_calls.jsonl").read_text(encoding="utf-8").strip())
    assert record["prompt_chars"] == len("секретный запрос")
    assert "секретный запрос" not in json.dumps(record)  # prompt content never logged


@pytest.mark.skipif(ANTHROPIC_INSTALLED, reason="exercises the missing-SDK path")
def test_missing_sdk_raises_clear_error() -> None:
    # No injected `create` -> the client tries to build the real SDK, which is absent here.
    with pytest.raises(LLMError, match="anthropic"):
        AnthropicClient(Settings(_env_file=None))
