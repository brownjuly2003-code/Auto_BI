# Golden fixtures: record, replay, fingerprint refresh

plan_sol step 9. Related: `docs/ARCHITECTURE.md` §3.14, `auto_bi/llm/fixture.py`.

## Guarantees (honest split)

| Mode | What it proves | Threshold | Needs provider? |
|---|---|---|---|
| **replay** | Scaffolding around LLM + fixture matches current **prompt contract** (template + IR schema + per-call prompt hash) | **100%** of cases | No |
| **live** | Current model still answers well enough on the suite | clear ≥ 80%; all ambiguous/infeasible flagged | Yes |
| **record** | Same as live + writes fixtures for later replay | same as live | Yes |
| **refresh-fingerprints** | Offline stamp of hashes/meta onto existing responses (no new model answers) | 100% | No |

Replay is **not** a live quality signal. A prompt edit that changes model behaviour but keeps the same rendered structure can still require a full re-record if answers diverge from what the suite asserts.

## Fixture format (v2)

One file per case: `tests/fixtures/golden_llm/<case_id>.json`.

```json
{
  "case_id": "g1_revenue_by_day",
  "format_version": 2,
  "template_version": "<16 hex of GROUNDING/SPEC_RULES/PROPOSE/PATCH templates>",
  "schema_version": "<16 hex of GroundingReport + DashboardSpec JSON schemas>",
  "provider": "gracekelly|anthropic|mistral|fixture-refresh|...",
  "model_id": "...",
  "calls": [
    {
      "step": "grounding",
      "schema": "GroundingReport",
      "prompt_sha256": "<sha256 of normalized prompt>",
      "response": { }
    }
  ]
}
```

Mismatch of `template_version`, `schema_version`, or any `prompt_sha256` → `FixtureStaleError` (CI red). Missing format v2 → stale (not silent pass).

## Reviewed re-record procedure

Do **not** bulk-overwrite fixtures without reading the diff.

### A. Prompt / template / schema text changed, recorded answers still valid

Use offline refresh (no tokens):

```bash
uv run auto_bi eval --suite golden --llm-mode refresh-fingerprints \
  --fixtures-dir tests/fixtures/golden_llm --model-path semantic/model.yaml
uv run auto_bi eval --suite golden --llm-mode refresh-fingerprints \
  --fixtures-dir tests/fixtures/golden_llm --model-path semantic/model_gp.yaml
uv run auto_bi eval --suite golden --llm-mode replay \
  --fixtures-dir tests/fixtures/golden_llm --model-path semantic/model.yaml
uv run auto_bi eval --suite golden --llm-mode replay \
  --fixtures-dir tests/fixtures/golden_llm --model-path semantic/model_gp.yaml
```

Review `git diff tests/fixtures/golden_llm` — only meta + `prompt_sha256` should change. Commit with a note that responses were **not** re-generated.

### B. Case expectations or agent call sequence changed, or answers are wrong

Full live re-record (costs tokens; ask for budget if large):

```bash
# subset first
uv run auto_bi eval --suite golden --llm-mode record --cases g1_revenue_by_day \
  --fixtures-dir tests/fixtures/golden_llm --model-path semantic/model.yaml
# then full suite when subset is good
uv run auto_bi eval --suite golden --llm-mode record \
  --fixtures-dir tests/fixtures/golden_llm --model-path semantic/model.yaml
```

Then replay offline until green. PR description must say provider + model_id from the fixture header.

### C. Live quality gate (optional CI)

Workflow `.github/workflows/eval-live-sentinel.yml` runs a **3-case** subset when secrets exist and prompt/LLM paths change. Budget env is set in the workflow. Without secrets the job skips (offline replay still gates PRs).

## CLI cheatsheet

```bash
auto_bi eval --suite advisor --model-path semantic/model.yaml
auto_bi eval --suite advisor --model-path semantic/model_gp.yaml
auto_bi eval --suite golden --llm-mode replay --model-path semantic/model.yaml
auto_bi eval --suite golden --llm-mode live --cases g1_revenue_by_day,a1_quantity
auto_bi eval --suite golden --llm-mode refresh-fingerprints --model-path semantic/model.yaml
```
