from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Pydantic-settings reads from .env automatically in dev. In prod, env vars
    are injected by the platform (Cloud Run, etc.) and .env is absent.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(...)
    app_env: str = Field(default="development")
    log_level: str = Field(default="INFO")


@lru_cache
def get_settings() -> Settings:
    """Cached so we read the env once per process. Reset in tests via cache_clear()."""
    return Settings()  # type: ignore[call-arg]
