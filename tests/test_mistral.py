"""MistralClient tests on httpx.MockTransport (no live API calls)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from auto_bi.config import Settings
from auto_bi.llm.base import LLMError
from auto_bi.llm.budget import BudgetExceeded, BudgetLimits, LLMBudget
from auto_bi.llm.factory import make_llm
from auto_bi.llm.mistral import MistralClient
from auto_bi.store import Store

REPO = Path(__file__).resolve().parents[1]


class Answer(BaseModel):
    title: str
    count: int


Responder = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def client_factory(tmp_path):
    clients: list[MistralClient] = []

    def make(
        responder: Responder,
        *,
        settings: Settings | None = None,
        store: Store | None = None,
        budget: LLMBudget | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> MistralClient:
        http = httpx.Client(
            base_url="https://api.mistral.test",
            transport=httpx.MockTransport(responder),
        )
        client = MistralClient(
            settings or Settings(_env_file=None, mistral_api_key="test-key"),
            http=http,
            log_path=tmp_path / "llm_calls.jsonl",
            store=store,
            budget=budget,
            sleep=sleep or (lambda _seconds: None),
        )
        clients.append(client)
        return client

    yield make

    for client in clients:
        client.close()


def mistral_response(
    content: str | list[dict[str, str]] | None,
    *,
    finish: str = "stop",
    usage: dict[str, int] | None = None,
) -> httpx.Response:
    choices = (
        []
        if content is None
        else [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish,
            }
        ]
    )
    body: dict[str, Any] = {"choices": choices}
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(200, json=body)


def test_complete_uses_official_chat_completions_shape(client_factory) -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == "/v1/chat/completions"
        assert body["model"] == "mistral-large-latest"
        assert body["messages"] == [{"role": "user", "content": "сделай"}]
        assert body["temperature"] == 0
        assert body["max_tokens"] == 16000
        return mistral_response('```json\n{"title": "ok", "count": 5}\n```')

    result = client_factory(responder).complete("сделай", Answer)
    assert result == Answer(title="ok", count=5)


def test_list_content_chunks_are_joined(client_factory) -> None:
    response = [
        {"type": "text", "text": '{"title": "ok", '},
        {"type": "text", "text": '"count": 6}'},
    ]
    result = client_factory(lambda _request: mistral_response(response)).complete("сделай", Answer)
    assert result == Answer(title="ok", count=6)


def test_complete_uses_shared_repair_loop(client_factory) -> None:
    prompts: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        prompts.append(json.loads(request.content)["messages"][0]["content"])
        if len(prompts) == 1:
            return mistral_response('{"title": "ok", "count": "не число"}')
        return mistral_response('{"title": "ok", "count": 7}')

    result = client_factory(responder).complete("сделай", Answer)
    assert result.count == 7
    assert len(prompts) == 2
    assert "не прошёл валидацию" in prompts[1]


def test_standard_environment_key_is_used_without_logging_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "test-standard-key")
    client = MistralClient(
        Settings(_env_file=None),
        log_path=tmp_path / "llm_calls.jsonl",
    )
    try:
        assert client._http.headers["Authorization"] == "Bearer test-standard-key"
        assert not (tmp_path / "llm_calls.jsonl").exists()
    finally:
        client.close()


def test_missing_key_fails_before_transport(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    with pytest.raises(LLMError, match="API key is not configured"):
        MistralClient(Settings(_env_file=None), log_path=tmp_path / "llm_calls.jsonl")


def test_standard_key_name_is_loaded_from_dotenv(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("MISTRAL_API_KEY=test-dotenv-key\n", encoding="utf-8")
    settings = Settings(_env_file=env_file)
    assert settings.mistral_api_key == "test-dotenv-key"


def test_http_error_omits_provider_response_body(client_factory) -> None:
    marker = "MISTRAL_SECRET_RESPONSE_MARKER"

    def responder(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"api_key": marker})

    with pytest.raises(LLMError) as exc_info:
        client_factory(responder).complete("сделай", Answer)
    message = str(exc_info.value)
    assert "HTTP 401" in message
    assert marker not in message


def test_retries_429_with_retry_after_then_succeeds(client_factory) -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    def responder(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) <= 2:
            return httpx.Response(429, headers={"Retry-After": "0.25"})
        return mistral_response('{"title": "ok", "count": 3}')

    result = client_factory(responder, sleep=sleeps.append).complete("сделай", Answer)
    assert result.count == 3
    assert len(calls) == 3
    assert sleeps == [0.25, 0.25]


def test_persistent_429_stops_after_bounded_retries(client_factory) -> None:
    calls: list[int] = []

    def responder(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, json={"message": "rate limited"})

    with pytest.raises(LLMError, match="rate-limited after 4 retries"):
        client_factory(responder).complete("сделай", Answer)
    assert len(calls) == 5


def test_usage_and_completed_status_are_written_to_store(client_factory, tmp_path) -> None:
    store = Store(tmp_path / "usage.sqlite")
    session_id = store.create_session("r")
    usage = {"prompt_tokens": 120, "completion_tokens": 45}
    client = client_factory(
        lambda _request: mistral_response(
            '{"title": "ok", "count": 5}',
            usage=usage,
        ),
        store=store,
    )
    client.complete("сделай", Answer, session_id=session_id, step="propose_spec")
    (call,) = store.llm_calls(session_id)
    assert call["step"] == "propose_spec"
    assert call["input_tokens"] == 120
    assert call["output_tokens"] == 45
    assert call["model"] == "mistral-large-latest"
    assert call["status"] == "completed"
    store.close()


def test_budget_blocks_repair_before_third_provider_call(client_factory, tmp_path) -> None:
    store = Store(tmp_path / "budget.sqlite")
    session_id = store.create_session("r")
    budget = LLMBudget(
        store,
        session_limits=BudgetLimits(max_calls=2),
        day_limits=BudgetLimits(),
    )
    calls: list[int] = []

    def responder(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return mistral_response(f'{{"title": "ok", "count": "bad-{len(calls)}"}}')

    with pytest.raises(BudgetExceeded, match="calls"):
        client_factory(responder, store=store, budget=budget).complete(
            "сделай",
            Answer,
            session_id=session_id,
        )
    assert len(calls) == 2
    store.close()


def test_factory_routes_mistral_and_wires_budget(tmp_path) -> None:
    store = Store(tmp_path / "factory.sqlite")
    settings = Settings(
        _env_file=None,
        llm_provider="mistral",
        mistral_api_key="test-key",
        llm_budget_enabled=True,
        llm_budget_session_max_calls=5,
    )
    client = make_llm(settings, store=store)
    try:
        assert isinstance(client, MistralClient)
        assert client._budget is not None
    finally:
        client.close()
        store.close()


def test_close_releases_injected_http_pool(client_factory) -> None:
    client = client_factory(lambda _request: mistral_response('{"title": "ok", "count": 1}'))
    http = client._http
    client.close()
    assert http.is_closed


def test_live_sentinel_routes_mistral_secret_and_selects_provider() -> None:
    workflow = (REPO / ".github" / "workflows" / "eval-live-sentinel.yml").read_text(
        encoding="utf-8"
    )
    assert "MISTRAL_API_KEY: ${{ secrets.MISTRAL_API_KEY }}" in workflow
    assert "AUTO_BI_MISTRAL_API_KEY: ${{ secrets.AUTO_BI_MISTRAL_API_KEY }}" in workflow
    assert "AUTO_BI_MISTRAL_MODEL: ${{ secrets.AUTO_BI_MISTRAL_MODEL }}" in workflow
    assert 'AUTO_BI_LLM_PROVIDER="mistral"' in workflow
    assert (
        'if [ -n "${MISTRAL_API_KEY}" ] || ' '[ -n "${AUTO_BI_MISTRAL_API_KEY}" ]; then'
    ) in workflow


def test_example_and_budget_table_cover_mistral() -> None:
    env_example = (REPO / ".env.example").read_text(encoding="utf-8")
    assert "MISTRAL_API_KEY=change_me" in env_example
    assert "AUTO_BI_MISTRAL_MODEL=mistral-large-latest" in env_example
    prices = Settings(_env_file=None).llm_budget_prices
    assert "mistral-large-latest:0.0005/0.0015" in prices


def test_cli_hosted_llm_readiness_message_uses_configured_provider() -> None:
    """llm_healthcheck must label the actual hosted provider, not hardcode anthropic.

    Nested closure in serve wiring — source-ratchet (same style as workflow checks above).
    """
    source = (REPO / "auto_bi" / "cli.py").read_text(encoding="utf-8")
    assert 'message="anthropic: no live check (avoids token cost)"' not in source
    assert 'message=f"{provider}: no live check (avoids token cost)"' in source
    assert "provider = settings.llm_provider.strip().lower()" in source


def test_auto_bi_prefixed_mistral_key_alias_populates_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.setenv("AUTO_BI_MISTRAL_API_KEY", "test-auto-bi-mistral-key")
    settings = Settings(_env_file=None)
    assert settings.mistral_api_key == "test-auto-bi-mistral-key"
