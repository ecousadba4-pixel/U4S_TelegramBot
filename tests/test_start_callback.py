"""Tests for start callback handling."""

from unittest.mock import AsyncMock

import pytest

from adapters.max.handlers import handle_message_callback


@pytest.mark.asyncio
async def test_handle_message_callback_start():
    max_client = AsyncMock()
    update = {
        "update_type": "message_callback",
        "callback": {
            "callback_id": "cb-1",
            "payload": "start",
            "user": {"user_id": 42},
        },
    }

    await handle_message_callback(update, max_client=max_client)

    max_client.answer_callback.assert_awaited_once_with("cb-1")
    max_client.send_start_message.assert_awaited_once_with(42)


@pytest.mark.asyncio
async def test_handle_message_callback_ignores_other_payloads():
    max_client = AsyncMock()
    update = {
        "update_type": "message_callback",
        "callback": {
            "callback_id": "cb-1",
            "payload": "other",
            "user": {"user_id": 42},
        },
    }

    await handle_message_callback(update, max_client=max_client)

    max_client.answer_callback.assert_not_awaited()
    max_client.send_start_message.assert_not_awaited()
