"""Tests for usage statistics logging."""

from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from services.bonus_service import SQL_LOG_USAGE, BotService
from services.responses import build_bonus_response


@pytest.mark.asyncio
async def test_log_usage_stat_writes_normalized_phone_to_stats_table():
    service = BotService("postgresql://", 1, 1)
    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    service._ensure_pool = AsyncMock(return_value=mock_pool)

    await service.log_usage_stat(12345, "+7 (999) 123-45-67", "contact")

    mock_conn.execute.assert_awaited_once_with(
        SQL_LOG_USAGE,
        12345,
        "9991234567",
        "contact",
    )


@pytest.mark.asyncio
async def test_log_usage_stat_skips_empty_phone():
    service = BotService("postgresql://", 1, 1)
    service._ensure_pool = AsyncMock()

    await service.log_usage_stat(12345, "", "contact")

    service._ensure_pool.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_bonus_response_logs_max_contact_check():
    bot_service = Mock()
    bot_service.log_usage_stat = AsyncMock()
    bot_service.get_guest_bonus = AsyncMock(
        return_value={
            "first_name": "Иван",
            "loyalty_level": "gold",
            "bonus_balances": 100,
            "expire_date": "01.01.2026",
        }
    )
    bot_service.format_bonus_amount = Mock(return_value=100)

    await build_bonus_response(
        bot_service=bot_service,
        user_id=999,
        phone_number="79991234567",
    )

    bot_service.log_usage_stat.assert_awaited_once_with(
        user_id=999,
        phone="79991234567",
        command="contact",
    )
