"""GraceKellyClient tests on httpx.MockTransport (no live service)."""

import json

import httpx
import pytest
from pydantic import BaseModel

from auto_bi.config import Settings
from auto_bi.llm.base import LLMError
from auto_bi.llm.gracekelly import PROMPT_LIMIT, GraceKellyClient, extract_json


class Answer(BaseModel):
    title: str
    count: int


def make_client(responder, tmp_path) -> GraceKellyClient:
    transport = httpx.MockTransport(responder)
    http = httpx.Client(base_url="http://gk.test", transport=transport)
    return GraceKellyClient(
        Settings(_env_file=None), http=http, log_path=tmp_path / "llm_calls.jsonl"
    )


def gk_response(output_text: str | None, status: str = "completed") -> httpx.Response:
    return httpx.Response(
        200,
        json={"task_id": "t1", "status": status, "output_text": output_text},
    )


# --- extract_json ---------------------------------------------------------


def test_extract_json_fenced() -> None:
    text = 'Вот спека:\n```json\n{"title": "x", "count": 1}\n```\nГотово.'
    assert json.loads(extract_json(text)) == {"title": "x", "count": 1}


def test_extract_json_bare_balanced() -> None:
    text = 'предисловие {"title": "a {b}", "count": 2} хвост {"another": 1}'
    assert json.loads(extract_json(text)) == {"title": "a {b}", "count": 2}


def test_extract_json_none() -> None:
    with pytest.raises(ValueError):
        extract_json("никакого джейсона тут нет")


# --- complete -------------------------------------------------------------


def test_complete_happy_path(tmp_path) -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "claude-sonnet-5"
        assert body["decompose"] is False
        assert body["metadata"] == {"app": "auto_bi"}
        return gk_response('```json\n{"title": "ok", "count": 5}\n```')

    result = make_client(responder, tmp_path).complete("сделай", Answer)
    assert result == Answer(title="ok", count=5)


def test_complete_logs_step_and_completion_chars_to_store(tmp_path) -> None:
    from auto_bi.store import Store

    output = '```json\n{"title": "ok", "count": 5}\n```'

    def responder(request: httpx.Request) -> httpx.Response:
        return gk_response(output)

    store = Store(tmp_path / "s.sqlite")
    sid = store.create_session("r")
    transport = httpx.MockTransport(responder)
    http = httpx.Client(base_url="http://gk.test", transport=transport)
    client = GraceKellyClient(
        Settings(_env_file=None), http=http, log_path=tmp_path / "llm_calls.jsonl", store=store
    )
    client.complete("сделай", Answer, session_id=sid, step="propose_spec")
    (call,) = store.llm_calls(sid)
    assert call["step"] == "propose_spec"
    assert call["completion_chars"] == len(output)
    store.close()


def test_complete_repair_loop(tmp_path) -> None:
    calls = []

    def responder(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content)["prompt"])
        if len(calls) == 1:
            return gk_response('{"title": "ok", "count": "не число"}')
        return gk_response('{"title": "ok", "count": 7}')

    result = make_client(responder, tmp_path).complete("сделай", Answer)
    assert result.count == 7
    assert len(calls) == 2
    assert "не прошёл валидацию" in calls[1]


def test_complete_gives_up_after_repairs(tmp_path) -> None:
    calls = []

    def responder(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return gk_response(f"это вообще не JSON {len(calls)}")  # distinct each time

    with pytest.raises(LLMError, match="after 3 repairs"):
        make_client(responder, tmp_path).complete("сделай", Answer)
    assert len(calls) == 4  # first try + 3 repairs


def test_complete_aborts_on_identical_answer(tmp_path) -> None:
    calls = []

    def responder(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return gk_response("это вообще не JSON")  # same broken answer every time

    with pytest.raises(LLMError, match="after 3 repairs"):
        make_client(responder, tmp_path).complete("сделай", Answer)
    assert len(calls) == 2  # stop repairing once the answer repeats (F7)


def test_failed_task_raises(tmp_path) -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "task_id": "t1",
                "status": "failed",
                "output_text": None,
                "failure_code": "model_error",
                "failure_message": "boom",
            },
        )

    with pytest.raises(LLMError, match="failed"):
        make_client(responder, tmp_path).complete("сделай", Answer)


def test_prompt_over_limit_raises(tmp_path) -> None:
    client = make_client(lambda r: gk_response("{}"), tmp_path)
    with pytest.raises(LLMError, match="limit"):
        client.complete("x" * (PROMPT_LIMIT + 1), Answer)


def test_calls_are_logged(tmp_path) -> None:
    client = make_client(lambda r: gk_response('{"title": "ok", "count": 1}'), tmp_path)
    client.complete("сделай", Answer)
    lines = (tmp_path / "llm_calls.jsonl").read_text(encoding="utf-8").strip().splitlines()
    record = json.loads(lines[0])
    assert record["status"] == "completed"
    assert record["prompt_chars"] == len("сделай")
    assert "сделай" not in json.dumps(record)  # prompt content never logged
