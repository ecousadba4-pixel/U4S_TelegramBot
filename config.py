"""Application configuration management."""

from functools import lru_cache
from typing import Optional

from pydantic import Field, HttpUrl, PostgresDsn, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_MAX_API_URL = "https://platform-api2.max.ru"


class Settings(BaseSettings):
    """Configuration loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    max_bot_token: str = Field(alias="MAX_BOT_TOKEN")
    max_webhook_secret: str = Field(alias="MAX_WEBHOOK_SECRET", min_length=5, max_length=256)
    max_api_url: str = Field(default=DEFAULT_MAX_API_URL, alias="MAX_API_URL")
    max_webhook_url: Optional[HttpUrl] = Field(default=None, alias="MAX_WEBHOOK_URL")
    database_url: PostgresDsn = Field(alias="DATABASE_URL")
    port: int = Field(default=8000, alias="PORT", ge=1, le=65535)
    pool_min_size: int = Field(default=1, alias="POOL_MIN_SIZE", ge=1)
    pool_max_size: int = Field(default=10, alias="POOL_MAX_SIZE", ge=1)

    @model_validator(mode="after")
    def validate_pool_limits(self) -> "Settings":
        if self.pool_min_size > self.pool_max_size:
            msg = "POOL_MIN_SIZE cannot be greater than POOL_MAX_SIZE"
            raise ValueError(msg)
        return self


@lru_cache
def get_settings() -> Settings:
    """Return a cached instance of application settings."""

    return Settings()
