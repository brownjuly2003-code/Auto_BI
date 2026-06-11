"""GraceKelly client: POST /orchestrate + structured-output repair loop (max 3).

Contract (verified against GraceKelly source, 2026-06-11):
- request: prompt (<=40k, server-enforced), model, reasoning, decompose, session_id, metadata;
- response: status accepted|completed|failed|cancelled, output_text, failure_code/message;
- the endpoint is synchronous.
Every call is appended to logs/llm_calls.jsonl (prompt hash, latency, status — never the prompt).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from auto_bi.config import Settings
from auto_bi.llm.base import LLMError

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)

PROMPT_LIMIT = 40_000
MAX_REPAIRS = 3
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

REPAIR_PROMPT = """Твой предыдущий ответ не прошёл валидацию схемы.

Ошибка валидации:
{error}

Предыдущий ответ:
{previous}

Верни ИСПРАВЛЕННЫЙ JSON-объект по той же схеме. Только JSON в блоке ```json```, без пояснений."""


def extract_json(text: str) -> str:
    """JSON object from an LLM answer: fenced ```json``` block, else first balanced {...}."""
    fenced = _JSON_FENCE_RE.search(text)
    if fenced:
        return fenced.group(1)
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found in the answer")
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text[start:], start):
        if escape:
            escape = False
        elif ch == "\\":
            escape = in_string
        elif ch == '"':
            in_string = not in_string
        elif not in_string:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    raise ValueError("unbalanced JSON object in the answer")


class GraceKellyClient:
    def __init__(
        self,
        settings: Settings,
        http: httpx.Client | None = None,
        log_path: str | Path = "logs/llm_calls.jsonl",
    ) -> None:
        self._settings = settings
        self._http = http or httpx.Client(
            base_url=settings.gracekelly_url,
            timeout=httpx.Timeout(300.0, connect=10.0),
        )
        self._log_path = Path(log_path)

    def complete(
        self,
        prompt: str,
        schema: type[T],
        *,
        reasoning: bool = False,
        session_id: str | None = None,
    ) -> T:
        current = prompt
        last_error = ""
        for attempt in range(1 + MAX_REPAIRS):
            answer = self._call(current, reasoning=reasoning, session_id=session_id)
            try:
                return schema.model_validate_json(extract_json(answer))
            except (ValueError, ValidationError) as exc:
                last_error = str(exc)
                logger.warning("structured output invalid (attempt %d): %s", attempt + 1, exc)
                current = REPAIR_PROMPT.format(error=last_error, previous=answer[:8000])
        raise LLMError(f"structured output failed after {MAX_REPAIRS} repairs: {last_error}")

    def _call(self, prompt: str, *, reasoning: bool, session_id: str | None) -> str:
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
        try:
            response = self._http.post("/orchestrate", json=payload)
            response.raise_for_status()
            data = response.json()
            status = data.get("status", "unknown")
            if status != "completed" or not data.get("output_text"):
                raise LLMError(
                    f"GraceKelly task {status}: "
                    f"{data.get('failure_code')} {data.get('failure_message')}"
                )
            return data["output_text"]
        except httpx.HTTPError as exc:
            raise LLMError(f"GraceKelly transport error: {exc}") from exc
        finally:
            self._log(prompt, reasoning, status, time.monotonic() - started)

    def _log(self, prompt: str, reasoning: bool, status: str, latency_s: float) -> None:
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "model": self._settings.gracekelly_model,
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()[:16],
            "prompt_chars": len(prompt),
            "reasoning": reasoning,
            "status": status,
            "latency_ms": round(latency_s * 1000),
        }
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:  # logging must never kill the pipeline
            logger.exception("failed to write llm call log")
