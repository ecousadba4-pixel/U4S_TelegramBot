"""Integration tests for MAX webhook endpoint."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from config import get_settings
from main import app


@pytest.fixture
def test_settings(monkeypatch):
    monkeypatch.setenv("MAX_BOT_TOKEN", "test_token")
    monkeypatch.setenv("MAX_WEBHOOK_SECRET", "test_secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def setup_app_state(test_settings):
    app.state.settings = get_settings()
    app.state.max_client = AsyncMock()
    app.state.bot_service = AsyncMock()


@pytest.mark.asyncio
async def test_webhook_rejects_invalid_secret():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhook",
            json={"update_type": "bot_started", "user": {"user_id": 1}},
            headers={"X-Max-Bot-Api-Secret": "wrong"},
        )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_webhook_accepts_valid_update():
    with patch("adapters.max.webhook.handle_update", new=AsyncMock()) as mock_handle:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/webhook",
                json={"update_type": "bot_started", "user": {"user_id": 12345}},
                headers={"X-Max-Bot-Api-Secret": "test_secret"},
            )
        await asyncio.sleep(0.05)

    assert response.status_code == 200
    mock_handle.assert_awaited_once()
