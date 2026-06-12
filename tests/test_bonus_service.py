"""Tests for bonus service."""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from services.bonus_service import BotService, get_bonus_by_phone


def test_normalize_phone_strips_non_digits():
    assert BotService.normalize_phone("+7 (999) 123-45-67") == "9991234567"


def test_normalize_phone_short_number():
    assert BotService.normalize_phone("12345") == "12345"


def test_format_bonus_amount():
    assert BotService.format_bonus_amount("1250.50") == 1250
    assert BotService.format_bonus_amount(None) == 0


def test_parse_guest_info_with_visit_date():
    row = {
        "first_name": "Иван",
        "loyalty_level": "gold",
        "bonus_balances": 500,
        "last_date_visit": datetime(2024, 1, 15),
    }

    service = BotService("postgresql://", 1, 1)
    info = service.parse_guest_info(row)

    assert info is not None
    assert info["first_name"] == "Иван"
    assert info["loyalty_level"] == "gold"
    assert info["bonus_balances"] == 500
    assert info["expire_date"] == "15.01.2025"


@pytest.mark.asyncio
async def test_get_bonus_by_phone_delegates_to_service():
    service = BotService("postgresql://", 1, 1)
    expected = {"first_name": "Test", "loyalty_level": "silver", "bonus_balances": 100, "expire_date": "01.01.2025"}
    service.get_guest_bonus = AsyncMock(return_value=expected)

    result = await get_bonus_by_phone(service, "+79991234567")

    assert result == expected
    service.get_guest_bonus.assert_awaited_once_with("+79991234567")
