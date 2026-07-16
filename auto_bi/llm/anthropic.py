"""AnthropicClient — direct Anthropic Messages API behind the same LLMClient seam.

A second implementation of the `LLMClient` protocol so Auto_BI can run WITHOUT the
GraceKelly service (removing that single point of failure — see ARCHITECTURE §3.6,
"if we hit tool-use/caching limits, a direct AnthropicClient is added without changing
the agent"). The structured-output repair loop and call logging are shared with
GraceKellyClient via `auto_bi.llm._structured`; only the transport differs.

The `anthropic` SDK is a **core** dependency (audit P1-3: default provider must work
after plain `pip install autobi-agent` / production Docker). It is still imported
lazily, so tests can inject a fake `create` callable without constructing the real client.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast

from pydantic import BaseModel

from auto_bi.config import Settings
from auto_bi.llm._structured import append_llm_log, complete_with_repair
from auto_bi.llm.base import LLMError

if TYPE_CHECKING:
    from auto_bi.store import Store

T = TypeVar("T", bound=BaseModel)

# A callable with the shape of `anthropic.Anthropic().messages.create` (kwargs in, response out).
MessagesCreate = Callable[..., Any]


def _build_create(settings: Settings) -> MessagesCreate:
    """Construct the real Anthropic SDK client lazily; clear error if it's unavailable."""
    try:
        import anthropic
    except ImportError as exc:  # optional dependency
        raise LLMError(
            "the 'anthropic' package is required for AUTO_BI_LLM_PROVIDER=anthropic "
            "but is not importable; reinstall with `pip install autobi-agent` "
            "(anthropic is a core dependency) or `uv sync`"
        ) from exc
    try:
        # api_key blank -> SDK falls back to the ANTHROPIC_API_KEY env var.
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key or None)
    except Exception as exc:  # missing key, bad config
        raise LLMError(f"failed to initialise the Anthropic client: {exc}") from exc
    return client.messages.create


def _extract_text(response: Any) -> str:
    """Concatenate the text content blocks of a Messages API response (ignore thinking blocks)."""
    parts: list[str] = []
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    return "".join(parts)


def _extract_usage(response: Any) -> tuple[int | None, int | None]:
    """Real (input_tokens, output_tokens) from a Messages API response, or (None, None).

    The Anthropic SDK exposes `response.usage.input_tokens/output_tokens`; defending with
    getattr keeps the parser robust to a fake/old response object that omits usage."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return None, None
    return getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None)


class AnthropicClient:
    """LLMClient backed by the Anthropic Messages API (sync, text-in/JSON-out)."""

    def __init__(
        self,
        settings: Settings,
        *,
        create: MessagesCreate | None = None,
        log_path: str | Path = "logs/llm_calls.jsonl",
        store: Store | None = None,
    ) -> None:
        self._settings = settings
        # Injected `create` keeps unit tests SDK-free; otherwise build the real client now,
        # which is also where the optional dependency / API key is actually required.
        self._create = create or _build_create(settings)
        self._log_path = Path(log_path)
        self._store = store

    def complete(
        self,
        prompt: str,
        schema: type[T],
        *,
        reasoning: bool = False,
        session_id: str | None = None,
        step: str = "",
    ) -> T:
        return cast(
            T,
            complete_with_repair(
                lambda p: self._call(p, reasoning=reasoning, session_id=session_id, step=step),
                prompt,
                schema,
            ),
        )

    def _call(self, prompt: str, *, reasoning: bool, session_id: str | None, step: str) -> str:
        started = time.monotonic()
        status = "transport_error"
        completion_chars = 0
        input_tokens: int | None = None
        output_tokens: int | None = None
        try:
            # reasoning -> adaptive thinking on GROUNDING/PROPOSE; mechanical steps run without it
            # (Sonnet 4.6 supports both; mirrors the GraceKelly reasoning flag, llm/policy.py).
            thinking = {"type": "adaptive"} if reasoning else {"type": "disabled"}
            response = self._create(
                model=self._settings.anthropic_model,
                max_tokens=self._settings.anthropic_max_tokens,
                thinking=thinking,
                messages=[{"role": "user", "content": prompt}],
            )
            # Native stop_reason (end_turn / max_tokens / refusal / …). Map successful
            # completions to status='completed' so usage dashboards match GraceKelly
            # (audit P2-1); keep the native reason in the error text on failure paths.
            stop_reason = getattr(response, "stop_reason", None) or "unknown"
            status = (
                "completed"
                if stop_reason in ("end_turn", "max_tokens", "stop_sequence")
                else stop_reason
            )
            # capture usage before the refusal/empty guards so even a refused or empty
            # response records the input tokens it actually spent
            input_tokens, output_tokens = _extract_usage(response)
            if stop_reason == "refusal":
                raise LLMError("Anthropic declined the request (stop_reason=refusal)")
            text = _extract_text(response)
            completion_chars = len(text)
            if not text:
                raise LLMError(f"Anthropic returned no text (stop_reason={stop_reason})")
            return text
        except LLMError:
            raise
        except Exception as exc:  # SDK/transport error
            raise LLMError(f"Anthropic transport error: {exc}") from exc
        finally:
            append_llm_log(
                self._log_path,
                self._store,
                model=self._settings.anthropic_model,
                prompt=prompt,
                reasoning=reasoning,
                status=status,
                latency_ms=round((time.monotonic() - started) * 1000),
                session_id=session_id,
                step=step,
                completion_chars=completion_chars,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
