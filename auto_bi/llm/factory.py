"""LLM client factory — the single place that maps `AUTO_BI_LLM_PROVIDER` to a client.

Business code depends only on the `LLMClient` protocol (llm/base.py); this resolves the
concrete implementation from settings, mirroring `adapters/factory.py` for BI adapters.
Concrete clients are imported lazily so selecting one provider never imports the other
(e.g. the optional `anthropic` SDK is only touched when the anthropic provider is chosen).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from auto_bi.config import Settings

if TYPE_CHECKING:
    from auto_bi.llm.base import LLMClient
    from auto_bi.llm.budget import LLMBudget
    from auto_bi.store import Store


def make_llm(settings: Settings, store: Store | None = None) -> LLMClient:
    provider = settings.llm_provider.strip().lower()
    budget = _make_budget(settings, store)
    if provider == "gracekelly":
        from auto_bi.llm.gracekelly import GraceKellyClient

        return GraceKellyClient(settings, store=store, budget=budget)
    if provider == "anthropic":
        from auto_bi.llm.anthropic import AnthropicClient

        return AnthropicClient(settings, store=store, budget=budget)
    raise ValueError(
        f"unknown AUTO_BI_LLM_PROVIDER {settings.llm_provider!r} (use 'gracekelly' or 'anthropic')"
    )


def _make_budget(settings: Settings, store: Store | None) -> LLMBudget | None:
    """Build the client-seam LLM budget (audit P0-3 item 4) when enabled; else None.

    Off by default. Fails closed on misconfiguration: enabling the budget without a Store
    (the usage ledger it reads) raises here rather than silently not enforcing — the
    server and CLI both construct a Store before calling make_llm.
    """
    if not settings.llm_budget_enabled:
        return None
    if store is None:
        raise ValueError(
            "AUTO_BI_LLM_BUDGET_ENABLED=true needs a Store (the llm_calls usage ledger) "
            "but make_llm was called without one; wire the store or disable the budget"
        )
    from auto_bi.llm.budget import LLMBudget

    return LLMBudget.from_settings(settings, store)
