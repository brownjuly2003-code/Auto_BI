"""Application settings loaded from environment / .env (never hardcode secrets)."""

import os
from collections.abc import Mapping
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTO_BI_", env_file=".env", extra="ignore")

    # ClickHouse demo-DM / DWH (read-only role)
    ch_host: str = "localhost"
    ch_port: int = 8123
    ch_user: str = "auto_bi_ro"
    ch_password: str = ""
    ch_database: str = "dm"
    # ClickHouse host:port as seen FROM the BI server (e.g. "clickhouse:8123" inside
    # the compose network) when it differs from ch_host (e.g. SSH tunnel from the CLI side)
    ch_host_from_bi: str = ""
    ch_port_from_bi: int = 0

    # Greenplum / Greengage DWH (v2 engine, read-only role)
    gp_host: str = "localhost"
    gp_port: int = 5432
    gp_user: str = "auto_bi_ro"
    gp_password: str = ""
    gp_database: str = "postgres"
    gp_schema: str = "dm"

    # Superset
    superset_url: str = "http://localhost:8088"
    superset_user: str = "admin"
    superset_password: str = ""
    # Public base for the dashboard LINKS shown to the user, when it differs from the
    # API URL the adapter calls (P8 demo: the adapter talks to 127.0.0.1:8088 inside
    # the container, the viewer needs https://<space>.hf.space). None = superset_url.
    superset_public_url: str | None = None

    # DataLens (self-hosted OSS stand, v2 BI target)
    datalens_url: str = "http://localhost:8090"
    datalens_user: str = "admin"
    # No shipped default (audit C-8): an empty password fails loudly at signin
    # (DataLensClient.login) instead of silently trying a well-known credential.
    datalens_password: str = ""
    # Dedicated "Auto_BI" workbook on the self-hosted stand (Phase 4 F3): the agent's
    # delete-then-create idempotency only touches entries it owns, so writing to an
    # ISOLATED workbook keeps it from ever clobbering foreign entries (the OpenSource Demo
    # workbook z4wtz6tg5194o holds 84 demo charts). Stand-specific id, not a secret;
    # created via US POST /private/v2/workbooks. ARCHITECTURE §3.5.
    datalens_workbook_id: str = "ra7f79yirtumb"
    # ClickHouse host as the DataLens connection reaches it (host.docker.internal on the
    # self-hosted compose stand); port reuses ch_port.
    ch_host_from_datalens: str = "host.docker.internal"
    # C-6: adapters reuse the BI connection by NAME; on reuse the stored host/port(/db)
    # fingerprint is compared to the current DWH config. Mismatch warns by default;
    # true = refuse the build (stale connection would silently read the wrong DWH).
    bi_connection_strict: bool = False

    # LLM provider seam (llm/factory.py): "anthropic" (default — direct Anthropic Messages
    # API, works out of the box with just an API key) or "gracekelly" (local orchestration
    # service, documented opt-in — ARCHITECTURE §3.6).
    llm_provider: str = "anthropic"

    # GraceKelly LLM service
    gracekelly_url: str = "http://127.0.0.1:8011"
    gracekelly_model: str = "claude-sonnet-5"

    # Direct Anthropic API (used when llm_provider="anthropic"; SDK is an optional extra).
    # api_key blank -> the SDK reads the standard ANTHROPIC_API_KEY env var.
    anthropic_api_key: str = ""
    # Current Sonnet. Thinking is passed explicitly on every call (adaptive on reasoning
    # steps, disabled on mechanical ones — llm/anthropic.py), so the model's own
    # thinking-when-omitted default never applies here. Its tokenizer counts the same text
    # ~30% higher than claude-sonnet-4-6 did: re-check `anthropic_max_tokens` and the price
    # table below before assuming an old cost or budget baseline still holds.
    anthropic_model: str = "claude-sonnet-5"
    # Kept at 16k on purpose: `messages.create` here is non-streaming, and the SDK refuses
    # non-streaming requests whose estimated duration would outrun the HTTP timeout.
    anthropic_max_tokens: int = 16000

    send_samples: bool = True

    # Auth + RBAC (Phase 4) — OPT-IN. Off by default: the CLI, tests and the single-user
    # §2.1 flow stay unauthenticated. When enabled, the API requires a bearer token and
    # restricts each user to their allowed DWH schemas (auto_bi.auth).
    auth_enabled: bool = False
    # YAML of users: `users: [{username, password, role, schemas: [...]}]`. Plaintext
    # passwords (operator secret, keep out of VCS) are hashed before reaching the store.
    auth_users_file: str = ""
    # Bootstrap admin used only when auth is on AND no users file is given.
    admin_user: str = "admin"
    admin_password: str = ""
    auth_token_ttl_hours: int = 24
    # `secure` flag on the login cookie: None = auto (on unless serving on a loopback
    # host — see cli.py::_serve); set true/false to force it regardless of bind host.
    auth_cookie_secure: bool | None = None

    # LLM-call quota on session-creating endpoints (O-2) — OPT-IN, off by default: local
    # dev/tests/CLI are unaffected unless explicitly enabled ahead of a public demo. Gates
    # POST /api/v1/sessions and /sessions/{id}/reply (both trigger LLM calls) per-IP,
    # per rolling day, protecting the LLM budget from runaway usage.
    session_rate_enabled: bool = False
    session_rate_per_day: int = 100
    # Expensive non-LLM work quota (audit P0-3): auto start / approve / insights burn
    # DWH+BI+CPU even when no LLM is involved. OPT-IN; forced ON when demo_auto_only
    # (public demo profile) so an anonymous visitor cannot flood builds.
    work_rate_enabled: bool = False
    work_rate_per_day: int = 50
    # Hard cap on concurrent builds in this process (audit P0-3). Approve returns 503
    # with Retry-After when the semaphore is full — no unbounded thread-per-build fan-out.
    max_concurrent_builds: int = 2
    # C-7: caps on concurrent SSE event-stream consumers (each parks a worker thread).
    # Exceeding either returns 429 + Retry-After; 0 = unlimited. On by default — the
    # heartbeat already frees dead peers, this bounds the live ones.
    sse_max_streams: int = 20
    sse_max_streams_per_session: int = 3
    # LLM client-seam budget (audit P0-3 item 4) — OPT-IN, off by default (matches the
    # session/work quota convention above). The HTTP quotas gate REQUESTS but cannot see
    # provider round-trips; one request fans out into grounding + propose + advisor +
    # up to 3 repair retries. This budget is enforced inside the shared repair loop, so
    # the initial call AND every repair draw it down, per session and per actor / rolling
    # day. A limit of 0 (or 0.0) means that dimension is unlimited. Fails closed: a call
    # that would cross a limit raises BudgetExceeded before it is issued (llm/budget.py).
    llm_budget_enabled: bool = False
    # per conversation (session id), all-time
    llm_budget_session_max_calls: int = 0
    llm_budget_session_max_tokens: int = 0
    llm_budget_session_max_seconds: float = 0.0
    llm_budget_session_max_cost_usd: float = 0.0
    # per actor (session owner; one global bucket when auth is off) / rolling 24h
    llm_budget_day_max_calls: int = 0
    llm_budget_day_max_tokens: int = 0
    llm_budget_day_max_seconds: float = 0.0
    llm_budget_day_max_cost_usd: float = 0.0
    # cost price table (USD per 1000 tokens), "model:in/out,...". List prices as of
    # 2026-07-18; override for your provider contract. Used only when a *_max_cost_usd
    # limit is set — an unlisted model prices at 0, so add yours before relying on a cap.
    # Sonnet 5 carries a lower introductory rate through 2026-08-31; the table keeps the
    # standard rate so the guard errs toward over-estimating spend, not under.
    llm_budget_prices: str = (
        "claude-opus-4-8:0.005/0.025,"
        "claude-sonnet-5:0.003/0.015,"
        "claude-sonnet-4-6:0.003/0.015,"
        "claude-haiku-4-5:0.001/0.005"
    )
    # Public demo mode (P8): the deterministic auto-overview path becomes the ONLY
    # entry — text/fields sessions and word edits (both call the LLM) and enrichment
    # writes (mutate the shared model.yaml) return 403; the UI greys those tabs out.
    # The server is wired with DisabledLLM, so no provider/key is needed at all.
    demo_auto_only: bool = False
    # Fail-closed remote bind (audit P0-3): serving on a non-loopback host with auth
    # off and demo_auto_only off refuses to start unless this is true. Docker images
    # and trusted internal networks set it explicitly; never leave it true on the
    # public internet without auth or a demo profile.
    allow_insecure_remote: bool = False
    # F-2: behind a reverse proxy request.client is the PROXY address, so the per-IP
    # login limiter (B-3) and session quota (O-2) above would degrade into one shared
    # bucket. uvicorn rewrites request.client from X-Forwarded-For, but only when the
    # direct peer is a trusted proxy — 127.0.0.1 by default. When the proxy is NOT on
    # loopback (docker compose, k8s), set this to its address(es): comma-separated
    # IPs/CIDRs, or "*" if the app port is reachable ONLY from the proxy (compose
    # internal network). None = uvicorn's default (trust loopback only).
    forwarded_allow_ips: str | None = None

    # Ownership-based live-cleanup on rebuild (audit P0-2 criterion 4, wired 2026-07-18):
    # after a successful build the pipeline deletes THIS session's prior-revision BI
    # artifacts by native id (selection = Store.orphan_bi_artifacts: session/owner-scoped,
    # shared kinds excluded in SQL). ON by default — a rebuild replaces its previous
    # revision; this is the chosen product behavior, and a prune failure never fails the
    # build. Kill-switch for operators who want prior revisions kept (clean them later
    # with `auto_bi prune`).
    prune_on_rebuild: bool = True

    # SQLite store (sessions, specs, builds, llm_calls, dm_change_requests, users)
    store_path: str = "data/auto_bi.sqlite"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def unknown_env_settings(environ: Mapping[str, str] | None = None) -> list[str]:
    """AUTO_BI_* environment variables that no Settings field consumes (audit C-2).

    `extra="ignore"` silently drops typos — `AUTO_BI_AUTH_ENABLE=true` leaves auth OFF
    with no trace. `serve` reports every returned name as a warning so a misspelled
    security flag is visible in the log instead of silently inert. Compares against
    `Settings.model_fields` plus any explicit string validation_alias (none today;
    AliasChoices would need unpacking if ever introduced).
    """
    env = os.environ if environ is None else environ
    prefix = str(Settings.model_config.get("env_prefix", "")).upper()
    known: set[str] = set()
    for name, field in Settings.model_fields.items():
        known.add(f"{prefix}{name}".upper())
        if isinstance(field.validation_alias, str):
            known.add(field.validation_alias.upper())
    return sorted(k for k in env if k.upper().startswith(prefix) and k.upper() not in known)


def warn_unknown_env_settings(log, environ: Mapping[str, str] | None = None) -> list[str]:
    """Log a warning per unknown AUTO_BI_* variable; returns what was flagged."""
    unknown = unknown_env_settings(environ)
    for var in unknown:
        log.warning("unknown AUTO_BI_* environment variable (typo?): %s is ignored", var)
    return unknown
