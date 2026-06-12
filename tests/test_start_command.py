"""Tests for /start command detection."""

import pytest

from adapters.max.handlers import is_start_command


@pytest.mark.parametrize(
    "text",
    [
        "/start",
        "start",
        "/START",
        "/start@my_bot",
        "/start payload",
    ],
)
def test_is_start_command_accepts_valid_values(text: str):
    assert is_start_command(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "hello",
        "/help",
        "not /start",
    ],
)
def test_is_start_command_rejects_other_messages(text: str):
    assert not is_start_command(text)
