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
    from auto_bi.store import Store


def make_llm(settings: Settings, store: Store | None = None) -> LLMClient:
    provider = settings.llm_provider.strip().lower()
    if provider == "gracekelly":
        from auto_bi.llm.gracekelly import GraceKellyClient

        return GraceKellyClient(settings, store=store)
    if provider == "anthropic":
        from auto_bi.llm.anthropic import AnthropicClient

        return AnthropicClient(settings, store=store)
    raise ValueError(
        f"unknown AUTO_BI_LLM_PROVIDER {settings.llm_provider!r} (use 'gracekelly' or 'anthropic')"
    )
