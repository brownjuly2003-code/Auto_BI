"""Offline record/replay for golden-eval LLM calls (S11 — golden-eval in CI, T-2).

Golden eval drives the real agent loop (grounding -> propose_spec -> optional patch)
through the `LLMClient` seam (`llm/base.py`). Running it live on every PR would need a
paid provider/quota available to CI and would be flaky (a live model's answer varies
run to run). `FixtureLLMClient` replays previously RECORDED responses instead, so the
deterministic scaffolding around the LLM call — spec validation, SQL generation,
advisor, adapters, and every assertion in `eval/runner.py` — is exercised on every PR
for free and deterministically.

This does NOT catch live prompt/model regressions (a prompt edit that makes the model
answer differently for the same request looks identical to replay, which never asks a
live model anything) — that is still the job of an optional live run against a real
provider (a manual session or a future weekly job with a real key). Replay's contract
is narrower and cheaper: "the code around the LLM call still does the right thing with
answers shaped like these", not "the model still answers well".

Fixture files: one JSON file per case, `<fixtures_dir>/<case_id>.json`:
    {"case_id": "...", "calls": [{"step": "...", "schema": "...", "response": {...}}]}
`calls` mirrors the exact sequence of `LLMClient.complete()` invocations a case makes
(grounding, then propose_spec, then optionally patch_spec/narrate_advisor for an edit).
Replay checks `step`/`schema` on each call against what was recorded, so a case whose
call sequence changed since the fixture was written fails loudly instead of silently
replaying an answer to a different question.

`eval/runner.py::run_golden_case` calls `begin_case(case_id)`/`end_case()` on the `llm`
object when they exist (duck-typed — `GraceKellyClient`/`AnthropicClient` don't have
them and are unaffected); that is how a single shared `LLMClient` instance knows which
case's fixture file it is currently replaying/recording.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from auto_bi.llm.base import LLMClient, LLMError

T = TypeVar("T", bound=BaseModel)


class FixtureMissingError(LLMError):
    """A case asked for an LLM call that the recorded fixture doesn't have an answer for.

    Means one of: the fixture was never recorded for this case, the case is new, or the
    code/prompt changed the number/order/schema of LLM calls a case makes since the
    fixture was last recorded. Fix: re-record with `--llm-mode record` against a live
    provider, then replay again.
    """


class FixtureLLMClient:
    """Replays recorded `complete()` responses in call order, one case at a time."""

    def __init__(self, fixtures_dir: str | Path) -> None:
        self._dir = Path(fixtures_dir)
        self._case_id: str | None = None
        self._calls: list[dict] = []
        self._pos = 0

    def begin_case(self, case_id: str) -> None:
        """Load `<fixtures_dir>/<case_id>.json` and reset the replay position for it."""
        self._case_id = case_id
        self._pos = 0
        path = self._dir / f"{case_id}.json"
        self._calls = json.loads(path.read_text(encoding="utf-8"))["calls"] if path.exists() else []

    def end_case(self) -> None:
        """No-op: replay has nothing to persist. Symmetric with RecordingLLMClient."""

    def complete(
        self,
        prompt: str,
        schema: type[T],
        *,
        reasoning: bool = False,
        session_id: str | None = None,
        step: str = "",
    ) -> T:
        if self._case_id is None:
            raise FixtureMissingError(
                "FixtureLLMClient.complete() called before begin_case() — the eval "
                "runner must call begin_case(case_id) before running a case"
            )
        if self._pos >= len(self._calls):
            raise FixtureMissingError(
                f"case {self._case_id!r}: no recorded call #{self._pos + 1} "
                f"(step={step!r}, schema={schema.__name__!r}) — re-record fixtures "
                f"for this case (`auto_bi eval --suite golden --llm-mode record "
                f"--cases {self._case_id}`)"
            )
        call = self._calls[self._pos]
        self._pos += 1
        if call.get("step") != step or call.get("schema") != schema.__name__:
            raise FixtureMissingError(
                f"case {self._case_id!r} call #{self._pos}: fixture has "
                f"step={call.get('step')!r}/schema={call.get('schema')!r}, but the agent "
                f"asked for step={step!r}/schema={schema.__name__!r} — the call sequence "
                "changed since this fixture was recorded; re-record it"
            )
        return schema.model_validate(call["response"])


class RecordingLLMClient:
    """Wraps a real `LLMClient` and writes each case's call sequence to a fixture file.

    Use to (re)generate fixtures: run the golden suite once against a live provider
    with `--llm-mode record --fixtures-dir ...`, then replay offline from then on.
    """

    def __init__(self, inner: LLMClient, fixtures_dir: str | Path) -> None:
        self._inner = inner
        self._dir = Path(fixtures_dir)
        self._case_id: str | None = None
        self._calls: list[dict] = []

    def begin_case(self, case_id: str) -> None:
        self._case_id = case_id
        self._calls = []

    def complete(
        self,
        prompt: str,
        schema: type[T],
        *,
        reasoning: bool = False,
        session_id: str | None = None,
        step: str = "",
    ) -> T:
        result = self._inner.complete(
            prompt, schema, reasoning=reasoning, session_id=session_id, step=step
        )
        self._calls.append(
            {"step": step, "schema": schema.__name__, "response": result.model_dump(mode="json")}
        )
        return result

    def end_case(self) -> None:
        """Write the fixture file for the case just finished (called after every case,
        pass or fail — a case that failed mid-dialogue still recorded real calls)."""
        if self._case_id is None:
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{self._case_id}.json"
        payload = {"case_id": self._case_id, "calls": self._calls}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
