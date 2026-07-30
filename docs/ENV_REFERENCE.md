# Environment reference (generated)

> **Generated file.** Do not edit by hand.
> Source of truth: `auto_bi.config.Settings`.
> Regenerate: `uv run python scripts/generate_env_reference.py`
> Check (CI): `uv run python scripts/generate_env_reference.py --check`

Curated operator tables (short lists + prose) live in
[USER_GUIDE §6](USER_GUIDE.md#6-конфигурация-переменные-окружения) and
[`.env.example`](../.env.example). This page is the **complete** inventory of
`AUTO_BI_*` keys consumed by Settings (`extra="ignore"` drops unknown
names; typos are warned at `serve` startup).

Deployment profile validation (`local` / `demo` / `production`) is described in
[DEPLOYMENT §2](DEPLOYMENT.md).

## Settings (`AUTO_BI_*`)

| Variable | Type | Default | Notes |
|---|---|---|---|
| `AUTO_BI_ADMIN_PASSWORD` | `str` | (empty) | Bootstrap admin password; empty leaves bootstrap credentials incomplete until set. |
| `AUTO_BI_ADMIN_USER` | `str` | `admin` | Bootstrap admin username used only when auth is on and no users file is given. |
| `AUTO_BI_ALLOW_INSECURE_REMOTE` | `bool` | false | Allow binding on a non-loopback host with auth and demo_auto_only both off; keep false on the public internet. |
| `AUTO_BI_ANTHROPIC_API_KEY` | `str` | (empty) | Anthropic API key for direct calls; empty lets the SDK fall back to ANTHROPIC_API_KEY. |
| `AUTO_BI_ANTHROPIC_MAX_TOKENS` | `int` | (set; not printed) | Max output tokens for non-streaming Anthropic calls; kept moderate to avoid SDK timeout refusals. |
| `AUTO_BI_ANTHROPIC_MODEL` | `str` | `claude-sonnet-5` | Anthropic model id used when llm_provider is anthropic. |
| `AUTO_BI_AUTH_COOKIE_SECURE` | `bool | null` | (unset / null) | Login cookie Secure flag; None auto-enables it off loopback, true/false forces the value. |
| `AUTO_BI_AUTH_ENABLED` | `bool` | false | Opt-in API auth and schema RBAC; false keeps CLI, tests, and single-user flows unauthenticated. |
| `AUTO_BI_AUTH_TOKEN_TTL_HOURS` | `int` | (set; not printed) | Bearer token lifetime in hours after login. |
| `AUTO_BI_AUTH_USERS_FILE` | `str` | (empty) | Path to YAML user list for auth; empty skips the file and may use bootstrap admin instead. |
| `AUTO_BI_BI_CONNECTION_STRICT` | `bool` | false | When true, refuse builds if a reused BI connection fingerprint mismatches the current DWH config. |
| `AUTO_BI_CH_DATABASE` | `str` | `dm` | ClickHouse database name that holds the demo DM tables. |
| `AUTO_BI_CH_HOST` | `str` | `localhost` | ClickHouse host the CLI and agent use for read-only DWH access. |
| `AUTO_BI_CH_HOST_FROM_BI` | `str` | (empty) | ClickHouse host as the BI server reaches it; empty reuses ch_host. |
| `AUTO_BI_CH_HOST_FROM_DATALENS` | `str` | `host.docker.internal` | ClickHouse host as DataLens reaches it; port reuses ch_port. |
| `AUTO_BI_CH_PASSWORD` | `str` | (empty) | ClickHouse password for the read-only role; empty means no password is sent. |
| `AUTO_BI_CH_PORT` | `int` | `8123` | ClickHouse HTTP port for the agent-side DWH connection. |
| `AUTO_BI_CH_PORT_FROM_BI` | `int` | `0` | ClickHouse port as the BI server reaches it; 0 reuses ch_port. |
| `AUTO_BI_CH_USER` | `str` | `auto_bi_ro` | ClickHouse username for the read-only DWH role. |
| `AUTO_BI_DATALENS_PASSWORD` | `str` | (empty) | DataLens login password; empty fails loudly at signin instead of using a well-known default. |
| `AUTO_BI_DATALENS_URL` | `str` | `http://localhost:8090` | Base URL of the self-hosted DataLens stand for adapter API calls. |
| `AUTO_BI_DATALENS_USER` | `str` | `admin` | DataLens login username used by the adapter. |
| `AUTO_BI_DATALENS_WORKBOOK_ID` | `str` | `ra7f79yirtumb` | Isolated DataLens workbook id owned by Auto_BI so rebuilds never clobber foreign entries. |
| `AUTO_BI_DEMO_AUTO_ONLY` | `bool` | false | Public demo mode: only auto-overview is allowed and LLM-backed paths return 403. |
| `AUTO_BI_FORWARDED_ALLOW_IPS` | `str | null` | (unset / null) | Trusted proxy IPs or CIDRs for X-Forwarded-For; None trusts loopback only, "*" trusts all direct peers. |
| `AUTO_BI_GP_DATABASE` | `str` | `postgres` | Greenplum or Greengage database name used for DM queries. |
| `AUTO_BI_GP_HOST` | `str` | `localhost` | Greenplum or Greengage host for the v2 read-only DWH path. |
| `AUTO_BI_GP_PASSWORD` | `str` | (empty) | Greenplum or Greengage password; empty means no password is sent. |
| `AUTO_BI_GP_PORT` | `int` | `5432` | Greenplum or Greengage TCP port for the v2 DWH connection. |
| `AUTO_BI_GP_SCHEMA` | `str` | `dm` | Greenplum or Greengage schema that holds DM tables and views. |
| `AUTO_BI_GP_USER` | `str` | `auto_bi_ro` | Greenplum or Greengage username for the read-only DWH role. |
| `AUTO_BI_GRACEKELLY_MODEL` | `str` | `claude-sonnet-5` | Model id requested from GraceKelly when llm_provider is gracekelly. |
| `AUTO_BI_GRACEKELLY_URL` | `str` | `http://127.0.0.1:8011` | Base URL of the local GraceKelly orchestration service. |
| `AUTO_BI_LLM_BUDGET_DAY_MAX_CALLS` | `int` | `0` | Max LLM calls per actor per rolling day when budget is on; 0 means unlimited. |
| `AUTO_BI_LLM_BUDGET_DAY_MAX_COST_USD` | `float` | `0.0` | Max estimated LLM spend in USD per actor per rolling day when budget is on; 0.0 means unlimited. |
| `AUTO_BI_LLM_BUDGET_DAY_MAX_SECONDS` | `float` | `0.0` | Max LLM wall-clock seconds per actor per rolling day when budget is on; 0.0 means unlimited. |
| `AUTO_BI_LLM_BUDGET_DAY_MAX_TOKENS` | `int` | `0` | Max LLM tokens per actor per rolling day when budget is on; 0 means unlimited. |
| `AUTO_BI_LLM_BUDGET_ENABLED` | `bool` | false | Opt-in LLM call budget enforced inside the repair loop; false leaves provider round-trips uncapped. |
| `AUTO_BI_LLM_BUDGET_PRICES` | `str` | `claude-opus-4-8:0.005/0.025,claude-sonnet-5:0.003/0.015,claude-sonnet-4-6:0.0…` | USD-per-1k-token price table as model:in/out pairs; unlisted models price at 0 until added. |
| `AUTO_BI_LLM_BUDGET_SESSION_MAX_CALLS` | `int` | `0` | Max LLM calls per session all-time when budget is on; 0 means unlimited. |
| `AUTO_BI_LLM_BUDGET_SESSION_MAX_COST_USD` | `float` | `0.0` | Max estimated LLM spend in USD per session all-time when budget is on; 0.0 means unlimited. |
| `AUTO_BI_LLM_BUDGET_SESSION_MAX_SECONDS` | `float` | `0.0` | Max LLM wall-clock seconds per session all-time when budget is on; 0.0 means unlimited. |
| `AUTO_BI_LLM_BUDGET_SESSION_MAX_TOKENS` | `int` | `0` | Max LLM tokens per session all-time when budget is on; 0 means unlimited. |
| `AUTO_BI_LLM_PROVIDER` | `str` | `anthropic` | LLM backend selector: "anthropic" or "mistral" for direct API access, or "gracekelly" for the local orchestration service. |
| `AUTO_BI_MAX_CONCURRENT_BUILDS` | `int` | `2` | Hard cap on concurrent builds in this process; excess approve calls return 503 with Retry-After. |
| `AUTO_BI_METRICS_ENABLED` | `bool` | false | Expose GET /api/v1/metrics; off by default because it reveals global spend and build counts. |
| `AUTO_BI_MISTRAL_API_KEY` | `str` | (empty) | Mistral API key for direct calls; empty lets the client fall back to MISTRAL_API_KEY. |
| `AUTO_BI_MISTRAL_MAX_TOKENS` | `int` | (set; not printed) | Maximum output tokens requested from direct Mistral chat completions. |
| `AUTO_BI_MISTRAL_MODEL` | `str` | `mistral-large-latest` | Mistral model id used when llm_provider is mistral. |
| `AUTO_BI_MISTRAL_URL` | `str` | `https://api.mistral.ai` | Mistral API base URL; the client appends /v1/chat/completions. |
| `AUTO_BI_PROFILE` | `str` | `local` | Serve-time validation profile: local, demo, or production; unknown values fall back to local. |
| `AUTO_BI_PRUNE_ON_REBUILD` | `bool` | true | After a successful rebuild, delete this session's prior-revision BI artifacts; false keeps them for later prune. |
| `AUTO_BI_REQUIRE_LLM_READY` | `bool` | false | When true, refuse to serve unless the configured LLM backend is ready. |
| `AUTO_BI_RETENTION_BI_ARTIFACTS_DAYS` | `int` | `30` | Age limit in days for non-live bi_artifacts rows when retention is on; 0 disables that table's sweep. |
| `AUTO_BI_RETENTION_ENABLED` | `bool` | false | Opt-in retention sweeps for telemetry tables; false never deletes operational history automatically. |
| `AUTO_BI_RETENTION_LLM_CALLS_DAYS` | `int` | `90` | Age limit in days for llm_calls rows when retention is on; 0 disables that table's sweep. |
| `AUTO_BI_RETENTION_SWEEP_HOURS` | `int` | `6` | Hours between retention sweeps after the startup sweep when retention is enabled. |
| `AUTO_BI_RETENTION_TRACE_EVENTS_DAYS` | `int` | `30` | Age limit in days for trace_events rows when retention is on; 0 disables that table's sweep. |
| `AUTO_BI_SEND_SAMPLES` | `bool` | false | When true, allow top-N DWH sample values to leave the process toward the external LLM. |
| `AUTO_BI_SESSION_RATE_ENABLED` | `bool` | false | Opt-in per-IP daily quota on session-creating LLM endpoints; false leaves local dev uncapped. |
| `AUTO_BI_SESSION_RATE_PER_DAY` | `int` | `100` | Max session create or reply requests per IP per rolling day when session_rate_enabled is true. |
| `AUTO_BI_SSE_MAX_STREAMS` | `int` | `20` | Max concurrent SSE event-stream consumers process-wide; 0 means unlimited, excess returns 429. |
| `AUTO_BI_SSE_MAX_STREAMS_PER_SESSION` | `int` | `3` | Max concurrent SSE streams per session; 0 means unlimited, excess returns 429. |
| `AUTO_BI_STORE_PATH` | `str` | `data/auto_bi.sqlite` | Filesystem path of the SQLite store for sessions, specs, builds, and related tables. |
| `AUTO_BI_SUPERSET_PASSWORD` | `str` | (empty) | Superset login password; empty fails authentication until set by the operator. |
| `AUTO_BI_SUPERSET_PUBLIC_URL` | `str | null` | (unset / null) | Public Superset base URL for user-facing dashboard links; None reuses superset_url. |
| `AUTO_BI_SUPERSET_URL` | `str` | `http://localhost:8088` | Base URL the Superset adapter calls for API operations. |
| `AUTO_BI_SUPERSET_USER` | `str` | `admin` | Superset login username used by the adapter. |
| `AUTO_BI_WORK_RATE_ENABLED` | `bool` | false | Opt-in daily quota for expensive non-LLM work; forced on under demo_auto_only. |
| `AUTO_BI_WORK_RATE_PER_DAY` | `int` | `50` | Max auto-start, approve, or insights jobs per actor per rolling day when work rate is active. |

## Companion variables (not Settings fields)

| Variable | Notes | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic SDK key when ``llm_provider=anthropic``. Also accepted as ``AUTO_BI_ANTHROPIC_API_KEY``. | (empty) |
| `MISTRAL_API_KEY` | Mistral API key when ``llm_provider=mistral``. Also accepted as ``AUTO_BI_MISTRAL_API_KEY``. | (empty) |

## Counts

- Settings fields / `AUTO_BI_*` keys: **70**
- Companion vars listed above: **2**

<!-- generate_env_reference:end -->
