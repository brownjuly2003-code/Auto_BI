"""Shared pydantic bases with extra="forbid" (audit P1-5 / ARCHITECTURE D8).

Unknown keys must fail validation rather than be silently ignored — a typo in an
LLM JSON block or API body (`limt`, `max_chart`, `tablse`) must surface as a
repair/422 error, not change behaviour with a default.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Base for IR, semantic model, and API request bodies."""

    model_config = ConfigDict(extra="forbid")
