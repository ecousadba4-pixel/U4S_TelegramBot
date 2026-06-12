"""Format user-facing bonus responses (messenger-agnostic)."""

from loguru import logger

from services.bonus_service import BotService
from services.messages import (
    MSG_BALANCE_TEMPLATE,
    MSG_EXPIRY_TEMPLATE,
    MSG_NO_BONUS,
)


async def build_bonus_response(
    bot_service: BotService,
    user_id: int,
    phone_number: str,
) -> str:
    try:
        await bot_service.log_usage_stat(user_id=user_id, phone=phone_number, command="contact")
    except Exception as exc:
        logger.error("Failed to log usage stat for user {}: {}", user_id, exc)

    try:
        guest_info = await bot_service.get_guest_bonus(phone_number)
    except Exception as exc:
        logger.error(
            "Failed to fetch bonus info for phone {} (user_id={}): {}",
            phone_number,
            user_id,
            exc,
        )
        raise RuntimeError("Произошла ошибка при получении данных. Попробуйте позже.") from exc

    if not guest_info:
        return MSG_NO_BONUS

    bonus_amount = bot_service.format_bonus_amount(guest_info["bonus_balances"])
    response_text = MSG_BALANCE_TEMPLATE.format(
        first_name=guest_info["first_name"],
        amount=bonus_amount,
        level=guest_info["loyalty_level"],
    )
    if bonus_amount > 0:
        response_text += MSG_EXPIRY_TEMPLATE.format(date=guest_info["expire_date"])
    return response_text
