"""LLM seam: business code depends on this protocol, never on httpx/GraceKelly directly."""

from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """Transport failure, model failure, or structured output that never validated."""


class LLMClient(Protocol):
    def complete(
        self,
        prompt: str,
        schema: type[T],
        *,
        reasoning: bool = False,
        session_id: str | None = None,
    ) -> T:
        """Run the prompt and return a schema-validated object (repair loop inside)."""
        ...
