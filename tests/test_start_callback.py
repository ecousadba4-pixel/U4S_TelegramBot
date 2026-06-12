"""Tests for start callback handling."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from adapters.max.handlers import handle_message_callback


@pytest.mark.asyncio
async def test_handle_message_callback_start():
    max_client = AsyncMock()
    bot_service = Mock()
    update = {
        "update_type": "message_callback",
        "callback": {
            "callback_id": "cb-1",
            "payload": "start",
            "user": {"user_id": 42},
        },
    }

    await handle_message_callback(update, max_client=max_client, bot_service=bot_service)

    max_client.answer_callback.assert_awaited_once_with("cb-1")
    max_client.send_welcome_message.assert_awaited_once_with(42)


@pytest.mark.asyncio
async def test_handle_message_callback_check_bonus():
    max_client = AsyncMock()
    bot_service = Mock()
    bot_service.get_verified_phone.return_value = "+79991234567"
    update = {
        "update_type": "message_callback",
        "callback": {
            "callback_id": "cb-2",
            "payload": "check_bonus",
            "user": {"user_id": 42},
        },
    }

    with patch("adapters.max.handlers.send_bonus_flow", new=AsyncMock()) as mock_send_bonus:
        await handle_message_callback(update, max_client=max_client, bot_service=bot_service)

    bot_service.get_verified_phone.assert_called_once_with(42)
    mock_send_bonus.assert_awaited_once()
    max_client.send_welcome_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_message_callback_check_bonus_without_phone():
    max_client = AsyncMock()
    bot_service = Mock()
    bot_service.get_verified_phone.return_value = None
    update = {
        "update_type": "message_callback",
        "callback": {
            "callback_id": "cb-3",
            "payload": "check_bonus",
            "user": {"user_id": 42},
        },
    }

    await handle_message_callback(update, max_client=max_client, bot_service=bot_service)

    max_client.send_welcome_message.assert_awaited_once_with(42)


@pytest.mark.asyncio
async def test_handle_message_callback_ignores_other_payloads():
    max_client = AsyncMock()
    bot_service = Mock()
    update = {
        "update_type": "message_callback",
        "callback": {
            "callback_id": "cb-1",
            "payload": "other",
            "user": {"user_id": 42},
        },
    }

    await handle_message_callback(update, max_client=max_client, bot_service=bot_service)

    max_client.answer_callback.assert_not_awaited()
    max_client.send_welcome_message.assert_not_awaited()
