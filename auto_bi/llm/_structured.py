"""Shared structured-output machinery for LLM clients (transport-agnostic).

Both GraceKellyClient and AnthropicClient turn text-in/text-out completions into
schema-validated objects via the SAME JSON-extraction + repair loop (invariant 1:
the LLM emits only DashboardSpec/etc. as JSON; we parse and validate it here, never
trusting native formats). Keeping this here means the two clients differ only in
transport, not in how structured output is coerced and logged.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ValidationError

from auto_bi.llm.base import LLMError

if TYPE_CHECKING:
    from auto_bi.store import Store

logger = logging.getLogger(__name__)

MAX_REPAIRS = 3
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

REPAIR_PROMPT = """Твой предыдущий ответ не прошёл валидацию схемы.

Ошибка валидации:
{error}

Предыдущий ответ:
{previous}

JSON Schema, которой должен соответствовать ответ:
{schema}

Верни ИСПРАВЛЕННЫЙ JSON-объект строго по этой схеме (все обязательные поля, без \
переименований). Только JSON в блоке ```json```, без пояснений."""


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


def complete_with_repair(
    call: Callable[[str], str],
    prompt: str,
    schema: type[BaseModel],
    *,
    on_attempt: Callable[[], None] | None = None,
) -> BaseModel:
    """Run `call(prompt)` and validate against `schema`; on failure, feed the error back.

    `call` is a transport-specific closure that sends a prompt and returns the model's
    raw text. The repair loop is identical for every client: up to MAX_REPAIRS retries,
    aborting early if a repair produces the same broken answer (no progress). Callers keep
    the precise return type via their own `type[T] -> T` signature (a `cast` at the seam).

    `on_attempt`, if given, runs immediately BEFORE each provider round-trip (the initial
    call and every repair). It is the LLM-budget hook (llm/budget.py): raising there —
    e.g. `BudgetExceeded` — aborts before the call is issued, so every attempt draws the
    budget down and no caller can bypass enforcement (it lives where `call` is invoked).
    """
    current = prompt
    last_error = ""
    previous_answer: str | None = None
    for attempt in range(1 + MAX_REPAIRS):
        if on_attempt is not None:
            on_attempt()
        answer = call(current)
        try:
            return schema.model_validate_json(extract_json(answer))
        except (ValueError, ValidationError) as exc:
            last_error = str(exc)
            logger.warning("structured output invalid (attempt %d): %s", attempt + 1, exc)
            if answer == previous_answer:  # repair produced the same broken output
                logger.warning("structured output unchanged after repair; aborting early")
                break
            previous_answer = answer
            current = REPAIR_PROMPT.format(
                error=last_error,
                previous=answer[:8000],
                schema=json.dumps(schema.model_json_schema(), ensure_ascii=False),
            )
    raise LLMError(f"structured output failed after {MAX_REPAIRS} repairs: {last_error}")


def append_llm_log(
    log_path: str | Path,
    store: Store | None,
    *,
    model: str,
    prompt: str,
    reasoning: bool,
    status: str,
    latency_ms: int,
    session_id: str | None,
    step: str,
    completion_chars: int,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> None:
    """Append one LLM-call record to the jsonl log and (if present) the durable Store.

    The prompt itself is NEVER logged — only its sha256 prefix and length (security §4).
    `input_tokens`/`output_tokens` are real usage from providers that report it (Anthropic);
    None where the provider returns no usage (GraceKelly) or the call failed before a response.
    Logging is best-effort: a failure here must never kill the pipeline.
    """
    prompt_sha256 = hashlib.sha256(prompt.encode()).hexdigest()[:16]
    prompt_chars = len(prompt)
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "model": model,
        "prompt_sha256": prompt_sha256,
        "prompt_chars": prompt_chars,
        "reasoning": reasoning,
        "status": status,
        "latency_ms": latency_ms,
        "step": step,
        "completion_chars": completion_chars,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    path = Path(log_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:  # logging must never kill the pipeline
        logger.exception("failed to write llm call log")
    if store is not None:
        try:
            store.log_llm_call(
                session_id=session_id,
                model=model,
                prompt_sha256=prompt_sha256,
                prompt_chars=prompt_chars,
                reasoning=reasoning,
                status=status,
                latency_ms=latency_ms,
                step=step,
                completion_chars=completion_chars,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        except Exception:  # logging must never kill the pipeline
            logger.exception("failed to write llm call to the store")
