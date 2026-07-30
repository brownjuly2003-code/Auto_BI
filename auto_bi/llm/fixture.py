"""Offline record/replay for golden-eval LLM calls (S11 — golden-eval in CI, T-2).

Golden eval drives the real agent loop (grounding -> propose_spec -> optional patch)
through the `LLMClient` seam (`llm/base.py`). Running it live on every PR would need a
paid provider/quota available to CI and would be flaky (a live model's answer varies
run to run). `FixtureLLMClient` replays previously RECORDED responses instead, so the
deterministic scaffolding around the LLM call — spec validation, SQL generation,
advisor, adapters, and every assertion in `eval/runner.py` — is exercised on every PR
for free and deterministically.

plan_sol step 9 extends the contract so a green replay also proves the fixture is
still current for the **prompt / template / IR-schema** surface:

- each call stores `prompt_sha256` of the normalized outbound prompt;
- the fixture header stores `template_version`, `schema_version`, `provider`, `model_id`;
- mismatch raises `FixtureStaleError` (not a silent reuse of an outdated answer).

This still does NOT catch live model quality drift (the model is never called in
replay) — that is the live sentinel / manual live run. Replay's guarantee:
"these recorded answers still match the current prompt contract and the code around
the call still behaves correctly".

Fixture files: one JSON file per case, `<fixtures_dir>/<case_id>.json` (format v2)::

    {
      "case_id": "...",
      "format_version": 2,
      "template_version": "<16 hex of prompt templates>",
      "schema_version": "<16 hex of IR JSON schemas>",
      "provider": "anthropic|mistral|gracekelly|fixture-refresh|...",
      "model_id": "...",
      "calls": [
        {"step": "...", "schema": "...", "prompt_sha256": "...", "response": {...}}
      ]
    }

`eval/runner.py::run_golden_case` calls `begin_case(case_id)`/`end_case()` on the `llm`
object when they exist (duck-typed). Modes:

- **replay** — enforce fingerprints; offline; no network.
- **record** — live provider; write full v2 fixtures.
- **refresh-fingerprints** — offline: replay recorded *responses*, rewrite hashes/meta
  for the current prompt contract (no provider, reviewed procedure before commit).
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from auto_bi.llm.base import LLMClient, LLMError

T = TypeVar("T", bound=BaseModel)

# Bump only when the on-disk fixture envelope shape changes incompatibly.
FIXTURE_FORMAT_VERSION = 2


class FixtureMissingError(LLMError):
    """A case asked for an LLM call that the recorded fixture doesn't have an answer for.

    Means one of: the fixture was never recorded for this case, the case is new, or the
    code/prompt changed the number/order/schema of LLM calls a case makes since the
    fixture was last recorded. Fix: re-record with `--llm-mode record` against a live
    provider, then replay again.
    """


class FixtureStaleError(LLMError):
    """Fixture no longer matches the current prompt / template / IR schema contract.

    plan_sol step 9: a green replay must not accept answers recorded against a different
    prompt surface. Fix: `auto_bi eval --suite golden --llm-mode refresh-fingerprints`
    (offline stamp when only hashes/meta need update and responses still valid) or
    full `--llm-mode record` when the model must re-answer.
    """


def normalize_prompt(prompt: str) -> str:
    """Stable form for hashing: NFKC, strip, collapse internal whitespace runs."""
    text = unicodedata.normalize("NFKC", prompt or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def hash_prompt(prompt: str) -> str:
    return hashlib.sha256(normalize_prompt(prompt).encode("utf-8")).hexdigest()


def compute_template_version() -> str:
    """Short hash of static prompt templates that define the agent↔LLM contract.

    Changing GROUNDING/PROPOSE/PATCH text without re-stamping fixtures must fail replay.
    """
    from auto_bi.agent.grounding import GROUNDING_PROMPT
    from auto_bi.agent.propose import PATCH_SPEC_PROMPT, PROPOSE_SPEC_PROMPT, SPEC_RULES

    blob = "\n---\n".join((GROUNDING_PROMPT, SPEC_RULES, PROPOSE_SPEC_PROMPT, PATCH_SPEC_PROMPT))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def compute_schema_version() -> str:
    """Short hash of IR / grounding JSON schemas the LLM is asked to satisfy."""
    from auto_bi.agent.grounding import GroundingReport
    from auto_bi.ir.spec import DashboardSpec

    blob = json.dumps(
        {
            "GroundingReport": GroundingReport.model_json_schema(),
            "DashboardSpec": DashboardSpec.model_json_schema(),
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def current_contract_meta(
    *,
    provider: str = "unknown",
    model_id: str = "unknown",
) -> dict[str, Any]:
    return {
        "format_version": FIXTURE_FORMAT_VERSION,
        "template_version": compute_template_version(),
        "schema_version": compute_schema_version(),
        "provider": provider,
        "model_id": model_id,
    }


def _load_fixture(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"case_id": path.stem, "calls": [], "format_version": 0}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise FixtureMissingError(f"fixture {path.name}: root must be a JSON object")
    data.setdefault("calls", [])
    data.setdefault("case_id", path.stem)
    return data


def _write_fixture(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class FixtureLLMClient:
    """Replays recorded `complete()` responses in call order, one case at a time.

    When `refresh_fingerprints=True`, responses are still taken from disk but
    prompt hashes and contract meta are rewritten on `end_case()` (offline stamp).
    """

    def __init__(
        self,
        fixtures_dir: str | Path,
        *,
        enforce_fingerprint: bool = True,
        refresh_fingerprints: bool = False,
    ) -> None:
        self._dir = Path(fixtures_dir)
        self._enforce = enforce_fingerprint and not refresh_fingerprints
        self._refresh = refresh_fingerprints
        self._case_id: str | None = None
        self._meta: dict[str, Any] = {}
        self._calls: list[dict[str, Any]] = []
        self._pos = 0

    def begin_case(self, case_id: str) -> None:
        """Load `<fixtures_dir>/<case_id>.json` and reset the replay position for it."""
        self._case_id = case_id
        self._pos = 0
        path = self._dir / f"{case_id}.json"
        exists = path.exists()
        data = _load_fixture(path)
        self._calls = list(data.get("calls") or [])
        self._meta = {
            k: data.get(k)
            for k in (
                "format_version",
                "template_version",
                "schema_version",
                "provider",
                "model_id",
            )
        }
        # Missing file → first complete() raises FixtureMissingError (not stale).
        if self._enforce and exists:
            self._check_header_contract(case_id)

    def _check_header_contract(self, case_id: str) -> None:
        fmt = int(self._meta.get("format_version") or 0)
        if fmt < FIXTURE_FORMAT_VERSION:
            raise FixtureStaleError(
                f"case {case_id!r}: fixture format_version={fmt} < {FIXTURE_FORMAT_VERSION} "
                f"(missing prompt fingerprints) — run "
                f"`auto_bi eval --suite golden --llm-mode refresh-fingerprints` "
                f"or re-record with --llm-mode record"
            )
        want_t = compute_template_version()
        got_t = self._meta.get("template_version") or ""
        if got_t != want_t:
            raise FixtureStaleError(
                f"case {case_id!r}: template_version mismatch "
                f"(fixture={got_t!r}, current={want_t!r}) — prompt templates changed; "
                f"refresh-fingerprints or re-record"
            )
        want_s = compute_schema_version()
        got_s = self._meta.get("schema_version") or ""
        if got_s != want_s:
            raise FixtureStaleError(
                f"case {case_id!r}: schema_version mismatch "
                f"(fixture={got_s!r}, current={want_s!r}) — IR/grounding schema changed; "
                f"refresh-fingerprints or re-record"
            )

    def end_case(self) -> None:
        if not self._refresh or self._case_id is None:
            return
        meta = current_contract_meta(
            provider="fixture-refresh",
            model_id="offline",
        )
        payload = {
            "case_id": self._case_id,
            **meta,
            "calls": self._calls,
        }
        _write_fixture(self._dir / f"{self._case_id}.json", payload)

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
        prompt_digest = hash_prompt(prompt)
        recorded = call.get("prompt_sha256") or ""
        if self._refresh:
            call["prompt_sha256"] = prompt_digest
        elif self._enforce:
            if not recorded:
                raise FixtureStaleError(
                    f"case {self._case_id!r} call #{self._pos}: missing prompt_sha256 "
                    f"— refresh-fingerprints or re-record"
                )
            if recorded != prompt_digest:
                raise FixtureStaleError(
                    f"case {self._case_id!r} call #{self._pos} step={step!r}: "
                    f"prompt_sha256 mismatch (fixture={recorded[:12]}…, "
                    f"current={prompt_digest[:12]}…) — prompt contract changed; "
                    f"refresh-fingerprints or re-record"
                )
        return schema.model_validate(call["response"])


class RecordingLLMClient:
    """Wraps a real `LLMClient` and writes each case's call sequence to a fixture file.

    Use to (re)generate fixtures: run the golden suite once against a live provider
    with `--llm-mode record --fixtures-dir ...`, then replay offline from then on.
    """

    def __init__(
        self,
        inner: LLMClient,
        fixtures_dir: str | Path,
        *,
        provider: str = "unknown",
        model_id: str = "unknown",
    ) -> None:
        self._inner = inner
        self._dir = Path(fixtures_dir)
        self._provider = provider
        self._model_id = model_id
        self._case_id: str | None = None
        self._calls: list[dict[str, Any]] = []

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
            {
                "step": step,
                "schema": schema.__name__,
                "prompt_sha256": hash_prompt(prompt),
                "response": result.model_dump(mode="json"),
            }
        )
        return result

    def end_case(self) -> None:
        """Write the fixture file for the case just finished (called after every case,
        pass or fail — a case that failed mid-dialogue still recorded real calls)."""
        if self._case_id is None:
            return
        meta = current_contract_meta(provider=self._provider, model_id=self._model_id)
        payload = {
            "case_id": self._case_id,
            **meta,
            "calls": self._calls,
        }
        _write_fixture(self._dir / f"{self._case_id}.json", payload)
