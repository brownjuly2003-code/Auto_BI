"""Application settings loaded from environment / .env (never hardcode secrets)."""

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

    # DataLens (self-hosted OSS stand, v2 BI target)
    datalens_url: str = "http://localhost:8090"
    datalens_user: str = "admin"
    datalens_password: str = "admin"
    # the OpenSource Demo workbook on the self-hosted stand (stable id; not a secret)
    datalens_workbook_id: str = "z4wtz6tg5194o"
    # ClickHouse host as the DataLens connection reaches it (host.docker.internal on the
    # self-hosted compose stand); port reuses ch_port.
    ch_host_from_datalens: str = "host.docker.internal"

    # GraceKelly LLM service
    gracekelly_url: str = "http://127.0.0.1:8011"
    gracekelly_model: str = "claude-sonnet-4-6"

    send_samples: bool = True

    # SQLite store (sessions, specs, builds, llm_calls, dm_change_requests)
    store_path: str = "data/auto_bi.sqlite"


@lru_cache
def get_settings() -> Settings:
    return Settings()
