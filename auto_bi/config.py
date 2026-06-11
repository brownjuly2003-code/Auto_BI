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

    # Superset
    superset_url: str = "http://localhost:8088"
    superset_user: str = "admin"
    superset_password: str = ""

    # GraceKelly LLM service
    gracekelly_url: str = "http://127.0.0.1:8011"
    gracekelly_model: str = "claude-sonnet-4-6"

    send_samples: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
