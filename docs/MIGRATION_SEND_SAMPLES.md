# Migration: `AUTO_BI_SEND_SAMPLES` default → `false`

**When:** plan_sol step 2 / audit P0-2 (2026-07-22).
**Affects:** any deployment that relied on the previous default (`true`) without setting the variable.

## What changed

| | Before | After |
|---|---|---|
| Default `AUTO_BI_SEND_SAMPLES` | `true` | `false` |
| Clean install → external LLM | schema + top-N values | schema/metadata only |
| Classification on table/column | n/a | `public` / `internal` / `confidential` / `restricted` |
| Samples with opt-in | all columns with `top_values` | only `public` and `internal` (column overrides table; unset → `internal`) |
| Free-text in prompts | raw | sanitized + untrusted envelope |

Values still live in local `semantic/model.yaml` after introspect. The flag controls **outbound** prompt inclusion only.

## Do you need to act?

1. **Public demo / HF Space / production with external Anthropic (or any third-party LLM)**
   Keep the new default (`false` or omit the variable). No action required for safety.

2. **Internal deployment that intentionally used top-N for better grounding**
   Set explicitly in `.env` / secrets:

   ```bash
   AUTO_BI_SEND_SAMPLES=true
   ```

   Then mark sensitive marts so values still cannot leave:

   ```yaml
   # semantic/model.yaml
   tables:
   - name: hr.salaries
     classification: confidential   # never sent, even with SEND_SAMPLES=true
     columns:
     - name: city
       classification: public       # column overrides table when set
       top_values: [Москва, Казань]
   ```

3. **Local GraceKelly-only lab**
   Same as (2): opt in with `AUTO_BI_SEND_SAMPLES=true` if you want sample-assisted grounding. The old "default true because GraceKelly is local" rationale is obsolete — default provider is external Anthropic.

## Limits (always applied when samples are eligible)

- at most 10 values per column in the prompt;
- each value ≤ 64 characters after sanitization;
- control characters stripped; descriptions/synonyms truncated.

## Verification

```bash
# default path must not contain a planted marker from model top_values
uv run pytest tests/test_prompt_data.py tests/test_smoke.py -q
```

After opt-in, confirm only expected classes appear in captured prompts (see `tests/test_prompt_data.py`).
