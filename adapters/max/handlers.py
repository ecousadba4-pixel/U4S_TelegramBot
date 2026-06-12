"""MAX webhook update handlers."""

from __future__ import annotations

import re
from typing import Any, Optional

from loguru import logger

from adapters.max.client import (
    MaxApiClient,
    build_start_keyboard,
    parse_phone_from_vcf,
    verify_contact_hash,
)
from services.bonus_service import BotService
from services.messages import CALLBACK_START_PAYLOAD, CMD_START_NAME, MSG_INVALID_CONTACT
from services.responses import build_bonus_response

START_COMMAND_PATTERN = re.compile(rf"^/?{CMD_START_NAME}(?:@\w+)?(?:\s|$)", re.IGNORECASE)


def extract_user_id(user: Optional[dict[str, Any]]) -> Optional[int]:
    if not user:
        return None
    user_id = user.get("user_id")
    if user_id is None:
        return None
    return int(user_id)


def extract_contact_attachment(message: dict[str, Any]) -> Optional[dict[str, Any]]:
    body = message.get("body") or {}
    attachments = body.get("attachments") or []
    for attachment in attachments:
        if attachment.get("type") == "contact":
            payload = attachment.get("payload") or {}
            if payload.get("vcf_info"):
                return payload
    return None


def extract_message_text(message: dict[str, Any]) -> str:
    body = message.get("body") or {}
    text = body.get("text")
    return text.strip() if isinstance(text, str) else ""


def is_start_command(text: str) -> bool:
    return bool(START_COMMAND_PATTERN.match(text.strip()))


async def send_start_flow(user_id: int, *, max_client: MaxApiClient) -> None:
    await max_client.send_start_message(user_id)


async def handle_bot_started(
    update: dict[str, Any],
    *,
    max_client: MaxApiClient,
) -> None:
    user_id = extract_user_id(update.get("user"))
    if user_id is None:
        logger.warning("bot_started update without user_id: {}", update)
        return
    logger.info("bot_started for user_id={}", user_id)
    await send_start_flow(user_id, max_client=max_client)


async def handle_message_callback(
    update: dict[str, Any],
    *,
    max_client: MaxApiClient,
) -> None:
    callback = update.get("callback") or {}
    payload = callback.get("payload")
    callback_id = callback.get("callback_id")
    user_id = extract_user_id(callback.get("user"))

    if payload != CALLBACK_START_PAYLOAD or user_id is None:
        return

    logger.info("Received start callback from user_id={}", user_id)
    if callback_id:
        try:
            await max_client.answer_callback(str(callback_id))
        except Exception:
            logger.exception("Failed to answer callback for user_id={}", user_id)

    await send_start_flow(user_id, max_client=max_client)


async def handle_message_created(
    update: dict[str, Any],
    *,
    max_client: MaxApiClient,
    bot_service: BotService,
    bot_token: str,
) -> None:
    message = update.get("message") or {}
    sender = message.get("sender") or {}
    user_id = extract_user_id(sender)
    if user_id is None:
        logger.warning("message_created without sender user_id: {}", update)
        return

    contact_payload = extract_contact_attachment(message)
    if contact_payload is not None:
        vcf_info = contact_payload.get("vcf_info", "")
        contact_hash = contact_payload.get("hash", "")

        if not verify_contact_hash(bot_token, vcf_info, contact_hash):
            logger.warning("Invalid contact hash for user_id={}", user_id)
            await max_client.send_message(user_id, MSG_INVALID_CONTACT)
            return

        phone_number = parse_phone_from_vcf(vcf_info)
        if not phone_number:
            logger.warning("Could not parse phone from vcf for user_id={}", user_id)
            await max_client.send_message(user_id, MSG_INVALID_CONTACT)
            return

        logger.info("Received MAX contact from {} (user_id={})", phone_number, user_id)
        try:
            response_text = await build_bonus_response(
                bot_service=bot_service,
                user_id=user_id,
                phone_number=phone_number,
            )
        except RuntimeError as exc:
            await max_client.send_message(user_id, str(exc))
            return

        await max_client.send_message(
            user_id,
            response_text,
            attachments=build_start_keyboard(),
        )
        return

    message_text = extract_message_text(message)
    if is_start_command(message_text):
        logger.info("Received /start command from user_id={}", user_id)
        await send_start_flow(user_id, max_client=max_client)


async def handle_update(
    update: dict[str, Any],
    *,
    max_client: MaxApiClient,
    bot_service: BotService,
    bot_token: str,
) -> None:
    update_type = update.get("update_type")
    if update_type == "bot_started":
        await handle_bot_started(update, max_client=max_client)
    elif update_type == "message_callback":
        await handle_message_callback(update, max_client=max_client)
    elif update_type == "message_created":
        await handle_message_created(
            update,
            max_client=max_client,
            bot_service=bot_service,
            bot_token=bot_token,
        )
    else:
        logger.debug("Ignoring unsupported update_type: {}", update_type)
