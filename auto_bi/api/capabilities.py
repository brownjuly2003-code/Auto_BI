"""Runtime capability surface for /health and the UI.

Capabilities are derived from how the process is actually wired (demo flag, LLM
client, model_path), not from aspirational config alone. The UI greys out modes
that cannot work; deploy smoke asserts the same object.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from auto_bi.llm.base import DisabledLLM, LLMClient


def llm_is_wired(llm: LLMClient) -> bool:
    """True when a real LLM client is injected (not the public-demo backstop)."""
    return not isinstance(llm, DisabledLLM)


def build_capabilities(
    *,
    demo_auto_only: bool,
    llm: LLMClient,
    model_path: str | Path | None,
) -> dict[str, Any]:
    """Capabilities object carried on GET /api/v1/health.

    - auto_overview: always on when the process is up (deterministic path).
    - text/fields/word_edit: require a non-demo profile AND a wired LLM.
    - enrichment: require a non-demo profile AND a writable model_path.
    - llm_wired: whether complete() can reach a provider (not DisabledLLM).
    """
    wired = llm_is_wired(llm)
    interactive = (not demo_auto_only) and wired
    return {
        "auto_overview": True,
        "text_session": interactive,
        "fields_session": interactive,
        "word_edit": interactive,
        "enrichment": (not demo_auto_only) and model_path is not None,
        "llm_wired": wired,
    }
