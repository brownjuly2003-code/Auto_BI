"""Direct Mistral chat-completions client behind the shared LLMClient seam."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast

import httpx
from pydantic import BaseModel

from auto_bi.config import Settings
from auto_bi.llm._structured import append_llm_log, complete_with_repair
from auto_bi.llm.base import LLMError

if TYPE_CHECKING:
    from auto_bi.llm.budget import LLMBudget
    from auto_bi.store import Store

T = TypeVar("T", bound=BaseModel)

_MAX_RATE_LIMIT_RETRIES = 4
_BACKOFF_BASE_SECONDS = 2.0
_BACKOFF_CAP_SECONDS = 30.0


def _extract_usage(data: dict[str, Any]) -> tuple[int | None, int | None]:
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None, None
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    return (
        prompt if isinstance(prompt, int) else None,
        completion if isinstance(completion, int) else None,
    )


def _extract_choice(data: dict[str, Any]) -> tuple[str, str]:
    """Return (text, finish_reason) for string or current chunked Mistral content."""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return "", "unknown"
    first = choices[0]
    finish_reason = first.get("finish_reason")
    status = finish_reason if isinstance(finish_reason, str) and finish_reason else "unknown"
    message = first.get("message")
    if not isinstance(message, dict):
        return "", status
    content = message.get("content")
    if isinstance(content, str):
        return content, status
    if not isinstance(content, list):
        return "", status
    parts: list[str] = []
    for chunk in content:
        if isinstance(chunk, dict) and isinstance(chunk.get("text"), str):
            parts.append(chunk["text"])
    return "".join(parts), status


def _retry_after_seconds(response: httpx.Response, attempt: int) -> float:
    header = response.headers.get("Retry-After")
    if header:
        try:
            return min(_BACKOFF_CAP_SECONDS, max(0.0, float(header)))
        except ValueError:
            pass
    return min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * (2.0**attempt))


class MistralClient:
    """Sync text-in/JSON-out client for Mistral's `/v1/chat/completions` API."""

    def __init__(
        self,
        settings: Settings,
        http: httpx.Client | None = None,
        log_path: str | Path = "logs/llm_calls.jsonl",
        store: Store | None = None,
        budget: LLMBudget | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        api_key = settings.mistral_api_key or os.environ.get("MISTRAL_API_KEY", "")
        if not api_key:
            raise LLMError(
                "Mistral API key is not configured; set MISTRAL_API_KEY or "
                "AUTO_BI_MISTRAL_API_KEY"
            )
        self._http = http or httpx.Client(
            base_url=settings.mistral_url,
            timeout=httpx.Timeout(300.0, connect=10.0),
            transport=httpx.HTTPTransport(retries=2),
            headers={"Authorization": f"Bearer {api_key}"},
        )
        self._log_path = Path(log_path)
        self._store = store
        self._budget = budget
        self._sleep = sleep

    def close(self) -> None:
        """Release the owned or injected HTTP pool."""
        self._http.close()

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
                lambda value: self._call(
                    value,
                    reasoning=reasoning,
                    session_id=session_id,
                    step=step,
                ),
                prompt,
                schema,
                on_attempt=self._budget_hook(session_id),
            ),
        )

    def _budget_hook(self, session_id: str | None) -> Callable[[], None] | None:
        if self._budget is None:
            return None
        budget = self._budget
        model = self._settings.mistral_model
        return lambda: budget.check(session_id=session_id, model=model)

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(1 + _MAX_RATE_LIMIT_RETRIES):
            response = self._http.post("/v1/chat/completions", json=payload)
            if response.status_code == 429:
                if attempt < _MAX_RATE_LIMIT_RETRIES:
                    self._sleep(_retry_after_seconds(response, attempt))
                    continue
                raise LLMError(
                    "Mistral API still rate-limited after " f"{_MAX_RATE_LIMIT_RETRIES} retries"
                )
            if response.status_code >= 400:
                # Provider bodies can echo request/auth diagnostics; never expose them.
                raise LLMError(f"Mistral API HTTP {response.status_code}")
            try:
                data = response.json()
            except ValueError as exc:
                raise LLMError("Mistral API returned invalid JSON") from exc
            if not isinstance(data, dict):
                raise LLMError("Mistral API returned a non-object response")
            return data
        raise LLMError("Mistral API retry loop exited unexpectedly")

    def _call(
        self,
        prompt: str,
        *,
        reasoning: bool,
        session_id: str | None,
        step: str,
    ) -> str:
        payload = {
            "model": self._settings.mistral_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self._settings.mistral_max_tokens,
            "temperature": 0,
        }
        started = time.monotonic()
        status = "transport_error"
        completion_chars = 0
        input_tokens: int | None = None
        output_tokens: int | None = None
        try:
            data = self._post(payload)
            input_tokens, output_tokens = _extract_usage(data)
            text, finish_reason = _extract_choice(data)
            status = (
                "completed"
                if finish_reason in {"stop", "length", "model_length"}
                else finish_reason
            )
            completion_chars = len(text)
            if not text:
                raise LLMError(f"Mistral returned no text (finish_reason={finish_reason})")
            return text
        except LLMError:
            raise
        except httpx.HTTPError as exc:
            raise LLMError(f"Mistral transport error: {exc}") from exc
        finally:
            append_llm_log(
                self._log_path,
                self._store,
                model=self._settings.mistral_model,
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
