"""HTTP client for MAX Bot API."""

from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any, Optional

import aiohttp
from loguru import logger

from services.messages import (
    BTN_CHECK_BONUS,
    BTN_SHARE_PHONE,
    CALLBACK_CHECK_BONUS_PAYLOAD,
    CMD_START_DESCRIPTION,
    CMD_START_NAME,
    MSG_START,
)

WEBHOOK_SECRET_HEADER = "X-Max-Bot-Api-Secret"
SUBSCRIPTION_UPDATE_TYPES = ["bot_started", "message_created", "message_callback"]
BOT_COMMANDS = [
    {
        "name": CMD_START_NAME,
        "description": CMD_START_DESCRIPTION,
    }
]
TEL_PATTERN = re.compile(r"^TEL(?:;[^:]*)?:(.+)$", re.MULTILINE)


def build_welcome_keyboard() -> list[dict[str, Any]]:
    return [
        {
            "type": "inline_keyboard",
            "payload": {
                "buttons": [
                    [
                        {
                            "type": "request_contact",
                            "text": BTN_SHARE_PHONE,
                        }
                    ],
                ]
            },
        }
    ]


def build_bonus_keyboard() -> list[dict[str, Any]]:
    return [
        {
            "type": "inline_keyboard",
            "payload": {
                "buttons": [
                    [
                        {
                            "type": "callback",
                            "text": BTN_CHECK_BONUS,
                            "payload": CALLBACK_CHECK_BONUS_PAYLOAD,
                        }
                    ],
                ]
            },
        }
    ]


class MaxApiClient:
    def __init__(self, api_url: str, bot_token: str):
        self._api_url = api_url.rstrip("/")
        self._bot_token = bot_token
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": self._bot_token},
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        session = await self._get_session()
        url = f"{self._api_url}{path}"
        async with session.request(method, url, params=params, json=json_body) as response:
            body: Any = None
            if response.content_type == "application/json":
                body = await response.json()
            else:
                text = await response.text()
                body = {"raw": text} if text else {}

            if response.status >= 400:
                logger.error(
                    "MAX API error: {} {} status={} body={}",
                    method,
                    path,
                    response.status,
                    body,
                )
                raise RuntimeError(f"MAX API request failed with status {response.status}")

            return body if isinstance(body, dict) else {"result": body}

    async def subscribe_webhook(self, webhook_url: str, secret: str) -> dict[str, Any]:
        payload = {
            "url": webhook_url,
            "update_types": SUBSCRIPTION_UPDATE_TYPES,
            "secret": secret,
        }
        result = await self._request("POST", "/subscriptions", json_body=payload)
        logger.info("MAX webhook subscription registered: {}", webhook_url)
        return result

    async def get_bot_info(self) -> dict[str, Any]:
        return await self._request("GET", "/me")

    async def set_bot_commands(self) -> dict[str, Any]:
        await self._request(
            "PATCH",
            "/me",
            json_body={"commands": BOT_COMMANDS},
        )
        bot_info = await self.get_bot_info()
        commands = bot_info.get("commands") or []
        command_names = [cmd.get("name") for cmd in commands if isinstance(cmd, dict)]
        logger.info("MAX bot commands active: {}", command_names or "none")
        return bot_info

    async def answer_callback(self, callback_id: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/answers",
            params={"callback_id": callback_id},
            json_body={},
        )

    async def send_message(
        self,
        user_id: int,
        text: str,
        *,
        attachments: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        json_body: dict[str, Any] = {"text": text}
        if attachments:
            json_body["attachments"] = attachments
        return await self._request(
            "POST",
            "/messages",
            params={"user_id": user_id},
            json_body=json_body,
        )

    async def send_welcome_message(self, user_id: int) -> dict[str, Any]:
        payload = {
            "text": MSG_START,
            "attachments": build_welcome_keyboard(),
        }
        return await self._request(
            "POST",
            "/messages",
            params={"user_id": user_id},
            json_body=payload,
        )


def normalize_vcf_info(vcf_info: str) -> str:
    """Convert escaped newlines in vcf_info to real CRLF as required by MAX API."""
    return vcf_info.replace("\\r\\n", "\r\n").replace("\\n", "\n")


def compute_contact_hash(bot_token: str, vcf_info: str) -> str:
    normalized = normalize_vcf_info(vcf_info)
    return hmac.new(
        bot_token.encode("utf-8"),
        normalized.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_contact_hash(bot_token: str, vcf_info: str, contact_hash: str) -> bool:
    if not vcf_info or not contact_hash:
        return False
    expected = compute_contact_hash(bot_token, vcf_info)
    return hmac.compare_digest(expected, contact_hash)


def parse_phone_from_vcf(vcf_info: str) -> Optional[str]:
    normalized = normalize_vcf_info(vcf_info)
    match = TEL_PATTERN.search(normalized)
    if not match:
        return None
    return match.group(1).strip()
