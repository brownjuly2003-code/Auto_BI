"""GraceKelly client: POST /orchestrate + structured-output repair loop (max 3).

Contract (verified against GraceKelly source, 2026-06-11):
- request: prompt (<=40k, server-enforced), model, reasoning, decompose, session_id, metadata;
- response: status accepted|completed|failed|cancelled, output_text, failure_code/message;
- the endpoint is synchronous.
Every call is appended to logs/llm_calls.jsonl (prompt hash, latency, status — never the prompt).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar, cast

import httpx
from pydantic import BaseModel

from auto_bi.config import Settings
from auto_bi.llm._structured import append_llm_log, complete_with_repair, extract_json
from auto_bi.llm.base import LLMError

if TYPE_CHECKING:
    from auto_bi.store import Store

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)

# GraceKelly enforces a 40k-char prompt limit (its own constraint — see ARCHITECTURE §3.6).
PROMPT_LIMIT = 40_000

# `extract_json` is re-exported (tests and callers import it from this module).
__all__ = ["PROMPT_LIMIT", "GraceKellyClient", "extract_json"]


class GraceKellyClient:
    def __init__(
        self,
        settings: Settings,
        http: httpx.Client | None = None,
        log_path: str | Path = "logs/llm_calls.jsonl",
        store: Store | None = None,
    ) -> None:
        self._settings = settings
        self._http = http or httpx.Client(
            base_url=settings.gracekelly_url,
            timeout=httpx.Timeout(300.0, connect=10.0),
            transport=httpx.HTTPTransport(retries=2),  # transient connect failures only
        )
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
        if len(prompt) > PROMPT_LIMIT:
            raise LLMError(f"prompt is {len(prompt)} chars, GraceKelly limit is {PROMPT_LIMIT}")
        payload = {
            "prompt": prompt,
            "model": self._settings.gracekelly_model,
            "reasoning": reasoning,
            "decompose": False,
            "session_id": session_id,
            "metadata": {"app": "auto_bi"},
        }
        started = time.monotonic()
        status = "transport_error"
        completion_chars = 0
        try:
            response = self._http.post("/api/v1/orchestrate", json=payload)
            response.raise_for_status()
            data = response.json()
            status = data.get("status", "unknown")
            output = data.get("output_text") or ""
            completion_chars = len(output)
            if status != "completed" or not output:
                raise LLMError(
                    f"GraceKelly task {status}: "
                    f"{data.get('failure_code')} {data.get('failure_message')}"
                )
            return output
        except httpx.HTTPError as exc:
            raise LLMError(f"GraceKelly transport error: {exc}") from exc
        finally:
            self._log(
                prompt,
                reasoning,
                status,
                time.monotonic() - started,
                session_id,
                step,
                completion_chars,
            )

    def _log(
        self,
        prompt: str,
        reasoning: bool,
        status: str,
        latency_s: float,
        session_id: str | None = None,
        step: str = "",
        completion_chars: int = 0,
    ) -> None:
        append_llm_log(
            self._log_path,
            self._store,
            model=self._settings.gracekelly_model,
            prompt=prompt,
            reasoning=reasoning,
            status=status,
            latency_ms=round(latency_s * 1000),
            session_id=session_id,
            step=step,
            completion_chars=completion_chars,
        )
