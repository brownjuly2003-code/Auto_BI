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
| `AUTO_BI_ADMIN_PASSWORD` | `str` | (empty) | Settings field ``admin_password`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_ADMIN_USER` | `str` | `admin` | Settings field ``admin_user`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_ALLOW_INSECURE_REMOTE` | `bool` | false | Settings field ``allow_insecure_remote`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_ANTHROPIC_API_KEY` | `str` | (empty) | Settings field ``anthropic_api_key`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_ANTHROPIC_MAX_TOKENS` | `int` | (set; not printed) | Settings field ``anthropic_max_tokens`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_ANTHROPIC_MODEL` | `str` | `claude-sonnet-5` | Settings field ``anthropic_model`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_AUTH_COOKIE_SECURE` | `bool | null` | (unset / null) | Settings field ``auth_cookie_secure`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_AUTH_ENABLED` | `bool` | false | Settings field ``auth_enabled`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_AUTH_TOKEN_TTL_HOURS` | `int` | (set; not printed) | Settings field ``auth_token_ttl_hours`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_AUTH_USERS_FILE` | `str` | (empty) | Settings field ``auth_users_file`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_BI_CONNECTION_STRICT` | `bool` | false | Settings field ``bi_connection_strict`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_CH_DATABASE` | `str` | `dm` | Settings field ``ch_database`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_CH_HOST` | `str` | `localhost` | Settings field ``ch_host`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_CH_HOST_FROM_BI` | `str` | (empty) | Settings field ``ch_host_from_bi`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_CH_HOST_FROM_DATALENS` | `str` | `host.docker.internal` | Settings field ``ch_host_from_datalens`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_CH_PASSWORD` | `str` | (empty) | Settings field ``ch_password`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_CH_PORT` | `int` | `8123` | Settings field ``ch_port`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_CH_PORT_FROM_BI` | `int` | `0` | Settings field ``ch_port_from_bi`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_CH_USER` | `str` | `auto_bi_ro` | Settings field ``ch_user`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_DATALENS_PASSWORD` | `str` | (empty) | Settings field ``datalens_password`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_DATALENS_URL` | `str` | `http://localhost:8090` | Settings field ``datalens_url`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_DATALENS_USER` | `str` | `admin` | Settings field ``datalens_user`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_DATALENS_WORKBOOK_ID` | `str` | `ra7f79yirtumb` | Settings field ``datalens_workbook_id`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_DEMO_AUTO_ONLY` | `bool` | false | Settings field ``demo_auto_only`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_FORWARDED_ALLOW_IPS` | `str | null` | (unset / null) | Settings field ``forwarded_allow_ips`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_GP_DATABASE` | `str` | `postgres` | Settings field ``gp_database`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_GP_HOST` | `str` | `localhost` | Settings field ``gp_host`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_GP_PASSWORD` | `str` | (empty) | Settings field ``gp_password`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_GP_PORT` | `int` | `5432` | Settings field ``gp_port`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_GP_SCHEMA` | `str` | `dm` | Settings field ``gp_schema`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_GP_USER` | `str` | `auto_bi_ro` | Settings field ``gp_user`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_GRACEKELLY_MODEL` | `str` | `claude-sonnet-5` | Settings field ``gracekelly_model`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_GRACEKELLY_URL` | `str` | `http://127.0.0.1:8011` | Settings field ``gracekelly_url`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_LLM_BUDGET_DAY_MAX_CALLS` | `int` | `0` | Settings field ``llm_budget_day_max_calls`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_LLM_BUDGET_DAY_MAX_COST_USD` | `float` | `0.0` | Settings field ``llm_budget_day_max_cost_usd`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_LLM_BUDGET_DAY_MAX_SECONDS` | `float` | `0.0` | Settings field ``llm_budget_day_max_seconds`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_LLM_BUDGET_DAY_MAX_TOKENS` | `int` | `0` | Settings field ``llm_budget_day_max_tokens`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_LLM_BUDGET_ENABLED` | `bool` | false | Settings field ``llm_budget_enabled`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_LLM_BUDGET_PRICES` | `str` | `claude-opus-4-8:0.005/0.025,claude-sonnet-5:0.003/0.015,claude-sonnet-4-6:0.0…` | Settings field ``llm_budget_prices`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_LLM_BUDGET_SESSION_MAX_CALLS` | `int` | `0` | Settings field ``llm_budget_session_max_calls`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_LLM_BUDGET_SESSION_MAX_COST_USD` | `float` | `0.0` | Settings field ``llm_budget_session_max_cost_usd`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_LLM_BUDGET_SESSION_MAX_SECONDS` | `float` | `0.0` | Settings field ``llm_budget_session_max_seconds`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_LLM_BUDGET_SESSION_MAX_TOKENS` | `int` | `0` | Settings field ``llm_budget_session_max_tokens`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_LLM_PROVIDER` | `str` | `anthropic` | Settings field ``llm_provider`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_MAX_CONCURRENT_BUILDS` | `int` | `2` | Settings field ``max_concurrent_builds`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_METRICS_ENABLED` | `bool` | false | Settings field ``metrics_enabled`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_PROFILE` | `str` | `local` | Settings field ``profile`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_PRUNE_ON_REBUILD` | `bool` | true | Settings field ``prune_on_rebuild`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_REQUIRE_LLM_READY` | `bool` | false | Settings field ``require_llm_ready`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_RETENTION_BI_ARTIFACTS_DAYS` | `int` | `30` | Settings field ``retention_bi_artifacts_days`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_RETENTION_ENABLED` | `bool` | false | Settings field ``retention_enabled`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_RETENTION_LLM_CALLS_DAYS` | `int` | `90` | Settings field ``retention_llm_calls_days`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_RETENTION_SWEEP_HOURS` | `int` | `6` | Settings field ``retention_sweep_hours`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_RETENTION_TRACE_EVENTS_DAYS` | `int` | `30` | Settings field ``retention_trace_events_days`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_SEND_SAMPLES` | `bool` | false | Settings field ``send_samples`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_SESSION_RATE_ENABLED` | `bool` | false | Settings field ``session_rate_enabled`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_SESSION_RATE_PER_DAY` | `int` | `100` | Settings field ``session_rate_per_day`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_SSE_MAX_STREAMS` | `int` | `20` | Settings field ``sse_max_streams`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_SSE_MAX_STREAMS_PER_SESSION` | `int` | `3` | Settings field ``sse_max_streams_per_session`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_STORE_PATH` | `str` | `data/auto_bi.sqlite` | Settings field ``store_path`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_SUPERSET_PASSWORD` | `str` | (empty) | Settings field ``superset_password`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_SUPERSET_PUBLIC_URL` | `str | null` | (unset / null) | Settings field ``superset_public_url`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_SUPERSET_URL` | `str` | `http://localhost:8088` | Settings field ``superset_url`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_SUPERSET_USER` | `str` | `admin` | Settings field ``superset_user`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_WORK_RATE_ENABLED` | `bool` | false | Settings field ``work_rate_enabled`` (see ``auto_bi/config.py`` and ``.env.example``). |
| `AUTO_BI_WORK_RATE_PER_DAY` | `int` | `50` | Settings field ``work_rate_per_day`` (see ``auto_bi/config.py`` and ``.env.example``). |

## Companion variables (not Settings fields)

| Variable | Notes | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic SDK key when ``llm_provider=anthropic``. Also accepted as ``AUTO_BI_ANTHROPIC_API_KEY``. | (empty) |

## Counts

- Settings fields / `AUTO_BI_*` keys: **66**
- Companion vars listed above: **1**

<!-- generate_env_reference:end -->
