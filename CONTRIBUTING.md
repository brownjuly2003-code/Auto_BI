# Contributing to Auto_BI

Auto_BI is a solo-maintained project, developed mostly through AI-assisted sessions with a strict quality gate. External contributions are welcome, but they go through the same gate the maintainer's own changes do — there is no lighter path for outside PRs.

## Before you start

For anything beyond a trivial fix, open an issue first (or comment on an existing one) describing the change and why it's needed. This avoids duplicate work and lets design-level questions get resolved before code is written — some changes require touching things covered by the design invariants below, which is a maintainer-level decision, not something to resolve mid-PR.

## Design invariants (do not break silently)

The project is built around a fixed set of invariants — see [CLAUDE.md](CLAUDE.md#инварианты-дизайна-не-нарушать-без-обновления-architecturemd) and [ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full list and rationale. The ones most likely to matter for a contribution:

- The LLM only ever produces a validated `DashboardSpec` (IR). Native BI payloads (Superset `form_data`, DataLens chart configs) are built exclusively by deterministic adapter code — never by model output.
- A spec is validated against `semantic/model.yaml` before any BI call; an unknown field is rejected (repair loop, max 3 attempts), never silently coerced.
- Generated SQL is `SELECT`-only (sqlglot-guarded), goes through `EXPLAIN` + a forced `LIMIT` before being trusted.
- Feasibility Advisor verdicts come only from deterministic findings (keys, stats, `EXPLAIN`) — the LLM narrates, it does not decide. Advisor is advisory-only and never blocks a build.
- Any change to prompts (`agent/*` prompt templates) must be accompanied by a full run of the eval suite (`auto_bi eval --suite golden` / `--suite advisor`) — see below.

If your change touches the IR schema, the `BIAdapter` protocol, or any of the numbered invariants in CLAUDE.md, open an issue proposing it before writing code.

## Dev setup

```bash
uv sync --group dev
uv run pre-commit install
```

Optional extras (`uv sync --extra anthropic`) are needed only if you're testing the Anthropic LLM provider path end-to-end.

## The gate

Every PR must pass, locally, before it's opened:

```bash
uv run ruff check .
uv run black --check auto_bi tests
uv run --with mypy --with types-PyYAML mypy auto_bi
uv run --with pytest-cov --with duckdb pytest -q --cov=auto_bi --cov-report=term-missing
uv run auto_bi eval --suite advisor --model-path semantic/model.yaml
```

This mirrors the `quality` job in `.github/workflows/ci.yml` — if it's green locally, that job will be green too. `pytest-cov` and `duckdb` are pulled in ephemerally (not in `uv.lock`) to keep the lockfile lean; same for `types-PyYAML`.

Two more CI jobs are **not** part of the local gate above and don't need Docker installed to contribute:
- `integration` — requires a live ClickHouse/Superset/DataLens stand; its tests (marked `@pytest.mark.integration`) are deselected by default (`pytest -m integration` to run them if you have a stand).
- `docker` — just builds the image (`docker build -t auto_bi .`) to catch Dockerfile drift; only relevant if your PR touches `Dockerfile`, `pyproject.toml`, or `uv.lock`.

If you touched anything under `agent/` that affects prompts or spec generation, also run the golden-eval suite against a live LLM before proposing the change is complete:

```bash
uv run auto_bi eval --suite golden --model-path semantic/model.yaml
```

This requires a working LLM provider (`ANTHROPIC_API_KEY` or a running GraceKelly instance — see [USER_GUIDE §6](docs/USER_GUIDE.md#6-конфигурация-переменные-окружения)). Mention in the PR description that you ran it and the pass rate.

## Commit and PR conventions

- Commit subjects follow `type(scope): summary` (`feat`, `fix`, `docs`, `chore`, `test`) — see `git log` for examples.
- Code, identifiers, and commit messages are in English; documentation and issue/PR discussion may be in Russian or English.
- Keep PRs scoped to one change; don't bundle an unrelated refactor with a feature or fix.
- Secrets never go in code, logs, or docs — `.env` is git-ignored for exactly this reason. If you accidentally commit one, tell the maintainer immediately (via a Security Advisory if it's a live credential) rather than just force-pushing a fix.

## Reporting bugs / requesting features

Use the issue templates under `.github/ISSUE_TEMPLATE/`. For security vulnerabilities, see [SECURITY.md](SECURITY.md) instead of opening a public issue.
