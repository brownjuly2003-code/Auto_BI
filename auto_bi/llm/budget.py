"""LLM budget enforcement at the client seam (audit P0-3 item 4).

The HTTP-endpoint quotas (`config.py` session_rate O-2 / work_rate P0-3) gate REQUESTS
but cannot see the provider round-trips a single request fans out into: grounding,
propose_spec, advisor narration, and up to `MAX_REPAIRS` schema-repair retries each hit
the provider. This budget is enforced where those round-trips actually happen — the
shared repair loop (`_structured.complete_with_repair`) calls `LLMBudget.check` before
EVERY attempt, so the initial call AND every repair draw the budget down, and a caller
cannot accidentally bypass it (the check lives at the one place `send_fn` is invoked).

Two scopes, both enforced per call:
- **session** — the whole conversation (session id), all-time;
- **actor / rolling day** — the session's owner when auth is on; a single global bucket
  when auth is off (the public demo), i.e. a total-spend circuit breaker across all
  anonymous visitors, in a rolling `window_hours` (24h) window.

Usage is read back from the existing `llm_calls` ledger (Store), which already records
every attempt with tokens/latency (`Store.log_llm_call`), so budgets survive across
requests and restarts without a parallel table. Tokens are real where the provider
reports them (Anthropic) and char-estimated (chars / 4) where it does not (GraceKelly).

Fail closed: `check` raises `BudgetExceeded` BEFORE issuing the call that would cross a
limit, naming the exceeded dimension. Opt-in, off by default (AUTO_BI_LLM_BUDGET_ENABLED),
matching the session/work quota convention — the LLM-enabled public demo turns it on
explicitly (docs/DEPLOYMENT.md); local dev / CLI / tests are unaffected. A limit of 0
(or 0.0) means that dimension is unlimited.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from auto_bi.llm.base import LLMError

if TYPE_CHECKING:
    from auto_bi.config import Settings
    from auto_bi.store import Store

DAY_WINDOW_HOURS = 24

# model -> (usd_per_1k_input_tokens, usd_per_1k_output_tokens)
ModelPrices = dict[str, tuple[float, float]]


class BudgetExceeded(LLMError):
    """An LLM call was refused because it would cross a configured budget limit.

    Subclasses LLMError so existing error handling treats a budget stop like any other
    LLM failure; the message names the scope and the exceeded dimension.
    """


@dataclass(frozen=True)
class BudgetLimits:
    """Caps for one scope. 0 / 0.0 = that dimension is unlimited (not enforced)."""

    max_calls: int = 0
    max_tokens: int = 0
    max_seconds: float = 0.0
    max_cost_usd: float = 0.0

    def is_active(self) -> bool:
        return bool(self.max_calls or self.max_tokens or self.max_seconds or self.max_cost_usd)


def parse_prices(spec: str) -> ModelPrices:
    """`"model:in/out,model2:in/out"` -> {model: (in_per_1k, out_per_1k)}; blanks skipped.

    Prices are USD per 1000 tokens. Raises ValueError on a malformed entry so a
    misconfigured price table fails fast at construction rather than mispricing silently.
    """
    prices: ModelPrices = {}
    for raw in spec.split(","):
        entry = raw.strip()
        if not entry:
            continue
        try:
            model, rates = entry.split(":", 1)
            inp, out = rates.split("/", 1)
            prices[model.strip()] = (float(inp), float(out))
        except ValueError as exc:
            raise ValueError(f"bad AUTO_BI_LLM_BUDGET_PRICES entry {entry!r}: {exc}") from exc
    return prices


class LLMBudget:
    """Enforces per-session and per-actor/rolling-day LLM limits from the usage ledger."""

    def __init__(
        self,
        store: Store,
        *,
        session_limits: BudgetLimits,
        day_limits: BudgetLimits,
        prices: ModelPrices | None = None,
        window_hours: int = DAY_WINDOW_HOURS,
    ) -> None:
        self._store = store
        self._session_limits = session_limits
        self._day_limits = day_limits
        self._prices = prices or {}
        self._window_hours = window_hours
        # a session's owner never changes; resolve it once per session id
        self._owner_cache: dict[str, str | None] = {}

    @classmethod
    def from_settings(cls, settings: Settings, store: Store) -> LLMBudget:
        return cls(
            store,
            session_limits=BudgetLimits(
                max_calls=settings.llm_budget_session_max_calls,
                max_tokens=settings.llm_budget_session_max_tokens,
                max_seconds=settings.llm_budget_session_max_seconds,
                max_cost_usd=settings.llm_budget_session_max_cost_usd,
            ),
            day_limits=BudgetLimits(
                max_calls=settings.llm_budget_day_max_calls,
                max_tokens=settings.llm_budget_day_max_tokens,
                max_seconds=settings.llm_budget_day_max_seconds,
                max_cost_usd=settings.llm_budget_day_max_cost_usd,
            ),
            prices=parse_prices(settings.llm_budget_prices),
        )

    def check(self, *, session_id: str | None, model: str) -> None:
        """Raise BudgetExceeded if the session or actor/day scope is already at a limit.

        Called before every provider round-trip (initial attempt AND each repair). The
        just-issued attempts of the current loop are already in the ledger, so this
        counts repairs; `model` is unused for scoping but documents the caller's model.
        """
        if self._session_limits.is_active() and session_id is not None:
            usage = self._store.session_llm_usage(session_id)
            self._enforce(usage, self._session_limits, f"session {session_id}")
        if self._day_limits.is_active():
            owner = self._owner_for(session_id)
            usage = self._store.actor_llm_usage(owner, window_hours=self._window_hours)
            who = owner if owner is not None else "*"
            self._enforce(usage, self._day_limits, f"actor {who} / {self._window_hours}h")

    def _owner_for(self, session_id: str | None) -> str | None:
        if session_id is None:
            return None
        if session_id not in self._owner_cache:
            row = self._store.session_row(session_id)
            self._owner_cache[session_id] = row.get("owner") if row else None
        return self._owner_cache[session_id]

    def _enforce(self, usage: dict[str, Any], limits: BudgetLimits, scope: str) -> None:
        calls = int(usage["calls"])
        if limits.max_calls and calls >= limits.max_calls:
            raise BudgetExceeded(
                f"LLM budget exceeded: {scope} already used {calls} calls "
                f"(limit {limits.max_calls})"
            )
        tokens = int(usage["tokens"])
        if limits.max_tokens and tokens >= limits.max_tokens:
            raise BudgetExceeded(
                f"LLM budget exceeded: {scope} already used {tokens} tokens "
                f"(limit {limits.max_tokens})"
            )
        seconds = int(usage["latency_ms"]) / 1000.0
        if limits.max_seconds and seconds >= limits.max_seconds:
            raise BudgetExceeded(
                f"LLM budget exceeded: {scope} already used {seconds:.1f}s of provider time "
                f"(limit {limits.max_seconds:.1f}s)"
            )
        if limits.max_cost_usd:
            cost = self._cost(usage["by_model"])
            if cost >= limits.max_cost_usd:
                raise BudgetExceeded(
                    f"LLM budget exceeded: {scope} already spent ${cost:.4f} "
                    f"(limit ${limits.max_cost_usd:.4f})"
                )

    def _cost(self, by_model: list[dict[str, Any]]) -> float:
        return cost_usd(by_model, self._prices)


def cost_usd(by_model: list[dict[str, Any]], prices: ModelPrices) -> float:
    """USD spend for per-model token rows, at the given price table (per 1000 tokens).

    Shared by the budget guard and the metrics endpoint so both price the same ledger the
    same way. An UNLISTED model prices at 0 — the documented behavior of the price table
    (config.llm_budget_prices), and the reason a cost limit on an unlisted model never fires.
    """
    total = 0.0
    for row in by_model:
        in_price, out_price = prices.get(row["model"], (0.0, 0.0))
        total += int(row["input_tokens"]) / 1000.0 * in_price
        total += int(row["output_tokens"]) / 1000.0 * out_price
    return total
