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

    redis_url: str = Field(...)
    jwt_secret: str = Field(..., min_length=32)
    jwt_algorithm: str = Field(default="HS256")
    jwt_access_ttl_minutes: int = Field(default=15)
    jwt_refresh_ttl_days: int = Field(default=7)


@lru_cache
def get_settings() -> Settings:
    """Cached so we read the env once per process. Reset in tests via cache_clear()."""
    return Settings()  # type: ignore[call-arg]
