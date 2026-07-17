"""LLM client-seam budget (audit P0-3 item 4).

Two layers of coverage:
- the `LLMBudget` enforcer against a real Store, driven by pre-logged ledger rows so
  every dimension (calls / tokens / time / cost) and scope (session vs actor/day) is
  deterministic without a live provider or real clocks;
- the enforcer wired through a real client (`GraceKellyClient` on `httpx.MockTransport`,
  `AnthropicClient` with a fake `create`), proving the initial call AND every repair draw
  the budget down and that a call crossing a limit is refused BEFORE it is issued.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from auto_bi.config import Settings
from auto_bi.llm.anthropic import AnthropicClient
from auto_bi.llm.budget import BudgetExceeded, BudgetLimits, LLMBudget, parse_prices
from auto_bi.llm.factory import make_llm
from auto_bi.llm.gracekelly import GraceKellyClient
from auto_bi.store import Store


class Answer(BaseModel):
    title: str
    count: int


def _store(tmp_path) -> Store:
    return Store(tmp_path / "s.sqlite")


def _log(
    store: Store,
    sid: str | None,
    *,
    model: str = "claude-sonnet-5",
    latency_ms: int = 0,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    prompt_chars: int = 0,
    completion_chars: int = 0,
    status: str = "completed",
) -> int:
    return store.log_llm_call(
        session_id=sid,
        model=model,
        prompt_sha256="deadbeef",
        prompt_chars=prompt_chars,
        reasoning=False,
        status=status,
        latency_ms=latency_ms,
        step="propose_spec",
        completion_chars=completion_chars,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _budget(
    store: Store,
    *,
    session: BudgetLimits | None = None,
    day: BudgetLimits | None = None,
    prices: dict[str, tuple[float, float]] | None = None,
) -> LLMBudget:
    return LLMBudget(
        store,
        session_limits=session or BudgetLimits(),
        day_limits=day or BudgetLimits(),
        prices=prices or {},
    )


# --- enforcer: per-dimension (session scope) ------------------------------------------


def test_under_limit_passes(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.create_session("r")
    _log(store, sid)
    _log(store, sid)
    _budget(store, session=BudgetLimits(max_calls=5)).check(session_id=sid, model="m")
    store.close()


def test_calls_limit_raises(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.create_session("r")
    _log(store, sid)
    _log(store, sid)
    with pytest.raises(BudgetExceeded, match="calls"):
        _budget(store, session=BudgetLimits(max_calls=2)).check(session_id=sid, model="m")
    store.close()


def test_tokens_limit_raises(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.create_session("r")
    _log(store, sid, input_tokens=80, output_tokens=40)  # 120 real tokens
    with pytest.raises(BudgetExceeded, match="tokens"):
        _budget(store, session=BudgetLimits(max_tokens=100)).check(session_id=sid, model="m")
    store.close()


def test_time_limit_raises(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.create_session("r")
    _log(store, sid, latency_ms=700)
    _log(store, sid, latency_ms=800)  # 1.5s of provider wall-clock
    with pytest.raises(BudgetExceeded, match="provider time"):
        _budget(store, session=BudgetLimits(max_seconds=1.0)).check(session_id=sid, model="m")
    store.close()


def test_cost_limit_raises(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.create_session("r")
    _log(store, sid, model="m", input_tokens=1000, output_tokens=1000)
    # 1k in * 0.003 + 1k out * 0.015 = $0.018 > $0.01
    budget = _budget(store, session=BudgetLimits(max_cost_usd=0.01), prices={"m": (0.003, 0.015)})
    with pytest.raises(BudgetExceeded, match=r"spent \$"):
        budget.check(session_id=sid, model="m")
    store.close()


def test_tokens_estimated_when_provider_reports_none(tmp_path) -> None:
    # GraceKelly reports no usage; the token budget still bites via a chars/4 estimate.
    store = _store(tmp_path)
    sid = store.create_session("r")
    _log(store, sid, input_tokens=None, output_tokens=None, prompt_chars=400, completion_chars=400)
    # est = 400/4 + 400/4 = 200 tokens > 150
    with pytest.raises(BudgetExceeded, match="tokens"):
        _budget(store, session=BudgetLimits(max_tokens=150)).check(session_id=sid, model="m")
    store.close()


def test_zero_limits_are_unlimited(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.create_session("r")
    for _ in range(10):
        _log(store, sid, latency_ms=9999, input_tokens=9999, output_tokens=9999)
    # every dimension left at 0 -> not enforced, however much is spent
    _budget(store, session=BudgetLimits()).check(session_id=sid, model="m")
    store.close()


# --- enforcer: scope isolation --------------------------------------------------------


def test_session_scope_isolated_between_sessions(tmp_path) -> None:
    store = _store(tmp_path)
    a = store.create_session("r")
    b = store.create_session("r")
    _log(store, a)
    _log(store, a)
    budget = _budget(store, session=BudgetLimits(max_calls=2))
    with pytest.raises(BudgetExceeded):
        budget.check(session_id=a, model="m")  # session a is at its cap
    budget.check(session_id=b, model="m")  # session b has its own budget
    store.close()


def test_actor_day_scope_spans_sessions_of_one_owner(tmp_path) -> None:
    store = _store(tmp_path)
    a = store.create_session("r", owner="alice")
    b = store.create_session("r", owner="alice")
    c = store.create_session("r", owner="bob")
    _log(store, a)
    _log(store, a)
    _log(store, b)  # alice: 3 calls across two sessions
    _log(store, c)  # bob: 1 call
    budget = _budget(store, day=BudgetLimits(max_calls=3))
    with pytest.raises(BudgetExceeded, match="alice"):
        budget.check(session_id=b, model="m")  # actor/day sees all 3 of alice's
    budget.check(session_id=c, model="m")  # bob is a different actor, unaffected
    store.close()


def test_actor_day_scope_is_global_when_auth_off(tmp_path) -> None:
    # auth off -> every session's owner is NULL -> one global daily bucket (demo breaker)
    store = _store(tmp_path)
    a = store.create_session("r")
    b = store.create_session("r")
    _log(store, a)
    _log(store, a)
    _log(store, b)  # 3 anonymous calls total
    with pytest.raises(BudgetExceeded, match=r"actor \*"):
        _budget(store, day=BudgetLimits(max_calls=3)).check(session_id=b, model="m")
    store.close()


def test_rolling_window_excludes_calls_older_than_the_window(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.create_session("r")
    old = _log(store, sid)
    # age that call out of the 24h window
    with store._lock, store._db:
        store._db.execute(
            "UPDATE llm_calls SET created_at = datetime('now', '-2 days') WHERE id = ?", (old,)
        )
    budget = _budget(store, day=BudgetLimits(max_calls=1))
    budget.check(session_id=sid, model="m")  # the aged call is outside the window
    _log(store, sid)  # a fresh call is inside it
    with pytest.raises(BudgetExceeded):
        budget.check(session_id=sid, model="m")
    store.close()


# --- wired through a real client: repairs draw down, refuse before issuing ------------


def _gk_response(output_text: str, status: str = "completed") -> httpx.Response:
    return httpx.Response(200, json={"task_id": "t", "status": status, "output_text": output_text})


def _gk_client(responder, tmp_path, store, budget) -> GraceKellyClient:
    http = httpx.Client(base_url="http://gk.test", transport=httpx.MockTransport(responder))
    return GraceKellyClient(
        Settings(_env_file=None),
        http=http,
        log_path=tmp_path / "llm.jsonl",
        store=store,
        budget=budget,
    )


def test_repairs_draw_down_the_session_budget(tmp_path) -> None:
    # invalid-schema answers force the repair loop; the cap must count each provider
    # round-trip, so a cap below 1 + MAX_REPAIRS trips mid-repair, not after 3 repairs.
    calls: list[int] = []

    def responder(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return _gk_response(f'{{"title": "ok", "count": "x{len(calls)}"}}')  # distinct, bad schema

    store = _store(tmp_path)
    sid = store.create_session("r")
    budget = _budget(store, session=BudgetLimits(max_calls=2))
    client = _gk_client(responder, tmp_path, store, budget)
    with pytest.raises(BudgetExceeded, match="calls"):
        client.complete("do", Answer, session_id=sid, step="propose_spec")
    assert len(calls) == 2  # exactly 2 round-trips consumed before the 3rd was refused
    store.close()


def test_under_budget_call_completes_through_the_client(tmp_path) -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        return _gk_response('{"title": "ok", "count": 5}')

    store = _store(tmp_path)
    sid = store.create_session("r")
    budget = _budget(store, session=BudgetLimits(max_calls=5), day=BudgetLimits(max_calls=50))
    client = _gk_client(responder, tmp_path, store, budget)
    assert client.complete("do", Answer, session_id=sid).count == 5
    store.close()


def test_day_cap_refuses_before_any_provider_round_trip(tmp_path) -> None:
    # the day bucket is already full from earlier requests; the next call must be refused
    # BEFORE the transport is touched (fail closed, before the call is issued).
    calls: list[int] = []

    def responder(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return _gk_response('{"title": "ok", "count": 5}')

    store = _store(tmp_path)
    earlier = store.create_session("r")
    _log(store, earlier)
    _log(store, earlier)  # global bucket already at 2
    sid = store.create_session("r")
    budget = _budget(store, day=BudgetLimits(max_calls=2))
    client = _gk_client(responder, tmp_path, store, budget)
    with pytest.raises(BudgetExceeded, match="actor"):
        client.complete("do", Answer, session_id=sid)
    assert calls == []  # transport never reached
    store.close()


def _anthropic_response(text: str) -> SimpleNamespace:
    # a text block that is valid JSON but fails the Answer schema (count is not an int),
    # so the repair loop retries — same shape the real SDK returns, minus usage.
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn"
    )


def test_repairs_draw_down_the_budget_on_anthropic_client(tmp_path) -> None:
    # the second concrete client wires the same hook; prove it counts repairs too.
    calls: list[int] = []

    def create(**kwargs: Any) -> SimpleNamespace:
        calls.append(1)
        return _anthropic_response(f'{{"title": "ok", "count": "x{len(calls)}"}}')

    store = _store(tmp_path)
    sid = store.create_session("r")
    budget = _budget(store, session=BudgetLimits(max_calls=2))
    client = AnthropicClient(
        Settings(_env_file=None),
        create=create,
        log_path=tmp_path / "llm.jsonl",
        store=store,
        budget=budget,
    )
    with pytest.raises(BudgetExceeded, match="calls"):
        client.complete("do", Answer, session_id=sid, step="propose_spec")
    assert len(calls) == 2
    store.close()


# --- factory wiring / off by default --------------------------------------------------


def test_budget_disabled_in_default_settings() -> None:
    assert Settings(_env_file=None).llm_budget_enabled is False


def test_make_llm_leaves_budget_unwired_by_default(tmp_path) -> None:
    store = _store(tmp_path)
    client = make_llm(Settings(_env_file=None, llm_provider="gracekelly"), store=store)
    assert isinstance(client, GraceKellyClient)
    assert client._budget is None
    store.close()


def test_make_llm_wires_budget_when_enabled(tmp_path) -> None:
    store = _store(tmp_path)
    settings = Settings(
        _env_file=None,
        llm_provider="gracekelly",
        llm_budget_enabled=True,
        llm_budget_session_max_calls=5,
    )
    client = make_llm(settings, store=store)
    assert client._budget is not None
    store.close()


def test_make_llm_budget_requires_a_store() -> None:
    settings = Settings(_env_file=None, llm_provider="gracekelly", llm_budget_enabled=True)
    with pytest.raises(ValueError, match="Store"):
        make_llm(settings, store=None)


# --- price table parsing --------------------------------------------------------------


def test_parse_prices_basic() -> None:
    assert parse_prices("m1:0.003/0.015, m2:1/2") == {"m1": (0.003, 0.015), "m2": (1.0, 2.0)}


def test_parse_prices_skips_blanks() -> None:
    assert parse_prices("   ") == {}


def test_parse_prices_bad_entry_raises() -> None:
    with pytest.raises(ValueError, match="AUTO_BI_LLM_BUDGET_PRICES"):
        parse_prices("garbage-no-colon")
