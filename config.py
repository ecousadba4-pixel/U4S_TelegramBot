"""Application configuration management."""

from functools import lru_cache
from typing import Literal, Optional

from pydantic import Field, HttpUrl, PostgresDsn, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN")
    telegram_transport: Literal["bot_api", "mtproto"] = Field(
        default="bot_api",
        alias="TELEGRAM_TRANSPORT",
    )
    telegram_api_id: Optional[int] = Field(default=None, alias="TELEGRAM_API_ID", ge=1)
    telegram_api_hash: Optional[str] = Field(default=None, alias="TELEGRAM_API_HASH")
    telegram_mtproxy_link: Optional[str] = Field(default=None, alias="TELEGRAM_MTPROXY_LINK")
    database_url: PostgresDsn = Field(alias="DATABASE_URL")
    webhook_url: Optional[HttpUrl] = Field(default=None, alias="WEBHOOK_URL")
    port: int = Field(default=8000, alias="PORT", ge=1, le=65535)
    pool_min_size: int = Field(default=1, alias="POOL_MIN_SIZE", ge=1)
    pool_max_size: int = Field(default=10, alias="POOL_MAX_SIZE", ge=1)

    @model_validator(mode="after")
    def validate_pool_limits(self) -> "Settings":
        if self.pool_min_size > self.pool_max_size:
            msg = "POOL_MIN_SIZE cannot be greater than POOL_MAX_SIZE"
            raise ValueError(msg)
        if self.telegram_transport == "mtproto":
            if not self.telegram_api_id:
                raise ValueError("TELEGRAM_API_ID is required when TELEGRAM_TRANSPORT=mtproto")
            if not self.telegram_api_hash:
                raise ValueError("TELEGRAM_API_HASH is required when TELEGRAM_TRANSPORT=mtproto")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return a cached instance of application settings."""

    return Settings()
