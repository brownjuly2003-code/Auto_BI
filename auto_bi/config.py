"""Application settings loaded from environment / .env (never hardcode secrets)."""

import os
from collections.abc import Mapping
from functools import lru_cache
from logging import Logger

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTO_BI_", env_file=".env", extra="ignore")

    # Deployment profile (plan_sol step 4 / audit P0-3, P2-4). Selects serve-time
    # validation strictness: local (default, developer-friendly), demo (public Space),
    # production (fail closed on weak secrets / missing auth / unbounded resources).
    # Env: AUTO_BI_PROFILE. Unknown values fall back to local with a warning in serve.
    profile: str = Field(
        default="local",
        description=(
            "Serve-time validation profile: local, demo, or production; unknown values fall back to"
            " local."
        ),
    )

    # ClickHouse demo-DM / DWH (read-only role)
    ch_host: str = Field(
        default="localhost",
        description="ClickHouse host the CLI and agent use for read-only DWH access.",
    )
    ch_port: int = Field(
        default=8123,
        description="ClickHouse HTTP port for the agent-side DWH connection.",
    )
    ch_user: str = Field(
        default="auto_bi_ro",
        description="ClickHouse username for the read-only DWH role.",
    )
    ch_password: str = Field(
        default="",
        description="ClickHouse password for the read-only role; empty means no password is sent.",
    )
    ch_database: str = Field(
        default="dm",
        description="ClickHouse database name that holds the demo DM tables.",
    )
    # ClickHouse host:port as seen FROM the BI server (e.g. "clickhouse:8123" inside
    # the compose network) when it differs from ch_host (e.g. SSH tunnel from the CLI side)
    ch_host_from_bi: str = Field(
        default="",
        description="ClickHouse host as the BI server reaches it; empty reuses ch_host.",
    )
    ch_port_from_bi: int = Field(
        default=0,
        description="ClickHouse port as the BI server reaches it; 0 reuses ch_port.",
    )

    # Greenplum / Greengage DWH (v2 engine, read-only role)
    gp_host: str = Field(
        default="localhost",
        description="Greenplum or Greengage host for the v2 read-only DWH path.",
    )
    gp_port: int = Field(
        default=5432,
        description="Greenplum or Greengage TCP port for the v2 DWH connection.",
    )
    gp_user: str = Field(
        default="auto_bi_ro",
        description="Greenplum or Greengage username for the read-only DWH role.",
    )
    gp_password: str = Field(
        default="",
        description="Greenplum or Greengage password; empty means no password is sent.",
    )
    gp_database: str = Field(
        default="postgres",
        description="Greenplum or Greengage database name used for DM queries.",
    )
    gp_schema: str = Field(
        default="dm",
        description="Greenplum or Greengage schema that holds DM tables and views.",
    )

    # Superset
    superset_url: str = Field(
        default="http://localhost:8088",
        description="Base URL the Superset adapter calls for API operations.",
    )
    superset_user: str = Field(
        default="admin",
        description="Superset login username used by the adapter.",
    )
    superset_password: str = Field(
        default="",
        description=(
            "Superset login password; empty fails authentication until set by the operator."
        ),
    )
    # Public base for the dashboard LINKS shown to the user, when it differs from the
    # API URL the adapter calls (P8 demo: the adapter talks to 127.0.0.1:8088 inside
    # the container, the viewer needs https://<space>.hf.space). None = superset_url.
    superset_public_url: str | None = Field(
        default=None,
        description=(
            "Public Superset base URL for user-facing dashboard links; None reuses superset_url."
        ),
    )

    # DataLens (self-hosted OSS stand, v2 BI target)
    datalens_url: str = Field(
        default="http://localhost:8090",
        description="Base URL of the self-hosted DataLens stand for adapter API calls.",
    )
    datalens_user: str = Field(
        default="admin",
        description="DataLens login username used by the adapter.",
    )
    # No shipped default (audit C-8): an empty password fails loudly at signin
    # (DataLensClient.login) instead of silently trying a well-known credential.
    datalens_password: str = Field(
        default="",
        description=(
            "DataLens login password; empty fails loudly at signin instead of using a well-known"
            " default."
        ),
    )
    # Dedicated "Auto_BI" workbook on the self-hosted stand (Phase 4 F3): the agent's
    # delete-then-create idempotency only touches entries it owns, so writing to an
    # ISOLATED workbook keeps it from ever clobbering foreign entries (the OpenSource Demo
    # workbook z4wtz6tg5194o holds 84 demo charts). Stand-specific id, not a secret;
    # created via US POST /private/v2/workbooks. ARCHITECTURE §3.5.
    datalens_workbook_id: str = Field(
        default="ra7f79yirtumb",
        description=(
            "Isolated DataLens workbook id owned by Auto_BI so rebuilds never clobber foreign"
            " entries."
        ),
    )
    # ClickHouse host as the DataLens connection reaches it (host.docker.internal on the
    # self-hosted compose stand); port reuses ch_port.
    ch_host_from_datalens: str = Field(
        default="host.docker.internal",
        description="ClickHouse host as DataLens reaches it; port reuses ch_port.",
    )
    # C-6: adapters reuse the BI connection by NAME; on reuse the stored host/port(/db)
    # fingerprint is compared to the current DWH config. Mismatch warns by default;
    # true = refuse the build (stale connection would silently read the wrong DWH).
    bi_connection_strict: bool = Field(
        default=False,
        description=(
            "When true, refuse builds if a reused BI connection fingerprint mismatches the current"
            " DWH config."
        ),
    )

    # LLM provider seam (llm/factory.py): "anthropic" (default — direct Anthropic Messages
    # API, works out of the box with just an API key) or "gracekelly" (local orchestration
    # service, documented opt-in — ARCHITECTURE §3.6).
    llm_provider: str = Field(
        default="anthropic",
        description=(
            'LLM backend selector: "anthropic" for direct API or "gracekelly" for the local'
            " orchestration service."
        ),
    )

    # GraceKelly LLM service
    gracekelly_url: str = Field(
        default="http://127.0.0.1:8011",
        description="Base URL of the local GraceKelly orchestration service.",
    )
    gracekelly_model: str = Field(
        default="claude-sonnet-5",
        description="Model id requested from GraceKelly when llm_provider is gracekelly.",
    )

    # Direct Anthropic API (used when llm_provider="anthropic"; SDK is an optional extra).
    # api_key blank -> the SDK reads the standard ANTHROPIC_API_KEY env var.
    anthropic_api_key: str = Field(
        default="",
        description=(
            "Anthropic API key for direct calls; empty lets the SDK fall back to ANTHROPIC_API_KEY."
        ),
    )
    # Current Sonnet. Thinking is passed explicitly on every call (adaptive on reasoning
    # steps, disabled on mechanical ones — llm/anthropic.py), so the model's own
    # thinking-when-omitted default never applies here. Its tokenizer counts the same text
    # ~30% higher than claude-sonnet-4-6 did: re-check `anthropic_max_tokens` and the price
    # table below before assuming an old cost or budget baseline still holds.
    anthropic_model: str = Field(
        default="claude-sonnet-5",
        description="Anthropic model id used when llm_provider is anthropic.",
    )
    # Kept at 16k on purpose: `messages.create` here is non-streaming, and the SDK refuses
    # non-streaming requests whose estimated duration would outrun the HTTP timeout.
    anthropic_max_tokens: int = Field(
        default=16000,
        description=(
            "Max output tokens for non-streaming Anthropic calls; kept moderate to avoid SDK"
            " timeout refusals."
        ),
    )

    # plan_sol step 2 / audit P0-2: DWH values (top-N) leave the process only on
    # explicit opt-in. Default false — clean install never sends samples to an
    # external LLM. Set true only for public/internal classes after classification.
    send_samples: bool = Field(
        default=False,
        description=(
            "When true, allow top-N DWH sample values to leave the process toward the external LLM."
        ),
    )

    # Auth + RBAC (Phase 4) — OPT-IN. Off by default: the CLI, tests and the single-user
    # §2.1 flow stay unauthenticated. When enabled, the API requires a bearer token and
    # restricts each user to their allowed DWH schemas (auto_bi.auth).
    auth_enabled: bool = Field(
        default=False,
        description=(
            "Opt-in API auth and schema RBAC; false keeps CLI, tests, and single-user flows"
            " unauthenticated."
        ),
    )
    # YAML of users: `users: [{username, password, role, schemas: [...]}]`. Plaintext
    # passwords (operator secret, keep out of VCS) are hashed before reaching the store.
    auth_users_file: str = Field(
        default="",
        description=(
            "Path to YAML user list for auth; empty skips the file and may use bootstrap admin"
            " instead."
        ),
    )
    # Bootstrap admin used only when auth is on AND no users file is given.
    admin_user: str = Field(
        default="admin",
        description=(
            "Bootstrap admin username used only when auth is on and no users file is given."
        ),
    )
    admin_password: str = Field(
        default="",
        description=(
            "Bootstrap admin password; empty leaves bootstrap credentials incomplete until set."
        ),
    )
    auth_token_ttl_hours: int = Field(
        default=24,
        description="Bearer token lifetime in hours after login.",
    )
    # `secure` flag on the login cookie: None = auto (on unless serving on a loopback
    # host — see cli.py::_serve); set true/false to force it regardless of bind host.
    auth_cookie_secure: bool | None = Field(
        default=None,
        description=(
            "Login cookie Secure flag; None auto-enables it off loopback, true/false forces the"
            " value."
        ),
    )

    # LLM-call quota on session-creating endpoints (O-2) — OPT-IN, off by default: local
    # dev/tests/CLI are unaffected unless explicitly enabled ahead of a public demo. Gates
    # POST /api/v1/sessions and /sessions/{id}/reply (both trigger LLM calls) per-IP,
    # per rolling day, protecting the LLM budget from runaway usage.
    session_rate_enabled: bool = Field(
        default=False,
        description=(
            "Opt-in per-IP daily quota on session-creating LLM endpoints; false leaves local dev"
            " uncapped."
        ),
    )
    session_rate_per_day: int = Field(
        default=100,
        description=(
            "Max session create or reply requests per IP per rolling day when session_rate_enabled"
            " is true."
        ),
    )
    # Expensive non-LLM work quota (audit P0-3): auto start / approve / insights burn
    # DWH+BI+CPU even when no LLM is involved. OPT-IN; forced ON when demo_auto_only
    # (public demo profile) so an anonymous visitor cannot flood builds.
    work_rate_enabled: bool = Field(
        default=False,
        description=(
            "Opt-in daily quota for expensive non-LLM work; forced on under demo_auto_only."
        ),
    )
    work_rate_per_day: int = Field(
        default=50,
        description=(
            "Max auto-start, approve, or insights jobs per actor per rolling day when work rate is"
            " active."
        ),
    )
    # Hard cap on concurrent builds in this process (audit P0-3). Approve returns 503
    # with Retry-After when the semaphore is full — no unbounded thread-per-build fan-out.
    max_concurrent_builds: int = Field(
        default=2,
        description=(
            "Hard cap on concurrent builds in this process; excess approve calls return 503 with"
            " Retry-After."
        ),
    )
    # C-7: caps on concurrent SSE event-stream consumers (each parks a worker thread).
    # Exceeding either returns 429 + Retry-After; 0 = unlimited. On by default — the
    # heartbeat already frees dead peers, this bounds the live ones.
    sse_max_streams: int = Field(
        default=20,
        description=(
            "Max concurrent SSE event-stream consumers process-wide; 0 means unlimited, excess"
            " returns 429."
        ),
    )
    sse_max_streams_per_session: int = Field(
        default=3,
        description=(
            "Max concurrent SSE streams per session; 0 means unlimited, excess returns 429."
        ),
    )
    # LLM client-seam budget (audit P0-3 item 4) — OPT-IN, off by default (matches the
    # session/work quota convention above). The HTTP quotas gate REQUESTS but cannot see
    # provider round-trips; one request fans out into grounding + propose + advisor +
    # up to 3 repair retries. This budget is enforced inside the shared repair loop, so
    # the initial call AND every repair draw it down, per session and per actor / rolling
    # day. A limit of 0 (or 0.0) means that dimension is unlimited. Fails closed: a call
    # that would cross a limit raises BudgetExceeded before it is issued (llm/budget.py).
    llm_budget_enabled: bool = Field(
        default=False,
        description=(
            "Opt-in LLM call budget enforced inside the repair loop; false leaves provider"
            " round-trips uncapped."
        ),
    )
    # per conversation (session id), all-time
    llm_budget_session_max_calls: int = Field(
        default=0,
        description="Max LLM calls per session all-time when budget is on; 0 means unlimited.",
    )
    llm_budget_session_max_tokens: int = Field(
        default=0,
        description="Max LLM tokens per session all-time when budget is on; 0 means unlimited.",
    )
    llm_budget_session_max_seconds: float = Field(
        default=0.0,
        description=(
            "Max LLM wall-clock seconds per session all-time when budget is on; 0.0 means"
            " unlimited."
        ),
    )
    llm_budget_session_max_cost_usd: float = Field(
        default=0.0,
        description=(
            "Max estimated LLM spend in USD per session all-time when budget is on; 0.0 means"
            " unlimited."
        ),
    )
    # per actor (session owner; one global bucket when auth is off) / rolling 24h
    llm_budget_day_max_calls: int = Field(
        default=0,
        description="Max LLM calls per actor per rolling day when budget is on; 0 means unlimited.",
    )
    llm_budget_day_max_tokens: int = Field(
        default=0,
        description=(
            "Max LLM tokens per actor per rolling day when budget is on; 0 means unlimited."
        ),
    )
    llm_budget_day_max_seconds: float = Field(
        default=0.0,
        description=(
            "Max LLM wall-clock seconds per actor per rolling day when budget is on; 0.0 means"
            " unlimited."
        ),
    )
    llm_budget_day_max_cost_usd: float = Field(
        default=0.0,
        description=(
            "Max estimated LLM spend in USD per actor per rolling day when budget is on; 0.0 means"
            " unlimited."
        ),
    )
    # cost price table (USD per 1000 tokens), "model:in/out,...". List prices as of
    # 2026-07-18; override for your provider contract. Used only when a *_max_cost_usd
    # limit is set — an unlisted model prices at 0, so add yours before relying on a cap.
    # Sonnet 5 carries a lower introductory rate through 2026-08-31; the table keeps the
    # standard rate so the guard errs toward over-estimating spend, not under.
    llm_budget_prices: str = Field(
        default=(
            "claude-opus-4-8:0.005/0.025,"
            "claude-sonnet-5:0.003/0.015,"
            "claude-sonnet-4-6:0.003/0.015,"
            "claude-haiku-4-5:0.001/0.005"
        ),
        description=(
            "USD-per-1k-token price table as model:in/out pairs; unlisted models price at 0 until"
            " added."
        ),
    )
    # Public demo mode (P8): the deterministic auto-overview path becomes the ONLY
    # entry — text/fields sessions and word edits (both call the LLM) and enrichment
    # writes (mutate the shared model.yaml) return 403; the UI greys those tabs out.
    # The server is wired with DisabledLLM, so no provider/key is needed at all.
    demo_auto_only: bool = Field(
        default=False,
        description=(
            "Public demo mode: only auto-overview is allowed and LLM-backed paths return 403."
        ),
    )
    # Text-enabled public deploy (plan_sol step 1): refuse to serve when the LLM is
    # not ready. OPT-IN for local dev (default false — 502 on text paths is fine).
    # HF demo start-autobi.sh forces this true whenever DEMO_AUTO_ONLY=false so a
    # Space cannot advertise text/fields while GraceKelly is down.
    require_llm_ready: bool = Field(
        default=False,
        description="When true, refuse to serve unless the configured LLM backend is ready.",
    )
    # Fail-closed remote bind (audit P0-3): serving on a non-loopback host with auth
    # off and demo_auto_only off refuses to start unless this is true. Docker images
    # and trusted internal networks set it explicitly; never leave it true on the
    # public internet without auth or a demo profile.
    allow_insecure_remote: bool = Field(
        default=False,
        description=(
            "Allow binding on a non-loopback host with auth and demo_auto_only both off; keep false"
            " on the public internet."
        ),
    )
    # F-2: behind a reverse proxy request.client is the PROXY address, so the per-IP
    # login limiter (B-3) and session quota (O-2) above would degrade into one shared
    # bucket. uvicorn rewrites request.client from X-Forwarded-For, but only when the
    # direct peer is a trusted proxy — 127.0.0.1 by default. When the proxy is NOT on
    # loopback (docker compose, k8s), set this to its address(es): comma-separated
    # IPs/CIDRs, or "*" if the app port is reachable ONLY from the proxy (compose
    # internal network). None = uvicorn's default (trust loopback only).
    forwarded_allow_ips: str | None = Field(
        default=None,
        description=(
            'Trusted proxy IPs or CIDRs for X-Forwarded-For; None trusts loopback only, "*" trusts'
            " all direct peers."
        ),
    )

    # Ownership-based live-cleanup on rebuild (audit P0-2 criterion 4, wired 2026-07-18):
    # after a successful build the pipeline deletes THIS session's prior-revision BI
    # artifacts by native id (selection = Store.orphan_bi_artifacts: session/owner-scoped,
    # shared kinds excluded in SQL). ON by default — a rebuild replaces its previous
    # revision; this is the chosen product behavior, and a prune failure never fails the
    # build. Kill-switch for operators who want prior revisions kept (clean them later
    # with `auto_bi prune`).
    prune_on_rebuild: bool = Field(
        default=True,
        description=(
            "After a successful rebuild, delete this session's prior-revision BI artifacts; false"
            " keeps them for later prune."
        ),
    )

    # SQLite store (sessions, specs, builds, llm_calls, dm_change_requests, users)
    store_path: str = Field(
        default="data/auto_bi.sqlite",
        description=(
            "Filesystem path of the SQLite store for sessions, specs, builds, and related tables."
        ),
    )

    # Store retention (audit D-3). The telemetry tables grow without bound: every LLM
    # round-trip appends to `llm_calls`, every pipeline step to `trace_events`, and every
    # rebuild leaves its prior revision's rows in `bi_artifacts`. Only `auth_tokens` was
    # ever swept. OFF by default and opt-in like the other limiters (auth/quotas/budget):
    # deleting operational history is irreversible, so an operator turns it on knowingly.
    # A sweep runs on `serve` startup and every `retention_sweep_hours` after.
    # SESSION-OWNED rows (sessions/messages/specs/builds) are NEVER touched — they are the
    # user's own work, not telemetry; clean those with `auto_bi prune` / by hand.
    retention_enabled: bool = Field(
        default=False,
        description=(
            "Opt-in retention sweeps for telemetry tables; false never deletes operational history"
            " automatically."
        ),
    )
    # per-table age limits in days; 0 disables that table's sweep while leaving the others on
    retention_llm_calls_days: int = Field(
        default=90,
        description=(
            "Age limit in days for llm_calls rows when retention is on; 0 disables that table's"
            " sweep."
        ),
    )
    retention_trace_events_days: int = Field(
        default=30,
        description=(
            "Age limit in days for trace_events rows when retention is on; 0 disables that table's"
            " sweep."
        ),
    )
    # only NON-live artifact rows (superseded/deleted) age out — a live row is the ledger
    # that live-cleanup selects on, and dropping it would orphan a real BI entity
    retention_bi_artifacts_days: int = Field(
        default=30,
        description=(
            "Age limit in days for non-live bi_artifacts rows when retention is on; 0 disables that"
            " table's sweep."
        ),
    )
    retention_sweep_hours: int = Field(
        default=6,
        description=(
            "Hours between retention sweeps after the startup sweep when retention is enabled."
        ),
    )

    # Prometheus metrics endpoint (audit D-3): GET /api/v1/metrics in the text exposition
    # format. Off by default — it aggregates GLOBAL spend and build counts, so with auth on
    # it is admin-only, and with auth off it would expose those numbers to anyone who can
    # reach the port.
    metrics_enabled: bool = Field(
        default=False,
        description=(
            "Expose GET /api/v1/metrics; off by default because it reveals global spend and build"
            " counts."
        ),
    )


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


def warn_unknown_env_settings(log: Logger, environ: Mapping[str, str] | None = None) -> list[str]:
    """Log a warning per unknown AUTO_BI_* variable; returns what was flagged."""
    unknown = unknown_env_settings(environ)
    for var in unknown:
        log.warning("unknown AUTO_BI_* environment variable (typo?): %s is ignored", var)
    return unknown
