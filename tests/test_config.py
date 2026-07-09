"""Tests for application configuration."""

from config import DEFAULT_MAX_API_URL, Settings


def test_default_max_api_url_uses_new_domain(monkeypatch):
    monkeypatch.delenv("MAX_API_URL", raising=False)

    settings = Settings(
        MAX_BOT_TOKEN="test_token",
        MAX_WEBHOOK_SECRET="test_secret",
        DATABASE_URL="postgresql://user:pass@localhost/db",
    )

    assert DEFAULT_MAX_API_URL == "https://platform-api2.max.ru"
    assert settings.max_api_url == DEFAULT_MAX_API_URL
