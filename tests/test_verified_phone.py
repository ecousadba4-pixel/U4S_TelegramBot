"""Tests for verified phone storage."""

from services.bonus_service import BotService


def test_remember_and_get_verified_phone():
    service = BotService("postgresql://", 1, 1)
    service.remember_verified_phone(42, "+79991234567")
    assert service.get_verified_phone(42) == "+79991234567"
    assert service.get_verified_phone(99) is None
