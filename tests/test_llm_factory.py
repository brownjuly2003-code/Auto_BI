"""make_llm dispatch tests (provider -> concrete LLMClient)."""

import importlib.util

import pytest

from auto_bi.config import Settings
from auto_bi.llm.base import LLMError
from auto_bi.llm.factory import make_llm
from auto_bi.llm.gracekelly import GraceKellyClient

ANTHROPIC_INSTALLED = importlib.util.find_spec("anthropic") is not None


def test_default_provider_is_gracekelly() -> None:
    llm = make_llm(Settings(_env_file=None))
    assert isinstance(llm, GraceKellyClient)


def test_explicit_gracekelly() -> None:
    llm = make_llm(Settings(_env_file=None, llm_provider="gracekelly"))
    assert isinstance(llm, GraceKellyClient)


def test_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="unknown AUTO_BI_LLM_PROVIDER"):
        make_llm(Settings(_env_file=None, llm_provider="bogus"))


@pytest.mark.skipif(ANTHROPIC_INSTALLED, reason="anthropic branch without the SDK installed")
def test_anthropic_provider_routes_to_anthropic_client() -> None:
    # Routing reaches AnthropicClient, which then reports the missing optional SDK.
    with pytest.raises(LLMError, match="anthropic"):
        make_llm(Settings(_env_file=None, llm_provider="anthropic"))
